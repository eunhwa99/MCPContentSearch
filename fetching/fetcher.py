import asyncio
import logging
from typing import List

from environments.config import AppConfig
from core.models import DocumentModel
from fetching.notion import fetch_notion_pages
from fetching.tistory import fetch_tistory_posts

logger = logging.getLogger(__name__)

class DocumentFetcher:
    """Document fetcher for various sources"""
    
    def __init__(self, config: AppConfig, notion_api_key: str, tistory_blog_name: str):
        self.config = config
        self.notion_api_key = notion_api_key
        self.tistory_blog_name = tistory_blog_name
    
    async def fetch_all(self) -> List[DocumentModel]:
        logger.info("Starting document collection...")
        
        results = await asyncio.gather(
            self._fetch_notion(),
            self._fetch_tistory(),
            return_exceptions=True
        )
        
        documents = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Fetch error: {result}")
                continue
            if result:
                documents.extend(result)
        
        logger.info(f"Collection complete: {len(documents)} documents")
        return documents
    
    async def _fetch_notion(self) -> List[DocumentModel]:
        try:
            return await fetch_notion_pages(self.notion_api_key, self.config)
        except Exception as e:
            logger.error(f"Notion fetch failed: {e}")
            return []
    
    async def _fetch_tistory(self) -> List[DocumentModel]:
        try:
            return await fetch_tistory_posts(
                self.tistory_blog_name,
                self.config.tistory_max_post_id,
                self.config.connection_limit,
                self.config.request_timeout,
                self.config.tistory_log_interval
            )
        except Exception as e:
            logger.error(f"Tistory fetch failed: {e}")
            return []
