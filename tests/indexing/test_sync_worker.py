import argparse
import asyncio

import pytest

from core.models import SourceModel, SourceType, SyncJobStatus
from fetching.connectors import SourceConnector, SourceRegistry
from indexing.chunker import DocumentChunker
from indexing.ingestion_service import IngestionService, WORKER_STOPPED_SYNC_ERROR
from indexing import sync_worker as sync_worker_module
from indexing.sync_worker import SyncWorker
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.integration


class FakeIndexer:
    async def index_documents(self, documents):
        return None


class EmptyConnector(SourceConnector):
    supports_stale_cleanup = True

    def __init__(self, source_id: str = "source_fake"):
        self.source = SourceModel(
            source_id=source_id,
            source_type=SourceType.NOTION,
            name=source_id,
            enabled=True,
        )

    async def fetch_documents(self):
        return []


class BlockingConnector(EmptyConnector):
    def __init__(
        self,
        started: asyncio.Event,
        release: asyncio.Event,
        source_id: str = "source_fake",
    ):
        super().__init__(source_id=source_id)
        self.started = started
        self.release = release

    async def fetch_documents(self):
        self.started.set()
        await self.release.wait()
        return []


def _service(db_path, connector, *, owner_id, max_concurrent_sync_jobs: int = 1):
    store = MetadataStore(
        db_path,
        sync_owner_id=owner_id,
        max_concurrent_sync_jobs=max_concurrent_sync_jobs,
    )
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(),
        indexer=FakeIndexer(),
    )
    return store, service


def _multi_service(
    db_path,
    connectors,
    *,
    owner_id,
    max_concurrent_sync_jobs: int,
):
    store = MetadataStore(
        db_path,
        sync_owner_id=owner_id,
        max_concurrent_sync_jobs=max_concurrent_sync_jobs,
    )
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry(list(connectors)),
        chunker=DocumentChunker(),
        indexer=FakeIndexer(),
    )
    return store, service


def _max_concurrent_parse():
    parse = getattr(sync_worker_module, "_max_concurrent_jobs", None)
    assert callable(parse), (
        "indexing.sync_worker must expose _max_concurrent_jobs for "
        "CONTEXTZIP_SYNC_WORKER_MAX_CONCURRENT parsing"
    )
    return parse


async def _wait_for_terminal_job(store, job_id: str, *, timeout: float = 2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        job = store.get_sync_job(job_id)
        if job is not None and job.status in {
            SyncJobStatus.SUCCEEDED,
            SyncJobStatus.FAILED,
        }:
            return job
        await asyncio.sleep(0.02)
    raise TimeoutError(job_id)


def test_worker_claims_and_completes_exact_queued_job(tmp_path):
    db_path = tmp_path / "context_zip.sqlite3"
    requester, requester_service = _service(
        db_path,
        EmptyConnector(),
        owner_id="requester",
        max_concurrent_sync_jobs=1,
    )
    queued = asyncio.run(requester_service.enqueue_sync_source("source_fake"))
    worker_store, worker_service = _service(
        db_path,
        EmptyConnector(),
        owner_id="worker",
        max_concurrent_sync_jobs=1,
    )
    worker = SyncWorker(
        worker_service,
        worker_store,
        source_ids=("source_fake",),
        poll_interval_seconds=0.1,
        max_concurrent_jobs=1,
    )

    completed = asyncio.run(worker.run_once())

    assert completed.job_id == queued.job_id
    assert completed.status == SyncJobStatus.SUCCEEDED
    assert requester.get_latest_sync_job("source_fake").job_id == queued.job_id


def test_graceful_worker_stop_fails_in_flight_job(tmp_path):
    async def scenario():
        db_path = tmp_path / "context_zip.sqlite3"
        requester, requester_service = _service(
            db_path,
            EmptyConnector(),
            owner_id="requester",
            max_concurrent_sync_jobs=1,
        )
        queued = await requester_service.enqueue_sync_source("source_fake")
        started = asyncio.Event()
        release = asyncio.Event()
        worker_store, worker_service = _service(
            db_path,
            BlockingConnector(started, release),
            owner_id="worker",
            max_concurrent_sync_jobs=1,
        )
        worker = SyncWorker(
            worker_service,
            worker_store,
            source_ids=("source_fake",),
            poll_interval_seconds=0.1,
            max_concurrent_jobs=1,
        )
        stop_event = asyncio.Event()
        worker_task = asyncio.create_task(worker.run(stop_event))

        await asyncio.wait_for(started.wait(), timeout=1)
        stop_event.set()
        await asyncio.wait_for(worker_task, timeout=1)
        return queued, requester.get_sync_job(queued.job_id)

    queued, failed = asyncio.run(scenario())

    assert failed.job_id == queued.job_id
    assert failed.status == SyncJobStatus.FAILED
    assert failed.error_message == WORKER_STOPPED_SYNC_ERROR


def test_worker_runs_two_claimed_jobs_concurrently_when_max_concurrent_is_two(tmp_path):
    async def scenario():
        db_path = tmp_path / "context_zip.sqlite3"
        source_ids = ("source_a", "source_b")
        started = {source_id: asyncio.Event() for source_id in source_ids}
        release = {source_id: asyncio.Event() for source_id in source_ids}
        requester, requester_service = _multi_service(
            db_path,
            [EmptyConnector(source_id) for source_id in source_ids],
            owner_id="requester",
            max_concurrent_sync_jobs=2,
        )
        queued = [
            await requester_service.enqueue_sync_source(source_id)
            for source_id in source_ids
        ]
        worker_store, worker_service = _multi_service(
            db_path,
            [
                BlockingConnector(started[source_id], release[source_id], source_id)
                for source_id in source_ids
            ],
            owner_id="worker",
            max_concurrent_sync_jobs=2,
        )
        worker = SyncWorker(
            worker_service,
            worker_store,
            source_ids=source_ids,
            poll_interval_seconds=0.05,
            max_concurrent_jobs=2,
        )
        stop_event = asyncio.Event()
        worker_task = asyncio.create_task(worker.run(stop_event))

        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in started.values())),
            timeout=2,
        )
        overlapping_running = sum(
            1
            for source_id in source_ids
            if requester.get_latest_sync_job(source_id).status == SyncJobStatus.RUNNING
        )
        for event in release.values():
            event.set()
        terminal = await asyncio.gather(
            *(_wait_for_terminal_job(requester, job.job_id) for job in queued)
        )
        stop_event.set()
        await asyncio.wait_for(worker_task, timeout=2)
        return overlapping_running, terminal

    overlapping_running, terminal = asyncio.run(scenario())

    assert overlapping_running == 2
    assert all(job.status == SyncJobStatus.SUCCEEDED for job in terminal)


