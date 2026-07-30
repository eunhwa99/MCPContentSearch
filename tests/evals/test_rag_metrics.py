from __future__ import annotations

import math

import pytest

from evals.metrics import (
    aggregate_suite_metrics,
    compute_ranking_metrics,
    metric_payload,
)
from evals.retrieval_quality import (
    RetrievalQualityCase,
    evaluate_search_payload,
    evaluate_search_suite,
)

pytestmark = pytest.mark.unit


def test_compute_ranking_metrics_for_perfect_and_missed_hit():
    perfect = compute_ranking_metrics(
        ranked_chunk_ids=["a", "b", "c"],
        relevant_chunk_ids=["a"],
        k=5,
    )
    missed = compute_ranking_metrics(
        ranked_chunk_ids=["x", "y"],
        relevant_chunk_ids=["a"],
        k=5,
    )

    assert perfect["hit"] == 1.0
    assert perfect["mrr"] == 1.0
    assert perfect["recall"] == 1.0
    assert perfect["ndcg"] == 1.0
    assert missed["hit"] == 0.0
    assert missed["mrr"] == 0.0
    assert missed["recall"] == 0.0
    assert missed["ndcg"] == 0.0


def test_compute_ranking_metrics_mrr_and_ndcg_at_rank_two():
    values = compute_ranking_metrics(
        ranked_chunk_ids=["noise", "rel"],
        relevant_chunk_ids=["rel"],
        k=5,
    )
    assert values["hit"] == 1.0
    assert values["mrr"] == 0.5
    assert values["recall"] == 1.0
    expected_ndcg = (1.0 / math.log2(3)) / (1.0 / math.log2(2))
    assert values["ndcg"] == pytest.approx(expected_ndcg)


def test_unlabeled_and_no_answer_cases_are_na_not_zero():
    suite = aggregate_suite_metrics(
        case_results=[
            {
                "case_id": "pos",
                "no_answer": False,
                "relevant_chunk_ids": ["a"],
                "ranked_chunk_ids": ["a"],
                "forbidden_inactive_chunk_ids": [],
                "cited_chunk_ids": ["a"],
                "required_citation_chunk_ids": ["a"],
                "expected_status": "grounded",
                "evidence_status": "grounded",
            },
            {
                "case_id": "no-answer",
                "no_answer": True,
                "relevant_chunk_ids": [],
                "ranked_chunk_ids": [],
                "forbidden_inactive_chunk_ids": [],
                "cited_chunk_ids": [],
                "required_citation_chunk_ids": [],
                "expected_status": "insufficient",
                "evidence_status": "insufficient",
            },
            {
                "case_id": "unlabeled",
                "no_answer": False,
                "relevant_chunk_ids": [],
                "ranked_chunk_ids": ["z"],
                "forbidden_inactive_chunk_ids": [],
                "cited_chunk_ids": [],
                "required_citation_chunk_ids": [],
                "expected_status": "",
                "evidence_status": "grounded",
            },
        ],
        k=5,
    )

    assert suite["scorable_case_count"] == 1
    assert suite["hit_at_k"]["value"] == 1.0
    assert suite["hit_at_k"]["numerator"] == 1.0
    assert suite["hit_at_k"]["denominator"] == 1
    # No citation-precision labels on unlabeled/no-answer alone should not force 0.
    assert "citation_precision" in suite
    assert "citation_recall" in suite
    assert "insufficient_status_accuracy" in suite
    assert suite["insufficient_status_accuracy"]["value"] == 1.0
    assert suite["insufficient_status_accuracy"]["denominator"] == 1


def test_citation_precision_penalizes_distractors_in_cited_list():
    """Full ranked top-k as cited must not tautologically yield precision 1.0."""
    suite = aggregate_suite_metrics(
        case_results=[
            {
                "case_id": "with-distractor",
                "no_answer": False,
                "relevant_chunk_ids": ["relevant"],
                "ranked_chunk_ids": ["relevant", "distractor"],
                "forbidden_inactive_chunk_ids": [],
                "cited_chunk_ids": ["relevant", "distractor"],
                "required_citation_chunk_ids": ["relevant"],
                "expected_status": "grounded",
                "evidence_status": "grounded",
            }
        ],
        k=5,
    )

    assert suite["citation_precision"]["value"] == 0.5
    assert suite["citation_recall"]["value"] == 1.0


