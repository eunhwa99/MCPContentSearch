import asyncio
import json
import shutil
from urllib.parse import parse_qs, urlparse

import pytest
from mcp.server.fastmcp import FastMCP

from api.tools import register_tools
from core.models import SyncJobStatus, SyncStatus
from environments.config import AppConfig
from fetching.connectors import ObsidianSourceConnector, SourceRegistry
from indexing.chunker import DocumentChunker
from indexing.ingestion_service import IngestionService
from search.answer_service import CitationAnswerService
from search.context_service import ContextSearchService
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.e2e

OBSIDIAN_ENV_VARS = (
    "CONTEXTWIKI_OBSIDIAN_VAULT_PATH",
    "CONTEXTWIKI_OBSIDIAN_MAX_FILES",
    "CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES",
)


@pytest.fixture(autouse=True)
def clear_obsidian_env(monkeypatch):
    for name in OBSIDIAN_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class RecordingIndexer:
    def __init__(self):
        self.documents = []
        self.deleted_ids = []

    async def index_documents(self, documents):
        self.documents.extend(documents)

    def delete_documents_by_ids(self, document_ids, source_id=""):
        self.deleted_ids.extend(document_ids)


def _call_tool_json(mcp: FastMCP, name: str, arguments: dict | None = None) -> dict:
    blocks = asyncio.run(mcp.call_tool(name, arguments or {}))
    return json.loads(blocks[0].text)


def _make_vault(tmp_path, files: dict[str, str]):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    for rel_path, content in files.items():
        target = vault / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return vault


def _obsidian_service(tmp_path, vault, **config_overrides):
    connector = ObsidianSourceConnector(
        AppConfig(obsidian_vault_path=vault, **config_overrides)
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    registry = SourceRegistry([connector])
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=registry,
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=indexer,
    )
    context_search = ContextSearchService(
        metadata_store=store,
        retriever=indexer.documents,
        default_source_ids=["source_obsidian"],
    )
    answer_service = CitationAnswerService(
        context_search=context_search,
        min_score=0.1,
        min_results=1,
    )
    mcp = FastMCP("obsidian-retained-source-smoke")
    register_tools(
        mcp,
        ingestion_service=ingestion,
        context_search_service=context_search,
        answer_service=answer_service,
        metadata_store=store,
        source_registry=registry,
    )
    return connector, store, indexer, mcp


def test_obsidian_sync_through_mcp_tools_indexes_temp_vault_notes_with_citations(tmp_path):
    vault = _make_vault(
        tmp_path,
        {
            "notes/project.md": (
                "---\n"
                "title: Project Atlas\n"
                "---\n\n"
                "# Architecture\n"
                "Obsidian retained smoke coverage writes searchable citation chunks.\n"
            ),
            "daily/2026-06-11.md": (
                "# Daily\n"
                "Temp vault sync keeps local markdown notes bounded and deterministic.\n"
            ),
        },
    )
    (vault / ".obsidian" / "config.md").write_text("internal", encoding="utf-8")
    (vault / ".trash").mkdir()
    (vault / ".trash" / "deleted.md").write_text("deleted", encoding="utf-8")

    connector, store, indexer, mcp = _obsidian_service(tmp_path, vault)

    listed = _call_tool_json(mcp, "list_sources")
    sync_job = _call_tool_json(mcp, "sync_source", {"source_id": "source_obsidian"})
    status = _call_tool_json(mcp, "get_sync_status", {"source_id": "source_obsidian"})
    project = store.get_document("notes/project.md")
    project_chunks = store.list_chunks_for_document("notes/project.md")
    search_result = _call_tool_json(
        mcp,
        "search_context",
        {
            "query": "Obsidian retained smoke coverage",
            "filters": {"source_id": "source_obsidian"},
            "top_k": 3,
        },
    )
    fetched_document = _call_tool_json(
        mcp,
        "fetch_context",
        {"document_id": "notes/project.md"},
    )
    fetched_chunk = _call_tool_json(
        mcp,
        "fetch_context",
        {"chunk_id": search_result["results"][0]["chunk_id"]},
    )
    answer = _call_tool_json(
        mcp,
        "answer_with_citations",
        {
            "question": "What does the Obsidian project note say about smoke coverage?",
            "filters": {"source_id": "source_obsidian"},
            "top_k": 3,
        },
    )

    assert [source["source_id"] for source in listed["sources"]] == ["source_obsidian"]
    assert listed["sources"][0]["enabled"] is True
    assert connector.supports_stale_cleanup is True
    assert sync_job["status"] == "succeeded"
    assert sync_job["source_id"] == "source_obsidian"
    assert sync_job["processed_documents"] == 2
    assert status["source"]["sync_status"] == "succeeded"
    assert status["latest_job"]["status"] == "succeeded"
    assert store.get_source("source_obsidian").sync_status == SyncStatus.SUCCEEDED
    assert project is not None
    assert project.source_id == "source_obsidian"
    assert project.external_id == "notes/project.md"
    assert project.content == "\n# Architecture\nObsidian retained smoke coverage writes searchable citation chunks.\n"
    assert store.get_document(".obsidian/config.md") is None
    assert store.get_document(".trash/deleted.md") is None
    assert project_chunks
    assert project_chunks[0].title == "Project Atlas"
    assert project_chunks[0].source_id == "source_obsidian"
    assert project_chunks[0].line_start == 5
    parsed_url = urlparse(project_chunks[0].url)
    assert parsed_url.scheme == "obsidian"
    assert parsed_url.netloc == "open"
    assert parse_qs(parsed_url.query)["file"] == ["notes/project.md"]
    assert all(document.source_id == "source_obsidian" for document in indexer.documents)
    assert search_result["results"][0]["source_id"] == "source_obsidian"
    assert search_result["results"][0]["title"] == "Project Atlas"
    assert fetched_document["document"]["document_id"] == "notes/project.md"
    assert fetched_document["chunks"][0]["chunk_id"] == project_chunks[0].chunk_id
    assert fetched_chunk["chunk"]["chunk_id"] == search_result["results"][0]["chunk_id"]
    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == [search_result["results"][0]["chunk_id"]]
    assert answer["citations"][0]["url"].startswith("obsidian://open?")


