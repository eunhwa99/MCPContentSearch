from __future__ import annotations

import pytest

from evals.reporting import render_rag_report

pytestmark = pytest.mark.unit


def test_render_rag_report_includes_dataset_metrics_failures_and_limits():
    summary = {
        "dataset_version": "rag_v1",
        "retrieval_config": {"mode": "lexical", "live": False},
        "fixture_metrics": {
            "scorable_case_count": 2,
            "hit_at_k": {"value": 1.0, "numerator": 2.0, "denominator": 2},
            "mrr_at_k": {"value": 1.0, "numerator": 2.0, "denominator": 2},
            "recall_at_k": {"value": 1.0, "numerator": 2.0, "denominator": 2},
            "ndcg_at_k": {"value": 1.0, "numerator": 2.0, "denominator": 2},
            "citation_precision": {"value": 1.0, "numerator": 1.0, "denominator": 1},
            "citation_recall": {"value": 1.0, "numerator": 1.0, "denominator": 1},
            "insufficient_status_accuracy": {
                "value": 1.0,
                "numerator": 1.0,
                "denominator": 1,
            },
            "stale_inactive_block_rate": {
                "value": 1.0,
                "numerator": 1.0,
                "denominator": 1,
            },
        },
        "live_metrics": None,
        "group_breakdown": {
            "readme": {"total": 1, "passed_count": 1},
            "runbook": {"total": 1, "passed_count": 1},
        },
        "failures": [
            {
                "case_id": "dev-hardneg-example",
                "reasons": ["required_chunk_ids_present"],
            }
        ],
        "latency_ms": {"average": 1.2, "p95": 2.0},
        "baseline_delta": None,
        "limitations": [
            "Fixture lexical results are not production embedding performance.",
        ],
    }

    report = render_rag_report(summary)

    assert "# RAG Evaluation Report" in report
    assert "rag_v1" in report
    assert "lexical" in report
    assert "Hit@K" in report or "hit_at_k" in report
    assert "Numerator" in report
    assert "Denominator" in report
    assert "dev-hardneg-example" in report
    assert "not production" in report.lower() or "not a production" in report.lower()
    assert "Fixture" in report
    assert "Live" in report
    assert "citation" in report.lower() or "Citation" in report
    # Per-metric scorable uses denominator, not a merged retrieval total alone.
    assert "Denominator" in report


def test_render_rag_report_separates_fixture_and_live_tables():
    summary = {
        "dataset_version": "rag_v1",
        "retrieval_config": {"mode": "vector", "live": True},
        "fixture_metrics": {
            "scorable_case_count": 1,
            "hit_at_k": {"value": 1.0, "numerator": 1.0, "denominator": 1},
            "mrr_at_k": {"value": 1.0, "numerator": 1.0, "denominator": 1},
            "recall_at_k": {"value": 1.0, "numerator": 1.0, "denominator": 1},
            "ndcg_at_k": {"value": 1.0, "numerator": 1.0, "denominator": 1},
            "citation_precision": {"value": None, "numerator": 0.0, "denominator": 0},
            "citation_recall": {"value": None, "numerator": 0.0, "denominator": 0},
            "insufficient_status_accuracy": {
                "value": None,
                "numerator": 0.0,
                "denominator": 0,
            },
            "stale_inactive_block_rate": {
                "value": None,
                "numerator": 0.0,
                "denominator": 0,
            },
        },
        "live_metrics": {
            "scorable_case_count": 1,
            "hit_at_k": {"value": 0.0, "numerator": 0.0, "denominator": 1},
            "mrr_at_k": {"value": 0.0, "numerator": 0.0, "denominator": 1},
            "recall_at_k": {"value": 0.0, "numerator": 0.0, "denominator": 1},
            "ndcg_at_k": {"value": 0.0, "numerator": 0.0, "denominator": 1},
            "status": "executed",
        },
        "group_breakdown": {},
        "failures": [],
        "latency_ms": {"average": 10.0, "p95": 12.0},
        "baseline_delta": {"hit_at_k": -1.0},
        "limitations": ["Live embedding quality depends on provider availability."],
    }

    report = render_rag_report(summary)
    assert "Fixture" in report
    assert "Live" in report
    lower = report.lower()
    assert "baseline" in lower or "delta" in lower or "change" in lower
