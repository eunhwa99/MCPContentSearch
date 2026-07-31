import asyncio
import inspect
import logging
import ssl
from datetime import datetime
from typing import Dict, List, Optional
import aiohttp
import certifi
from bs4 import BeautifulSoup

from core.models import DocumentModel
from fetching.notion import _StopRequested

logger = logging.getLogger(__name__)


async def _emit_progress(progress_callback, event: dict, *, stop_signal=None) -> bool:
    if progress_callback is None:
        return False
    try:
        result = progress_callback(event)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        # Match by name to avoid importing indexing.ingestion_service (cycle).
        if type(exc).__name__ == "_InactiveJobStop":
            raise
        logger.debug("Ignoring progress callback failure")
        return False
    return stop_signal is not None and result is stop_signal

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

    def extract_published_at(self, display_date: str) -> str:
        time_tag = self.soup.find("time")
        candidates = [
            time_tag.get("datetime", "") if time_tag else "",
            display_date,
        ]
        for candidate in candidates:
            if not isinstance(candidate, str) or not candidate:
                continue
            try:
                datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            except ValueError:
                continue
            return candidate
        return ""
    
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
            published_at = extractor.extract_published_at(date)
            content = extractor.extract_content()
            
            if not content:
                return None
            
            return {
                "id": f"tistory_{post_id}",
                "document_id": f"{blog_name}:{post_id}",
                "external_id": f"{blog_name}:{post_id}",
                "title": title,
                "url": url,
                "canonical_url": url,
                "date": date,
                "published_at": published_at,
                "date_provenance": "tistory" if published_at else "",
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
    log_interval: int = 10,
    progress_callback=None,
    progress_stop_signal=None,
) -> List[DocumentModel]:
    """Tistory 포스트 수집"""
    posts = []
    found_count = 0
    total_pages = max(int(max_id or 0), 0)
    if await _emit_progress(
        progress_callback,
        {"event": "search_completed", "total_pages": total_pages},
        stop_signal=progress_stop_signal,
    ):
        raise _StopRequested
    
    connector = aiohttp.TCPConnector(limit=connection_limit)
    timeout_config = aiohttp.ClientTimeout(total=request_timeout)
    
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout_config
    ) as session:
        tasks = [
            asyncio.create_task(
                fetch_post(session, blog_name, post_id, request_timeout)
            )
            for post_id in range(1, max_id + 1)
        ]
        
        completed = 0
        try:
            for future in asyncio.as_completed(tasks):
                post = await future
                completed += 1
                if await _emit_progress(
                    progress_callback,
                    {
                        "event": "page_fetch_completed",
                        "current_page": completed,
                        "total_pages": total_pages,
                    },
                    stop_signal=progress_stop_signal,
                ):
                    raise _StopRequested

                if post:
                    posts.append(DocumentModel(**post))
                    found_count += 1

                    if found_count % log_interval == 0:
                        logger.info(f"In progress: {found_count} posts found")
        finally:
            # Cancel pending fan-out before ClientSession exits on cooperative
            # stop, CancelledError (worker SIGTERM), or any mid-loop failure.
            # Shield the drain so a true Task.cancel cannot abort gather mid-await
            # and leave fan-out hitting a closing ClientSession. If cancel is
            # delivered to the waiter, still await the shielded gather to finish.
            pending = [task for task in tasks if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                drain = asyncio.gather(*pending, return_exceptions=True)
                try:
                    await asyncio.shield(drain)
                except asyncio.CancelledError:
                    await drain
                    raise
    
    logger.info(f"Complete: {found_count} Tistory posts found")
    return posts
