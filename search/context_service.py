from collections.abc import Callable, Iterable
from datetime import datetime, timezone
import inspect
from typing import Any, cast

from llama_index.core.retrievers import VectorIndexRetriever

from core.models import (
    ChunkModel,
    ContextSearchResult,
    DocumentModel,
    DocumentSearchResult,
    DocumentSortBy,
    SearchFilters,
    SearchSortBy,
    SortOrder,
)
from environments.config import AppConfig
from search.intent import classify_intent
from search import debug_redaction, ranking
from search.ranking import ContextCandidateRanker
from search.retrieval_pipeline import (
    BoundedRetrievalExecutor,
    ContextRetrievalPipeline,
    managed_hit_matches_chunk,
    metadata_filters,
)
from storage.metadata_store import MetadataStore


class _HydrationCachedMetadataStore:
    """Per-search facade that avoids repeated hydration across refill rounds."""

    def __init__(
        self,
        metadata_store: MetadataStore,
        *,
        enable_batch_hydration: bool = False,
    ):
        self._metadata_store = metadata_store
        self._enable_batch_hydration = enable_batch_hydration
        self.chunks: dict[str, ChunkModel | None] = {}
        self.documents: dict[str, DocumentModel | None] = {}
        self.sources: dict[str, Any | None] = {}

    def get_chunk(self, chunk_id: str) -> ChunkModel | None:
        if chunk_id not in self.chunks:
            self.chunks[chunk_id] = self._metadata_store.get_chunk(chunk_id)
        return self.chunks[chunk_id]

    def get_document(self, document_id: str) -> DocumentModel | None:
        if document_id not in self.documents:
            self.documents[document_id] = self._metadata_store.get_document(document_id)
        return self.documents[document_id]

    def get_source(self, source_id: str):
        if source_id not in self.sources:
            self.sources[source_id] = self._metadata_store.get_source(source_id)
        return self.sources[source_id]

    def prime_active_evidence_snapshots(self, chunk_ids: Iterable[str]) -> None:
        """Batch-prime active chunk/document rows before ranking hydration."""
        if not self._enable_batch_hydration:
            return
        requested = list(
            dict.fromkeys(
                chunk_id
                for chunk_id in chunk_ids
                if chunk_id and chunk_id not in self.chunks
            )
        )
        if not requested:
            return
        loader = getattr(self._metadata_store, "get_active_evidence_snapshots", None)
        if not callable(loader):
            return
        snapshots = loader(requested)
        for chunk_id in requested:
            snapshot = snapshots.get(chunk_id)
            if not isinstance(snapshot, (tuple, list)) or len(snapshot) != 2:
                self.chunks[chunk_id] = None
                continue
            chunk, document = snapshot
            self.chunks[chunk_id] = chunk
            self.documents[chunk.document_id] = document

    def __getattr__(self, name: str):
        return getattr(self._metadata_store, name)


