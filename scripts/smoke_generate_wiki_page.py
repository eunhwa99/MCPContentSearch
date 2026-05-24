#!/usr/bin/env python3
"""Smoke `generate_wiki_page` through real FastMCP registration and call_tool."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

# Keep the fake smoke local-only before chromadb is imported through AppConfig.
os.environ["ANONYMIZED_TELEMETRY"] = "False"

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.models import DocumentModel, SourceModel, SourceType, SyncStatus
from fetching.connectors import GitHubSourceConnector, SourceConnector, SourceRegistry
from wiki.synthesis import OpenAIWikiSynthesizer


DEFAULT_OUTPUT_DIR = Path("/private/tmp/contextwiki-wiki-smoke")


class SmokeFailure(RuntimeError):
    """A deterministic smoke check failed."""


class FakeWikiConnector(SourceConnector):
    supports_stale_cleanup = True
    source = SourceModel(
        source_id="source_fake_wiki_smoke",
        source_type=SourceType.NOTION,
        name="Fake Wiki Smoke",
        enabled=True,
        auth_ref="env:FAKE_WIKI_SMOKE",
        sync_status=SyncStatus.IDLE,
    )

    async def fetch_documents(self) -> list[DocumentModel]:
        return [
            DocumentModel(
                id="doc_contextwiki_wiki_smoke",
                source_id=self.source.source_id,
                title="ContextWiki Wiki Smoke",
                content=(
                    "ContextWiki citations generate wiki pages from active chunks. "
                    "The smoke validates FastMCP call_tool, backlinks, and citations."
                ),
                url="https://example.com/contextwiki/wiki-smoke",
                platform="Notion",
                path="ContextWiki Wiki Smoke",
                updated_at="2026-05-24T00:00:00Z",
            ),
            DocumentModel(
                id="doc_contextwiki_validation",
                source_id=self.source.source_id,
                title="Wiki Validation",
                content=(
                    "Live smoke should use temporary Chroma and SQLite paths and "
                    "write Markdown output outside persistent user data directories."
                ),
                url="https://example.com/contextwiki/wiki-validation",
                platform="Notion",
                path="Wiki Validation",
                updated_at="2026-05-24T00:00:00Z",
            ),
        ]


def _redact(value: Any) -> str:
    return str(OpenAIWikiSynthesizer._redact_secret_like(str(value)))


@contextmanager
def _suppress_sync_error_logs():
    """Keep failed live smoke output limited to the redacted JSON result."""

    logger_names = (
        "api.tools",
        "indexing.ingestion_service",
        "fetching.connectors",
        "fetching.github",
    )
    previous_levels = []
    try:
        for logger_name in logger_names:
            logger = logging.getLogger(logger_name)
            previous_levels.append((logger, logger.level))
            logger.setLevel(logging.CRITICAL + 1)
        yield
    finally:
        for logger, level in previous_levels:
            logger.setLevel(level)


def _collection_name(mode: str) -> str:
    safe_mode = re.sub(r"[^a-zA-Z0-9_]", "_", mode)
    return f"contextwiki_wiki_smoke_{safe_mode}_{os.getpid()}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._")
    return slug[:80] or "wiki-page"


def _decode_call_tool_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if isinstance(result, list):
        text = "\n".join(
            item.text for item in result if getattr(item, "type", "") == "text"
        )
        if text:
            return json.loads(text)
    raise SmokeFailure(f"Unexpected FastMCP call_tool result: {type(result).__name__}")


async def _call_tool_json(
    mcp: FastMCP,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return _decode_call_tool_result(await mcp.call_tool(name, arguments))


def _write_markdown(
    output_dir: Path,
    *,
    mode: str,
    topic: str,
    payload: dict[str, Any],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{_slug(mode)}-{_slug(topic)}.md"
    markdown = payload.get("markdown") or payload.get("message") or ""
    output_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return output_path


def _build_mcp_app(config: AppConfig, registry: SourceRegistry) -> FastMCP:
    from llama_index.core import StorageContext
    from llama_index.vector_stores.chroma import ChromaVectorStore
    from mcp.server.fastmcp import FastMCP

    from api.tools import register_tools
    from environments.config import setup_chroma
    from indexing.chunker import DocumentChunker
    from indexing.indexer import ContentIndexer
    from indexing.ingestion_service import IngestionService
    from search.answer_service import CitationAnswerService
    from search.context_service import ContextSearchService
    from storage.metadata_store import MetadataStore
    from wiki.service import WikiGenerationService

    chroma_collection = setup_chroma(config)
    storage_context = StorageContext.from_defaults(
        vector_store=ChromaVectorStore(chroma_collection=chroma_collection)
    )
    indexer = ContentIndexer(config, chroma_collection, storage_context)
    metadata_store = MetadataStore(config.metadata_db_path)
    ingestion = IngestionService(
        metadata_store=metadata_store,
        source_registry=registry,
        chunker=DocumentChunker(max_chars=600, overlap_chars=80),
        indexer=indexer,
    )
    context_search = ContextSearchService(
        metadata_store=metadata_store,
        indexer=indexer,
        config=config,
    )
    answer_service = CitationAnswerService(context_search=context_search)
    wiki_service = WikiGenerationService(context_search)
    mcp = FastMCP("contextwiki-wiki-smoke")
    register_tools(
        mcp,
        indexer=indexer,
        search_service=None,
        dynamic_search=None,
        web_searcher=None,
        ingestion_service=ingestion,
        context_search_service=context_search,
        answer_service=answer_service,
        wiki_service=wiki_service,
        metadata_store=metadata_store,
        source_registry=registry,
    )
    return mcp


async def _run_with_temp_app(
    *,
    mode: str,
    registry_factory,
    source_id: str,
    topic: str,
    output_dir: Path,
    require_generated: bool,
) -> dict[str, Any]:
    from llama_index.core import Settings
    from llama_index.core.embeddings import MockEmbedding

    from environments.config import AppConfig

    previous_embed_model = Settings.embed_model
    Settings.embed_model = MockEmbedding(embed_dim=8)
    try:
        with tempfile.TemporaryDirectory(
            prefix=f"contextwiki-{mode}-", dir="/private/tmp"
        ) as temp_root:
            temp_path = Path(temp_root)
            config = AppConfig(
                chroma_db_path=temp_path / "chroma",
                metadata_db_path=temp_path / "contextwiki_metadata.sqlite3",
                collection_name=_collection_name(mode),
                search_multiplier=4,
                github_repositories=registry_factory.github_repositories,
                github_max_files=registry_factory.github_max_files,
                github_max_file_bytes=registry_factory.github_max_file_bytes,
                request_timeout=registry_factory.request_timeout,
                wiki_llm_enabled=False,
            )
            try:
                registry = registry_factory(config)
                mcp = _build_mcp_app(config, registry)
            except Exception as exc:
                return {
                    "mode": mode,
                    "status": "skipped",
                    "reason": _redact(exc),
                }

            with _suppress_sync_error_logs():
                sync = await _call_tool_json(mcp, "sync_source", {"source_id": source_id})
            if sync.get("status") != "succeeded":
                return {
                    "mode": mode,
                    "status": "skipped",
                    "reason": _redact(
                        sync.get("error_message") or sync.get("message") or sync
                    ),
                }

            wiki = await _call_tool_json(
                mcp,
                "generate_wiki_page",
                {
                    "topic": topic,
                    "filters": {"source_id": source_id},
                    "top_k": 8,
                },
            )
            if wiki.get("status") != "generated" and require_generated:
                raise SmokeFailure(
                    f"{mode} wiki smoke returned status={wiki.get('status')!r}"
                )
            output_path = _write_markdown(output_dir, mode=mode, topic=topic, payload=wiki)
            return {
                "mode": mode,
                "status": "passed" if wiki.get("status") == "generated" else "skipped",
                "wiki_status": wiki.get("status"),
                "output_path": str(output_path),
                "citations": len(wiki.get("citations", [])),
                "backlinks": len(wiki.get("backlinks", [])),
                "used_chunks": len(wiki.get("used_chunks", [])),
            }
    finally:
        Settings.embed_model = previous_embed_model


class FakeRegistryFactory:
    github_repositories: tuple[str, ...] = ()
    github_max_files = 20
    github_max_file_bytes = 64_000
    request_timeout = 10.0

    def __call__(self, config: AppConfig) -> SourceRegistry:
        return SourceRegistry([FakeWikiConnector()])


class GitHubRegistryFactory:
    def __init__(
        self,
        *,
        repository: str,
        max_files: int,
        max_file_bytes: int,
        request_timeout: float,
    ):
        self.github_repositories = (repository,)
        self.github_max_files = max_files
        self.github_max_file_bytes = max_file_bytes
        self.request_timeout = request_timeout

    def __call__(self, config: AppConfig) -> SourceRegistry:
        token = os.getenv(config.github_token_env_var, "")
        return SourceRegistry(
            [GitHubSourceConnector(config.github_repositories, config, token=token)]
        )


async def run_fake(output_dir: Path, topic: str) -> dict[str, Any]:
    result = await _run_with_temp_app(
        mode="fake",
        registry_factory=FakeRegistryFactory(),
        source_id="source_fake_wiki_smoke",
        topic=topic,
        output_dir=output_dir,
        require_generated=True,
    )
    if result["status"] != "passed":
        raise SmokeFailure(f"Fake wiki smoke did not pass: {result}")
    return result


async def run_github(args) -> dict[str, Any]:
    repository = args.github_repository or _first_configured_github_repository()
    if not repository:
        return {
            "mode": "github",
            "status": "skipped",
            "reason": (
                "No GitHub repository configured. Pass --github-repository or set "
                "CONTEXTWIKI_GITHUB_REPOSITORIES."
            ),
        }
    return await _run_with_temp_app(
        mode="github",
        registry_factory=GitHubRegistryFactory(
            repository=repository,
            max_files=args.github_max_files,
            max_file_bytes=args.github_max_file_bytes,
            request_timeout=args.request_timeout,
        ),
        source_id="source_github",
        topic=args.topic or "README",
        output_dir=args.output_dir,
        require_generated=args.require_generated,
    )


def _first_configured_github_repository() -> str:
    configured = os.getenv("CONTEXTWIKI_GITHUB_REPOSITORIES", "")
    return next(
        (item.strip() for item in configured.replace("\n", ",").split(",") if item.strip()),
        "",
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run ContextWiki wiki generation smoke through real FastMCP "
            "registration and call_tool."
        )
    )
    parser.add_argument(
        "--mode",
        choices=("fake", "github", "all"),
        default="fake",
        help="Run deterministic fake smoke, optional live GitHub smoke, or both.",
    )
    parser.add_argument(
        "--topic",
        default=None,
        help="Wiki topic. Defaults to 'ContextWiki citations' for fake and 'README' for GitHub.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for generated Markdown output.",
    )
    parser.add_argument(
        "--github-repository",
        default="",
        help="Optional live GitHub repository spec, for example owner/repo@main.",
    )
    parser.add_argument(
        "--github-max-files",
        type=int,
        default=20,
        help="Maximum files to fetch during live GitHub smoke.",
    )
    parser.add_argument(
        "--github-max-file-bytes",
        type=int,
        default=64_000,
        help="Maximum GitHub file size fetched during live smoke.",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=10.0,
        help="Per-request timeout for live GitHub smoke.",
    )
    parser.add_argument(
        "--require-generated",
        action="store_true",
        help="Fail optional live smoke if sync succeeds but wiki status is not generated.",
    )
    return parser.parse_args(argv)


async def async_main(argv: list[str]) -> int:
    args = parse_args(argv)
    args.output_dir = args.output_dir.expanduser()

    results: list[dict[str, Any]] = []
    try:
        if args.mode in {"fake", "all"}:
            fake_topic = args.topic or "ContextWiki citations"
            results.append(await run_fake(args.output_dir, fake_topic))
        if args.mode in {"github", "all"}:
            results.append(await run_github(args))
    except SmokeFailure as exc:
        print(json.dumps({"status": "failed", "error": _redact(exc)}, indent=2))
        return 1

    print(json.dumps({"status": "completed", "results": results}, indent=2))
    return 0


def main() -> int:
    return asyncio.run(async_main(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
