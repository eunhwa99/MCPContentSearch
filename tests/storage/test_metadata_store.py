import pytest

from core.models import (
    ChunkModel,
    DocumentModel,
    SourceModel,
    SourceType,
    SyncJobStatus,
    SyncStatus,
)
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.integration


def test_metadata_store_tracks_sources_jobs_documents_and_chunks(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.ensure_schema()

    source = store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            auth_ref="env:NOTION_API_KEY",
            sync_status=SyncStatus.IDLE,
        )
    )

    job = store.create_sync_job("source_notion")
    store.update_sync_job(
        job.job_id,
        status=SyncJobStatus.SUCCEEDED,
        total_documents=1,
        processed_documents=1,
        indexed_chunks=1,
        skipped_documents=0,
    )

    document = DocumentModel(
        id="notion_page_1",
        source_id="source_notion",
        title="Architecture Note",
        content="ContextWiki indexes knowledge with citations.",
        url="https://notion.so/page-1",
        platform="Notion",
        path="Architecture Note",
        updated_at="2026-05-20T00:00:00Z",
    )
    store.upsert_document(document)

    chunk = ChunkModel(
        chunk_id="notion_page_1:chunk:0:abc123",
        document_id="notion_page_1",
        source_id="source_notion",
        title="Architecture Note",
        text="ContextWiki indexes knowledge with citations.",
        url="https://notion.so/page-1",
        path="Architecture Note",
        chunk_index=0,
        content_hash="abc123",
        updated_at="2026-05-20T00:00:00Z",
    )
    store.replace_document_chunks("notion_page_1", [chunk])

    assert store.list_sources()[0].source_id == source.source_id
    assert store.get_latest_sync_job("source_notion").status == SyncJobStatus.SUCCEEDED
    assert store.get_document("notion_page_1").title == "Architecture Note"
    assert store.get_chunk(chunk.chunk_id).document_id == "notion_page_1"
    assert store.list_chunks_for_document("notion_page_1") == [chunk]


def test_atomic_document_chunk_commit_rolls_back_when_chunk_insert_fails(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.ensure_schema()
    document = DocumentModel(
        id="doc_atomic",
        source_id="source_notion",
        title="Atomic",
        content="Atomic metadata transaction",
        url="https://notion.so/atomic",
        platform="Notion",
    )
    duplicate_chunk = ChunkModel(
        chunk_id="duplicate",
        document_id="doc_atomic",
        source_id="source_notion",
        title="Atomic",
        text="chunk",
        chunk_index=0,
        content_hash="hash",
    )

    with pytest.raises(Exception):
        store.upsert_document_and_replace_chunks(document, [duplicate_chunk, duplicate_chunk])

    assert store.get_document("doc_atomic") is None
    assert store.list_chunks_for_document("doc_atomic") == []
