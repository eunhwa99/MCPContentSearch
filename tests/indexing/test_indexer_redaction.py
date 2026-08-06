import asyncio
import threading
import time
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


def test_content_indexer_logs_sanitized_failure_stage_and_trace_frames(caplog):
    indexer = ContentIndexer(
        config=SimpleNamespace(progress_log_interval=1),
        chroma_collection=None,
        storage_context=None,
    )

    def nested_failure():
        raise RuntimeError("filter failed token=super-secret-value")

    async def fail_filter(documents):
        nested_failure()

    indexer._filter_documents = fail_filter
    documents = [
        DocumentModel(
            id="doc-1",
            title="Doc",
            content="private document body must not be logged",
            url="https://example.com",
            platform="web",
        )
    ]

    with caplog.at_level("ERROR", logger="indexing.indexer"):
        with pytest.raises(IndexingError):
            asyncio.run(indexer.index_documents(documents))

    assert "indexing_stage=filter_documents" in caplog.text
    assert "trace_frames=" in caplog.text
    assert "nested_failure" in caplog.text
    assert "token=<redacted>" in caplog.text
    assert "super-secret-value" not in caplog.text
    assert "private document body" not in caplog.text
    assert "/Users/eunhwa" not in caplog.text


def test_chroma_worker_logs_sanitized_operation_and_trace_frames(caplog):
    indexer = ContentIndexer(
        config=SimpleNamespace(progress_log_interval=1, batch_size=10),
        chroma_collection=None,
        storage_context=None,
    )

    def fail_from_documents(batch, storage_context=None, show_progress=True):
        raise RuntimeError("embedding failed token=super-secret-value")

    with caplog.at_level("ERROR", logger="indexing.indexer"):
        with pytest.raises(RuntimeError):
            asyncio.run(
                indexer._run_chroma_in_thread(
                    fail_from_documents,
                    [SimpleNamespace(text="private document body must not be logged")],
                    operation="vector_store_from_documents",
                )
            )

    assert "indexing_operation=vector_store_from_documents" in caplog.text
    assert "trace_frames=" in caplog.text
    assert "fail_from_documents" in caplog.text
    assert "token=<redacted>" in caplog.text
    assert "super-secret-value" not in caplog.text
    assert "private document body" not in caplog.text
    assert "/Users/eunhwa" not in caplog.text


def test_batch_index_failure_logs_single_sanitized_diagnostic(caplog):
    indexer = ContentIndexer(
        config=SimpleNamespace(progress_log_interval=1, batch_size=10),
        chroma_collection=None,
        storage_context=None,
    )

    def fail_from_documents(batch, storage_context=None, show_progress=True):
        raise RuntimeError("embedding failed token=super-secret-value")

    async def fake_filter(documents):
        return {
            "documents": [SimpleNamespace(text="private document body must not be logged")],
            "new": 1,
            "updated": 0,
        }

    import indexing.indexer as indexer_module

    indexer._filter_documents = fake_filter
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(indexer_module.VectorStoreIndex, "from_documents", fail_from_documents)
    try:
        with caplog.at_level("ERROR", logger="indexing.indexer"):
            with pytest.raises(IndexingError):
                asyncio.run(
                    indexer.index_documents(
                        [
                            DocumentModel(
                                id="doc-1",
                                title="Doc",
                                content="private document body must not be logged",
                                url="https://example.com",
                                platform="web",
                            )
                        ]
                    )
                )
    finally:
        monkeypatch.undo()

    indexing_errors = [
        record for record in caplog.records if record.message.startswith("Indexing error:")
    ]
    assert len(indexing_errors) == 1
    assert "indexing_operation=vector_store_from_documents" in caplog.text
    assert "token=<redacted>" in caplog.text
    assert "super-secret-value" not in caplog.text
    assert "private document body" not in caplog.text


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


