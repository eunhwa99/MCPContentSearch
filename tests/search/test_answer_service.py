import asyncio

import pytest

from core.models import (
    ChunkModel,
    ContextSearchResult,
    DocumentModel,
    SourceModel,
    SourceType,
    SyncStatus,
)
from search.answer_service import CitationAnswerService
from search.context_service import ContextSearchService
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.unit


class FakeContextSearch:
    def __init__(self, results):
        self.results = results

    async def search_context(
        self,
        query,
        filters=None,
        top_k=5,
        include_debug=False,
        include_internal_metadata=False,
    ):
        return {"query": query, "results": self.results[:top_k]}


class RecordingContextSearch(FakeContextSearch):
    def __init__(self, results):
        super().__init__(results)
        self.calls = []

    async def search_context(
        self,
        query,
        filters=None,
        top_k=5,
        include_debug=False,
        include_internal_metadata=False,
    ):
        self.calls.append(
            {
                "query": query,
                "filters": filters,
                "top_k": top_k,
                "include_debug": include_debug,
                "include_internal_metadata": include_internal_metadata,
            }
        )
        return await super().search_context(
            query,
            filters=filters,
            top_k=top_k,
            include_debug=include_debug,
            include_internal_metadata=include_internal_metadata,
        )


def test_answer_service_returns_insufficient_evidence_without_grounding():
    service = CitationAnswerService(
        context_search=FakeContextSearch([]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("What is ContextZip?", include_debug=True))

    assert answer["evidence_status"] == "insufficient"
    assert answer["citations"] == []


def test_answer_service_uses_only_returned_context_as_citations():
    result = ContextSearchResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        source_id="source_fake",
        source_type="notion",
        title="ContextZip",
        url="https://notion.so/doc-1",
        path="ContextZip",
        line_start=12,
        line_end=18,
        version_id="page-version-1",
        score=0.92,
        preview="ContextZip is an MCP knowledge backend.",
        text="ContextZip is an MCP knowledge backend.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("What is ContextZip?", include_debug=True))

    assert answer["evidence_status"] == "grounded"
    assert answer["answer_mode"] == "context_zip_debug"
    assert answer["citations"] == [
        {
            "chunk_id": "chunk-1",
            "title": "ContextZip",
            "url": "https://notion.so/doc-1",
            "path": "ContextZip",
            "line_start": 12,
            "line_end": 18,
            "version_id": "page-version-1",
        }
    ]
    assert "## Summary" in answer["answer"]
    assert "## Best Matches" in answer["answer"]
    assert "[C1]" in answer["answer"]
    assert "## Query" in answer["debug_markdown"]
    assert answer["debug"]["selected_chunks"][0]["matched_terms"]


def test_answer_service_always_requests_search_debug_for_internal_grounding():
    result = ContextSearchResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        source_id="source_fake",
        source_type="notion",
        title="ContextZip",
        score=0.92,
        preview="ContextZip is an MCP knowledge backend.",
        text="ContextZip is an MCP knowledge backend.",
    )
    context_search = RecordingContextSearch([result])
    service = CitationAnswerService(
        context_search=context_search,
        min_score=0.5,
        min_results=1,
    )

    asyncio.run(service.answer_with_citations("What is ContextZip?"))
    asyncio.run(service.answer_with_citations("What is ContextZip?", include_debug=True))

    assert context_search.calls[0]["include_debug"] is False
    assert context_search.calls[0]["include_internal_metadata"] is True
    assert context_search.calls[1]["include_debug"] is True
    assert context_search.calls[1]["include_internal_metadata"] is True


def test_answer_service_rejects_high_score_context_without_query_terms():
    result = ContextSearchResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        source_id="source_github",
        source_type="github",
        title="eunhwa99/context-zip/README.md",
        url="https://github.com/eunhwa99/context-zip/blob/main/README.md",
        path="README.md",
        line_start=1,
        line_end=20,
        version_id="commit-1",
        score=0.92,
        preview="ContextZip project overview.",
        text="ContextZip project overview.",
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
        url="https://github.com/eunhwa99/context-zip/blob/main/search/graph.py",
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
        url="https://github.com/eunhwa99/context-zip#project-structure",
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


