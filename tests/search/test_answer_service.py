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

    async def search_context(self, query, filters=None, top_k=5):
        return {"query": query, "results": self.results[:top_k]}


def test_answer_service_returns_insufficient_evidence_without_grounding():
    service = CitationAnswerService(
        context_search=FakeContextSearch([]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("What is ContextWiki?", include_debug=True))

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

    answer = asyncio.run(service.answer_with_citations("What is ContextWiki?", include_debug=True))

    assert answer["evidence_status"] == "grounded"
    assert answer["answer_mode"] == "contextwiki_debug"
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
    assert "## Summary" in answer["answer"]
    assert "## Best Matches" in answer["answer"]
    assert "[C1]" in answer["answer"]
    assert "## Query" in answer["debug_markdown"]
    assert answer["debug"]["selected_chunks"][0]["matched_terms"]


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


def test_answer_service_treats_broad_usage_terms_as_optional_hints():
    result = ContextSearchResult(
        chunk_id="chunk-aws-overview",
        document_id="doc-aws-overview",
        source_id="source_github",
        source_type="github",
        title="AWS Overview",
        url="https://github.com/eunhwa99/MCPContentSearch/blob/main/docs/aws-overview.md",
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
        url="https://github.com/eunhwa99/MCPContentSearch/blob/main/docs/ec2-guide.md",
        path="docs/ec2-guide.md",
        line_start=1,
        line_end=20,
        version_id="commit-1",
        score=0.92,
        preview="EC2 setup and instance launch notes.",
        text="EC2 setup and instance launch notes.",
    )

    class RewriteDebugContextSearch(FakeContextSearch):
        async def search_context(self, query, filters=None, top_k=5):
            return {
                "query": query,
                "results": [result],
                "debug": {
                    "effective_term_groups": [["aws"], ["ec2"], ["setup"]],
                    "retrieval_queries": ["aws virtual machine startup", "aws ec2 setup"],
                    "rewritten_queries": ["aws ec2 setup"],
                },
            }

    service = CitationAnswerService(
        context_search=RewriteDebugContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("aws virtual machine startup", include_debug=True))

    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == ["chunk-ec2-guide"]
    assert answer["debug"]["rewritten_queries"] == ["aws ec2 setup"]


def test_answer_service_preserves_raw_effective_term_groups_for_grounding():
    result = ContextSearchResult(
        chunk_id="chunk-debug-guide",
        document_id="doc-debug-guide",
        source_id="source_github",
        source_type="github",
        title="ContextWiki context-wiki-debug guide",
        url="https://github.com/eunhwa99/MCPContentSearch/blob/main/docs/contextwiki-debug-guide.md",
        path="docs/context-wiki-debug-guide.md",
        line_start=1,
        line_end=20,
        version_id="commit-1",
        score=0.92,
        preview="ContextWiki context-wiki-debug guide and console workflow.",
        text="ContextWiki context-wiki-debug guide and console workflow.",
    )

    class RedactedDisplayContextSearch(FakeContextSearch):
        async def search_context(self, query, filters=None, top_k=5):
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
        title="ContextWiki context-wiki-debug guide",
        url="https://github.com/eunhwa99/MCPContentSearch/blob/main/docs/contextwiki-debug-guide.md",
        path="docs/context-wiki-debug-guide.md",
        score=0.92,
        preview="ContextWiki context-wiki-debug guide and console workflow.",
        text="ContextWiki context-wiki-debug guide and console workflow.",
    )
    service = CitationAnswerService(
        context_search=FakeContextSearch([result]),
        min_score=0.5,
        min_results=1,
    )

    answer = asyncio.run(service.answer_with_citations("context-wiki-debug guide"))

    assert "[REDACTED]" not in answer["answer"]
    assert "context-wiki-debug guide" in answer["answer"]


def test_answer_service_keeps_original_topical_constraint_when_rewrite_relaxes_query():
    result = ContextSearchResult(
        chunk_id="chunk-neetcode-arrays",
        document_id="doc-neetcode-arrays",
        source_id="source_github",
        source_type="github",
        title="Neetcode arrays docs",
        url="https://github.com/eunhwa99/MCPContentSearch/blob/main/docs/neetcode-arrays.md",
        path="docs/neetcode-arrays.md",
        line_start=1,
        line_end=20,
        version_id="commit-1",
        score=0.92,
        preview="Neetcode arrays walkthrough and study notes.",
        text="Neetcode arrays walkthrough and study notes.",
    )

    class RewriteDebugContextSearch(FakeContextSearch):
        async def search_context(self, query, filters=None, top_k=5):
            return {
                "query": query,
                "results": [result],
                "_grounding": {
                    "original_term_groups": [["neetcode"], ["graph"], ["docs"]],
                    "effective_term_groups": [["neetcode"], ["graph"], ["docs"], ["guide"]],
                },
                "debug": {
                    "effective_term_groups": [["neetcode"], ["graph"], ["docs"], ["guide"]],
                    "rewritten_queries": ["neetcode docs guide"],
                },
            }

    service = CitationAnswerService(
        context_search=RewriteDebugContextSearch([result]),
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
    assert answer["answer_mode"] == "contextwiki_debug"
    assert "## Query" in answer["debug_markdown"]
    assert "amazon web services" in answer["debug_markdown"]


def test_answer_service_redacts_http_paths_in_debug_output():
    result = ContextSearchResult(
        chunk_id="chunk-secret-url",
        document_id="doc-secret-url",
        source_id="source_web",
        source_type="web",
        title="Signed URL guide",
        url="https://example.com/private/token/opaque-value?signature=secret-value",
        path="https://example.com/private/token/opaque-value?signature=secret-value",
        line_start=1,
        line_end=5,
        version_id="web-1",
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
        source_id="source_web",
        source_type="web",
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
    assert "debug" not in answer
    assert "debug_markdown" not in answer
    assert "answer_mode" not in answer


def test_answer_service_redacts_debug_locations_for_credentials_and_local_paths():
    result = ContextSearchResult(
        chunk_id="chunk-secret",
        document_id="doc-secret",
        source_id="source_web",
        source_type="web",
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
        source_id="source_web",
        source_type="web",
        title="Private doc",
        url="https://docs.example.com/private-doc",
        path="docs/private-doc.md",
        score=0.95,
        preview="private doc",
        text="private doc",
    )

    class QueryDebugContextSearch(FakeContextSearch):
        async def search_context(self, query, filters=None, top_k=5):
            return {
                "query": query,
                "results": [result],
                "debug": {
                    "effective_term_groups": [["private"], ["doc"]],
                    "retrieval_queries": [
                        "/Users/eunhwa/private/doc.md",
                        "https://user:pass@example.com/path?token=abc",
                    ],
                    "rewritten_queries": ["~/private/doc.md C:/Users/test/private/doc.md"],
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
    assert answer["debug"]["rewritten_queries"] == ["redacted redacted"]
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

    assert answer["question"] == "https://user:pass@example.com/private?token=super-secret-value /Users/eunhwa/private/doc.md"
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
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
