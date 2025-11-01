import asyncio
import ssl
import logging
from typing import Optional, Dict, List

import aiohttp
import certifi
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 상수 정의
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
REQUEST_TIMEOUT = 10
CONNECTION_LIMIT = 10
LOG_INTERVAL = 10

# 다양한 Tistory 스킨 대응을 위한 본문 선택자
CONTENT_SELECTORS = [
    "div.entry-content",
    "div.article",
    "div.post-content",
    "div.tt_article_useless_p_margin",
    "div.contents_style",
    "div#content",
]

# 제거할 광고/불필요한 요소
AD_SELECTORS = ["div.revenue_unit_wrap", "ins.google-auto-placed"]

class TistoryPostExtractor:
    """Extract title, date, and content from Tistory post HTML"""
    
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
            
            # 광고 및 불필요한 요소 제거
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
    post_id: int
) -> Optional[Dict[str, str]]:
    """
    Fetch a single Tistory post by ID asynchronously
    
    Args:
        session: aiohttp session
        blog_name: Tistory blog name
        post_id: post ID
    
    Returns:
        post data dictionary or None if not found/error"""
    url = f"https://{blog_name}.tistory.com/{post_id}"
    
    try:
        async with session.get(url, ssl=SSL_CONTEXT, timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                logger.info(f"{url} : HTTP {resp.status}")
                return None
            
            html = await resp.text()
            soup = BeautifulSoup(html, "html.parser")
            
            extractor = TistoryPostExtractor(soup)
            
            title = extractor.extract_title(post_id)
            date = extractor.extract_date()
            content = extractor.extract_content()
            
            if not content:
                logger.info(f"{url} : Cannot find content")
                return None
            
            return {
                "id": str(post_id),
                "title": title,
                "url": url,
                "date": date,
                "content": content,
                "platform": "Tistory"
            }
    
    except asyncio.TimeoutError:
        logger.warning(f"⏱{url} : Timeout")
        return None
    
    except Exception as e:
        logger.warning(f"{url} : Error - {e}")
        return None


async def fetch_tistory_posts(
    blog_name: str = "silver-programmer", 
    max_id: int = 200
) -> List[Dict[str, str]]:
    """
    Fetch Tistory posts up to max_id asynchronously    
    Args:
        blog_name: Tistory blog name
        max_id: maximum post ID to fetch
    
    Returns:
        List of Tistory post data dictionaries
    """
    posts = []
    found_count = 0
    skipped_count = 0
    
    connector = aiohttp.TCPConnector(limit=CONNECTION_LIMIT)
    timeout_config = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout_config
    ) as session:
        
        # 모든 포스트 ID에 대해 비동기 작업 생성
        tasks = [
            fetch_post(session, blog_name, post_id) 
            for post_id in range(1, max_id + 1)
        ]
        
        # 완료되는 순서대로 결과 처리
        for future in asyncio.as_completed(tasks):
            post = await future
            
            if post:
                posts.append(post)
                found_count += 1
                
                # 진행 상황 로깅
                if found_count % LOG_INTERVAL == 0:
                    logger.info(f"In progress: {found_count} posts found")
            else:
                skipped_count += 1
    
    logger.info(f"Complete job: {found_count} found, {skipped_count} skipped")
    
    return posts