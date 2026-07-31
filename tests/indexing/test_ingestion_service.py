import asyncio
import inspect
import logging

import pytest

import indexing.ingestion_service as ingestion_module
from core.models import ChunkModel, DocumentModel, SourceModel, SourceType, SyncJobStatus, SyncStatus
from core.utils import ContentHasher
from environments.config import AppConfig
from indexing.chunker import DocumentChunker
from indexing.ingestion_service import IngestionService
from fetching.connectors import GitHubSourceConnector, SourceConnector, SourceRegistry
from storage.metadata_store import MetadataStore


class FakeConnector(SourceConnector):
    supports_stale_cleanup = True
    source = SourceModel(
        source_id="source_fake",
        source_type=SourceType.NOTION,
        name="Fake Notion",
        enabled=True,
        auth_ref="env:FAKE",
        sync_status=SyncStatus.IDLE,
    )

    def __init__(self, documents=None, error=None):
        self.documents = documents or []
        self.error = error

    async def fetch_documents(self):
        if self.error:
            raise self.error
        return self.documents


class PartialSnapshotConnector(FakeConnector):
    supports_stale_cleanup = False


class ScopedCleanupConnector(FakeConnector):
    cleanup_document_id_prefixes = ("github:eunhwa99/mcpcontentsearch:",)


class ProgressRecordingMetadataStore(MetadataStore):
    def __init__(self, db_path):
        super().__init__(db_path)
        self.progress_updates = []

    def update_sync_job(self, job_id: str, **updates):
        if {
            "total_documents",
            "processed_documents",
            "indexed_chunks",
            "skipped_documents",
        }.intersection(updates):
            self.progress_updates.append(dict(updates))
        return super().update_sync_job(job_id, **updates)


class TouchRecordingMetadataStore(ProgressRecordingMetadataStore):
    def __init__(self, db_path):
        super().__init__(db_path)
        self.touched_job_ids = []

    def touch_sync_job(self, job_id: str):
        self.touched_job_ids.append(job_id)
        return super().touch_sync_job(job_id)


class FailingProgressMetadataStore(MetadataStore):
    def update_sync_job(self, job_id: str, **updates):
        raise RuntimeError("progress failed with token=secret-value")


class HintFailingMetadataStore(MetadataStore):
    def update_sync_job(self, job_id: str, **updates):
        if {
            "phase",
            "upstream_total",
            "upstream_done",
            "last_progress_at",
            "status_message",
        }.intersection(updates):
            raise RuntimeError("hint progress failed with token=secret-value")
        return super().update_sync_job(job_id, **updates)


class SecondTouchFailingMetadataStore(MetadataStore):
    def __init__(self, db_path):
        super().__init__(db_path)
        self.touch_calls = 0

    def touch_sync_job(self, job_id: str):
        self.touch_calls += 1
        if self.touch_calls == 2:
            raise RuntimeError("touch failed with token=secret-value")
        return super().touch_sync_job(job_id)


class SourceAConnector(FakeConnector):
    source = SourceModel(
        source_id="source_a",
        source_type=SourceType.GITHUB,
        name="Source A",
        enabled=True,
        sync_status=SyncStatus.IDLE,
    )


class SourceBConnector(FakeConnector):
    source = SourceModel(
        source_id="source_b",
        source_type=SourceType.GITHUB,
        name="Source B",
        enabled=True,
        sync_status=SyncStatus.IDLE,
    )


class BlockingConnector(FakeConnector):
    def __init__(self, documents, started, release):
        super().__init__(documents)
        self.started = started
        self.release = release
        self.calls = 0

    async def fetch_documents(self):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return self.documents


class DisabledConnector(FakeConnector):
    source = SourceModel(
        source_id="source_disabled",
        source_type=SourceType.NOTION,
        name="Disabled",
        enabled=False,
        auth_ref="env:MISSING",
        sync_status=SyncStatus.IDLE,
    )

    def __init__(self):
        super().__init__([])
        self.called = False

    async def fetch_documents(self):
        self.called = True
        return []


class DisabledSameSourceConnector(DisabledConnector):
    source = FakeConnector.source.model_copy(update={"enabled": False, "name": "Disabled Fake"})


class ProgressAwareConnector(FakeConnector):
    def __init__(self, documents=None, error=None):
        super().__init__(documents, error=error)
        self.progress_callback = None

    async def fetch_documents(self):
        if self.progress_callback is not None:
            await self.progress_callback(
                {
                    "event": "search_completed",
                    "total_pages": len(self.documents),
                }
            )
            for index, document in enumerate(self.documents, 1):
                await self.progress_callback(
                    {
                        "event": "page_fetch_started",
                        "current_page": index,
                        "total_pages": len(self.documents),
                        "page_id": document.document_id or document.id,
                        "title": document.title,
                    }
                )
                await self.progress_callback(
                    {
                        "event": "page_fetch_completed",
                        "current_page": index,
                        "total_pages": len(self.documents),
                        "page_id": document.document_id or document.id,
                        "title": document.title,
                        "elapsed_seconds": 0.25,
                    }
                )
        return await super().fetch_documents()


class DiscoveryProgressConnector(FakeConnector):
    def __init__(self, documents=None):
        super().__init__(documents or [])
        self.progress_callback = None

    async def fetch_documents(self):
        if self.progress_callback is not None:
            await self.progress_callback(
                {
                    "event": "search_started",
                }
            )
            await self.progress_callback(
                {
                    "event": "search_page_batch_completed",
                    "batch_index": 1,
                    "pages_discovered": 42,
                    "has_more": True,
                }
            )
            await self.progress_callback(
                {
                    "event": "search_completed",
                    "total_pages": len(self.documents),
                }
            )
        return await super().fetch_documents()


class ExistingObserverStopConnector(FakeConnector):
    def __init__(self, documents=None):
        super().__init__(documents or [])
        self.external_stop_signal = object()
        self.progress_stop_signal = self.external_stop_signal
        self.progress_callback = self._observer
        self.stop_requested = False

    async def _observer(self, event):
        if event.get("event") == "search_started":
            self.stop_requested = True
            return self.external_stop_signal
        return None

    async def fetch_documents(self):
        if self.progress_callback is not None:
            result = await self.progress_callback({"event": "search_started"})
            if result is getattr(self, "progress_stop_signal", None):
                return []
        return await super().fetch_documents()


class _ResolvedAwaitable:
    def __init__(self, value):
        self.value = value

    def __await__(self):
        async def _resolve():
            return self.value

        return _resolve().__await__()


class AwaitableObserverStopConnector(ExistingObserverStopConnector):
    def _observer(self, event):
        if event.get("event") == "search_started":
            self.stop_requested = True
            return _ResolvedAwaitable(self.external_stop_signal)
        return None


class CallbackOnlyObserverStopConnector(FakeConnector):
    def __init__(self, documents=None):
        super().__init__(documents or [])
        self.stop_requested = False
        self.progress_stop_signal = True
        self.progress_callback = self._observer

    async def _observer(self, event):
        if event.get("event") == "search_started":
            self.stop_requested = True
            return True
        return None

    async def fetch_documents(self):
        if self.progress_callback is not None:
            result = self.progress_callback({"event": "search_started"})
            if inspect.isawaitable(result):
                result = await result
            if result is getattr(self, "progress_stop_signal", None):
                raise ingestion_module._StopRequested
        return await super().fetch_documents()


class AsyncFalseStopCheckerConnector(FakeConnector):
    def __init__(self, documents=None):
        super().__init__(documents or [])
        self.progress_stop_checker = self._stop_checker
        self.stop_checks = 0

    async def _stop_checker(self):
        self.stop_checks += 1
        return False

    async def fetch_documents(self):
        if self.progress_stop_checker is not None:
            result = self.progress_stop_checker()
            if inspect.isawaitable(result):
                result = await result
            if result:
                return []
        return await super().fetch_documents()


class RaisingStopCheckerConnector(FakeConnector):
    def __init__(self, documents=None):
        super().__init__(documents or [])
        self.progress_stop_checker = self._stop_checker
        self.stop_requested = False

    async def _stop_checker(self):
        self.stop_requested = True
        raise ingestion_module._StopRequested

    async def fetch_documents(self):
        if self.progress_stop_checker is not None:
            result = self.progress_stop_checker()
            if inspect.isawaitable(result):
                result = await result
            if result:
                return []
        return await super().fetch_documents()


class ObserverCancelledOnceConnector(FakeConnector):
    def __init__(self, documents=None):
        super().__init__(documents or [])
        self.external_stop_signal = object()
        self.progress_callback = self._observer
        self.progress_stop_signal = self.external_stop_signal
        self.cancel_first_run = True

    async def _observer(self, event):
        if event.get("event") == "search_started" and self.cancel_first_run:
            self.cancel_first_run = False
            return self.external_stop_signal
        return None

    async def fetch_documents(self):
        if self.progress_callback is not None:
            result = self.progress_callback({"event": "search_started"})
            if inspect.isawaitable(result):
                result = await result
            if result is getattr(self, "progress_stop_signal", None):
                raise ingestion_module._StopRequested
        return await super().fetch_documents()


class LeaseLostDuringFetchConnector(FakeConnector):
    def __init__(self, db_path, documents=None):
        super().__init__(documents or [])
        self.db_path = db_path
        self.progress_stop_checker = self._stop_checker
        self.replacement_job = None
        self.stop_checks = 0

    async def _stop_checker(self):
        self.stop_checks += 1
        if self.stop_checks == 1:
            replacement_store = MetadataStore(self.db_path, running_job_timeout_seconds=0)
            self.replacement_job, _ = replacement_store.begin_sync_job("source_fake")
        return False

    async def fetch_documents(self):
        if self.progress_stop_checker is not None:
            result = self.progress_stop_checker()
            if inspect.isawaitable(result):
                result = await result
            if result:
                return []
        return await super().fetch_documents()


class ExceptionObserverStopConnector(FakeConnector):
    def __init__(self, documents=None):
        super().__init__(documents or [])
        self.stop_requested = False
        self.progress_callback = self._observer

    async def _observer(self, event):
        if event.get("event") == "search_started":
            self.stop_requested = True
            raise ingestion_module._StopRequested
        return None

    async def fetch_documents(self):
        if self.progress_callback is not None:
            result = self.progress_callback({"event": "search_started"})
            if inspect.isawaitable(result):
                result = await result
            if result is getattr(self, "progress_stop_signal", None):
                raise ingestion_module._StopRequested
        return await super().fetch_documents()


class RecordingIndexer:
    def __init__(self):
        self.indexed_batches = []
        self.deleted_ids = []

    async def index_documents(self, documents):
        self.indexed_batches.append(list(documents))

    def delete_documents_by_ids(self, document_ids, source_id=""):
        self.deleted_ids.extend(document_ids)


class FailingDeleteIndexer(RecordingIndexer):
    def __init__(self, message="vector delete failed"):
        super().__init__()
        self.message = message

    def delete_documents_by_ids(self, document_ids, source_id=""):
        raise RuntimeError(self.message)


class FailingOnceIndexer(RecordingIndexer):
    def __init__(self):
        super().__init__()
        self.failed = False

    async def index_documents(self, documents):
        if not self.failed:
            self.failed = True
            raise RuntimeError("index failed")
        await super().index_documents(documents)


class ReplacementDuringIndexingIndexer(RecordingIndexer):
    def __init__(self, db_path):
        super().__init__()
        self.db_path = db_path
        self.replacement_job = None

    async def index_documents(self, documents):
        await super().index_documents(documents)
        replacement_store = MetadataStore(self.db_path, running_job_timeout_seconds=0)
        self.replacement_job, _ = replacement_store.begin_sync_job("source_fake")


class BlockingFirstIndexIndexer(RecordingIndexer):
    def __init__(self, started, release):
        super().__init__()
        self.started = started
        self.release = release

    async def index_documents(self, documents):
        await super().index_documents(documents)
        if len(self.indexed_batches) == 1:
            self.started.set()
            await self.release.wait()


class FailingOnceMetadataStore(MetadataStore):
    def __init__(self, db_path):
        super().__init__(db_path)
        self.failed = False

    def replace_document_chunks(self, document_id, chunks):
        if not self.failed:
            self.failed = True
            raise RuntimeError("chunk metadata failed")
        return super().replace_document_chunks(document_id, chunks)

    def upsert_document_and_replace_chunks(self, document, chunks):
        if not self.failed:
            self.failed = True
            raise RuntimeError("chunk metadata failed")
        return super().upsert_document_and_replace_chunks(document, chunks)

    def upsert_document_and_replace_chunks_for_running_job(self, job_id, document, chunks):
        if not self.failed:
            self.failed = True
            raise RuntimeError("chunk metadata failed")
        return super().upsert_document_and_replace_chunks_for_running_job(job_id, document, chunks)


