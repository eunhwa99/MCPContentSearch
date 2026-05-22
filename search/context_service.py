from collections.abc import Callable, Iterable
from typing import Any

from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.vector_stores import FilterOperator, MetadataFilter, MetadataFilters

from core.models import ChunkModel, ContextSearchResult, DocumentModel
from environments.config import AppConfig
from storage.metadata_store import MetadataStore


class ContextSearchService:
    """Structured citation search over indexed chunks."""

    def __init__(
        self,
        metadata_store: MetadataStore,
        indexer=None,
        config: AppConfig | None = None,
        retriever: Callable | Iterable[DocumentModel] | None = None,
    ):
        self.metadata_store = metadata_store
        self.indexer = indexer
        self.config = config or AppConfig()
        self.retriever = retriever

    async def search_context(self, query: str, filters: dict | None = None, top_k: int = 10) -> dict:
        filters = filters or {}
        source_ids = self._normalize_source_ids(filters)
        candidates = self._retrieve_candidates(query, top_k, source_ids)
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
                    preview=self._preview(chunk.text),
                    text=chunk.text,
                    line_start=chunk.line_start,
                    line_end=chunk.line_end,
                    updated_at=chunk.updated_at,
                )
            )
            if len(results) >= top_k:
                break

        return {"query": query, "results": results}

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

    def _retrieve_candidates(self, query: str, top_k: int, source_ids: list[str] | None) -> list[dict[str, Any]]:
        if self.retriever is not None:
            if callable(self.retriever):
                return list(self.retriever(query, top_k, source_ids))
            return self._keyword_candidates(query, self.retriever, top_k, source_ids)

        if self.indexer is None:
            return []

        index = self.indexer.get_or_create_index()
        base_limit = max(top_k, top_k * self.config.search_multiplier)
        max_limit = self._max_retrieval_limit(base_limit)
        seen = set()
        candidates = []
        limit = base_limit

        while limit <= max_limit:
            retriever = VectorIndexRetriever(
                index=index,
                similarity_top_k=limit,
                vector_store_query_mode="hybrid",
                filters=self._metadata_filters(source_ids),
            )
            nodes = retriever.retrieve(query)
            for node in nodes:
                chunk_id = node.metadata.get("chunk_id") or node.metadata.get("doc_id")
                if not chunk_id or chunk_id in seen:
                    continue
                chunk = self.metadata_store.get_chunk(chunk_id)
                if not chunk:
                    continue
                if not self._managed_hit_matches_chunk(node.metadata, chunk):
                    continue
                if source_ids and chunk.source_id not in source_ids:
                    continue
                seen.add(chunk_id)
                candidates.append(
                    {
                        "chunk_id": chunk_id,
                        "score": float(node.score or 0.0),
                    }
                )
                if len(candidates) >= top_k:
                    return candidates
            if len(nodes) < limit:
                break
            next_limit = min(limit * 2, max_limit)
            if next_limit == limit:
                break
            limit = next_limit

        return candidates

    @staticmethod
    def _managed_hit_matches_chunk(metadata: dict[str, Any], chunk) -> bool:
        if metadata.get("contextwiki_managed") != "true":
            return False
        source_id = metadata.get("source_id")
        document_id = metadata.get("document_id")
        if source_id != chunk.source_id:
            return False
        if document_id != chunk.document_id:
            return False
        return True

    @staticmethod
    def _metadata_filters(source_ids: list[str] | None):
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

    @staticmethod
    def _keyword_candidates(
        query: str,
        documents: Iterable[DocumentModel],
        top_k: int,
        source_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        terms = [term.lower() for term in query.split() if term.strip()]
        candidates = []
        for document in documents:
            if source_ids and document.source_id not in source_ids:
                continue
            haystack = document.content.lower()
            matches = sum(1 for term in terms if term in haystack)
            if matches == 0:
                continue
            candidates.append(
                {
                    "chunk_id": document.chunk_id or document.id,
                    "score": matches / max(len(terms), 1),
                }
            )
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[:top_k]

    @staticmethod
    def _preview(text: str, length: int = 240) -> str:
        return text if len(text) <= length else text[:length].rstrip() + "..."

    def _max_retrieval_limit(self, base_limit: int) -> int:
        collection = getattr(self.indexer, "collection", None)
        if collection is not None and hasattr(collection, "count"):
            try:
                return max(base_limit, int(collection.count()))
            except Exception:
                pass
        return max(base_limit, base_limit * 64)
