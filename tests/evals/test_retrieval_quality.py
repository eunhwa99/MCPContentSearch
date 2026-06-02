from evals.contextwiki_eval import run_contextwiki_eval
from evals.retrieval_quality import (
    RetrievalQualityCase,
    evaluate_search_payload,
    evaluate_search_suite,
    load_cases,
)


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
            {"chunk_id": "runtime-notes-chunk", "source_id": "source_web"},
        ]
    }

    result = evaluate_search_payload(payload, case)

    assert result.passed
    assert result.score == 1.0


def test_retrieval_payload_fails_when_expected_chunk_is_not_ranked_first():
    case = RetrievalQualityCase(
        case_id="architecture-guide-phrase",
        query="project architecture guide",
        expected_top_chunk_id="architecture-guide-chunk",
    )
    payload = {
        "results": [
            {"chunk_id": "runtime-notes-chunk", "source_id": "source_web"},
            {"chunk_id": "architecture-guide-chunk", "source_id": "source_web"},
        ]
    }

    result = evaluate_search_payload(payload, case)

    assert not result.passed
    assert "expected_top_chunk_id" in result.failures


def test_retrieval_fixture_cases_load_and_suite_summarizes_results():
    cases = load_cases("evals/retrieval_quality_cases.json")
    payloads = {
        "github-sync-docs": {"results": [{"chunk_id": "github-sync-doc-chunk", "source_id": "source_github"}]},
        "neetcode-problems": {"results": [{"chunk_id": "neetcode-problems-chunk", "source_id": "source_github"}]},
        "notion-source-preferred": {"results": [{"chunk_id": "notion-sync-chunk", "source_id": "source_notion"}]},
        "architecture-guide-phrase": {"results": [{"chunk_id": "architecture-guide-chunk", "source_id": "source_web"}]},
        "aws-doc-alias": {"results": [{"chunk_id": "aws-guide-chunk", "source_id": "source_web"}]},
    }

    summary = evaluate_search_suite(payloads, cases)

    assert summary["passed"]
    assert summary["total"] == 5
    assert summary["passed_count"] == 5


def test_contextwiki_eval_runner_passes_fixture_suites():
    summary = run_contextwiki_eval()

    assert summary["passed"]
    assert summary["retrieval_suite"]["passed"]
    assert summary["answer_suite"]["passed"]
