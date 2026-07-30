"""Integration: Notion connector + MetadataStore fetch-before-index skip."""

from __future__ import annotations

import asyncio

import pytest

from core.models import DocumentModel
from core.utils import ContentHasher
from environments.config import AppConfig
from fetching.connectors import NotionSourceConnector
from fetching.notion import NotionAPIClient
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.integration


def _stored_notion_document(
    page_id: str,
    *,
    content: str,
    modified_at: str,
) -> DocumentModel:
    return DocumentModel(
        id=f"notion_{page_id}",
        document_id=page_id,
        external_id=page_id,
        source_id="source_notion",
        title="Stored Notion Page",
        content=content,
        url=f"https://notion.so/{page_id}",
        canonical_url=f"https://notion.so/{page_id}",
        platform="Notion",
        path="Stored Notion Page",
        modified_at=modified_at,
        published_at=modified_at,
        content_hash=ContentHasher.hash_content(content),
    )


@pytest.mark.integration
def test_notion_connector_skips_block_fetch_for_unchanged_stored_page(monkeypatch, tmp_path):
    page_id = "page-stored-unchanged"
    edited_at = "2026-06-01T00:00:00Z"
    stored_content = "already indexed notion body"
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.upsert_document(
        _stored_notion_document(page_id, content=stored_content, modified_at=edited_at)
    )

    fetch_calls: list[str] = []
    captured: dict[str, object] = {}

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
                    "title": {"title": [{"plain_text": "Stored Notion Page"}]}
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
        return f"should-not-fetch-{block_id}"

    monkeypatch.setattr(
        "fetching.notion.httpx.AsyncClient",
        lambda *args, **kwargs: FakeAsyncClient(),
    )
    monkeypatch.setattr(NotionAPIClient, "search_pages", fake_search_pages)
    monkeypatch.setattr(NotionAPIClient, "fetch_block_content", fake_fetch_block_content)

    list_calls: list[object] = []
    get_calls: list[str] = []
    original_list = store.list_documents
    original_get = store.get_document

    def tracking_list_documents(*args, **kwargs):
        list_calls.append({"args": args, "kwargs": kwargs})
        return original_list(*args, **kwargs)

    def tracking_get_document(document_id):
        get_calls.append(document_id)
        return original_get(document_id)

    store.list_documents = tracking_list_documents  # type: ignore[method-assign]
    store.get_document = tracking_get_document  # type: ignore[method-assign]

    original_fetch = __import__("fetching.notion", fromlist=["fetch_notion_pages"]).fetch_notion_pages

    async def spy_fetch_notion_pages(*args, **kwargs):
        captured["existing_documents"] = kwargs.get("existing_documents")
        captured["existing_documents_loader"] = kwargs.get("existing_documents_loader")
        return await original_fetch(*args, **kwargs)

    monkeypatch.setattr(
        "fetching.connectors.fetch_notion_pages",
        spy_fetch_notion_pages,
    )

    connector = NotionSourceConnector("secret", AppConfig(), metadata_store=store)
    documents = asyncio.run(connector.fetch_documents())

    assert fetch_calls == []
    assert len(documents) == 1
    assert documents[0].document_id == page_id
    assert documents[0].content == stored_content
    assert list_calls == [], "must not browse full corpus via list_documents"
    assert get_calls == [page_id]
    loader = captured.get("existing_documents_loader")
    assert callable(loader)
    loaded = loader([page_id])
    assert page_id in loaded
    assert loaded[page_id].content == stored_content


