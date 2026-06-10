import asyncio
from pathlib import Path
import shutil

import pytest

from core.models import SyncJobStatus, SyncStatus
from environments.config import AppConfig
from fetching import obsidian as obsidian_module
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


class HookedRecordingIndexer(RecordingIndexer):
    def __init__(self, on_index=None):
        super().__init__()
        self.on_index = on_index

    async def index_documents(self, documents):
        await super().index_documents(documents)
        if self.on_index is not None:
            callback = self.on_index
            self.on_index = None
            callback()


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
        "notes/untitled.md": "---\ntitle: My Custom Title\n---\n\nBody content here.",
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
    document = store.get_document("notes/untitled.md")
    chunks = store.list_chunks_for_document("notes/untitled.md")
    assert document is not None
    assert chunks
    assert document.content == "\nBody content here."
    assert chunks[0].title == "My Custom Title"
    assert "---" not in chunks[0].text
    assert chunks[0].text.strip() == "Body content here."
    assert chunks[0].line_start == 5


def test_obsidian_connector_strips_crlf_frontmatter_separator_blank_line(tmp_path):
    vault = _make_vault(tmp_path, {
        "notes/crlf.md": "---\r\ntitle: CRLF Title\r\n---\r\n\r\nBody content here.\r\n",
    })
    config = AppConfig(obsidian_vault_path=vault)
    connector = ObsidianSourceConnector(config)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    job = asyncio.run(service.sync_source("source_obsidian"))

    assert job.status == SyncJobStatus.SUCCEEDED
    document = store.get_document("notes/crlf.md")
    chunks = store.list_chunks_for_document("notes/crlf.md")
    assert document is not None
    assert chunks
    assert document.content == "\nBody content here.\n"
    assert chunks[0].line_start == 5


def test_obsidian_connector_strips_whitespace_only_frontmatter_separator_line(tmp_path):
    vault = _make_vault(tmp_path, {
        "notes/spaces.md": "---\ntitle: Space Title\n---\n\n   \nBody content here.\n",
    })
    config = AppConfig(obsidian_vault_path=vault)
    connector = ObsidianSourceConnector(config)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    job = asyncio.run(service.sync_source("source_obsidian"))

    assert job.status == SyncJobStatus.SUCCEEDED
    document = store.get_document("notes/spaces.md")
    chunks = store.list_chunks_for_document("notes/spaces.md")
    assert document is not None
    assert chunks
    assert document.content == "\n   \nBody content here.\n"
    assert chunks[0].line_start == 6


def test_obsidian_connector_supports_empty_frontmatter_block(tmp_path):
    vault = _make_vault(tmp_path, {
        "notes/empty-frontmatter.md": "---\n---\nBody content here.\n",
    })
    config = AppConfig(obsidian_vault_path=vault)
    connector = ObsidianSourceConnector(config)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    job = asyncio.run(service.sync_source("source_obsidian"))

    assert job.status == SyncJobStatus.SUCCEEDED
    document = store.get_document("notes/empty-frontmatter.md")
    chunks = store.list_chunks_for_document("notes/empty-frontmatter.md")
    assert document is not None
    assert chunks
    assert document.content == "Body content here.\n"
    assert chunks[0].line_start == 3


def test_obsidian_connector_supports_comment_only_frontmatter_block(tmp_path):
    vault = _make_vault(tmp_path, {
        "notes/comment-frontmatter.md": "---\n# comment\n---\nBody content here.\n",
    })
    config = AppConfig(obsidian_vault_path=vault)
    connector = ObsidianSourceConnector(config)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    job = asyncio.run(service.sync_source("source_obsidian"))

    assert job.status == SyncJobStatus.SUCCEEDED
    document = store.get_document("notes/comment-frontmatter.md")
    chunks = store.list_chunks_for_document("notes/comment-frontmatter.md")
    assert document is not None
    assert chunks
    assert document.content == "Body content here.\n"
    assert chunks[0].line_start == 4