def test_answer_service_grounds_korean_obsidian_source_intent_from_source_type():
    result = ContextSearchResult(
        chunk_id="daily-planning-chunk",
        document_id="daily/planning.md",
        source_id="source_obsidian",
        source_type="obsidian",
        title="Daily Planning",
        url="obsidian://open?vault=team&file=daily%2Fplanning.md",
        path="daily/planning.md",
        line_start=1,
        line_end=20,
        version_id="hash-1",
        score=0.92,
        preview="Local markdown planning archive and weekly notes.",
        text="Local markdown planning archive and weekly notes.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("옵시디언"))

    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == ["daily-planning-chunk"]
    assert answer["citations"][0]["chunk_id"] == "daily-planning-chunk"


def test_answer_service_treats_broad_usage_terms_as_optional_hints():
    result = ContextSearchResult(
        chunk_id="chunk-aws-overview",
        document_id="doc-aws-overview",
        source_id="source_github",
        source_type="github",
        title="AWS Overview",
        url="https://github.com/eunhwa99/context-zip/blob/main/docs/aws-overview.md",
        path="docs/aws-overview.md",
        line_start=1,
        line_end=20,
        version_id="commit-1",
        score=0.92,
        preview="Amazon Web Services overview and service notes.",
        text="Amazon Web Services overview and service notes.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("AWS 사용법"))

    assert answer["evidence_status"] == "grounded"
    assert answer["citations"][0]["chunk_id"] == "chunk-aws-overview"


def test_answer_service_uses_effective_term_groups_from_search_debug():
    result = ContextSearchResult(
        chunk_id="chunk-ec2-guide",
        document_id="doc-ec2-guide",
        source_id="source_github",
        source_type="github",
        title="EC2 setup guide",
        url="https://github.com/eunhwa99/context-zip/blob/main/docs/ec2-guide.md",
        path="docs/ec2-guide.md",
        line_start=1,
        line_end=20,
        version_id="commit-1",
        score=0.92,
        preview="EC2 setup and instance launch notes.",
        text="EC2 setup and instance launch notes.",
    )

    class DeterministicDebugContextSearch(FakeContextSearch):
        async def search_context(
            self,
            query,
            filters=None,
            top_k=5,
            include_debug=False,
            include_internal_metadata=False,
        ):
            return {
                "query": query,
                "results": [result],
                "debug": {
                    "effective_term_groups": [["aws"], ["ec2"], ["setup"]],
                    "retrieval_queries": ["aws virtual machine startup", "aws ec2 setup"],
                },
            }

    service = CitationAnswerService(
        context_search=DeterministicDebugContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("aws virtual machine startup", include_debug=True))

    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == ["chunk-ec2-guide"]
    assert answer["debug"]["retrieval_queries"] == [
        "aws virtual machine startup",
        "aws ec2 setup",
    ]


def test_answer_service_carries_retrieval_explainability_from_search_debug():
    result = ContextSearchResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        source_id="source_fake",
        source_type="notion",
        title="EC2 setup",
        score=0.9,
        preview="EC2 setup notes",
        text="EC2 setup notes",
    )

    class ExplainedContextSearch(FakeContextSearch):
        async def search_context(
            self,
            query,
            filters=None,
            top_k=5,
            include_debug=False,
            include_internal_metadata=False,
        ):
            return {
                "query": query,
                "results": [result],
                "_grounding": {
                    "original_term_groups": [["aws"], ["virtual"], ["machine"]],
                    "effective_term_groups": [["aws"], ["ec2"], ["setup"]],
                },
                "debug": {
                    "retrieval_queries": ["aws virtual machine startup", "aws ec2 setup"],
                    "effective_term_groups": [["aws"], ["ec2"], ["setup"]],
                    "filters": {"source_ids": ["source_fake"]},
                    "selected_results": [{"chunk_id": "chunk-1", "source_id": "source_fake"}],
                },
            }

    service = CitationAnswerService(
        context_search=ExplainedContextSearch([result]),
        min_score=0.1,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("aws virtual machine startup", include_debug=True))

    assert answer["debug"]["retrieval_queries"] == [
        "aws virtual machine startup",
        "aws ec2 setup",
    ]
    assert answer["debug"]["filters"] == {"source_ids": ["source_fake"]}
    assert answer["debug"]["retrieval_selected_results"][0]["chunk_id"] == "chunk-1"
    assert "rewrite" not in answer["debug_markdown"]


def test_answer_service_debug_markdown_contains_only_deterministic_queries():
    result = ContextSearchResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        source_id="source_fake",
        source_type="notion",
        title="EC2 setup",
        score=0.9,
        preview="EC2 setup notes",
        text="EC2 setup notes",
    )

    class DeterministicContextSearch(FakeContextSearch):
        async def search_context(
            self,
            query,
            filters=None,
            top_k=5,
            include_debug=False,
            include_internal_metadata=False,
        ):
            return {
                "query": query,
                "results": [result],
                "debug": {
                    "retrieval_queries": ["aws virtual machine startup"],
                    "filters": {"source_ids": ["source_fake"]},
                    "selected_results": [{"chunk_id": "chunk-1", "source_id": "source_fake"}],
                },
            }

    service = CitationAnswerService(
        context_search=DeterministicContextSearch([result]),
        min_score=0.1,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("aws virtual machine startup", include_debug=True))

    assert "- retrieval queries: `aws virtual machine startup`" in answer["debug_markdown"]
    assert "rewrite" not in answer["debug_markdown"]


def test_answer_service_renders_grounded_list_for_collection_request():
    results = [
        ContextSearchResult(
            chunk_id="chunk-aws-1",
            document_id="doc-aws-1",
            source_id="source_notion",
            source_type="notion",
            title="AWS deployment guide",
            url="https://www.notion.so/aws-deployment-guide",
            path="AWS deployment guide",
            line_start=1,
            line_end=20,
            version_id="v1",
            score=0.92,
            preview="Deployment checklist for AWS environment setup.",
            text="Deployment checklist for AWS environment setup.",
        ),
        ContextSearchResult(
            chunk_id="chunk-aws-2",
            document_id="doc-aws-2",
            source_id="source_github",
            source_type="github",
            title="AWS runtime notes",
            url="https://github.com/example/repo/blob/main/docs/aws-runtime.md",
            path="docs/aws-runtime.md",
            line_start=1,
            line_end=20,
            version_id="v2",
            score=0.88,
            preview="Runtime notes for AWS service usage.",
            text="Runtime notes for AWS service usage.",
        ),
    ]
    service = CitationAnswerService(
        context_search=FakeContextSearch(results),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("AWS 관련 문서 모아줘"))

    assert answer["evidence_status"] == "grounded"
    assert "## Grounded List" in answer["answer"]
    assert "- [C1]" in answer["answer"]
    assert "- [C2]" in answer["answer"]


def test_answer_service_dedupes_grounded_list_by_document():
    results = [
        ContextSearchResult(
            chunk_id="chunk-aws-1",
            document_id="doc-aws-1",
            source_id="source_notion",
            source_type="notion",
            title="AWS deployment guide",
            url="https://www.notion.so/aws-deployment-guide",
            path="AWS deployment guide",
            score=0.92,
            preview="Deployment checklist for AWS environment setup.",
            text="Deployment checklist for AWS environment setup.",
        ),
        ContextSearchResult(
            chunk_id="chunk-aws-1b",
            document_id="doc-aws-1",
            source_id="source_notion",
            source_type="notion",
            title="AWS deployment guide",
            url="https://www.notion.so/aws-deployment-guide",
            path="AWS deployment guide",
            score=0.9,
            preview="More deployment notes for the same AWS document.",
            text="More deployment notes for the same AWS document.",
        ),
    ]
    service = CitationAnswerService(
        context_search=FakeContextSearch(results),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("AWS 관련 문서 모아줘"))

    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == ["chunk-aws-1"]
    assert "- [C2]" not in answer["answer"]


