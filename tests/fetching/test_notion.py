import asyncio
import logging

import httpx
import pytest

from core.exceptions import APIError
from environments.config import AppConfig, NotionConfig
from fetching.notion import (
    NotionAPIClient,
    NotionPageProcessor,
    _StopRequested,
    _await_request_with_stop,
    _emit_progress,
    fetch_notion_pages,
    fetch_notion_target,
    parse_notion_object_id,
)


pytestmark = pytest.mark.unit


def test_fetch_notion_pages_emits_progress_events(monkeypatch):
    events = []
    pages = [
        {
            "id": "page-1",
            "url": "https://notion.so/page-1",
            "created_time": "2026-06-01T00:00:00Z",
            "last_edited_time": "2026-06-01T00:00:00Z",
            "properties": {"title": {"title": [{"plain_text": "Page 1"}]}},
        },
        {
            "id": "page-2",
            "url": "https://notion.so/page-2",
            "created_time": "2026-06-01T00:00:00Z",
            "last_edited_time": "2026-06-01T00:00:00Z",
            "properties": {"title": {"title": [{"plain_text": "Page 2"}]}},
        },
    ]

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_search_pages(
        self,
        client,
        progress_callback=None,
        progress_stop_signal=None,
        progress_stop_checker=None,
    ):
        return pages

    async def fake_fetch_block_content(
        self,
        client,
        block_id,
        depth=0,
        strict=False,
        stop_checker=None,
    ):
        return f"content for {block_id}"

    async def capture(event):
        events.append(event)

    monkeypatch.setattr("fetching.notion.httpx.AsyncClient", lambda *args, **kwargs: FakeAsyncClient())
    monkeypatch.setattr(NotionAPIClient, "search_pages", fake_search_pages)
    monkeypatch.setattr(NotionAPIClient, "fetch_block_content", fake_fetch_block_content)

    documents = asyncio.run(fetch_notion_pages("secret", AppConfig(), progress_callback=capture))

    assert [event["event"] for event in events] == [
        "search_started",
        "search_completed",
        "page_fetch_started",
        "page_fetch_completed",
        "page_fetch_started",
        "page_fetch_completed",
    ]
    assert events[1]["total_pages"] == 2
    assert events[2]["current_page"] == 1
    assert events[2]["page_id"] == "page-1"
    assert events[3]["title"] == "Page 1"
    assert events[5]["current_page"] == 2
    assert len(documents) == 2


def test_fetch_notion_pages_stops_when_progress_callback_returns_terminal_signal(monkeypatch):
    pages = [
        {
            "id": "page-1",
            "url": "https://notion.so/page-1",
            "created_time": "2026-06-01T00:00:00Z",
            "last_edited_time": "2026-06-01T00:00:00Z",
            "properties": {"title": {"title": [{"plain_text": "Page 1"}]}},
        },
        {
            "id": "page-2",
            "url": "https://notion.so/page-2",
            "created_time": "2026-06-01T00:00:00Z",
            "last_edited_time": "2026-06-01T00:00:00Z",
            "properties": {"title": {"title": [{"plain_text": "Page 2"}]}},
        },
    ]
    fetch_calls = []

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_search_pages(
        self,
        client,
        progress_callback=None,
        progress_stop_signal=None,
        progress_stop_checker=None,
    ):
        return pages

    async def fake_fetch_block_content(
        self,
        client,
        block_id,
        depth=0,
        strict=False,
        stop_checker=None,
    ):
        fetch_calls.append(block_id)
        return f"content for {block_id}"

    stop_signal = object()

    async def stop_on_first_page(event):
        if event["event"] == "page_fetch_started":
            return stop_signal
        return None

    monkeypatch.setattr("fetching.notion.httpx.AsyncClient", lambda *args, **kwargs: FakeAsyncClient())
    monkeypatch.setattr(NotionAPIClient, "search_pages", fake_search_pages)
    monkeypatch.setattr(NotionAPIClient, "fetch_block_content", fake_fetch_block_content)

    with pytest.raises(_StopRequested):
        asyncio.run(
            fetch_notion_pages(
                "secret",
                AppConfig(),
                progress_callback=stop_on_first_page,
                progress_stop_signal=stop_signal,
            )
        )

    assert fetch_calls == []


