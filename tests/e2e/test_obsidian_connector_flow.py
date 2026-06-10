import asyncio

import pytest

from core.models import SyncJobStatus, SyncStatus
from environments.config import AppConfig
from fetching.connectors import ObsidianSourceConnector, SourceRegistry
from indexing.chunker import DocumentChunker
from indexing.ingestion_service import IngestionService
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.e2e


class RecordingIndexer:
    def __init__(self):
        self.documents = []
        self.deleted_ids = []

    async def index_documents(self, documents):
        self.documents.extend(documents)

    def delete_documents_by_ids(self, document_ids, source_id=""):
        self.deleted_ids.extend(document_ids)


def _make_vault(tmp_path, files: dict[str, str]):
    """Create a fake Obsidian vault under tmp_path with the given file contents."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    for rel_path, content in files.items():
        target = vault / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return vault


def test_obsidian_connector_syncs_markdown_files(tmp_path):
    vault = _make_vault(tmp_path, {
        "notes/project.md": "# Project\nThis is a project note.",
        "daily/2026-06-10.md": "## Daily\nToday's standup notes.",
    })
    config = AppConfig(obsidian_vault_path=vault)
    connector = ObsidianSourceConnector(config)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=indexer,
    )

    job = asyncio.run(service.sync_source("source_obsidian"))

    assert job.status == SyncJobStatus.SUCCEEDED
    assert store.get_source("source_obsidian").sync_status == SyncStatus.SUCCEEDED
    doc_ids = {d.document_id for d in indexer.documents}
    assert "notes/project.md" in doc_ids
    assert "daily/2026-06-10.md" in doc_ids


def test_obsidian_connector_skips_obsidian_system_dirs(tmp_path):
    vault = _make_vault(tmp_path, {
        "notes/real.md": "# Real note",
    })
    (vault / ".obsidian" / "config.md").write_text("internal config", encoding="utf-8")
    (vault / ".trash").mkdir()
    (vault / ".trash" / "deleted.md").write_text("deleted note", encoding="utf-8")
    # Generic dot-prefixed directory (not in the explicit frozenset)
    (vault / ".hidden_plugin").mkdir()
    (vault / ".hidden_plugin" / "note.md").write_text("plugin note", encoding="utf-8")
    # Dot-prefixed file at vault root
    (vault / ".DS_Store.md").write_text("system file", encoding="utf-8")

    config = AppConfig(obsidian_vault_path=vault)
    connector = ObsidianSourceConnector(config)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=indexer,
    )

    job = asyncio.run(service.sync_source("source_obsidian"))

    doc_ids = {d.document_id for d in indexer.documents}
    assert job.status == SyncJobStatus.SUCCEEDED
    assert "notes/real.md" in doc_ids
    assert ".obsidian/config.md" not in doc_ids
    assert ".trash/deleted.md" not in doc_ids
    assert ".hidden_plugin/note.md" not in doc_ids
    assert ".DS_Store.md" not in doc_ids


def test_obsidian_connector_uses_frontmatter_title(tmp_path):
    vault = _make_vault(tmp_path, {
        "notes/untitled.md": "---\ntitle: My Custom Title\n---\nBody content here.",
    })
    config = AppConfig(obsidian_vault_path=vault)
    connector = ObsidianSourceConnector(config)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=indexer,
    )

    job = asyncio.run(service.sync_source("source_obsidian"))

    assert job.status == SyncJobStatus.SUCCEEDED
    chunks = store.list_chunks_for_document("notes/untitled.md")
    assert chunks
    assert chunks[0].title == "My Custom Title"
    assert "---" not in chunks[0].text
    assert chunks[0].text.strip() == "Body content here."


def test_obsidian_connector_falls_back_to_filename_when_no_frontmatter(tmp_path):
    vault = _make_vault(tmp_path, {
        "notes/my-idea.md": "Just some content, no frontmatter.",
    })
    config = AppConfig(obsidian_vault_path=vault)
    connector = ObsidianSourceConnector(config)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=indexer,
    )

    job = asyncio.run(service.sync_source("source_obsidian"))

    assert job.status == SyncJobStatus.SUCCEEDED
    chunks = store.list_chunks_for_document("notes/my-idea.md")
    assert chunks
    assert chunks[0].title == "my-idea"


def test_obsidian_connector_canonical_url_uses_obsidian_scheme(tmp_path):
    vault = _make_vault(tmp_path, {
        "notes/design.md": "Architecture notes.",
    })
    config = AppConfig(obsidian_vault_path=vault)
    connector = ObsidianSourceConnector(config)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=indexer,
    )

    job = asyncio.run(service.sync_source("source_obsidian"))

    assert job.status == SyncJobStatus.SUCCEEDED
    chunks = store.list_chunks_for_document("notes/design.md")
    assert chunks
    assert chunks[0].url.startswith("obsidian://open?vault=")
    assert "notes/design.md" in chunks[0].url


def test_obsidian_stale_cleanup_removes_deleted_file(tmp_path):
    vault = _make_vault(tmp_path, {
        "keep.md": "This note stays.",
        "delete_me.md": "This note will be deleted.",
    })
    config = AppConfig(obsidian_vault_path=vault)

    store = MetadataStore(tmp_path / "meta.sqlite3")
    indexer = RecordingIndexer()
    first_connector = ObsidianSourceConnector(config)
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_obsidian"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert first_connector.supports_stale_cleanup is True
    assert store.list_chunks_for_document("delete_me.md")

    (vault / "delete_me.md").unlink()

    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_obsidian"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("delete_me.md").deleted_at != ""
    assert store.get_document("keep.md").deleted_at == ""
    assert any(d.startswith("delete_me.md") for d in indexer.deleted_ids)


def test_obsidian_connector_disabled_when_vault_path_not_set(tmp_path):
    config = AppConfig(obsidian_vault_path=None)
    connector = ObsidianSourceConnector(config)

    assert connector.source.enabled is False
    assert connector.disabled_reason != ""

    documents = asyncio.run(connector.fetch_documents())
    assert documents == []


def test_obsidian_connector_disabled_when_vault_path_does_not_exist(tmp_path):
    config = AppConfig(obsidian_vault_path=tmp_path / "nonexistent_vault")
    connector = ObsidianSourceConnector(config)

    assert connector.source.enabled is False
    documents = asyncio.run(connector.fetch_documents())
    assert documents == []