def test_answer_service_list_requires_specific_topic_match_not_only_docs_intent():
    results = [
        ContextSearchResult(
            chunk_id="chunk-k8s",
            document_id="doc-k8s",
            source_id="source_github",
            source_type="github",
            title="Kubernetes deployment guide",
            url="https://github.com/example/repo/blob/main/docs/k8s-guide.md",
            path="docs/k8s-guide.md",
            score=0.92,
            preview="Kubernetes deployment guide and rollout notes.",
            text="Kubernetes deployment guide and rollout notes.",
        ),
        ContextSearchResult(
            chunk_id="chunk-frontend",
            document_id="doc-frontend",
            source_id="source_notion",
            source_type="notion",
            title="Frontend notes",
            url="https://www.notion.so/frontend-notes",
            path="Frontend notes",
            score=0.9,
            preview="Frontend implementation notes.",
            text="Frontend implementation notes.",
        ),
    ]
    service = CitationAnswerService(
        context_search=FakeContextSearch(results),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("AWS 관련 문서 모아줘"))

    assert answer["evidence_status"] == "insufficient"
    assert answer["citations"] == []


def test_answer_service_list_requires_all_specific_constraints():
    results = [
        ContextSearchResult(
            chunk_id="notion-k8s",
            document_id="doc-notion-k8s",
            source_id="source_notion",
            source_type="notion",
            title="Kubernetes deployment notes",
            url="https://www.notion.so/kubernetes-deployment-notes",
            path="Kubernetes deployment notes",
            score=0.92,
            preview="Kubernetes deployment notes in Notion.",
            text="Kubernetes deployment notes in Notion.",
        )
    ]
    service = CitationAnswerService(
        context_search=FakeContextSearch(results),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("AWS Notion 문서 모아줘"))

    assert answer["evidence_status"] == "insufficient"
    assert answer["citations"] == []


def test_answer_service_renders_grounded_comparison_for_comparison_request():
    results = [
        ContextSearchResult(
            chunk_id="chunk-ddb",
            document_id="doc-ddb",
            source_id="source_notion",
            source_type="notion",
            title="DynamoDB notes",
            url="https://www.notion.so/dynamodb-notes",
            path="DynamoDB notes",
            line_start=1,
            line_end=20,
            version_id="v1",
            score=0.92,
            preview="DynamoDB strengths and scaling notes.",
            text="DynamoDB strengths and scaling notes.",
        ),
        ContextSearchResult(
            chunk_id="chunk-cassandra",
            document_id="doc-cassandra",
            source_id="source_tistory",
            source_type="tistory",
            title="Cassandra notes",
            url="https://devlog.tistory.com/cassandra-notes",
            path="Cassandra notes",
            line_start=1,
            line_end=20,
            version_id="v2",
            score=0.9,
            preview="Cassandra consistency and partitioning notes.",
            text="Cassandra consistency and partitioning notes.",
        ),
    ]
    service = CitationAnswerService(
        context_search=FakeContextSearch(results),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("DynamoDB vs Cassandra 차이 비교"))

    assert answer["evidence_status"] == "grounded"
    assert "## Grounded Comparison" in answer["answer"]
    assert "DynamoDB notes" in answer["answer"]
    assert "Cassandra notes" in answer["answer"]


def test_answer_service_requires_two_sides_for_grounded_comparison():
    results = [
        ContextSearchResult(
            chunk_id="chunk-ddb",
            document_id="doc-ddb",
            source_id="source_notion",
            source_type="notion",
            title="DynamoDB notes",
            url="https://www.notion.so/dynamodb-notes",
            path="DynamoDB notes",
            score=0.92,
            preview="DynamoDB comparison notes and scaling characteristics.",
            text="DynamoDB comparison notes and scaling characteristics.",
        )
    ]
    service = CitationAnswerService(
        context_search=FakeContextSearch(results),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("DynamoDB vs Cassandra 차이 비교"))

    assert answer["evidence_status"] == "insufficient"
    assert answer["citations"] == []


def test_answer_service_comparison_rejects_repeated_comparison_words_without_second_side():
    results = [
        ContextSearchResult(
            chunk_id="chunk-ddb-1",
            document_id="doc-ddb-1",
            source_id="source_notion",
            source_type="notion",
            title="DynamoDB comparison notes",
            url="https://www.notion.so/dynamodb-comparison-notes",
            path="DynamoDB comparison notes",
            score=0.92,
            preview="DynamoDB comparison notes and scaling characteristics.",
            text="DynamoDB comparison notes and scaling characteristics.",
        ),
        ContextSearchResult(
            chunk_id="chunk-ddb-2",
            document_id="doc-ddb-2",
            source_id="source_tistory",
            source_type="tistory",
            title="DynamoDB vs partitioning notes",
            url="https://devlog.tistory.com/dynamodb-vs-partitioning",
            path="DynamoDB vs partitioning notes",
            score=0.9,
            preview="DynamoDB vs partitioning operational notes.",
            text="DynamoDB vs partitioning operational notes.",
        ),
    ]
    service = CitationAnswerService(
        context_search=FakeContextSearch(results),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("DynamoDB vs Cassandra comparison"))

    assert answer["evidence_status"] == "insufficient"
    assert answer["citations"] == []


def test_answer_service_comparison_accepts_single_document_covering_both_sides():
    results = [
        ContextSearchResult(
            chunk_id="chunk-ddb-cassandra",
            document_id="doc-ddb-cassandra",
            source_id="source_notion",
            source_type="notion",
            title="DynamoDB vs Cassandra notes",
            url="https://www.notion.so/dynamodb-vs-cassandra-notes",
            path="DynamoDB vs Cassandra notes",
            score=0.92,
            preview="DynamoDB and Cassandra tradeoffs, consistency, and scaling notes.",
            text="DynamoDB and Cassandra tradeoffs, consistency, and scaling notes.",
        )
    ]
    service = CitationAnswerService(
        context_search=FakeContextSearch(results),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("DynamoDB vs Cassandra comparison"))

    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == ["chunk-ddb-cassandra"]


