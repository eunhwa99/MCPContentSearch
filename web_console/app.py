from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import logging
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = REPO_ROOT / "web"
logger = logging.getLogger(__name__)


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


@dataclass
class ConsoleDependencies:
    answer_service: Any = None
    wiki_service: Any = None
    metadata_store: Any = None
    smoke_runner: Any = None


class ScriptSmokeRunner:
    """Run existing smoke helpers and keep their structured result shape."""

    async def run_fake(self, *, topic: str | None = None) -> dict[str, Any]:
        from scripts.smoke_generate_wiki_page import run_fake

        with tempfile.TemporaryDirectory(
            prefix="contextwiki-web-console-fake-", dir="/private/tmp"
        ) as output_dir:
            result = await run_fake(Path(output_dir), topic or "ContextWiki citations")
        return _without_persisted_output_path(result)

    async def run_github(
        self,
        *,
        topic: str | None = None,
        github_repository: str = "",
        require_generated: bool = False,
    ) -> dict[str, Any]:
        from scripts.smoke_generate_wiki_page import run_github

        with tempfile.TemporaryDirectory(
            prefix="contextwiki-web-console-github-", dir="/private/tmp"
        ) as output_dir:
            args = SimpleNamespace(
                github_repository=github_repository,
                github_max_files=20,
                github_max_file_bytes=64_000,
                request_timeout=10.0,
                topic=topic or "README",
                output_dir=Path(output_dir),
                require_generated=require_generated,
            )
            result = await run_github(args)
        return _without_persisted_output_path(result)


def create_console_app(dependencies: ConsoleDependencies) -> FastAPI:
    app = FastAPI(
        title="ContextWiki Local Web Test Console",
        description="Local-only HTTP wrapper over ContextWiki services.",
        version="0.1.0",
    )

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

    @app.post("/api/answer")
    async def answer(request: ConsoleQuery) -> dict[str, Any]:
        if dependencies.answer_service is None:
            raise HTTPException(status_code=503, detail="answer service is not configured")
        question = _normalize_text(request.question)
        if not question:
            raise HTTPException(status_code=400, detail="question is required")
        try:
            filters = _build_filters(request, dependencies.metadata_store)
            return await dependencies.answer_service.answer_with_citations(
                question,
                filters=filters,
                top_k=_normalize_top_k(request.top_k, default=5),
            )
        except HTTPException:
            raise
        except Exception:
            _log_suppressed_error("Answer request failed")
            return {
                "question": question,
                "answer": "Answer failed. See server logs for details.",
                "evidence_status": "error",
                "citations": [],
                "used_chunks": [],
            }

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

    config = AppConfig()
    chroma_collection = setup_chroma(config)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    Settings.cache_dir = config.cache_dir

    indexer = ContentIndexer(config, chroma_collection, storage_context)
    metadata_store = MetadataStore(config.metadata_db_path)
    search_service = SearchService(config, indexer, metadata_store=metadata_store)
    web_searcher = WebSearcher(
        notion_api_key=NOTION_API_KEY,
        tistory_blog_name=TISTORY_BLOG_NAME,
        config=config,
    )
    DynamicSearchService(
        local_search=search_service,
        web_searcher=web_searcher,
        indexer=indexer,
        min_threshold=3,
    )
    source_registry = build_source_registry(
        config=config,
        notion_api_key=NOTION_API_KEY,
        tistory_blog_name=TISTORY_BLOG_NAME,
        github_token=get_env_secret(config.github_token_env_var),
    )
    IngestionService(
        metadata_store=metadata_store,
        source_registry=source_registry,
        chunker=DocumentChunker(),
        indexer=indexer,
    )
    context_search = ContextSearchService(
        metadata_store=metadata_store,
        indexer=indexer,
        config=config,
    )
    answer_service = CitationAnswerService(context_search)
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
        ConsoleDependencies(
            answer_service=answer_service,
            wiki_service=wiki_service,
            metadata_store=metadata_store,
            smoke_runner=ScriptSmokeRunner(),
        )
    )


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalize_top_k(value: Any, *, default: int) -> int:
    try:
        top_k = int(value)
    except (TypeError, ValueError):
        top_k = default
    return max(1, min(top_k, 20))


