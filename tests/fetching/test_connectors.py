import asyncio

import pytest

from core.models import DocumentModel
from environments.config import AppConfig
from fetching import connectors as connector_module
from fetching.connectors import (
    GitHubSourceConnector,
    NotionSourceConnector,
    TistorySourceConnector,
    WebsiteSourceConnector,
    build_source_registry,
)
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


def test_build_source_registry_includes_phase_b_sources():
    config = AppConfig(
        github_repositories=("eunhwa99/MCPContentSearch@main",),
        web_seed_urls=("https://docs.example.com",),
    )

    registry = build_source_registry(
        config=config,
        notion_api_key="notion-secret",
        tistory_blog_name="devlog",
        github_token="github-secret",
        github_http_client=object(),
        web_http_client=object(),
    )
    sources = {source.source_id: source for source in registry.list_sources()}

    assert set(sources) == {
        "source_github",
        "source_notion",
        "source_tistory",
        "source_web",
    }
    assert isinstance(registry.get_connector("source_github"), GitHubSourceConnector)
    assert isinstance(registry.get_connector("source_web"), WebsiteSourceConnector)
    assert sources["source_github"].enabled is True
    assert sources["source_github"].auth_ref == "env:GITHUB_TOKEN"
    assert sources["source_web"].enabled is True
    assert sources["source_web"].auth_ref == "env:CONTEXTWIKI_WEB_URLS"


def test_github_connector_uses_validated_custom_token_env_ref():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_token_env_var="CONTEXTWIKI_GITHUB_TOKEN"),
    )

    assert connector.source.auth_ref == "env:CONTEXTWIKI_GITHUB_TOKEN"