def test_answer_service_comparison_accepts_two_chunks_from_same_document():
    results = [
        ContextSearchResult(
            chunk_id="chunk-ddb",
            document_id="doc-ddb-cassandra",
            source_id="source_notion",
            source_type="notion",
            title="DynamoDB vs Cassandra notes",
            url="https://www.notion.so/dynamodb-vs-cassandra-notes",
            path="DynamoDB vs Cassandra notes",
            score=0.92,
            preview="DynamoDB tradeoffs and scaling notes.",
            text="DynamoDB tradeoffs and scaling notes.",
        ),
        ContextSearchResult(
            chunk_id="chunk-cassandra",
            document_id="doc-ddb-cassandra",
            source_id="source_notion",
            source_type="notion",
            title="DynamoDB vs Cassandra notes",
            url="https://www.notion.so/dynamodb-vs-cassandra-notes",
            path="DynamoDB vs Cassandra notes",
            score=0.9,
            preview="Cassandra consistency and partitioning notes.",
            text="Cassandra consistency and partitioning notes.",
        ),
    ]
    service = CitationAnswerService(
        context_search=FakeContextSearch(results),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("DynamoDB vs Cassandra comparison"))

    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == ["chunk-ddb", "chunk-cassandra"]


def test_answer_service_comparison_oversamples_retrieval_to_recover_second_side():
    results = [
        ContextSearchResult(
            chunk_id=f"ddb-{index}",
            document_id=f"doc-ddb-{index}",
            source_id="source_notion",
            source_type="notion",
            title=f"DynamoDB notes {index}",
            url=f"https://www.notion.so/dynamodb-notes-{index}",
            path=f"DynamoDB notes {index}",
            score=0.95 - (index * 0.01),
            preview="DynamoDB scaling notes.",
            text="DynamoDB scaling notes.",
        )
        for index in range(5)
    ] + [
        ContextSearchResult(
            chunk_id="cass-0",
            document_id="doc-cass-0",
            source_id="source_tistory",
            source_type="tistory",
            title="Cassandra notes 0",
            url="https://devlog.tistory.com/cassandra-notes-0",
            path="Cassandra notes 0",
            score=0.7,
            preview="Cassandra consistency notes.",
            text="Cassandra consistency notes.",
        )
    ]
    context_search = RecordingContextSearch(results)
    service = CitationAnswerService(
        context_search=context_search,
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("DynamoDB vs Cassandra comparison", top_k=5))

    assert context_search.calls[0]["top_k"] > 5
    assert answer["evidence_status"] == "grounded"
    assert "cass-0" in answer["used_chunks"]


def test_answer_service_comparison_rejects_generic_compare_docs_request():
    results = [
        ContextSearchResult(
            chunk_id="chunk-docs-1",
            document_id="doc-docs-1",
            source_id="source_github",
            source_type="github",
            title="Architecture docs",
            url="https://github.com/example/repo/blob/main/docs/architecture.md",
            path="docs/architecture.md",
            score=0.92,
            preview="Architecture docs and design notes.",
            text="Architecture docs and design notes.",
        ),
        ContextSearchResult(
            chunk_id="chunk-docs-2",
            document_id="doc-docs-2",
            source_id="source_notion",
            source_type="notion",
            title="Runtime docs",
            url="https://www.notion.so/runtime-docs",
            path="Runtime docs",
            score=0.9,
            preview="Runtime docs and operational notes.",
            text="Runtime docs and operational notes.",
        ),
    ]
    service = CitationAnswerService(
        context_search=FakeContextSearch(results),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("compare docs"))

    assert answer["evidence_status"] == "insufficient"
    assert answer["citations"] == []


def test_answer_service_comparison_rejects_hint_only_extra_document():
    results = [
        ContextSearchResult(
            chunk_id="chunk-ddb-cassandra",
            document_id="doc-ddb-cassandra",
            source_id="source_notion",
            source_type="notion",
            title="DynamoDB and Cassandra overview",
            url="https://www.notion.so/dynamodb-cassandra-overview",
            path="DynamoDB and Cassandra overview",
            score=0.92,
            preview="DynamoDB and Cassandra overview and tradeoffs.",
            text="DynamoDB and Cassandra overview and tradeoffs.",
        ),
        ContextSearchResult(
            chunk_id="chunk-compare-checklist",
            document_id="doc-compare-checklist",
            source_id="source_tistory",
            source_type="tistory",
            title="General comparison checklist",
            url="https://devlog.tistory.com/comparison-checklist",
            path="General comparison checklist",
            score=0.9,
            preview="Comparison checklist and evaluation prompts.",
            text="Comparison checklist and evaluation prompts.",
        ),
    ]
    service = CitationAnswerService(
        context_search=FakeContextSearch(results),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("DynamoDB vs Cassandra comparison"))

    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == ["chunk-ddb-cassandra"]


