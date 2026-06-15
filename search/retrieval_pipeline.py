from collections.abc import Callable, Iterable
from typing import Any

from llama_index.core.vector_stores import FilterOperator, MetadataFilter, MetadataFilters

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


class QueryRewritePolicy:
    LOW_INITIAL_SCORE_THRESHOLD = 0.75
    MAX_RETRIEVAL_LIMIT_MULTIPLIER = 64


class ContextRetrievalPipeline:
    def __init__(
        self,
        *,
        metadata_store: MetadataStore,
        config: AppConfig,
        indexer,
        retriever: Callable | Iterable[DocumentModel] | None,
        query_rewriter,
        vector_retriever_cls,
        ranker: ContextCandidateRanker,
    ):
        self.metadata_store = metadata_store
        self.config = config
        self.indexer = indexer
        self.retriever = retriever
        self.query_rewriter = query_rewriter
        self.vector_retriever_cls = vector_retriever_cls
        self.ranker = ranker

    async def retrieve_candidates(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
        *,
        allow_query_rewrite: bool = True,
    ) -> dict[str, Any]:
        rewrite_debug = self._rewrite_debug_state(
            term_groups=[],
            rewrite_enabled=self.query_rewriter is not None,
            rewrite_attempted=False,
            rewrite_applied=False,
            rewrite_skipped_reason="disabled" if self.query_rewriter is None else "",
        )
        if self.retriever is not None:
            if callable(self.retriever):
                candidates = list(self.retriever(query, top_k, source_ids))
            else:
                candidates = self.ranker.keyword_candidates(
                    query,
                    self.retriever,
                    top_k,
                    source_ids,
                )
            term_groups = query_term_groups(query)
            reranked = self.ranker.rerank_candidates(query, candidates, term_groups, top_k)
            return {
                "candidates": reranked,
                "retrieval_queries": [query],
                "rewritten_queries": [],
                "original_term_groups": term_groups,
                "effective_term_groups": term_groups,
                "initial_top_vector_score": self.top_vector_score(candidates),
                "final_top_score": self.top_score(reranked),
                "rewrite_debug": self._rewrite_debug_state(
                    term_groups=term_groups,
                    rewrite_enabled=self.query_rewriter is not None,
                    rewrite_attempted=False,
                    rewrite_applied=False,
                    rewrite_skipped_reason="disabled" if self.query_rewriter is None else "not_supported",
                ),
            }

        term_groups = query_term_groups(query)
        rewrite_debug = self._rewrite_debug_state(
            term_groups=term_groups,
            rewrite_enabled=self.query_rewriter is not None,
            rewrite_attempted=False,
            rewrite_applied=False,
            rewrite_skipped_reason="disabled" if self.query_rewriter is None else "",
        )
        if self.indexer is None:
            if not term_groups:
                return {
                    "candidates": [],
                    "retrieval_queries": [query],
                    "rewritten_queries": [],
                    "original_term_groups": [],
                    "effective_term_groups": [],
                    "initial_top_vector_score": 0.0,
                    "final_top_score": 0.0,
                    "rewrite_debug": self._rewrite_debug_state(
                        term_groups=[],
                        rewrite_enabled=self.query_rewriter is not None,
                        rewrite_attempted=False,
                        rewrite_applied=False,
                        rewrite_skipped_reason=(
                            "disabled" if self.query_rewriter is None else "no_term_groups"
                        ),
                    ),
                }
            candidates = self.ranker.metadata_fallback_candidates(
                query,
                top_k,
                source_ids,
                term_groups,
                [],
            )
            reranked = self.ranker.rerank_candidates(query, candidates, term_groups, top_k)
            return {
                "candidates": reranked,
                "retrieval_queries": [query],
                "rewritten_queries": [],
                "original_term_groups": term_groups,
                "effective_term_groups": term_groups,
                "initial_top_vector_score": self.top_vector_score(candidates),
                "final_top_score": self.top_score(reranked),
                "rewrite_debug": self._rewrite_debug_state(
                    term_groups=term_groups,
                    rewrite_enabled=self.query_rewriter is not None,
                    rewrite_attempted=False,
                    rewrite_applied=False,
                    rewrite_skipped_reason="disabled" if self.query_rewriter is None else "not_supported",
                ),
            }

        retrieval_queries = retrieval_query_variants(query, term_groups)
        selected_retrieval_queries = retrieval_queries
        candidates, initial_top_vector_score = self.retrieve_candidates_for_variants_with_vector_score(
            query,
            top_k,
            source_ids,
            term_groups,
            retrieval_queries,
        )
        effective_term_groups = term_groups
        rewritten_queries: list[str] = []
        rewrite_reason = ""
        selected_reranked: list[dict[str, Any]] | None = None
        rewrite_applied = False
        if not allow_query_rewrite:
            rewrite_debug = self._rewrite_debug_state(
                term_groups=term_groups,
                rewrite_enabled=self.query_rewriter is not None,
                rewrite_attempted=False,
                rewrite_applied=False,
                rewrite_skipped_reason="not_supported",
            )
        elif not term_groups:
            rewrite_debug = self._rewrite_debug_state(
                term_groups=term_groups,
                rewrite_enabled=self.query_rewriter is not None,
                rewrite_attempted=False,
                rewrite_applied=False,
                rewrite_skipped_reason="no_term_groups",
            )
        elif self.should_try_query_rewrite(
            query,
            candidates,
            term_groups,
            top_k,
            initial_top_vector_score=initial_top_vector_score,
        ):
            rewrite_debug = self._rewrite_debug_state(
                term_groups=term_groups,
                rewrite_enabled=self.query_rewriter is not None,
                rewrite_attempted=True,
                rewrite_applied=False,
                rewrite_skipped_reason="",
            )
            rewritten_queries, rewrite_failed = await self.try_rewrite_queries(query, term_groups)
            rewrite_reason = self.query_rewrite_reason(
                query,
                candidates,
                term_groups,
                top_k,
                initial_top_vector_score=initial_top_vector_score,
            )
            if rewritten_queries:
                rewritten_term_groups = self.merged_term_groups(
                    term_groups,
                    *[query_term_groups(rewrite) for rewrite in rewritten_queries],
                )
                rewritten_retrieval_queries = self.dedupe_queries(
                    [
                        *retrieval_queries,
                        *rewritten_queries,
                        *[
                            variant
                            for rewrite in rewritten_queries
                            for variant in retrieval_query_variants(
                                rewrite,
                                query_term_groups(rewrite),
                            )
                        ],
                    ]
                )
                rewritten_candidates, _ = self.retrieve_candidates_for_variants_with_vector_score(
                    query,
                    top_k,
                    source_ids,
                    rewritten_term_groups,
                    rewritten_retrieval_queries,
                )
                original_reranked = self.ranker.rerank_candidates(query, candidates, term_groups, top_k)
                rewritten_reranked = self.ranker.rerank_candidates(
                    query,
                    rewritten_candidates,
                    rewritten_term_groups,
                    top_k,
                )
                if self.prefer_rewritten_results(original_reranked, rewritten_reranked):
                    rewrite_applied = True
                    rewrite_debug = self._rewrite_debug_state(
                        term_groups=term_groups,
                        rewrite_enabled=self.query_rewriter is not None,
                        rewrite_attempted=True,
                        rewrite_applied=True,
                        rewrite_skipped_reason="",
                    )
                    effective_term_groups = rewritten_term_groups
                    selected_retrieval_queries = rewritten_retrieval_queries
                    selected_reranked = rewritten_reranked
                else:
                    rewrite_debug = self._rewrite_debug_state(
                        term_groups=term_groups,
                        rewrite_enabled=self.query_rewriter is not None,
                        rewrite_attempted=True,
                        rewrite_applied=False,
                        rewrite_skipped_reason="not_better_than_original",
                    )
                    selected_reranked = original_reranked
            elif rewrite_failed:
                rewrite_debug = self._rewrite_debug_state(
                    term_groups=term_groups,
                    rewrite_enabled=self.query_rewriter is not None,
                    rewrite_attempted=True,
                    rewrite_applied=False,
                    rewrite_skipped_reason="rewrite_failed",
                )
            else:
                rewrite_debug = self._rewrite_debug_state(
                    term_groups=term_groups,
                    rewrite_enabled=self.query_rewriter is not None,
                    rewrite_attempted=True,
                    rewrite_applied=False,
                    rewrite_skipped_reason="empty_result",
                )
        else:
            rewrite_debug = self._rewrite_debug_state(
                term_groups=term_groups,
                rewrite_enabled=self.query_rewriter is not None,
                rewrite_attempted=False,
                rewrite_applied=False,
                rewrite_skipped_reason=(
                    "disabled" if self.query_rewriter is None else "not_needed"
                ),
            )

        reranked = selected_reranked or self.ranker.rerank_candidates(
            query,
            candidates,
            effective_term_groups,
            top_k,
        )
        return {
            "candidates": reranked,
            "retrieval_queries": selected_retrieval_queries,
            "rewritten_queries": rewritten_queries,
            "original_term_groups": term_groups,
            "effective_term_groups": effective_term_groups,
            "query_rewrite_reason": rewrite_reason or "",
            "query_rewrite_attempted": bool(rewrite_reason),
            "query_rewrite_applied": rewrite_applied,
            "initial_top_vector_score": initial_top_vector_score,
            "final_top_score": self.top_score(reranked),
            "rewrite_debug": rewrite_debug,
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
    ) -> tuple[list[dict[str, Any]], float]:

        index = self.indexer.get_or_create_index()
        base_limit = max(top_k, top_k * self.config.search_multiplier)
        max_limit = self.max_retrieval_limit(base_limit)
        candidate_map: dict[str, dict[str, Any]] = {}
        rejected = set()
        limit = base_limit
        top_initial_vector_score = 0.0

        while limit <= max_limit:
            retriever = self.vector_retriever_cls(
                index=index,
                similarity_top_k=limit,
                vector_store_query_mode="hybrid",
                filters=metadata_filters(source_ids),
            )
            max_node_count = 0
            for retrieval_query in query_variants:
                nodes = retriever.retrieve(retrieval_query)
                max_node_count = max(max_node_count, len(nodes))
                for node in nodes:
                    score = float(node.score or 0.0)
                    top_initial_vector_score = max(top_initial_vector_score, score)
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
                    if not self.ranker.document_intent_allows_chunk(query, term_groups, chunk):
                        rejected.add(chunk_id)
                        continue
                    rejected.discard(chunk_id)
                    existing = candidate_map.get(chunk_id)
                    if existing is None or score > float(existing.get("score", 0.0)):
                        candidate_map[chunk_id] = {
                            "chunk_id": chunk_id,
                            "score": score,
                        }
            candidates = list(candidate_map.values())
            if len(candidates) >= top_k:
                break
            if max_node_count < limit:
                break
            next_limit = min(limit * 2, max_limit)
            if next_limit == limit:
                break
            limit = next_limit

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
        force_textual_metadata_fallback = (
            missing_textual_matches
            and bool(repository_lookup_terms_from_groups(term_groups))
        )
        if (
            should_run_metadata_fallback(
                query,
                term_groups,
                candidates,
                top_k,
                source_ids,
            )
            or force_textual_metadata_fallback
        ) and not (
            lowercase_github_probe
            and len(candidates) >= top_k
            and not missing_textual_matches
        ):
            metadata_candidates = self.ranker.metadata_fallback_candidates(
                query,
                top_k,
                source_ids,
                term_groups,
                candidates,
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

    async def rewrite_queries(self, query: str, term_groups: list[set[str]]) -> list[str]:
        queries, _ = await self.try_rewrite_queries(query, term_groups)
        return queries

    async def try_rewrite_queries(
        self,
        query: str,
        term_groups: list[set[str]],
    ) -> tuple[list[str], bool]:
        if self.query_rewriter is None:
            return [], False
        try:
            return await self.query_rewriter.rewrite_query(query, term_groups), False
        except Exception:
            return [], True

    def should_try_query_rewrite(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        term_groups: list[set[str]],
        top_k: int,
        initial_top_vector_score: float | None = None,
    ) -> bool:
        return bool(
            self.query_rewrite_reason(
                query,
                candidates,
                term_groups,
                top_k,
                initial_top_vector_score=initial_top_vector_score,
            )
        )

    def query_rewrite_reason(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        term_groups: list[set[str]],
        top_k: int,
        initial_top_vector_score: float | None = None,
    ) -> str:
        if self.query_rewriter is None or not term_groups:
            return ""
        if not candidates:
            return "no_initial_candidates"
        textual_matches = self.ranker.candidates_have_textual_matches(candidates, term_groups)
        if not textual_matches:
            return "missing_textual_match"
        top_vector_score = (
            float(initial_top_vector_score)
            if initial_top_vector_score is not None
            else self.top_vector_score(candidates)
        )
        top_candidate = max(
            candidates,
            key=lambda candidate: float(
                candidate.get("vector_score", candidate.get("score", 0.0)) or 0.0
            ),
        )
        unique_chunk_ids = {
            str(candidate.get("chunk_id") or "")
            for candidate in candidates
            if candidate.get("chunk_id")
        }
        has_high_confidence_vector_hit = any(
            candidate.get("vector_score") is not None
            and float(candidate.get("vector_score", 0.0) or 0.0)
            >= QueryRewritePolicy.LOW_INITIAL_SCORE_THRESHOLD
            for candidate in candidates
        )
        if (
            len(unique_chunk_ids) == 1
            and len(candidates) < top_k
            and top_vector_score >= QueryRewritePolicy.LOW_INITIAL_SCORE_THRESHOLD
            and has_high_confidence_vector_hit
            and self._candidate_exactly_matches_query(query, top_candidate)
        ):
            return ""
        if top_vector_score < QueryRewritePolicy.LOW_INITIAL_SCORE_THRESHOLD:
            return "low_initial_vector_score"
        return ""

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
        return max((float(candidate.get("score", 0.0) or 0.0) for candidate in candidates), default=0.0)

    def prefer_rewritten_results(
        self,
        original_reranked: list[dict[str, Any]],
        rewritten_reranked: list[dict[str, Any]],
    ) -> bool:
        return self.result_set_signature(rewritten_reranked) > self.result_set_signature(original_reranked)

    @staticmethod
    def result_set_signature(
        candidates: list[dict[str, Any]],
    ) -> tuple[tuple[tuple[float, float], ...], float, float, int]:
        per_item = tuple(
            (
                float(candidate.get("score", 0.0) or 0.0),
                float(candidate.get("vector_score", 0.0) or 0.0),
            )
            for candidate in candidates
        )
        return (
            per_item,
            sum(score for score, _ in per_item),
            sum(vector_score for _, vector_score in per_item),
            len(per_item),
        )

    def _candidate_exactly_matches_query(
        self,
        query: str,
        candidate: dict[str, Any],
    ) -> bool:
        chunk_id = str(candidate.get("chunk_id") or "")
        if not chunk_id:
            return False
        chunk = self.metadata_store.get_chunk(chunk_id)
        if chunk is None:
            return False
        document = chunk.to_document_model(platform=self.ranker.document_platform(chunk.source_id))
        normalized_query = " ".join(str(query or "").split()).lower()
        if not normalized_query:
            return False
        haystack = " ".join(
            [
                document.title or "",
                document.document_id or "",
                document.path or "",
                document.url or "",
                document.content or "",
            ]
        ).lower()
        return normalized_query in haystack

    @staticmethod
    def _rewrite_debug_state(
        *,
        term_groups: list[set[str]],
        rewrite_enabled: bool,
        rewrite_attempted: bool,
        rewrite_applied: bool,
        rewrite_skipped_reason: str,
    ) -> dict[str, Any]:
        if not rewrite_enabled and not rewrite_skipped_reason:
            rewrite_skipped_reason = "disabled"
        if rewrite_enabled and not term_groups and not rewrite_attempted and not rewrite_applied:
            rewrite_skipped_reason = rewrite_skipped_reason or "no_term_groups"
        return {
            "rewrite_enabled": rewrite_enabled,
            "rewrite_attempted": rewrite_attempted,
            "rewrite_applied": rewrite_applied,
            "rewrite_skipped_reason": rewrite_skipped_reason,
        }

    @staticmethod
    def dedupe_queries(queries: list[str]) -> list[str]:
        deduped = []
        seen = set()
        for query in queries:
            normalized = " ".join(str(query or "").split())
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(normalized)
        return deduped

    @staticmethod
    def merged_term_groups(*group_lists: list[set[str]]) -> list[set[str]]:
        merged = []
        seen = set()
        for group_list in group_lists:
            for group in group_list:
                key = tuple(sorted(group))
                if key in seen:
                    continue
                seen.add(key)
                merged.append(set(group))
        return merged

    def max_retrieval_limit(self, base_limit: int) -> int:
        collection = getattr(self.indexer, "collection", None)
        if collection is not None and hasattr(collection, "count"):
            try:
                return max(base_limit, int(collection.count()))
            except Exception:
                pass
        return max(base_limit, base_limit * QueryRewritePolicy.MAX_RETRIEVAL_LIMIT_MULTIPLIER)


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


def metadata_filters(source_ids: list[str] | None):
    filters = [MetadataFilter(key="contextwiki_managed", value="true")]
    if not source_ids:
        return MetadataFilters(filters=filters)
    if len(source_ids) == 1:
        filters.append(MetadataFilter(key="source_id", value=source_ids[0]))
    else:
        filters.append(
            MetadataFilter(
                key="source_id",
                value=source_ids,
                operator=FilterOperator.IN,
            )
        )
    return MetadataFilters(filters=filters)
