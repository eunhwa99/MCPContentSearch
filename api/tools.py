import asyncio
import logging

from mcp.server.fastmcp import FastMCP

from environments.config import AppConfig
from environments.token import NOTION_API_KEY, TISTORY_BLOG_NAME
from fetching.fetcher import DocumentFetcher
from indexing.indexer import ContentIndexer
from search.service import SearchService
from core.models import IndexState

logger = logging.getLogger(__name__)

def register_tools(
    mcp: FastMCP,
    indexer: ContentIndexer,
    search_service: SearchService
):
    """Register MCP tools for indexing and searching."""
    
    @mcp.tool()
    async def trigger_index_all_content() -> str:
        """
        인덱싱을 백그라운드에서 시작합니다.
        즉시 응답하며, 진행상황은 get_index_status()로 확인하세요.

        Returns:
            str: 인덱싱 시작 메시지
        """
        if indexer.status.state == IndexState.RUNNING:
            return "이미 인덱싱이 진행 중입니다. 잠시 후 다시 확인해주세요."
        
        asyncio.create_task(_index_all_content_background(indexer))
        return "인덱싱 작업을 백그라운드에서 시작했습니다. 'get_index_status'로 상태를 확인하세요."

    @mcp.tool()
    async def search_content(query: str, n_results: int = 10) -> str:
        """
        하이브리드 검색을 수행합니다.
        LlamaIndex의 고급 검색 기능을 사용하여 더 정확한 결과를 제공합니다.
        
        Args:
            query: 검색할 내용
            n_results: 반환할 결과 개수
        
        Returns:
            str: 마크다운 형식의 검색 결과
        """
        try:
            return await search_service.search(query, n_results)
        except Exception as e:
            logger.error(f"검색 오류: {e}")
            return f"검색 중 오류 발생: {str(e)}"

    @mcp.tool()
    async def get_index_status() -> dict:
        """
        현재 인덱싱 상태를 반환합니다.
        
        Returns:
            dict: 인덱싱 상태 정보
                - state: 현재 상태 (idle/running/done/error)
                - message: 상태 메시지
                - progress: 진행률 (0.0~1.0)
                - total_docs: 전체 문서 수
                - processed_docs: 처리된 문서 수
        """
        return indexer.status.model_dump()


async def _index_all_content_background(indexer: ContentIndexer):
    """Background task to index all content."""
    try:
        config = AppConfig()
        fetcher = DocumentFetcher(config, NOTION_API_KEY, TISTORY_BLOG_NAME)
        
        documents = await fetcher.fetch_all()
        await indexer.index_documents(documents)
        
        logger.info("✅ Background indexing completed")
    except Exception as e:
        logger.error(f"❌ Background indexing failed: {e}")