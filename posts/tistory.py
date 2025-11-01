import asyncio
import aiohttp
from bs4 import BeautifulSoup
import ssl
import certifi
import logging

logger = logging.getLogger(__name__)
ssl_context = ssl.create_default_context(cafile=certifi.where())

# 여러 스킨 대응용 본문 후보 선택자
CONTENT_SELECTORS = [
    "div.entry-content",
    "div.article",
    "div.post-content",
    "div.tt_article_useless_p_margin",
    "div.contents_style",
    "div#content",
]

async def fetch_post(session, blog_name, post_id):
    url = f"https://{blog_name}.tistory.com/{post_id}"
    try:
        async with session.get(url, ssl=ssl_context, timeout=10) as resp:
            if resp.status != 200:
                logger.debug(f"❌ {url} → status {resp.status}")
                return None
            
            html = await resp.text()
            soup = BeautifulSoup(html, "html.parser")
            
            # 제목
            h1 = soup.find("h1")
            title_tag = h1 or soup.find("meta", property="og:title")
            title = title_tag.get("content", "").strip() if title_tag and title_tag.name == "meta" else (title_tag.get_text(strip=True) if title_tag else f"Post {post_id}")
            
            # 작성일
            date_tag = soup.find("span", class_="date") or soup.find("time")
            date = date_tag.get_text(strip=True) if date_tag else ""
            
            # 본문 (다양한 스킨 지원)
            content = ""
            for selector in CONTENT_SELECTORS:
                tag = soup.select_one(selector)
                if tag:
                    # 광고/불필요한 영역 제거
                    for ad in tag.find_all(["div", "ins"], class_=["revenue_unit_wrap", "google-auto-placed"]):
                        ad.decompose()
                    content = tag.get_text(separator="\n", strip=True)
                    if content:
                        break
            
            if not content:
                logger.debug(f"⚠️ {url} → 본문 탐색 실패")
                return None
            
            return {
                "id": str(post_id),
                "title": title,
                "url": url,
                "date": date,
                "content": content,
                "platform": "Tistory"
            }

    except Exception as e:
        logger.warning(f"🚨 {url} → 요청 실패: {e}")
        return None


async def fetch_tistory_posts(blog_name="silver-programmer", max_id=300):
    posts = []
    connector = aiohttp.TCPConnector(limit=10)
    timeout_config = aiohttp.ClientTimeout(total=10)

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout_config
    ) as session:
        tasks = [fetch_post(session, blog_name, i) for i in range(1, max_id + 1)]
        found_count = 0
        skipped_count = 0

        for i, future in enumerate(asyncio.as_completed(tasks), 1):
            post = await future
            if post:
                posts.append(post)
                found_count += 1
                if found_count % 10 == 0:
                    logger.info(f"진행 중... {found_count}개 포스트 발견")
            else:
                skipped_count += 1

        logger.info(f"✅ 크롤링 완료: {found_count}개 발견, {skipped_count}개 스킵")

    return posts