def test_emit_progress_propagates_stop_requested():
    async def stop_now(event):
        raise _StopRequested

    with pytest.raises(_StopRequested):
        asyncio.run(_emit_progress(stop_now, {"event": "search_started"}))


def test_fetch_notion_pages_stops_during_search_discovery(monkeypatch):
    fetch_calls = []
    stop_signal = object()

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "page-1",
                            "url": "https://notion.so/page-1",
                            "created_time": "2026-06-01T00:00:00Z",
                            "last_edited_time": "2026-06-01T00:00:00Z",
                            "properties": {"title": {"title": [{"plain_text": "Page 1"}]}},
                        }
                    ],
                    "has_more": True,
                    "next_cursor": "cursor-2",
                },
                request=request,
            )

    async def fake_fetch_block_content(
        self,
        client,
        block_id,
        depth=0,
        strict=False,
        stop_checker=None,
    ):
        fetch_calls.append(block_id)
        return f"content for {block_id}"

    async def stop_during_discovery(event):
        if event["event"] == "search_page_batch_completed":
            return stop_signal
        return None

    monkeypatch.setattr("fetching.notion.httpx.AsyncClient", lambda *args, **kwargs: FakeAsyncClient())
    monkeypatch.setattr(NotionAPIClient, "fetch_block_content", fake_fetch_block_content)

    with pytest.raises(_StopRequested):
        asyncio.run(
            fetch_notion_pages(
                "secret",
                AppConfig(),
                progress_callback=stop_during_discovery,
                progress_stop_signal=stop_signal,
            )
        )

    assert fetch_calls == []


def test_fetch_notion_pages_stops_during_search_discovery_via_stop_checker(monkeypatch):
    fetch_calls = []

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "page-1",
                            "url": "https://notion.so/page-1",
                            "created_time": "2026-06-01T00:00:00Z",
                            "last_edited_time": "2026-06-01T00:00:00Z",
                            "properties": {"title": {"title": [{"plain_text": "Page 1"}]}},
                        }
                    ],
                    "has_more": True,
                    "next_cursor": "cursor-2",
                },
                request=request,
            )

    async def fake_fetch_block_content(
        self,
        client,
        block_id,
        depth=0,
        strict=False,
        stop_checker=None,
    ):
        fetch_calls.append(block_id)
        return f"content for {block_id}"

    stop_checks = 0

    async def stop_checker():
        nonlocal stop_checks
        stop_checks += 1
        return stop_checks >= 3

    monkeypatch.setattr("fetching.notion.httpx.AsyncClient", lambda *args, **kwargs: FakeAsyncClient())
    monkeypatch.setattr(NotionAPIClient, "fetch_block_content", fake_fetch_block_content)

    with pytest.raises(_StopRequested):
        asyncio.run(
            fetch_notion_pages(
                "secret",
                AppConfig(),
                progress_stop_checker=stop_checker,
            )
        )

    assert fetch_calls == []


def test_search_pages_stops_before_discovery_progress_when_checker_trips(monkeypatch):
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())
    events = []

    class FakeAsyncClient:
        async def post(self, url, **kwargs):
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "page-1",
                            "url": "https://notion.so/page-1",
                            "created_time": "2026-06-01T00:00:00Z",
                            "last_edited_time": "2026-06-01T00:00:00Z",
                            "properties": {"title": {"title": [{"plain_text": "Page 1"}]}},
                        }
                    ],
                    "has_more": False,
                },
                request=request,
            )

    stop_checks = 0

    async def stop_checker():
        nonlocal stop_checks
        stop_checks += 1
        return stop_checks >= 2

    async def capture(event):
        events.append(event["event"])

    with pytest.raises(_StopRequested):
        asyncio.run(
            client.search_pages(
                FakeAsyncClient(),
                progress_callback=capture,
                progress_stop_checker=stop_checker,
            )
        )

    assert events == []


