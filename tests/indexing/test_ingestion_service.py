import asyncio
import logging

import pytest

import indexing.ingestion_service as ingestion_module
from core.models import ChunkModel, DocumentModel, SourceModel, SourceType, SyncJobStatus, SyncStatus
from core.utils import ContentHasher
from environments.config import AppConfig
from indexing.chunker import DocumentChunker
from indexing.ingestion_service import IngestionService
from fetching.connectors import SourceConnector, SourceRegistry
from search.service import SearchService
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
            "eyJheader.payloadvalue.signaturevalue"
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
    assert "password=<redacted>" in job.error_message
    assert "credential=<redacted>" in job.error_message
    assert "x-amz-credential=<redacted>" in job.error_message
    assert "secret-value" not in job.error_message
    assert "privatevalue" not in job.error_message
    assert "aws-privatevalue" not in job.error_message
    assert "ghp_secretcredential" not in job.error_message
    assert "AKIAIOSFODNN7EXAMPLE" not in job.error_message
    assert "xoxb-1234567890-secret" not in job.error_message
    assert "AIzaSyDExampleExampleExampleExample1234" not in job.error_message
    assert "eyJheader.payloadvalue.signaturevalue" not in job.error_message
    assert "secret-value" not in caplog.text
    assert "privatevalue" not in caplog.text
    assert "aws-privatevalue" not in caplog.text
    assert "ghp_secretcredential" not in caplog.text
    assert "AKIAIOSFODNN7EXAMPLE" not in caplog.text
    assert "xoxb-1234567890-secret" not in caplog.text
    assert "AIzaSyDExampleExampleExampleExample1234" not in caplog.text
    assert "eyJheader.payloadvalue.signaturevalue" not in caplog.text


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


def test_overlapping_source_sync_returns_existing_running_job_without_second_fetch(tmp_path):
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


def test_source_registration_preserves_existing_sync_status(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    connector = FakeConnector()
    service = IngestionService(
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
    raw_node = type(
        "FakeNode",
        (),
        {
            "metadata": {
                "doc_id": old_chunk_id,
                "title": "Old markerless raw",
            },
            "text": "old stale vector text",
            "score": 0.9,
        },
    )()
    search = SearchService(AppConfig(preview_length=80), indexer=None, metadata_store=store)

    assert store.get_document("reappears").deleted_at == ""
    assert active_chunks[0].chunk_id != old_chunk_id
    assert store.get_chunk(old_chunk_id) is None
    assert store.has_chunk_record(old_chunk_id) is True
    assert search._format_results("old", [raw_node], 10) == "No results found for 'old'"


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
    indexer = FailingDeleteIndexer("delete failed credential=privatevalue token=secret-value")
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
