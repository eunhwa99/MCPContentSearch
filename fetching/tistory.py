import asyncio
import ssl
import logging
from typing import Optional, Dict, List

import aiohttp
import certifi
from bs4 import BeautifulSoup

from core.models import DocumentModel

logger = logging.getLogger(__name__)

SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

CONTENT_SELECTORS = [
    "div.entry-content",
    "div.article",
    "div.post-content",
    "div.tt_article_useless_p_margin",
    "div.contents_style",
    "div#content",
]

AD_SELECTORS = ["div.revenue_unit_wrap", "ins.google-auto-placed"]


class TistoryPostExtractor:
    """Tistory 포스트 추출기"""
    
    def __init__(self, soup: BeautifulSoup):
        self.soup = soup
    
    def extract_title(self, post_id: int) -> str:
        h1 = self.soup.find("h1")
        if h1:
            return h1.get_text(strip=True)
        
        og_title = self.soup.find("meta", property="og:title")
        if og_title:
            return og_title.get("content", "").strip()
        
        return f"Post {post_id}"
    
    def extract_date(self) -> str:
        date_tag = self.soup.find("span", class_="date") or self.soup.find("time")
        return date_tag.get_text(strip=True) if date_tag else ""
    
    def extract_content(self) -> str:
        for selector in CONTENT_SELECTORS:
            tag = self.soup.select_one(selector)
            
            if not tag:
                continue
            
            self._remove_ads(tag)
            content = tag.get_text(separator="\n", strip=True)
            if content:
                return content
        
        return ""
    
    @staticmethod
    def _remove_ads(tag):
        for ad_selector in AD_SELECTORS:
            for ad in tag.select(ad_selector):
                ad.decompose()

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


async def fetch_post(
    session: aiohttp.ClientSession, 
    blog_name: str, 
    post_id: int,
    request_timeout: float
) -> Optional[Dict[str, str]]:
    """단일 Tistory 포스트 가져오기"""
    url = f"https://{blog_name}.tistory.com/{post_id}"
    
    try:
        async with session.get(url, ssl=SSL_CONTEXT, timeout=request_timeout) as resp:
            if resp.status != 200:
                return None
            
            html = await resp.text()
            soup = BeautifulSoup(html, "html.parser")
            
            extractor = TistoryPostExtractor(soup)
            
            title = extractor.extract_title(post_id)
            date = extractor.extract_date()
            content = extractor.extract_content()
            
            if not content:
                return None
            
            return {
                "id": f"tistory_{post_id}",
                "title": title,
                "url": url,
                "date": date,
                "content": content,
                "platform": "Tistory"
            }
    
    except Exception as e:
        logger.debug(f"{url} : Error - {e}")
        return None


async def fetch_tistory_posts(
    blog_name: str,
    max_id: int,
    connection_limit: int = 10,
    request_timeout: float = 10.0,
    log_interval: int = 10
) -> List[DocumentModel]:
    """Tistory 포스트 수집"""
    posts = []
    found_count = 0
    
    connector = aiohttp.TCPConnector(limit=connection_limit)
    timeout_config = aiohttp.ClientTimeout(total=request_timeout)
    
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout_config
    ) as session:
        tasks = [
            fetch_post(session, blog_name, post_id, request_timeout) 
            for post_id in range(1, max_id + 1)
        ]
        
        for future in asyncio.as_completed(tasks):
            post = await future
            
            if post:
                posts.append(DocumentModel(**post))
                found_count += 1
                
                if found_count % log_interval == 0:
                    logger.info(f"In progress: {found_count} posts found")
    
    logger.info(f"Complete: {found_count} Tistory posts found")
    return posts
