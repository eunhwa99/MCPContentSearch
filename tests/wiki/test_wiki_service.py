import asyncio

import pytest

from core.models import ContextSearchResult
from wiki.service import WikiGenerationService


pytestmark = pytest.mark.unit


class FakeContextSearch:
    def __init__(self, results):
        self.results = results
        self.calls = []

    async def search_context(self, query, filters=None, top_k=10):
        self.calls.append({"query": query, "filters": filters, "top_k": top_k})
        return {"query": query, "results": self.results[:top_k]}


def test_generate_wiki_page_returns_citations_backlinks_and_markdown():
    service = WikiGenerationService(
        FakeContextSearch(
            [
                ContextSearchResult(
                    chunk_id="chunk-1",
                    document_id="doc-1",
                    source_id="source_github",
                    source_type="github",
                    title="README",
                    url="https://github.com/eunhwa99/MCPContentSearch/blob/main/README.md",
                    path="README.md",
                    score=0.91,
                    text="ContextWiki generates citation-backed wiki pages from indexed chunks.",
                    line_start=10,
                    line_end=12,
                    version_id="blob-sha",
                ),
                ContextSearchResult(
                    chunk_id="chunk-2",
                    document_id="doc-1",
                    source_id="source_github",
                    source_type="github",
                    title="README",
                    url="https://github.com/eunhwa99/MCPContentSearch/blob/main/README.md",
                    path="README.md",
                    score=0.82,
                    text="Backlinks point to source documents represented in the page evidence.",
                    line_start=14,
                    line_end=16,
                    version_id="blob-sha",
                ),
            ]
        )
    )

    result = asyncio.run(
        service.generate_wiki_page(
            "  Auto   Wiki  ",
            filters={"source_id": "source_github"},
            top_k=5,
        )
    )

    assert result["status"] == "generated"
    assert result["topic"] == "Auto Wiki"
    assert result["used_chunks"] == ["chunk-1", "chunk-2"]
    assert [citation["marker"] for citation in result["citations"]] == ["C1", "C2"]
    assert result["citations"][0]["line_start"] == 10
    assert result["backlinks"] == [
        {
            "document_id": "doc-1",
            "source_id": "source_github",
            "source_type": "github",
            "title": "README",
            "url": "https://github.com/eunhwa99/MCPContentSearch/blob/main/README.md",
            "path": "README.md",
            "chunk_ids": ["chunk-1", "chunk-2"],
        }
    ]
    assert "# Auto Wiki Wiki" in result["markdown"]
    assert "[C1]" in result["markdown"]
    assert "`chunk-1`" in result["markdown"]


def test_generate_wiki_page_respects_filters_and_top_k_limit():
    fake_search = FakeContextSearch(
        [
            ContextSearchResult(
                chunk_id="chunk-1",
                document_id="doc-1",
                source_id="source_notion",
                source_type="notion",
                title="ContextWiki",
                score=0.7,
                text="ContextWiki evidence.",
            )
        ]
    )
    service = WikiGenerationService(fake_search, max_top_k=3)

    result = asyncio.run(
        service.generate_wiki_page(
            "ContextWiki",
            filters={"source_ids": ["source_notion"]},
            top_k=99,
        )
    )

    assert result["status"] == "generated"
    assert fake_search.calls == [
        {
            "query": "ContextWiki",
            "filters": {"source_ids": ["source_notion"]},
            "top_k": 3,
        }
    ]


def test_generate_wiki_page_gates_low_score_results_as_insufficient():
    service = WikiGenerationService(
        FakeContextSearch(
            [
                ContextSearchResult(
                    chunk_id="chunk-low",
                    document_id="doc-low",
                    source_id="source_github",
                    source_type="github",
                    title="Unrelated",
                    score=0.1,
                    text="A weak nearest-neighbor result should not become a wiki page.",
                )
            ]
        ),
        min_score=0.35,
    )

    result = asyncio.run(service.generate_wiki_page("Precise topic"))

    assert result["status"] == "insufficient_evidence"
    assert result["used_chunks"] == []
    assert result["citations"] == []


def test_generate_wiki_page_returns_insufficient_evidence_without_results():
    service = WikiGenerationService(FakeContextSearch([]))

    result = asyncio.run(service.generate_wiki_page("Missing topic"))

    assert result["status"] == "insufficient_evidence"
    assert result["citations"] == []
    assert result["backlinks"] == []
    assert "Insufficient evidence" in result["message"]


def test_generate_wiki_page_requires_topic():
    service = WikiGenerationService(FakeContextSearch([]))

    result = asyncio.run(service.generate_wiki_page("   "))

    assert result["status"] == "insufficient_evidence"
    assert result["topic"] == ""
    assert "non-empty topic" in result["message"]
