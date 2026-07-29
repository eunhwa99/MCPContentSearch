import asyncio
import json

from core.models import ChunkModel, DocumentModel, SearchFilters
from evals.contextwiki_eval import run_contextwiki_eval
from evals.retrieval_quality import (
    RetrievalQualityCase,
    evaluate_search_payload,
    evaluate_search_suite,
    load_cases,
)
from llama_index.core.vector_stores import FilterOperator, MetadataFilter, MetadataFilters
from search.context_service import ContextSearchService
from storage.metadata_store import MetadataStore


def test_retrieval_payload_passes_when_expected_top_chunk_and_source_match():
    case = RetrievalQualityCase(
        case_id="github-sync-docs",
        query="github sync 문서",
        expected_top_chunk_id="github-sync-doc-chunk",
        expected_source_id="source_github",
        required_chunk_ids=("github-sync-doc-chunk",),
    )
    payload = {
        "results": [
            {"chunk_id": "github-sync-doc-chunk", "source_id": "source_github"},
            {"chunk_id": "runtime-notes-chunk", "source_id": "source_tistory"},
        ]
    }

    result = evaluate_search_payload(payload, case)

    assert result.passed
    assert result.score == 1.0


def test_retained_retrieval_eval_selects_only_documents_inside_date_filter(tmp_path):
    store = MetadataStore(tmp_path / "date-filter-eval.sqlite3")
    retriever_documents = []
    for document_id, published_at in [
        ("outside", "2026-06-30T23:59:59Z"),
        ("inside", "2026-07-01T00:00:00Z"),
    ]:
        document = DocumentModel(
            id=document_id,
            document_id=document_id,
            source_id="source_notion",
            title=f"ContextWiki {document_id}",
            content="ContextWiki retained date-filter evidence.",
            url=f"https://example.com/{document_id}",
            platform="Notion",
            published_at=published_at,
            modified_at=published_at,
            indexed_at=published_at,
            date_provenance="notion",
        )
        chunk = ChunkModel(
            chunk_id=f"{document_id}-chunk",
            document_id=document_id,
            source_id="source_notion",
            title=document.title,
            text=document.content,
            url=document.url,
            chunk_index=0,
            content_hash=document_id,
        )
        store.upsert_document_and_replace_chunks(document, [chunk])
        retriever_documents.append(chunk.to_document_model(platform="Notion"))

    payload = asyncio.run(
        ContextSearchService(
            store,
            retriever=retriever_documents,
        ).search_context(
            "ContextWiki",
            filters=SearchFilters(
                source_ids=["source_notion"],
                published_from="2026-07-01T00:00:00Z",
                published_to="2026-07-01T00:00:00Z",
            ),
            top_k=2,
        )
    )

    assert [result.document_id for result in payload["results"]] == ["inside"]


def test_retrieval_payload_fails_when_expected_chunk_is_not_ranked_first():
    case = RetrievalQualityCase(
        case_id="architecture-guide-phrase",
        query="project architecture guide",
        expected_top_chunk_id="architecture-guide-chunk",
    )
    payload = {
        "results": [
            {"chunk_id": "runtime-notes-chunk", "source_id": "source_tistory"},
            {"chunk_id": "architecture-guide-chunk", "source_id": "source_notion"},
        ]
    }

    result = evaluate_search_payload(payload, case)

    assert not result.passed
    assert "expected_top_chunk_id" in result.failures


def test_retrieval_payload_fails_when_expected_source_is_not_ranked_first():
    case = RetrievalQualityCase(
        case_id="notion-source-preferred",
        query="notion sync notes",
        expected_source_id="source_notion",
    )
    payload = {
        "results": [
            {"chunk_id": "tistory-sync-chunk", "source_id": "source_tistory"},
            {"chunk_id": "notion-sync-chunk", "source_id": "source_notion"},
        ]
    }

    result = evaluate_search_payload(payload, case)

    assert not result.passed
    assert "expected_source_id" in result.failures