def test_fetch_notion_pages_stops_after_final_discovery_batch_via_stop_checker(monkeypatch):
    fetch_calls = []

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "page-1",
                            "url": "https://notion.so/page-1",
                            "created_time": "2026-06-01T00:00:00Z",
                            "last_edited_time": "2026-06-01T00:00:00Z",
                            "properties": {"title": {"title": [{"plain_text": "Page 1"}]}},
                        }
                    ],
                    "has_more": False,
                },
                request=request,
            )

    async def fake_fetch_block_content(
        self,
        client,
        block_id,
        depth=0,
        strict=False,
        stop_checker=None,
    ):
        fetch_calls.append(block_id)
        return f"content for {block_id}"

    stop_checks = 0

    async def stop_checker():
        nonlocal stop_checks
        stop_checks += 1
        return stop_checks >= 3

    monkeypatch.setattr("fetching.notion.httpx.AsyncClient", lambda *args, **kwargs: FakeAsyncClient())
    monkeypatch.setattr(NotionAPIClient, "fetch_block_content", fake_fetch_block_content)

    with pytest.raises(_StopRequested):
        asyncio.run(
            fetch_notion_pages(
                "secret",
                AppConfig(),
                progress_stop_checker=stop_checker,
            )
        )

    assert fetch_calls == []


def test_request_json_stops_before_retry_sleep(monkeypatch):
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())
    sleep_calls = []

    class FakeAsyncClient:
        async def get(self, url, **kwargs):
            request = httpx.Request("GET", url)
            response = httpx.Response(429, headers={"Retry-After": "7"}, request=request)
            raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr("fetching.notion.asyncio.sleep", fake_sleep)

    with pytest.raises(_StopRequested):
        asyncio.run(
            client._request_json(
                FakeAsyncClient(),
                "get",
                "https://api.notion.com/v1/pages/page-1",
                stop_checker=lambda: True,
            )
        )

    assert sleep_calls == []


def test_request_json_stops_after_retry_sleep(monkeypatch):
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())
    sleep_calls = []
    stop_checks = 0

    class FakeAsyncClient:
        async def get(self, url, **kwargs):
            request = httpx.Request("GET", url)
            response = httpx.Response(429, headers={"Retry-After": "7"}, request=request)
            raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    async def stop_checker():
        nonlocal stop_checks
        stop_checks += 1
        return stop_checks >= 3

    monkeypatch.setattr("fetching.notion.asyncio.sleep", fake_sleep)

    with pytest.raises(_StopRequested):
        asyncio.run(
            client._request_json(
                FakeAsyncClient(),
                "get",
                "https://api.notion.com/v1/pages/page-1",
                stop_checker=stop_checker,
            )
        )

    assert sleep_calls == []


def test_request_json_propagates_stop_requested_from_stop_checker():
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())

    async def stop_checker():
        raise _StopRequested

    class FakeAsyncClient:
        async def get(self, url, **kwargs):
            raise AssertionError("request should not be attempted after stop")

    with pytest.raises(_StopRequested):
        asyncio.run(
            client._request_json(
                FakeAsyncClient(),
                "get",
                "https://api.notion.com/v1/pages/page-1",
                stop_checker=stop_checker,
            )
        )


def test_await_request_with_stop_prefers_stop_requested_over_completed_request_error(
    monkeypatch,
):
    async def request_coro():
        await asyncio.sleep(0)
        request = httpx.Request("GET", "https://api.notion.com/v1/pages/page-1")
        response = httpx.Response(429, request=request)
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    async def fake_wait(tasks, timeout):
        await asyncio.sleep(0)
        return set(tasks), set()

    monkeypatch.setattr("fetching.notion.asyncio.wait", fake_wait)

    with pytest.raises(_StopRequested):
        asyncio.run(_await_request_with_stop(request_coro(), stop_checker=lambda: True))


