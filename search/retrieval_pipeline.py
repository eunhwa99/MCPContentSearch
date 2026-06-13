from collections.abc import Callable, Iterable
from typing import Any

from llama_index.core.vector_stores import FilterOperator, MetadataFilter, MetadataFilters

from core.models import DocumentModel
from environments.config import AppConfig
from search.query_terms import retrieval_query_variants
from search.ranking import (
    ContextCandidateRanker,
    query_term_groups,
    should_run_metadata_fallback,
    should_try_lowercase_github_probe,
)
from storage.metadata_store import MetadataStore


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
                "rewrite_debug": self._rewrite_debug_state(
                    term_groups=term_groups,
                    rewrite_enabled=self.query_rewriter is not None,
                    rewrite_attempted=False,
                    rewrite_applied=False,
                    rewrite_skipped_reason="disabled" if self.query_rewriter is None else "not_supported",
                ),
            }

        retrieval_queries = retrieval_query_variants(query, term_groups)
        candidates = self.retrieve_candidates_for_variants(
            query,
            top_k,
            source_ids,
            term_groups,
            retrieval_queries,
        )
        effective_term_groups = term_groups
        rewritten_queries: list[str] = []
        rewrite_reason = ""

        rewrite_reason = ""
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
        elif self.should_try_query_rewrite(candidates, term_groups, top_k):
            rewrite_debug = self._rewrite_debug_state(
                term_groups=term_groups,
                rewrite_enabled=self.query_rewriter is not None,
                rewrite_attempted=True,
                rewrite_applied=False,
                rewrite_skipped_reason="",
            )
            rewritten_queries, rewrite_failed = await self.try_rewrite_queries(query, term_groups)
            rewrite_reason = self.query_rewrite_reason(candidates, term_groups, top_k)
            if rewritten_queries:
                rewrite_debug = self._rewrite_debug_state(
                    term_groups=term_groups,
                    rewrite_enabled=self.query_rewriter is not None,
                    rewrite_attempted=True,
                    rewrite_applied=True,
                    rewrite_skipped_reason="",
                )
                effective_term_groups = self.merged_term_groups(
                    term_groups,
                    *[query_term_groups(rewrite) for rewrite in rewritten_queries],
                )
                retrieval_queries = self.dedupe_queries(
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
                candidates = self.retrieve_candidates_for_variants(
                    query,
                    top_k,
                    source_ids,
                    effective_term_groups,
                    retrieval_queries,
                )
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

        reranked = self.ranker.rerank_candidates(query, candidates, effective_term_groups, top_k)
        return {
            "candidates": reranked,
            "retrieval_queries": retrieval_queries,
            "rewritten_queries": rewritten_queries,
            "original_term_groups": term_groups,
            "effective_term_groups": effective_term_groups,
            "query_rewrite_reason": rewrite_reason or "",
            "query_rewrite_attempted": bool(rewrite_reason),
            "query_rewrite_applied": bool(rewritten_queries),
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

        index = self.indexer.get_or_create_index()
        base_limit = max(top_k, top_k * self.config.search_multiplier)
        max_limit = self.max_retrieval_limit(base_limit)
        candidate_map: dict[str, dict[str, Any]] = {}
        rejected = set()
        limit = base_limit

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
                    if not self.ranker.document_intent_allows_chunk(term_groups, chunk):
                        rejected.add(chunk_id)
                        continue
                    rejected.discard(chunk_id)
                    score = float(node.score or 0.0)
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
        if should_run_metadata_fallback(
            query,
            term_groups,
            candidates,
            top_k,
            source_ids,
        ) and not (
            lowercase_github_probe
            and len(candidates) >= top_k
            and self.ranker.candidates_have_textual_matches(candidates, term_groups)
        ):
            metadata_candidates = self.ranker.metadata_fallback_candidates(
                query,
                top_k,
                source_ids,
                term_groups,
                candidates,
            )

        return self.ranker.merge_ranked_candidates(
            list(candidate_map.values()),
            metadata_candidates,
            rejected,
            top_k,
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
        candidates: list[dict[str, Any]],
        term_groups: list[set[str]],
        top_k: int,
    ) -> bool:
        return bool(self.query_rewrite_reason(candidates, term_groups, top_k))

    def query_rewrite_reason(
        self,
        candidates: list[dict[str, Any]],
        term_groups: list[set[str]],
        top_k: int,
    ) -> str:
        if self.query_rewriter is None or not term_groups:
            return ""
        if not candidates:
            return "no_initial_candidates"
        if len(candidates) < top_k:
            return "insufficient_candidate_count"
        if not self.ranker.candidates_have_textual_matches(candidates, term_groups):
            return "missing_textual_match"
        top_score = max(float(candidate.get("score", 0.0)) for candidate in candidates)
        if top_score < 0.75:
            return "low_initial_score"
        return ""

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
        return max(base_limit, base_limit * 64)


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
