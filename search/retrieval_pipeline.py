import asyncio
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
import inspect
import math
import threading
from typing import Any

from llama_index.core.vector_stores import (
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)

from core.models import DocumentModel
from environments.config import AppConfig
from search.query_terms import retrieval_query_variants
from search.ranking import (
    ContextCandidateRanker,
    query_term_groups,
    repository_lookup_terms_from_groups,
    should_run_metadata_fallback,
    should_try_lowercase_github_probe,
)
from storage.metadata_store import MetadataStore


class RetrievalPolicy:
    MAX_RETRIEVAL_LIMIT_MULTIPLIER = 64


class RetrievalDeadlineExceeded(TimeoutError):
    """A bounded synchronous retrieval did not finish before its deadline."""

    def __init__(self):
        super().__init__("Retrieval deadline exceeded")


class BoundedRetrievalExecutor:
    """Keep synchronous vector work off the caller loop with bounded spillover."""

    def __init__(self, *, timeout_seconds: float, max_concurrency: int):
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise ValueError("retrieval timeout must be a positive finite number")
        if (
            isinstance(max_concurrency, bool)
            or not isinstance(max_concurrency, int)
            or max_concurrency < 1
        ):
            raise ValueError("retrieval concurrency must be a positive integer")
        self.timeout_seconds = float(timeout_seconds)
        self.max_concurrency = max_concurrency
        self._semaphore = threading.BoundedSemaphore(max_concurrency)
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="contextwiki-retrieval",
        )

    async def run(self, function: Callable[..., Any], /, *args, **kwargs) -> Any:
        """Run one synchronous retrieval under a total queue/execution deadline."""
        return await self.run_until(self.deadline(), function, *args, **kwargs)

    def deadline(self) -> float:
        """Return one loop-monotonic deadline reusable across retrieval steps."""
        loop = asyncio.get_running_loop()
        return loop.time() + self.timeout_seconds

    async def run_until(
        self,
        deadline: float,
        function: Callable[..., Any],
        /,
        *args,
        **kwargs,
    ) -> Any:
        """Run synchronous work without extending an existing request deadline."""
        loop = asyncio.get_running_loop()
        await self._acquire_slot(deadline)

        worker: Future[Any] | None = None
        release_deferred = False
        try:
            worker = self._executor.submit(partial(function, *args, **kwargs))
            remaining = max(0.0, deadline - loop.time())
            wrapped = asyncio.wrap_future(worker, loop=loop)
            try:
                return await asyncio.wait_for(
                    asyncio.shield(wrapped),
                    timeout=remaining,
                )
            except TimeoutError:
                if worker.done():
                    return worker.result()
                release_deferred = True
                worker.add_done_callback(
                    partial(
                        self._release_after_worker,
                        semaphore=self._semaphore,
                    )
                )
                raise RetrievalDeadlineExceeded() from None
            except asyncio.CancelledError:
                release_deferred = True
                worker.add_done_callback(
                    partial(
                        self._release_after_worker,
                        semaphore=self._semaphore,
                    )
                )
                raise
        finally:
            if not release_deferred:
                self._semaphore.release()

    async def _acquire_slot(self, deadline: float) -> None:
        loop = asyncio.get_running_loop()
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RetrievalDeadlineExceeded() from None
            if self._semaphore.acquire(blocking=False):
                return
            await asyncio.sleep(min(0.01, remaining))

    @staticmethod
    def _release_after_worker(
        worker: Future[Any],
        *,
        semaphore: threading.BoundedSemaphore,
    ) -> None:
        try:
            worker.exception()
        except BaseException:
            pass
        semaphore.release()


