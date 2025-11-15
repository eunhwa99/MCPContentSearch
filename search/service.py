import logging
from typing import List, Set

from llama_index.core.retrievers import VectorIndexRetriever

from environments.config import AppConfig
from core.exceptions import SearchError

logger = logging.getLogger(__name__)


class SearchService:
    """검색 서비스"""
    
    def __init__(self, config: AppConfig, indexer):
        self.config = config
        self.indexer = indexer
    
    async def search(self, query: str, n_results: int) -> str:
        """컨텐츠 검색"""
        try:
            index = self.indexer.get_or_create_index()
            
            retriever = VectorIndexRetriever(
                index=index,
                similarity_top_k=n_results * self.config.search_multiplier,
                vector_store_query_mode="hybrid",
            )
            
            nodes = retriever.retrieve(query)
            
            return self._format_results(query, nodes, n_results)
        
        except Exception as e:
            logger.error(f"Search error: {e}")
            raise SearchError(f"Search failed: {e}")
    
    def _format_results(self, query: str, nodes: List, limit: int) -> str:
        """검색 결과 포맷팅"""
        if not nodes:
            return f"No results found for '{query}'"
        
        seen_titles: Set[str] = set()
        results = []
        
        for node in nodes:
            title = node.metadata.get("title", "Untitled")
            
            if title in seen_titles:
                continue
            
            seen_titles.add(title)
            results.append({
                "title": title,
                "platform": node.metadata.get("platform", "Unknown"),
                "url": node.metadata.get("url", ""),
                "date": node.metadata.get("date", ""),
                "score": node.score,
                "text": node.text[:self.config.preview_length] + "..."
            })
            
            if len(results) >= limit:
                break
        
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

