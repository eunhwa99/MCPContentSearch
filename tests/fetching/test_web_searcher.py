import asyncio

import pytest

from core.models import DocumentModel
from environments.config import AppConfig
from fetching.github import GitHubSearcher
from fetching.web_searcher import WebSearcher


pytestmark = pytest.mark.unit


def test_github_searcher_ranks_title_and_path_matches(monkeypatch):
    docs = [
        DocumentModel(
            id="doc-1",
            document_id="doc-1",
            title="README",
            path="docs/aws-setup.md",
            content="setup guide for EC2 instances",
            url="https://github.com/example/repo/blob/main/docs/aws-setup.md",
            platform="GitHub",
            source_id="source_github",
        ),
        DocumentModel(
            id="doc-2",
            document_id="doc-2",
            title="misc",
            path="notes/random.txt",
            content="mentions aws once",
            url="https://github.com/example/repo/blob/main/notes/random.txt",
            platform="GitHub",
            source_id="source_github",
        ),
    ]

    async def fake_fetch_documents(self):
        return docs

    monkeypatch.setattr(
        "fetching.github.GitHubRepositoryFetcher.fetch_documents",
        fake_fetch_documents,
    )

    searcher = GitHubSearcher(
        ("eunhwa99/MCPContentSearch@main",),
        AppConfig(),
    )
    results = asyncio.run(searcher.search("aws setup", max_results=2))

    assert [doc.id for doc in results] == ["doc-1", "doc-2"]


def test_web_searcher_routes_github_platform(monkeypatch):
    captured = {}

    async def fake_search(self, query, max_results=10):
        captured["query"] = query
        captured["max_results"] = max_results
        return [
            DocumentModel(
                id="doc-1",
                title="GitHub doc",
                content="content",
                url="https://github.com/example/repo/blob/main/README.md",
                platform="github",
            )
        ]

    monkeypatch.setattr("fetching.github.GitHubSearcher.search", fake_search)

    searcher = WebSearcher(
        notion_api_key="",
        tistory_blog_name="",
        config=AppConfig(),
        github_repositories=("eunhwa99/MCPContentSearch@main",),
    )

    results = asyncio.run(searcher.search("readme", 5, platforms=["github"]))

    assert len(results) == 1
    assert results[0].title == "GitHub doc"
    assert captured == {"query": "readme", "max_results": 5}


def test_web_searcher_default_platforms_include_github_when_configured(monkeypatch):
    captured = {"github_calls": 0}

    async def fake_notion_search(self, query, max_results=10):
        return []

    async def fake_tistory_search(self, query, max_results=10):
        return []

    async def fake_github_search(self, query, max_results=10):
        captured["github_calls"] += 1
        return []

    monkeypatch.setattr("fetching.notion.NotionSearcher.search", fake_notion_search)
    monkeypatch.setattr("fetching.tistory.TistorySearcher.search", fake_tistory_search)
    monkeypatch.setattr("fetching.github.GitHubSearcher.search", fake_github_search)

    searcher = WebSearcher(
        notion_api_key="token",
        tistory_blog_name="blog",
        config=AppConfig(),
        github_repositories=("eunhwa99/MCPContentSearch@main",),
    )

    asyncio.run(searcher.search("readme", 5))

    assert captured["github_calls"] == 1
