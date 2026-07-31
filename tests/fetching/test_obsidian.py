import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.models import DocumentModel
from environments.config import AppConfig
from fetching.connectors import ObsidianSourceConnector
from fetching.obsidian import (
    _content_hash,
    _emit_progress,
    _open_note_from_root_fd,
    fetch_obsidian_documents,
)
from fetching.notion import _StopRequested
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.unit


def _make_vault(tmp_path, notes: dict[str, str]):
    vault = tmp_path / "vault"
    vault.mkdir()
    for rel_path, content in notes.items():
        target = vault / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return vault


def _filesystem_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _existing_obsidian_document(
    relative_path: str,
    *,
    content: str,
    modified_at: str,
    deleted_at: str = "",
    content_hash: str | None = None,
    title: str = "Stored Note",
) -> DocumentModel:
    resolved_hash = content_hash
    if resolved_hash is None:
        resolved_hash = _content_hash(content) if content else ""
    return DocumentModel(
        id=relative_path,
        document_id=relative_path,
        external_id=relative_path,
        source_id="source_obsidian",
        title=title,
        content=content,
        url=f"obsidian://open?vault=vault&file={relative_path}",
        canonical_url=f"obsidian://open?vault=vault&file={relative_path}",
        platform="obsidian",
        path=relative_path,
        modified_at=modified_at,
        updated_at=modified_at,
        deleted_at=deleted_at,
        content_hash=resolved_hash,
        date_provenance="filesystem",
    )


def _progress_advanced_for_page(events, *, current_page: int, total_pages: int) -> bool:
    for event in events:
        if event.get("current_page") != current_page:
            continue
        if event.get("total_pages") != total_pages:
            continue
        if event.get("event") == "page_fetch_skipped":
            return True
        if event.get("event") == "page_fetch_completed" and event.get("skipped") is True:
            return True
    return False


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


def test_fetch_obsidian_documents_emits_list_total_and_per_item_upstream_progress(
    tmp_path,
):
    vault = _make_vault(
        tmp_path,
        {
            "a.md": "# A\n\nalpha",
            "nested/b.md": "# B\n\nbeta",
        },
    )
    events = []

    async def capture(event):
        events.append(event)

    snapshot = asyncio.run(
        fetch_obsidian_documents(vault, progress_callback=capture)
    )

    assert len(snapshot.documents) == 2
    list_ready = [event for event in events if event.get("event") == "search_completed"]
    assert list_ready, "expected list-total progress after Obsidian walk"
    assert list_ready[0]["total_pages"] == 2
    item_done = [
        event for event in events if event.get("event") == "page_fetch_completed"
    ]
    assert len(item_done) == 2
    assert item_done[0]["current_page"] == 1
    assert item_done[-1]["current_page"] == 2
    assert item_done[-1]["total_pages"] == 2


def test_obsidian_connector_emits_list_total_and_per_item_upstream_progress(tmp_path):
    vault = _make_vault(
        tmp_path,
        {
            "note.md": "# Note\n\nbody",
        },
    )
    events = []

    async def capture(event):
        events.append(event)

    connector = ObsidianSourceConnector(
        AppConfig(obsidian_vault_path=vault)
    )
    assert hasattr(connector, "progress_callback")
    connector.progress_callback = capture

    documents = asyncio.run(connector.fetch_documents())

    assert len(documents) == 1
    assert any(event.get("event") == "search_completed" for event in events)
    assert any(event.get("event") == "page_fetch_completed" for event in events)
    assert events[-1]["current_page"] == 1
    assert events[-1]["total_pages"] == 1


def test_obsidian_emit_progress_reraises_inactive_job_stop():
    class _InactiveJobStop(Exception):
        pass

    async def boom(_event):
        raise _InactiveJobStop("job inactive")

    with pytest.raises(_InactiveJobStop):
        asyncio.run(_emit_progress(boom, {"event": "page_fetch_completed"}))


def test_obsidian_emit_progress_returns_stop_signal():
    stop_signal = object()

    async def request_stop(_event):
        return stop_signal

    assert (
        asyncio.run(
            _emit_progress(
                request_stop,
                {"event": "search_completed"},
                stop_signal=stop_signal,
            )
        )
        is True
    )


