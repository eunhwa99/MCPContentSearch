import asyncio

import pytest

from environments.config import AppConfig
from fetching.connectors import TistorySourceConnector
from fetching.notion import _StopRequested
from fetching.tistory import _emit_progress, fetch_post, fetch_tistory_posts


pytestmark = pytest.mark.unit


class FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return """
        <html>
          <h1>Post title</h1>
          <time>2026-05-22</time>
          <div class="entry-content">Post body</div>
        </html>
        """


class FakeSession:
    def get(self, url, ssl, timeout):
        self.url = url
        return FakeResponse()


class FakeDatetimeResponse(FakeResponse):
    async def text(self):
        return """
        <html>
          <h1>Post title</h1>
          <time datetime="2026-05-22T03:04:05+09:00">2026. 5. 22. 03:04</time>
          <div class="entry-content">Post body</div>
        </html>
        """


class FakeDatetimeSession(FakeSession):
    def get(self, url, ssl, timeout):
        self.url = url
        return FakeDatetimeResponse()


class FakeDisplayDateResponse(FakeResponse):
    async def text(self):
        return """
        <html>
          <h1>Post title</h1>
          <span class="date">어제 오후 3시</span>
          <div class="entry-content">Post body</div>
        </html>
        """


class FakeDisplayDateSession(FakeSession):
    def get(self, url, ssl, timeout):
        self.url = url
        return FakeDisplayDateResponse()


def test_fetch_post_populates_native_external_id():
    session = FakeSession()

    post = asyncio.run(fetch_post(session, "devlog", 7, 1.0))

    assert post["id"] == "tistory_7"
    assert post["document_id"] == "devlog:7"
    assert post["external_id"] == "devlog:7"
    assert post["canonical_url"] == "https://devlog.tistory.com/7"
    assert post["published_at"] == "2026-05-22"
    assert post["date_provenance"] == "tistory"
    assert session.url == "https://devlog.tistory.com/7"


def test_fetch_post_prefers_time_datetime_for_normalized_publication():
    post = asyncio.run(fetch_post(FakeDatetimeSession(), "devlog", 7, 1.0))

    assert post["date"] == "2026. 5. 22. 03:04"
    assert post["published_at"] == "2026-05-22T03:04:05+09:00"


def test_fetch_post_keeps_non_iso_display_date_out_of_normalized_field():
    post = asyncio.run(fetch_post(FakeDisplayDateSession(), "devlog", 7, 1.0))

    assert post["date"] == "어제 오후 3시"
    assert post["published_at"] == ""
    assert post["date_provenance"] == ""


def test_fetch_tistory_posts_emits_scan_upstream_progress(monkeypatch):
    events = []

    async def capture(event):
        events.append(event)

    async def fake_fetch_post(session, blog_name, post_id, request_timeout):
        if post_id == 2:
            return {
                "id": "tistory_2",
                "document_id": f"{blog_name}:2",
                "external_id": f"{blog_name}:2",
                "title": "Two",
                "content": "body",
                "url": f"https://{blog_name}.tistory.com/2",
                "platform": "Tistory",
                "date": "",
                "published_at": "",
                "date_provenance": "",
            }
        return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeConnector:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr("fetching.tistory.aiohttp.ClientSession", lambda **kwargs: FakeSession())
    monkeypatch.setattr("fetching.tistory.aiohttp.TCPConnector", FakeConnector)
    monkeypatch.setattr("fetching.tistory.aiohttp.ClientTimeout", lambda **kwargs: object())
    monkeypatch.setattr("fetching.tistory.fetch_post", fake_fetch_post)

    documents = asyncio.run(
        fetch_tistory_posts(
            "devlog",
            max_id=3,
            connection_limit=2,
            request_timeout=1.0,
            log_interval=10,
            progress_callback=capture,
        )
    )

    assert len(documents) == 1
    list_ready = [event for event in events if event.get("event") == "search_completed"]
    assert list_ready, "expected Tistory scan to publish total progress"
    assert list_ready[0]["total_pages"] == 3
    assert any(
        event.get("event") in {"page_fetch_completed", "page_fetch_started"}
        and event.get("total_pages") == 3
        for event in events
    ), "expected Tistory scan to publish done/progress during fetch"
    done_values = [
        event.get("current_page")
        for event in events
        if event.get("event") == "page_fetch_completed"
    ]
    assert done_values, "expected at least one completed-item progress event"
    assert max(done_values) >= 1