def test_retrieval_payload_top_rank_checks_fail_when_first_result_is_malformed():
    case = RetrievalQualityCase(
        case_id="malformed-top-result",
        query="github sync 문서",
        expected_top_chunk_id="github-sync-doc-chunk",
        expected_source_id="source_github",
    )
    payload = {
        "results": [
            {"title": "missing identifiers"},
            {"chunk_id": "github-sync-doc-chunk", "source_id": "source_github"},
        ]
    }

    result = evaluate_search_payload(payload, case)

    assert not result.passed
    assert "expected_top_chunk_id" in result.failures
    assert "expected_source_id" in result.failures


def test_retrieval_fixture_cases_load_and_suite_summarizes_results():
    cases = load_cases("evals/retrieval_quality_cases.json")
    payloads = {
        "github-sync-docs": {"results": [{"chunk_id": "github-sync-doc-chunk", "source_id": "source_github"}]},
        "neetcode-problems": {"results": [{"chunk_id": "neetcode-problems-chunk", "source_id": "source_github"}]},
        "notion-source-preferred": {"results": [{"chunk_id": "notion-sync-chunk", "source_id": "source_notion"}]},
        "architecture-guide-phrase": {"results": [{"chunk_id": "architecture-guide-chunk", "source_id": "source_notion"}]},
        "aws-doc-alias": {"results": [{"chunk_id": "aws-guide-chunk", "source_id": "source_notion"}]},
        "aws-collection-intent": {"results": [{"chunk_id": "aws-guide-chunk", "source_id": "source_notion"}]},
        "graph-search-code": {"results": [{"chunk_id": "graph-search-code-chunk", "source_id": "source_github"}]},
        "adr-markdown-scope": {"results": [{"chunk_id": "adr-markdown-chunk", "source_id": "source_github"}]},
        "obsidian-daily-planning": {"results": [{"chunk_id": "obsidian-daily-planning-chunk", "source_id": "source_obsidian"}]},
        "mixed-language-daily-note": {"results": [{"chunk_id": "obsidian-daily-planning-chunk", "source_id": "source_obsidian"}]},
        "lowercase-long-token-no-github-bias": {"results": []},
        "mixed-language-comparison-no-github-bias": {
            "results": [
                {"chunk_id": "dynamodb-notes-chunk", "source_id": "source_notion"},
                {"chunk_id": "cassandra-notes-chunk", "source_id": "source_tistory"},
            ]
        },
        "compound-expansion-collision-awslambda": {"results": []},
        "published-date-window": {
            "results": [
                {
                    "chunk_id": "release-date-inside-chunk",
                    "source_id": "source_notion",
                }
            ]
        },
    }

    summary = evaluate_search_suite(payloads, cases)

    assert summary["passed"]
    assert summary["total"] == 14
    assert summary["passed_count"] == 14


def test_retrieval_fixture_cases_cover_mixed_query_groups():
    cases = load_cases("evals/retrieval_quality_cases.json")

    groups = {case.group for case in cases}

    assert "repo-specific" in groups
    assert "generic-behavior" in groups
    assert "code-format" in groups
    assert "markdown-format" in groups
    assert "obsidian-format" in groups
    assert "mixed-language" in groups
    assert "date-filter" in groups


def test_retrieval_suite_fails_when_case_list_is_empty():
    summary = evaluate_search_suite({}, [])

    assert not summary["passed"]
    assert summary["total"] == 0


def test_contextwiki_eval_runner_passes_fixture_suites():
    summary = run_contextwiki_eval()

    assert summary["passed"]
    assert summary["retrieval_suite"]["passed"]
    assert summary["document_sort_suite"]["passed"]
    assert summary["answer_suite"]["passed"]
    assert "group_breakdown" in summary["retrieval_suite"]
    assert "group_breakdown" in summary["answer_suite"]
    assert "runtime_metrics" not in summary
    assert summary["retrieval_suite"]["total"] == 14
    date_result = next(
        result
        for result in summary["retrieval_suite"]["results"]
        if result["case_id"] == "published-date-window"
    )
    assert date_result["passed"]
    assert date_result["details"]["chunk_ids"] == ["release-date-inside-chunk"]


