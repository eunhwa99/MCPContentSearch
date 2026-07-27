import json
import math

import pytest

from evals.contextwiki_eval import run_contextwiki_eval
from evals.retrieval_quality import (
    RetrievalQualityCase,
    evaluate_search_payload,
    evaluate_search_suite,
    load_cases,
)
from llama_index.core.vector_stores import FilterOperator, MetadataFilter, MetadataFilters


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
    }

    summary = evaluate_search_suite(payloads, cases)

    assert summary["passed"]
    assert summary["total"] == 13
    assert summary["passed_count"] == 13


def test_retrieval_fixture_cases_cover_mixed_query_groups():
    cases = load_cases("evals/retrieval_quality_cases.json")

    groups = {case.group for case in cases}

    assert "repo-specific" in groups
    assert "generic-behavior" in groups
    assert "code-format" in groups
    assert "markdown-format" in groups
    assert "obsidian-format" in groups
    assert "mixed-language" in groups


def test_retrieval_suite_fails_when_case_list_is_empty():
    summary = evaluate_search_suite({}, [])

    assert not summary["passed"]
    assert summary["total"] == 0


def test_retrieval_quality_metrics_use_standard_ranked_relevance_semantics():
    cases = [
        RetrievalQualityCase(
            case_id="relevant-at-ranks-two-and-three",
            query="ranked relevance",
            top_k=3,
            required_chunk_ids=("relevant-a", "relevant-b"),
        ),
        RetrievalQualityCase(
            case_id="relevant-outside-cutoff",
            query="cutoff behavior",
            top_k=2,
            required_chunk_ids=("relevant-c",),
        ),
        RetrievalQualityCase(
            case_id="negative-unscorable",
            query="must not match",
            top_k=3,
            min_result_count=0,
            forbidden_chunk_ids=("forbidden",),
        ),
    ]
    payloads = {
        "relevant-at-ranks-two-and-three": {
            "results": [
                {"chunk_id": "distractor"},
                {"chunk_id": "relevant-a"},
                {"chunk_id": "relevant-b"},
            ]
        },
        "relevant-outside-cutoff": {
            "results": [
                {"chunk_id": "distractor-1"},
                {"chunk_id": "distractor-2"},
                {"chunk_id": "relevant-c"},
            ]
        },
        "negative-unscorable": {"results": []},
    }

    summary = evaluate_search_suite(payloads, cases)

    metrics = summary["quality_metrics"]
    expected_first_case_ndcg = (
        (1 / math.log2(3)) + (1 / math.log2(4))
    ) / (1.0 + (1 / math.log2(3)))

    assert metrics["cutoff"] == "case_top_k"
    assert metrics["scorable_case_count"] == 2
    assert metrics["unscorable_case_count"] == 1
    assert metrics["hit_rate_at_k"] == {
        "value": 0.5,
        "numerator": 1.0,
        "denominator": 2,
    }
    assert metrics["mrr_at_k"] == {
        "value": 0.25,
        "numerator": 0.5,
        "denominator": 2,
    }
    assert metrics["recall_at_k"] == {
        "value": 0.5,
        "numerator": 1.0,
        "denominator": 2,
    }
    assert metrics["ndcg_at_k"]["denominator"] == 2
    assert metrics["ndcg_at_k"]["numerator"] == pytest.approx(
        expected_first_case_ndcg
    )
    assert metrics["ndcg_at_k"]["value"] == pytest.approx(
        expected_first_case_ndcg / 2
    )


def test_retrieval_quality_metrics_report_unscorable_suites_without_perfect_scores():
    negative_case = RetrievalQualityCase(
        case_id="negative-only",
        query="must not match",
        min_result_count=0,
        forbidden_chunk_ids=("forbidden",),
    )

    negative_summary = evaluate_search_suite(
        {"negative-only": {"results": []}},
        [negative_case],
    )
    empty_summary = evaluate_search_suite({}, [])

    for summary, unscorable_count in (
        (negative_summary, 1),
        (empty_summary, 0),
    ):
        metrics = summary["quality_metrics"]
        assert metrics["scorable_case_count"] == 0
        assert metrics["unscorable_case_count"] == unscorable_count
        for metric_name in (
            "hit_rate_at_k",
            "mrr_at_k",
            "recall_at_k",
            "ndcg_at_k",
        ):
            assert metrics[metric_name] == {
                "value": None,
                "numerator": 0.0,
                "denominator": 0,
            }

    assert set(empty_summary) >= {
        "passed",
        "total",
        "passed_count",
        "average_score",
        "group_breakdown",
        "results",
        "quality_metrics",
    }


def test_retrieval_metrics_preserve_malformed_result_rank_positions():
    case = RetrievalQualityCase(
        case_id="malformed-rank-one",
        query="rank preservation",
        top_k=2,
        required_chunk_ids=("relevant",),
    )
    payloads = {
        "malformed-rank-one": {
            "results": [
                {"title": "missing chunk id"},
                {"chunk_id": "relevant"},
            ]
        }
    }

    metrics = evaluate_search_suite(payloads, [case])["quality_metrics"]

    assert metrics["hit_rate_at_k"]["value"] == 1.0
    assert metrics["mrr_at_k"]["value"] == 0.5
    assert metrics["recall_at_k"]["value"] == 1.0
    assert metrics["ndcg_at_k"]["value"] == pytest.approx(1 / math.log2(3))


def test_contextwiki_eval_runner_passes_fixture_suites():
    summary = run_contextwiki_eval()

    assert summary["passed"]
    assert summary["retrieval_suite"]["passed"]
    assert summary["answer_suite"]["passed"]
    assert "group_breakdown" in summary["retrieval_suite"]
    assert "group_breakdown" in summary["answer_suite"]
    assert "runtime_metrics" not in summary


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

    for suite_name in ("retrieval_suite", "answer_suite"):
        latency = summary["runtime_metrics"][suite_name]["latency_ms"]
        assert latency["total"] > 0
        assert latency["max"] >= latency["min"] >= 0
        assert latency["average"] >= 0

    assert (output_dir / "summary.json").is_file()
    assert (output_dir / "retrieval_suite.json").is_file()
    assert (output_dir / "answer_suite.json").is_file()
    assert (output_dir / "runtime_metrics.json").is_file()
    assert (output_dir / "portfolio_report.md").is_file()

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
    assert (output_dir / "portfolio_report.md").read_text(encoding="utf-8") == (
        second_output_dir / "portfolio_report.md"
    ).read_text(encoding="utf-8")

    deterministic_rerun = run_contextwiki_eval(output_dir=output_dir)

    assert deterministic_rerun["passed"]
    assert "runtime_metrics" not in deterministic_rerun
    assert not (output_dir / "runtime_metrics.json").exists()


def test_contextwiki_eval_runner_disables_query_rewriter(monkeypatch):
    def fail_build_query_rewriter(*args, **kwargs):
        raise AssertionError("D1 eval runner should not build a live query rewriter")

    monkeypatch.setattr("search.context_service.build_query_rewriter", fail_build_query_rewriter)

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