def test_answer_service_comparison_requires_all_named_sides():
    results = [
        ContextSearchResult(
            chunk_id="ddb",
            document_id="doc-ddb",
            source_id="source_notion",
            source_type="notion",
            title="DynamoDB notes",
            url="https://www.notion.so/dynamodb-notes",
            path="DynamoDB notes",
            score=0.92,
            preview="DynamoDB scaling notes.",
            text="DynamoDB scaling notes.",
        ),
        ContextSearchResult(
            chunk_id="cass",
            document_id="doc-cass",
            source_id="source_tistory",
            source_type="tistory",
            title="Cassandra notes",
            url="https://devlog.tistory.com/cassandra-notes",
            path="Cassandra notes",
            score=0.9,
            preview="Cassandra consistency notes.",
            text="Cassandra consistency notes.",
        ),
    ]
    service = CitationAnswerService(
        context_search=FakeContextSearch(results),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(
        service.answer_with_citations("DynamoDB vs Cassandra vs MongoDB comparison")
    )

    assert answer["evidence_status"] == "insufficient"
    assert answer["citations"] == []


def test_answer_service_preserves_raw_effective_term_groups_for_grounding():
    result = ContextSearchResult(
        chunk_id="chunk-debug-guide",
        document_id="doc-debug-guide",
        source_id="source_github",
        source_type="github",
        title="ContextZip context-wiki-debug guide",
        url="https://github.com/eunhwa99/context-zip/blob/main/docs/context_zip-debug-guide.md",
        path="docs/context-wiki-debug-guide.md",
        line_start=1,
        line_end=20,
        version_id="commit-1",
        score=0.92,
        preview="ContextZip context-wiki-debug guide and console workflow.",
        text="ContextZip context-wiki-debug guide and console workflow.",
    )

    class RedactedDisplayContextSearch(FakeContextSearch):
        async def search_context(
            self,
            query,
            filters=None,
            top_k=5,
            include_debug=False,
            include_internal_metadata=False,
        ):
            return {
                "query": query,
                "results": [result],
                "_grounding": {
                    "effective_term_groups": [["context-wiki-debug"], ["guide"]],
                },
                "debug": {
                    "effective_term_groups": [["[REDACTED]"], ["guide"]],
                },
            }

    service = CitationAnswerService(
        context_search=RedactedDisplayContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(
        service.answer_with_citations("context-wiki-debug guide", include_debug=True)
    )

    assert answer["evidence_status"] == "grounded"
    assert answer["citations"][0]["chunk_id"] == "chunk-debug-guide"
    assert "context-wiki-debug guide" in answer["debug_markdown"]
    assert "[REDACTED]" in answer["debug_markdown"]


def test_answer_service_visible_answer_keeps_benign_repo_slugs():
    result = ContextSearchResult(
        chunk_id="chunk-debug-guide",
        document_id="doc-debug-guide",
        source_id="source_github",
        source_type="github",
        title="ContextZip context-wiki-debug guide",
        url="https://github.com/eunhwa99/context-zip/blob/main/docs/context_zip-debug-guide.md",
        path="docs/context-wiki-debug-guide.md",
        score=0.92,
        preview="ContextZip context-wiki-debug guide and console workflow.",
        text="ContextZip context-wiki-debug guide and console workflow.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("context-wiki-debug guide"))

    assert "[REDACTED]" not in answer["answer"]
    assert "context-wiki-debug guide" in answer["answer"]


def test_answer_service_keeps_original_topical_constraint_from_grounding_state():
    result = ContextSearchResult(
        chunk_id="chunk-neetcode-arrays",
        document_id="doc-neetcode-arrays",
        source_id="source_github",
        source_type="github",
        title="Neetcode arrays docs",
        url="https://github.com/eunhwa99/context-zip/blob/main/docs/neetcode-arrays.md",
        path="docs/neetcode-arrays.md",
        line_start=1,
        line_end=20,
        version_id="commit-1",
        score=0.92,
        preview="Neetcode arrays walkthrough and study notes.",
        text="Neetcode arrays walkthrough and study notes.",
    )

    class GroundingContextSearch(FakeContextSearch):
        async def search_context(
            self,
            query,
            filters=None,
            top_k=5,
            include_debug=False,
            include_internal_metadata=False,
        ):
            return {
                "query": query,
                "results": [result],
                "_grounding": {
                    "original_term_groups": [["neetcode"], ["graph"], ["docs"]],
                    "effective_term_groups": [["neetcode"], ["graph"], ["docs"], ["guide"]],
                },
                "debug": {
                    "effective_term_groups": [["neetcode"], ["graph"], ["docs"], ["guide"]],
                },
            }

    service = CitationAnswerService(
        context_search=GroundingContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("neetcode graph docs"))

    assert answer["evidence_status"] == "insufficient"
    assert answer["citations"] == []


def test_answer_service_adds_debug_markdown_for_insufficient_evidence():
    service = CitationAnswerService(
        context_search=FakeContextSearch([]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("AWS에 적은 문서를 찾아줘", include_debug=True))

    assert answer["evidence_status"] == "insufficient"
    assert answer["answer_mode"] == "context_zip_debug"
    assert "## Query" in answer["debug_markdown"]
    assert "amazon web services" in answer["debug_markdown"]


def test_answer_service_redacts_http_paths_in_debug_output():
    result = ContextSearchResult(
        chunk_id="chunk-secret-url",
        document_id="doc-secret-url",
        source_id="source_github",
        source_type="github",
        title="Signed URL guide",
        url="https://example.com/private/token/opaque-value?signature=secret-value",
        path="https://example.com/private/token/opaque-value?signature=secret-value",
        line_start=1,
        line_end=5,
        version_id="github-1",
        score=0.92,
        preview="Signed URL guide.",
        text="Signed URL guide.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("signed url guide", include_debug=True))

    debug_markdown = answer["debug_markdown"]
    assert "opaque-value" not in debug_markdown
    assert "/private/token" not in debug_markdown
    assert "https://example.com [path redacted]" in debug_markdown


def test_answer_service_redacts_paths_and_credential_urls_inside_preview_text():
    result = ContextSearchResult(
        chunk_id="chunk-preview-secret",
        document_id="doc-preview-secret",
        source_id="source_github",
        source_type="github",
        title="Preview leak check",
        score=0.92,
        preview="see file:///tmp/secret.md and /Users/eunhwa/private/doc.md and https://user:pass@example.com/path",
        text="see file:///tmp/secret.md and /Users/eunhwa/private/doc.md and https://user:pass@example.com/path",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("preview leak check", include_debug=True))

    debug_markdown = answer["debug_markdown"]
    assert "file://" not in debug_markdown
    assert "/Users/eunhwa" not in debug_markdown
    assert "user:pass@" not in debug_markdown


def test_answer_service_debug_scores_show_grounding_score():
    result = ContextSearchResult(
        chunk_id="chunk-grounding-score",
        document_id="doc-grounding-score",
        source_id="source_github",
        source_type="github",
        title="NeetCode Graph",
        score=0.92,
        vector_score=0.2,
        preview="neetcode graph",
        text="neetcode graph",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.1,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("neetcode graph", include_debug=True))

    selected = answer["debug"]["selected_chunks"][0]
    assert selected["score"] == 0.2
    assert selected["search_score"] == 0.92
    assert "grounding score: 0.200" in answer["debug_markdown"]


def test_answer_service_defaults_to_non_debug_payload():
    result = ContextSearchResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        source_id="source_fake",
        source_type="notion",
        title="ContextZip",
        url="https://notion.so/doc-1",
        path="ContextZip",
        score=0.92,
        preview="ContextZip is an MCP knowledge backend.",
        text="ContextZip is an MCP knowledge backend.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("What is ContextZip?"))

    assert answer["evidence_status"] == "grounded"
    assert "debug" not in answer
    assert "debug_markdown" not in answer
    assert "answer_mode" not in answer


def test_answer_service_redacts_debug_locations_for_credentials_and_local_paths():
    result = ContextSearchResult(
        chunk_id="chunk-secret",
        document_id="doc-secret",
        source_id="source_github",
        source_type="github",
        title="secret token=ghp_example123",
        url="https://user:pass@example.com/private?token=abcd",
        path="/Users/eunhwa/private/project.md",
        line_start=1,
        line_end=5,
        version_id="v1",
        score=0.93,
        preview="contains secrets",
        text="contains secrets",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("secret", include_debug=True))

    assert answer["debug"]["selected_chunks"][0]["url"] == "redacted"
    assert answer["debug"]["selected_chunks"][0]["path"] == "redacted"
    assert "`redacted`" in answer["debug_markdown"]


def test_answer_service_redacts_query_fields_when_they_include_paths_or_credential_urls():
    result = ContextSearchResult(
        chunk_id="chunk-1",
        document_id="doc-1",
        source_id="source_github",
        source_type="github",
        title="Private doc",
        url="https://docs.example.com/private-doc",
        path="docs/private-doc.md",
        score=0.95,
        preview="private doc",
        text="private doc",
    )

    class QueryDebugContextSearch(FakeContextSearch):
        async def search_context(
            self,
            query,
            filters=None,
            top_k=5,
            include_debug=False,
            include_internal_metadata=False,
        ):
            return {
                "query": query,
                "results": [result],
                "debug": {
                    "effective_term_groups": [["private"], ["doc"]],
                    "retrieval_queries": [
                        "/Users/eunhwa/private/doc.md",
                        "https://user:pass@example.com/path?token=abc",
                    ],
                },
            }

    service = CitationAnswerService(
        context_search=QueryDebugContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(
        service.answer_with_citations(
            "https://user:pass@example.com/private /Users/eunhwa/private/doc.md",
            include_debug=True,
        )
    )

    assert answer["debug"]["question"] == "redacted redacted"
    assert answer["debug"]["retrieval_queries"] == ["redacted", "redacted"]
    assert "rewritten_queries" not in answer["debug"]
    assert "/Users/eunhwa" not in answer["debug_markdown"]
    assert "user:pass@" not in answer["debug_markdown"]
    assert "~/" not in answer["debug_markdown"]
    assert "C:/Users" not in answer["debug_markdown"]


def test_answer_service_grounding_uses_vector_score_not_rerank_bonus():
    result = ContextSearchResult(
        chunk_id="chunk-low-vector",
        document_id="doc-low-vector",
        source_id="source_github",
        source_type="github",
        title="GitHub sync guide",
        url="https://github.com/example/repo/blob/main/docs/github-sync.md",
        path="docs/github-sync.md",
        score=0.91,
        vector_score=0.21,
        preview="GitHub sync guide and checklist.",
        text="GitHub sync guide and checklist.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("github sync 문서"))

    assert answer["evidence_status"] == "insufficient"


def test_answer_service_requires_problem_hint_for_strong_anchor_problem_queries():
    result = ContextSearchResult(
        chunk_id="chunk-neetcode-readme",
        document_id="doc-neetcode-readme",
        source_id="source_github",
        source_type="github",
        title="NeetCode README",
        url="https://github.com/example/neetcode/blob/main/README.md",
        path="README.md",
        score=0.92,
        preview="NeetCode repository overview and setup notes.",
        text="NeetCode repository overview and setup notes.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("neetcode 문제"))

    assert answer["evidence_status"] == "insufficient"
    assert answer["citations"] == []


def test_answer_service_redacts_secret_like_debug_query_and_locations():
    result = ContextSearchResult(
        chunk_id="chunk-secret",
        document_id="doc-secret",
        source_id="source_github",
        source_type="github",
        title="Signed URL notes",
        url="https://example.com/docs?token=super-secret-value",
        path="docs/access_token=super-secret-value.md",
        score=0.92,
        preview="Contains api_key=super-secret-value in example text.",
        text="Contains api_key=super-secret-value in example text.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(
        service.answer_with_citations("show token=super-secret-value docs", include_debug=True)
    )

    assert "super-secret-value" not in answer["debug_markdown"]
    assert "super-secret-value" not in str(answer["debug"])


def test_answer_service_preserves_public_question_raw_for_mcp_contract():
    result = ContextSearchResult(
        chunk_id="chunk-secret",
        document_id="doc-secret",
        source_id="source_github",
        source_type="github",
        title="Signed URL notes",
        url="https://user:pass@example.com/docs?token=super-secret-value",
        path="/Users/eunhwa/private/doc.md",
        score=0.92,
        preview="Contains api_key=super-secret-value in example text.",
        text="Contains api_key=super-secret-value in example text.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(
        service.answer_with_citations(
            "https://user:pass@example.com/private?token=super-secret-value /Users/eunhwa/private/doc.md"
        )
    )

    assert answer["question"] == "redacted redacted"
    assert "super-secret-value" not in str(answer["citations"])
    assert answer["citations"][0]["url"] == "redacted"
    assert answer["citations"][0]["path"] == "redacted"


def test_answer_service_does_not_redact_benign_secret_management_phrases():
    result = ContextSearchResult(
        chunk_id="chunk-secret-guide",
        document_id="doc-secret-guide",
        source_id="source_github",
        source_type="github",
        title="Secret management guide",
        path="docs/secret-management.md",
        score=0.92,
        preview="Secret management guide and token rotation docs.",
        text="Secret management guide and token rotation docs.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(
        service.answer_with_citations("secret management guide", include_debug=True)
    )

    assert "secret [REDACTED]" not in answer["answer"]
    assert "token [REDACTED]" not in answer["answer"]
    assert "secret [REDACTED]" not in answer["debug_markdown"]
    assert "token [REDACTED]" not in answer["debug_markdown"]


def test_answer_service_ground_neetcode_korean_query_from_github_repository_metadata(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            sync_status=SyncStatus.IDLE,
        )
    )
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:README.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="Algorithm docs",
            content="Dynamic programming solution notes and study documents.",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            platform="GitHub",
            path="README.md",
        ),
        [
            ChunkModel(
                chunk_id="neetcode-readme-chunk",
                document_id=document_id,
                source_id="source_github",
                title="Algorithm docs",
                text="Dynamic programming solution notes and study documents.",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
                path="README.md",
                chunk_index=0,
                content_hash="neetcode-readme-chunk",
            )
        ],
    )
    service = CitationAnswerService(
        context_search=ContextSearchService(store),
        min_score=0.35,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("니트코드 문서 찾아와"))

    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == ["neetcode-readme-chunk"]


def test_answer_service_treats_readme_as_document_for_neetcode_korean_query(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            sync_status=SyncStatus.IDLE,
        )
    )
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:README.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="README",
            content="Dynamic programming notes.",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            platform="GitHub",
            path="README.md",
        ),
        [
            ChunkModel(
                chunk_id="neetcode-readme-no-doc-word",
                document_id=document_id,
                source_id="source_github",
                title="README",
                text="Dynamic programming notes.",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
                path="README.md",
                chunk_index=0,
                content_hash="neetcode-readme-no-doc-word",
            )
        ],
    )
    service = CitationAnswerService(
        context_search=ContextSearchService(store),
        min_score=0.35,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("니트코드 문서 찾아와"))

    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == ["neetcode-readme-no-doc-word"]


def test_answer_service_ignores_common_request_words_for_specific_repo_query(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            sync_status=SyncStatus.IDLE,
        )
    )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-docs-chunk",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/usage.md",
                chunk_index=0,
                content_hash="imagegallery-docs-chunk",
            )
        ],
    )
    service = CitationAnswerService(
        context_search=ContextSearchService(store),
        min_score=0.35,
        min_results=1,
    )

    for query in (
        "get ImageGallery docs",
        "please ImageGallery docs",
        "show me ImageGallery docs",
        "find ImageGallery docs",
        "search ImageGallery docs",
        "search for ImageGallery docs",
    ):
        answer = asyncio.run(service.answer_with_citations(query))

        assert answer["evidence_status"] == "grounded"
        assert answer["used_chunks"] == ["imagegallery-docs-chunk"]


