import asyncio
import traceback
from types import SimpleNamespace

import pytest

from core.exceptions import IndexingError
from core.models import DocumentModel
from indexing.indexer import ContentIndexer


pytestmark = pytest.mark.unit


def test_content_indexer_redacts_failure_status_logs_and_exception(caplog):
    indexer = ContentIndexer(
        config=SimpleNamespace(progress_log_interval=1),
        chroma_collection=None,
        storage_context=None,
    )

    async def fail_filter(documents):
        raise RuntimeError(
            "index failed token=super-secret-value "
            "AKIAIOSFODNN7EXAMPLE "
            "Authorization: Basic dXNlcjpwYXNzd29yZA=="
        )

    indexer._filter_documents = fail_filter
    documents = [
        DocumentModel(
            id="doc-1",
            title="Doc",
            content="content",
            url="https://example.com",
            platform="web",
        )
    ]

    with caplog.at_level("ERROR", logger="indexing.indexer"):
        with pytest.raises(IndexingError) as exc_info:
            asyncio.run(indexer.index_documents(documents))

    assert "super-secret-value" not in indexer.status.message
    assert "AKIAIOSFODNN7EXAMPLE" not in indexer.status.message
    assert "Basic dXNlcjpwYXNzd29yZA==" not in indexer.status.message
    assert "token=<redacted>" in indexer.status.message
    assert "super-secret-value" not in str(exc_info.value)
    assert "AKIAIOSFODNN7EXAMPLE" not in str(exc_info.value)
    assert "Basic dXNlcjpwYXNzd29yZA==" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    formatted_traceback = "".join(
        traceback.format_exception(
            type(exc_info.value),
            exc_info.value,
            exc_info.value.__traceback__,
        )
    )
    assert "super-secret-value" not in formatted_traceback
    assert "AKIAIOSFODNN7EXAMPLE" not in formatted_traceback
    assert "Basic dXNlcjpwYXNzd29yZA==" not in formatted_traceback
    assert "super-secret-value" not in caplog.text
    assert "AKIAIOSFODNN7EXAMPLE" not in caplog.text
    assert "Basic dXNlcjpwYXNzd29yZA==" not in caplog.text


def test_content_indexer_serializes_concurrent_index_documents_calls():
    indexer = ContentIndexer(
        config=SimpleNamespace(progress_log_interval=1, batch_size=10),
        chroma_collection=None,
        storage_context=None,
    )
    active_calls = 0
    max_active_calls = 0
    release_first = asyncio.Event()
    entered_first = asyncio.Event()

    async def fake_filter(documents):
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        if not entered_first.is_set():
            entered_first.set()
            await release_first.wait()
        await asyncio.sleep(0)
        active_calls -= 1
        return {"documents": [], "new": 0, "updated": 0}

    indexer._filter_documents = fake_filter
    documents = [
        DocumentModel(
            id="doc-1",
            title="Doc",
            content="content",
            url="https://example.com",
            platform="web",
        )
    ]

    async def run_two_calls():
        first = asyncio.create_task(indexer.index_documents(documents))
        await entered_first.wait()
        second = asyncio.create_task(indexer.index_documents(documents))
        await asyncio.sleep(0)
        release_first.set()
        await asyncio.gather(first, second)

    asyncio.run(run_two_calls())

    assert max_active_calls == 1
