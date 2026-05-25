from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from environments.config import AppConfig
from environments.token import NOTION_API_KEY, TISTORY_BLOG_NAME
from fetching.fetcher import DocumentFetcher
from core.models import IndexState

if TYPE_CHECKING:
    from fetching.web_searcher import WebSearcher
    from indexing.indexer import ContentIndexer
    from search.dynamic_search import DynamicSearchService
    from search.service import SearchService

logger = logging.getLogger(__name__)


def register_tools(
    mcp: FastMCP,
    indexer: ContentIndexer,
    search_service: SearchService,
    dynamic_search: DynamicSearchService,
    web_searcher: WebSearcher,
    ingestion_service=None,
    context_search_service=None,
    answer_service=None,
    wiki_service=None,
    metadata_store=None,
    source_registry=None,
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
    # ContextWiki MVP 도구
    # ================================================================

    @mcp.tool()
    async def list_sources() -> dict:
        """등록된 ContextWiki source 목록 조회"""
        if metadata_store is None:
            return {"sources": []}
        return {
            "sources": [
                source.model_dump(mode="json")
                for source in metadata_store.list_sources()
            ]
        }

    @mcp.tool()
    async def sync_source(source_id: str) -> dict:
        """특정 source incremental sync 실행"""
        if ingestion_service is None:
            return {"status": "error", "message": "ingestion service is not configured"}
        try:
            job = await ingestion_service.sync_source(source_id)
            return job.model_dump(mode="json")
        except Exception as e:
            logger.error(f"Sync source error: {e}")
            return {"status": "error", "message": str(e)}

    @mcp.tool()
    async def get_sync_status(source_id: str = "") -> dict:
        """source 및 sync job 상태 조회"""
        if metadata_store is None:
            return {"sources": []}

        if source_id:
            latest_job = metadata_store.get_latest_sync_job(source_id)
            source = metadata_store.get_source(source_id)
            return {
                "source": source.model_dump(mode="json") if source else None,
                "latest_job": latest_job.model_dump(mode="json") if latest_job else None,
            }

        statuses = []
        for source in metadata_store.list_sources():
            latest_job = metadata_store.get_latest_sync_job(source.source_id)
            source = metadata_store.get_source(source.source_id) or source
            statuses.append(
                {
                    "source": source.model_dump(mode="json"),
                    "latest_job": latest_job.model_dump(mode="json") if latest_job else None,
                }
            )
        return {"sources": statuses}

    @mcp.tool()
    async def search_context(query: str, filters: dict = None, top_k: int = 10) -> dict:
        """Citation 가능한 structured context 검색"""
        if context_search_service is None:
            return {"query": query, "results": []}
        result = await context_search_service.search_context(query, filters=filters, top_k=top_k)
        return {
            "query": result["query"],
            "results": [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in result["results"]
            ],
        }

    @mcp.tool()
    async def fetch_context(document_id: str = "", chunk_id: str = "") -> dict:
        """문서 또는 chunk context 원문 조회"""
        if metadata_store is None:
            return {"status": "error", "message": "metadata store is not configured"}
        if not document_id and not chunk_id:
            return {"status": "error", "message": "document_id or chunk_id is required"}

        if chunk_id:
            chunk = metadata_store.get_chunk(chunk_id)
            return {
                "chunk": chunk.model_dump(mode="json") if chunk else None,
            }

        document = metadata_store.get_document(document_id)
        if document and getattr(document, "deleted_at", ""):
            return {
                "document": None,
                "chunks": [],
            }
        chunks = metadata_store.list_chunks_for_document(document_id)
        return {
            "document": document.model_dump(mode="json") if document else None,
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
        }

    @mcp.tool()
    async def answer_with_citations(question: str, filters: dict = None, top_k: int = 5) -> dict:
        """검색된 chunk 근거만 사용해 citation 포함 답변 생성"""
        if answer_service is None:
            return {
                "question": question,
                "answer": "Citation answer service is not configured.",
                "evidence_status": "insufficient",
                "citations": [],
                "used_chunks": [],
            }
        return await answer_service.answer_with_citations(question, filters=filters, top_k=top_k)

    @mcp.tool()
    async def generate_wiki_page(topic: str, filters: dict = None, top_k: int = 8) -> dict:
        """검색된 ContextWiki 근거로 citation-backed wiki page 생성"""
        if wiki_service is None:
            return {
                "topic": topic,
                "status": "not_configured",
                "title": f"{topic} Wiki" if topic else "",
                "markdown": "Wiki generation service is not configured.",
                "sections": [],
                "citations": [],
                "backlinks": [],
                "used_chunks": [],
                "message": "Wiki generation service is not configured.",
            }
        try:
            return await wiki_service.generate_wiki_page(topic, filters=filters, top_k=top_k)
        except Exception:
            logger.exception("Generate wiki page error")
            return {
                "topic": topic,
                "status": "error",
                "title": f"{topic} Wiki" if topic else "",
                "markdown": "Wiki page generation failed.",
                "sections": [],
                "citations": [],
                "backlinks": [],
                "used_chunks": [],
                "message": "Wiki page generation failed.",
                "error_code": "wiki_generation_failed",
            }
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
