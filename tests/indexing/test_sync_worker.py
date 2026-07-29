import asyncio

import pytest

from core.models import SourceModel, SourceType, SyncJobStatus
from fetching.connectors import SourceConnector, SourceRegistry
from indexing.chunker import DocumentChunker
from indexing.ingestion_service import IngestionService, WORKER_STOPPED_SYNC_ERROR
from indexing.sync_worker import SyncWorker
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.integration


class FakeIndexer:
    async def index_documents(self, documents):
        return None


class EmptyConnector(SourceConnector):
    supports_stale_cleanup = True

    def __init__(self):
        self.source = SourceModel(
            source_id="source_fake",
            source_type=SourceType.NOTION,
            name="Fake",
            enabled=True,
        )

    async def fetch_documents(self):
        return []


class BlockingConnector(EmptyConnector):
    def __init__(self, started: asyncio.Event, release: asyncio.Event):
        super().__init__()
        self.started = started
        self.release = release

    async def fetch_documents(self):
        self.started.set()
        await self.release.wait()
        return []


def _service(db_path, connector, *, owner_id):
    store = MetadataStore(db_path, sync_owner_id=owner_id)
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(),
        indexer=FakeIndexer(),
    )
    return store, service


def test_worker_claims_and_completes_exact_queued_job(tmp_path):
    db_path = tmp_path / "contextwiki.sqlite3"
    requester, requester_service = _service(
        db_path,
        EmptyConnector(),
        owner_id="requester",
    )
    queued = asyncio.run(requester_service.enqueue_sync_source("source_fake"))
    worker_store, worker_service = _service(
        db_path,
        EmptyConnector(),
        owner_id="worker",
    )
    worker = SyncWorker(
        worker_service,
        worker_store,
        source_ids=("source_fake",),
        poll_interval_seconds=0.1,
    )

    completed = asyncio.run(worker.run_once())

    assert completed.job_id == queued.job_id
    assert completed.status == SyncJobStatus.SUCCEEDED
    assert requester.get_latest_sync_job("source_fake").job_id == queued.job_id


def test_graceful_worker_stop_fails_in_flight_job(tmp_path):
    async def scenario():
        db_path = tmp_path / "contextwiki.sqlite3"
        requester, requester_service = _service(
            db_path,
            EmptyConnector(),
            owner_id="requester",
        )
        queued = await requester_service.enqueue_sync_source("source_fake")
        started = asyncio.Event()
        release = asyncio.Event()
        worker_store, worker_service = _service(
            db_path,
            BlockingConnector(started, release),
            owner_id="worker",
        )
        worker = SyncWorker(
            worker_service,
            worker_store,
            source_ids=("source_fake",),
            poll_interval_seconds=0.1,
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
