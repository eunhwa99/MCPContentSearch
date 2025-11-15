import logging
from mcp.server.fastmcp import FastMCP

from environments.config import AppConfig, setup_chroma
from environments.token import NOTION_API_KEY, TISTORY_BLOG_NAME
from llama_index.core import StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore

from indexing.indexer import ContentIndexer
from search.service import SearchService
from search.dynamic_search import DynamicSearchService
from fetching.web_searcher import WebSearcher
from api.tools import register_tools

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_app() -> FastMCP:
    """애플리케이션 초기화"""
    
    # 설정 로드
    config = AppConfig()
    
    # ChromaDB 설정
    chroma_collection = setup_chroma(config)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    
    # LlamaIndex 설정
    Settings.cache_dir = config.cache_dir
    
    # 기본 서비스
    indexer = ContentIndexer(config, chroma_collection, storage_context)
    search_service = SearchService(config, indexer)
    
    # 웹 검색기
    web_searcher = WebSearcher(
        notion_api_key=NOTION_API_KEY,
        tistory_blog_name=TISTORY_BLOG_NAME,
        config=config
    )
    
    # 동적 검색 서비스
    dynamic_search = DynamicSearchService(
        local_search=search_service,
        web_searcher=web_searcher,
        indexer=indexer,
        min_threshold=3  # 로컬에 3개 미만이면 웹 검색
    )
    
    # FastMCP 서버
    mcp = FastMCP("content-search-server")
    
    # 도구 등록
    register_tools(mcp, indexer, search_service, dynamic_search, web_searcher)
    
    logger.info("✅ Application initialized with dynamic search")
    
    return mcp


# ================================================================
# 🚀 실행
# ================================================================
if __name__ == "__main__":
    mcp = create_app()
    
    logger.info("🚀 Starting MCP server with auto-fallback search...")
    mcp.run()