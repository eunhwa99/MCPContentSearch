import logging
from mcp.server.fastmcp import FastMCP

from environments.config import AppConfig, setup_chroma
from llama_index.core import StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore

from indexing.indexer import ContentIndexer
from search.service import SearchService
from api.tools import register_tools

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def create_app() -> FastMCP:
    """Initialize and return the MCP server, indexer, and search service."""
    
    # Load application configuration
    config = AppConfig()
    
    # ChromaDB 
    chroma_collection = setup_chroma(config)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    
    # LlamaIndex
    Settings.cache_dir = config.cache_dir
    
    # Indexer and Search Service
    indexer = ContentIndexer(config, chroma_collection, storage_context)
    search_service = SearchService(config, indexer)
    
    # FastMCP 
    mcp = FastMCP("content-search-server")
    
    # Register tools
    register_tools(mcp, indexer, search_service)
    
    logger.info("Application initialized successfully")
    
    return mcp


# ================================================================
# 🚀 Start Application
# ================================================================
if __name__ == "__main__":
    mcp = create_app()
    
    logger.info("🚀 Starting MCP server...")
    mcp.run()