class ContextRetrievalPipeline:
    def __init__(
        self,
        *,
        metadata_store: MetadataStore,
        config: AppConfig,
        indexer,
        retriever: Callable | Iterable[DocumentModel] | None,
        vector_retriever_cls,
        ranker: ContextCandidateRanker,
        retrieval_executor: BoundedRetrievalExecutor,
    ):
        self.metadata_store = metadata_store
        self.config = config
        self.indexer = indexer
        self.retriever = retriever
        self.vector_retriever_cls = vector_retriever_cls
        self.ranker = ranker
        self.retrieval_executor = retrieval_executor

    async def retrieve_candidates(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
        hard_candidate_limit: int | None = None,
        candidate_metadata_filters: dict[str, list[str]] | None = None,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        arguments = (
            query,
            top_k,
            source_ids,
            hard_candidate_limit,
            candidate_metadata_filters,
        )
        if deadline is None:
            return await self.retrieval_executor.run(
                self._retrieve_candidates_sync,
                *arguments,
            )
        return await self.retrieval_executor.run_until(
            deadline,
            self._retrieve_candidates_sync,
            *arguments,
        )

    def _retrieve_candidates_sync(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
        hard_candidate_limit: int | None = None,
        candidate_metadata_filters: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        effective_top_k = min(top_k, hard_candidate_limit or top_k)
        if self.retriever is not None:
            if callable(self.retriever):
                supports_metadata_filters = False
                if candidate_metadata_filters:
                    parameters = inspect.signature(self.retriever).parameters.values()
                    supports_metadata_filters = any(
                        parameter.name == "candidate_metadata_filters"
                        or parameter.kind is inspect.Parameter.VAR_KEYWORD
                        for parameter in parameters
                    )
                if supports_metadata_filters:
                    retrieved = self.retriever(
                        query,
                        effective_top_k,
                        source_ids,
                        candidate_metadata_filters=candidate_metadata_filters,
                    )
                else:
                    retrieved = self.retriever(query, effective_top_k, source_ids)
                candidates = list(retrieved)[:effective_top_k]
            else:
                candidates = self.ranker.keyword_candidates(
                    query,
                    self.retriever,
                    effective_top_k,
                    source_ids,
                )
            term_groups = query_term_groups(query)
            self._prime_candidate_hydration(candidates)
            reranked = self.ranker.rerank_candidates(
                query,
                candidates,
                term_groups,
                effective_top_k,
            )
            return {
                "candidates": reranked,
                "retrieval_queries": [query],
                "original_term_groups": term_groups,
                "effective_term_groups": term_groups,
                "initial_top_vector_score": self.top_vector_score(candidates),
                "final_top_score": self.top_score(reranked),
            }

        term_groups = query_term_groups(query)
        if self.indexer is None:
            if not term_groups:
                return {
                    "candidates": [],
                    "retrieval_queries": [query],
                    "original_term_groups": [],
                    "effective_term_groups": [],
                    "initial_top_vector_score": 0.0,
                    "final_top_score": 0.0,
                }
            candidates = self.ranker.metadata_fallback_candidates(
                query,
                effective_top_k,
                source_ids,
                term_groups,
                [],
                candidate_scan_limit=hard_candidate_limit,
            )
            self._prime_candidate_hydration(candidates)
            reranked = self.ranker.rerank_candidates(
                query,
                candidates,
                term_groups,
                effective_top_k,
            )
            return {
                "candidates": reranked,
                "retrieval_queries": [query],
                "original_term_groups": term_groups,
                "effective_term_groups": term_groups,
                "initial_top_vector_score": self.top_vector_score(candidates),
                "final_top_score": self.top_score(reranked),
            }

        retrieval_queries = retrieval_query_variants(query, term_groups)
        candidates, initial_top_vector_score = (
            self.retrieve_candidates_for_variants_with_vector_score(
                query,
                effective_top_k,
                source_ids,
                term_groups,
                retrieval_queries,
                hard_candidate_limit=hard_candidate_limit,
                candidate_metadata_filters=candidate_metadata_filters,
            )
        )
        self._prime_candidate_hydration(candidates)
        reranked = self.ranker.rerank_candidates(
            query,
            candidates,
            term_groups,
            effective_top_k,
        )
        return {
            "candidates": reranked,
            "retrieval_queries": retrieval_queries,
            "original_term_groups": term_groups,
            "effective_term_groups": term_groups,
            "initial_top_vector_score": initial_top_vector_score,
            "final_top_score": self.top_score(reranked),
        }

    def retrieve_candidates_for_variants(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
        term_groups: list[set[str]],
        query_variants: list[str],
    ) -> list[dict[str, Any]]:
        candidates, _ = self.retrieve_candidates_for_variants_with_vector_score(
            query,
            top_k,
            source_ids,
            term_groups,
            query_variants,
        )
        return candidates

    def retrieve_candidates_for_variants_with_vector_score(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
        term_groups: list[set[str]],
        query_variants: list[str],
        hard_candidate_limit: int | None = None,
        candidate_metadata_filters: dict[str, list[str]] | None = None,
    ) -> tuple[list[dict[str, Any]], float]:

        index = self.indexer.get_or_create_index()
        candidate_map: dict[str, dict[str, Any]] = {}
        rejected: set[str] = set()
        top_initial_vector_score = 0.0
        if hard_candidate_limit is not None:
            node_batches = []
            for retrieval_query, limit in zip(
                query_variants,
                self._variant_limits(hard_candidate_limit, len(query_variants)),
            ):
                retriever = self.vector_retriever_cls(
                    index=index,
                    similarity_top_k=limit,
                    vector_store_query_mode="hybrid",
                    filters=metadata_filters(
                        source_ids,
                        candidate_metadata_filters,
                    ),
                )
                node_batches.append(list(retriever.retrieve(retrieval_query))[:limit])
            top_initial_vector_score = self._merge_vector_node_batches(
                query,
                term_groups,
                source_ids,
                node_batches,
                candidate_map,
                rejected,
            )
        else:
            base_limit = max(top_k, top_k * self.config.search_multiplier)
            max_limit = self.max_retrieval_limit(base_limit)
            limit = base_limit
            while limit <= max_limit:
                retriever = self.vector_retriever_cls(
                    index=index,
                    similarity_top_k=limit,
                    vector_store_query_mode="hybrid",
                    filters=metadata_filters(
                        source_ids,
                        candidate_metadata_filters,
                    ),
                )
                node_batches = [
                    list(retriever.retrieve(retrieval_query))
                    for retrieval_query in query_variants
                ]
                top_initial_vector_score = max(
                    top_initial_vector_score,
                    self._merge_vector_node_batches(
                        query,
                        term_groups,
                        source_ids,
                        node_batches,
                        candidate_map,
                        rejected,
                    ),
                )
                candidates = list(candidate_map.values())
                if len(candidates) >= top_k:
                    break
                max_node_count = max((len(nodes) for nodes in node_batches), default=0)
                if max_node_count < limit:
                    break
                next_limit = min(limit * 2, max_limit)
                if next_limit == limit:
                    break
                limit = next_limit

        candidates = list(candidate_map.values())
        metadata_candidates = []
        lowercase_github_probe = should_try_lowercase_github_probe(
            query,
            term_groups,
            source_ids,
        )
        missing_textual_matches = not self.ranker.candidates_have_textual_matches(
            candidates,
            term_groups,
        )
        force_textual_metadata_fallback = missing_textual_matches and bool(
            repository_lookup_terms_from_groups(term_groups)
        )
        if (
            hard_candidate_limit is None
            and (
                should_run_metadata_fallback(
                    query,
                    term_groups,
                    candidates,
                    top_k,
                    source_ids,
                )
                or force_textual_metadata_fallback
            )
            and not (
                lowercase_github_probe
                and len(candidates) >= top_k
                and not missing_textual_matches
            )
        ):
            metadata_candidates = self.ranker.metadata_fallback_candidates(
                query,
                top_k,
                source_ids,
                term_groups,
                candidates,
                candidate_scan_limit=hard_candidate_limit,
            )

        return (
            self.ranker.merge_ranked_candidates(
                list(candidate_map.values()),
                metadata_candidates,
                rejected,
                top_k,
            ),
            top_initial_vector_score,
        )

    @staticmethod
    def _variant_limits(total_limit: int, variant_count: int) -> list[int]:
        active_count = min(total_limit, variant_count)
        if active_count < 1:
            return []
        base, remainder = divmod(total_limit, active_count)
        return [base + int(index < remainder) for index in range(active_count)]

    def _prime_candidate_hydration(
        self,
        candidates: Iterable[dict[str, Any]],
    ) -> None:
        loader = getattr(self.metadata_store, "prime_active_evidence_snapshots", None)
        if not callable(loader):
            return
        loader(str(candidate.get("chunk_id") or "") for candidate in candidates)

    def _merge_vector_node_batches(
        self,
        query: str,
        term_groups: list[set[str]],
        source_ids: list[str] | None,
        node_batches: list[list[Any]],
        candidate_map: dict[str, dict[str, Any]],
        rejected: set[str],
    ) -> float:
        chunk_ids = [
            str(node.metadata.get("chunk_id") or node.metadata.get("doc_id") or "")
            for nodes in node_batches
            for node in nodes
        ]
        self._prime_candidate_hydration(
            {"chunk_id": chunk_id} for chunk_id in chunk_ids if chunk_id
        )
        top_vector_score = 0.0
        for nodes in node_batches:
            for node in nodes:
                score = float(node.score or 0.0)
                top_vector_score = max(top_vector_score, score)
                chunk_id = node.metadata.get("chunk_id") or node.metadata.get("doc_id")
                if not chunk_id:
                    continue
                chunk = self.metadata_store.get_chunk(chunk_id)
                if not chunk:
                    continue
                if not managed_hit_matches_chunk(node.metadata, chunk):
                    rejected.add(chunk_id)
                    continue
                if source_ids and chunk.source_id not in source_ids:
                    rejected.add(chunk_id)
                    continue
                if not self.ranker.document_intent_allows_chunk(
                    query, term_groups, chunk
                ):
                    rejected.add(chunk_id)
                    continue
                rejected.discard(chunk_id)
                existing = candidate_map.get(chunk_id)
                if existing is None or score > float(existing.get("score", 0.0)):
                    candidate_map[chunk_id] = {
                        "chunk_id": chunk_id,
                        "score": score,
                    }
        return top_vector_score

    @staticmethod
    def top_vector_score(candidates: list[dict[str, Any]]) -> float:
        return max(
            (
                float(candidate.get("vector_score", 0.0) or 0.0)
                for candidate in candidates
                if candidate.get("vector_score") is not None
            ),
            default=0.0,
        )

    @staticmethod
    def top_score(candidates: list[dict[str, Any]]) -> float:
        return max(
            (float(candidate.get("score", 0.0) or 0.0) for candidate in candidates),
            default=0.0,
        )

    def max_retrieval_limit(self, base_limit: int) -> int:
        collection = getattr(self.indexer, "collection", None)
        if collection is not None and hasattr(collection, "count"):
            try:
                return max(base_limit, int(collection.count()))
            except Exception:
                pass
        return max(
            base_limit, base_limit * RetrievalPolicy.MAX_RETRIEVAL_LIMIT_MULTIPLIER
        )


def managed_hit_matches_chunk(metadata: dict[str, Any], chunk) -> bool:
    if metadata.get("contextwiki_managed") != "true":
        return False
    source_id = metadata.get("source_id")
    document_id = metadata.get("document_id")
    if source_id != chunk.source_id:
        return False
    if document_id != chunk.document_id:
        return False
    return True


def metadata_filters(
    source_ids: list[str] | None,
    candidate_metadata_filters: dict[str, list[str]] | None = None,
):
    filters = [MetadataFilter(key="contextwiki_managed", value="true")]
    if source_ids and len(source_ids) == 1:
        filters.append(MetadataFilter(key="source_id", value=source_ids[0]))
    elif source_ids:
        filters.append(
            MetadataFilter(
                key="source_id",
                value=source_ids,
                operator=FilterOperator.IN,
            )
        )
    for key in ("evidence_source_type", "experience_type", "document_id"):
        values = list((candidate_metadata_filters or {}).get(key) or [])
        if not values:
            continue
        if len(values) == 1:
            filters.append(MetadataFilter(key=key, value=values[0]))
        else:
            filters.append(
                MetadataFilter(
                    key=key,
                    value=values,
                    operator=FilterOperator.IN,
                )
            )
    return MetadataFilters(filters=filters)
