import logging
from typing import List, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from environments.config import AppConfig, NotionConfig
from core.models import DocumentModel
from core.exceptions import APIError, FetchError

logger = logging.getLogger(__name__)

class NotionAPIClient:
    def __init__(self, config: NotionConfig, app_config: AppConfig):
        self.config = config
        self.app_config = app_config
        self.headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Notion-Version": config.api_version,
            "Content-Type": "application/json",
        }
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPStatusError)
    )
    async def search_pages(self, client: httpx.AsyncClient) -> List[dict]:
        pages = []
        next_cursor = None
        
        while True:
            payload = self._build_search_payload(next_cursor)
            
            try:
                response = await client.post(
                    f"{self.config.base_url}/search",
                    headers=self.headers,
                    json=payload,
                    timeout=self.app_config.request_timeout
                )
                response.raise_for_status()
                data = response.json()
                
                pages.extend(data.get("results", []))
                
                if not data.get("has_more", False):
                    break
                    
                next_cursor = data.get("next_cursor")
                
            except httpx.HTTPStatusError as e:
                raise APIError("Notion", e.response.status_code, str(e))
        
        return pages
    
    async def fetch_block_content(
        self, 
        client: httpx.AsyncClient, 
        block_id: str,
        depth: int = 0
    ) -> str:
        """블록 컨텐츠 재귀 추출"""
        if depth > self.app_config.notion_max_depth:
            logger.warning(f"Max depth reached for block {block_id}")
            return ""
        
        try:
            blocks = await self._fetch_blocks(client, block_id)
            return await self._extract_text_recursive(client, blocks, depth)
        except Exception as e:
            logger.debug(f"Failed to fetch block {block_id}: {e}")
            return ""
    
    async def _fetch_blocks(self, client: httpx.AsyncClient, block_id: str) -> List[dict]:
        """페이지네이션 지원 블록 가져오기"""
        all_blocks = []
        next_cursor = None
        
        while True:
            params = {"page_size": self.app_config.notion_page_size}
            if next_cursor:
                params["start_cursor"] = next_cursor
            
            try:
                response = await client.get(
                    f"{self.config.base_url}/blocks/{block_id}/children",
                    headers=self.headers,
                    params=params,
                    timeout=self.app_config.request_timeout
                )
                response.raise_for_status()
                data = response.json()
                
                all_blocks.extend(data.get("results", []))
                
                if not data.get("has_more", False):
                    break
                    
                next_cursor = data.get("next_cursor")
                
            except httpx.HTTPStatusError as e:
                raise APIError("Notion", e.response.status_code, str(e))
        
        return all_blocks
    
    async def _extract_text_recursive(
        self,
        client: httpx.AsyncClient,
        blocks: List[dict],
        depth: int
    ) -> str:
        """재귀적 텍스트 추출"""
        content_parts = []
        
        for block in blocks:
            block_type = block.get("type")
            
            if block_type in self.config.supported_block_types:
                text_array = block.get(block_type, {}).get("rich_text", [])
                content_parts.extend(
                    obj.get("plain_text", "") 
                    for obj in text_array 
                    if obj.get("plain_text")
                )
            
            if block.get("has_children", False):
                child_content = await self.fetch_block_content(
                    client, block["id"], depth + 1
                )
                if child_content:
                    content_parts.append(child_content)
        
        return " ".join(content_parts).strip()
    
    @staticmethod
    def _build_search_payload(cursor: Optional[str] = None) -> dict:
        """검색 페이로드 생성"""
        payload = {
            "filter": {"property": "object", "value": "page"},
            "page_size": 100
        }
        if cursor:
            payload["start_cursor"] = cursor
        return payload


class NotionPageProcessor:
    """Notion 페이지 처리기"""
    
    def __init__(self, config: NotionConfig):
        self.config = config
    
    def extract_title(self, properties: dict) -> str:
        """제목 추출"""
        for prop_name in self.config.title_property_names:
            if prop_name not in properties:
                continue
            
            title_data = properties[prop_name].get("title", [])
            if title_data:
                return title_data[0].get("plain_text", "Untitled")
        
        return "Untitled"
    
    def build_document(self, page: dict, content: str) -> DocumentModel:
        """DocumentModel 생성"""
        return DocumentModel(
            id=f"notion_{page['id']}",
            platform="Notion",
            title=self.extract_title(page.get("properties", {})),
            content=content,
            url=page.get("url", ""),
            date=page.get("created_time", "")
        )


async def fetch_notion_pages(api_key: str, app_config: AppConfig) -> List[DocumentModel]:
    """Notion 페이지 가져오기"""
    if not api_key:
        logger.warning("NOTION_API_KEY not set. Skipping.")
        return []
    
    notion_config = NotionConfig(api_key=api_key)
    api_client = NotionAPIClient(notion_config, app_config)
    processor = NotionPageProcessor(notion_config)
    
    documents = []
    
    try:
        async with httpx.AsyncClient(timeout=app_config.request_timeout) as client:
            raw_pages = await api_client.search_pages(client)
            logger.info(f"Found {len(raw_pages)} Notion pages")
            
            for idx, page in enumerate(raw_pages, 1):
                content = await api_client.fetch_block_content(client, page["id"])
                document = processor.build_document(page, content)
                documents.append(document)
                
                if idx % 10 == 0:
                    logger.info(f"Progress: {idx}/{len(raw_pages)}")
            
            logger.info(f"✅ Complete: {len(documents)} pages")
    
    except APIError as e:
        logger.error(f"Notion API error: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise FetchError(f"Failed to fetch Notion pages: {e}")
    
    return documents