def test_batch_index_offloads_blocking_chroma_work_so_event_loop_can_progress(
    monkeypatch,
):
    import indexing.indexer as indexer_module

    indexer = ContentIndexer(
        config=SimpleNamespace(progress_log_interval=1, batch_size=10),
        chroma_collection=None,
        storage_context=None,
    )
    peer_progressed = asyncio.Event()
    entered_batch = asyncio.Event()

    def blocking_from_documents(batch, storage_context=None, show_progress=True):
        entered_batch.set()
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            if peer_progressed.is_set():
                return object()
            time.sleep(0.01)
        raise AssertionError(
            "event loop did not progress a peer task during blocking Chroma work"
        )

    monkeypatch.setattr(
        indexer_module.VectorStoreIndex,
        "from_documents",
        staticmethod(blocking_from_documents),
    )

    async def scenario():
        async def peer():
            await entered_batch.wait()
            peer_progressed.set()

        peer_task = asyncio.create_task(peer())
        await indexer._batch_index([SimpleNamespace(text="content")])
        await peer_task

    asyncio.run(scenario())


def test_batch_index_shields_chroma_thread_so_cancel_keeps_mutation_lock(
    monkeypatch,
):
    import indexing.indexer as indexer_module

    indexer = ContentIndexer(
        config=SimpleNamespace(progress_log_interval=1, batch_size=10),
        chroma_collection=None,
        storage_context=None,
    )
    entered_batch = asyncio.Event()
    release_batch = threading.Event()

    def blocking_from_documents(batch, storage_context=None, show_progress=True):
        entered_batch.set()
        assert release_batch.wait(timeout=2)
        return object()

    monkeypatch.setattr(
        indexer_module.VectorStoreIndex,
        "from_documents",
        staticmethod(blocking_from_documents),
    )

    async def scenario():
        task = asyncio.create_task(
            indexer.index_documents(
                [
                    DocumentModel(
                        id="doc-1",
                        title="Doc",
                        content="content",
                        url="https://example.com",
                        platform="web",
                    )
                ]
            )
        )
        await entered_batch.wait()
        task.cancel()
        await asyncio.sleep(0)
        # Lock must remain held until the shielded Chroma thread finishes.
        acquired_during_write = False
        try:
            await asyncio.wait_for(indexer._mutation_lock.acquire(), timeout=0.05)
            acquired_during_write = True
            indexer._mutation_lock.release()
        except TimeoutError:
            pass
        release_batch.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not acquired_during_write

    async def fake_filter(documents):
        from llama_index.core import Document

        return {
            "documents": [Document(text="content")],
            "new": 1,
            "updated": 0,
        }

    indexer._filter_documents = fake_filter
    asyncio.run(scenario())


def test_batch_index_join_survives_double_cancel_while_chroma_thread_runs(
    monkeypatch,
):
    import indexing.indexer as indexer_module

    indexer = ContentIndexer(
        config=SimpleNamespace(progress_log_interval=1, batch_size=10),
        chroma_collection=None,
        storage_context=None,
    )
    entered_batch = asyncio.Event()
    release_batch = threading.Event()

    def blocking_from_documents(batch, storage_context=None, show_progress=True):
        entered_batch.set()
        assert release_batch.wait(timeout=2)
        return object()

    monkeypatch.setattr(
        indexer_module.VectorStoreIndex,
        "from_documents",
        staticmethod(blocking_from_documents),
    )

    async def scenario():
        task = asyncio.create_task(
            indexer.index_documents(
                [
                    DocumentModel(
                        id="doc-1",
                        title="Doc",
                        content="content",
                        url="https://example.com",
                        platform="web",
                    )
                ]
            )
        )
        await entered_batch.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        acquired_during_write = False
        try:
            await asyncio.wait_for(indexer._mutation_lock.acquire(), timeout=0.05)
            acquired_during_write = True
            indexer._mutation_lock.release()
        except TimeoutError:
            pass
        release_batch.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not acquired_during_write

    async def fake_filter(documents):
        from llama_index.core import Document

        return {
            "documents": [Document(text="content")],
            "new": 1,
            "updated": 0,
        }

    indexer._filter_documents = fake_filter
    asyncio.run(scenario())
