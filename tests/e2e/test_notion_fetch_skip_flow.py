"""Deterministic E2E: unchanged Notion pages skip block fetch on second sync."""

from __future__ import annotations

import asyncio

import pytest

from core.models import SyncJobStatus
from environments.config import AppConfig
from fetching.connectors import NotionSourceConnector, SourceRegistry
from fetching.notion import NotionAPIClient
from indexing.chunker import DocumentChunker
from indexing.ingestion_service import IngestionService
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.e2e


class RecordingIndexer:
    def __init__(self):
        self.documents = []
        self.deleted_ids = []

    async def index_documents(self, documents):
        self.documents.extend(documents)

    def delete_documents_by_ids(self, document_ids, source_id=""):
        self.deleted_ids.extend(document_ids)


def test_second_notion_sync_skips_block_fetch_for_unchanged_pages(monkeypatch, tmp_path):
    page_id = "e2e-notion-unchanged"
    edited_at = "2026-07-01T10:00:00Z"
    page_title = "E2E Unchanged Notion Page"
    page_body = "Deterministic Notion body used for fetch-skip E2E coverage."
    fetch_calls: list[str] = []

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
                "id": page_id,
                "url": f"https://notion.so/{page_id}",
                "created_time": edited_at,
                "last_edited_time": edited_at,
                "properties": {
                    "title": {"title": [{"plain_text": page_title}]}
                },
            }
        ]

    async def fake_fetch_block_content(
        self,
        client,
        block_id,
        depth=0,
        strict=False,
        stop_checker=None,
    ):
        fetch_calls.append(block_id)
        return page_body

    monkeypatch.setattr(
        "fetching.notion.httpx.AsyncClient",
        lambda *args, **kwargs: FakeAsyncClient(),
    )
    monkeypatch.setattr(NotionAPIClient, "search_pages", fake_search_pages)
    monkeypatch.setattr(NotionAPIClient, "fetch_block_content", fake_fetch_block_content)

    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    connector = NotionSourceConnector(
        "secret",
        AppConfig(
            chroma_db_path=tmp_path / "chroma",
            metadata_db_path=tmp_path / "context_zip.sqlite3",
            cache_dir=str(tmp_path / "cache"),
        ),
        metadata_store=store,
    )
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    first_job = asyncio.run(ingestion.sync_source("source_notion"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert fetch_calls == [page_id]
    assert store.get_document(page_id) is not None
    assert store.get_document(page_id).content == page_body
    assert store.get_document(page_id).modified_at

    fetch_calls.clear()
    second_job = asyncio.run(ingestion.sync_source("source_notion"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert fetch_calls == []
    assert store.get_document(page_id).content == page_body


def test_skipped_unchanged_notion_page_is_not_tombstoned_when_peer_disappears(
    monkeypatch, tmp_path
):
    kept_id = "e2e-notion-kept-skip"
    removed_id = "e2e-notion-removed-peer"
    edited_at = "2026-07-01T10:00:00Z"
    kept_body = "Kept body reused on second sync."
    removed_body = "Removed peer body."
    fetch_calls: list[str] = []
    remote_pages = [
        {
            "id": kept_id,
            "url": f"https://notion.so/{kept_id}",
            "created_time": edited_at,
            "last_edited_time": edited_at,
            "properties": {
                "title": {"title": [{"plain_text": "Kept Skip Page"}]}
            },
        },
        {
            "id": removed_id,
            "url": f"https://notion.so/{removed_id}",
            "created_time": edited_at,
            "last_edited_time": edited_at,
            "properties": {
                "title": {"title": [{"plain_text": "Removed Peer Page"}]}
            },
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
        return list(remote_pages)

    async def fake_fetch_block_content(
        self,
        client,
        block_id,
        depth=0,
        strict=False,
        stop_checker=None,
    ):
        fetch_calls.append(block_id)
        if block_id == kept_id:
            return kept_body
        return removed_body

    monkeypatch.setattr(
        "fetching.notion.httpx.AsyncClient",
        lambda *args, **kwargs: FakeAsyncClient(),
    )
    monkeypatch.setattr(NotionAPIClient, "search_pages", fake_search_pages)
    monkeypatch.setattr(NotionAPIClient, "fetch_block_content", fake_fetch_block_content)

    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    connector = NotionSourceConnector(
        "secret",
        AppConfig(
            chroma_db_path=tmp_path / "chroma",
            metadata_db_path=tmp_path / "context_zip.sqlite3",
            cache_dir=str(tmp_path / "cache"),
        ),
        metadata_store=store,
    )
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    first_job = asyncio.run(ingestion.sync_source("source_notion"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert sorted(fetch_calls) == sorted([kept_id, removed_id])
    assert store.get_document(kept_id).deleted_at == ""
    assert store.get_document(removed_id).deleted_at == ""

    fetch_calls.clear()
    remote_pages[:] = [remote_pages[0]]
    second_job = asyncio.run(ingestion.sync_source("source_notion"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert fetch_calls == [], "kept page must skip block fetch"
    kept = store.get_document(kept_id)
    removed = store.get_document(removed_id)
    assert kept is not None
    assert kept.deleted_at == ""
    assert kept.last_seen_at
    assert kept.last_seen_sync_id == second_job.job_id
    assert kept.content == kept_body
    assert removed is not None
    assert removed.deleted_at
