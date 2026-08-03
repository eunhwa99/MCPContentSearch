import asyncio
import threading
import time

import pytest

from search.context_service import ContextSearchService
from search.retrieval_pipeline import (
    BoundedRetrievalExecutor,
    RetrievalDeadlineExceeded,
)


pytestmark = pytest.mark.unit


class _EmptyMetadataStore:
    pass


def test_bounded_executor_reuses_one_absolute_deadline_across_steps():
    executor = BoundedRetrievalExecutor(
        timeout_seconds=0.02,
        max_concurrency=1,
    )

    async def scenario():
        deadline = executor.deadline()
        assert await executor.run_until(deadline, lambda: "first") == "first"
        await asyncio.sleep(0.03)
        with pytest.raises(RetrievalDeadlineExceeded):
            await executor.run_until(deadline, lambda: "too late")

    asyncio.run(scenario())


def test_cancelled_run_until_holds_shared_slot_until_worker_finishes():
    executor = BoundedRetrievalExecutor(
        timeout_seconds=1,
        max_concurrency=1,
    )
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()

    def first_work():
        first_started.set()
        release_first.wait(timeout=1)

    def second_work():
        second_started.set()

    async def scenario():
        first = asyncio.create_task(executor.run_until(executor.deadline(), first_work))
        assert await asyncio.to_thread(first_started.wait, 0.5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        second = asyncio.create_task(
            executor.run_until(executor.deadline(), second_work)
        )
        await asyncio.sleep(0.05)
        assert not second_started.is_set()

        release_first.set()
        await asyncio.wait_for(second, timeout=1)
        assert second_started.is_set()

    try:
        asyncio.run(scenario())
    finally:
        release_first.set()


def test_sync_retriever_runs_without_blocking_the_event_loop():
    release = threading.Event()

    def blocking_retriever(query, top_k, source_ids):
        del query, top_k, source_ids
        release.wait(timeout=1)
        return []

    service = ContextSearchService(
        _EmptyMetadataStore(),
        retriever=blocking_retriever,
        retrieval_timeout_seconds=1,
        retrieval_max_concurrency=1,
    )

    async def scenario():
        timer = threading.Timer(0.2, release.set)
        timer.start()
        try:
            search_task = asyncio.create_task(
                service.search_context("bounded", top_k=1)
            )
            started_at = time.perf_counter()
            await asyncio.sleep(0.02)
            heartbeat_latency = time.perf_counter() - started_at
            assert heartbeat_latency < 0.1
            assert not search_task.done()
            await asyncio.wait_for(search_task, timeout=1)
        finally:
            release.set()
            timer.cancel()
            timer.join(timeout=1)

    asyncio.run(scenario())


def test_cancellation_keeps_the_slot_until_blocking_retrieval_finishes():
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    def blocking_retriever(query, top_k, source_ids):
        nonlocal call_count
        del query, top_k, source_ids
        with call_lock:
            call_count += 1
            current_call = call_count
        if current_call == 1:
            first_started.set()
            release_first.wait(timeout=1)
        else:
            second_started.set()
        return []

    service = ContextSearchService(
        _EmptyMetadataStore(),
        retriever=blocking_retriever,
        retrieval_timeout_seconds=1,
        retrieval_max_concurrency=1,
    )

    async def scenario():
        first = asyncio.create_task(service.search_context("first", top_k=1))
        assert await asyncio.to_thread(first_started.wait, 0.5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        second = asyncio.create_task(service.search_context("second", top_k=1))
        await asyncio.sleep(0.05)
        assert not second_started.is_set()

        release_first.set()
        await asyncio.wait_for(second, timeout=1)
        assert second_started.is_set()

    try:
        asyncio.run(scenario())
    finally:
        release_first.set()


def test_retrieval_timeout_is_bounded_and_does_not_echo_query():
    release = threading.Event()

    def blocking_retriever(query, top_k, source_ids):
        del query, top_k, source_ids
        release.wait(timeout=1)
        return []

    service = ContextSearchService(
        _EmptyMetadataStore(),
        retriever=blocking_retriever,
        retrieval_timeout_seconds=0.02,
        retrieval_max_concurrency=1,
    )

    timer = threading.Timer(0.1, release.set)
    timer.start()
    try:
        with pytest.raises(TimeoutError) as exc_info:
            asyncio.run(
                service.search_context(
                    "private retrieval query must not be echoed",
                    top_k=1,
                )
            )
    finally:
        release.set()
        timer.cancel()
        timer.join(timeout=1)

    assert "private retrieval query" not in str(exc_info.value)
