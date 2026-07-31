"""Deterministic E2E: unchanged Obsidian notes skip byte read on second sync."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.models import SyncJobStatus
from environments.config import AppConfig
from fetching.connectors import ObsidianSourceConnector, SourceRegistry
from fetching.obsidian import _open_note_from_root_fd
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
    vault = tmp_path / "vault"
    vault.mkdir()
    for rel_path, content in files.items():
        target = vault / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return vault


def _track_note_byte_reads(monkeypatch) -> list[str]:
    read_calls: list[str] = []
    original = _open_note_from_root_fd

    def tracking(root_fd, relative_path, *, max_file_bytes=None):
        read_calls.append(Path(relative_path).as_posix())
        return original(root_fd, relative_path, max_file_bytes=max_file_bytes)

    monkeypatch.setattr(
        "fetching.obsidian._open_note_from_root_fd",
        tracking,
    )
    return read_calls


def test_second_obsidian_sync_skips_note_byte_read_for_unchanged_notes(
    monkeypatch, tmp_path
):
    note_path = "notes/unchanged.md"
    note_body = "Deterministic Obsidian body used for fetch-skip E2E coverage.\n"
    vault = _make_vault(tmp_path, {note_path: note_body})
    read_calls = _track_note_byte_reads(monkeypatch)

    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    connector = ObsidianSourceConnector(
        AppConfig(
            chroma_db_path=tmp_path / "chroma",
            metadata_db_path=tmp_path / "contextwiki.sqlite3",
            cache_dir=str(tmp_path / "cache"),
            obsidian_vault_path=vault,
        ),
        metadata_store=store,
    )
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    first_job = asyncio.run(ingestion.sync_source("source_obsidian"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert read_calls == [note_path]
    assert store.get_document(note_path) is not None
    assert store.get_document(note_path).content == note_body
    assert store.get_document(note_path).modified_at

    read_calls.clear()
    second_job = asyncio.run(ingestion.sync_source("source_obsidian"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert read_calls == []
    assert store.get_document(note_path).content == note_body


def test_second_obsidian_sync_skip_preserves_frontmatter_title_and_line_start(
    monkeypatch, tmp_path
):
    """Real batch reuse must keep frontmatter title and citation line_start."""
    note_path = "notes/project.md"
    frontmatter_title = "Project Atlas"
    note_file = (
        "---\n"
        f"title: {frontmatter_title}\n"
        "---\n\n"
        "# Architecture\n"
        "Obsidian fetch-skip preserves citation bases across unchanged sync.\n"
    )
    indexed_body = (
        "\n# Architecture\n"
        "Obsidian fetch-skip preserves citation bases across unchanged sync.\n"
    )
    vault = _make_vault(tmp_path, {note_path: note_file})
    read_calls = _track_note_byte_reads(monkeypatch)

    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    connector = ObsidianSourceConnector(
        AppConfig(
            chroma_db_path=tmp_path / "chroma",
            metadata_db_path=tmp_path / "contextwiki.sqlite3",
            cache_dir=str(tmp_path / "cache"),
            obsidian_vault_path=vault,
        ),
        metadata_store=store,
    )
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    first_job = asyncio.run(ingestion.sync_source("source_obsidian"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert read_calls == [note_path]
    project = store.get_document(note_path)
    assert project is not None
    assert project.title == frontmatter_title
    assert project.content == indexed_body
    first_chunks = store.list_chunks_for_document(note_path)
    assert first_chunks
    assert first_chunks[0].title == frontmatter_title
    assert first_chunks[0].line_start == 5

    read_calls.clear()
    second_job = asyncio.run(ingestion.sync_source("source_obsidian"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert read_calls == [], "unchanged frontmatter note must skip byte read"
    project_after = store.get_document(note_path)
    assert project_after is not None
    assert project_after.title == frontmatter_title
    assert project_after.title != Path(note_path).stem
    assert project_after.content == indexed_body
    second_chunks = store.list_chunks_for_document(note_path)
    assert second_chunks
    assert second_chunks[0].title == frontmatter_title
    assert second_chunks[0].line_start == 5, (
        "citation line_start must remain body base after skipped second sync"
    )


def test_skipped_unchanged_obsidian_note_is_not_tombstoned_when_peer_disappears(
    monkeypatch, tmp_path
):
    kept_path = "notes/kept.md"
    removed_path = "notes/removed.md"
    kept_body = "Kept body reused on second sync.\n"
    removed_body = "Removed peer body.\n"
    vault = _make_vault(
        tmp_path,
        {
            kept_path: kept_body,
            removed_path: removed_body,
        },
    )
    read_calls = _track_note_byte_reads(monkeypatch)

    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    connector = ObsidianSourceConnector(
        AppConfig(obsidian_vault_path=vault),
        metadata_store=store,
    )
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    first_job = asyncio.run(ingestion.sync_source("source_obsidian"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert sorted(read_calls) == sorted([kept_path, removed_path])
    assert store.get_document(kept_path).deleted_at == ""
    assert store.get_document(removed_path).deleted_at == ""

    read_calls.clear()
    (vault / removed_path).unlink()
    second_job = asyncio.run(ingestion.sync_source("source_obsidian"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert read_calls == [], "kept note must skip byte read"
    kept = store.get_document(kept_path)
    removed = store.get_document(removed_path)
    assert kept is not None
    assert kept.deleted_at == ""
    assert kept.last_seen_at
    assert kept.last_seen_sync_id == second_job.job_id
    assert kept.content == kept_body
    assert removed is not None
    assert removed.deleted_at