async def _run_smoke(mode: str, runner_method, **kwargs) -> dict[str, Any]:
    try:
        return await runner_method(**kwargs)
    except Exception:
        _log_suppressed_error(f"Web console {mode} smoke failed")
        return {
            "mode": mode,
            "status": "failed",
            "error": "Smoke check failed. See server logs for details.",
        }


def _list_sources(metadata_store: Any) -> list[dict[str, Any]]:
    if metadata_store is None:
        return []
    return [
        _dump_model(source)
        for source in metadata_store.list_sources()
    ]


def _dump_model(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return dict(value)


def _build_filters(request: ConsoleQuery, metadata_store: Any) -> dict[str, Any]:
    filters = dict(request.filters or {})
    source_ids = _normalize_list(filters.pop("source_ids", []))
    source_ids.extend(_normalize_list(filters.pop("source_id", [])))
    source_types = _normalize_list(filters.pop("source_types", []))
    source_types.extend(_normalize_list(filters.pop("source_type", [])))
    source_types.extend(_normalize_list(request.source_types))
    matched_source_ids = _source_ids_for_types(metadata_store, source_types)
    if source_types and not matched_source_ids:
        raise HTTPException(
            status_code=400,
            detail="no configured sources match selected source types",
        )
    source_ids.extend(matched_source_ids)
    source_ids.extend(_normalize_list(request.source_ids))
    if source_ids:
        filters["source_ids"] = _dedupe(source_ids)
    return filters


def _normalize_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        values = value.replace("\n", ",").split(",")
    elif isinstance(value, list | tuple | set):
        values = value
    else:
        values = [value]
    return [_normalize_text(item) for item in values if _normalize_text(item)]


def _source_ids_for_types(metadata_store: Any, source_types: list[str]) -> list[str]:
    requested = {_normalize_source_type(value) for value in source_types}
    requested.discard("")
    if not requested or metadata_store is None:
        return []
    source_ids = []
    for source in _list_sources(metadata_store):
        if _normalize_source_type(source.get("source_type", "")) in requested:
            source_ids.append(source["source_id"])
    return source_ids


def _normalize_source_type(value: Any) -> str:
    normalized = _normalize_text(value).lower()
    if normalized in {"docs", "pdf"}:
        return "web"
    return normalized


def _is_loopback_client(host: str | None) -> bool:
    if host in {"testclient", "localhost"}:
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def _remote_console_allowed() -> bool:
    return os.getenv("CONTEXTWIKI_WEB_CONSOLE_ALLOW_REMOTE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _log_suppressed_error(message: str) -> None:
    logger.error("%s; details suppressed to avoid leaking secrets", message)


def _is_local_host_header(value: str) -> bool:
    host = _parse_authority_host(value)
    if not host:
        return False
    return host in {"localhost", "testserver"} or _is_loopback_client(host)


def _is_local_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return False
    return _is_local_host_header(parsed.netloc or parsed.path)


def _parse_authority_host(value: str) -> str:
    authority = (value or "").strip()
    if not authority or "@" in authority:
        return ""
    if "://" in authority:
        parsed = urlparse(authority)
        authority = parsed.netloc
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            return ""
        host = authority[1:end].strip().lower()
        remainder = authority[end + 1 :]
        if remainder and not (remainder.startswith(":") and remainder[1:].isdigit()):
            return ""
        return host
    try:
        ipaddress.ip_address(authority)
        return authority.lower()
    except ValueError:
        pass
    if ":" in authority:
        host, port = authority.rsplit(":", 1)
        if not port.isdigit():
            return ""
        authority = host
    if ":" in authority:
        return ""
    return authority.strip().lower()


def _without_persisted_output_path(result: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(result)
    if cleaned.pop("output_path", None):
        cleaned["output_retention"] = "temporary file cleaned up"
    return cleaned


def _dedupe(values: list[str]) -> list[str]:
    deduped = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped
