import asyncio
import logging
from typing import List, Optional
import httpx
import aiohttp
import ssl
import certifi
from bs4 import BeautifulSoup

from core.models import DocumentModel
from environments.config import AppConfig, NotionConfig
from fetching.notion import NotionAPIClient, NotionPageProcessor
from fetching.tistory import TistoryPostExtractor, fetch_post

logger = logging.getLogger(__name__)

class NotionSearcher:
    """Notion real-time search"""
    
    def __init__(self, api_key: str, config: AppConfig):
        self.api_key = api_key
        self.config = config
        self.notion_config = NotionConfig(api_key=api_key) if api_key else None
    
    async def search(self, query: str, max_results: int = 10) -> List[DocumentModel]:
        """Notion search"""
        if not self.api_key or not self.notion_config:
            logger.warning("Notion API key not set")
            return []
        
        try:
            client = NotionAPIClient(self.notion_config, self.config)
            processor = NotionPageProcessor(self.notion_config)
            
            documents = []
            
            async with httpx.AsyncClient(timeout=self.config.request_timeout) as http_client:
                # Notion search API with keyword query
                response = await http_client.post(
                    f"{self.notion_config.base_url}/search",
                    headers=client.headers,
                    json={
                        "query": query,  # keyword search
                        "filter": {"property": "object", "value": "page"},
                        "page_size": min(max_results, 100),
                        "sort": {"direction": "descending", "timestamp": "last_edited_time"}
                    }
                )
                
                response.raise_for_status()
                results = response.json().get("results", [])
                
                logger.info(f"Notion: {len(results)} pages found for '{query}'")
                
                for page in results[:max_results]:
                    content = await client.fetch_block_content(http_client, page["id"])
                    if content:
                        doc = processor.build_document(page, content)
                        documents.append(doc)
            
            return documents
            
        except Exception as e:
            logger.error(f"Notion search error: {e}")
            return []


class TistorySearcher:
    """Tistory real-time search"""
    
    def __init__(self, blog_name: str, config: AppConfig):
        self.blog_name = blog_name
        self.config = config
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())
    
    async def search(self, query: str, max_results: int = 10) -> List[DocumentModel]:
        """Tistory에서 검색"""
        if not self.blog_name:
            logger.warning("Tistory blog name not set")
            return []
        
        try:
            documents = []
            search_url = f"https://{self.blog_name}.tistory.com/search/{query}"
            
            async with aiohttp.ClientSession() as session:
                # 검색 결과 페이지 가져오기
                async with session.get(search_url, ssl=self.ssl_context) as response:
                    if response.status != 200:
                        return []
                    
                    html = await response.text()
                    soup = BeautifulSoup(html, "html.parser")
                    
                    # 포스트 링크 추출
                    post_links = self._extract_post_links(soup)
                    logger.info(f"Tistory: {len(post_links)} posts found for '{query}'")
                    
                    for link in post_links[:max_results]:
                        post_id = self._extract_post_id(link)
                        if not post_id:
                            continue
                        
                        post_data = await fetch_post(
                            session, 
                            self.blog_name, 
                            post_id,
                            self.config.request_timeout
                        )
                        
                        if post_data:
                            documents.append(DocumentModel(**post_data))
            
            return documents
            
        except Exception as e:
            logger.error(f"Tistory search error: {e}")
            return []
    
    @staticmethod
    def _extract_post_links(soup: BeautifulSoup) -> List[str]:
        """검색 결과에서 포스트 링크 추출"""
        links = []
        selectors = [
            "a.link_post",
            "a.article-title", 
            "div.post-item a",
            "div.search-result a"
        ]
        
        for selector in selectors:
            for a in soup.select(selector):
                href = a.get("href")
                if href and "/search/" not in href:
                    links.append(href)
        
        return list(set(links))
    
    @staticmethod
    def _extract_post_id(url: str) -> Optional[int]:
        """URL에서 포스트 ID 추출"""
        import re
        match = re.search(r"/(\d+)$", url)
        return int(match.group(1)) if match else None


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