def test_graceful_worker_stop_fails_all_in_flight_jobs_when_max_concurrent_is_two(
    tmp_path,
):
    async def scenario():
        db_path = tmp_path / "context_zip.sqlite3"
        source_ids = ("source_a", "source_b")
        started = {source_id: asyncio.Event() for source_id in source_ids}
        release = {source_id: asyncio.Event() for source_id in source_ids}
        requester, requester_service = _multi_service(
            db_path,
            [EmptyConnector(source_id) for source_id in source_ids],
            owner_id="requester",
            max_concurrent_sync_jobs=2,
        )
        queued = [
            await requester_service.enqueue_sync_source(source_id)
            for source_id in source_ids
        ]
        worker_store, worker_service = _multi_service(
            db_path,
            [
                BlockingConnector(started[source_id], release[source_id], source_id)
                for source_id in source_ids
            ],
            owner_id="worker",
            max_concurrent_sync_jobs=2,
        )
        worker = SyncWorker(
            worker_service,
            worker_store,
            source_ids=source_ids,
            poll_interval_seconds=0.05,
            max_concurrent_jobs=2,
        )
        stop_event = asyncio.Event()
        worker_task = asyncio.create_task(worker.run(stop_event))

        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in started.values())),
            timeout=2,
        )
        stop_event.set()
        await asyncio.wait_for(worker_task, timeout=2)
        return [requester.get_sync_job(job.job_id) for job in queued]

    failed_jobs = asyncio.run(scenario())

    assert len(failed_jobs) == 2
    assert all(job is not None for job in failed_jobs)
    assert all(job.status == SyncJobStatus.FAILED for job in failed_jobs)
    assert all(job.error_message == WORKER_STOPPED_SYNC_ERROR for job in failed_jobs)


@pytest.mark.parametrize("value", (1, 2, 8, "1", "2", "8"))
def test_max_concurrent_jobs_parse_accepts_bounds(value):
    parse = _max_concurrent_parse()
    assert parse(value) == int(value)


@pytest.mark.parametrize("value", (0, 9, -1, "0", "9", "abc", "true", "2.5", ""))
def test_max_concurrent_jobs_parse_fails_closed_for_invalid_values(value):
    parse = _max_concurrent_parse()
    with pytest.raises((ValueError, argparse.ArgumentTypeError)):
        parse(value)