class ContextSearchService:
    """Structured citation search over indexed chunks."""

    def __init__(
        self,
        metadata_store: MetadataStore,
        indexer=None,
        config: AppConfig | None = None,
        retriever: Callable | Iterable[DocumentModel] | None = None,
        vector_retriever_cls=None,
        default_source_ids: Iterable[str] | None = None,
        retrieval_timeout_seconds: float | None = None,
        retrieval_max_concurrency: int | None = None,
    ):
        self.metadata_store = metadata_store
        self.indexer = indexer
        self.config = config or AppConfig()
        self.retriever = retriever
        self.vector_retriever_cls = vector_retriever_cls or VectorIndexRetriever
        self.default_source_ids = self._normalize_default_source_ids(default_source_ids)
        self._default_source_id_set = set(self.default_source_ids or ())
        self.ranker = ContextCandidateRanker(self.metadata_store, self.config)
        self._retrieval_executor = BoundedRetrievalExecutor(
            timeout_seconds=(
                self.config.request_timeout
                if retrieval_timeout_seconds is None
                else retrieval_timeout_seconds
            ),
            max_concurrency=(
                self.config.connection_limit
                if retrieval_max_concurrency is None
                else retrieval_max_concurrency
            ),
        )

    @property
    def retrieval_executor(self) -> BoundedRetrievalExecutor:
        """Shared bounded executor for multi-step internal retrieval requests."""
        return self._retrieval_executor

    async def search_context(
        self,
        query: str,
        filters: SearchFilters | dict | None = None,
        top_k: int = 10,
        include_debug: bool = False,
        include_internal_metadata: bool = False,
        candidate_budget: int | None = None,
        candidate_metadata_filters: dict[str, list[str]] | None = None,
        _retrieval_deadline: float | None = None,
    ) -> dict:
        filter_payload = self._filter_payload(filters)
        source_ids = self._effective_source_ids(filter_payload)
        if source_ids == []:
            return self._empty_search_result(
                query,
                source_ids=[],
                include_debug=include_debug,
                include_internal_metadata=include_internal_metadata,
            )
        normalized_filters = self._normalized_filters(filter_payload)
        bounded_candidate_budget = self._candidate_budget(candidate_budget)
        bounded_metadata_filters = self._candidate_metadata_filters(
            candidate_metadata_filters
        )
        retrieval_limit = min(top_k, bounded_candidate_budget or top_k)
        max_limit = (
            bounded_candidate_budget
            if bounded_candidate_budget is not None
            else self._max_retrieval_limit(retrieval_limit)
        )
        hydration_store = _HydrationCachedMetadataStore(
            self.metadata_store,
            enable_batch_hydration=bounded_candidate_budget is not None,
        )
        has_date_filters = self._has_date_filters(filter_payload)
        cached_pipeline = (
            self._pipeline(
                metadata_store=cast(MetadataStore, hydration_store),
            )
            if (
                has_date_filters
                or bounded_candidate_budget is not None
                or bounded_metadata_filters is not None
            )
            else None
        )

        while True:
            retrieval_debug = (
                await cached_pipeline.retrieve_candidates(
                    query,
                    retrieval_limit,
                    source_ids,
                    hard_candidate_limit=bounded_candidate_budget,
                    candidate_metadata_filters=bounded_metadata_filters,
                    deadline=_retrieval_deadline,
                )
                if cached_pipeline is not None
                else await self._retrieve_candidates(
                    query,
                    retrieval_limit,
                    source_ids,
                )
            )
            results = self._context_results(
                retrieval_debug["candidates"],
                source_ids,
                normalized_filters,
                top_k,
                chunk_cache=hydration_store.chunks,
                document_cache=hydration_store.documents,
                source_cache=hydration_store.sources,
            )
            if (
                len(results) >= top_k
                or not has_date_filters
                or len(retrieval_debug["candidates"]) < retrieval_limit
                or retrieval_limit >= max_limit
            ):
                break
            retrieval_limit = min(retrieval_limit * 2, max_limit)

        effective_term_groups = retrieval_debug["effective_term_groups"]
        return self._search_response(
            query,
            results,
            retrieval_debug,
            effective_term_groups,
            source_ids=source_ids,
            include_debug=include_debug,
            include_internal_metadata=include_internal_metadata,
        )

    @staticmethod
    def _candidate_budget(value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("candidate_budget must be a positive integer")
        return value

    @staticmethod
    def _candidate_metadata_filters(
        value: dict[str, list[str]] | None,
    ) -> dict[str, list[str]] | None:
        if value is None:
            return None
        allowed_keys = {
            "evidence_source_type",
            "experience_type",
            "document_id",
        }
        if set(value) - allowed_keys:
            raise ValueError("unsupported candidate metadata filter")
        normalized: dict[str, list[str]] = {}
        for key, raw_values in value.items():
            if not isinstance(raw_values, list):
                raise ValueError("candidate metadata filter values must be lists")
            values = list(
                dict.fromkeys(
                    str(item) for item in raw_values if isinstance(item, str) and item
                )
            )
            if values:
                normalized[key] = values
        return normalized or None

    async def search_documents(
        self,
        query: str,
        filters: SearchFilters | dict | None = None,
        sort_by: SearchSortBy | str = SearchSortBy.RELEVANCE,
        sort_order: SortOrder | str = SortOrder.DESC,
        top_k: int = 10,
    ) -> dict:
        normalized_sort_by = self._normalize_document_sort(sort_by)
        normalized_sort_order = self._normalize_sort_order(sort_order)
        filter_payload = self._filter_payload(filters)
        source_ids = self._effective_source_ids(filter_payload)
        if source_ids == []:
            return self._empty_search_result(query, source_ids=[])
        normalized_filters = self._normalized_filters(filter_payload)
        hydration_store = _HydrationCachedMetadataStore(self.metadata_store)
        retrieval_limit = self._document_search_candidate_limit(top_k)
        max_limit = self._max_retrieval_limit(retrieval_limit)
        retrieval_debug = await self._retrieve_candidates_with_hydration_cache(
            query,
            retrieval_limit,
            source_ids,
            hydration_store,
        )
        best_by_document = self._group_document_results(
            retrieval_debug["candidates"],
            source_ids,
            normalized_filters,
            chunk_cache=hydration_store.chunks,
            document_cache=hydration_store.documents,
            source_cache=hydration_store.sources,
        )

        while (
            len(best_by_document) < top_k
            and len(retrieval_debug["candidates"]) >= retrieval_limit
            and retrieval_limit < max_limit
        ):
            retrieval_limit = min(retrieval_limit * 2, max_limit)
            retrieval_debug = await self._retrieve_candidates_with_hydration_cache(
                query,
                retrieval_limit,
                source_ids,
                hydration_store,
            )
            best_by_document = self._group_document_results(
                retrieval_debug["candidates"],
                source_ids,
                normalized_filters,
                chunk_cache=hydration_store.chunks,
                document_cache=hydration_store.documents,
                source_cache=hydration_store.sources,
            )

        results = list(best_by_document.values())
        if normalized_sort_by != "relevance":
            results = self._sort_document_results(
                results,
                normalized_sort_by,
                normalized_sort_order,
            )
        results = results[:top_k]
        effective_term_groups = retrieval_debug["effective_term_groups"]
        return self._search_response(
            query,
            results,
            retrieval_debug,
            effective_term_groups,
            source_ids=source_ids,
        )

    async def search_context_for_answer(
        self,
        query: str,
        filters: SearchFilters | dict | None = None,
        top_k: int = 10,
        *,
        include_debug: bool = False,
    ) -> tuple[dict, dict]:
        payload = await self.search_context(
            query,
            filters=filters,
            top_k=top_k,
            include_debug=include_debug,
            include_internal_metadata=True,
        )
        return payload, payload.get(
            "_internal_grounding", payload.get("_grounding", {})
        )

    def _empty_search_result(
        self,
        query: str,
        source_ids: list[str] | None = None,
        include_debug: bool = False,
        include_internal_metadata: bool = False,
    ) -> dict:
        payload: dict[str, Any] = {
            "query": query,
            "results": [],
        }
        debug_payload = self._build_debug_payload(
            query=query,
            source_ids=source_ids,
            retrieval_debug={
                "retrieval_queries": [],
                "original_term_groups": [],
                "effective_term_groups": [],
                "initial_top_vector_score": 0.0,
                "final_top_score": 0.0,
            },
            effective_term_groups=[],
            results=[],
        )
        if include_debug or include_internal_metadata:
            payload["_grounding"] = {
                "original_term_groups": [],
                "effective_term_groups": [],
            }
        if include_internal_metadata:
            payload["_debug"] = debug_payload
        if include_debug:
            payload["debug"] = debug_payload
        return payload

    def _effective_source_ids(self, filters: dict) -> list[str] | None:
        requested_source_ids = self._normalize_source_ids(filters)
        if self.default_source_ids is None:
            return requested_source_ids
        if requested_source_ids is None:
            return list(self.default_source_ids)
        return [
            source_id
            for source_id in requested_source_ids
            if source_id in self._default_source_id_set
        ]

    @staticmethod
    def _normalize_default_source_ids(
        source_ids: Iterable[str] | None,
    ) -> tuple[str, ...] | None:
        if source_ids is None:
            return None
        if isinstance(source_ids, str):
            values = [source_ids]
        else:
            values = list(source_ids)
        normalized = []
        for source_id in values:
            if source_id and source_id not in normalized:
                normalized.append(str(source_id))
        return tuple(normalized)

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

    @staticmethod
    def _filter_payload(filters: SearchFilters | dict | None) -> dict[str, Any]:
        if filters is None:
            return {}
        if isinstance(filters, dict):
            return dict(filters)
        model_dump = getattr(filters, "model_dump", None)
        if callable(model_dump):
            return model_dump(mode="json", exclude_none=True)
        raise TypeError("filters must be SearchFilters, a mapping, or None")

    @staticmethod
    def _normalized_filters(filters: dict[str, Any]) -> SearchFilters:
        return SearchFilters.model_validate(filters)

    @staticmethod
    def _has_date_filters(filters: dict[str, Any]) -> bool:
        return any(
            filters.get(field)
            for field in (
                "published_from",
                "published_to",
                "modified_from",
                "modified_to",
                "indexed_from",
                "indexed_to",
            )
        )

    def _pipeline(
        self,
        metadata_store: MetadataStore | None = None,
    ) -> ContextRetrievalPipeline:
        active_store = metadata_store or self.metadata_store
        return ContextRetrievalPipeline(
            metadata_store=active_store,
            config=self.config,
            indexer=self.indexer,
            retriever=self.retriever,
            vector_retriever_cls=self.vector_retriever_cls,
            ranker=(
                self.ranker
                if metadata_store is None
                else ContextCandidateRanker(active_store, self.config)
            ),
            retrieval_executor=self._retrieval_executor,
        )

    async def _retrieve_candidates(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
        metadata_store: MetadataStore | None = None,
    ) -> dict[str, Any]:
        return await self._pipeline(metadata_store=metadata_store).retrieve_candidates(
            query,
            top_k,
            source_ids,
        )

    async def _retrieve_candidates_with_hydration_cache(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
        hydration_store: _HydrationCachedMetadataStore,
    ) -> dict[str, Any]:
        retrieve_candidates = self._retrieve_candidates
        if "metadata_store" in inspect.signature(retrieve_candidates).parameters:
            return await retrieve_candidates(
                query,
                top_k,
                source_ids,
                metadata_store=cast(MetadataStore, hydration_store),
            )
        return await retrieve_candidates(query, top_k, source_ids)

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

    def _metadata_fallback_candidates(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
        term_groups: list[set[str]],
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return self.ranker.metadata_fallback_candidates(
            query, top_k, source_ids, term_groups, candidates
        )

    @staticmethod
    def _managed_hit_matches_chunk(metadata: dict[str, Any], chunk) -> bool:
        return managed_hit_matches_chunk(metadata, chunk)

    @staticmethod
    def _metadata_filters(
        source_ids: list[str] | None,
        candidate_metadata_filters: dict[str, list[str]] | None = None,
    ):
        return metadata_filters(source_ids, candidate_metadata_filters)

    def _keyword_candidates(
        self,
        query: str,
        documents: Iterable[DocumentModel],
        top_k: int,
        source_ids: list[str] | None,
        term_groups: list[set[str]] | None = None,
    ) -> list[dict[str, Any]]:
        return self.ranker.keyword_candidates(
            query, documents, top_k, source_ids, term_groups
        )

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

    def _is_document_like(
        self, document: DocumentModel, metadata_haystack: str
    ) -> bool:
        return self.ranker.is_document_like(document, metadata_haystack)

    def _document_intent_allows_chunk(
        self,
        query: str,
        term_groups: list[set[str]],
        chunk: ChunkModel,
    ) -> bool:
        return self.ranker.document_intent_allows_chunk(query, term_groups, chunk)

    @staticmethod
    def _term_group_matches(
        term_group: set[str],
        haystack: str,
        metadata_haystack: str,
        is_document_like: bool,
    ) -> bool:
        return ranking.term_group_matches(
            term_group, haystack, metadata_haystack, is_document_like
        )

    @staticmethod
    def _query_term_groups(query: str) -> list[set[str]]:
        return ranking.query_term_groups(query)

    @staticmethod
    def _append_query_term_group(
        raw_term: str, groups: list[set[str]], seen: set[tuple[str, ...]]
    ):
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
        return ranking.should_run_metadata_fallback(
            query, term_groups, candidates, top_k, source_ids
        )

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
    def _github_metadata_anchor_groups(
        query: str, term_groups: list[set[str]]
    ) -> list[set[str]]:
        return ranking.github_metadata_anchor_groups(query, term_groups)

    @staticmethod
    def _ordinary_metadata_lookup_terms(term_groups: list[set[str]]) -> set[str] | None:
        return ranking.ordinary_metadata_lookup_terms(term_groups)

    @staticmethod
    def _document_intent_metadata_lookup_terms(
        term_groups: list[set[str]],
    ) -> set[str] | None:
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
        return self.ranker.document_matches_source_type_terms(
            document, source_type_terms
        )

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
    def _strong_anchor_lookup_terms(
        term_groups: list[set[str]],
    ) -> tuple[set[str], set[str]]:
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
        return self.ranker.includes_text_in_metadata_lookup(
            query, term_groups, source_ids
        )

    def _requires_document_like_metadata_lookup(
        self,
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> bool:
        return self.ranker.requires_document_like_metadata_lookup(
            term_groups, source_ids
        )

    def _prefers_document_like_metadata_lookup(
        self,
        query: str,
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> bool:
        return self.ranker.prefers_document_like_metadata_lookup(
            query, term_groups, source_ids
        )

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
    def _query_looks_like_repository_name_from_groups(
        term_groups: list[set[str]],
    ) -> bool:
        return ranking.query_looks_like_repository_name_from_groups(term_groups)

    @staticmethod
    def _query_has_strong_repository_signal(
        query: str, term_groups: list[set[str]]
    ) -> bool:
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

    def _build_debug_payload(
        self,
        *,
        query: str,
        source_ids: list[str] | None,
        retrieval_debug: dict[str, Any],
        effective_term_groups: list[set[str]],
        results: list[ContextSearchResult] | list[DocumentSearchResult],
    ) -> dict[str, Any]:
        intent_decision = classify_intent(query, effective_term_groups)
        retrieval_queries = [
            self._redact_debug_query_text(value)
            for value in retrieval_debug.get("retrieval_queries", [])
        ]
        return {
            "intent": intent_decision.as_debug_payload(),
            "retrieval_queries": retrieval_queries,
            "effective_term_groups": [
                [self._redact_debug_term(term) for term in sorted(group)]
                for group in effective_term_groups
            ],
            "original_term_groups": [
                [self._redact_debug_term(term) for term in sorted(group)]
                for group in retrieval_debug.get("original_term_groups", [])
            ],
            "filters": {"source_ids": list(source_ids or [])},
            "initial_top_vector_score": round(
                float(retrieval_debug.get("initial_top_vector_score", 0.0) or 0.0),
                4,
            ),
            "final_top_score": round(
                float(retrieval_debug.get("final_top_score", 0.0) or 0.0),
                4,
            ),
            "selected_results": [
                self._debug_result_payload(item, effective_term_groups)
                for item in results
            ],
            "result_summary": {
                "returned_chunks": len(results),
                "retrieval_queries": len(retrieval_queries),
            },
        }

    def _debug_result_payload(
        self,
        item: ContextSearchResult | DocumentSearchResult,
        effective_term_groups: list[set[str]],
    ) -> dict[str, Any]:
        return {
            "chunk_id": item.chunk_id,
            "document_id": item.document_id,
            "source_id": item.source_id,
            "source_type": item.source_type,
            "title": self._redact_debug_query_text(item.title),
            "score": round(float(item.score or 0.0), 4),
            "vector_score": round(float(item.vector_score or 0.0), 4),
            "matched_terms": [
                self._redact_debug_term(term)
                for term in self._matched_terms(item, effective_term_groups)
            ],
            "path": self._safe_debug_location(item.path),
            "url": self._safe_debug_location(item.url),
        }

    @staticmethod
    def _matched_terms(
        item: ContextSearchResult | DocumentSearchResult,
        term_groups: list[set[str]],
    ) -> list[str]:
        haystack = " ".join(
            [
                item.title or "",
                item.document_id or "",
                item.url or "",
                item.path or "",
                getattr(item, "preview", "") or "",
                getattr(item, "matched_context", "") or "",
                getattr(item, "text", "") or "",
            ]
        ).lower()
        matched = []
        for group in term_groups:
            for term in sorted(group):
                if term and term in haystack:
                    matched.append(term)
                    break
        return matched

    def _candidate_chunk(
        self,
        candidate: dict[str, Any],
        source_ids: list[str] | None,
        chunk_cache: dict[str, ChunkModel | None] | None = None,
    ) -> ChunkModel | None:
        chunk_id = candidate["chunk_id"]
        if chunk_cache is not None and chunk_id in chunk_cache:
            chunk = chunk_cache[chunk_id]
        else:
            chunk = self.metadata_store.get_chunk(chunk_id)
            if chunk_cache is not None:
                chunk_cache[chunk_id] = chunk
        if not chunk:
            return None
        if source_ids and chunk.source_id not in source_ids:
            return None
        return chunk

    def _candidate_document(
        self,
        chunk: ChunkModel,
        filters: SearchFilters,
        document_cache: dict[str, DocumentModel | None] | None = None,
    ) -> DocumentModel | None:
        if document_cache is not None and chunk.document_id in document_cache:
            document = document_cache[chunk.document_id]
        else:
            document = self.metadata_store.get_document(chunk.document_id)
            if document_cache is not None:
                document_cache[chunk.document_id] = document
        if document is None:
            return None
        matches_filters = getattr(self.metadata_store, "document_matches_filters", None)
        if callable(matches_filters) and not matches_filters(document, filters):
            return None
        return document

    def _context_results(
        self,
        candidates: list[dict[str, Any]],
        source_ids: list[str] | None,
        filters: SearchFilters,
        top_k: int,
        *,
        chunk_cache: dict[str, ChunkModel | None] | None = None,
        document_cache: dict[str, DocumentModel | None] | None = None,
        source_cache: dict[str, Any | None] | None = None,
    ) -> list[ContextSearchResult]:
        results = []
        for candidate in candidates:
            chunk = self._candidate_chunk(
                candidate,
                source_ids,
                chunk_cache=chunk_cache,
            )
            if not chunk:
                continue
            document = self._candidate_document(
                chunk,
                filters,
                document_cache=document_cache,
            )
            if not document:
                continue
            results.append(
                self._context_search_result(
                    chunk,
                    candidate,
                    document,
                    source_cache=source_cache,
                )
            )
            if len(results) >= top_k:
                break
        return results

    def _context_search_result(
        self,
        chunk: ChunkModel,
        candidate: dict[str, Any],
        document: DocumentModel,
        source_cache: dict[str, Any | None] | None = None,
    ) -> ContextSearchResult:
        return ContextSearchResult(
            chunk_id=chunk.chunk_id,
            document_id=chunk.document_id,
            source_id=chunk.source_id,
            source_type=self._source_type_for_chunk(
                chunk,
                source_cache=source_cache,
            ),
            title=chunk.title,
            url=chunk.url,
            path=chunk.path,
            score=float(candidate.get("score", 0.0)),
            vector_score=float(
                candidate.get("vector_score", candidate.get("score", 0.0))
            ),
            metadata_priority=int(candidate.get("metadata_priority", 0) or 0),
            preview=self._preview(chunk.text),
            text=chunk.text,
            line_start=chunk.line_start,
            line_end=chunk.line_end,
            version_id=chunk.version_id,
            updated_at=chunk.updated_at,
            published_at=document.published_at,
            modified_at=document.modified_at,
            indexed_at=document.indexed_at,
            date_provenance=document.date_provenance,
        )

    def _document_search_result(
        self,
        chunk: ChunkModel,
        candidate: dict[str, Any],
        document: DocumentModel,
        source_cache: dict[str, Any | None] | None = None,
    ) -> DocumentSearchResult:
        return DocumentSearchResult(
            document_id=chunk.document_id,
            chunk_id=chunk.chunk_id,
            source_id=chunk.source_id,
            source_type=self._source_type_for_chunk(
                chunk,
                source_cache=source_cache,
            ),
            title=chunk.title,
            url=chunk.url,
            path=chunk.path,
            score=float(candidate.get("score", 0.0)),
            vector_score=float(
                candidate.get("vector_score", candidate.get("score", 0.0))
            ),
            metadata_priority=int(candidate.get("metadata_priority", 0) or 0),
            matched_context=chunk.text or "",
            published_at=document.published_at,
            modified_at=document.modified_at,
            indexed_at=document.indexed_at,
            date_provenance=document.date_provenance,
        )

    def _source_type_for_chunk(
        self,
        chunk: ChunkModel,
        source_cache: dict[str, Any | None] | None = None,
    ) -> str:
        if source_cache is not None and chunk.source_id in source_cache:
            source = source_cache[chunk.source_id]
        else:
            source = self.metadata_store.get_source(chunk.source_id)
            if source_cache is not None:
                source_cache[chunk.source_id] = source
        return source.source_type.value if source else ""

    def _group_document_results(
        self,
        candidates: list[dict[str, Any]],
        source_ids: list[str] | None,
        filters: SearchFilters,
        *,
        chunk_cache: dict[str, ChunkModel | None] | None = None,
        document_cache: dict[str, DocumentModel | None] | None = None,
        source_cache: dict[str, Any | None] | None = None,
    ) -> dict[str, DocumentSearchResult]:
        best_by_document: dict[str, DocumentSearchResult] = {}
        for candidate in candidates:
            chunk = self._candidate_chunk(
                candidate,
                source_ids,
                chunk_cache=chunk_cache,
            )
            if not chunk:
                continue
            if chunk.document_id in best_by_document:
                continue
            document = self._candidate_document(
                chunk,
                filters,
                document_cache=document_cache,
            )
            if not document:
                continue
            best_by_document[chunk.document_id] = self._document_search_result(
                chunk,
                candidate,
                document,
                source_cache=source_cache,
            )
        return best_by_document

    @staticmethod
    def _normalize_document_sort(sort_by: SearchSortBy | str) -> str:
        value = getattr(sort_by, "value", sort_by)
        normalized = str(value)
        if normalized not in {
            SearchSortBy.RELEVANCE.value,
            DocumentSortBy.PUBLISHED_AT.value,
            DocumentSortBy.MODIFIED_AT.value,
            DocumentSortBy.INDEXED_AT.value,
        }:
            raise ValueError(f"Unsupported sort_by: {normalized}")
        return normalized

    @staticmethod
    def _normalize_sort_order(sort_order: SortOrder | str) -> str:
        value = getattr(sort_order, "value", sort_order)
        normalized = str(value)
        if normalized not in {SortOrder.ASC.value, SortOrder.DESC.value}:
            raise ValueError(f"Unsupported sort_order: {normalized}")
        return normalized

    @staticmethod
    def _sort_timestamp(value: str) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _sort_document_results(
        cls,
        results: list[DocumentSearchResult],
        sort_by: str,
        sort_order: str,
    ) -> list[DocumentSearchResult]:
        dated = [
            item
            for item in results
            if cls._sort_timestamp(getattr(item, sort_by, "")) is not None
        ]
        undated = [item for item in results if item not in dated]
        dated.sort(key=lambda item: item.document_id)
        dated.sort(
            key=lambda item: (
                cls._sort_timestamp(getattr(item, sort_by))
                or datetime.min.replace(tzinfo=timezone.utc)
            ),
            reverse=sort_order == SortOrder.DESC.value,
        )
        undated.sort(key=lambda item: item.document_id)
        return dated + undated

    def _document_search_candidate_limit(self, top_k: int) -> int:
        return max(
            top_k,
            top_k * self.config.search_multiplier,
            top_k * 4,
        )

    def _search_response(
        self,
        query: str,
        results: list[ContextSearchResult] | list[DocumentSearchResult],
        retrieval_debug: dict[str, Any],
        effective_term_groups: list[set[str]],
        *,
        source_ids: list[str] | None = None,
        include_debug: bool = False,
        include_internal_metadata: bool = False,
    ) -> dict:
        payload: dict[str, Any] = {
            "query": query,
            "results": results,
        }
        raw_grounding = {
            "original_term_groups": [
                sorted(group)
                for group in retrieval_debug.get("original_term_groups", [])
            ],
            "effective_term_groups": [sorted(group) for group in effective_term_groups],
        }
        debug_payload = self._build_debug_payload(
            query=query,
            source_ids=source_ids,
            retrieval_debug=retrieval_debug,
            effective_term_groups=effective_term_groups,
            results=results,
        )
        if include_debug or include_internal_metadata:
            payload["_grounding"] = {
                "original_term_groups": [
                    [self._redact_debug_term(term) for term in sorted(group)]
                    for group in retrieval_debug.get("original_term_groups", [])
                ],
                "effective_term_groups": [
                    [self._redact_debug_term(term) for term in sorted(group)]
                    for group in effective_term_groups
                ],
            }
        if include_internal_metadata:
            payload["_internal_grounding"] = raw_grounding
            payload["_debug"] = debug_payload
        if include_debug:
            payload["debug"] = debug_payload
        return payload
