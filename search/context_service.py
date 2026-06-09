import os
from collections.abc import Callable, Iterable
from typing import Any

from llama_index.core.retrievers import VectorIndexRetriever

from core.models import ChunkModel, ContextSearchResult, DocumentModel
from environments.config import AppConfig
from search import debug_redaction, ranking
from search.query_rewrite import build_query_rewriter
from search.ranking import ContextCandidateRanker
from search.retrieval_pipeline import (
    ContextRetrievalPipeline,
    managed_hit_matches_chunk,
    metadata_filters,
)
from storage.metadata_store import MetadataStore


class ContextSearchService:
    """Structured citation search over indexed chunks."""

    def __init__(
        self,
        metadata_store: MetadataStore,
        indexer=None,
        config: AppConfig | None = None,
        retriever: Callable | Iterable[DocumentModel] | None = None,
        query_rewriter=None,
        vector_retriever_cls=None,
    ):
        self.metadata_store = metadata_store
        self.indexer = indexer
        self.config = config or AppConfig()
        self.retriever = retriever
        self.vector_retriever_cls = vector_retriever_cls or VectorIndexRetriever
        api_key = os.getenv(self.config.search_llm_api_key_env_var, "").strip()
        self.query_rewriter = query_rewriter or build_query_rewriter(self.config, api_key=api_key)
        self.ranker = ContextCandidateRanker(self.metadata_store, self.config)

    async def search_context(self, query: str, filters: dict | None = None, top_k: int = 10) -> dict:
        filters = filters or {}
        source_ids = self._normalize_source_ids(filters)
        retrieval_debug = await self._retrieve_candidates(query, top_k, source_ids)
        candidates = retrieval_debug["candidates"]
        effective_term_groups = retrieval_debug["effective_term_groups"]
        results = []

        for candidate in candidates:
            chunk_id = candidate["chunk_id"]
            chunk = self.metadata_store.get_chunk(chunk_id)
            if not chunk:
                continue
            if source_ids and chunk.source_id not in source_ids:
                continue

            source = self.metadata_store.get_source(chunk.source_id)
            source_type = source.source_type.value if source else ""
            results.append(
                ContextSearchResult(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    source_id=chunk.source_id,
                    source_type=source_type,
                    title=chunk.title,
                    url=chunk.url,
                    path=chunk.path,
                    score=candidate["score"],
                    vector_score=float(candidate.get("vector_score", candidate.get("score", 0.0))),
                    metadata_priority=int(candidate.get("metadata_priority", 0) or 0),
                    preview=self._preview(chunk.text),
                    text=chunk.text,
                    line_start=chunk.line_start,
                    line_end=chunk.line_end,
                    version_id=chunk.version_id,
                    updated_at=chunk.updated_at,
                )
            )
            if len(results) >= top_k:
                break

        return {
            "query": query,
            "results": results,
            "_grounding": {
                "original_term_groups": [sorted(group) for group in retrieval_debug.get("original_term_groups", [])],
                "effective_term_groups": [sorted(group) for group in effective_term_groups],
            },
            "debug": {
                "retrieval_queries": [
                    self._redact_debug_query_text(value)
                    for value in retrieval_debug["retrieval_queries"]
                ],
                "rewritten_queries": [
                    self._redact_debug_query_text(value)
                    for value in retrieval_debug["rewritten_queries"]
                ],
                "effective_term_groups": [
                    [self._redact_debug_term(term) for term in sorted(group)]
                    for group in effective_term_groups
                ],
            },
        }

    @staticmethod
    def _normalize_source_ids(filters: dict) -> list[str] | None:
        normalized = []

        for key in ("source_ids", "source_id"):
            value = filters.get(key)
            if not value:
                continue
            if isinstance(value, str):
                values = [value]
            elif isinstance(value, Iterable):
                values = list(value)
            else:
                values = [value]

            for source_id in values:
                if source_id and source_id not in normalized:
                    normalized.append(str(source_id))

        return normalized or None

    def _pipeline(self) -> ContextRetrievalPipeline:
        return ContextRetrievalPipeline(
            metadata_store=self.metadata_store,
            config=self.config,
            indexer=self.indexer,
            retriever=self.retriever,
            query_rewriter=self.query_rewriter,
            vector_retriever_cls=self.vector_retriever_cls,
            ranker=self.ranker,
        )

    async def _retrieve_candidates(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
    ) -> dict[str, Any]:
        return await self._pipeline().retrieve_candidates(query, top_k, source_ids)

    def _retrieve_candidates_for_variants(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
        term_groups: list[set[str]],
        query_variants: list[str],
    ) -> list[dict[str, Any]]:
        return self._pipeline().retrieve_candidates_for_variants(
            query,
            top_k,
            source_ids,
            term_groups,
            query_variants,
        )

    async def _rewrite_queries(self, query: str, term_groups: list[set[str]]) -> list[str]:
        return await self._pipeline().rewrite_queries(query, term_groups)

    def _should_try_query_rewrite(
        self,
        candidates: list[dict[str, Any]],
        term_groups: list[set[str]],
        top_k: int,
    ) -> bool:
        return self._pipeline().should_try_query_rewrite(candidates, term_groups, top_k)

    @staticmethod
    def _dedupe_queries(queries: list[str]) -> list[str]:
        return ContextRetrievalPipeline.dedupe_queries(queries)

    @staticmethod
    def _merged_term_groups(*group_lists: list[set[str]]) -> list[set[str]]:
        return ContextRetrievalPipeline.merged_term_groups(*group_lists)

    def _metadata_fallback_candidates(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
        term_groups: list[set[str]],
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return self.ranker.metadata_fallback_candidates(query, top_k, source_ids, term_groups, candidates)

    @staticmethod
    def _managed_hit_matches_chunk(metadata: dict[str, Any], chunk) -> bool:
        return managed_hit_matches_chunk(metadata, chunk)

    @staticmethod
    def _metadata_filters(source_ids: list[str] | None):
        return metadata_filters(source_ids)

    def _keyword_candidates(
        self,
        query: str,
        documents: Iterable[DocumentModel],
        top_k: int,
        source_ids: list[str] | None,
        term_groups: list[set[str]] | None = None,
    ) -> list[dict[str, Any]]:
        return self.ranker.keyword_candidates(query, documents, top_k, source_ids, term_groups)

    def _metadata_keyword_candidates(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
        *,
        term_groups: list[set[str]] | None = None,
        metadata_terms: set[str] | None = None,
        require_all_metadata_terms: bool = False,
        require_document_like: bool = False,
        prefer_document_like: bool = False,
        include_text: bool | None = None,
        metadata_only_terms: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self.ranker.metadata_keyword_candidates(
            query,
            top_k,
            source_ids,
            term_groups=term_groups,
            metadata_terms=metadata_terms,
            require_all_metadata_terms=require_all_metadata_terms,
            require_document_like=require_document_like,
            prefer_document_like=prefer_document_like,
            include_text=include_text,
            metadata_only_terms=metadata_only_terms,
        )

    def _metadata_chunks(
        self,
        source_ids: list[str] | None,
        metadata_terms: set[str] | None,
        require_document_like: bool,
        include_text: bool = False,
        require_all_terms: bool = False,
        metadata_only_terms: set[str] | None = None,
    ) -> list[ChunkModel] | None:
        return self.ranker.metadata_chunks(
            source_ids,
            metadata_terms,
            require_document_like,
            include_text,
            require_all_terms,
            metadata_only_terms,
        )

    @staticmethod
    def _metadata_terms_are_repo_or_identity(metadata_terms: set[str]) -> bool:
        return ranking.metadata_terms_are_repo_or_identity(metadata_terms)

    def _merge_ranked_candidates(
        self,
        vector_candidates: list[dict[str, Any]],
        metadata_candidates: list[dict[str, Any]],
        rejected_chunk_ids: set[str],
        top_k: int,
    ) -> list[dict[str, Any]]:
        return self.ranker.merge_ranked_candidates(
            vector_candidates,
            metadata_candidates,
            rejected_chunk_ids,
            top_k,
        )

    def _candidates_have_textual_matches(
        self,
        candidates: list[dict[str, Any]],
        term_groups: list[set[str]],
    ) -> bool:
        return self.ranker.candidates_have_textual_matches(candidates, term_groups)

    def _rerank_candidates(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        term_groups: list[set[str]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        return self.ranker.rerank_candidates(query, candidates, term_groups, top_k)

    @staticmethod
    def _document_haystack(document: DocumentModel) -> str:
        return ranking.document_haystack(document)

    @staticmethod
    def _document_metadata_haystack(document: DocumentModel) -> str:
        return ranking.document_metadata_haystack(document)

    def _is_document_like(self, document: DocumentModel, metadata_haystack: str) -> bool:
        return self.ranker.is_document_like(document, metadata_haystack)

    def _document_intent_allows_chunk(self, term_groups: list[set[str]], chunk: ChunkModel) -> bool:
        return self.ranker.document_intent_allows_chunk(term_groups, chunk)

    @staticmethod
    def _term_group_matches(
        term_group: set[str],
        haystack: str,
        metadata_haystack: str,
        is_document_like: bool,
    ) -> bool:
        return ranking.term_group_matches(term_group, haystack, metadata_haystack, is_document_like)

    @staticmethod
    def _query_term_groups(query: str) -> list[set[str]]:
        return ranking.query_term_groups(query)

    @staticmethod
    def _append_query_term_group(raw_term: str, groups: list[set[str]], seen: set[tuple[str, ...]]):
        ranking.append_query_term_group(raw_term, groups, seen)

    @staticmethod
    def _split_attached_latin_korean_token(raw_token: str) -> list[str]:
        return ranking.split_attached_latin_korean_token(raw_token)

    @staticmethod
    def _scoring_term_groups(term_groups: list[set[str]]) -> list[set[str]]:
        return ranking.scoring_term_groups(term_groups)

    @staticmethod
    def _should_run_metadata_fallback(
        query: str,
        term_groups: list[set[str]],
        candidates: list[dict[str, Any]],
        top_k: int,
        source_ids: list[str] | None,
    ) -> bool:
        return ranking.should_run_metadata_fallback(query, term_groups, candidates, top_k, source_ids)

    def _metadata_fallback_source_ids(
        self,
        query: str,
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> list[str] | None:
        return self.ranker.metadata_fallback_source_ids(query, term_groups, source_ids)

    @staticmethod
    def _metadata_lookup_terms(
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> set[str] | None:
        return ranking.metadata_lookup_terms(term_groups, source_ids)

    @staticmethod
    def _github_metadata_anchor_groups(query: str, term_groups: list[set[str]]) -> list[set[str]]:
        return ranking.github_metadata_anchor_groups(query, term_groups)

    @staticmethod
    def _ordinary_metadata_lookup_terms(term_groups: list[set[str]]) -> set[str] | None:
        return ranking.ordinary_metadata_lookup_terms(term_groups)

    @staticmethod
    def _document_intent_metadata_lookup_terms(term_groups: list[set[str]]) -> set[str] | None:
        return ranking.document_intent_metadata_lookup_terms(term_groups)

    @staticmethod
    def _preferred_metadata_terms(terms: set[str]) -> set[str]:
        return ranking.preferred_metadata_terms(terms)

    @classmethod
    def _preferred_query_phrases(
        cls,
        query: str,
        term_groups: list[set[str]],
    ) -> list[str]:
        return ranking.preferred_query_phrases(query, term_groups)

    @staticmethod
    def _phrase_match_bonus(phrases: list[str], metadata_haystack: str) -> float:
        return ranking.phrase_match_bonus(phrases, metadata_haystack)

    @staticmethod
    def _query_source_type_terms(term_groups: list[set[str]]) -> set[str]:
        return ranking.query_source_type_terms(term_groups)

    def _document_matches_source_type_terms(
        self,
        document: DocumentModel,
        source_type_terms: set[str],
    ) -> bool:
        return self.ranker.document_matches_source_type_terms(document, source_type_terms)

    @classmethod
    def _redact_debug_query_text(cls, value: str) -> str:
        return debug_redaction.redact_debug_query_text(value)

    @classmethod
    def _redact_debug_term(cls, value: str) -> str:
        return debug_redaction.redact_debug_term(value)

    @staticmethod
    def _safe_debug_location(value: str) -> str:
        return debug_redaction.safe_debug_location(value)

    @staticmethod
    def _metadata_lookup_topical_terms(term_groups: list[set[str]]) -> set[str]:
        return ranking.metadata_lookup_topical_terms(term_groups)

    @staticmethod
    def _strong_anchor_lookup_terms(term_groups: list[set[str]]) -> tuple[set[str], set[str]]:
        return ranking.strong_anchor_lookup_terms(term_groups)

    @staticmethod
    def _requires_all_metadata_lookup_terms(term_groups: list[set[str]]) -> bool:
        return ranking.requires_all_metadata_lookup_terms(term_groups)

    @staticmethod
    def _allows_github_document_body_lookup(term_groups: list[set[str]]) -> bool:
        return ranking.allows_github_document_body_lookup(term_groups)

    def _includes_text_in_metadata_lookup(
        self,
        query: str,
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> bool:
        return self.ranker.includes_text_in_metadata_lookup(query, term_groups, source_ids)

    def _requires_document_like_metadata_lookup(
        self,
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> bool:
        return self.ranker.requires_document_like_metadata_lookup(term_groups, source_ids)

    def _prefers_document_like_metadata_lookup(
        self,
        query: str,
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> bool:
        return self.ranker.prefers_document_like_metadata_lookup(query, term_groups, source_ids)

    @staticmethod
    def _query_is_metadata_like(query: str, term_groups: list[set[str]]) -> bool:
        return ranking.query_is_metadata_like(query, term_groups)

    def _github_source_ids(self, source_ids: list[str] | None = None) -> set[str]:
        return self.ranker.github_source_ids(source_ids)

    def _source_ids_include_github(self, source_ids: list[str] | None) -> bool:
        return self.ranker.source_ids_include_github(source_ids)

    def _is_github_source_id(self, source_id: str) -> bool:
        return self.ranker.is_github_source_id(source_id)

    def _is_github_document(self, document: DocumentModel) -> bool:
        return self.ranker.is_github_document(document)

    def _document_platform(self, source_id: str) -> str:
        return self.ranker.document_platform(source_id)

    @staticmethod
    def _should_try_lowercase_github_probe(
        query: str,
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> bool:
        return ranking.should_try_lowercase_github_probe(query, term_groups, source_ids)

    @staticmethod
    def _query_looks_like_repository_name(query: str) -> bool:
        return ranking.query_looks_like_repository_name(query)

    @staticmethod
    def _query_looks_like_repository_name_from_groups(term_groups: list[set[str]]) -> bool:
        return ranking.query_looks_like_repository_name_from_groups(term_groups)

    @staticmethod
    def _query_has_strong_repository_signal(query: str, term_groups: list[set[str]]) -> bool:
        return ranking.query_has_strong_repository_signal(query, term_groups)

    @staticmethod
    def _query_has_lowercase_repository_probe(term_groups: list[set[str]]) -> bool:
        return ranking.query_has_lowercase_repository_probe(term_groups)

    @staticmethod
    def _repository_lookup_terms_from_groups(term_groups: list[set[str]]) -> set[str]:
        return ranking.repository_lookup_terms_from_groups(term_groups)

    @staticmethod
    def _is_generic_single_token_term(term: str) -> bool:
        return ranking.is_generic_single_token_term(term)

    @staticmethod
    def _token_looks_like_api_path(token: str) -> bool:
        return ranking.token_looks_like_api_path(token)

    @staticmethod
    def _preview(text: str, length: int = 240) -> str:
        return ranking.preview(text, length)

    def _max_retrieval_limit(self, base_limit: int) -> int:
        return self._pipeline().max_retrieval_limit(base_limit)