def test_max_concurrent_jobs_env_default_is_two(monkeypatch):
    monkeypatch.delenv("CONTEXTZIP_SYNC_WORKER_MAX_CONCURRENT", raising=False)
    default_parse = getattr(sync_worker_module, "_default_max_concurrent_jobs", None)
    assert callable(default_parse), (
        "indexing.sync_worker must expose _default_max_concurrent_jobs"
    )
    assert default_parse() == 2


def test_sync_worker_rejects_invalid_max_concurrent_jobs(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3", sync_owner_id="worker")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([EmptyConnector()]),
        chunker=DocumentChunker(),
        indexer=FakeIndexer(),
    )
    with pytest.raises(ValueError, match="max_concurrent"):
        SyncWorker(
            service,
            store,
            source_ids=("source_fake",),
            poll_interval_seconds=0.1,
            max_concurrent_jobs=0,
        )


def test_cancelled_claimed_jobs_finalize_failed_when_ingestion_never_finalizes(
    tmp_path,
):
    """Cancel before ingestion finalize must not leave claimed jobs RUNNING."""

    async def scenario():
        db_path = tmp_path / "context_zip.sqlite3"
        source_ids = ("source_a", "source_b")
        started = {source_id: asyncio.Event() for source_id in source_ids}
        release = asyncio.Event()
        requester, requester_service = _multi_service(
            db_path,
            [EmptyConnector(source_id) for source_id in source_ids],
            owner_id="requester",
            max_concurrent_sync_jobs=2,
        )
        queued = [
            await requester_service.enqueue_sync_source(source_id)
            for source_id in source_ids
        ]
        job_by_id = {job.job_id: job for job in queued}
        worker_store, worker_service = _multi_service(
            db_path,
            [EmptyConnector(source_id) for source_id in source_ids],
            owner_id="worker",
            max_concurrent_sync_jobs=2,
        )

        async def never_finalize_run_claimed_sync_job(job_id: str):
            job = job_by_id[job_id]
            started[job.source_id].set()
            await release.wait()
            raise AssertionError("release must not be set before worker stop")

        worker_service.run_claimed_sync_job = never_finalize_run_claimed_sync_job
        worker = SyncWorker(
            worker_service,
            worker_store,
            source_ids=source_ids,
            poll_interval_seconds=0.05,
            max_concurrent_jobs=2,
        )
        stop_event = asyncio.Event()
        worker_task = asyncio.create_task(worker.run(stop_event))

        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in started.values())),
            timeout=2,
        )
        stop_event.set()
        await asyncio.wait_for(worker_task, timeout=2)
        return [requester.get_sync_job(job.job_id) for job in queued]

    failed_jobs = asyncio.run(scenario())

    assert len(failed_jobs) == 2
    assert all(job is not None for job in failed_jobs)
    assert all(job.status == SyncJobStatus.FAILED for job in failed_jobs), [
        (job.job_id, job.status) for job in failed_jobs
    ]
    assert all(job.error_message == WORKER_STOPPED_SYNC_ERROR for job in failed_jobs)


def test_sync_worker_aligns_store_claim_budget_when_store_defaults_to_one(tmp_path):
    async def scenario():
        db_path = tmp_path / "context_zip.sqlite3"
        source_ids = ("source_a", "source_b")
        started = {source_id: asyncio.Event() for source_id in source_ids}
        release = {source_id: asyncio.Event() for source_id in source_ids}
        requester_store = MetadataStore(db_path, sync_owner_id="requester")
        assert requester_store.max_concurrent_sync_jobs == 1
        requester_service = IngestionService(
            metadata_store=requester_store,
            source_registry=SourceRegistry(
                [EmptyConnector(source_id) for source_id in source_ids]
            ),
            chunker=DocumentChunker(),
            indexer=FakeIndexer(),
        )
        for source_id in source_ids:
            await requester_service.enqueue_sync_source(source_id)

        worker_store = MetadataStore(db_path, sync_owner_id="worker")
        assert worker_store.max_concurrent_sync_jobs == 1
        worker_service = IngestionService(
            metadata_store=worker_store,
            source_registry=SourceRegistry(
                [
                    BlockingConnector(
                        started[source_id],
                        release[source_id],
                        source_id,
                    )
                    for source_id in source_ids
                ]
            ),
            chunker=DocumentChunker(),
            indexer=FakeIndexer(),
        )
        worker = SyncWorker(
            worker_service,
            worker_store,
            source_ids=source_ids,
            poll_interval_seconds=0.05,
            max_concurrent_jobs=2,
        )

        stop_event = asyncio.Event()
        worker_task = asyncio.create_task(worker.run(stop_event))
        try:
            await asyncio.wait_for(
                asyncio.gather(*(event.wait() for event in started.values())),
                timeout=1,
            )
        except TimeoutError:
            # Expected RED path when the store claim budget stays at 1.
            pass
        await asyncio.sleep(0.05)
        overlapping_running = sum(
            1
            for source_id in source_ids
            if requester_store.get_latest_sync_job(source_id).status
            == SyncJobStatus.RUNNING
        )
        for event in release.values():
            event.set()
        stop_event.set()
        await asyncio.wait_for(worker_task, timeout=2)
        return overlapping_running, worker_store.max_concurrent_sync_jobs

    overlapping_running, store_budget = asyncio.run(scenario())

    assert store_budget == 2
    assert overlapping_running == 2


