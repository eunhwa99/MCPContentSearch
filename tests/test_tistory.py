import asyncio
import aiohttp
from bs4 import BeautifulSoup
import re
import ssl
import certifi

# SSL context 설정 (인증서 오류 방지)
ssl_context = ssl.create_default_context(cafile=certifi.where())

async def fetch_post(session, blog_name, post_id):
    url = f"https://{blog_name}.tistory.com/{post_id}"
    try:
        async with session.get(url, ssl=ssl_context, timeout=10) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()

            # BeautifulSoup으로 HTML 파싱
            soup = BeautifulSoup(html, "html.parser")

            # 1️⃣ 제목
            h1 = soup.find("h1")
            title = h1.get_text(strip=True) if h1 else f"Post {post_id}"

            # 2️⃣ 작성일
            date_tag = soup.find("span", class_="date")
            date = date_tag.get_text(strip=True) if date_tag else ""

            # 3️⃣ 본문
            content_tag = soup.find("div", class_="entry-content")
            if content_tag:
                # 광고 등 불필요한 div 제거
                for ad in content_tag.find_all("div", class_="revenue_unit_wrap"):
                    ad.decompose()
                content = content_tag.get_text(separator="\n", strip=True)
            else:
                content = ""

            if not content:  # 본문이 없는 글은 제외
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
        print(f"⚠️ 글 {post_id} 크롤링 실패: {e}")
        return None


async def fetch_all_tistory_posts(blog_name="silver-programmer", max_id=300):
    posts = []
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_post(session, blog_name, i) for i in range(1, max_id + 1)]
        for i, future in enumerate(asyncio.as_completed(tasks), 1):
            post = await future
            if post:
                print(f"✅ Found post {post['id']}: {post['title']}: {post['content'][:500]}...")
                posts.append(post)
            else:
                print(f"❌ Skipped {i}")
    return posts

# 예시 실행
asyncio.run(fetch_all_tistory_posts("silver-programmer", max_id=300))
