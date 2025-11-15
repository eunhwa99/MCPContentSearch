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
