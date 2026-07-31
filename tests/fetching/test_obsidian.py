import asyncio

import pytest

from environments.config import AppConfig
from fetching.connectors import ObsidianSourceConnector
from fetching.obsidian import _emit_progress, fetch_obsidian_documents
from fetching.notion import _StopRequested


pytestmark = pytest.mark.unit


def _make_vault(tmp_path, notes: dict[str, str]):
    vault = tmp_path / "vault"
    vault.mkdir()
    for rel_path, content in notes.items():
        target = vault / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return vault


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