def test_obsidian_fetch_aborts_when_progress_stop_signal_returned(tmp_path):
    vault = _make_vault(
        tmp_path,
        {
            "a.md": "# A\n\nalpha",
            "b.md": "# B\n\nbeta",
        },
    )
    stop_signal = object()
    events = []

    async def stop_after_list(event):
        events.append(event)
        if event.get("event") == "search_completed":
            return stop_signal
        return None

    with pytest.raises(_StopRequested):
        asyncio.run(
            fetch_obsidian_documents(
                vault,
                progress_callback=stop_after_list,
                progress_stop_signal=stop_signal,
            )
        )

    assert any(event.get("event") == "search_completed" for event in events)
    assert not any(event.get("event") == "page_fetch_completed" for event in events)


def test_fetch_obsidian_skip_unchanged_note_reuses_stored_content(monkeypatch, tmp_path):
    note_path = "notes/unchanged.md"
    stored_body = "stored body for unchanged note"
    vault = _make_vault(tmp_path, {note_path: "# Unchanged\n\nfilesystem body ignored"})
    modified_at = MetadataStore.canonical_document_timestamp(
        _filesystem_mtime_iso(vault / note_path)
    )
    existing = {
        note_path: _existing_obsidian_document(
            note_path,
            content=stored_body,
            modified_at=modified_at,
            title="Unchanged Stored",
        )
    }
    read_calls = _track_note_byte_reads(monkeypatch)
    events = []

    async def capture(event):
        events.append(event)

    snapshot = asyncio.run(
        fetch_obsidian_documents(
            vault,
            progress_callback=capture,
            existing_documents=existing,
        )
    )

    assert read_calls == []
    assert len(snapshot.documents) == 1
    assert snapshot.documents[0].document_id == note_path
    assert snapshot.documents[0].content == stored_body
    assert snapshot.documents[0].content_hash == existing[note_path].content_hash
    assert snapshot.snapshot_complete is True
    assert _progress_advanced_for_page(events, current_page=1, total_pages=1)


def test_fetch_obsidian_skip_unchanged_uses_canonical_modified_at(
    monkeypatch, tmp_path
):
    note_path = "canonical.md"
    vault = _make_vault(tmp_path, {note_path: "body"})
    # Filesystem isoformat uses +00:00; stored row may already be Z-canonical.
    fs_iso = _filesystem_mtime_iso(vault / note_path)
    stored_modified = MetadataStore.canonical_document_timestamp(fs_iso)
    assert stored_modified.endswith("Z")
    existing = {
        note_path: _existing_obsidian_document(
            note_path,
            content="reuse me",
            modified_at=stored_modified,
        )
    }
    read_calls = _track_note_byte_reads(monkeypatch)

    snapshot = asyncio.run(
        fetch_obsidian_documents(vault, existing_documents=existing)
    )

    assert read_calls == []
    assert snapshot.documents[0].content == "reuse me"


def test_fetch_obsidian_fetch_skip_still_reads_when_mtime_differs(monkeypatch, tmp_path):
    note_path = "changed.md"
    vault = _make_vault(tmp_path, {note_path: "fresh filesystem body"})
    existing = {
        note_path: _existing_obsidian_document(
            note_path,
            content="stale stored body",
            modified_at="2020-01-01T00:00:00Z",
        )
    }
    read_calls = _track_note_byte_reads(monkeypatch)

    snapshot = asyncio.run(
        fetch_obsidian_documents(vault, existing_documents=existing)
    )

    assert read_calls == [note_path]
    assert snapshot.documents[0].content == "fresh filesystem body"


def test_fetch_obsidian_fetch_skip_still_reads_when_existing_missing(monkeypatch, tmp_path):
    note_path = "missing.md"
    vault = _make_vault(tmp_path, {note_path: "new note body"})
    read_calls = _track_note_byte_reads(monkeypatch)

    snapshot = asyncio.run(
        fetch_obsidian_documents(vault, existing_documents={})
    )

    assert read_calls == [note_path]
    assert snapshot.documents[0].content == "new note body"


def test_fetch_obsidian_fetch_skip_still_reads_when_tombstoned(
    monkeypatch, tmp_path
):
    note_path = "tombstoned.md"
    vault = _make_vault(tmp_path, {note_path: "revived body"})
    modified_at = MetadataStore.canonical_document_timestamp(
        _filesystem_mtime_iso(vault / note_path)
    )
    existing = {
        note_path: _existing_obsidian_document(
            note_path,
            content="old tombstoned body",
            modified_at=modified_at,
            deleted_at="2026-06-03T00:00:00Z",
        )
    }
    read_calls = _track_note_byte_reads(monkeypatch)

    snapshot = asyncio.run(
        fetch_obsidian_documents(vault, existing_documents=existing)
    )

    assert read_calls == [note_path]
    assert snapshot.documents[0].content == "revived body"


def test_fetch_obsidian_fetch_skip_still_reads_when_content_empty(monkeypatch, tmp_path):
    note_path = "empty-stored.md"
    vault = _make_vault(tmp_path, {note_path: "non-empty filesystem body"})
    modified_at = MetadataStore.canonical_document_timestamp(
        _filesystem_mtime_iso(vault / note_path)
    )
    existing = {
        note_path: _existing_obsidian_document(
            note_path,
            content="",
            modified_at=modified_at,
            content_hash="",
        )
    }
    read_calls = _track_note_byte_reads(monkeypatch)

    snapshot = asyncio.run(
        fetch_obsidian_documents(vault, existing_documents=existing)
    )

    assert read_calls == [note_path]
    assert snapshot.documents[0].content == "non-empty filesystem body"


def test_fetch_obsidian_fetch_reuse_loader_after_list(
    monkeypatch, tmp_path
):
    kept = "keep.md"
    other = "other.md"
    vault = _make_vault(
        tmp_path,
        {
            kept: "filesystem keep",
            other: "filesystem other",
        },
    )
    kept_mtime = MetadataStore.canonical_document_timestamp(
        _filesystem_mtime_iso(vault / kept)
    )
    existing = {
        kept: _existing_obsidian_document(
            kept,
            content="reuse keep",
            modified_at=kept_mtime,
        )
    }
    call_order: list[object] = []
    read_calls = _track_note_byte_reads(monkeypatch)

    original_iter = __import__(
        "fetching.obsidian", fromlist=["_iter_obsidian_markdown_files"]
    )._iter_obsidian_markdown_files

    def tracking_iter(*args, **kwargs):
        call_order.append("list")
        return original_iter(*args, **kwargs)

    def loader(ids):
        call_order.append(("loader", tuple(ids)))
        return {doc_id: existing[doc_id] for doc_id in ids if doc_id in existing}

    monkeypatch.setattr(
        "fetching.obsidian._iter_obsidian_markdown_files",
        tracking_iter,
    )

    snapshot = asyncio.run(
        fetch_obsidian_documents(
            vault,
            existing_documents_loader=loader,
        )
    )

    assert call_order[0] == "list"
    assert ("loader", (kept, other)) in call_order or ("loader", (other, kept)) in call_order
    assert call_order.index("list") < next(
        i for i, item in enumerate(call_order) if isinstance(item, tuple)
    )
    assert kept not in read_calls
    assert other in read_calls
    assert [doc.document_id for doc in snapshot.documents] == [kept, other] or [
        doc.document_id for doc in snapshot.documents
    ] == [other, kept]
    kept_doc = next(doc for doc in snapshot.documents if doc.document_id == kept)
    other_doc = next(doc for doc in snapshot.documents if doc.document_id == other)
    assert kept_doc.content == "reuse keep"
    assert other_doc.content == "filesystem other"


def test_should_skip_obsidian_note_fetch_reuse_helper(tmp_path):
    from fetching.obsidian import _should_skip_obsidian_note_fetch

    note_path = "helper.md"
    vault = _make_vault(tmp_path, {note_path: "body"})
    modified_at = MetadataStore.canonical_document_timestamp(
        _filesystem_mtime_iso(vault / note_path)
    )
    existing = _existing_obsidian_document(
        note_path,
        content="reuse me",
        modified_at=modified_at,
    )
    mtime = (vault / note_path).stat().st_mtime

    assert _should_skip_obsidian_note_fetch(existing, mtime) is True
    assert (
        _should_skip_obsidian_note_fetch(
            _existing_obsidian_document(
                note_path,
                content="reuse me",
                modified_at="2020-01-01T00:00:00Z",
            ),
            mtime,
        )
        is False
    )
    assert _should_skip_obsidian_note_fetch(None, mtime) is False
