import logging
from datetime import datetime, timezone
from mcp.server.fastmcp import FastMCP

from app_runtime import build_ingestion_runtime
from environments.config import AppConfig, setup_chroma
from environments.runtime_env import get_env_secret
from environments.token import NOTION_API_KEY, TISTORY_BLOG_NAME
from llama_index.core import StorageContext
from llama_index.vector_stores.chroma import ChromaVectorStore

from indexing.indexer import ContentIndexer
from fetching.connectors import build_source_registry
from indexing.chunker import DocumentChunker
from indexing.ingestion_service import IngestionService
from search.answer_service import CitationAnswerService
from search.context_service import ContextSearchService
from search.evidence_service import EvidenceSearchService
from storage.metadata_store import MetadataStore, ORPHANED_SYNC_JOB_RECOVERY_MESSAGE
from api.tools import register_tools

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_app() -> FastMCP:
    """애플리케이션 초기화"""

    process_started_at = datetime.now(timezone.utc).isoformat()

    # 설정 로드
    config = AppConfig()

    runtime = build_ingestion_runtime(
        config=config,
        notion_api_key=NOTION_API_KEY,
        tistory_blog_name=TISTORY_BLOG_NAME,
        github_token=get_env_secret(config.github_token_env_var),
        setup_chroma_fn=setup_chroma,
        vector_store_cls=ChromaVectorStore,
        storage_context_cls=StorageContext,
        indexer_cls=ContentIndexer,
        metadata_store_cls=MetadataStore,
        source_registry_builder=build_source_registry,
        chunker_cls=DocumentChunker,
        ingestion_service_cls=IngestionService,
    )
    indexer = runtime.indexer
    metadata_store = runtime.metadata_store
    source_registry = runtime.source_registry
    ingestion_service = runtime.ingestion_service
    retained_source_ids = list(runtime.retained_source_ids)
    recovered_count = metadata_store.recover_orphaned_running_jobs(
        started_before=process_started_at,
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
        source_ids=retained_source_ids,
    )
    if recovered_count:
        logger.info("Recovered %s orphaned running sync job(s)", recovered_count)
    context_search = ContextSearchService(
        metadata_store=metadata_store,
        indexer=indexer,
        config=config,
        default_source_ids=retained_source_ids,
    )
    answer_service = CitationAnswerService(context_search)
    evidence_search_service = EvidenceSearchService(
        context_search_service=context_search,
        metadata_store=metadata_store,
    )

    # FastMCP 서버
    mcp = FastMCP("content-search-server")

    # 도구 등록
    register_tools(
        mcp,
        ingestion_service=ingestion_service,
        context_search_service=context_search,
        evidence_search_service=evidence_search_service,
        answer_service=answer_service,
        metadata_store=metadata_store,
        source_registry=source_registry,
    )

    logger.info("✅ Application initialized with slim ContextWiki MCP tools")

    return mcp


# ================================================================
# 🚀 실행
# ================================================================
if __name__ == "__main__":
    mcp = create_app()

    logger.info("🚀 Starting slim ContextWiki MCP server...")
    mcp.run()
