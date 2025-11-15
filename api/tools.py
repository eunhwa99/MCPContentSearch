import asyncio
import logging

from mcp.server.fastmcp import FastMCP

from environments.config import AppConfig
from environments.token import NOTION_API_KEY, TISTORY_BLOG_NAME
from fetching.fetcher import DocumentFetcher
from fetching.web_searcher import WebSearcher
from indexing.indexer import ContentIndexer
from search.service import SearchService
from search.dynamic_search import DynamicSearchService
from core.models import IndexState

logger = logging.getLogger(__name__)


def register_tools(
    mcp: FastMCP,
    indexer: ContentIndexer,
    search_service: SearchService,
    dynamic_search: DynamicSearchService,
    web_searcher: WebSearcher
):
    """MCP 도구 등록"""
    
    # ================================================================
    # 검색 도구
    # ================================================================
    
    @mcp.tool()
    async def search_content(query: str, n_results: int = 10) -> str:
        """
        콘텐츠 검색 (자동 폴백)
        
        1. 로컬 DB에서 검색
        2. 결과 부족 시 자동으로 웹에서 검색
        3. 웹 결과는 자동으로 DB에 추가
        
        Args:
            query: 검색어
            n_results: 원하는 결과 수
        
        Returns:
            검색 결과 (마크다운)
        """
        try:
            result = await dynamic_search.search(query, n_results)
            
            # 웹 검색 사용 시 알림 추가
            if result.source == "web":
                footer = (
                    f"\n\n---\n"
                    f"💡 **로컬 DB에 결과가 부족하여 웹에서 검색했습니다.**\n"
                    f"📚 {result.new_docs_count}개의 새 문서가 데이터베이스에 추가됩니다.\n"
                    f"⏱️ 다음 검색부터는 더 빠르게 찾을 수 있습니다!"
                )
                return result.results + footer
            
            return result.results
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            return f"검색 중 오류 발생: {str(e)}"
    
    
    @mcp.tool()
    async def search_notion(query: str, n_results: int = 10) -> str:
        """
        Notion에서만 실시간 검색
        
        Args:
            query: 검색어
            n_results: 결과 수
        
        Returns:
            검색 결과
        """
        try:
            logger.info(f"🔍 Searching Notion for: '{query}'")
            docs = await web_searcher.search(query, n_results, platforms=["notion"])
            
            if not docs:
                return f"Notion에서 '{query}'에 대한 검색 결과가 없습니다."
            
            # 포맷팅
            output = [
                f"# 📘 Notion Search: '{query}'",
                "",
                f"Found {len(docs)} documents",
                ""
            ]
            
            for i, doc in enumerate(docs, 1):
                output.extend([
                    f"## {i}. [{doc.title}]({doc.url})",
                    f"**Date**: {doc.date}",
                    f"**Preview**: {doc.content[:200]}...",
                    ""
                ])
            
            # 백그라운드 인덱싱
            asyncio.create_task(_index_background(indexer, docs))
            
            return "\n".join(output) + f"\n\n💡 {len(docs)}개 문서를 DB에 추가합니다."
            
        except Exception as e:
            logger.error(f"Notion search error: {e}")
            return f"Notion 검색 오류: {str(e)}"
    
    
    @mcp.tool()
    async def search_tistory(query: str, n_results: int = 10) -> str:
        """
        Tistory에서만 실시간 검색
        
        Args:
            query: 검색어
            n_results: 결과 수
        
        Returns:
            검색 결과
        """
        try:
            logger.info(f"🔍 Searching Tistory for: '{query}'")
            docs = await web_searcher.search(query, n_results, platforms=["tistory"])
            
            if not docs:
                return f"Tistory에서 '{query}'에 대한 검색 결과가 없습니다."
            
            # 포맷팅
            output = [
                f"# 📝 Tistory Search: '{query}'",
                "",
                f"Found {len(docs)} posts",
                ""
            ]
            
            for i, doc in enumerate(docs, 1):
                output.extend([
                    f"## {i}. [{doc.title}]({doc.url})",
                    f"**Date**: {doc.date}",
                    f"**Preview**: {doc.content[:200]}...",
                    ""
                ])
            
            # 백그라운드 인덱싱
            asyncio.create_task(_index_background(indexer, docs))
            
            return "\n".join(output) + f"\n\n💡 {len(docs)}개 문서를 DB에 추가합니다."
            
        except Exception as e:
            logger.error(f"Tistory search error: {e}")
            return f"Tistory 검색 오류: {str(e)}"
    
    
    # ================================================================
    # 인덱싱 도구
    # ================================================================
    
    @mcp.tool()
    async def trigger_index_all_content() -> str:
        """
        모든 콘텐츠 인덱싱 (백그라운드)
        
        Returns:
            시작 메시지
        """
        if indexer.status.state == IndexState.RUNNING:
            return "이미 인덱싱이 진행 중입니다."
        
        asyncio.create_task(_index_all_background(indexer))
        return "인덱싱을 백그라운드에서 시작했습니다. 'get_index_status'로 상태 확인하세요."
    
    
    @mcp.tool()
    async def get_index_status() -> dict:
        """
        인덱싱 상태 조회
        
        Returns:
            상태 정보
        """
        return indexer.status.model_dump()
# ================================================================
# 헬퍼 함수
# ================================================================

async def _index_all_background(indexer: ContentIndexer):
    """전체 인덱싱 백그라운드 작업"""
    try:
        config = AppConfig()
        fetcher = DocumentFetcher(config, NOTION_API_KEY, TISTORY_BLOG_NAME)
        
        documents = await fetcher.fetch_all()
        await indexer.index_documents(documents)
        
        logger.info("✅ Background indexing completed")
    except Exception as e:
        logger.error(f"❌ Background indexing failed: {e}")


async def _index_background(indexer: ContentIndexer, documents: list):
    """웹 검색 결과 백그라운드 인덱싱"""
    try:
        await indexer.index_documents(documents)
        logger.info(f"✅ Indexed {len(documents)} documents")
    except Exception as e:
        logger.error(f"❌ Indexing failed: {e}")

