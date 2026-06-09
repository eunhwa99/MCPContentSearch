from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from web_console.codex_cli import (
    CodexCliExecutionError,
    codex_sandbox_profile as _codex_sandbox_profile,
    run_codex_cli as _run_codex_cli,
    safe_codex_failure_message as _safe_codex_failure_message,
)
from web_console.payloads import (
    build_filters as _build_filters,
    codex_answer_payload as _codex_answer_payload,
    contextwiki_console_answer_payload as _contextwiki_console_answer_payload,
    list_sources as _list_sources,
    normalize_auto_sync_source_ids as _normalize_auto_sync_source_ids,
    normalize_target_source_type as _normalize_target_source_type,
    normalize_text as _normalize_text,
    normalize_top_k as _normalize_top_k,
    redact_prompt_text as _redact_prompt_text,
    remote_console_allowed as _remote_console_allowed,
    safe_answer_failure_payload as _safe_answer_failure_payload,
    safe_github_sync_payload as _safe_github_sync_payload,
    safe_public_config_error as _safe_public_config_error,
    safe_sync_job_payload as _safe_sync_job_payload,
    safe_target_sync_payload as _safe_target_sync_payload,
    source_id_for_target_type as _source_id_for_target_type,
    source_sync_status as _source_sync_status,
    is_local_host_header as _is_local_host_header,
    is_local_url as _is_local_url,
    is_loopback_client as _is_loopback_client,
)
from web_console.services.codex_answer import CodexCliAnswerService
from web_console.services.smoke_runner import ScriptSmokeRunner, run_smoke as _run_smoke
from web_console.services.target_sync import (
    GitHubTargetSyncService,
    NotionTargetSyncService,
    TargetSyncService,
    WebTargetSyncService,
    _NotionTargetConnector,
)
from storage.metadata_store import ORPHANED_SYNC_JOB_RECOVERY_MESSAGE

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = REPO_ROOT / "web"
logger = logging.getLogger(__name__)

__all__ = [
    "CodexCliExecutionError",
    "CodexCliAnswerService",
    "ConsoleDependencies",
    "ConsoleQuery",
    "GitHubSyncRequest",
    "GitHubTargetSyncService",
    "NotionTargetSyncService",
    "REPO_ROOT",
    "ScriptSmokeRunner",
    "SmokeRequest",
    "TargetSyncRequest",
    "TargetSyncService",
    "WEB_ROOT",
    "WebTargetSyncService",
    "create_console_app",
    "create_default_app",
    "_NotionTargetConnector",
    "_codex_sandbox_profile",
    "_configured_notion_api_key",
    "_recover_orphaned_sync_jobs_for_startup",
    "_redact_prompt_text",
    "_run_codex_cli",
    "_run_smoke",
    "_safe_codex_failure_message",
]


class ConsoleQuery(BaseModel):
    question: str = ""
    topic: str = ""
    filters: dict[str, Any] = Field(default_factory=dict)
    source_ids: list[str] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list)
    top_k: int | None = None


class SmokeRequest(BaseModel):
    topic: str = ""
    github_repository: str = ""
    require_generated: bool = False


class GitHubSyncRequest(BaseModel):
    target: str = ""


class TargetSyncRequest(BaseModel):
    source_type: str = ""
    target: str = ""


@dataclass
class ConsoleDependencies:
    answer_service: Any = None
    wiki_service: Any = None
    metadata_store: Any = None
    ingestion_service: Any = None
    target_sync_service: Any = None
    github_sync_service: Any = None
    codex_answer_service: Any = None
    smoke_runner: Any = None
    auto_sync_source_ids: tuple[str, ...] = ()


def _console_lifespan(dependencies: ConsoleDependencies):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = _schedule_startup_auto_sync_task(app, dependencies)
        try:
            yield
        finally:
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    return lifespan


def _schedule_startup_auto_sync_task(
    app: FastAPI,
    dependencies: ConsoleDependencies,
) -> asyncio.Task | None:
    source_ids = app.state.contextwiki_auto_sync_source_ids
    if not source_ids or dependencies.ingestion_service is None:
        return None
    task = asyncio.create_task(
        _run_startup_auto_sync_sources(
            dependencies.ingestion_service,
            source_ids,
        )
    )
    app.state.contextwiki_auto_sync_task = task
    return task


def _recover_orphaned_sync_jobs_for_startup(
    metadata_store: Any,
    *,
    process_started_at: str,
) -> int:
    recover = getattr(metadata_store, "recover_orphaned_running_jobs", None)
    if not callable(recover):
        return 0
    recovered_count = recover(
        started_before=process_started_at,
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
    )
    if recovered_count:
        logger.info("Recovered %s orphaned running sync job(s)", recovered_count)
    return recovered_count


