import logging
from typing import List, Dict, Optional
import httpx
from environments.token import NOTION_API_KEY

logger = logging.getLogger(__name__)

# Constants
NOTION_API_VERSION = "2025-09-03"
NOTION_BASE_URL = "https://api.notion.com/v1"
REQUEST_TIMEOUT = 10.0
PAGE_SIZE = 100

# Notion block types
SUPPORTED_BLOCK_TYPES = {
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "bulleted_list_item",
    "numbered_list_item",
}

# Title property names to check
TITLE_PROPERTY_NAMES = ["title", "Title", "Name", "이름"]


class NotionAPIClient:
    """Client for interacting with the Notion API"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json",
        }
    
    async def search_pages(self, client: httpx.AsyncClient) -> List[Dict]:
        """
        Find all pages in Notion workspace
        
        Args:
            client: httpx async client
        
        Returns:
            List of page objects
        """
        pages = []
        has_more = True
        next_cursor = None
        
        while has_more:
            payload = self._build_search_payload(next_cursor)
            
            try:
                response = await client.post(
                    f"{NOTION_BASE_URL}/search",
                    headers=self.headers,
                    json=payload
                )
                response.raise_for_status()
                data = response.json()
                
                pages.extend(data.get("results", []))
                has_more = data.get("has_more", False)
                next_cursor = data.get("next_cursor")
                
            except httpx.HTTPStatusError as e:
                logger.error(f"Notion API (HTTP {e.response.status_code}): {e}")
                break
            except Exception as e:
                logger.error(f"Notion error : {e}")
                break
        
        return pages
    
    async def fetch_block_content(
        self, 
        client: httpx.AsyncClient, 
        block_id: str
    ) -> str:
        """
        Fetch the text content of a Notion block by its ID
        
        Args:
            client: httpx async client
            block_id: Notion block ID
        
        Returns:
            Extracted text content
        """
        try:
            response = await client.get(
                f"{NOTION_BASE_URL}/blocks/{block_id}/children",
                headers=self.headers,
                params={"page_size": PAGE_SIZE}
            )
            response.raise_for_status()
            data = response.json()
            
            return self._extract_text_from_blocks(data.get("results", []))
        
        except Exception as e:
            logger.debug(f"Block {block_id} failed to fetch content: {e}")
            return ""
    
    @staticmethod
    def _build_search_payload(cursor: Optional[str] = None) -> Dict:
        payload = {
            "filter": {"property": "object", "value": "page"},
            "page_size": PAGE_SIZE
        }
        if cursor:
            payload["start_cursor"] = cursor
        return payload
    
    @staticmethod
    def _extract_text_from_blocks(blocks: List[Dict]) -> str:
        content_parts = []
        
        for block in blocks:
            block_type = block.get("type")
            
            if block_type not in SUPPORTED_BLOCK_TYPES:
                continue
            
            text_array = block.get(block_type, {}).get("rich_text", [])
            
            for text_obj in text_array:
                plain_text = text_obj.get("plain_text", "")
                if plain_text:
                    content_parts.append(plain_text)
        
        return " ".join(content_parts).strip()


class NotionPageExtractor:
    """Extract metadata and content from Notion pages"""
    
    @staticmethod
    def extract_title(properties: Dict) -> str:
        """
        Extract the title from Notion page properties
        
        Args:
            properties: Notion page properties dictionary
        
        Returns:
            page title
        """
        for prop_name in TITLE_PROPERTY_NAMES:
            if prop_name not in properties:
                continue
            
            title_data = properties[prop_name].get("title", [])
            if title_data:
                return title_data[0].get("plain_text", "Untitled")
        
        return "Untitled"
    
    @staticmethod
    def build_page_data(
        page: Dict, 
        content: str
    ) -> Dict[str, str]:
        """
        Create page data dictionary from Notion page and content
        
        Args:
            page: Notion API page object
            content: page content text
        
        Returns:
            normalized page data dictionary
        """
        page_id = page["id"]
        properties = page.get("properties", {})
        
        return {
            "id": f"notion_{page_id}",
            "platform": "Notion",
            "title": NotionPageExtractor.extract_title(properties),
            "content": content,
            "url": page.get("url", ""),
            "date": page.get("created_time", "")
        }


async def fetch_notion_pages() -> List[Dict]:
    """
    Fetch all Notion pages using the Notion API
    
    Returns:
        List of Notion page data dictionaries
    """
    if not NOTION_API_KEY:
        logger.warning("NOTION_API_KEY not set. Skipping Notion crawling.")
        return []
    
    pages = []
    api_client = NotionAPIClient(NOTION_API_KEY)
    extractor = NotionPageExtractor()
    
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            # 모든 페이지 검색
            raw_pages = await api_client.search_pages(client)
            
            logger.info(f"{len(raw_pages)} Notion pages found. Starting to fetch content...")
            
            # 각 페이지의 본문 가져오기
            for idx, page in enumerate(raw_pages, 1):
                page_id = page["id"]
                
                # 블록 내용 가져오기
                content = await api_client.fetch_block_content(client, page_id)
                
                # 페이지 데이터 생성
                page_data = extractor.build_page_data(page, content)
                pages.append(page_data)
                
                # 진행 상황 로깅
                if idx % 10 == 0:
                    logger.info(f"In progress: {idx}/{len(raw_pages)} pages")
            
            logger.info(f"✅ Notion crawling complete: {len(pages)} pages")
    
    except Exception as e:
        logger.error(f"Notion crawling failed: {e}")
    
    return pages