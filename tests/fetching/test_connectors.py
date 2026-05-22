import asyncio

import pytest

from core.models import DocumentModel
from environments.config import AppConfig
from fetching import connectors as connector_module
from fetching.connectors import NotionSourceConnector, TistorySourceConnector
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.unit


def test_notion_connector_persists_external_id(monkeypatch, tmp_path):
    async def fake_fetch_notion_pages(api_key, config):
        return [
            DocumentModel(
                id="notion_page-1",
                document_id="page-1",
                external_id="page-1",
                title="Page",
                content="body",
                url="https://notion.so/page-1",
                platform="Notion",
            )
        ]

    monkeypatch.setattr(connector_module, "fetch_notion_pages", fake_fetch_notion_pages)
    connector = NotionSourceConnector("secret", AppConfig())

    document = asyncio.run(connector.fetch_documents())[0]
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    persisted = store.upsert_document(document)

    assert document.source_id == "source_notion"
    assert document.document_id == "page-1"
    assert persisted.external_id == "page-1"
    assert persisted.canonical_url == "https://notion.so/page-1"


def test_tistory_connector_persists_external_id(monkeypatch, tmp_path):
    async def fake_fetch_tistory_posts(
        blog_name,
        max_id,
        connection_limit,
        request_timeout,
        log_interval,
    ):
        return [
            DocumentModel(
                id="tistory_7",
                document_id="devlog:7",
                external_id="devlog:7",
                title="Post",
                content="body",
                url="https://devlog.tistory.com/7",
                platform="Tistory",
            )
        ]

    monkeypatch.setattr(connector_module, "fetch_tistory_posts", fake_fetch_tistory_posts)
    connector = TistorySourceConnector("devlog", AppConfig(tistory_max_post_id=7))

    document = asyncio.run(connector.fetch_documents())[0]
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    persisted = store.upsert_document(document)

    assert document.source_id == "source_tistory"
    assert document.document_id == "devlog:7"
    assert persisted.external_id == "devlog:7"
    assert persisted.canonical_url == "https://devlog.tistory.com/7"