def create_console_app(dependencies: ConsoleDependencies) -> FastAPI:
    app = FastAPI(
        title="ContextWiki Local Web Test Console",
        description="Local-only HTTP wrapper over ContextWiki services.",
        version="0.1.0",
        lifespan=_console_lifespan(dependencies),
    )
    app.state.contextwiki_auto_sync_source_ids = _normalize_auto_sync_source_ids(
        dependencies.auto_sync_source_ids
    )
    app.state.contextwiki_auto_sync_task = None

    @app.middleware("http")
    async def enforce_loopback_clients(request, call_next):
        allow_remote = _remote_console_allowed()
        if not allow_remote and not _is_loopback_client(request.client.host):
            return JSONResponse(
                status_code=403,
                content={"detail": "web console is local-only"},
            )
        if not _is_local_host_header(request.headers.get("host", "")):
            return JSONResponse(
                status_code=403,
                content={"detail": "web console host is not local"},
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin", "")
            referer = request.headers.get("referer", "")
            if origin and not _is_local_url(origin):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "web console origin is not local"},
                )
            if referer and not _is_local_url(referer):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "web console origin is not local"},
                )
        return await call_next(request)


    @app.get("/")
    async def index():
        index_path = WEB_ROOT / "index.html"
        if not index_path.exists():
            return {
                "service": "contextwiki-web-console",
                "local_only": True,
                "message": "Web console static files are not available.",
            }
        return FileResponse(index_path)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "contextwiki-web-console",
            "local_only": True,
        }

    @app.get("/api/sources")
    async def sources() -> dict[str, Any]:
        try:
            return {"sources": _list_sources(dependencies.metadata_store)}
        except Exception:
            _log_suppressed_error("Source listing failed")
            return {
                "sources": [],
                "status": "error",
                "message": "Source listing failed. See server logs for details.",
            }

    @app.get("/api/sources/{source_id}/sync-status")
    async def source_sync_status(source_id: str) -> dict[str, Any]:
        if dependencies.metadata_store is None:
            raise HTTPException(status_code=503, detail="metadata store is not configured")
        normalized_source_id = _normalize_text(source_id)
        try:
            return _source_sync_status(dependencies.metadata_store, normalized_source_id)
        except Exception:
            _log_suppressed_error("Source sync status failed")
            return {
                "source_id": normalized_source_id,
                "source": None,
                "latest_job": None,
                "status": "error",
                "message": "Source sync status failed. See server logs for details.",
            }

    @app.post("/api/sources/{source_id}/sync")
    async def sync_source(source_id: str) -> dict[str, Any]:
        if dependencies.ingestion_service is None:
            raise HTTPException(status_code=503, detail="ingestion service is not configured")
        normalized_source_id = _normalize_text(source_id)
        if not normalized_source_id:
            raise HTTPException(status_code=400, detail="source_id is required")
        try:
            job = await dependencies.ingestion_service.sync_source(normalized_source_id)
            return _safe_sync_job_payload(job)
        except HTTPException:
            raise
        except Exception:
            _log_suppressed_error("Source sync failed")
            return {
                "source_id": normalized_source_id,
                "status": "error",
                "message": "Source sync failed. See server logs for details.",
            }

    @app.post("/api/github/sync")
    async def sync_github_target(request: GitHubSyncRequest) -> dict[str, Any]:
        if dependencies.github_sync_service is None:
            raise HTTPException(status_code=503, detail="github sync service is not configured")
        target = _normalize_text(request.target)
        if not target:
            raise HTTPException(status_code=400, detail="target is required")
        try:
            return _safe_github_sync_payload(
                await dependencies.github_sync_service.sync_target(target)
            )
        except HTTPException:
            raise
        except Exception:
            _log_suppressed_error("GitHub target sync failed")
            return {
                "source_id": "source_github",
                "status": "error",
                "message": "GitHub target sync failed. See server logs for details.",
            }

    @app.post("/api/targets/sync")
    async def sync_target(request: TargetSyncRequest) -> dict[str, Any]:
        if dependencies.target_sync_service is None:
            raise HTTPException(status_code=503, detail="target sync service is not configured")
        source_type = _normalize_target_source_type(request.source_type)
        target = _normalize_text(request.target)
        if source_type not in {"github", "notion", "web"}:
            raise HTTPException(status_code=400, detail="source_type must be github, notion, or web")
        if not target:
            raise HTTPException(status_code=400, detail="target is required")
        try:
            return _safe_target_sync_payload(
                source_type,
                await dependencies.target_sync_service.sync_target(source_type, target),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except HTTPException:
            raise
        except Exception as exc:
            _log_suppressed_error("Target sync failed")
            return {
                "source_id": _source_id_for_target_type(source_type),
                "target_type": source_type,
                "status": "error",
                "message": _safe_public_config_error(
                    str(exc),
                    fallback="Target sync failed. See server logs for details.",
                ),
            }

    @app.post("/api/answer")
    async def answer(request: ConsoleQuery) -> dict[str, Any]:
        if dependencies.answer_service is None:
            raise HTTPException(status_code=503, detail="answer service is not configured")
        question = _normalize_text(request.question)
        if not question:
            raise HTTPException(status_code=400, detail="question is required")
        try:
            filters = _build_filters(request, dependencies.metadata_store)
            return _contextwiki_console_answer_payload(
                await dependencies.answer_service.answer_with_citations(
                    question,
                    filters=filters,
                    top_k=_normalize_top_k(request.top_k, default=5),
                    include_debug=True,
                )
            )
        except HTTPException:
            raise
        except Exception as exc:
            _log_suppressed_error("Answer request failed")
            return _safe_answer_failure_payload(question, exc)

    @app.post("/api/answer/codex")
    async def answer_codex(request: ConsoleQuery) -> dict[str, Any]:
        if dependencies.codex_answer_service is None:
            raise HTTPException(status_code=503, detail="codex answer service is not configured")
        question = _normalize_text(request.question)
        if not question:
            raise HTTPException(status_code=400, detail="question is required")
        try:
            filters = _build_filters(request, dependencies.metadata_store)
            return await dependencies.codex_answer_service.answer_with_codex(
                question,
                filters=filters,
                top_k=_normalize_top_k(request.top_k, default=5),
            )
        except HTTPException:
            raise
        except Exception as exc:
            _log_suppressed_error("Codex answer request failed", exc)
            return _codex_answer_payload(
                question,
                "Codex CLI answer failed. See server logs for details.",
                "error",
                [],
                [],
                codex_status="failed",
            )

    @app.post("/api/wiki/generate")
    async def generate_wiki(request: ConsoleQuery) -> dict[str, Any]:
        if dependencies.wiki_service is None:
            raise HTTPException(status_code=503, detail="wiki service is not configured")
        topic = _normalize_text(request.topic or request.question)
        if not topic:
            raise HTTPException(status_code=400, detail="topic is required")
        try:
            filters = _build_filters(request, dependencies.metadata_store)
            return await dependencies.wiki_service.generate_wiki_page(
                topic,
                filters=filters,
                top_k=_normalize_top_k(request.top_k, default=8),
            )
        except HTTPException:
            raise
        except Exception:
            _log_suppressed_error("Wiki generation failed")
            return {
                "topic": topic,
                "status": "error",
                "title": f"{topic} Wiki",
                "markdown": "Wiki generation failed. See server logs for details.",
                "sections": [],
                "citations": [],
                "backlinks": [],
                "used_chunks": [],
                "message": "Wiki generation failed. See server logs for details.",
            }

    @app.post("/api/smoke/fake")
    async def smoke_fake(request: SmokeRequest | None = None) -> dict[str, Any]:
        runner = dependencies.smoke_runner or ScriptSmokeRunner()
        topic = _normalize_text(request.topic if request else "")
        return await _run_smoke("fake", runner.run_fake, topic=topic or None)

    @app.post("/api/smoke/github")
    async def smoke_github(request: SmokeRequest | None = None) -> dict[str, Any]:
        runner = dependencies.smoke_runner or ScriptSmokeRunner()
        return await _run_smoke(
            "github",
            runner.run_github,
            topic=_normalize_text(request.topic if request else "") or None,
            github_repository=_normalize_text(request.github_repository if request else ""),
            require_generated=bool(request.require_generated if request else False),
        )

    if WEB_ROOT.exists():
        app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")

    return app


def create_default_app() -> FastAPI:
    from environments.config import AppConfig, setup_chroma
    from environments.runtime_env import get_env_secret
    from environments.token import NOTION_API_KEY, TISTORY_BLOG_NAME
    from fetching.connectors import build_source_registry
    from fetching.web_searcher import WebSearcher
    from indexing.chunker import DocumentChunker
    from indexing.indexer import ContentIndexer
    from indexing.ingestion_service import IngestionService
    from llama_index.core import Settings, StorageContext
    from llama_index.vector_stores.chroma import ChromaVectorStore
    from search.answer_service import CitationAnswerService
    from search.context_service import ContextSearchService
    from search.dynamic_search import DynamicSearchService
    from search.service import SearchService
    from storage.metadata_store import MetadataStore
    from wiki.service import WikiGenerationService
    from wiki.synthesis import build_wiki_synthesizer

    process_started_at = datetime.now(timezone.utc).isoformat()
    config = AppConfig()
    notion_api_key = _configured_notion_api_key(NOTION_API_KEY)
    chroma_collection = setup_chroma(config)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    Settings.cache_dir = config.cache_dir

    indexer = ContentIndexer(config, chroma_collection, storage_context)
    metadata_store = MetadataStore(config.metadata_db_path)
    search_service = SearchService(config, indexer, metadata_store=metadata_store)
    web_searcher = WebSearcher(
        notion_api_key=notion_api_key,
        tistory_blog_name=TISTORY_BLOG_NAME,
        config=config,
        github_repositories=config.github_repositories,
        github_token=get_env_secret(config.github_token_env_var),
    )
    DynamicSearchService(
        local_search=search_service,
        web_searcher=web_searcher,
        indexer=indexer,
        min_threshold=3,
    )
    source_registry = build_source_registry(
        config=config,
        notion_api_key=notion_api_key,
        tistory_blog_name=TISTORY_BLOG_NAME,
        github_token=get_env_secret(config.github_token_env_var),
    )
    ingestion_service = IngestionService(
        metadata_store=metadata_store,
        source_registry=source_registry,
        chunker=DocumentChunker(),
        indexer=indexer,
    )
    _recover_orphaned_sync_jobs_for_startup(
        metadata_store,
        process_started_at=process_started_at,
    )
    context_search = ContextSearchService(
        metadata_store=metadata_store,
        indexer=indexer,
        config=config,
    )
    answer_service = CitationAnswerService(context_search)
    codex_answer_service = CodexCliAnswerService(context_search)
    wiki_llm_api_key = (
        get_env_secret(config.wiki_llm_api_key_env_var)
        if config.wiki_llm_enabled and config.wiki_llm_provider == "openai"
        else ""
    )
    wiki_service = WikiGenerationService(
        context_search,
        llm_synthesizer=build_wiki_synthesizer(config, api_key=wiki_llm_api_key),
    )
    return create_console_app(
        _build_console_dependencies(
            config=config,
            answer_service=answer_service,
            codex_answer_service=codex_answer_service,
            wiki_service=wiki_service,
            metadata_store=metadata_store,
            ingestion_service=ingestion_service,
            indexer=indexer,
            github_token=get_env_secret(config.github_token_env_var),
            notion_api_key=notion_api_key,
            auto_sync_source_ids=config.contextwiki_auto_sync_sources,
        )
    )


def _build_console_dependencies(
    *,
    config: Any,
    answer_service: Any,
    codex_answer_service: Any,
    wiki_service: Any,
    metadata_store: Any,
    ingestion_service: Any,
    indexer: Any,
    github_token: str,
    notion_api_key: str,
    auto_sync_source_ids: tuple[str, ...] = (),
) -> ConsoleDependencies:
    github_sync_service = GitHubTargetSyncService(
        config=config,
        metadata_store=metadata_store,
        indexer=indexer,
        github_token=github_token,
    )
    notion_sync_service = NotionTargetSyncService(
        config=config,
        metadata_store=metadata_store,
        indexer=indexer,
        notion_api_key=notion_api_key,
    )
    web_sync_service = WebTargetSyncService(
        config=config,
        metadata_store=metadata_store,
        indexer=indexer,
    )
    target_sync_service = TargetSyncService(
        github_sync_service=github_sync_service,
        notion_sync_service=notion_sync_service,
        web_sync_service=web_sync_service,
    )
    return ConsoleDependencies(
        answer_service=answer_service,
        codex_answer_service=codex_answer_service,
        wiki_service=wiki_service,
        metadata_store=metadata_store,
        ingestion_service=ingestion_service,
        target_sync_service=target_sync_service,
        github_sync_service=github_sync_service,
        smoke_runner=ScriptSmokeRunner(),
        auto_sync_source_ids=auto_sync_source_ids,
    )


async def _run_startup_auto_sync_sources(
    ingestion_service: Any,
    source_ids: tuple[str, ...],
) -> None:
    for source_id in source_ids:
        try:
            await ingestion_service.sync_source(source_id)
        except Exception as exc:
            _log_suppressed_error(f"Startup auto-sync failed for {source_id}", exc)


def _configured_notion_api_key(canonical_value: str) -> str:
    if canonical_value:
        return canonical_value
    for name in ("NOTION_API_KEY", "NOTION_TOKEN", "NOTION_API_TOKEN", "notion_token"):
        value = os.getenv(name, "")
        if value:
            return value
    return ""


def _log_suppressed_error(message: str, exc: Exception | None = None) -> None:
    if exc is None:
        logger.error("%s; details suppressed to avoid leaking secrets", message)
        return
    logger.error(
        "%s; details suppressed to avoid leaking secrets; error_type=%s",
        message,
        type(exc).__name__,
    )
