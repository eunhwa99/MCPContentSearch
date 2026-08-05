from collections.abc import Callable, Iterable
from typing import Any

from llama_index.core.vector_stores import (
    FilterCondition,
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
    ):
        self.metadata_store = metadata_store
        self.config = config
        self.indexer = indexer
        self.retriever = retriever
        self.vector_retriever_cls = vector_retriever_cls
        self.ranker = ranker

    async def retrieve_candidates(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
    ) -> dict[str, Any]:
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
                top_k,
                source_ids,
                term_groups,
                [],
            )
            reranked = self.ranker.rerank_candidates(query, candidates, term_groups, top_k)
            return {
                "candidates": reranked,
                "retrieval_queries": [query],
                "original_term_groups": term_groups,
                "effective_term_groups": term_groups,
                "initial_top_vector_score": self.top_vector_score(candidates),
                "final_top_score": self.top_score(reranked),
            }

        retrieval_queries = retrieval_query_variants(query, term_groups)
        candidates, initial_top_vector_score = self.retrieve_candidates_for_variants_with_vector_score(
            query,
            top_k,
            source_ids,
            term_groups,
            retrieval_queries,
        )
        reranked = self.ranker.rerank_candidates(
            query,
            candidates,
            term_groups,
            top_k,
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

    def max_retrieval_limit(self, base_limit: int) -> int:
        collection = getattr(self.indexer, "collection", None)
        if collection is not None and hasattr(collection, "count"):
            try:
                return max(base_limit, int(collection.count()))
            except Exception:
                pass
        return max(base_limit, base_limit * RetrievalPolicy.MAX_RETRIEVAL_LIMIT_MULTIPLIER)


MANAGED_METADATA_KEY = "context_zip_managed"
LEGACY_MANAGED_METADATA_KEY = "context" + "wiki_managed"


def managed_hit_matches_chunk(metadata: dict[str, Any], chunk) -> bool:
    if not any(
        metadata.get(key) == "true"
        for key in (MANAGED_METADATA_KEY, LEGACY_MANAGED_METADATA_KEY)
    ):
        return False
    source_id = metadata.get("source_id")
    document_id = metadata.get("document_id")
    if source_id != chunk.source_id:
        return False
    if document_id != chunk.document_id:
        return False
    return True


def metadata_filters(source_ids: list[str] | None):
    managed_filters = MetadataFilters(
        filters=[
            MetadataFilter(key=MANAGED_METADATA_KEY, value="true"),
            MetadataFilter(key=LEGACY_MANAGED_METADATA_KEY, value="true"),
        ],
        condition=FilterCondition.OR,
    )
    filters = [managed_filters]
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
