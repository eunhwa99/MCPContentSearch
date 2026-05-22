import logging
from typing import List, Set
from urllib.parse import urlparse

from llama_index.core.retrievers import VectorIndexRetriever

from environments.config import AppConfig
from core.exceptions import SearchError

logger = logging.getLogger(__name__)


class SearchService:
    """검색 서비스"""
    
    def __init__(self, config: AppConfig, indexer, metadata_store=None):
        self.config = config
        self.indexer = indexer
        self.metadata_store = metadata_store
    
    async def search(self, query: str, n_results: int) -> str:
        """컨텐츠 검색"""
        try:
            index = self.indexer.get_or_create_index()
            base_limit = max(n_results, n_results * self.config.search_multiplier)
            max_limit = self._max_retrieval_limit(base_limit)
            limit = base_limit
            results = []

            while limit <= max_limit:
                retriever = VectorIndexRetriever(
                    index=index,
                    similarity_top_k=limit,
                    vector_store_query_mode="hybrid",
                )
                nodes = retriever.retrieve(query)
                results = self._collect_results(nodes, n_results)
                if len(results) >= n_results:
                    break
                if len(nodes) < limit:
                    break
                next_limit = min(limit * 2, max_limit)
                if next_limit == limit:
                    break
                limit = next_limit

            return self._format_result_items(query, results)
        
        except Exception as e:
            logger.error(f"Search error: {e}")
            raise SearchError(f"Search failed: {e}")
    
    def _format_results(self, query: str, nodes: List, limit: int) -> str:
        """검색 결과 포맷팅"""
        if not nodes:
            return f"No results found for '{query}'"

        return self._format_result_items(query, self._collect_results(nodes, limit))

    def _collect_results(self, nodes: List, limit: int) -> list[dict]:
        seen_titles: Set[str] = set()
        results = []
        
        for node in nodes:
            result = self._result_from_node(node)
            if result is None:
                continue
            title = result["title"]
            
            if title in seen_titles:
                continue
            
            seen_titles.add(title)
            results.append(result)
            
            if len(results) >= limit:
                break

        return results

    def _format_result_items(self, query: str, results: list[dict]) -> str:
        if not results:
            return f"No results found for '{query}'"
        
        output = [
            f"# 🔍 Search results: '{query}'",
            "",
            f"Total {len(results)} documents found",
            ""
        ]
        
        for i, result in enumerate(results, 1):
            output.extend([
                f"## {i}. [{result['title']}]({result['url']})",
                f"**Platform**: {result['platform']} | **Date**: {result['date']}",
                f"**Relevance**: {result['score']:.3f}",
                f"**Preview**: {result['text']}",
                ""
            ])
        
        return "\n".join(output)

    def _result_from_node(self, node) -> dict | None:
        if node.metadata.get("contextwiki_managed") == "true":
            if self.metadata_store is None:
                return None
            chunk_id = node.metadata.get("chunk_id") or node.metadata.get("doc_id")
            if not chunk_id:
                return None
            chunk = self.metadata_store.get_chunk(chunk_id)
            if not chunk:
                return None
            if not self._managed_hit_matches_chunk(node.metadata, chunk):
                return None
            return {
                "title": chunk.title,
                "platform": chunk.source_id or "Unknown",
                "url": chunk.url,
                "date": chunk.updated_at,
                "score": node.score,
                "text": chunk.text[:self.config.preview_length] + "...",
            }

        if self.metadata_store is not None and self._known_contextwiki_hit(node.metadata):
            return None

        return {
            "title": node.metadata.get("title", "Untitled"),
            "platform": node.metadata.get("platform", "Unknown"),
            "url": node.metadata.get("url", ""),
            "date": node.metadata.get("date", ""),
            "score": node.score,
            "text": node.text[:self.config.preview_length] + "...",
        }

    def _known_contextwiki_hit(self, metadata: dict) -> bool:
        for chunk_id in self._metadata_id_candidates(metadata, "chunk_id", "doc_id", "document_id"):
            if self._metadata_store_has_chunk_record(chunk_id):
                return True
        for document_id in self._document_id_candidates(metadata):
            if document_id and self.metadata_store.get_document(document_id):
                return True
        for url in self._metadata_url_candidates(metadata):
            if self._metadata_store_has_document_url(url):
                return True
        return False

    def _metadata_store_has_document_url(self, url: str | None) -> bool:
        if not url:
            return False
        get_document_by_url = getattr(self.metadata_store, "get_document_by_url", None)
        if callable(get_document_by_url):
            return bool(get_document_by_url(url))
        return False

    def _metadata_store_has_chunk_record(self, chunk_id: str) -> bool:
        has_chunk_record = getattr(self.metadata_store, "has_chunk_record", None)
        if callable(has_chunk_record):
            return bool(has_chunk_record(chunk_id))
        return bool(self.metadata_store.get_chunk(chunk_id))

    @staticmethod
    def _metadata_id_candidates(metadata: dict, *keys: str) -> list[str]:
        seen = set()
        candidates = []
        for key in keys:
            value = metadata.get(key)
            if value and value not in seen:
                seen.add(value)
                candidates.append(value)
        return candidates

    @classmethod
    def _document_id_candidates(cls, metadata: dict) -> list[str]:
        candidates = cls._metadata_id_candidates(metadata, "document_id", "doc_id", "chunk_id")
        for value in list(candidates):
            for url in cls._metadata_url_candidates(metadata):
                alias = cls._legacy_document_alias(value, url)
                if alias and alias not in candidates:
                    candidates.append(alias)
        return candidates

    @staticmethod
    def _metadata_url_candidates(metadata: dict) -> list[str]:
        seen = set()
        urls = []
        for key in ("canonical_url", "url"):
            value = metadata.get(key)
            if value and value not in seen:
                seen.add(value)
                urls.append(value)
        return urls

    @staticmethod
    def _legacy_document_alias(document_id: str, url: str | None) -> str:
        if document_id.startswith("notion_"):
            return document_id.removeprefix("notion_")
        if document_id.startswith("tistory_") and url:
            post_id = document_id.removeprefix("tistory_")
            host = urlparse(url).netloc
            suffix = ".tistory.com"
            if host.endswith(suffix):
                return f"{host.removesuffix(suffix)}:{post_id}"
        return ""

    @staticmethod
    def _managed_hit_matches_chunk(metadata: dict, chunk) -> bool:
        if metadata.get("contextwiki_managed") != "true":
            return False
        source_id = metadata.get("source_id")
        document_id = metadata.get("document_id")
        if source_id != chunk.source_id:
            return False
        if document_id != chunk.document_id:
            return False
        return True

    def _max_retrieval_limit(self, base_limit: int) -> int:
        collection = getattr(self.indexer, "collection", None)
        if collection is not None and hasattr(collection, "count"):
            try:
                return max(base_limit, int(collection.count()))
            except Exception:
                logger.debug("Failed to read Chroma collection count", exc_info=True)
        return max(base_limit, base_limit * 64)