def test_request_json_stops_while_request_is_in_flight():
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())
    started = asyncio.Event()
    request_cancelled = asyncio.Event()
    stop_checks = 0

    class SlowHTTPClient:
        async def get(self, url, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                request_cancelled.set()
                raise

    async def stop_checker():
        nonlocal stop_checks
        if not started.is_set():
            return False
        stop_checks += 1
        return stop_checks >= 2

    with pytest.raises(_StopRequested):
        asyncio.run(
            client._request_json(
                SlowHTTPClient(),
                "get",
                "https://api.notion.com/v1/pages/page-1",
                stop_checker=stop_checker,
            )
        )

    assert request_cancelled.is_set()


def test_fetch_notion_pages_stops_before_search_completed_progress_when_checker_trips(
    monkeypatch,
):
    events = []

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_search_pages(
        self,
        client,
        progress_callback=None,
        progress_stop_signal=None,
        progress_stop_checker=None,
    ):
        return [
            {
                "id": "page-1",
                "url": "https://notion.so/page-1",
                "created_time": "2026-06-01T00:00:00Z",
                "last_edited_time": "2026-06-01T00:00:00Z",
                "properties": {"title": {"title": [{"plain_text": "Page 1"}]}},
            }
        ]

    async def capture(event):
        events.append(event["event"])

    stop_checks = 0

    async def stop_checker():
        nonlocal stop_checks
        stop_checks += 1
        return stop_checks >= 2

    monkeypatch.setattr("fetching.notion.httpx.AsyncClient", lambda *args, **kwargs: FakeAsyncClient())
    monkeypatch.setattr(NotionAPIClient, "search_pages", fake_search_pages)

    with pytest.raises(_StopRequested):
        asyncio.run(
            fetch_notion_pages(
                "secret",
                AppConfig(),
                progress_callback=capture,
                progress_stop_checker=stop_checker,
            )
        )

    assert events == ["search_started"]


def test_fetch_notion_pages_does_not_stop_on_non_sentinel_progress_value(monkeypatch):
    pages = [
        {
            "id": "page-1",
            "url": "https://notion.so/page-1",
            "created_time": "2026-06-01T00:00:00Z",
            "last_edited_time": "2026-06-01T00:00:00Z",
            "properties": {"title": {"title": [{"plain_text": "Page 1"}]}},
        }
    ]
    fetch_calls = []

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_search_pages(
        self,
        client,
        progress_callback=None,
        progress_stop_signal=None,
        progress_stop_checker=None,
    ):
        return pages

    async def fake_fetch_block_content(
        self,
        client,
        block_id,
        depth=0,
        strict=False,
        stop_checker=None,
    ):
        fetch_calls.append(block_id)
        return f"content for {block_id}"

    async def ack(event):
        return {"ack": event["event"]}

    monkeypatch.setattr("fetching.notion.httpx.AsyncClient", lambda *args, **kwargs: FakeAsyncClient())
    monkeypatch.setattr(NotionAPIClient, "search_pages", fake_search_pages)
    monkeypatch.setattr(NotionAPIClient, "fetch_block_content", fake_fetch_block_content)

    documents = asyncio.run(fetch_notion_pages("secret", AppConfig(), progress_callback=ack))

    assert len(documents) == 1
    assert fetch_calls == ["page-1"]


def test_fetch_notion_pages_ignores_progress_callback_exception(monkeypatch):
    pages = [
        {
            "id": "page-1",
            "url": "https://notion.so/page-1",
            "created_time": "2026-06-01T00:00:00Z",
            "last_edited_time": "2026-06-01T00:00:00Z",
            "properties": {"title": {"title": [{"plain_text": "Page 1"}]}},
        }
    ]

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def fake_search_pages(
        self,
        client,
        progress_callback=None,
        progress_stop_signal=None,
        progress_stop_checker=None,
    ):
        return pages

    async def fake_fetch_block_content(
        self,
        client,
        block_id,
        depth=0,
        strict=False,
        stop_checker=None,
    ):
        return f"content for {block_id}"

    async def fail(event):
        raise RuntimeError("progress hook failed")

    monkeypatch.setattr("fetching.notion.httpx.AsyncClient", lambda *args, **kwargs: FakeAsyncClient())
    monkeypatch.setattr(NotionAPIClient, "search_pages", fake_search_pages)
    monkeypatch.setattr(NotionAPIClient, "fetch_block_content", fake_fetch_block_content)

    documents = asyncio.run(fetch_notion_pages("secret", AppConfig(), progress_callback=fail))

    assert len(documents) == 1


def test_notion_block_fetch_can_surface_strict_full_sync_failures():
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())

    async def fail_fetch_blocks(http_client, block_id, stop_checker=None):
        raise RuntimeError("block fetch failed")

    client._fetch_blocks = fail_fetch_blocks

    with pytest.raises(RuntimeError, match="block fetch failed"):
        asyncio.run(client.fetch_block_content(object(), "block-id", strict=True))

    assert asyncio.run(client.fetch_block_content(object(), "block-id")) == ""