def test_obsidian_connector_normalizes_quoted_and_commented_frontmatter_titles(tmp_path):
    vault = _make_vault(tmp_path, {
        "notes/quoted.md": "---\ntitle: \"Quoted Title\"\n---\nQuoted body.",
        "notes/commented.md": "---\ntitle: My Title # comment\n---\nCommented body.",
        "notes/escaped-quote.md": "---\ntitle: \"Alice \\\"A\\\" Bob\"\n---\nEscaped quote body.",
        "notes/doubled-quote.md": "---\ntitle: 'Alice ''A'' Bob'\n---\nSingle quote body.",
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
    quoted_chunks = store.list_chunks_for_document("notes/quoted.md")
    commented_chunks = store.list_chunks_for_document("notes/commented.md")
    escaped_quote_chunks = store.list_chunks_for_document("notes/escaped-quote.md")
    doubled_quote_chunks = store.list_chunks_for_document("notes/doubled-quote.md")
    assert quoted_chunks[0].title == "Quoted Title"
    assert commented_chunks[0].title == "My Title"
    assert escaped_quote_chunks[0].title == 'Alice "A" Bob'
    assert doubled_quote_chunks[0].title == "Alice 'A' Bob"


def test_obsidian_connector_preserves_original_body_line_numbers_after_frontmatter(tmp_path):
    vault = _make_vault(tmp_path, {
        "notes/offset.md": "---\ntitle: Offset Title\ncategory: notes\n---\n# Heading\nBody line\n",
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
    chunks = store.list_chunks_for_document("notes/offset.md")
    assert chunks
    assert chunks[0].line_start == 5
    assert chunks[0].line_end == 6
    assert chunks[0].text.startswith("# Heading")


def test_obsidian_connector_preserves_line_numbers_for_plain_text_body_after_frontmatter(tmp_path):
    vault = _make_vault(tmp_path, {
        "notes/plain-offset.md": "---\ntitle: Plain Offset\ncategory: notes\n---\nFirst line\nSecond line\n",
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
    chunks = store.list_chunks_for_document("notes/plain-offset.md")
    assert chunks
    assert chunks[0].line_start == 5
    assert chunks[0].line_end == 6
    assert chunks[0].text == "First line\nSecond line"


def test_obsidian_connector_preserves_non_frontmatter_delimited_prefix(tmp_path):
    vault = _make_vault(tmp_path, {
        "notes/not-frontmatter.md": "---\ntitle: [unterminated\n---\nReal body\n",
    })
    config = AppConfig(obsidian_vault_path=vault)
    connector = ObsidianSourceConnector(config)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=400, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    job = asyncio.run(service.sync_source("source_obsidian"))

    assert job.status == SyncJobStatus.SUCCEEDED
    document = store.get_document("notes/not-frontmatter.md")
    chunks = store.list_chunks_for_document("notes/not-frontmatter.md")
    assert document is not None
    assert chunks
    assert document.content == "---\ntitle: [unterminated\n---\nReal body\n"
    assert chunks[0].line_start == 1


def test_obsidian_connector_does_not_index_frontmatter_only_note_body(tmp_path):
    vault = _make_vault(tmp_path, {
        "notes/frontmatter-only.md": "---\ntitle: Metadata Only\nstatus: draft\n---\n",
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
    document = store.get_document("notes/frontmatter-only.md")
    assert document is not None
    assert document.content == ""
    assert store.list_chunks_for_document("notes/frontmatter-only.md") == []


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


def test_obsidian_stale_cleanup_uses_per_sync_snapshot_decision(tmp_path):
    vault = _make_vault(tmp_path, {
        "keep.md": "This note stays.",
        "delete_me.md": "This note will be deleted.",
    })
    config = AppConfig(obsidian_vault_path=vault)

    store = MetadataStore(tmp_path / "meta.sqlite3")
    first_indexer = RecordingIndexer()
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=first_indexer,
    )
    first_job = asyncio.run(first_service.sync_source("source_obsidian"))
    assert first_job.status == SyncJobStatus.SUCCEEDED

    (vault / "delete_me.md").unlink()
    (vault / "keep.md").write_text("This note stays, but changed.", encoding="utf-8")

    second_service = None

    def simulate_read_only_refresh_after_fetch():
        shutil.rmtree(vault)
        second_service.refresh_registered_sources()

    second_indexer = HookedRecordingIndexer(on_index=simulate_read_only_refresh_after_fetch)
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=second_indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_obsidian"))
    refreshed_source = store.get_source("source_obsidian")

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert refreshed_source is not None
    assert refreshed_source.enabled is True
    assert refreshed_source.last_error == ""
    assert store.get_document("delete_me.md").deleted_at != ""
    assert any(d.startswith("delete_me.md") for d in second_indexer.deleted_ids)


def test_obsidian_sync_skips_stale_cleanup_when_note_becomes_unreadable(tmp_path):
    vault = _make_vault(tmp_path, {
        "keep.md": "This note stays.",
        "unstable.md": "This note becomes unreadable.",
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
    assert store.list_chunks_for_document("unstable.md")

    (vault / "unstable.md").write_bytes(b"\xff\xfe\x00bad-utf8")

    second_connector = ObsidianSourceConnector(config)
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_obsidian"))

    assert second_job.status == SyncJobStatus.FAILED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("unstable.md").deleted_at == ""
    assert store.list_chunks_for_document("unstable.md")
    assert indexer.deleted_ids == []
    assert "snapshot was incomplete" in second_job.error_message


def test_obsidian_refresh_registered_sources_preserves_incomplete_snapshot_error_until_successful_sync(
    tmp_path,
):
    vault = _make_vault(tmp_path, {
        "keep.md": "This note stays.",
        "unstable.md": "This note becomes unreadable.",
    })
    config = AppConfig(obsidian_vault_path=vault)

    store = MetadataStore(tmp_path / "meta.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    first_job = asyncio.run(service.sync_source("source_obsidian"))
    assert first_job.status == SyncJobStatus.SUCCEEDED

    (vault / "unstable.md").write_bytes(b"\xff\xfe\x00bad-utf8")
    failed_job = asyncio.run(service.sync_source("source_obsidian"))
    failed_source = store.get_source("source_obsidian")
    assert failed_job.status == SyncJobStatus.FAILED
    assert failed_source is not None
    assert failed_source.enabled is True
    assert failed_source.last_error == (
        "Obsidian vault snapshot was incomplete because one or more notes could not be read."
    )

    (vault / "unstable.md").write_text("Recovered note.", encoding="utf-8")
    service.refresh_registered_sources()

    refreshed_source = store.get_source("source_obsidian")
    assert refreshed_source is not None
    assert refreshed_source.enabled is True
    assert refreshed_source.last_error == (
        "Obsidian vault snapshot was incomplete because one or more notes could not be read."
    )

    shutil.rmtree(vault)
    service.refresh_registered_sources()

    disabled_source = store.get_source("source_obsidian")
    assert disabled_source is not None
    assert disabled_source.enabled is False
    assert disabled_source.last_error == (
        "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
        "is not set or is not an existing directory."
    )

    vault.mkdir()
    (vault / ".obsidian").mkdir()
    (vault / "keep.md").write_text("This note stays.", encoding="utf-8")
    (vault / "unstable.md").write_text("Recovered note.", encoding="utf-8")
    recovered_job = asyncio.run(service.sync_source("source_obsidian"))
    recovered_source = store.get_source("source_obsidian")
    assert recovered_job.status == SyncJobStatus.SUCCEEDED
    assert recovered_source is not None
    assert recovered_source.enabled is True
    assert recovered_source.last_error == ""


def test_obsidian_sync_skips_out_of_root_symlink_note(tmp_path):
    vault = _make_vault(tmp_path, {
        "keep.md": "This note stays.",
    })
    outside = tmp_path / "outside.md"
    outside.write_text("External note content.", encoding="utf-8")
    (vault / "linked.md").symlink_to(outside)

    config = AppConfig(obsidian_vault_path=vault)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(service.sync_source("source_obsidian"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("keep.md") is not None
    assert store.get_document("linked.md") is None


def test_obsidian_sync_skips_in_vault_symlink_note(tmp_path):
    vault = _make_vault(tmp_path, {
        "keep.md": "This note stays.",
        "notes/real.md": "Real note content.",
    })
    (vault / "notes" / "alias.md").symlink_to(vault / "notes" / "real.md")

    config = AppConfig(obsidian_vault_path=vault)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=indexer,
    )

    job = asyncio.run(service.sync_source("source_obsidian"))

    assert job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("keep.md") is not None
    assert store.get_document("notes/real.md") is not None
    assert store.get_document("notes/alias.md") is None


def test_obsidian_sync_skips_note_that_becomes_symlink_after_walk(monkeypatch, tmp_path):
    vault = _make_vault(tmp_path, {
        "keep.md": "This note stays.",
        "race.md": "Original race note.",
    })
    outside = tmp_path / "outside.md"
    outside.write_text("External note content.", encoding="utf-8")

    config = AppConfig(obsidian_vault_path=vault)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    first_job = asyncio.run(service.sync_source("source_obsidian"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("race.md") is not None

    original_open = obsidian_module.os.open
    race_swapped = False

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal race_swapped
        if path == "race.md" and dir_fd is not None and not race_swapped:
            race_swapped = True
            note_path = vault / "race.md"
            note_path.unlink()
            note_path.symlink_to(outside)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(obsidian_module.os, "open", racing_open)

    job = asyncio.run(service.sync_source("source_obsidian"))

    assert job.status == SyncJobStatus.FAILED
    assert "snapshot was incomplete" in job.error_message
    assert store.get_document("keep.md") is not None
    assert store.get_document("race.md") is not None


def test_obsidian_sync_fails_when_parent_directory_becomes_symlink_before_open(
    monkeypatch,
    tmp_path,
):
    vault = _make_vault(tmp_path, {
        "notes/race.md": "Original race note.",
    })
    outside_dir = tmp_path / "outside-notes"
    outside_dir.mkdir()
    (outside_dir / "race.md").write_text("External note content.", encoding="utf-8")

    config = AppConfig(obsidian_vault_path=vault)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    first_job = asyncio.run(service.sync_source("source_obsidian"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("notes/race.md") is not None

    original_open = obsidian_module.os.open
    parent_swapped = False

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal parent_swapped
        if path == "notes" and dir_fd is not None and not parent_swapped:
            parent_swapped = True
            shutil.rmtree(vault / "notes")
            (vault / "notes").symlink_to(outside_dir, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(obsidian_module.os, "open", racing_open)

    job = asyncio.run(service.sync_source("source_obsidian"))

    assert job.status == SyncJobStatus.FAILED
    assert "snapshot was incomplete" in job.error_message
    assert store.get_document("notes/race.md") is not None


def test_obsidian_sync_disables_symlinked_vault_root(tmp_path):
    real_vault = _make_vault(tmp_path, {
        "note.md": "Real note content.",
    })
    symlink_vault = tmp_path / "vault-link"
    symlink_vault.symlink_to(real_vault, target_is_directory=True)

    config = AppConfig(obsidian_vault_path=symlink_vault)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    job = asyncio.run(service.sync_source("source_obsidian"))
    source = store.get_source("source_obsidian")

    assert job.status == SyncJobStatus.FAILED
    assert "must not be a symlink" in job.error_message
    assert source is not None
    assert source.enabled is False
    assert source.last_error == (
        "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
        "must not be a symlink."
    )
    assert store.get_document("note.md") is None


def test_obsidian_sync_skips_symlinked_directory(tmp_path):
    vault = _make_vault(tmp_path, {
        "keep.md": "This note stays.",
        "real-dir/note.md": "Nested real note.",
    })
    (vault / "alias-dir").symlink_to(vault / "real-dir", target_is_directory=True)

    config = AppConfig(obsidian_vault_path=vault)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    job = asyncio.run(service.sync_source("source_obsidian"))

    assert job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("keep.md") is not None
    assert store.get_document("real-dir/note.md") is not None
    assert store.get_document("alias-dir/note.md") is None


def test_obsidian_sync_fails_safely_when_vault_disappears_after_connector_setup(tmp_path):
    vault = _make_vault(tmp_path, {
        "keep.md": "This note stays.",
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
    assert store.get_document("keep.md").deleted_at == ""

    second_connector = ObsidianSourceConnector(config)
    shutil.rmtree(vault)
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_obsidian"))

    assert second_job.status == SyncJobStatus.FAILED
    assert second_connector.source.enabled is False
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("keep.md").deleted_at == ""
    assert store.list_chunks_for_document("keep.md")
    assert "source_obsidian is disabled" in second_job.error_message


def test_obsidian_sync_redacts_vault_path_when_vault_disappears_during_walk(
    monkeypatch,
    tmp_path,
):
    vault = _make_vault(tmp_path, {
        "keep.md": "This note stays.",
    })
    config = AppConfig(obsidian_vault_path=vault)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    def disappear_during_resolution(_vault_path):
        shutil.rmtree(vault)
        raise FileNotFoundError(str(vault))

    monkeypatch.setattr(obsidian_module, "_resolved_vault_path", disappear_during_resolution)

    job = asyncio.run(service.sync_source("source_obsidian"))

    assert job.status == SyncJobStatus.FAILED
    assert job.error_message == (
        "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
        "is not set or is not an existing directory."
    )
    assert str(vault) not in job.error_message


def test_obsidian_refresh_registered_sources_marks_vault_unavailable(tmp_path):
    vault = _make_vault(tmp_path, {
        "keep.md": "This note stays.",
    })
    config = AppConfig(obsidian_vault_path=vault)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    assert store.get_source("source_obsidian").enabled is True
    shutil.rmtree(vault)

    service.refresh_registered_sources()

    refreshed = store.get_source("source_obsidian")
    assert refreshed is not None
    assert refreshed.enabled is False


def test_obsidian_refresh_registered_sources_marks_unreadable_vault_unavailable(
    monkeypatch,
    tmp_path,
):
    vault = _make_vault(tmp_path, {
        "keep.md": "This note stays.",
    })
    config = AppConfig(obsidian_vault_path=vault)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=RecordingIndexer(),
    )
    original_access = obsidian_module.os.access

    def fake_access(path, mode):
        if Path(path) == vault:
            return False
        return original_access(path, mode)

    monkeypatch.setattr(obsidian_module.os, "access", fake_access)
    service.refresh_registered_sources()

    refreshed = store.get_source("source_obsidian")
    assert refreshed is not None
    assert refreshed.enabled is False
    assert refreshed.last_error == (
        "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
        "is not set or is not an existing directory."
    )


def test_obsidian_refresh_registered_sources_clears_stale_disabled_error_after_mid_sync_failure(
    tmp_path,
):
    class RacingObsidianConnector(ObsidianSourceConnector):
        def __init__(self, config, vault_to_remove):
            self._refresh_calls = 0
            self._vault_to_remove = vault_to_remove
            super().__init__(config)

        def refresh_source_state(self) -> None:
            self._refresh_calls += 1
            if self._refresh_calls == 4 and self._vault_to_remove.exists():
                shutil.rmtree(self._vault_to_remove)
            super().refresh_source_state()

    vault = _make_vault(tmp_path, {
        "keep.md": "This note stays.",
    })
    config = AppConfig(obsidian_vault_path=vault)
    store = MetadataStore(tmp_path / "meta.sqlite3")
    connector = RacingObsidianConnector(config, vault)
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    initial_source = store.get_source("source_obsidian")
    assert initial_source is not None
    assert initial_source.enabled is True

    failed_job = asyncio.run(service.sync_source("source_obsidian"))
    failed_source = store.get_source("source_obsidian")
    assert failed_job.status == SyncJobStatus.FAILED
    assert failed_source is not None
    assert failed_source.enabled is True
    assert "source_obsidian is disabled" in failed_source.last_error

    vault.mkdir()
    (vault / ".obsidian").mkdir()
    service.refresh_registered_sources()

    refreshed_source = store.get_source("source_obsidian")
    assert refreshed_source is not None
    assert refreshed_source.enabled is True
    assert refreshed_source.last_error == ""


def test_obsidian_frontmatter_only_churn_does_not_change_content_hash(tmp_path):
    vault = _make_vault(tmp_path, {
        "notes/hash-stable.md": "---\ntitle: Stable Title\nstatus: draft\n---\nBody text\n",
    })
    config = AppConfig(obsidian_vault_path=vault)

    store = MetadataStore(tmp_path / "meta.sqlite3")
    indexer = RecordingIndexer()
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_obsidian"))
    first_document = store.get_document("notes/hash-stable.md")

    (vault / "notes/hash-stable.md").write_text(
        "---\ntitle: Stable Title\nstatus: published\nauthor: test\n---\nBody text\n",
        encoding="utf-8",
    )

    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=indexer,
    )
    second_job = asyncio.run(second_service.sync_source("source_obsidian"))
    second_document = store.get_document("notes/hash-stable.md")

    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert first_document is not None
    assert second_document is not None
    assert second_document.content_hash == first_document.content_hash


def test_obsidian_leading_blank_line_churn_changes_content_hash_without_frontmatter(tmp_path):
    vault = _make_vault(tmp_path, {
        "notes/leading-blank.md": "Body text\n",
    })
    config = AppConfig(obsidian_vault_path=vault)

    store = MetadataStore(tmp_path / "meta.sqlite3")
    indexer = RecordingIndexer()
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_obsidian"))
    first_document = store.get_document("notes/leading-blank.md")

    (vault / "notes/leading-blank.md").write_text("\nBody text\n", encoding="utf-8")

    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ObsidianSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=indexer,
    )
    second_job = asyncio.run(second_service.sync_source("source_obsidian"))
    second_document = store.get_document("notes/leading-blank.md")

    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert first_document is not None
    assert second_document is not None
    assert second_document.content_hash != first_document.content_hash


def test_obsidian_connector_disabled_when_vault_path_not_set(tmp_path):
    config = AppConfig(obsidian_vault_path=None)
    connector = ObsidianSourceConnector(config)

    assert connector.source.enabled is False
    assert connector.disabled_reason != ""
    with pytest.raises(FileNotFoundError, match="source_obsidian is disabled"):
        asyncio.run(connector.fetch_documents())


def test_obsidian_connector_disabled_when_vault_path_does_not_exist(tmp_path):
    config = AppConfig(obsidian_vault_path=tmp_path / "nonexistent_vault")
    connector = ObsidianSourceConnector(config)

    assert connector.source.enabled is False
    with pytest.raises(FileNotFoundError, match="source_obsidian is disabled"):
        asyncio.run(connector.fetch_documents())