def test_contextwiki_eval_runner_reports_group_metrics_and_artifacts(tmp_path):
    output_dir = tmp_path / "eval-artifacts"
    second_output_dir = tmp_path / "eval-artifacts-second"

    summary = run_contextwiki_eval(output_dir=output_dir, include_latency=True)

    assert summary["passed"]
    assert summary["artifact_dir"] == str(output_dir)

    retrieval_groups = summary["retrieval_suite"]["group_breakdown"]
    answer_groups = summary["answer_suite"]["group_breakdown"]

    assert retrieval_groups["code-format"]["total"] >= 1
    assert retrieval_groups["markdown-format"]["total"] >= 1
    assert retrieval_groups["obsidian-format"]["total"] >= 1
    assert retrieval_groups["mixed-language"]["total"] >= 1
    assert answer_groups["obsidian-format"]["total"] >= 1
    assert answer_groups["mixed-language"]["total"] >= 1

    for suite_name in ("retrieval_suite", "document_sort_suite", "answer_suite"):
        latency = summary["runtime_metrics"][suite_name]["latency_ms"]
        assert latency["total"] > 0
        assert latency["max"] >= latency["min"] >= 0
        assert latency["average"] >= 0

    assert (output_dir / "summary.json").is_file()
    assert (output_dir / "retrieval_suite.json").is_file()
    assert (output_dir / "document_sort_suite.json").is_file()
    assert (output_dir / "answer_suite.json").is_file()
    assert (output_dir / "runtime_metrics.json").is_file()

    written_summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    written_retrieval_suite = json.loads(
        (output_dir / "retrieval_suite.json").read_text(encoding="utf-8")
    )
    written_answer_suite = json.loads(
        (output_dir / "answer_suite.json").read_text(encoding="utf-8")
    )

    assert "group_breakdown" in written_summary["retrieval_suite"]
    assert "group_breakdown" in written_summary["answer_suite"]
    assert written_retrieval_suite["group_breakdown"]["code-format"]["total"] >= 1
    assert written_retrieval_suite["group_breakdown"]["markdown-format"]["total"] >= 1
    assert written_answer_suite["group_breakdown"]["code-format"]["total"] >= 1
    assert written_answer_suite["group_breakdown"]["markdown-format"]["total"] >= 1

    second_summary = run_contextwiki_eval(
        output_dir=second_output_dir,
        include_latency=True,
    )

    assert second_summary["passed"]
    assert (output_dir / "summary.json").read_text(encoding="utf-8") == (
        second_output_dir / "summary.json"
    ).read_text(encoding="utf-8")
    assert (output_dir / "retrieval_suite.json").read_text(encoding="utf-8") == (
        second_output_dir / "retrieval_suite.json"
    ).read_text(encoding="utf-8")
    assert (output_dir / "answer_suite.json").read_text(encoding="utf-8") == (
        second_output_dir / "answer_suite.json"
    ).read_text(encoding="utf-8")

    deterministic_rerun = run_contextwiki_eval(output_dir=output_dir)

    assert deterministic_rerun["passed"]
    assert "runtime_metrics" not in deterministic_rerun
    assert not (output_dir / "runtime_metrics.json").exists()


def test_contextwiki_eval_runner_uses_deterministic_retrieval():
    summary = run_contextwiki_eval()

    assert summary["passed"]


def test_contextwiki_eval_runner_uses_vector_index_path(monkeypatch):
    calls = []

    class SpyRetriever:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            from evals.contextwiki_eval import FixtureVectorIndexRetriever

            self.delegate = FixtureVectorIndexRetriever(**kwargs)

        def retrieve(self, query):
            return self.delegate.retrieve(query)

    monkeypatch.setattr("evals.contextwiki_eval.FIXTURE_VECTOR_RETRIEVER_CLASS", SpyRetriever)

    summary = run_contextwiki_eval()

    assert summary["passed"]
    assert calls


def test_contextwiki_eval_filter_parser_handles_in_operator_values():
    from evals.contextwiki_eval import _source_ids_from_filters

    filters = MetadataFilters(
        filters=[
            MetadataFilter(
                key="source_id",
                operator=FilterOperator.IN,
                value=["source_github", "source_notion"],
            )
        ]
    )

    assert _source_ids_from_filters(filters) == {"source_github", "source_notion"}