def test_create_worker_wires_env_max_concurrent_to_worker_and_store(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CONTEXTZIP_SYNC_WORKER_MAX_CONCURRENT", "3")
    store = MetadataStore(tmp_path / "context_zip.sqlite3", sync_owner_id="runtime")
    assert store.max_concurrent_sync_jobs == 1

    class FakeConfig:
        github_token_env_var = "GITHUB_TOKEN"

    class FakeRuntime:
        def __init__(self):
            self.ingestion_service = object()
            self.metadata_store = store
            self.retained_source_ids = ("source_notion",)

    monkeypatch.setattr(
        sync_worker_module,
        "build_ingestion_runtime",
        lambda **_kwargs: FakeRuntime(),
    )
    monkeypatch.setattr(sync_worker_module, "AppConfig", FakeConfig)
    monkeypatch.setattr(sync_worker_module, "NOTION_API_KEY", "")
    monkeypatch.setattr(sync_worker_module, "TISTORY_BLOG_NAME", "")
    monkeypatch.setattr(
        sync_worker_module,
        "get_env_secret",
        lambda *_args, **_kwargs: "",
    )

    worker = sync_worker_module.create_worker(poll_interval_seconds=0.1)

    assert worker.max_concurrent_jobs == 3
    assert worker.metadata_store.max_concurrent_sync_jobs == 3
    assert store.max_concurrent_sync_jobs == 3


def test_worker_reclaims_when_slot_frees_with_three_sources_and_max_two(tmp_path):
    async def scenario():
        db_path = tmp_path / "context_zip.sqlite3"
        source_ids = ("source_a", "source_b", "source_c")
        started = {source_id: asyncio.Event() for source_id in source_ids}
        release = {source_id: asyncio.Event() for source_id in source_ids}
        requester, requester_service = _multi_service(
            db_path,
            [EmptyConnector(source_id) for source_id in source_ids],
            owner_id="requester",
            max_concurrent_sync_jobs=2,
        )
        queued = [
            await requester_service.enqueue_sync_source(source_id)
            for source_id in source_ids
        ]
        worker_store, worker_service = _multi_service(
            db_path,
            [
                BlockingConnector(started[source_id], release[source_id], source_id)
                for source_id in source_ids
            ],
            owner_id="worker",
            max_concurrent_sync_jobs=2,
        )
        worker = SyncWorker(
            worker_service,
            worker_store,
            source_ids=source_ids,
            poll_interval_seconds=0.05,
            max_concurrent_jobs=2,
        )
        stop_event = asyncio.Event()
        worker_task = asyncio.create_task(worker.run(stop_event))

        await asyncio.wait_for(
            asyncio.gather(started["source_a"].wait(), started["source_b"].wait()),
            timeout=2,
        )
        assert not started["source_c"].is_set()
        assert (
            requester.get_latest_sync_job("source_c").status == SyncJobStatus.QUEUED
        )

        # Free one slot; a no-refill loop would keep waiting on B and never start C.
        release["source_a"].set()
        await asyncio.wait_for(started["source_c"].wait(), timeout=2)
        assert (
            requester.get_latest_sync_job("source_b").status == SyncJobStatus.RUNNING
        )
        assert (
            requester.get_latest_sync_job("source_c").status == SyncJobStatus.RUNNING
        )
        assert (
            requester.get_latest_sync_job("source_a").status == SyncJobStatus.SUCCEEDED
        )

        release["source_b"].set()
        release["source_c"].set()
        terminal = await asyncio.gather(
            *(_wait_for_terminal_job(requester, job.job_id) for job in queued)
        )
        stop_event.set()
        await asyncio.wait_for(worker_task, timeout=2)
        return terminal

    terminal = asyncio.run(scenario())

    assert len(terminal) == 3
    assert all(job.status == SyncJobStatus.SUCCEEDED for job in terminal)


def test_unexpected_task_cancel_drains_sibling_in_flight_jobs(tmp_path):
    async def scenario():
        db_path = tmp_path / "context_zip.sqlite3"
        source_ids = ("source_a", "source_b")
        started = {source_id: asyncio.Event() for source_id in source_ids}
        release = {source_id: asyncio.Event() for source_id in source_ids}
        requester, requester_service = _multi_service(
            db_path,
            [EmptyConnector(source_id) for source_id in source_ids],
            owner_id="requester",
            max_concurrent_sync_jobs=2,
        )
        queued = [
            await requester_service.enqueue_sync_source(source_id)
            for source_id in source_ids
        ]
        worker_store, worker_service = _multi_service(
            db_path,
            [
                BlockingConnector(started[source_id], release[source_id], source_id)
                for source_id in source_ids
            ],
            owner_id="worker",
            max_concurrent_sync_jobs=2,
        )
        worker = SyncWorker(
            worker_service,
            worker_store,
            source_ids=source_ids,
            poll_interval_seconds=0.05,
            max_concurrent_jobs=2,
        )
        stop_event = asyncio.Event()
        worker_task = asyncio.create_task(worker.run(stop_event))

        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in started.values())),
            timeout=2,
        )

        # Cancel run() without stop_event: must still drain/finalize all in-flight.
        worker_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker_task

        deadline = asyncio.get_running_loop().time() + 1
        while asyncio.get_running_loop().time() < deadline:
            jobs = [requester.get_sync_job(job.job_id) for job in queued]
            if all(
                job is not None and job.status == SyncJobStatus.FAILED for job in jobs
            ):
                break
            await asyncio.sleep(0.02)
        else:
            jobs = [requester.get_sync_job(job.job_id) for job in queued]

        # Unblock any orphaned children so the event loop can settle.
        for event in release.values():
            event.set()
        await asyncio.sleep(0.05)
        return jobs, stop_event.is_set()

    failed_jobs, stop_was_set = asyncio.run(scenario())

    assert stop_was_set is False
    assert len(failed_jobs) == 2
    assert all(job is not None for job in failed_jobs)
    assert all(job.status == SyncJobStatus.FAILED for job in failed_jobs), [
        (job.job_id, job.status) for job in failed_jobs
    ]
    assert all(job.error_message == WORKER_STOPPED_SYNC_ERROR for job in failed_jobs)


