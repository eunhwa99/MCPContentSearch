import asyncio
import logging
from typing import List, Optional

from core.models import DocumentModel
from environments.config import AppConfig
from fetching.notion import NotionSearcher
from fetching.tistory import TistorySearcher

logger = logging.getLogger(__name__)

class WebSearcher:
    """Integrated web searcher for Notion and Tistory"""
    
    def __init__(
        self, 
        notion_api_key: str, 
        tistory_blog_name: str, 
        config: AppConfig
    ):
        self.notion = NotionSearcher(notion_api_key, config) if notion_api_key else None
        self.tistory = TistorySearcher(tistory_blog_name, config) if tistory_blog_name else None
        self.config = config
    
    async def search(
        self, 
        query: str, 
        max_results: int = 10,
        platforms: List[str] = None
    ) -> List[DocumentModel]:
        """
        지정된 플랫폼에서 검색
        
        Args:
            query: 검색어
            max_results: 최대 결과 수
            platforms: 검색할 플랫폼 ["notion", "tistory"] or None (모두)
        """
        tasks = []
        
        # 플랫폼 선택
        if platforms is None:
            platforms = ["notion", "tistory"]
        
        per_platform = max(3, max_results // len(platforms))
        
        # Notion 검색
        if "notion" in platforms and self.notion:
            tasks.append(self.notion.search(query, per_platform))
        
        # Tistory 검색
        if "tistory" in platforms and self.tistory:
            tasks.append(self.tistory.search(query, per_platform))
        
        if not tasks:
            return []
        
        # 병렬 검색
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 결과 통합
        all_docs = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Search error: {result}")
                continue
            if result:
                all_docs.extend(result)
        
        return all_docs[:max_results]

