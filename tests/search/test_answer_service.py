import asyncio

import pytest

from core.models import ContextSearchResult
from search.answer_service import CitationAnswerService


pytestmark = pytest.mark.unit


class FakeContextSearch:
    def __init__(self, results):
        self.results = results

    async def search_context(self, query, filters=None, top_k=5):
        return {"query": query, "results": self.results[:top_k]}


def test_answer_service_returns_insufficient_evidence_without_grounding():
    service = CitationAnswerService(
        context_search=FakeContextSearch([]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("What is ContextWiki?"))

    assert answer["evidence_status"] == "insufficient"
    assert answer["citations"] == []


def test_answer_service_uses_only_returned_context_as_citations():
    result = ContextSearchResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        source_id="source_fake",
        source_type="notion",
        title="ContextWiki",
        url="https://notion.so/doc-1",
        path="ContextWiki",
        line_start=12,
        line_end=18,
        version_id="page-version-1",
        score=0.92,
        preview="ContextWiki is an MCP knowledge backend.",
        text="ContextWiki is an MCP knowledge backend.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("What is ContextWiki?"))

    assert answer["evidence_status"] == "grounded"
    assert answer["citations"] == [
        {
            "chunk_id": "chunk-1",
            "title": "ContextWiki",
            "url": "https://notion.so/doc-1",
            "path": "ContextWiki",
            "line_start": 12,
            "line_end": 18,
            "version_id": "page-version-1",
        }
    ]
    assert "ContextWiki is an MCP knowledge backend." in answer["answer"]


def test_answer_service_rejects_high_score_context_without_query_terms():
    result = ContextSearchResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        source_id="source_github",
        source_type="github",
        title="eunhwa99/MCPContentSearch/web/index.html",
        url="https://github.com/eunhwa99/MCPContentSearch/blob/main/web/index.html",
        path="web/index.html",
        line_start=1,
        line_end=20,
        version_id="commit-1",
        score=0.92,
        preview="ContextWiki Local Console HTML.",
        text="<main>ContextWiki Local Console</main>",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(
        service.answer_with_citations("니트코드 알고리즘에서 그래프 관련 코드 알려줘")
    )

    assert answer["evidence_status"] == "insufficient"
    assert answer["citations"] == []


def test_answer_service_requires_strong_anchor_for_neetcode_queries():
    result = ContextSearchResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        source_id="source_github",
        source_type="github",
        title="Graph utilities",
        url="https://github.com/eunhwa99/MCPContentSearch/blob/main/search/graph.py",
        path="search/graph.py",
        line_start=1,
        line_end=20,
        version_id="commit-1",
        score=0.92,
        preview="A generic graph helper for search traversal.",
        text="A generic graph helper for search traversal.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(
        service.answer_with_citations("니트코드 알고리즘에서 그래프 관련 코드 알려줘")
    )

    assert answer["evidence_status"] == "insufficient"
    assert answer["citations"] == []


def test_answer_service_matches_common_korean_query_terms_to_english_context():
    result = ContextSearchResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        source_id="source_github",
        source_type="github",
        title="Project Structure",
        url="https://github.com/eunhwa99/MCPContentSearch#project-structure",
        path="README.md",
        line_start=1,
        line_end=20,
        version_id="commit-1",
        score=0.92,
        preview="Project Structure describes the search and indexing modules.",
        text="Project Structure describes the search and indexing modules.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("이 프로젝트 구조 정리해줘"))

    assert answer["evidence_status"] == "grounded"
    assert answer["citations"][0]["chunk_id"] == "chunk-1"