def test_cancelling_one_in_flight_child_drains_sibling_jobs(tmp_path):
    async def scenario():
        db_path = tmp_path / "context_zip.sqlite3"
        source_ids = ("source_a", "source_b")
        started = {source_id: asyncio.Event() for source_id in source_ids}
        release = {source_id: asyncio.Event() for source_id in source_ids}
        requester, requester_service = _multi_service(
            db_path,
            [EmptyConnector(source_id) for source_id in source_ids],
            owner_id="requester",
            max_concurrent_sync_jobs=2,
        )
        queued = [
            await requester_service.enqueue_sync_source(source_id)
            for source_id in source_ids
        ]
        worker_store, worker_service = _multi_service(
            db_path,
            [
                BlockingConnector(started[source_id], release[source_id], source_id)
                for source_id in source_ids
            ],
            owner_id="worker",
            max_concurrent_sync_jobs=2,
        )
        worker = SyncWorker(
            worker_service,
            worker_store,
            source_ids=source_ids,
            poll_interval_seconds=0.05,
            max_concurrent_jobs=2,
        )
        stop_event = asyncio.Event()
        worker_task = asyncio.create_task(worker.run(stop_event))

        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in started.values())),
            timeout=2,
        )
        child_tasks = [
            task
            for task in asyncio.all_tasks()
            if task.get_name().startswith("durable-sync:")
        ]
        assert len(child_tasks) == 2
        child_tasks[0].cancel()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker_task, timeout=2)

        deadline = asyncio.get_running_loop().time() + 1
        while asyncio.get_running_loop().time() < deadline:
            jobs = [requester.get_sync_job(job.job_id) for job in queued]
            if all(
                job is not None and job.status == SyncJobStatus.FAILED for job in jobs
            ):
                break
            await asyncio.sleep(0.02)
        else:
            jobs = [requester.get_sync_job(job.job_id) for job in queued]

        for event in release.values():
            event.set()
        await asyncio.sleep(0.05)
        return jobs, stop_event.is_set()

    failed_jobs, stop_was_set = asyncio.run(scenario())

    assert stop_was_set is False
    assert len(failed_jobs) == 2
    assert all(job is not None for job in failed_jobs)
    assert all(job.status == SyncJobStatus.FAILED for job in failed_jobs), [
        (job.job_id, job.status) for job in failed_jobs
    ]
    assert all(job.error_message == WORKER_STOPPED_SYNC_ERROR for job in failed_jobs)
