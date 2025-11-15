"""
Notion 수집 기능 테스트
실행: python -m tests.test_notion
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from posts.notion import fetch_notion_pages
import logging

logging.basicConfig(level=logging.INFO)


async def test_fetch_pages():
    """Notion 페이지 수집 테스트"""
    print("\n" + "="*60)
    print("Notion 페이지 수집 테스트 시작")
    print("="*60 + "\n")
    
    pages = await fetch_notion_pages()
    
    print("\n" + "="*60)
    print(f"✅ 총 {len(pages)}개 페이지 수집 완료")
    print("="*60 + "\n")
    
    if pages:
        print("📄 첫 번째 페이지 샘플:")
        page = pages[0]
        print(f"  제목: {page['title']}")
        print(f"  URL: {page['url']}")
        print(f"  날짜: {page['date']}")
        print(f"  내용 길이: {len(page['content'])}자")
        print(f"  내용 미리보기:\n  {page['content'][:200]}...\n")
        
        print("\n📚 수집된 페이지 목록:")
        for i, page in enumerate(pages, 1):
            print(f"  {i}. {page['title']}")
    else:
        print("⚠️ 수집된 페이지가 없습니다.")


if __name__ == "__main__":
    asyncio.run(test_fetch_pages())