def test_obsidian_incomplete_snapshot_does_not_tombstone_active_notes(tmp_path):
    vault = _make_vault(
        tmp_path,
        {
            "keep.md": "This note stays active.",
            "unstable.md": "This note becomes unreadable.",
        },
    )
    config = AppConfig(obsidian_vault_path=vault)
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    first_connector = ObsidianSourceConnector(config)
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_obsidian"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document("unstable.md")

    (vault / "unstable.md").write_bytes(b"\xff\xfe\x00bad-utf8")
    second_connector = ObsidianSourceConnector(config)
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_obsidian"))

    assert second_job.status == SyncJobStatus.FAILED
    assert "snapshot was incomplete" in second_job.error_message
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("keep.md").deleted_at == ""
    assert store.get_document("unstable.md").deleted_at == ""
    assert store.list_chunks_for_document("unstable.md")
    assert indexer.deleted_ids == []


def test_obsidian_disabled_after_missing_vault_does_not_tombstone_active_notes(tmp_path):
    vault = _make_vault(tmp_path, {"keep.md": "This note stays active."})
    config = AppConfig(obsidian_vault_path=vault)
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_obsidian"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("keep.md").deleted_at == ""

    second_connector = ObsidianSourceConnector(config)
    shutil.rmtree(vault)
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_obsidian"))

    assert second_job.status == SyncJobStatus.FAILED
    assert second_connector.source.enabled is False
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("keep.md").deleted_at == ""
    assert store.list_chunks_for_document("keep.md")
    assert indexer.deleted_ids == []


def test_obsidian_file_limit_failure_does_not_tombstone_active_notes(tmp_path):
    vault = _make_vault(tmp_path, {"keep.md": "This note stays active."})
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    first_connector = ObsidianSourceConnector(
        AppConfig(obsidian_vault_path=vault, obsidian_max_files=10)
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_obsidian"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("keep.md").deleted_at == ""

    (vault / "extra.md").write_text("This exceeds the configured limit.", encoding="utf-8")
    second_connector = ObsidianSourceConnector(
        AppConfig(obsidian_vault_path=vault, obsidian_max_files=1)
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_obsidian"))

    assert second_job.status == SyncJobStatus.FAILED
    assert "snapshot was incomplete" in second_job.error_message
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("keep.md").deleted_at == ""
    assert store.list_chunks_for_document("keep.md")
    assert indexer.deleted_ids == []


def test_obsidian_visible_symlinked_note_failure_does_not_tombstone_active_note(
    tmp_path,
):
    vault = _make_vault(tmp_path, {"keep.md": "This note starts as a real file."})
    config = AppConfig(obsidian_vault_path=vault)
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    first_connector = ObsidianSourceConnector(config)
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_obsidian"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("keep.md").deleted_at == ""
    assert store.list_chunks_for_document("keep.md")

    outside_note = tmp_path / "outside.md"
    outside_note.write_text("This outside note must not be followed.", encoding="utf-8")
    (vault / "keep.md").unlink()
    (vault / "keep.md").symlink_to(outside_note)
    second_connector = ObsidianSourceConnector(config)
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_obsidian"))

    assert second_job.status == SyncJobStatus.FAILED
    assert "snapshot was incomplete" in second_job.error_message
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("keep.md").deleted_at == ""
    assert store.list_chunks_for_document("keep.md")
    assert indexer.deleted_ids == []
