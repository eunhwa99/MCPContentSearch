from environments.token import NOTION_API_KEY
import httpx
from typing import List, Dict

async def fetch_notion_pages() -> List[Dict]:
    """Notion에서 모든 페이지의 본문 가져오기"""
    if not NOTION_API_KEY:
        return []
    
    pages = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            has_more = True
            next_cursor = None

            while has_more:
                payload = {
                    "filter": {"property": "object", "value": "page"},
                    "page_size": 100
                }
                if next_cursor:
                    payload["start_cursor"] = next_cursor
                
                response = await client.post(
                    "https://api.notion.com/v1/search",
                    headers={
                        "Authorization": f"Bearer {NOTION_API_KEY}",
                        "Notion-Version": "2025-09-03",
                        "Content-Type": "application/json",
                    },
                    json=payload
                )
                response.raise_for_status()
                data = response.json()
                
                for page in data.get("results", []):
                    page_id = page["id"]
                    title = extract_notion_title(page.get("properties", {}))
                    content = await fetch_notion_blocks(client, page_id)
                    
                    pages.append({
                        "id": f"notion_{page_id}",
                        "platform": "Notion",
                        "title": title,
                        "content": content,
                        "url": page.get("url", ""),
                        "date": page.get("created_time", "")
                    })
                
                has_more = data.get("has_more", False)
                next_cursor = data.get("next_cursor")
    
    except Exception as e:
        print(f"Notion fetch error: {e}")
    
    return pages

async def fetch_notion_blocks(client: httpx.AsyncClient, block_id: str) -> str:
    """Notion 블록 내용 가져오기"""
    content = ""
    try:
        response = await client.get(
            f"https://api.notion.com/v1/blocks/{block_id}/children",
            headers={
                "Authorization": f"Bearer {NOTION_API_KEY}",
                "Notion-Version": "2025-09-03",
            },
            params={"page_size": 100}
        )
        response.raise_for_status()
        data = response.json()
        
        for block in data.get("results", []):
            block_type = block.get("type")
            if block_type in ["paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item", "numbered_list_item"]:
                text_array = block.get(block_type, {}).get("rich_text", [])
                for text_obj in text_array:
                    content += text_obj.get("plain_text", "") + " "
    except Exception:
        pass
    
    return content.strip()


def extract_notion_title(props: Dict) -> str:
    """Notion 제목 추출"""
    for prop_name in ["title", "Title", "Name", "이름"]:
        if prop_name in props:
            title_data = props[prop_name].get("title", [])
            if title_data:
                return title_data[0].get("plain_text", "Untitled")
    return "Untitled"

