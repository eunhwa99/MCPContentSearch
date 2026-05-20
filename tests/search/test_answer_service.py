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
    assert answer["citations"] == [{"chunk_id": "chunk-1", "title": "ContextWiki", "url": "https://notion.so/doc-1"}]
    assert "ContextWiki is an MCP knowledge backend." in answer["answer"]