def test_answer_service_treats_non_github_evidence_as_document_like_for_docs_query():
    result = ContextSearchResult(
        chunk_id="notion-configuration",
        document_id="notion-configuration-guide",
        source_id="source_notion",
        source_type="notion",
        title="Configuration guide",
        score=0.92,
        preview="Configuration reference.",
        text="Configuration reference.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.35,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("configuration docs"))

    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == ["notion-configuration"]


def test_answer_service_rejects_github_docs_helper_code_for_document_query():
    result = ContextSearchResult(
        chunk_id="docs-helper-code",
        document_id="github:eunhwa99/neetcode-submissions-8ogaz8xl:src/docs_helper.py",
        source_id="source_github",
        source_type="github",
        title="src/docs_helper.py",
        path="src/docs_helper.py",
        score=0.92,
        preview="NeetCode helper implementation.",
        text="NeetCode helper implementation.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.35,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("니트코드 문서 찾아와"))

    assert answer["evidence_status"] == "insufficient"
    assert answer["used_chunks"] == []


def test_answer_service_ignores_korean_search_filler_for_specific_repo_query(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            sync_status=SyncStatus.IDLE,
        )
    )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-docs-korean-filler",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/usage.md",
                chunk_index=0,
                content_hash="imagegallery-docs-korean-filler",
            )
        ],
    )
    service = CitationAnswerService(
        context_search=ContextSearchService(store),
        min_score=0.35,
        min_results=1,
    )

    for query in (
        "ImageGallery 라고 검색해도",
        "ImageGallery라고 검색해도",
        "ImageGallery라는 리포지토리 검색",
    ):
        answer = asyncio.run(service.answer_with_citations(query))

        assert answer["evidence_status"] == "grounded"
        assert answer["used_chunks"] == ["imagegallery-docs-korean-filler"]