def test_notion_max_depth_warning_does_not_log_block_id(caplog):
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())
    sensitive_block_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    with caplog.at_level(logging.WARNING, logger="fetching.notion"):
        result = asyncio.run(
            client.fetch_block_content(
                object(),
                sensitive_block_id,
                depth=client.app_config.notion_max_depth + 1,
            )
        )

    assert result == ""
    assert "Max Notion block depth reached" in caplog.text
    assert sensitive_block_id not in caplog.text


def test_notion_block_fetch_stops_during_paginated_children():
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())
    calls = []
    stop_checks = 0

    async def fake_request_json(http_client, method, url, **kwargs):
        calls.append(url)
        return {
            "results": [
                {
                    "id": "block-1",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"plain_text": "hello"}]},
                }
            ],
            "has_more": True,
            "next_cursor": "cursor-2",
        }

    def stop_checker():
        nonlocal stop_checks
        stop_checks += 1
        return stop_checks >= 3

    client._request_json = fake_request_json

    with pytest.raises(_StopRequested):
        asyncio.run(
            client.fetch_block_content(
                object(),
                "block-id",
                strict=True,
                stop_checker=stop_checker,
            )
        )

    assert len(calls) == 1


def test_notion_block_fetch_propagates_stop_requested_when_not_strict():
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())

    async def stop_checker():
        raise _StopRequested

    with pytest.raises(_StopRequested):
        asyncio.run(
            client.fetch_block_content(
                object(),
                "block-id",
                strict=False,
                stop_checker=stop_checker,
            )
        )


def _notion_response(status_code: int, payload: dict | None = None) -> httpx.Response:
    request = httpx.Request("GET", "https://api.notion.com/v1/test")
    return httpx.Response(status_code, json=payload or {}, request=request)


def test_notion_block_children_retries_transient_502(monkeypatch):
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())
    calls = []

    async def no_sleep(seconds):
        return None

    class FlakyHTTPClient:
        async def get(self, url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                return _notion_response(502, {"code": "bad_gateway", "message": "Bad Gateway"})
            return _notion_response(
                200,
                {
                    "results": [
                        {
                            "id": "block-1",
                            "type": "paragraph",
                            "paragraph": {"rich_text": [{"plain_text": "retried"}]},
                        }
                    ],
                    "has_more": False,
                },
            )

    monkeypatch.setattr("fetching.notion.asyncio.sleep", no_sleep)

    blocks = asyncio.run(client._fetch_blocks(FlakyHTTPClient(), "block-id"))

    assert len(calls) == 2
    assert blocks[0]["id"] == "block-1"


def test_notion_block_children_does_not_retry_permission_errors(monkeypatch):
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())
    calls = []

    async def fail_sleep(seconds):
        raise AssertionError("403 should not sleep for retry")

    class PermissionHTTPClient:
        async def get(self, url, **kwargs):
            calls.append(url)
            return _notion_response(
                403,
                {"code": "restricted_resource", "message": "not shared"},
            )

    monkeypatch.setattr("fetching.notion.asyncio.sleep", fail_sleep)

    with pytest.raises(APIError, match="HTTP 403"):
        asyncio.run(client._fetch_blocks(PermissionHTTPClient(), "block-id"))

    assert len(calls) == 1


def test_notion_block_children_raises_after_transient_retry_exhaustion(monkeypatch):
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())
    calls = []

    async def no_sleep(seconds):
        return None

    class AlwaysBadGatewayHTTPClient:
        async def get(self, url, **kwargs):
            calls.append(url)
            return _notion_response(502, {"code": "bad_gateway", "message": "Bad Gateway"})

    monkeypatch.setattr("fetching.notion.asyncio.sleep", no_sleep)

    with pytest.raises(APIError, match="HTTP 502"):
        asyncio.run(client._fetch_blocks(AlwaysBadGatewayHTTPClient(), "block-id"))

    assert len(calls) == 3


