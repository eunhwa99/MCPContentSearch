import logging
import re
from typing import Tuple
from dataclasses import dataclass

from search.service import SearchService
from fetching.web_searcher import WebSearcher
from indexing.indexer import ContentIndexer
from indexing.background_tasks import get_default_background_task_registry
from core.models import DocumentModel

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """검색 결과"""
    source: str  # "local" | "web"
    results: str
    new_docs_count: int = 0


class DynamicSearchService:
    """
    동적 검색 서비스
    
    1. 로컬 DB 검색
    2. 결과 부족 시 웹 검색
    3. 웹 결과 자동 인덱싱
    """
    
    def __init__(
        self,
        local_search: SearchService,
        web_searcher: WebSearcher,
        indexer: ContentIndexer,
        min_threshold: int = 3,
        background_task_registry=None,
    ):
        self.local_search = local_search
        self.web_searcher = web_searcher
        self.indexer = indexer
        self.min_threshold = min_threshold
        self.background_task_registry = (
            background_task_registry or get_default_background_task_registry()
        )
    
    async def search(
        self, 
        query: str, 
        n_results: int = 10
    ) -> SearchResult:
        """
        하이브리드 검색 (로컬 → 웹)
        
        Args:
            query: 검색어
            n_results: 원하는 결과 수
        
        Returns:
            SearchResult
        """
        # 1단계: 로컬 DB 검색
        logger.info(f"🔍 Searching local DB for: '{query}'")
        local_results, local_count = await self._search_local(query, n_results)
        
        # 충분한 결과가 있으면 반환
        if local_count >= self.min_threshold:
            logger.info(f"✓ Found {local_count} results in local DB")
            return SearchResult(
                source="local",
                results=local_results
            )
        
        # 2단계: 웹 검색
        logger.info(f"⚠ Insufficient results ({local_count}/{self.min_threshold}), searching web...")
        web_docs = await self.web_searcher.search(query, n_results)
        
        if not web_docs:
            logger.warning("✗ No results found on web")
            return SearchResult(
                source="local",
                results=local_results or f"No results found for '{query}'"
            )
        
        # 3단계: 웹 결과 포맷팅
        logger.info(f"✓ Found {len(web_docs)} results from web")
        web_results = self._format_web_results(query, web_docs)
        
        # 4단계: 백그라운드 인덱싱
        logger.info(f"📚 Scheduling {len(web_docs)} documents for background indexing")
        self.background_task_registry.schedule(
            "search_content_fallback",
            self._index_background(web_docs),
            total_docs=len(web_docs),
        )
        
        return SearchResult(
            source="web",
            results=web_results,
            new_docs_count=len(web_docs)
        )
    
    async def _search_local(
        self, 
        query: str, 
        n: int
    ) -> Tuple[str, int]:
        """로컬 DB 검색"""
        try:
            results = await self.local_search.search(query, n)
            count = self._extract_count(results)
            return results, count
        except Exception as e:
            logger.error(f"Local search error: {e}")
            return "", 0
    
    async def _index_background(self, documents: list) -> int:
        """백그라운드 인덱싱"""
        logger.info(f"⏳ Background indexing started")
        await self.indexer.index_documents(documents)
        logger.info(f"✅ Successfully indexed {len(documents)} documents")
        return len(documents)
    
    @staticmethod
    def _extract_count(markdown: str) -> int:
        """마크다운에서 결과 수 추출"""
        match = re.search(r"Total (\d+) documents found", markdown)
        return int(match.group(1)) if match else 0
    
    @staticmethod
    def _format_web_results(query: str, docs: list) -> str:
        """웹 검색 결과 포맷팅"""
        output = [
            f"# 🌐 Real-time Web Search: '{query}'",
            "",
            f"⚡ **Live results** - Found {len(docs)} documents",
            "📝 *These results are being added to your database...*",
            "",
            "---",
            ""
        ]
        
        for i, doc in enumerate(docs, 1):
            output.extend([
                f"## {i}. [{doc.title}]({doc.url})",
                f"**Platform**: {doc.platform} | **Date**: {doc.date}",
                f"**Preview**: {doc.content[:200]}...",
                ""
            ])
        
        return "\n".join(output)