def test_answer_service_rejects_partial_github_metadata_match_for_specific_repo_query(
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            sync_status=SyncStatus.IDLE,
        )
    )
    document_id = "github:eunhwa99/other:docs/README.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="Other docs",
            content="Repository documentation for a different project.",
            url="https://github.com/eunhwa99/other/blob/main/docs/README.md",
            canonical_url="https://github.com/eunhwa99/other/blob/main/docs/README.md",
            platform="GitHub",
            path="docs/README.md",
        ),
        [
            ChunkModel(
                chunk_id="other-docs-chunk",
                document_id=document_id,
                source_id="source_github",
                title="Other docs",
                text="Repository documentation for a different project.",
                url="https://github.com/eunhwa99/other/blob/main/docs/README.md",
                path="docs/README.md",
                chunk_index=0,
                content_hash="other-docs-chunk",
            )
        ],
    )
    service = CitationAnswerService(
        context_search=ContextSearchService(store),
        min_score=0.35,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("ImageGallery docs"))

    assert answer["evidence_status"] == "insufficient"
    assert answer["used_chunks"] == []


def test_answer_service_rejects_neetcode_docs_query_for_code_only_metadata_match(
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            sync_status=SyncStatus.IDLE,
        )
    )
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:Graph.java"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="Graph.java",
            content="class GraphSolution { void dfs() {} }",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/Graph.java",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/Graph.java",
            platform="GitHub",
            path="Graph.java",
        ),
        [
            ChunkModel(
                chunk_id="code-only",
                document_id=document_id,
                source_id="source_github",
                title="Graph.java",
                text="class GraphSolution { void dfs() {} }",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/Graph.java",
                path="Graph.java",
                chunk_index=0,
                content_hash="code-only",
            )
        ],
    )
    service = CitationAnswerService(
        context_search=ContextSearchService(store),
        min_score=0.35,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("니트코드 문서 찾아와"))

    assert answer["evidence_status"] == "insufficient"
    assert answer["used_chunks"] == []