pytestmark = pytest.mark.integration


def test_ingestion_indexes_changed_documents_and_skips_unchanged(tmp_path):
    document = DocumentModel(
        id="notion_page_1",
        source_id="source_fake",
        title="ContextWiki",
        content="ContextWiki stores citation chunks.",
        url="https://notion.so/page-1",
        platform="Notion",
        path="ContextWiki",
        updated_at="2026-05-20T00:00:00Z",
    )
    connector = FakeConnector([document])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(service.sync_source("source_fake"))
    second_job = asyncio.run(service.sync_source("source_fake"))

    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert first_job.processed_documents == 1
    assert first_job.indexed_chunks == 1
    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_job.skipped_documents == 1
    assert len(indexer.indexed_batches) == 1
    assert store.get_latest_sync_job("source_fake").status == SyncJobStatus.SUCCEEDED


def test_ingestion_records_failed_sync_for_retry(tmp_path):
    connector = FakeConnector(error=RuntimeError("boom"))
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(),
        indexer=RecordingIndexer(),
    )

    job = asyncio.run(service.sync_source("source_fake"))

    assert job.status == SyncJobStatus.FAILED
    assert "boom" in job.error_message
    assert store.get_source("source_fake").sync_status == SyncStatus.FAILED


def test_ingestion_redacts_secret_failed_sync_for_retry(tmp_path, caplog):
    connector = FakeConnector(
        error=RuntimeError(
            "fetch failed with token=secret-value, api-key=abc123, "
            "password: hunter2, credential=privatevalue, "
            "x-amz-credential: aws-privatevalue, ghp_secretcredential, "
            "AKIAIOSFODNN7EXAMPLE, "
            "xoxb-1234567890-secret, AIzaSyDExampleExampleExampleExample1234, "
            "eyJheader.payloadvalue.signaturevalue, "
            "path /Users/eunhwa/private/vault.md, token supersecretvalue123456"
        )
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(),
        indexer=RecordingIndexer(),
    )

    with caplog.at_level(logging.ERROR, logger="indexing.ingestion_service"):
        job = asyncio.run(service.sync_source("source_fake"))

    assert job.status == SyncJobStatus.FAILED
    assert "token=<redacted>" in job.error_message
    assert "api-key=<redacted>" in job.error_message
    assert "password: <redacted>" in job.error_message
    assert "credential=<redacted>" in job.error_message
    assert "x-amz-credential: <redacted>" in job.error_message
    assert "token <redacted>" in job.error_message
    assert "secret-value" not in job.error_message
    assert "privatevalue" not in job.error_message
    assert "aws-privatevalue" not in job.error_message
    assert "/Users/eunhwa/private/vault.md" not in job.error_message
    assert "supersecretvalue123456" not in job.error_message
    assert "ghp_secretcredential" not in job.error_message
    assert "AKIAIOSFODNN7EXAMPLE" not in job.error_message
    assert "xoxb-1234567890-secret" not in job.error_message
    assert "AIzaSyDExampleExampleExampleExample1234" not in job.error_message
    assert "eyJheader.payloadvalue.signaturevalue" not in job.error_message
    assert "secret-value" not in caplog.text
    assert "privatevalue" not in caplog.text
    assert "aws-privatevalue" not in caplog.text
    assert "/Users/eunhwa/private/vault.md" not in caplog.text
    assert "supersecretvalue123456" not in caplog.text
    assert "ghp_secretcredential" not in caplog.text
    assert "AKIAIOSFODNN7EXAMPLE" not in caplog.text
    assert "xoxb-1234567890-secret" not in caplog.text
    assert "AIzaSyDExampleExampleExampleExample1234" not in caplog.text
    assert "eyJheader.payloadvalue.signaturevalue" not in caplog.text


def test_ingestion_persists_strongly_sanitized_failure_with_structured_fields(
    tmp_path,
):
    notion_tokens = (
        "ntn_abcdefghijklmnopqrstuvwxyz0123456789",
        "secret_abcdefghijklmnopqrstuvwxyz0123456789",
    )
    sensitive_paths = (
        "/Users/tester/private,vault;meeting notes.md",
        r"C:\Users\tester\private,vault;meeting notes.md",
    )
    connector = FakeConnector(
        error=RuntimeError(
            f"provider failure at {sensitive_paths[0]}, job_id=job-123; "
            f"fallback={sensitive_paths[1]}; source_id=source_fake "
            f"tokens={notion_tokens[0]} {notion_tokens[1]}"
        )
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(),
        indexer=RecordingIndexer(),
    )

    completed = asyncio.run(service.sync_source("source_fake"))
    persisted_job = store.get_sync_job(completed.job_id)
    persisted_source = store.get_source("source_fake")

    assert completed.status == SyncJobStatus.FAILED
    assert persisted_job is not None
    assert persisted_source is not None
    for value in (
        completed.error_message,
        persisted_job.error_message,
        persisted_source.last_error,
    ):
        assert all(token not in value for token in notion_tokens)
        assert all(path not in value for path in sensitive_paths)
        assert "vault;meeting notes.md" not in value
        assert "notes.md" not in value
        assert "job_id=job-123" in value
        assert "source_id=source_fake" in value
        assert "<redacted" in value


def test_ingestion_can_skip_source_config_registration_for_ad_hoc_sync(tmp_path):
    document = DocumentModel(
        id="doc-1",
        source_id="source_fake",
        title="Ad hoc",
        content="Ad hoc sync should not rewrite source static configuration.",
        url="https://example.com/doc-1",
        platform="GitHub",
        path="doc-1.md",
    )
    connector = FakeConnector([document])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.register_source(
        FakeConnector.source.model_copy(update={"enabled": False, "name": "Configured Fake"})
    )
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=200, overlap_chars=0),
        indexer=RecordingIndexer(),
        register_source_config=False,
    )

    job = asyncio.run(service.sync_source("source_fake"))

    assert job.status == SyncJobStatus.SUCCEEDED
    source = store.get_source("source_fake")
    assert source.enabled is False
    assert source.name == "Configured Fake"


def test_overlapping_source_sync_reuses_running_job_without_second_fetch(tmp_path):
    document = DocumentModel(
        id="doc-1",
        source_id="source_fake",
        title="Concurrent",
        content="Only one source sync should fetch at a time.",
        url="https://example.com/doc-1",
        platform="GitHub",
        path="doc-1.md",
    )

    async def run_overlapping_syncs():
        started = asyncio.Event()
        release = asyncio.Event()
        connector = BlockingConnector([document], started, release)
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        indexer = RecordingIndexer()
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=indexer,
        )

        first_task = asyncio.create_task(service.sync_source("source_fake"))
        await started.wait()
        second_job = await service.sync_source("source_fake")
        release.set()
        first_job = await first_task
        return connector, store, first_job, second_job

    connector, store, first_job, second_job = asyncio.run(run_overlapping_syncs())

    assert connector.calls == 1
    assert second_job.status == SyncJobStatus.RUNNING
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert second_job.job_id == first_job.job_id
    assert store.get_source("source_fake").sync_status == SyncStatus.SUCCEEDED


def test_start_sync_source_returns_running_job_and_completes_in_background(tmp_path):
    document = DocumentModel(
        id="doc-started",
        source_id="source_fake",
        title="Background",
        content="Background launch should return before completion.",
        url="https://example.com/doc-started",
        platform="GitHub",
        path="doc-started.md",
    )

    async def run_background_launch():
        started = asyncio.Event()
        release = asyncio.Event()
        connector = BlockingConnector([document], started, release)
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        indexer = RecordingIndexer()
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=indexer,
        )

        launched_job = await service.start_sync_source("source_fake")
        await started.wait()
        running_job = store.get_latest_sync_job("source_fake")
        release.set()

        completed_job = None
        for _ in range(20):
            completed_job = store.get_latest_sync_job("source_fake")
            if completed_job and completed_job.status != SyncJobStatus.RUNNING:
                break
            await asyncio.sleep(0)
        else:
            raise AssertionError("background sync did not complete within polling window")

        return connector, launched_job, running_job, completed_job, store

    connector, launched_job, running_job, completed_job, store = asyncio.run(
        run_background_launch()
    )

    assert connector.calls == 1
    assert launched_job.status == SyncJobStatus.RUNNING
    assert running_job is not None
    assert running_job.status == SyncJobStatus.RUNNING
    assert completed_job is not None
    assert completed_job.job_id == launched_job.job_id
    assert completed_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_source("source_fake").sync_status == SyncStatus.SUCCEEDED


def test_start_sync_source_reuses_existing_running_job_without_second_fetch(tmp_path):
    document = DocumentModel(
        id="doc-reuse",
        source_id="source_fake",
        title="Reuse",
        content="A second launcher call should reuse the running job.",
        url="https://example.com/doc-reuse",
        platform="GitHub",
        path="doc-reuse.md",
    )

    async def run_reused_background_launch():
        started = asyncio.Event()
        release = asyncio.Event()
        connector = BlockingConnector([document], started, release)
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        indexer = RecordingIndexer()
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=indexer,
        )

        first_job = await service.start_sync_source("source_fake")
        await started.wait()
        second_job = await service.start_sync_source("source_fake")
        release.set()

        completed_job = None
        for _ in range(20):
            completed_job = store.get_latest_sync_job("source_fake")
            if completed_job and completed_job.status != SyncJobStatus.RUNNING:
                break
            await asyncio.sleep(0)
        else:
            raise AssertionError("background sync did not complete within polling window")

        return connector, first_job, second_job, completed_job

    connector, first_job, second_job, completed_job = asyncio.run(
        run_reused_background_launch()
    )

    assert connector.calls == 1
    assert first_job.status == SyncJobStatus.RUNNING
    assert second_job.status == SyncJobStatus.RUNNING
    assert second_job.job_id == first_job.job_id
    assert completed_job is not None
    assert completed_job.job_id == first_job.job_id
    assert completed_job.status == SyncJobStatus.SUCCEEDED


def test_sync_source_can_start_fresh_run_after_local_background_completion(tmp_path):
    document = DocumentModel(
        id="doc-blocking-join",
        source_id="source_fake",
        title="Blocking join",
        content="Direct sync_source can start a fresh run after a local background launch finishes.",
        url="https://example.com/doc-blocking-join",
        platform="GitHub",
        path="doc-blocking-join.md",
    )

    async def run_blocking_join():
        started = asyncio.Event()
        release = asyncio.Event()
        connector = BlockingConnector([document], started, release)
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        indexer = RecordingIndexer()
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=indexer,
        )

        launched_job = await service.start_sync_source("source_fake")
        await started.wait()
        direct_task = asyncio.create_task(service.sync_source("source_fake"))
        await asyncio.sleep(0)
        assert not direct_task.done()
        release.set()
        completed_job = await direct_task
        return connector, launched_job, completed_job, store

    connector, launched_job, completed_job, store = asyncio.run(run_blocking_join())

    assert connector.calls == 2
    assert launched_job.status == SyncJobStatus.RUNNING
    assert completed_job.job_id != launched_job.job_id
    assert completed_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_latest_sync_job("source_fake").status == SyncJobStatus.SUCCEEDED


def test_sync_source_starts_fresh_run_after_successful_background_completion(tmp_path):
    document = DocumentModel(
        id="doc-background-success-rerun",
        source_id="source_fake",
        title="Background rerun",
        content="A completed background sync should not be cached as the next direct sync result.",
        url="https://example.com/doc-background-success-rerun",
        platform="GitHub",
        path="doc-background-success-rerun.md",
    )

    async def run_background_then_direct_sync():
        started = asyncio.Event()
        release = asyncio.Event()
        connector = BlockingConnector([document], started, release)
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        indexer = RecordingIndexer()
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=indexer,
        )

        launched_job = await service.start_sync_source("source_fake")
        await started.wait()
        release.set()
        for _ in range(20):
            latest_job = store.get_latest_sync_job("source_fake")
            if latest_job and latest_job.status == SyncJobStatus.SUCCEEDED:
                break
            await asyncio.sleep(0)
        rerun_job = await service.sync_source("source_fake")
        return connector, launched_job, rerun_job

    connector, launched_job, rerun_job = asyncio.run(run_background_then_direct_sync())

    assert connector.calls == 2
    assert launched_job.status == SyncJobStatus.RUNNING
    assert rerun_job.status == SyncJobStatus.SUCCEEDED
    assert rerun_job.job_id != launched_job.job_id