def test_notion_search_pages_retries_transient_500(monkeypatch):
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())
    calls = []

    async def no_sleep(seconds):
        return None

    class FlakyHTTPClient:
        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            if len(calls) == 1:
                return _notion_response(
                    500,
                    {"code": "internal_server_error", "message": "try again"},
                )
            return _notion_response(
                200,
                {
                    "results": [{"id": "page-id"}],
                    "has_more": False,
                },
            )

    monkeypatch.setattr("fetching.notion.asyncio.sleep", no_sleep)

    pages = asyncio.run(client.search_pages(FlakyHTTPClient()))

    assert pages == [{"id": "page-id"}]
    assert len(calls) == 2
    assert calls[0][1]["json"]["filter"] == {"property": "object", "value": "page"}


def test_notion_page_fetch_retries_transient_503(monkeypatch):
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())
    calls = []

    async def no_sleep(seconds):
        return None

    class FlakyHTTPClient:
        async def get(self, url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                return _notion_response(
                    503,
                    {"code": "service_unavailable", "message": "try again later"},
                )
            return _notion_response(200, {"id": "page-id"})

    monkeypatch.setattr("fetching.notion.asyncio.sleep", no_sleep)

    page = asyncio.run(client.fetch_page(FlakyHTTPClient(), "page-id"))

    assert page["id"] == "page-id"
    assert len(calls) == 2


def test_notion_database_query_retries_transient_429(monkeypatch):
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())
    calls = []

    async def no_sleep(seconds):
        return None

    class RateLimitedHTTPClient:
        async def post(self, url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                return _notion_response(
                    429,
                    {"code": "rate_limited", "message": "slow down"},
                )
            return _notion_response(
                200,
                {
                    "results": [{"id": "page-id"}],
                    "has_more": False,
                },
            )

    monkeypatch.setattr("fetching.notion.asyncio.sleep", no_sleep)

    pages = asyncio.run(client.query_database(RateLimitedHTTPClient(), "database-id"))

    assert pages == [{"id": "page-id"}]
    assert len(calls) == 2


def test_notion_retry_uses_retry_after_header_for_429(monkeypatch):
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    class RateLimitedHTTPClient:
        async def post(self, url, **kwargs):
            response = _notion_response(
                429,
                {"code": "rate_limited", "message": "slow down"},
            )
            response.headers["Retry-After"] = "7"
            return response

    monkeypatch.setattr("fetching.notion.asyncio.sleep", fake_sleep)

    with pytest.raises(APIError, match="HTTP 429"):
        asyncio.run(client.search_pages(RateLimitedHTTPClient()))

    assert slept == [7.0, 7.0]


def test_notion_retry_caps_large_retry_after_header(monkeypatch):
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    class RateLimitedHTTPClient:
        async def post(self, url, **kwargs):
            response = _notion_response(
                429,
                {"code": "rate_limited", "message": "slow down"},
            )
            response.headers["Retry-After"] = "3600"
            return response

    monkeypatch.setattr("fetching.notion.asyncio.sleep", fake_sleep)

    with pytest.raises(APIError, match="HTTP 429"):
        asyncio.run(client.search_pages(RateLimitedHTTPClient()))

    assert slept == [10.0, 10.0]


def test_notion_request_retries_timeout_exception(monkeypatch):
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())
    calls = []
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    class FlakyHTTPClient:
        async def get(self, url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                raise httpx.ReadTimeout("timeout", request=httpx.Request("GET", url))
            return _notion_response(200, {"id": "page-id"})

    monkeypatch.setattr("fetching.notion.asyncio.sleep", fake_sleep)

    page = asyncio.run(client.fetch_page(FlakyHTTPClient(), "page-id"))

    assert page["id"] == "page-id"
    assert len(calls) == 2
    assert slept == [2]


def test_notion_page_processor_populates_native_external_id():
    processor = NotionPageProcessor(NotionConfig(api_key="secret"))

    document = processor.build_document(
        {
            "id": "page-123",
            "url": "https://notion.so/page-123",
            "created_time": "2026-05-21T00:00:00Z",
            "last_edited_time": "2026-05-22T00:00:00Z",
            "properties": {
                "title": {
                    "title": [
                        {
                            "plain_text": "Identity",
                        }
                    ]
                }
            },
        },
        "content",
    )

    assert document.id == "notion_page-123"
    assert document.document_id == "page-123"
    assert document.external_id == "page-123"
    assert document.canonical_url == "https://notion.so/page-123"
    assert document.updated_at == "2026-05-22T00:00:00Z"
    assert document.published_at == "2026-05-21T00:00:00Z"
    assert document.modified_at == "2026-05-22T00:00:00Z"
    assert document.date_provenance == "notion"


def test_parse_notion_object_id_from_page_url_and_bare_uuid():
    assert parse_notion_object_id(
        "https://www.notion.so/ContextWiki-0123456789abcdef0123456789abcdef?pvs=4"
    ) == "01234567-89ab-cdef-0123-456789abcdef"
    assert parse_notion_object_id(
        "01234567-89ab-cdef-0123-456789abcdef"
    ) == "01234567-89ab-cdef-0123-456789abcdef"


def test_parse_notion_object_id_uses_trailing_page_id_without_title_hex_bleed():
    assert parse_notion_object_id(
        "https://www.notion.so/Page-0123456789abcdef0123456789abcdef"
    ) == "01234567-89ab-cdef-0123-456789abcdef"


def test_parse_notion_object_id_prefers_trailing_id_over_hex_like_title():
    assert parse_notion_object_id(
        "https://www.notion.so/deadbeefdeadbeefdeadbeefdeadbeef-0123456789abcdef0123456789abcdef"
    ) == "01234567-89ab-cdef-0123-456789abcdef"
    assert parse_notion_object_id(
        "https://www.notion.so/deadbeef-0123456789abcdef0123456789abcdef"
    ) == "01234567-89ab-cdef-0123-456789abcdef"


def test_parse_notion_object_id_rejects_non_notion_url():
    with pytest.raises(ValueError, match="Invalid Notion URL"):
        parse_notion_object_id("https://example.com/0123456789abcdef0123456789abcdef")


def test_fetch_notion_target_fetches_single_page(monkeypatch):
    page_id = "01234567-89ab-cdef-0123-456789abcdef"
    calls = []

    async def fake_fetch_page(self, client, object_id):
        calls.append(("fetch_page", object_id))
        return {
            "id": object_id,
            "url": f"https://www.notion.so/{object_id.replace('-', '')}",
            "created_time": "2026-05-25T00:00:00Z",
            "last_edited_time": "2026-05-26T00:00:00Z",
            "properties": {"title": {"title": [{"plain_text": "Target page"}]}},
        }

    async def fake_fetch_block_content(
        self,
        client,
        block_id,
        strict=False,
        stop_checker=None,
    ):
        calls.append(("fetch_block_content", block_id, strict))
        return "target page content"

    async def fail_query_database(self, client, database_id):
        raise AssertionError("single page target should not query a database")

    monkeypatch.setattr(NotionAPIClient, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(NotionAPIClient, "fetch_block_content", fake_fetch_block_content)
    monkeypatch.setattr(NotionAPIClient, "query_database", fail_query_database)

    documents = asyncio.run(fetch_notion_target("secret", AppConfig(), page_id))

    assert [(call[0], call[1]) for call in calls] == [
        ("fetch_page", page_id),
        ("fetch_block_content", page_id),
    ]
    assert calls[1][2] is True
    assert len(documents) == 1
    assert documents[0].source_id == "source_notion"
    assert documents[0].document_id == page_id
    assert documents[0].title == "Target page"
    assert documents[0].content == "target page content"


def test_fetch_notion_target_falls_back_to_database_on_page_404(monkeypatch):
    database_id = "01234567-89ab-cdef-0123-456789abcdef"
    page_ids = [
        "11111111-2222-3333-4444-555555555555",
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    ]
    calls = []

    async def fake_fetch_page(self, client, object_id):
        calls.append(("fetch_page", object_id))
        raise APIError("Notion", 404, "page not found")

    async def fake_query_database(self, client, object_id):
        calls.append(("query_database", object_id))
        return [
            {
                "id": page_ids[0],
                "url": f"https://www.notion.so/{page_ids[0].replace('-', '')}",
                "created_time": "2026-05-25T00:00:00Z",
                "last_edited_time": "2026-05-26T00:00:00Z",
                "properties": {"title": {"title": [{"plain_text": "First page"}]}},
            },
            {
                "id": page_ids[1],
                "url": f"https://www.notion.so/{page_ids[1].replace('-', '')}",
                "created_time": "2026-05-25T00:00:00Z",
                "last_edited_time": "2026-05-27T00:00:00Z",
                "properties": {"title": {"title": [{"plain_text": "Second page"}]}},
            },
        ]

    async def fake_fetch_block_content(
        self,
        client,
        block_id,
        strict=False,
        stop_checker=None,
    ):
        calls.append(("fetch_block_content", block_id, strict))
        return f"content for {block_id}"

    monkeypatch.setattr(NotionAPIClient, "fetch_page", fake_fetch_page)
    monkeypatch.setattr(NotionAPIClient, "query_database", fake_query_database)
    monkeypatch.setattr(NotionAPIClient, "fetch_block_content", fake_fetch_block_content)

    documents = asyncio.run(fetch_notion_target("secret", AppConfig(), database_id))

    assert [(call[0], call[1]) for call in calls] == [
        ("fetch_page", database_id),
        ("query_database", database_id),
        ("fetch_block_content", page_ids[0]),
        ("fetch_block_content", page_ids[1]),
    ]
    assert [document.document_id for document in documents] == page_ids
    assert [document.source_id for document in documents] == ["source_notion", "source_notion"]
    assert [document.content for document in documents] == [
        f"content for {page_ids[0]}",
        f"content for {page_ids[1]}",
    ]


def test_fetch_notion_target_preserves_page_error_when_database_fallback_also_fails(monkeypatch):
    page_id = "01234567-89ab-cdef-0123-456789abcdef"

    async def fail_fetch_page(self, client, object_id):
        raise APIError("Notion", 404, "object_not_found | page not found")

    async def fail_query_database(self, client, database_id):
        raise APIError("Notion", 404, "object_not_found | database not found")

    monkeypatch.setattr(NotionAPIClient, "fetch_page", fail_fetch_page)
    monkeypatch.setattr(NotionAPIClient, "query_database", fail_query_database)

    with pytest.raises(APIError, match="page not found"):
        asyncio.run(fetch_notion_target("secret", AppConfig(), page_id))


def test_fetch_notion_target_surfaces_database_error_when_fallback_fails_differently(monkeypatch):
    page_id = "01234567-89ab-cdef-0123-456789abcdef"

    async def fail_fetch_page(self, client, object_id):
        raise APIError("Notion", 404, "object_not_found | page not found")

    async def fail_query_database(self, client, database_id):
        raise APIError("Notion", 403, "restricted_resource | not shared")

    monkeypatch.setattr(NotionAPIClient, "fetch_page", fail_fetch_page)
    monkeypatch.setattr(NotionAPIClient, "query_database", fail_query_database)

    with pytest.raises(APIError, match="HTTP 403"):
        asyncio.run(fetch_notion_target("secret", AppConfig(), page_id))


def test_fetch_notion_target_does_not_fallback_to_database_on_non_404_page_error(monkeypatch):
    page_id = "01234567-89ab-cdef-0123-456789abcdef"

    async def fail_fetch_page(self, client, object_id):
        raise APIError("Notion", 403, "not shared with integration")

    async def fail_query_database(self, client, database_id):
        raise AssertionError("non-404 page errors must not query a database")

    monkeypatch.setattr(NotionAPIClient, "fetch_page", fail_fetch_page)
    monkeypatch.setattr(NotionAPIClient, "query_database", fail_query_database)

    with pytest.raises(APIError, match="HTTP 403"):
        asyncio.run(fetch_notion_target("secret", AppConfig(), page_id))


def test_fetch_notion_target_restores_page_error_when_database_fallback_is_not_a_database(
    monkeypatch,
):
    page_id = "01234567-89ab-cdef-0123-456789abcdef"

    async def fail_fetch_page(self, client, object_id):
        raise APIError("Notion", 404, "object_not_found | page not found")

    async def fail_query_database(self, client, database_id):
        raise APIError("Notion", 400, "validation_error | object is not a database")

    monkeypatch.setattr(NotionAPIClient, "fetch_page", fail_fetch_page)
    monkeypatch.setattr(NotionAPIClient, "query_database", fail_query_database)

    with pytest.raises(APIError, match="page not found"):
        asyncio.run(fetch_notion_target("secret", AppConfig(), page_id))