def test_metric_payload_uses_null_value_when_denominator_zero():
    payload = metric_payload(numerator=0.0, denominator=0)
    assert payload["value"] is None
    assert payload["numerator"] == 0.0
    assert payload["denominator"] == 0


def test_evaluate_search_suite_adds_quality_metrics_without_breaking_contract():
    cases = [
        RetrievalQualityCase(
            case_id="hit",
            query="readme setup",
            group="readme",
            top_k=5,
            expected_top_chunk_id="readme-chunk",
            required_chunk_ids=("readme-chunk",),
            expected_source_id="source_aurora_docs",
        ),
        RetrievalQualityCase(
            case_id="no-label",
            query="unknown topic",
            group="no-answer",
            top_k=5,
            min_result_count=0,
            no_answer=True,
            forbidden_chunk_ids=("inactive-chunk",),
            forbidden_inactive_chunk_ids=("inactive-chunk",),
        ),
    ]
    payloads = {
        "hit": {
            "results": [
                {"chunk_id": "readme-chunk", "source_id": "source_aurora_docs"},
            ]
        },
        "no-label": {"results": []},
    }

    suite = evaluate_search_suite(payloads, cases)

    assert suite["passed"] is True
    assert suite["total"] == 2
    assert suite["passed_count"] == 2
    assert "average_score" in suite
    assert "group_breakdown" in suite
    assert "results" in suite
    metrics = suite["quality_metrics"]
    assert metrics["scorable_case_count"] == 1
    assert metrics["hit_at_k"]["value"] == 1.0
    assert metrics["mrr_at_k"]["denominator"] == 1
    assert metrics["recall_at_k"]["denominator"] == 1
    assert metrics["ndcg_at_k"]["denominator"] == 1
    assert "stale_inactive_block_rate" in metrics
    # Retrieval-only suite must not invent insufficient accuracy from empty evidence.
    assert metrics["insufficient_status_accuracy"]["value"] is None
    assert metrics["insufficient_status_accuracy"]["denominator"] == 0


def test_hard_negative_ordering_check_only_when_both_appear():
    case = RetrievalQualityCase(
        case_id="hardneg",
        query="sqlite gate",
        group="hard-negative",
        top_k=3,
        expected_top_chunk_id="adr-chunk",
        required_chunk_ids=("adr-chunk",),
        hard_negative_chunk_ids=("hardneg-chunk",),
        relevant_chunk_ids=("adr-chunk",),
    )
    # Relevant missing: ordering check stays true; required/top checks fail.
    missing_relevant = evaluate_search_payload(
        {"results": [{"chunk_id": "hardneg-chunk", "source_id": "src"}]},
        case,
    )
    assert missing_relevant.checks["hard_negative_not_above_relevant"] is True
    assert "required_chunk_ids_present" in missing_relevant.failures

    # Hard-neg above relevant fails ordering.
    hard_first = evaluate_search_payload(
        {
            "results": [
                {"chunk_id": "hardneg-chunk", "source_id": "src"},
                {"chunk_id": "adr-chunk", "source_id": "src"},
            ]
        },
        case,
    )
    assert hard_first.checks["hard_negative_not_above_relevant"] is False


def test_failed_cases_include_ids_and_failure_reasons():
    cases = [
        RetrievalQualityCase(
            case_id="miss",
            query="adr sqlite",
            top_k=3,
            expected_top_chunk_id="adr-chunk",
            required_chunk_ids=("adr-chunk",),
        )
    ]
    suite = evaluate_search_suite(
        {"miss": {"results": [{"chunk_id": "other", "source_id": "source_aurora_docs"}]}},
        cases,
    )
    assert suite["passed"] is False
    failed = [item for item in suite["results"] if not item["passed"]]
    assert failed[0]["case_id"] == "miss"
    assert failed[0]["failures"]