def test_sync_source_can_start_new_job_when_joined_background_task_is_cancelled(tmp_path):
    document = DocumentModel(
        id="doc-blocking-cancel",
        source_id="source_fake",
        title="Blocking cancel",
        content="Direct sync_source may start a fresh run after a joined local background task is cancelled.",
        url="https://example.com/doc-blocking-cancel",
        platform="GitHub",
        path="doc-blocking-cancel.md",
    )

    async def run_blocking_join_cancel():
        started = asyncio.Event()
        release = asyncio.Event()
        connector = BlockingConnector([document], started, release)
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        indexer = RecordingIndexer()
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=indexer,
        )

        launched_job = await service.start_sync_source("source_fake")
        await started.wait()
        background_task = service._background_sync_tasks["source_fake"]
        direct_task = asyncio.create_task(service.sync_source("source_fake"))
        await asyncio.sleep(0)
        background_task.cancel()
        release.set()
        completed_job = await direct_task
        return launched_job, completed_job, store

    launched_job, completed_job, store = asyncio.run(run_blocking_join_cancel())

    assert launched_job.status == SyncJobStatus.RUNNING
    assert completed_job.job_id != launched_job.job_id
    assert completed_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_latest_sync_job("source_fake").status == SyncJobStatus.SUCCEEDED


def test_sync_source_can_start_new_job_when_joined_background_task_is_cancelled_before_start(
    tmp_path,
):
    document = DocumentModel(
        id="doc-blocking-prestart-cancel",
        source_id="source_fake",
        title="Blocking prestart cancel",
        content="Direct sync_source may start a fresh run when a local background task is cancelled before the handoff settles.",
        url="https://example.com/doc-blocking-prestart-cancel",
        platform="GitHub",
        path="doc-blocking-prestart-cancel.md",
    )

    async def run_blocking_join_prestart_cancel():
        connector = FakeConnector([document])
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        indexer = RecordingIndexer()
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=indexer,
        )

        launched_job = await service.start_sync_source("source_fake")
        background_task = service._background_sync_tasks["source_fake"]
        direct_task = asyncio.create_task(service.sync_source("source_fake"))
        background_task.cancel()
        completed_job = await direct_task
        return launched_job, completed_job, store

    launched_job, completed_job, store = asyncio.run(run_blocking_join_prestart_cancel())

    assert launched_job.status == SyncJobStatus.RUNNING
    assert completed_job.job_id != launched_job.job_id
    assert completed_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_latest_sync_job("source_fake").status == SyncJobStatus.SUCCEEDED


def test_cancelled_background_sync_task_marks_job_failed(tmp_path):
    document = DocumentModel(
        id="doc-cancel-window",
        source_id="source_fake",
        title="Cancelled background",
        content="Cancelling the background task should not leave a stuck running job.",
        url="https://example.com/doc-cancel-window",
        platform="GitHub",
        path="doc-cancel-window.md",
    )

    async def run_cancelled_background_task():
        started = asyncio.Event()
        release = asyncio.Event()
        connector = BlockingConnector([document], started, release)
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        indexer = RecordingIndexer()
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=indexer,
        )

        launched_job = await service.start_sync_source("source_fake")
        background_task = service._background_sync_tasks["source_fake"]
        background_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await background_task
        await asyncio.sleep(0)
        failed_job = store.get_latest_sync_job("source_fake")
        release.set()
        return launched_job, failed_job, store

    launched_job, failed_job, store = asyncio.run(run_cancelled_background_task())

    assert launched_job.status == SyncJobStatus.RUNNING
    assert failed_job is not None
    assert failed_job.job_id == launched_job.job_id
    assert failed_job.status == SyncJobStatus.FAILED
    assert failed_job.error_message == "Sync request was cancelled before completion."
    assert store.get_source("source_fake").sync_status == SyncStatus.FAILED


def test_start_sync_source_retries_immediately_after_generic_background_cancellation(tmp_path):
    document = DocumentModel(
        id="doc-cancel-retry",
        source_id="source_fake",
        title="Cancel retry",
        content="A cancelled local background sync can surface its terminal failure once before a later retry launches new work.",
        url="https://example.com/doc-cancel-retry",
        platform="GitHub",
        path="doc-cancel-retry.md",
    )

    async def run_cancel_then_immediate_retry():
        started = asyncio.Event()
        release = asyncio.Event()
        connector = BlockingConnector([document], started, release)
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        indexer = RecordingIndexer()
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=indexer,
        )

        first_job = await service.start_sync_source("source_fake")
        await started.wait()
        background_task = service._background_sync_tasks["source_fake"]
        background_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await background_task
        release.set()
        retried_job = await service.start_sync_source("source_fake")

        for _ in range(20):
            latest_job = store.get_latest_sync_job("source_fake")
            if latest_job and latest_job.job_id == retried_job.job_id and latest_job.status != SyncJobStatus.RUNNING:
                cancelled_job = store.get_sync_job(first_job.job_id)
                return connector, first_job, cancelled_job, retried_job, latest_job
            await asyncio.sleep(0)
        raise AssertionError("follow-up retry did not reach a terminal status")

    connector, first_job, cancelled_job, retried_job, latest_job = asyncio.run(
        run_cancel_then_immediate_retry()
    )

    assert connector.calls == 2
    assert first_job.status == SyncJobStatus.RUNNING
    assert cancelled_job is not None
    assert cancelled_job.job_id == first_job.job_id
    assert cancelled_job.status == SyncJobStatus.FAILED
    assert retried_job.status == SyncJobStatus.RUNNING
    assert retried_job.job_id != first_job.job_id
    assert latest_job.job_id == retried_job.job_id
    assert latest_job.status == SyncJobStatus.SUCCEEDED


def test_sync_source_can_start_new_job_after_cancelled_background_callback_finalizes(tmp_path):
    document = DocumentModel(
        id="doc-cancelled-callback-handoff",
        source_id="source_fake",
        title="Cancelled callback handoff",
        content="A later direct sync can start a fresh run after a cancelled background callback finalizes.",
        url="https://example.com/doc-cancelled-callback-handoff",
        platform="GitHub",
        path="doc-cancelled-callback-handoff.md",
    )

    async def run_cancel_then_direct_sync():
        started = asyncio.Event()
        release = asyncio.Event()
        connector = BlockingConnector([document], started, release)
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        indexer = RecordingIndexer()
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=indexer,
        )

        launched_job = await service.start_sync_source("source_fake")
        await started.wait()
        background_task = service._background_sync_tasks["source_fake"]
        background_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await background_task
        await asyncio.sleep(0)

        release.set()
        rerun_job = await service.sync_source("source_fake")
        return connector, launched_job, rerun_job, store

    connector, launched_job, rerun_job, store = asyncio.run(run_cancel_then_direct_sync())

    assert connector.calls == 2
    assert rerun_job.status == SyncJobStatus.SUCCEEDED
    assert rerun_job.job_id != launched_job.job_id
    assert store.get_latest_sync_job("source_fake").job_id == rerun_job.job_id


def test_sync_source_ignores_cancelled_background_cache_when_newer_foreign_job_is_running(tmp_path):
    document = DocumentModel(
        id="doc-cancelled-foreign-running",
        source_id="source_fake",
        title="Cancelled foreign running",
        content="A stale cancelled background handoff must not override a newer authoritative running job.",
        url="https://example.com/doc-cancelled-foreign-running",
        platform="GitHub",
        path="doc-cancelled-foreign-running.md",
    )

    async def run_cancel_then_foreign_restart():
        started = asyncio.Event()
        release = asyncio.Event()
        connector = BlockingConnector([document], started, release)
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        indexer = RecordingIndexer()
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=indexer,
        )

        launched_job = await service.start_sync_source("source_fake")
        await started.wait()
        background_task = service._background_sync_tasks["source_fake"]
        background_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await background_task
        await asyncio.sleep(0)

        foreign_job, started_foreign = store.begin_sync_job("source_fake")
        assert started_foreign is True
        direct_job = await service.sync_source("source_fake")
        release.set()
        return launched_job, foreign_job, direct_job

    launched_job, foreign_job, direct_job = asyncio.run(run_cancel_then_foreign_restart())

    assert direct_job.job_id != launched_job.job_id
    assert direct_job.job_id == foreign_job.job_id
    assert direct_job.status == SyncJobStatus.RUNNING


def test_background_sync_missing_initial_job_marks_job_failed(tmp_path, monkeypatch):
    document = DocumentModel(
        id="doc-missing-job",
        source_id="source_fake",
        title="Missing job",
        content="Initial metadata lookup failures should not leave the job running.",
        url="https://example.com/doc-missing-job",
        platform="GitHub",
        path="doc-missing-job.md",
    )

    async def run_missing_job_flow():
        connector = FakeConnector([document])
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        indexer = RecordingIndexer()
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=indexer,
        )

        original_get_sync_job = store.get_sync_job
        first_lookup = True

        def fail_first_get_sync_job(job_id):
            nonlocal first_lookup
            if first_lookup:
                first_lookup = False
                return None
            return original_get_sync_job(job_id)

        monkeypatch.setattr(store, "get_sync_job", fail_first_get_sync_job)

        launched_job = await service.start_sync_source("source_fake")

        for _ in range(20):
            failed_job = store.get_latest_sync_job("source_fake")
            if failed_job and failed_job.status != SyncJobStatus.RUNNING:
                return launched_job, failed_job, store
            await asyncio.sleep(0)
        raise AssertionError("missing-job failure did not reach a terminal status")

    launched_job, failed_job, store = asyncio.run(run_missing_job_flow())

    assert launched_job.status == SyncJobStatus.RUNNING
    assert failed_job is not None
    assert failed_job.job_id == launched_job.job_id
    assert failed_job.status == SyncJobStatus.FAILED
    assert failed_job.error_message == "Unknown sync job: " + launched_job.job_id
    assert store.get_source("source_fake").sync_status == SyncStatus.FAILED


def test_cancelled_source_sync_marks_job_failed_and_allows_retry(tmp_path):
    document = DocumentModel(
        id="doc-cancelled",
        source_id="source_fake",
        title="Cancelled",
        content="A cancelled sync should not stay running forever.",
        url="https://example.com/doc-cancelled",
        platform="GitHub",
        path="doc-cancelled.md",
    )

    async def run_cancelled_sync():
        started = asyncio.Event()
        release = asyncio.Event()
        connector = BlockingConnector([document], started, release)
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        indexer = RecordingIndexer()
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=indexer,
        )

        first_task = asyncio.create_task(service.sync_source("source_fake"))
        await started.wait()
        first_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_task
        failed_job = store.get_latest_sync_job("source_fake")
        source_after_cancel = store.get_source("source_fake")
        release.set()
        retried_job = await service.sync_source("source_fake")
        return connector, failed_job, source_after_cancel, retried_job, store

    connector, failed_job, source_after_cancel, retried_job, store = asyncio.run(
        run_cancelled_sync()
    )

    assert connector.calls == 2
    assert failed_job is not None
    assert failed_job.status == SyncJobStatus.FAILED
    assert failed_job.error_message == "Sync request was cancelled before completion."
    assert source_after_cancel is not None
    assert source_after_cancel.sync_status == SyncStatus.FAILED
    assert source_after_cancel.last_error == "Sync request was cancelled before completion."
    assert retried_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_latest_sync_job("source_fake").status == SyncJobStatus.SUCCEEDED


def test_source_registration_preserves_existing_sync_status(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    connector = FakeConnector()
    IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(),
        indexer=RecordingIndexer(),
    )
    store.update_source_status(
        "source_fake",
        SyncStatus.SUCCEEDED,
        last_synced_at="2026-05-20T00:00:00Z",
        last_error="",
    )

    IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(),
        indexer=RecordingIndexer(),
    )

    source = store.get_source("source_fake")
    assert source.sync_status == SyncStatus.SUCCEEDED
    assert source.last_synced_at == "2026-05-20T00:00:00Z"