def test_answer_service_rejects_neetcode_docs_query_when_code_text_mentions_documentation(
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            sync_status=SyncStatus.IDLE,
        )
    )
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:Graph.java"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="Graph.java",
            content="// documentation for graph solution\nclass GraphSolution {}",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/Graph.java",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/Graph.java",
            platform="GitHub",
            path="Graph.java",
        ),
        [
            ChunkModel(
                chunk_id="code-only-documentation-text",
                document_id=document_id,
                source_id="source_github",
                title="Graph.java",
                text="// documentation for graph solution\nclass GraphSolution {}",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/Graph.java",
                path="Graph.java",
                chunk_index=0,
                content_hash="code-only-documentation-text",
            )
        ],
    )
    service = CitationAnswerService(
        context_search=ContextSearchService(store),
        min_score=0.35,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("니트코드 문서 찾아와"))

    assert answer["evidence_status"] == "insufficient"
    assert answer["used_chunks"] == []


def test_answer_service_rejects_neetcode_graph_docs_query_for_generic_readme(
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            sync_status=SyncStatus.IDLE,
        )
    )
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:README.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="README",
            content="Dynamic programming notes.",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            platform="GitHub",
            path="README.md",
        ),
        [
            ChunkModel(
                chunk_id="generic-neetcode-readme",
                document_id=document_id,
                source_id="source_github",
                title="README",
                text="Dynamic programming notes.",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
                path="README.md",
                chunk_index=0,
                content_hash="generic-neetcode-readme",
            )
        ],
    )
    service = CitationAnswerService(
        context_search=ContextSearchService(store),
        min_score=0.35,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("니트코드 그래프 문서 찾아와"))

    assert answer["evidence_status"] == "insufficient"
    assert answer["used_chunks"] == []


def test_answer_service_rejects_body_only_neetcode_anchor_from_unrelated_readme():
    result = ContextSearchResult(
        chunk_id="other-neetcode-body-only",
        document_id="github:eunhwa99/other:README.md",
        source_id="source_github",
        source_type="github",
        title="Other README",
        url="https://github.com/eunhwa99/other/blob/main/README.md",
        path="README.md",
        line_start=1,
        line_end=20,
        version_id="commit-1",
        score=0.92,
        preview="NeetCode graph notes appear in this unrelated README body.",
        text="NeetCode graph notes appear in this unrelated README body.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.35,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("니트코드 그래프 문서 찾아와"))

    assert answer["evidence_status"] == "insufficient"
    assert answer["used_chunks"] == []


def test_answer_service_rejects_no_space_neetcode_graph_docs_query_for_generic_readme(
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            sync_status=SyncStatus.IDLE,
        )
    )
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:README.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="README",
            content="Dynamic programming notes.",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            platform="GitHub",
            path="README.md",
        ),
        [
            ChunkModel(
                chunk_id="generic-neetcode-readme-no-space",
                document_id=document_id,
                source_id="source_github",
                title="README",
                text="Dynamic programming notes.",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
                path="README.md",
                chunk_index=0,
                content_hash="generic-neetcode-readme-no-space",
            )
        ],
    )
    service = CitationAnswerService(
        context_search=ContextSearchService(store),
        min_score=0.35,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("니트코드그래프문서찾아와"))

    assert answer["evidence_status"] == "insufficient"
    assert answer["used_chunks"] == []


def test_answer_service_accepts_neetcode_graph_docs_query_for_matching_readme(
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            sync_status=SyncStatus.IDLE,
        )
    )
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:README.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="README",
            content="Graph traversal notes.",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            platform="GitHub",
            path="README.md",
        ),
        [
            ChunkModel(
                chunk_id="graph-neetcode-readme",
                document_id=document_id,
                source_id="source_github",
                title="README",
                text="Graph traversal notes.",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
                path="README.md",
                chunk_index=0,
                content_hash="graph-neetcode-readme",
            )
        ],
    )
    service = CitationAnswerService(
        context_search=ContextSearchService(store),
        min_score=0.35,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("니트코드 그래프 문서 찾아와"))

    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == ["graph-neetcode-readme"]


def test_answer_service_ignores_polite_request_words_for_neetcode_docs_query(
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            sync_status=SyncStatus.IDLE,
        )
    )
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:README.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="README",
            content="Repository usage notes.",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            platform="GitHub",
            path="README.md",
        ),
        [
            ChunkModel(
                chunk_id="polite-neetcode-readme",
                document_id=document_id,
                source_id="source_github",
                title="README",
                text="Repository usage notes.",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
                path="README.md",
                chunk_index=0,
                content_hash="polite-neetcode-readme",
            )
        ],
    )
    service = CitationAnswerService(
        context_search=ContextSearchService(store),
        min_score=0.35,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("please get neetcode docs"))

    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == ["polite-neetcode-readme"]


def test_answer_service_accepts_generic_problem_term_for_strong_anchor_query(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            sync_status=SyncStatus.IDLE,
        )
    )
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:README.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="README",
            content="Repository usage notes and solved-problem study links.",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            platform="GitHub",
            path="README.md",
        ),
        [
            ChunkModel(
                chunk_id="neetcode-problem-readme",
                document_id=document_id,
                source_id="source_github",
                title="README",
                text="Repository usage notes and solved-problem study links.",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
                path="README.md",
                chunk_index=0,
                content_hash="neetcode-problem-readme",
            )
        ],
    )
    service = CitationAnswerService(
        context_search=ContextSearchService(store),
        min_score=0.35,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("neetcode 문제"))

    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == ["neetcode-problem-readme"]