@pytest.mark.integration
def test_notion_connector_fetches_when_stored_modified_at_differs(monkeypatch, tmp_path):
    page_id = "page-stored-changed"
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.upsert_document(
        _stored_notion_document(
            page_id,
            content="old body",
            modified_at="2026-06-01T00:00:00Z",
        )
    )

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
                "created_time": "2026-06-01T00:00:00Z",
                "last_edited_time": "2026-06-02T00:00:00Z",
                "properties": {
                    "title": {"title": [{"plain_text": "Stored Notion Page"}]}
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
        return f"fresh body for {block_id}"

    monkeypatch.setattr(
        "fetching.notion.httpx.AsyncClient",
        lambda *args, **kwargs: FakeAsyncClient(),
    )
    monkeypatch.setattr(NotionAPIClient, "search_pages", fake_search_pages)
    monkeypatch.setattr(NotionAPIClient, "fetch_block_content", fake_fetch_block_content)

    connector = NotionSourceConnector("secret", AppConfig(), metadata_store=store)
    documents = asyncio.run(connector.fetch_documents())

    assert fetch_calls == [page_id]
    assert documents[0].content == f"fresh body for {page_id}"


@pytest.mark.integration
def test_build_ingestion_runtime_wires_metadata_store_onto_notion_connector(tmp_path):
    from app_runtime import build_ingestion_runtime

    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "contextwiki.sqlite3",
        cache_dir=str(tmp_path / "cache"),
        github_repositories=(),
    )

    class FakeCollection:
        pass

    class FakeVectorStore:
        def __init__(self, chroma_collection):
            self.chroma_collection = chroma_collection

    class FakeStorageContext:
        @staticmethod
        def from_defaults(vector_store):
            return {"vector_store": vector_store}

    class FakeIndexer:
        def __init__(self, config, chroma_collection, storage_context):
            self.config = config

    runtime = build_ingestion_runtime(
        config=config,
        notion_api_key="notion-secret",
        tistory_blog_name="",
        github_token="",
        setup_chroma_fn=lambda _config: FakeCollection(),
        vector_store_cls=FakeVectorStore,
        storage_context_cls=FakeStorageContext,
        indexer_cls=FakeIndexer,
    )
    connector = runtime.source_registry.get_connector("source_notion")

    assert isinstance(connector, NotionSourceConnector)
    assert connector.metadata_store is runtime.metadata_store


@pytest.mark.integration
def test_notion_connector_does_not_browse_unrelated_stored_documents(monkeypatch, tmp_path):
    kept_id = "page-kept-for-skip"
    unrelated_id = "page-unrelated-corpus"
    edited_at = "2026-06-01T00:00:00Z"
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.upsert_document(
        _stored_notion_document(kept_id, content="kept body", modified_at=edited_at)
    )
    store.upsert_document(
        _stored_notion_document(
            unrelated_id,
            content="unrelated body that must not be preloaded",
            modified_at=edited_at,
        )
    )

    fetch_calls: list[str] = []
    list_calls: list[object] = []
    get_calls: list[str] = []
    original_list = store.list_documents
    original_get = store.get_document

    def tracking_list_documents(*args, **kwargs):
        list_calls.append({"args": args, "kwargs": kwargs})
        return original_list(*args, **kwargs)

    def tracking_get_document(document_id):
        get_calls.append(document_id)
        return original_get(document_id)

    store.list_documents = tracking_list_documents  # type: ignore[method-assign]
    store.get_document = tracking_get_document  # type: ignore[method-assign]

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
                "id": kept_id,
                "url": f"https://notion.so/{kept_id}",
                "created_time": edited_at,
                "last_edited_time": edited_at,
                "properties": {
                    "title": {"title": [{"plain_text": "Stored Notion Page"}]}
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
        return f"should-not-fetch-{block_id}"

    monkeypatch.setattr(
        "fetching.notion.httpx.AsyncClient",
        lambda *args, **kwargs: FakeAsyncClient(),
    )
    monkeypatch.setattr(NotionAPIClient, "search_pages", fake_search_pages)
    monkeypatch.setattr(NotionAPIClient, "fetch_block_content", fake_fetch_block_content)

    connector = NotionSourceConnector("secret", AppConfig(), metadata_store=store)
    documents = asyncio.run(connector.fetch_documents())

    assert fetch_calls == []
    assert [doc.document_id for doc in documents] == [kept_id]
    assert documents[0].content == "kept body"
    assert list_calls == []
    assert get_calls == [kept_id]
    assert unrelated_id not in get_calls