def test_tistory_connector_exposes_progress_callback_for_ingestion_wiring():
    connector = TistorySourceConnector("devlog", AppConfig(tistory_max_post_id=3))
    assert hasattr(connector, "progress_callback")


def test_tistory_emit_progress_reraises_inactive_job_stop():
    class _InactiveJobStop(Exception):
        pass

    async def boom(_event):
        raise _InactiveJobStop("job inactive")

    with pytest.raises(_InactiveJobStop):
        asyncio.run(_emit_progress(boom, {"event": "page_fetch_completed"}))


def test_tistory_emit_progress_returns_stop_signal():
    stop_signal = object()

    async def request_stop(_event):
        return stop_signal

    assert (
        asyncio.run(
            _emit_progress(
                request_stop,
                {"event": "page_fetch_completed"},
                stop_signal=stop_signal,
            )
        )
        is True
    )


def test_tistory_fetch_aborts_when_progress_stop_signal_returned(monkeypatch):
    stop_signal = object()
    events = []
    fetch_calls = []

    async def capture_and_stop(event):
        events.append(event)
        if event.get("event") == "search_completed":
            return stop_signal
        return None

    async def fake_fetch_post(session, blog_name, post_id, request_timeout):
        fetch_calls.append(post_id)
        return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeConnector:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(
        "fetching.tistory.aiohttp.ClientSession", lambda **kwargs: FakeSession()
    )
    monkeypatch.setattr("fetching.tistory.aiohttp.TCPConnector", FakeConnector)
    monkeypatch.setattr(
        "fetching.tistory.aiohttp.ClientTimeout", lambda **kwargs: object()
    )
    monkeypatch.setattr("fetching.tistory.fetch_post", fake_fetch_post)

    with pytest.raises(_StopRequested):
        asyncio.run(
            fetch_tistory_posts(
                "devlog",
                max_id=5,
                connection_limit=2,
                request_timeout=1.0,
                log_interval=10,
                progress_callback=capture_and_stop,
                progress_stop_signal=stop_signal,
            )
        )

    assert any(event.get("event") == "search_completed" for event in events)
    assert not any(event.get("event") == "page_fetch_completed" for event in events)
    assert fetch_calls == []


def test_tistory_fetch_cancels_pending_tasks_on_inactive_job_stop(monkeypatch):
    class _InactiveJobStop(Exception):
        pass

    cancelled_before_session_exit: list[int] = []
    session_exited = {"done": False}
    completed_events = {"n": 0}

    async def fake_fetch_post(session, blog_name, post_id, request_timeout):
        if post_id == 1:
            await asyncio.sleep(0)
            return None
        try:
            await asyncio.sleep(30)
            return None
        except asyncio.CancelledError:
            if not session_exited["done"]:
                cancelled_before_session_exit.append(post_id)
            raise

    async def raise_inactive(event):
        if event.get("event") != "page_fetch_completed":
            return None
        completed_events["n"] += 1
        if completed_events["n"] == 1:
            raise _InactiveJobStop("job inactive")
        return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            session_exited["done"] = True
            return False

    class FakeConnector:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(
        "fetching.tistory.aiohttp.ClientSession", lambda **kwargs: FakeSession()
    )
    monkeypatch.setattr("fetching.tistory.aiohttp.TCPConnector", FakeConnector)
    monkeypatch.setattr(
        "fetching.tistory.aiohttp.ClientTimeout", lambda **kwargs: object()
    )
    monkeypatch.setattr("fetching.tistory.fetch_post", fake_fetch_post)

    with pytest.raises(_InactiveJobStop):
        asyncio.run(
            fetch_tistory_posts(
                "devlog",
                max_id=4,
                connection_limit=4,
                request_timeout=1.0,
                log_interval=10,
                progress_callback=raise_inactive,
            )
        )

    assert cancelled_before_session_exit, (
        "InactiveJobStop must cancel pending create_task fan-out before "
        "ClientSession closes"
    )