def test_failed_indexing_does_not_mark_document_as_indexed_for_retry(tmp_path):
    document = DocumentModel(
        id="raw-id",
        document_id="canonical-id",
        source_id="source_fake",
        title="Retry Safety",
        content="Retry should index after a failed vector write.",
        url="https://notion.so/retry",
        platform="Notion",
        path="Retry Safety",
        updated_at="2026-05-20T00:00:00Z",
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = FailingOnceIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([FakeConnector([document])]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    failed = asyncio.run(service.sync_source("source_fake"))
    retried = asyncio.run(service.sync_source("source_fake"))

    assert failed.status == SyncJobStatus.FAILED
    assert retried.status == SyncJobStatus.SUCCEEDED
    assert retried.processed_documents == 1
    assert store.get_document("canonical-id").content_hash
    assert len(indexer.indexed_batches) == 1


def test_stale_sync_does_not_commit_metadata_after_losing_lease_during_indexing(tmp_path):
    document = DocumentModel(
        id="lease-lost",
        source_id="source_fake",
        title="Lease Lost",
        content="A timed out sync must not publish active metadata.",
        url="https://example.com/lease-lost",
        platform="GitHub",
        path="lease-lost.md",
    )
    db_path = tmp_path / "contextwiki.sqlite3"
    store = MetadataStore(db_path, running_job_timeout_seconds=60)
    indexer = ReplacementDuringIndexingIndexer(db_path)
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([FakeConnector([document])]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    result = asyncio.run(service.sync_source("source_fake"))

    assert result.status == SyncJobStatus.FAILED
    assert "timed out" in result.error_message
    assert indexer.replacement_job.status == SyncJobStatus.RUNNING
    assert store.get_document("lease-lost") is None
    assert store.list_chunks_for_document("lease-lost") == []
    assert indexer.deleted_ids == [
        indexer.indexed_batches[0][0].chunk_id,
    ]


def test_sync_source_returns_failed_job_when_lease_is_lost_during_fetch(tmp_path):
    document = DocumentModel(
        id="lease-lost-fetch",
        source_id="source_fake",
        title="Lease Lost Fetch",
        content="A stale fetch-phase sync should return its failed job instead of raising raw cancellation.",
        url="https://example.com/lease-lost-fetch",
        platform="GitHub",
        path="lease-lost-fetch.md",
    )
    db_path = tmp_path / "contextwiki.sqlite3"
    store = MetadataStore(db_path, running_job_timeout_seconds=60)
    connector = LeaseLostDuringFetchConnector(db_path, [document])
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    result = asyncio.run(service.sync_source("source_fake"))

    assert result.status == SyncJobStatus.FAILED
    assert "timed out" in result.error_message
    assert connector.replacement_job is not None
    assert connector.replacement_job.status == SyncJobStatus.RUNNING


def test_stale_sync_does_not_delete_replacement_active_vector_after_losing_lease(tmp_path):
    document = DocumentModel(
        id="lease-lost",
        source_id="source_fake",
        title="Lease Lost",
        content="A replacement sync may commit the same deterministic chunk id.",
        url="https://example.com/lease-lost",
        platform="GitHub",
        path="lease-lost.md",
    )
    db_path = tmp_path / "contextwiki.sqlite3"
    store = MetadataStore(db_path, running_job_timeout_seconds=60)
    chunker = DocumentChunker(max_chars=120, overlap_chars=0)
    normalized = IngestionService._normalize_document(
        document,
        "source_fake",
        "2026-05-22T00:00:00+00:00",
        "replacement-job",
    )
    active_chunk = chunker.chunk_document(normalized)[0]

    class ReplacementCommitDuringIndexingIndexer(RecordingIndexer):
        def __init__(self, db_path, active_document, active_chunk):
            super().__init__()
            self.db_path = db_path
            self.active_document = active_document
            self.active_chunk = active_chunk
            self.replacement_job = None

        async def index_documents(self, documents):
            await super().index_documents(documents)
            replacement_store = MetadataStore(self.db_path, running_job_timeout_seconds=0)
            self.replacement_job, _ = replacement_store.begin_sync_job("source_fake")
            replacement_store.upsert_document_and_replace_chunks(
                self.active_document,
                [self.active_chunk],
            )

    indexer = ReplacementCommitDuringIndexingIndexer(db_path, normalized, active_chunk)
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([FakeConnector([document])]),
        chunker=chunker,
        indexer=indexer,
    )

    result = asyncio.run(service.sync_source("source_fake"))

    assert result.status == SyncJobStatus.FAILED
    assert indexer.replacement_job.status == SyncJobStatus.RUNNING
    assert store.get_chunk(active_chunk.chunk_id) == active_chunk
    assert indexer.deleted_ids == []


def test_ingestion_uses_canonical_document_id_for_hash_and_chunks(tmp_path):
    document = DocumentModel(
        id="raw-id",
        document_id="canonical-id",
        source_id="source_fake",
        title="Canonical Identity",
        content="Canonical id should control chunks and skip checks.",
        url="https://notion.so/canonical",
        platform="Notion",
        path="Canonical Identity",
        updated_at="2026-05-20T00:00:00Z",
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([FakeConnector([document])]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first = asyncio.run(service.sync_source("source_fake"))
    second = asyncio.run(service.sync_source("source_fake"))

    chunks = store.list_chunks_for_document("canonical-id")
    assert first.processed_documents == 1
    assert second.skipped_documents == 1
    assert chunks[0].chunk_id.startswith("canonical-id:chunk:0:")
    assert store.list_chunks_for_document("raw-id") == []


def test_ingestion_rejects_cross_source_document_identity_collision(tmp_path):
    first = DocumentModel(
        id="raw-a",
        external_id="shared-native-id",
        source_id="wrong_source",
        title="Source A",
        content="source a content",
        url="https://example.com/a",
        platform="GitHub",
    )
    second = DocumentModel(
        id="raw-b",
        external_id="shared-native-id",
        source_id="wrong_source",
        title="Source B",
        content="source b content",
        url="https://example.com/b",
        platform="GitHub",
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([
            SourceAConnector([first]),
            SourceBConnector([second]),
        ]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(service.sync_source("source_a"))
    second_job = asyncio.run(service.sync_source("source_b"))

    persisted = store.get_document("shared-native-id")
    chunks = store.list_chunks_for_document("shared-native-id")
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert second_job.status == SyncJobStatus.FAILED
    assert "already belongs to source_a" in second_job.error_message
    assert len(indexer.indexed_batches) == 1
    assert persisted.source_id == "source_a"
    assert persisted.external_id == "shared-native-id"
    assert chunks[0].source_id == "source_a"
    assert store.get_source("source_b").sync_status == SyncStatus.FAILED


def test_sync_all_runs_multiple_sources_and_returns_aggregate_summary(tmp_path):
    first = DocumentModel(
        id="doc-a",
        source_id="source_a",
        title="Source A",
        content="source a content",
        url="https://example.com/a",
        platform="GitHub",
        path="a.md",
    )
    second = DocumentModel(
        id="doc-b",
        source_id="source_b",
        title="Source B",
        content="source b content",
        url="https://example.com/b",
        platform="GitHub",
        path="b.md",
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([
            SourceAConnector([first]),
            SourceBConnector([second]),
        ]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    async def launch_and_wait():
        result = await service.sync_all()
        assert all(item["job"].status == SyncJobStatus.RUNNING for item in result["results"])
        await asyncio.gather(*service._background_sync_tasks.values())
        return result

    result = asyncio.run(launch_and_wait())

    assert result["status"] == "accepted"
    assert result["summary"]["total_sources"] == 2
    assert result["summary"]["started"] == 2
    assert result["summary"]["already_running"] == 0
    assert result["summary"]["failed"] == 0
    assert result["summary"]["requested_at"]
    assert {item["source_id"] for item in result["results"]} == {"source_a", "source_b"}
    assert all(item["launch_outcome"] == "started" for item in result["results"])
    assert store.get_latest_sync_job("source_a").status == SyncJobStatus.SUCCEEDED
    assert store.get_latest_sync_job("source_b").status == SyncJobStatus.SUCCEEDED


def test_sync_all_counts_disabled_source_as_skipped(tmp_path):
    document = DocumentModel(
        id="doc-a",
        source_id="source_a",
        title="Source A",
        content="source a content",
        url="https://example.com/a",
        platform="GitHub",
        path="a.md",
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([
            SourceAConnector([document]),
            DisabledConnector(),
        ]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    async def launch_and_wait():
        result = await service.sync_all(["source_a", "source_disabled"])
        await asyncio.gather(*service._background_sync_tasks.values())
        return result

    result = asyncio.run(launch_and_wait())

    started = next(item for item in result["results"] if item["source_id"] == "source_a")
    skipped = next(item for item in result["results"] if item["source_id"] == "source_disabled")
    assert result["status"] == "accepted"
    assert started["launch_outcome"] == "started"
    assert skipped["launch_outcome"] == "skipped"
    assert skipped["job"].status == SyncJobStatus.FAILED
    assert result["summary"]["started"] == 1
    assert result["summary"]["already_running"] == 0
    assert result["summary"]["failed"] == 0
    assert result["summary"]["skipped"] == 1


def test_sync_all_returns_before_new_background_connector_completes(tmp_path):
    document = DocumentModel(
        id="doc-a",
        source_id="source_a",
        title="Source A",
        content="source a content",
        url="https://example.com/a",
        platform="GitHub",
        path="a.md",
    )

    async def launch_before_release():
        started = asyncio.Event()
        release = asyncio.Event()
        connector = BlockingConnector([document], started, release)
        connector.source = SourceAConnector.source
        service = IngestionService(
            metadata_store=MetadataStore(tmp_path / "contextwiki.sqlite3"),
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=RecordingIndexer(),
        )

        result = await asyncio.wait_for(service.sync_all(["source_a"]), timeout=0.5)
        await started.wait()
        background_task = service._background_sync_tasks["source_a"]
        assert not background_task.done()
        release.set()
        await background_task
        return result

    result = asyncio.run(launch_before_release())

    assert result["status"] == "accepted"
    assert result["results"][0]["launch_outcome"] == "started"
    assert result["results"][0]["job"].status == SyncJobStatus.RUNNING


def test_sync_all_empty_selection_is_a_no_op(tmp_path):
    document = DocumentModel(
        id="doc-a",
        source_id="source_a",
        title="Source A",
        content="source a content",
        url="https://example.com/a",
        platform="GitHub",
        path="a.md",
    )
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=MetadataStore(tmp_path / "contextwiki.sqlite3"),
        source_registry=SourceRegistry([SourceAConnector([document])]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    result = asyncio.run(service.sync_all([]))

    assert result["status"] == "accepted"
    assert result["summary"]["total_sources"] == 0
    assert result["summary"]["started"] == 0
    assert result["summary"]["already_running"] == 0
    assert result["summary"]["failed"] == 0
    assert result["summary"]["skipped"] == 0
    assert result["summary"]["requested_at"]
    assert result["results"] == []
    assert indexer.indexed_batches == []


def test_sync_all_reports_already_running_source_without_waiting(tmp_path):
    document = DocumentModel(
        id="doc-a",
        source_id="source_a",
        title="Source A",
        content="source a content",
        url="https://example.com/a",
        platform="GitHub",
        path="a.md",
    )

    async def run_sync_all_while_one_source_is_running():
        started = asyncio.Event()
        release = asyncio.Event()
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([
                SourceAConnector([document]),
                SourceBConnector([document.model_copy(update={"id": "doc-b", "document_id": "doc-b"})]),
            ]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=RecordingIndexer(),
        )
        blocking_a = BlockingConnector([document], started, release)
        blocking_a.source = SourceAConnector.source
        service.source_registry = SourceRegistry([
            blocking_a,
            SourceBConnector([document.model_copy(update={"id": "doc-b", "document_id": "doc-b"})]),
        ])

        first_task = asyncio.create_task(service.sync_source("source_a"))
        await started.wait()
        result = await service.sync_all()
        assert result["results"][0]["launch_outcome"] == "already_running"
        release.set()
        await first_task
        await asyncio.gather(*service._background_sync_tasks.values())
        return result

    result = asyncio.run(run_sync_all_while_one_source_is_running())

    running = next(item for item in result["results"] if item["source_id"] == "source_a")
    started = next(item for item in result["results"] if item["source_id"] == "source_b")
    assert result["status"] == "accepted"
    assert running["launch_outcome"] == "already_running"
    assert running["job"].status == SyncJobStatus.RUNNING
    assert started["launch_outcome"] == "started"
    assert result["summary"]["already_running"] == 1
    assert result["summary"]["started"] == 1


def test_sync_all_reuses_local_background_sync_without_waiting(tmp_path):
    document = DocumentModel(
        id="doc-a",
        source_id="source_a",
        title="Source A",
        content="source a content",
        url="https://example.com/a",
        platform="GitHub",
        path="a.md",
    )

    async def run_sync_all_while_background_sync_is_running():
        started = asyncio.Event()
        release = asyncio.Event()
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        blocking_a = BlockingConnector([document], started, release)
        blocking_a.source = SourceAConnector.source
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([
                blocking_a,
                SourceBConnector([document.model_copy(update={"id": "doc-b", "document_id": "doc-b"})]),
            ]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=RecordingIndexer(),
        )

        launched_job = await service.start_sync_source("source_a")
        await started.wait()
        result = await service.sync_all()
        release.set()
        await asyncio.gather(*service._background_sync_tasks.values())
        return launched_job, result

    launched_job, result = asyncio.run(run_sync_all_while_background_sync_is_running())

    running = next(item for item in result["results"] if item["source_id"] == "source_a")
    started = next(item for item in result["results"] if item["source_id"] == "source_b")
    assert launched_job.status == SyncJobStatus.RUNNING
    assert result["status"] == "accepted"
    assert running["launch_outcome"] == "already_running"
    assert running["job"].status == SyncJobStatus.RUNNING
    assert started["launch_outcome"] == "started"
    assert result["summary"]["already_running"] == 1
    assert result["summary"]["started"] == 1


def test_sync_all_accepts_when_all_selected_sources_are_already_running(tmp_path):
    document = DocumentModel(
        id="doc-a",
        source_id="source_a",
        title="Source A",
        content="source a content",
        url="https://example.com/a",
        platform="GitHub",
        path="a.md",
    )

    async def run_sync_all_while_source_is_running():
        started = asyncio.Event()
        release = asyncio.Event()
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([SourceAConnector([document])]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=RecordingIndexer(),
        )
        blocking_a = BlockingConnector([document], started, release)
        blocking_a.source = SourceAConnector.source
        service.source_registry = SourceRegistry([blocking_a])

        first_task = asyncio.create_task(service.sync_source("source_a"))
        await started.wait()
        result = await service.sync_all(["source_a"])
        release.set()
        await first_task
        return result

    result = asyncio.run(run_sync_all_while_source_is_running())

    assert result["status"] == "accepted"
    assert result["summary"]["total_sources"] == 1
    assert result["summary"]["started"] == 0
    assert result["summary"]["failed"] == 0
    assert result["summary"]["already_running"] == 1
    assert result["summary"]["skipped"] == 0
    assert result["results"][0]["source_id"] == "source_a"
    assert result["results"][0]["launch_outcome"] == "already_running"
    assert result["results"][0]["job"].status == SyncJobStatus.RUNNING


def test_sync_all_launch_acceptance_does_not_claim_connector_completion(tmp_path):
    document = DocumentModel(
        id="doc-a",
        source_id="source_a",
        title="Source A",
        content="source a content",
        url="https://example.com/a",
        platform="GitHub",
        path="a.md",
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([
            SourceAConnector([document]),
            FakeConnector(error=RuntimeError("boom")),
        ]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    async def launch_and_wait():
        result = await service.sync_all(["source_a", "source_fake"])
        assert all(item["launch_outcome"] == "started" for item in result["results"])
        await asyncio.gather(*service._background_sync_tasks.values())
        return result

    result = asyncio.run(launch_and_wait())

    assert result["status"] == "accepted"
    assert result["summary"]["started"] == 2
    assert result["summary"]["failed"] == 0
    assert result["summary"]["already_running"] == 0
    assert result["summary"]["skipped"] == 0
    assert {
        (item["source_id"], item["launch_outcome"])
        for item in result["results"]
    } == {("source_a", "started"), ("source_fake", "started")}
    assert store.get_latest_sync_job("source_a").status == SyncJobStatus.SUCCEEDED
    assert store.get_latest_sync_job("source_fake").status == SyncJobStatus.FAILED


def test_sync_all_reports_failed_when_source_launch_cannot_start(tmp_path):
    service = IngestionService(
        metadata_store=MetadataStore(tmp_path / "contextwiki.sqlite3"),
        source_registry=SourceRegistry([FakeConnector()]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    result = asyncio.run(service.sync_all(["source_missing"]))

    assert result["status"] == "failed"
    assert result["summary"]["total_sources"] == 1
    assert result["summary"]["started"] == 0
    assert result["summary"]["failed"] == 1
    assert result["summary"]["already_running"] == 0
    assert result["summary"]["skipped"] == 0
    assert result["results"][0]["source_id"] == "source_missing"
    assert result["results"][0]["launch_outcome"] == "failed"


def test_concurrent_cross_source_collision_is_rejected_before_vector_write(tmp_path):
    first = DocumentModel(
        id="raw-a",
        external_id="shared-native-id",
        source_id="wrong_source",
        title="Source A",
        content="source a content",
        url="https://example.com/a",
        platform="GitHub",
    )
    second = DocumentModel(
        id="raw-b",
        external_id="shared-native-id",
        source_id="wrong_source",
        title="Source B",
        content="source b content",
        url="https://example.com/b",
        platform="GitHub",
    )

    async def run_collision():
        started = asyncio.Event()
        release = asyncio.Event()
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        indexer = BlockingFirstIndexIndexer(started, release)
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([
                SourceAConnector([first]),
                SourceBConnector([second]),
            ]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=indexer,
        )

        first_task = asyncio.create_task(service.sync_source("source_a"))
        await started.wait()
        second_job = await service.sync_source("source_b")
        release.set()
        first_job = await first_task
        return store, indexer, first_job, second_job

    store, indexer, first_job, second_job = asyncio.run(run_collision())

    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert second_job.status == SyncJobStatus.FAILED
    assert "claimed by source_a" in second_job.error_message
    assert len(indexer.indexed_batches) == 1
    assert store.get_document("shared-native-id").source_id == "source_a"


def test_self_expired_fetch_does_not_finalize_or_tombstone(tmp_path):
    existing = DocumentModel(
        id="existing",
        source_id="source_fake",
        title="Existing",
        content="existing content",
        url="https://example.com/existing",
        platform="GitHub",
        path="existing.md",
        last_seen_at="2026-05-22T00:00:00Z",
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3", running_job_timeout_seconds=0)
    store.upsert_document_and_replace_chunks(
        existing,
        [
            ChunkModel(
                chunk_id="existing:chunk:0:hash",
                document_id="existing",
                source_id="source_fake",
                title="Existing",
                text="existing content",
                chunk_index=0,
                content_hash="hash",
            )
        ],
    )
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([FakeConnector([])]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    result = asyncio.run(service.sync_source("source_fake"))

    assert result.status == SyncJobStatus.FAILED
    assert "heartbeat refresh" in result.error_message
    assert store.get_source("source_fake").sync_status == SyncStatus.FAILED
    assert store.get_document("existing").deleted_at == ""
    assert len(store.list_chunks_for_document("existing")) == 1


def test_partial_update_deletes_only_stale_chunk_vectors(tmp_path):
    first_document = DocumentModel(
        id="doc-multi",
        source_id="source_fake",
        title="Multi Chunk",
        content=("A" * 30) + ("B" * 30),
        url="https://notion.so/multi",
        platform="Notion",
        path="Multi Chunk",
        updated_at="2026-05-20T00:00:00Z",
    )
    second_document = first_document.model_copy(update={"content": ("A" * 30) + ("C" * 30)})
    connector = FakeConnector([first_document])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=30, overlap_chars=0),
        indexer=indexer,
    )

    asyncio.run(service.sync_source("source_fake"))
    old_chunks = store.list_chunks_for_document("doc-multi")
    connector.documents = [second_document]
    asyncio.run(service.sync_source("source_fake"))
    new_chunks = store.list_chunks_for_document("doc-multi")

    unchanged_chunk_id = old_chunks[0].chunk_id
    stale_chunk_id = old_chunks[1].chunk_id
    assert new_chunks[0].chunk_id == unchanged_chunk_id
    assert new_chunks[1].chunk_id != stale_chunk_id
    assert indexer.deleted_ids == [stale_chunk_id]


def test_disabled_source_records_failed_job_without_fetching(tmp_path):
    connector = DisabledConnector()
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(),
        indexer=RecordingIndexer(),
    )

    job = asyncio.run(service.sync_source("source_disabled"))

    assert job.status == SyncJobStatus.FAILED
    assert "disabled" in job.error_message.lower()
    assert connector.called is False
    assert store.get_source("source_disabled").sync_status == SyncStatus.FAILED


def test_durable_disabled_source_is_failed_atomically_without_completion_handoff(
    tmp_path,
    monkeypatch,
):
    connector = DisabledConnector()
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(),
        indexer=RecordingIndexer(),
        durable_dispatch=True,
    )

    def unexpected_second_transaction(**_kwargs):
        raise AssertionError("disabled enqueue must be terminal in its enqueue transaction")

    monkeypatch.setattr(store, "complete_failed_sync", unexpected_second_transaction)

    job, launch_outcome = asyncio.run(
        service._enqueue_sync_source_with_outcome("source_disabled")
    )

    assert launch_outcome == "skipped"
    assert job.status == SyncJobStatus.FAILED
    assert job.finished_at
    assert "disabled" in job.error_message.lower()
    assert connector.called is False
    assert store.get_source("source_disabled").sync_status == SyncStatus.FAILED


def test_disabled_github_source_records_public_missing_repository_config_error(tmp_path):
    connector = GitHubSourceConnector(
        repositories=(),
        config=AppConfig(),
        token="ghp_secretcredential",
        http_client=object(),
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(),
        indexer=RecordingIndexer(),
    )

    job = asyncio.run(service.sync_source("source_github"))
    source = store.get_source("source_github")

    assert job.status == SyncJobStatus.FAILED
    assert job.error_message == (
        "Source source_github is disabled because no GitHub repositories are "
        "configured in CONTEXTWIKI_GITHUB_REPOSITORIES."
    )
    assert "CONTEXTWIKI_GITHUB_REPOSITORIES" in source.last_error
    assert "ghp_secretcredential" not in job.error_message
    assert "ghp_secretcredential" not in source.last_error


def test_disabled_source_request_returns_existing_running_job_without_clobbering(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.register_source(FakeConnector.source)
    running_job, started = store.begin_sync_job("source_fake")
    assert started is True
    connector = DisabledSameSourceConnector()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(),
        indexer=RecordingIndexer(),
    )

    returned = asyncio.run(service.sync_source("source_fake"))

    with store._connect() as conn:
        job_count = conn.execute(
            "SELECT COUNT(*) AS count FROM sync_jobs WHERE source_id = ?",
            ("source_fake",),
        ).fetchone()["count"]

    assert returned.job_id == running_job.job_id
    assert returned.status == SyncJobStatus.RUNNING
    assert connector.called is False
    assert job_count == 1
    assert store.get_source("source_fake").sync_status == SyncStatus.RUNNING


def test_metadata_commit_failure_does_not_make_retry_skip_document(tmp_path):
    document = DocumentModel(
        id="doc-atomic",
        source_id="source_fake",
        title="Atomic Metadata",
        content="Metadata commit should be atomic with chunks.",
        url="https://notion.so/atomic",
        platform="Notion",
        path="Atomic Metadata",
        updated_at="2026-05-20T00:00:00Z",
    )
    store = FailingOnceMetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([FakeConnector([document])]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    failed = asyncio.run(service.sync_source("source_fake"))
    retried = asyncio.run(service.sync_source("source_fake"))

    assert failed.status == SyncJobStatus.FAILED
    assert retried.status == SyncJobStatus.SUCCEEDED
    assert retried.processed_documents == 1
    assert store.get_document("doc-atomic").content_hash
    assert len(store.list_chunks_for_document("doc-atomic")) == 1
    assert indexer.deleted_ids == [indexer.indexed_batches[0][0].chunk_id]


def test_successful_full_sync_tombstones_missing_documents_and_deletes_vectors(tmp_path):
    kept = DocumentModel(
        id="kept",
        source_id="source_fake",
        title="Kept",
        content="This document remains.",
        url="https://example.com/kept",
        platform="GitHub",
        path="kept.md",
    )
    removed = DocumentModel(
        id="removed",
        source_id="source_fake",
        title="Removed",
        content="This document disappears.",
        url="https://example.com/removed",
        platform="GitHub",
        path="removed.md",
    )
    connector = FakeConnector([kept, removed])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    asyncio.run(service.sync_source("source_fake"))
    removed_chunk_id = store.list_chunks_for_document("removed")[0].chunk_id
    connector.documents = [kept]
    second = asyncio.run(service.sync_source("source_fake"))

    assert second.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("removed").deleted_at
    assert store.list_chunks_for_document("removed") == []
    assert indexer.deleted_ids == [removed_chunk_id]
    assert store.get_document("kept").last_seen_at
    assert store.get_document("kept").deleted_at == ""


def test_running_sync_records_document_progress(tmp_path):
    first = DocumentModel(
        id="first",
        source_id="source_fake",
        title="First",
        content="First document.",
        url="https://example.com/first",
        platform="GitHub",
        path="first.md",
    )
    second = DocumentModel(
        id="second",
        source_id="source_fake",
        title="Second",
        content="Second document.",
        url="https://example.com/second",
        platform="GitHub",
        path="second.md",
    )
    store = ProgressRecordingMetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([FakeConnector([first, second])]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    job = asyncio.run(service.sync_source("source_fake"))

    assert job.status == SyncJobStatus.SUCCEEDED
    assert store.progress_updates[0]["total_documents"] == 2
    assert store.progress_updates[0]["processed_documents"] == 0
    assert store.progress_updates[-1]["processed_documents"] == 2
    assert store.progress_updates[-1]["indexed_chunks"] == 2


def test_running_sync_progress_update_failure_logs_redacted_error(tmp_path, caplog):
    document = DocumentModel(
        id="first",
        source_id="source_fake",
        title="First",
        content="First document.",
        url="https://example.com/first",
        platform="GitHub",
        path="first.md",
    )
    store = FailingProgressMetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([FakeConnector([document])]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    with caplog.at_level(logging.DEBUG, logger="indexing.ingestion_service"):
        job = asyncio.run(service.sync_source("source_fake"))

    assert job.status == SyncJobStatus.SUCCEEDED
    assert "token=secret-value" not in caplog.text
    assert "token=<redacted>" in caplog.text


def test_running_sync_fetch_progress_refreshes_heartbeat_and_logs(tmp_path, caplog):
    first = DocumentModel(
        id="first",
        document_id="page-1",
        source_id="source_fake",
        title="First",
        content="First document.",
        url="https://example.com/first",
        platform="Notion",
        path="first.md",
    )
    second = DocumentModel(
        id="second",
        document_id="page-2",
        source_id="source_fake",
        title="Second",
        content="Second document.",
        url="https://example.com/second",
        platform="Notion",
        path="second.md",
    )
    store = TouchRecordingMetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ProgressAwareConnector([first, second])]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    with caplog.at_level(logging.INFO, logger="indexing.ingestion_service"):
        job = asyncio.run(service.sync_source("source_fake"))

    assert job.status == SyncJobStatus.SUCCEEDED
    assert store.progress_updates[0]["total_documents"] == 2
    assert store.touched_job_ids
    assert "discovered 2 upstream item(s) before indexing" in caplog.text
    assert "fetching upstream item 1/2" in caplog.text
    assert "fetched upstream item 2/2" in caplog.text
    assert "page-1" not in caplog.text
    assert "page-2" not in caplog.text
    latest = store.get_latest_sync_job("source_fake")
    assert latest.phase == "completed"
    assert latest.upstream_total == 2
    assert latest.upstream_done == 2
    assert "upstream_total_pages" not in latest.model_dump()
    assert "upstream_fetched_pages" not in latest.model_dump()
    assert latest.last_progress_at
    assert latest.status_message == "Sync completed. Indexed 2/2 documents; skipped 0."


def test_handle_source_fetch_progress_page_fetch_skipped_advances_upstream_done(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([FakeConnector([])]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )
    job, started = store.begin_sync_job("source_fake")
    assert started is True
    store.update_sync_job(
        job.job_id,
        phase=ingestion_module.FETCHING_PAGE_CONTENT_PHASE,
        upstream_total=3,
        upstream_done=1,
    )

    result = asyncio.run(
        service._handle_source_fetch_progress(
            job.job_id,
            "source_fake",
            {
                "event": "page_fetch_skipped",
                "current_page": 3,
                "total_pages": 3,
                "page_id": "page-skipped",
                "title": "Skipped",
                "elapsed_seconds": 0.01,
            },
        )
    )
    latest = store.get_sync_job(job.job_id)

    assert result is None
    assert latest.phase == ingestion_module.FETCHING_PAGE_CONTENT_PHASE
    assert latest.upstream_total == 3
    assert latest.upstream_done == 3
    assert "Reused stored upstream item content 3/3" in latest.status_message
    assert "Notion" not in latest.status_message


def test_handle_source_fetch_progress_status_message_is_source_neutral_for_github(
    tmp_path,
):
    github_connector = FakeConnector([])
    github_connector.source = github_connector.source.model_copy(
        update={
            "source_id": "source_github",
            "source_type": SourceType.GITHUB,
            "name": "GitHub",
        }
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([github_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )
    job, started = store.begin_sync_job("source_github")
    assert started is True

    asyncio.run(
        service._handle_source_fetch_progress(
            job.job_id,
            "source_github",
            {
                "event": "search_completed",
                "total_pages": 10,
            },
        )
    )
    latest = store.get_sync_job(job.job_id)

    assert "Notion" not in latest.status_message
    assert "upstream item" in latest.status_message.lower()
    assert "0/10" in latest.status_message


def test_running_sync_discovery_progress_exposes_numeric_discovery_count(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([DiscoveryProgressConnector([])]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )
    job, started = store.begin_sync_job("source_fake")
    assert started is True

    result = asyncio.run(
        service._handle_source_fetch_progress(
            job.job_id,
            "source_fake",
            {
                "event": "search_page_batch_completed",
                "batch_index": 1,
                "pages_discovered": 42,
                "has_more": True,
            },
        )
    )
    latest = store.get_sync_job(job.job_id)

    assert result is None
    assert latest.phase == "discovering_pages"
    assert latest.upstream_total == 42
    assert latest.upstream_done == 0
    assert latest.status_message == (
        "Discovering upstream items: 42 found after batch 1. More results remain."
    )
    assert "Notion" not in latest.status_message


def test_refresh_running_job_for_progress_updates_heartbeat_without_hint_write(
    tmp_path,
):
    store = TouchRecordingMetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([FakeConnector([])]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )
    job, started = store.begin_sync_job("source_fake")
    assert started is True
    store.update_sync_job(
        job.job_id,
        phase=ingestion_module.FETCHING_PAGE_CONTENT_PHASE,
        last_progress_at="2026-06-15T00:00:00+00:00",
        status_message="Fetching upstream items 0/10 before indexing begins.",
    )

    result = service._refresh_running_job_for_progress(job.job_id)
    latest = store.get_sync_job(job.job_id)

    assert result is None
    # Heartbeat stays fresh for orphan detection; visible hints are coalesced elsewhere.
    assert job.job_id in store.touched_job_ids
    assert latest.last_progress_at == "2026-06-15T00:00:00+00:00"
    assert latest.status_message == "Fetching upstream items 0/10 before indexing begins."


def test_page_fetch_hint_persistence_is_throttled(tmp_path, monkeypatch):
    monkeypatch.setattr(ingestion_module, "_PAGE_FETCH_HINT_PERSIST_INTERVAL", 3)
    monkeypatch.setattr(ingestion_module, "_PAGE_FETCH_LIVENESS_PERSIST_INTERVAL", 5)

    class HintCountingStore(TouchRecordingMetadataStore):
        def __init__(self, db_path):
            super().__init__(db_path)
            self.hint_writes = 0

        def update_sync_job(self, job_id: str, **updates):
            if {"upstream_done", "status_message"}.intersection(updates):
                self.hint_writes += 1
            return super().update_sync_job(job_id, **updates)

    obsidian_connector = FakeConnector([])
    obsidian_connector.source = obsidian_connector.source.model_copy(
        update={
            "source_id": "source_obsidian",
            "source_type": SourceType.OBSIDIAN,
            "name": "Obsidian",
        }
    )
    store = HintCountingStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([obsidian_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )
    job, started = store.begin_sync_job("source_obsidian")
    assert started is True
    store.update_sync_job(
        job.job_id,
        phase=ingestion_module.FETCHING_PAGE_CONTENT_PHASE,
        upstream_total=7,
        upstream_done=0,
    )
    baseline_writes = store.hint_writes
    touch_baseline = len(store.touched_job_ids)

    for current in range(1, 8):
        asyncio.run(
            service._handle_source_fetch_progress(
                job.job_id,
                "source_obsidian",
                {
                    "event": "page_fetch_completed",
                    "current_page": current,
                    "total_pages": 7,
                },
            )
        )

    # Persist on cadence (3, 6) and always on the last item (7).
    assert store.hint_writes - baseline_writes == 3
    # Heartbeat touches coalesce on liveness cadence (5) plus last (7).
    assert len(store.touched_job_ids) - touch_baseline == 2
    latest = store.get_sync_job(job.job_id)
    assert latest.upstream_done == 7
    assert "7/7" in latest.status_message
    assert "Notion" not in latest.status_message


def test_page_fetch_liveness_updates_last_progress_at_more_often_than_hints(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(ingestion_module, "_PAGE_FETCH_HINT_PERSIST_INTERVAL", 10)
    monkeypatch.setattr(
        ingestion_module,
        "_PAGE_FETCH_LIVENESS_PERSIST_INTERVAL",
        5,
        raising=False,
    )

    class ProgressTimestampStore(TouchRecordingMetadataStore):
        def __init__(self, db_path):
            super().__init__(db_path)
            self.last_progress_writes = 0
            self.hint_counter_writes = 0

        def update_sync_job(self, job_id: str, **updates):
            if "last_progress_at" in updates:
                self.last_progress_writes += 1
            if {"upstream_done", "status_message"}.intersection(updates):
                self.hint_counter_writes += 1
            return super().update_sync_job(job_id, **updates)

    store = ProgressTimestampStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([FakeConnector([])]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )
    job, started = store.begin_sync_job("source_fake")
    assert started is True
    store.update_sync_job(
        job.job_id,
        phase=ingestion_module.FETCHING_PAGE_CONTENT_PHASE,
        upstream_total=12,
        upstream_done=0,
        last_progress_at="2026-06-15T00:00:00+00:00",
        status_message="Fetching upstream items 0/12 before indexing begins.",
    )
    progress_baseline = store.last_progress_writes
    hint_baseline = store.hint_counter_writes
    frozen_message = store.get_sync_job(job.job_id).status_message

    for current in range(1, 6):
        asyncio.run(
            service._handle_source_fetch_progress(
                job.job_id,
                "source_fake",
                {
                    "event": "page_fetch_completed",
                    "current_page": current,
                    "total_pages": 12,
                },
            )
        )

    latest = store.get_sync_job(job.job_id)
    # Liveness cadence (page 5) advances public last_progress_at before hint interval 10.
    assert store.last_progress_writes - progress_baseline >= 1
    assert latest.last_progress_at != "2026-06-15T00:00:00+00:00"
    # Full upstream_*/status_message hints stay throttled until interval 10 / last.
    assert store.hint_counter_writes - hint_baseline == 0
    assert latest.upstream_done == 0
    assert latest.status_message == frozen_message


def test_page_fetch_coalesces_touch_sync_job_below_per_event(tmp_path, monkeypatch):
    monkeypatch.setattr(ingestion_module, "_PAGE_FETCH_HINT_PERSIST_INTERVAL", 25)
    monkeypatch.setattr(
        ingestion_module,
        "_PAGE_FETCH_LIVENESS_PERSIST_INTERVAL",
        5,
        raising=False,
    )

    class ReadCountingStore(TouchRecordingMetadataStore):
        def __init__(self, db_path):
            super().__init__(db_path)
            self.get_sync_job_calls = 0

        def get_sync_job(self, job_id: str):
            self.get_sync_job_calls += 1
            return super().get_sync_job(job_id)

    store = ReadCountingStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([FakeConnector([])]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )
    job, started = store.begin_sync_job("source_fake")
    assert started is True
    store.update_sync_job(
        job.job_id,
        phase=ingestion_module.FETCHING_PAGE_CONTENT_PHASE,
        upstream_total=12,
        upstream_done=0,
    )
    touch_baseline = len(store.touched_job_ids)
    get_baseline = store.get_sync_job_calls

    for current in range(1, 13):
        asyncio.run(
            service._handle_source_fetch_progress(
                job.job_id,
                "source_fake",
                {
                    "event": "page_fetch_completed",
                    "current_page": current,
                    "total_pages": 12,
                },
            )
        )

    touch_count = len(store.touched_job_ids) - touch_baseline
    # Liveness every 5 + first/last — not one IMMEDIATE touch per event.
    assert touch_count < 12
    assert touch_count <= 4
    # Skipped intervals still observe job activity without a write.
    assert store.get_sync_job_calls - get_baseline >= 1
    latest = store.get_sync_job(job.job_id)
    assert latest.upstream_done == 12


def test_running_sync_fails_when_existing_observer_requests_stop(tmp_path):
    document = DocumentModel(
        id="first",
        document_id="page-1",
        source_id="source_fake",
        title="First",
        content="First document.",
        url="https://example.com/first",
        platform="Notion",
        path="first.md",
    )
    connector = ExistingObserverStopConnector([document])
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=MetadataStore(tmp_path / "contextwiki.sqlite3"),
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    job = asyncio.run(service.sync_source("source_fake"))

    assert connector.stop_requested is True
    assert job.status == SyncJobStatus.FAILED
    assert job.total_documents == 0
    assert job.processed_documents == 0
    assert indexer.indexed_batches == []
    assert job.error_message == ingestion_module.OBSERVER_CANCELLED_SYNC_ERROR


def test_running_sync_fails_when_awaitable_observer_requests_stop(tmp_path):
    document = DocumentModel(
        id="first",
        document_id="page-1",
        source_id="source_fake",
        title="First",
        content="First document.",
        url="https://example.com/first",
        platform="Notion",
        path="first.md",
    )
    connector = AwaitableObserverStopConnector([document])
    service = IngestionService(
        metadata_store=MetadataStore(tmp_path / "contextwiki.sqlite3"),
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    job = asyncio.run(service.sync_source("source_fake"))

    assert connector.stop_requested is True
    assert job.status == SyncJobStatus.FAILED
    assert job.error_message == ingestion_module.OBSERVER_CANCELLED_SYNC_ERROR


def test_running_sync_fails_when_callback_only_observer_requests_stop(tmp_path):
    document = DocumentModel(
        id="first",
        document_id="page-1",
        source_id="source_fake",
        title="First",
        content="First document.",
        url="https://example.com/first",
        platform="Notion",
        path="first.md",
    )
    connector = CallbackOnlyObserverStopConnector([document])
    service = IngestionService(
        metadata_store=MetadataStore(tmp_path / "contextwiki.sqlite3"),
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    job = asyncio.run(service.sync_source("source_fake"))

    assert connector.stop_requested is True
    assert job.status == SyncJobStatus.FAILED
    assert job.error_message == ingestion_module.OBSERVER_CANCELLED_SYNC_ERROR


def test_running_sync_fails_when_observer_raises_stop_requested(tmp_path):
    document = DocumentModel(
        id="first",
        document_id="page-1",
        source_id="source_fake",
        title="First",
        content="First document.",
        url="https://example.com/first",
        platform="Notion",
        path="first.md",
    )
    connector = ExceptionObserverStopConnector([document])
    service = IngestionService(
        metadata_store=MetadataStore(tmp_path / "contextwiki.sqlite3"),
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    job = asyncio.run(service.sync_source("source_fake"))

    assert connector.stop_requested is True
    assert job.status == SyncJobStatus.FAILED
    assert job.error_message == ingestion_module.OBSERVER_CANCELLED_SYNC_ERROR


def test_running_sync_fails_when_nested_stop_checker_raises_stop_requested(tmp_path):
    document = DocumentModel(
        id="first",
        document_id="page-1",
        source_id="source_fake",
        title="First",
        content="First document.",
        url="https://example.com/first",
        platform="Notion",
        path="first.md",
    )
    connector = RaisingStopCheckerConnector([document])
    service = IngestionService(
        metadata_store=MetadataStore(tmp_path / "contextwiki.sqlite3"),
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    job = asyncio.run(service.sync_source("source_fake"))

    assert connector.stop_requested is True
    assert job.status == SyncJobStatus.FAILED
    assert job.error_message == ingestion_module.OBSERVER_CANCELLED_SYNC_ERROR


def test_sync_source_replays_observer_cancelled_background_failure_once(tmp_path):
    document = DocumentModel(
        id="doc-observer-cancelled-handoff",
        source_id="source_fake",
        title="Observer cancelled handoff",
        content="The first direct retry should replay the failed observer-cancelled background job once.",
        url="https://example.com/doc-observer-cancelled-handoff",
        platform="Notion",
        path="doc-observer-cancelled-handoff.md",
    )

    async def run_background_then_direct_sync():
        connector = ExistingObserverStopConnector([document])
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=RecordingIndexer(),
        )

        launched_job = await service.start_sync_source("source_fake")
        for _ in range(20):
            latest_job = store.get_latest_sync_job("source_fake")
            if latest_job and latest_job.status == SyncJobStatus.FAILED:
                break
            await asyncio.sleep(0)
        replayed_job = await service.sync_source("source_fake")
        rerun_job = await service.sync_source("source_fake")
        return launched_job, replayed_job, rerun_job, store

    launched_job, replayed_job, rerun_job, store = asyncio.run(run_background_then_direct_sync())

    assert replayed_job.job_id == launched_job.job_id
    assert replayed_job.status == SyncJobStatus.FAILED
    assert replayed_job.error_message == ingestion_module.OBSERVER_CANCELLED_SYNC_ERROR
    assert rerun_job.job_id != launched_job.job_id
    assert store.get_latest_sync_job("source_fake").job_id == rerun_job.job_id


def test_start_sync_source_replays_observer_cancelled_background_failure_once(tmp_path):
    document = DocumentModel(
        id="doc-observer-cancelled-start-replay",
        source_id="source_fake",
        title="Observer cancelled start replay",
        content="The first MCP-style retry should replay the failed observer-cancelled background job once.",
        url="https://example.com/doc-observer-cancelled-start-replay",
        platform="Notion",
        path="doc-observer-cancelled-start-replay.md",
    )

    async def run_background_then_restart():
        connector = ExistingObserverStopConnector([document])
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=RecordingIndexer(),
        )

        launched_job = await service.start_sync_source("source_fake")
        for _ in range(20):
            latest_job = store.get_latest_sync_job("source_fake")
            if latest_job and latest_job.status == SyncJobStatus.FAILED:
                break
            await asyncio.sleep(0)
        replayed_job = await service.start_sync_source("source_fake")
        rerun_job = await service.start_sync_source("source_fake")
        return launched_job, replayed_job, rerun_job, store

    launched_job, replayed_job, rerun_job, store = asyncio.run(run_background_then_restart())

    assert replayed_job.job_id == launched_job.job_id
    assert replayed_job.status == SyncJobStatus.FAILED
    assert replayed_job.error_message == ingestion_module.OBSERVER_CANCELLED_SYNC_ERROR
    assert rerun_job.job_id != launched_job.job_id
    assert store.get_latest_sync_job("source_fake").job_id == rerun_job.job_id


def test_sync_all_replays_observer_cancelled_background_failure_once(tmp_path):
    document = DocumentModel(
        id="doc-observer-cancelled-bulk-replay",
        source_id="source_fake",
        title="Observer cancelled bulk replay",
        content="The first bulk retry should replay the failed observer-cancelled background job once.",
        url="https://example.com/doc-observer-cancelled-bulk-replay",
        platform="Notion",
        path="doc-observer-cancelled-bulk-replay.md",
    )

    async def run_background_then_bulk_sync():
        connector = ObserverCancelledOnceConnector([document])
        store = MetadataStore(tmp_path / "contextwiki.sqlite3")
        service = IngestionService(
            metadata_store=store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=RecordingIndexer(),
        )

        launched_job = await service.start_sync_source("source_fake")
        for _ in range(100):
            latest_job = store.get_latest_sync_job("source_fake")
            if latest_job is not None and latest_job.status != SyncJobStatus.RUNNING:
                break
            await asyncio.sleep(0.01)
        replayed = await service.sync_all(["source_fake"])
        rerun = await service.sync_all(["source_fake"])
        await asyncio.gather(*service._background_sync_tasks.values())
        return launched_job, replayed, rerun

    launched_job, replayed, rerun = asyncio.run(run_background_then_bulk_sync())

    assert replayed["results"][0]["job"].job_id == launched_job.job_id
    assert replayed["results"][0]["job"].status == SyncJobStatus.FAILED
    assert replayed["results"][0]["job"].error_message == (
        ingestion_module.OBSERVER_CANCELLED_SYNC_ERROR
    )
    assert rerun["results"][0]["job"].job_id != launched_job.job_id
    assert rerun["results"][0]["launch_outcome"] == "started"


def test_running_sync_supports_async_nested_stop_checker_without_false_stop(tmp_path):
    document = DocumentModel(
        id="first",
        document_id="page-1",
        source_id="source_fake",
        title="First",
        content="First document.",
        url="https://example.com/first",
        platform="Notion",
        path="first.md",
    )
    connector = AsyncFalseStopCheckerConnector([document])
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=MetadataStore(tmp_path / "contextwiki.sqlite3"),
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    job = asyncio.run(service.sync_source("source_fake"))

    assert connector.stop_checks >= 1
    assert job.status == SyncJobStatus.SUCCEEDED
    assert job.processed_documents == 1
    assert len(indexer.indexed_batches) == 1


def test_running_sync_fetch_progress_hint_failure_logs_redacted_error_and_sync_survives(
    tmp_path,
    caplog,
):
    document = DocumentModel(
        id="first",
        document_id="page-1",
        source_id="source_fake",
        title="First",
        content="First document.",
        url="https://example.com/first",
        platform="Notion",
        path="first.md",
    )
    store = HintFailingMetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ProgressAwareConnector([document])]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    with caplog.at_level(logging.DEBUG, logger="indexing.ingestion_service"):
        job = asyncio.run(service.sync_source("source_fake"))

    assert job.status == SyncJobStatus.SUCCEEDED
    assert "hint progress failed with token=secret-value" not in caplog.text
    assert "token=<redacted>" in caplog.text


def test_running_sync_fetch_progress_heartbeat_failure_logs_redacted_error_and_sync_survives(
    tmp_path,
    caplog,
):
    document = DocumentModel(
        id="first",
        document_id="page-1",
        source_id="source_fake",
        title="First",
        content="First document.",
        url="https://example.com/first",
        platform="Notion",
        path="first.md",
    )
    store = SecondTouchFailingMetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ProgressAwareConnector([document])]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    with caplog.at_level(logging.DEBUG, logger="indexing.ingestion_service"):
        job = asyncio.run(service.sync_source("source_fake"))

    assert job.status == SyncJobStatus.SUCCEEDED
    assert "touch failed with token=secret-value" not in caplog.text
    assert "token=<redacted>" in caplog.text


def test_successful_full_sync_cleanup_uses_unique_job_marker_when_timestamp_repeats(
    tmp_path,
    monkeypatch,
):
    marker = "2026-05-22T00:00:00+00:00"
    monkeypatch.setattr(ingestion_module, "_now", lambda: marker)
    kept = DocumentModel(
        id="kept",
        source_id="source_fake",
        title="Kept",
        content="This document remains.",
        url="https://example.com/kept",
        platform="GitHub",
        path="kept.md",
    )
    removed = DocumentModel(
        id="removed",
        source_id="source_fake",
        title="Removed",
        content="This document disappears.",
        url="https://example.com/removed",
        platform="GitHub",
        path="removed.md",
    )
    connector = FakeConnector([kept, removed])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first = asyncio.run(service.sync_source("source_fake"))
    removed_chunk_id = store.list_chunks_for_document("removed")[0].chunk_id
    connector.documents = [kept]
    second = asyncio.run(service.sync_source("source_fake"))

    assert first.status == SyncJobStatus.SUCCEEDED
    assert second.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("kept").last_seen_at == marker
    assert store.get_document("kept").last_seen_sync_id == second.job_id
    assert store.get_document("removed").deleted_at == marker
    assert store.list_chunks_for_document("removed") == []
    assert indexer.deleted_ids == [removed_chunk_id]


def test_successful_full_sync_only_tombstones_scoped_documents(tmp_path):
    kept = DocumentModel(
        id="github:eunhwa99/mcpcontentsearch:README.md",
        source_id="source_fake",
        title="README",
        content="This configured repo document remains.",
        url="https://example.com/readme",
        platform="GitHub",
        path="README.md",
    )
    removed = DocumentModel(
        id="github:eunhwa99/mcpcontentsearch:old.py",
        source_id="source_fake",
        title="Old",
        content="This configured repo document disappears.",
        url="https://example.com/old",
        platform="GitHub",
        path="old.py",
    )
    ad_hoc = DocumentModel(
        id="github:eunhwa99/leetcode:graph.py",
        source_id="source_fake",
        title="Graph",
        content="This ad hoc repo document should remain searchable.",
        url="https://example.com/graph",
        platform="GitHub",
        path="graph.py",
    )
    connector = ScopedCleanupConnector([kept, removed, ad_hoc])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    asyncio.run(service.sync_source("source_fake"))
    removed_chunk_id = store.list_chunks_for_document(removed.id)[0].chunk_id
    connector.documents = [kept]
    second = asyncio.run(service.sync_source("source_fake"))

    assert second.status == SyncJobStatus.SUCCEEDED
    assert store.get_document(removed.id).deleted_at
    assert store.get_document(ad_hoc.id).deleted_at == ""
    assert store.list_chunks_for_document(ad_hoc.id)
    assert indexer.deleted_ids == [removed_chunk_id]


def test_failed_sync_does_not_tombstone_previous_documents(tmp_path):
    document = DocumentModel(
        id="survivor",
        source_id="source_fake",
        title="Survivor",
        content="Partial failures must not tombstone me.",
        url="https://example.com/survivor",
        platform="GitHub",
        path="survivor.md",
    )
    connector = FakeConnector([document])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    asyncio.run(service.sync_source("source_fake"))
    connector.error = RuntimeError("temporary source failure")
    failed = asyncio.run(service.sync_source("source_fake"))

    assert failed.status == SyncJobStatus.FAILED
    assert store.get_document("survivor").deleted_at == ""
    assert len(store.list_chunks_for_document("survivor")) == 1
    assert indexer.deleted_ids == []


def test_reappearing_tombstoned_document_reindexes_even_when_hash_matches(tmp_path):
    document = DocumentModel(
        id="reappears",
        source_id="source_fake",
        title="Reappears",
        content="Same content after deletion.",
        url="https://example.com/reappears",
        platform="GitHub",
        path="reappears.md",
    )
    connector = FakeConnector([document])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    asyncio.run(service.sync_source("source_fake"))
    connector.documents = []
    asyncio.run(service.sync_source("source_fake"))
    assert store.get_document("reappears").deleted_at

    connector.documents = [document]
    reindexed = asyncio.run(service.sync_source("source_fake"))

    assert reindexed.processed_documents == 1
    assert store.get_document("reappears").deleted_at == ""
    assert len(store.list_chunks_for_document("reappears")) == 1
    assert len(indexer.indexed_batches) == 2


def test_reappearing_document_preserves_old_chunk_id_for_raw_suppression(tmp_path):
    first = DocumentModel(
        id="reappears",
        source_id="source_fake",
        title="Reappears",
        content="Old content before deletion.",
        url="https://example.com/old-reappears",
        platform="GitHub",
        path="reappears.md",
    )
    second = first.model_copy(
        update={
            "content": "New content after reappearance.",
            "url": "https://example.com/new-reappears",
        }
    )
    connector = FakeConnector([first])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = FailingDeleteIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    asyncio.run(service.sync_source("source_fake"))
    old_chunk_id = store.list_chunks_for_document("reappears")[0].chunk_id
    connector.documents = []
    asyncio.run(service.sync_source("source_fake"))
    assert store.get_document("reappears").deleted_at
    assert store.has_chunk_record(old_chunk_id) is True

    connector.documents = [second]
    asyncio.run(service.sync_source("source_fake"))
    active_chunks = store.list_chunks_for_document("reappears")

    assert store.get_document("reappears").deleted_at == ""
    assert active_chunks[0].chunk_id != old_chunk_id
    assert store.get_chunk(old_chunk_id) is None
    assert store.has_chunk_record(old_chunk_id) is True


def test_partial_snapshot_connector_does_not_tombstone_missing_documents(tmp_path):
    kept = DocumentModel(
        id="kept",
        source_id="source_fake",
        title="Kept",
        content="This document remains.",
        url="https://example.com/kept",
        platform="Tistory",
        path="kept",
    )
    maybe_missing = DocumentModel(
        id="maybe-missing",
        source_id="source_fake",
        title="Maybe Missing",
        content="A partial crawler may omit this.",
        url="https://example.com/maybe-missing",
        platform="Tistory",
        path="maybe-missing",
    )
    connector = PartialSnapshotConnector([kept, maybe_missing])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    asyncio.run(service.sync_source("source_fake"))
    connector.documents = [kept]
    second = asyncio.run(service.sync_source("source_fake"))

    assert second.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("maybe-missing").deleted_at == ""
    assert len(store.list_chunks_for_document("maybe-missing")) == 1
    assert indexer.deleted_ids == []


def test_metadata_only_citation_change_refreshes_chunks_without_vector_reindex(tmp_path):
    first = DocumentModel(
        id="doc-meta",
        source_id="source_fake",
        title="Old Title",
        content="Same content.",
        url="https://example.com/old",
        canonical_url="https://example.com/old",
        platform="GitHub",
        path="old.md",
    )
    second = first.model_copy(
        update={
            "title": "New Title",
            "url": "https://example.com/new",
            "canonical_url": "https://example.com/new",
            "path": "new.md",
        }
    )
    connector = FakeConnector([first])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    asyncio.run(service.sync_source("source_fake"))
    connector.documents = [second]
    result = asyncio.run(service.sync_source("source_fake"))
    chunk = store.list_chunks_for_document("doc-meta")[0]

    assert result.skipped_documents == 1
    assert len(indexer.indexed_batches) == 1
    assert chunk.title == "New Title"
    assert chunk.url == "https://example.com/new"
    assert chunk.path == "new.md"


def test_unchanged_content_reindexes_when_chunk_strategy_changes(tmp_path):
    content = "# Intro\nContextWiki overview.\n## Install\nRun uv sync.\n"
    existing = DocumentModel(
        id="readme",
        source_id="source_fake",
        title="README",
        content=content,
        url="https://example.com/README",
        platform="GitHub",
        path="README",
    )
    old_chunk = ChunkModel(
        chunk_id="readme:chunk:0:legacy",
        document_id="readme",
        source_id="source_fake",
        title="README",
        text=content,
        url="https://example.com/README",
        path="README",
        chunk_index=0,
        line_start=1,
        line_end=4,
        content_hash=ContentHasher.hash_content(content),
    )
    fetched = existing.model_copy(
        update={
            "title": "README.md",
            "url": "https://example.com/README.md",
            "path": "README.md",
        }
    )
    connector = FakeConnector([fetched])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_document_and_replace_chunks(existing, [old_chunk])
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    result = asyncio.run(service.sync_source("source_fake"))
    chunks = store.list_chunks_for_document("readme")

    assert result.processed_documents == 1
    assert result.skipped_documents == 0
    assert result.indexed_chunks == 2
    assert len(indexer.indexed_batches) == 1
    assert indexer.deleted_ids == ["readme:chunk:0:legacy"]
    assert [chunk.line_start for chunk in chunks] == [1, 3]
    assert all(chunk.chunk_id != "readme:chunk:0:legacy" for chunk in chunks)


def test_changed_document_metadata_failure_does_not_delete_old_vectors(tmp_path):
    first = DocumentModel(
        id="doc-multi",
        source_id="source_fake",
        title="Multi Chunk",
        content=("A" * 30) + ("B" * 30),
        url="https://notion.so/multi",
        platform="Notion",
        path="Multi Chunk",
    )
    second = first.model_copy(update={"content": ("A" * 30) + ("C" * 30)})
    store = FailingOnceMetadataStore(tmp_path / "contextwiki.sqlite3")
    old_chunks = DocumentChunker(max_chars=30, overlap_chars=0).chunk_document(first)
    MetadataStore.upsert_document_and_replace_chunks(store, first, old_chunks)
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([FakeConnector([second])]),
        chunker=DocumentChunker(max_chars=30, overlap_chars=0),
        indexer=indexer,
    )

    failed = asyncio.run(service.sync_source("source_fake"))

    old_chunk_ids = {chunk.chunk_id for chunk in old_chunks}
    new_chunk_ids = {
        chunk.chunk_id
        for chunk in DocumentChunker(max_chars=30, overlap_chars=0).chunk_document(second)
    }
    assert failed.status == SyncJobStatus.FAILED
    assert set(indexer.deleted_ids) == new_chunk_ids - old_chunk_ids
    assert not old_chunk_ids.intersection(indexer.deleted_ids)
    assert [chunk.chunk_id for chunk in store.list_chunks_for_document("doc-multi")] == [
        chunk.chunk_id for chunk in old_chunks
    ]


def test_vector_delete_failure_after_tombstone_does_not_fail_sync_or_restore_chunks(tmp_path):
    removed = DocumentModel(
        id="removed",
        source_id="source_fake",
        title="Removed",
        content="This document disappears.",
        url="https://example.com/removed",
        platform="GitHub",
        path="removed.md",
    )
    connector = FakeConnector([removed])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = FailingDeleteIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    asyncio.run(service.sync_source("source_fake"))
    connector.documents = []
    result = asyncio.run(service.sync_source("source_fake"))

    assert result.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("removed").deleted_at
    assert store.list_chunks_for_document("removed") == []


def test_vector_delete_failure_logs_redacted_error(tmp_path, caplog):
    removed = DocumentModel(
        id="removed",
        source_id="source_fake",
        title="Removed",
        content="This document disappears.",
        url="https://example.com/removed",
        platform="GitHub",
        path="removed.md",
    )
    connector = FakeConnector([removed])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = FailingDeleteIndexer(
        "delete failed at /Users/eunhwa/private/vector.db "
        "credential=privatevalue token=secret-value token supersecretvalue123456"
    )
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    asyncio.run(service.sync_source("source_fake"))
    connector.documents = []
    with caplog.at_level(logging.ERROR, logger="indexing.ingestion_service"):
        result = asyncio.run(service.sync_source("source_fake"))

    assert result.status == SyncJobStatus.SUCCEEDED
    assert "credential=<redacted>" in caplog.text
    assert "token=<redacted>" in caplog.text
    assert "privatevalue" not in caplog.text
    assert "secret-value" not in caplog.text
    assert "/Users/eunhwa/private/vector.db" not in caplog.text
    assert "supersecretvalue123456" not in caplog.text


def test_success_finalization_failure_rolls_back_stale_cleanup(tmp_path):
    kept = DocumentModel(
        id="kept",
        source_id="source_fake",
        title="Kept",
        content="This document remains.",
        url="https://example.com/kept",
        platform="GitHub",
        path="kept.md",
    )
    removed = DocumentModel(
        id="removed",
        source_id="source_fake",
        title="Removed",
        content="This document should remain active if finalization fails.",
        url="https://example.com/removed",
        platform="GitHub",
        path="removed.md",
    )
    connector = FakeConnector([kept, removed])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    asyncio.run(service.sync_source("source_fake"))
    with store._connect() as conn:
        conn.execute(
            """
            CREATE TRIGGER fail_source_success
            BEFORE UPDATE OF sync_status ON sources
            WHEN NEW.sync_status = 'succeeded'
              AND NEW.last_synced_at != OLD.last_synced_at
            BEGIN
                SELECT RAISE(ABORT, 'source finish failed');
            END;
            """
        )
    connector.documents = [kept]
    failed = asyncio.run(service.sync_source("source_fake"))

    assert failed.status == SyncJobStatus.FAILED
    assert "source finish failed" in failed.error_message
    assert store.get_document("removed").deleted_at == ""
    assert len(store.list_chunks_for_document("removed")) == 1


def test_successful_sync_cleanup_uses_seen_marker_not_large_seen_id_list(tmp_path):
    kept = DocumentModel(
        id="kept",
        source_id="source_fake",
        title="Kept",
        content="This document remains.",
        url="https://example.com/kept",
        platform="GitHub",
        path="kept.md",
    )
    removed = DocumentModel(
        id="removed",
        source_id="source_fake",
        title="Removed",
        content="This document disappears.",
        url="https://example.com/removed",
        platform="GitHub",
        path="removed.md",
    )
    connector = FakeConnector([kept, removed])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    asyncio.run(service.sync_source("source_fake"))

    connector.documents = [kept]
    result = asyncio.run(service.sync_source("source_fake"))

    assert result.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("removed").deleted_at
    assert store.list_chunks_for_document("removed") == []
