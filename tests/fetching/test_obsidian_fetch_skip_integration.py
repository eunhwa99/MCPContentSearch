"""Integration: Obsidian connector + MetadataStore fetch-before-index skip."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.models import ChunkModel, DocumentModel
from environments.config import AppConfig
from fetching.connectors import ObsidianSourceConnector
from fetching.obsidian import _content_hash, _open_note_from_root_fd
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.integration


def _filesystem_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _stored_obsidian_document(
    relative_path: str,
    *,
    content: str,
    modified_at: str,
) -> DocumentModel:
    return DocumentModel(
        id=relative_path,
        document_id=relative_path,
        external_id=relative_path,
        source_id="source_obsidian",
        title="Stored Obsidian Note",
        content=content,
        url=f"obsidian://open?vault=vault&file={relative_path}",
        canonical_url=f"obsidian://open?vault=vault&file={relative_path}",
        platform="obsidian",
        path=relative_path,
        modified_at=modified_at,
        content_hash=_content_hash(content),
        date_provenance="filesystem",
    )


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


@pytest.mark.integration
def test_obsidian_connector_skips_note_read_for_unchanged_stored_note(
    monkeypatch, tmp_path
):
    note_path = "notes/stored-unchanged.md"
    vault = tmp_path / "vault"
    vault.mkdir()
    note_file = vault / note_path
    note_file.parent.mkdir(parents=True, exist_ok=True)
    note_file.write_text("# Unchanged\n\nfilesystem body", encoding="utf-8")
    stored_content = "already indexed obsidian body"
    modified_at = MetadataStore.canonical_document_timestamp(
        _filesystem_mtime_iso(note_file)
    )
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.upsert_document(
        _stored_obsidian_document(
            note_path, content=stored_content, modified_at=modified_at
        )
    )

    read_calls = _track_note_byte_reads(monkeypatch)
    list_calls: list[object] = []
    get_calls: list[str] = []
    batch_calls: list[list[str]] = []
    original_list = store.list_documents
    original_get = store.get_document
    original_batch = store.get_documents_for_fetch_reuse

    def tracking_list_documents(*args, **kwargs):
        list_calls.append({"args": args, "kwargs": kwargs})
        return original_list(*args, **kwargs)

    def tracking_get_document(document_id):
        get_calls.append(document_id)
        return original_get(document_id)

    def tracking_batch(document_ids):
        batch_calls.append(list(document_ids))
        # Use production batch payload shape (do not substitute get_document).
        return original_batch(document_ids)

    store.list_documents = tracking_list_documents  # type: ignore[method-assign]
    store.get_document = tracking_get_document  # type: ignore[method-assign]
    store.get_documents_for_fetch_reuse = tracking_batch  # type: ignore[method-assign]

    captured: dict[str, object] = {}
    original_fetch = __import__(
        "fetching.obsidian", fromlist=["fetch_obsidian_documents"]
    ).fetch_obsidian_documents

    async def spy_fetch_obsidian_documents(*args, **kwargs):
        captured["existing_documents_loader"] = kwargs.get("existing_documents_loader")
        return await original_fetch(*args, **kwargs)

    monkeypatch.setattr(
        "fetching.connectors.fetch_obsidian_documents",
        spy_fetch_obsidian_documents,
    )

    connector = ObsidianSourceConnector(
        AppConfig(obsidian_vault_path=vault),
        metadata_store=store,
    )
    documents = asyncio.run(connector.fetch_documents())

    assert read_calls == []
    assert len(documents) == 1
    assert documents[0].document_id == note_path
    assert documents[0].content == stored_content
    assert documents[0].title == "Stored Obsidian Note"
    assert documents[0].title != Path(note_path).stem
    assert list_calls == [], "must not browse full corpus via list_documents"
    assert get_calls == [], "hydrate must use batch API, not per-id get_document"
    assert batch_calls == [[note_path]]
    loader = captured.get("existing_documents_loader")
    assert callable(loader)
    loaded = loader([note_path])  # type: ignore[operator]
    assert note_path in loaded
    assert loaded[note_path].content == stored_content
    assert loaded[note_path].title == "Stored Obsidian Note"


@pytest.mark.integration
def test_obsidian_connector_skip_preserves_line_start_from_batch_reuse(
    monkeypatch, tmp_path
):
    """Real batch reuse must recover citation body base from chunk MIN(line_start)."""
    note_path = "notes/frontmatter-skip.md"
    vault = tmp_path / "vault"
    vault.mkdir()
    note_file = vault / note_path
    note_file.parent.mkdir(parents=True, exist_ok=True)
    # On-disk note includes frontmatter; skip path must not re-read bytes.
    note_file.write_text(
        "---\ntitle: Project Atlas\n---\n\n# Architecture\nbody\n",
        encoding="utf-8",
    )
    # Indexed body after frontmatter strip (leading blank before heading).
    stored_content = "\n# Architecture\nbody\n"
    modified_at = MetadataStore.canonical_document_timestamp(
        _filesystem_mtime_iso(note_file)
    )
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.upsert_document(
        DocumentModel(
            id=note_path,
            document_id=note_path,
            external_id=note_path,
            source_id="source_obsidian",
            title="Project Atlas",
            content=stored_content,
            url=f"obsidian://open?vault=vault&file={note_path}",
            canonical_url=f"obsidian://open?vault=vault&file={note_path}",
            platform="obsidian",
            path=note_path,
            modified_at=modified_at,
            content_hash=_content_hash(stored_content),
            date_provenance="filesystem",
        )
    )
    store.replace_document_chunks(
        note_path,
        [
            ChunkModel(
                chunk_id=f"{note_path}:chunk:0:hash",
                document_id=note_path,
                source_id="source_obsidian",
                title="Project Atlas",
                text="# Architecture\nbody",
                url=f"obsidian://open?vault=vault&file={note_path}",
                path=note_path,
                chunk_index=0,
                line_start=5,
                line_end=6,
                content_hash="chunk-hash",
            )
        ],
    )

    read_calls = _track_note_byte_reads(monkeypatch)
    get_calls: list[str] = []
    batch_calls: list[list[str]] = []
    original_get = store.get_document
    original_batch = store.get_documents_for_fetch_reuse

    def tracking_get_document(document_id):
        get_calls.append(document_id)
        return original_get(document_id)

    def tracking_batch(document_ids):
        batch_calls.append(list(document_ids))
        # Use production batch payload shape (do not substitute get_document).
        return original_batch(document_ids)

    store.get_document = tracking_get_document  # type: ignore[method-assign]
    store.get_documents_for_fetch_reuse = tracking_batch  # type: ignore[method-assign]

    connector = ObsidianSourceConnector(
        AppConfig(obsidian_vault_path=vault),
        metadata_store=store,
    )
    documents = asyncio.run(connector.fetch_documents())

    assert read_calls == []
    assert get_calls == [], "hydrate must use batch API, not per-id get_document"
    assert batch_calls == [[note_path]]
    assert len(documents) == 1
    assert documents[0].document_id == note_path
    assert documents[0].title == "Project Atlas"
    assert documents[0].content == stored_content
    # MIN(chunk.line_start)=5 minus one leading blank → body base 4 (frontmatter)
    assert documents[0].line_start == 4


@pytest.mark.integration
def test_obsidian_connector_reads_when_stored_modified_at_differs(
    monkeypatch, tmp_path
):
    note_path = "notes/stored-changed.md"
    vault = tmp_path / "vault"
    vault.mkdir()
    note_file = vault / note_path
    note_file.parent.mkdir(parents=True, exist_ok=True)
    note_file.write_text("fresh body for changed note", encoding="utf-8")
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.upsert_document(
        _stored_obsidian_document(
            note_path,
            content="old body",
            modified_at="2020-01-01T00:00:00Z",
        )
    )
    read_calls = _track_note_byte_reads(monkeypatch)

    connector = ObsidianSourceConnector(
        AppConfig(obsidian_vault_path=vault),
        metadata_store=store,
    )
    documents = asyncio.run(connector.fetch_documents())

    assert read_calls == [note_path]
    assert documents[0].content == "fresh body for changed note"


@pytest.mark.integration
def test_build_ingestion_runtime_wires_metadata_store_onto_obsidian_connector(
    tmp_path,
):
    from app_runtime import build_ingestion_runtime

    vault = tmp_path / "vault"
    vault.mkdir()
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "contextwiki.sqlite3",
        cache_dir=str(tmp_path / "cache"),
        github_repositories=(),
        obsidian_vault_path=vault,
    )

    class FakeCollection:
        pass

    class FakeVectorStore:
        def __init__(self, chroma_collection):
            self.chroma_collection = chroma_collection

    class FakeStorageContext:
        @staticmethod
        def from_defaults(vector_store):
            return {"vector_store": vector_store}

    class FakeIndexer:
        def __init__(self, config, chroma_collection, storage_context):
            self.config = config

    runtime = build_ingestion_runtime(
        config=config,
        notion_api_key="",
        tistory_blog_name="",
        github_token="",
        setup_chroma_fn=lambda _config: FakeCollection(),
        vector_store_cls=FakeVectorStore,
        storage_context_cls=FakeStorageContext,
        indexer_cls=FakeIndexer,
    )
    connector = runtime.source_registry.get_connector("source_obsidian")

    assert isinstance(connector, ObsidianSourceConnector)
    assert connector.metadata_store is runtime.metadata_store