def test_tistory_fetch_cancels_pending_tasks_on_cancelled_error(monkeypatch):
    """asyncio.CancelledError (e.g. worker SIGTERM) must cancel fan-out before session exit."""
    cancelled_before_session_exit: list[int] = []
    session_exited = {"done": False}
    completed_events = {"n": 0}

    async def fake_fetch_post(session, blog_name, post_id, request_timeout):
        if post_id == 1:
            await asyncio.sleep(0)
            return None
        try:
            await asyncio.sleep(30)
            return None
        except asyncio.CancelledError:
            if not session_exited["done"]:
                cancelled_before_session_exit.append(post_id)
            raise

    async def raise_cancelled(event):
        if event.get("event") != "page_fetch_completed":
            return None
        completed_events["n"] += 1
        if completed_events["n"] == 1:
            raise asyncio.CancelledError()
        return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            session_exited["done"] = True
            return False

    class FakeConnector:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(
        "fetching.tistory.aiohttp.ClientSession", lambda **kwargs: FakeSession()
    )
    monkeypatch.setattr("fetching.tistory.aiohttp.TCPConnector", FakeConnector)
    monkeypatch.setattr(
        "fetching.tistory.aiohttp.ClientTimeout", lambda **kwargs: object()
    )
    monkeypatch.setattr("fetching.tistory.fetch_post", fake_fetch_post)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            fetch_tistory_posts(
                "devlog",
                max_id=4,
                connection_limit=4,
                request_timeout=1.0,
                log_interval=10,
                progress_callback=raise_cancelled,
            )
        )

    assert cancelled_before_session_exit, (
        "CancelledError must cancel pending create_task fan-out before "
        "ClientSession closes"
    )


def test_tistory_task_cancel_shields_finally_drain_before_session_exit(monkeypatch):
    """True Task.cancel must finish shielded finally drain before ClientSession exits.

    Raising CancelledError from a callback does not keep the task in a cancelling
    state; worker SIGTERM uses Task.cancel(), which can abort an unshielded
    gather await mid-drain and leave fan-out hitting a closing session.
    """
    cancelled_before_session_exit: list[int] = []
    session_exited = {"done": False}
    fanout_running = asyncio.Event()
    shield_used = {"n": 0}
    real_shield = asyncio.shield

    def tracking_shield(awaitable):
        shield_used["n"] += 1
        return real_shield(awaitable)

    async def fake_fetch_post(session, blog_name, post_id, request_timeout):
        fanout_running.set()
        try:
            # Slow cancel acknowledgment so an unshielded finally gather can be
            # aborted by Task.cancel before drain finishes.
            await asyncio.sleep(30)
            return None
        except asyncio.CancelledError:
            await asyncio.sleep(0.05)
            if not session_exited["done"]:
                cancelled_before_session_exit.append(post_id)
            raise

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            session_exited["done"] = True
            return False

    class FakeConnector:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(
        "fetching.tistory.aiohttp.ClientSession", lambda **kwargs: FakeSession()
    )
    monkeypatch.setattr("fetching.tistory.aiohttp.TCPConnector", FakeConnector)
    monkeypatch.setattr(
        "fetching.tistory.aiohttp.ClientTimeout", lambda **kwargs: object()
    )
    monkeypatch.setattr("fetching.tistory.fetch_post", fake_fetch_post)
    monkeypatch.setattr("fetching.tistory.asyncio.shield", tracking_shield)

    async def run_and_cancel():
        task = asyncio.create_task(
            fetch_tistory_posts(
                "devlog",
                max_id=4,
                connection_limit=4,
                request_timeout=1.0,
                log_interval=10,
            )
        )
        await fanout_running.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_and_cancel())

    assert cancelled_before_session_exit, (
        "Task.cancel must shield finally gather so pending fan-out finishes "
        "cleanup before ClientSession closes"
    )
    assert shield_used["n"] >= 1, (
        "finally pending gather must be wrapped in asyncio.shield so a true "
        "Task.cancel cannot abort drain mid-await"
    )
