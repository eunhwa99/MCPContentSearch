import asyncio

import pytest

from core.models import DocumentModel, SourceModel, SourceType, SyncJobStatus, SyncStatus
from indexing.chunker import DocumentChunker
from indexing.ingestion_service import IngestionService
from fetching.connectors import SourceConnector, SourceRegistry
from storage.metadata_store import MetadataStore


class FakeConnector(SourceConnector):
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


class RecordingIndexer:
    def __init__(self):
        self.indexed_batches = []
        self.deleted_ids = []

    async def index_documents(self, documents):
        self.indexed_batches.append(list(documents))

    def delete_documents_by_ids(self, document_ids):
        self.deleted_ids.extend(document_ids)


class FailingOnceIndexer(RecordingIndexer):
    def __init__(self):
        super().__init__()
        self.failed = False

    async def index_documents(self, documents):
        if not self.failed:
            self.failed = True
            raise RuntimeError("index failed")
        await super().index_documents(documents)


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
