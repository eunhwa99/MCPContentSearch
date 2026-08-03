import math

import pytest

from evaluation.metrics import (
    calculate_citation_validity,
    calculate_duplicate_rates,
    calculate_ingestion_metrics,
    evaluate_retrieval_metrics,
    latency_summary_ms,
)


pytestmark = pytest.mark.unit


def test_retrieval_metrics_use_unique_relevance_and_explicit_denominators():
    cases = [
        {
            "query_id": "q-1",
            "expected_chunk_ids": ["c1", "c2"],
            "graded_relevance": {"c1": 3, "c2": 2},
            "should_return_empty": False,
            "allowed_source_types": [],
            "allowed_experience_types": [],
        },
        {
            "query_id": "q-2",
            "expected_chunk_ids": ["c3"],
            "graded_relevance": {"c3": 3},
            "should_return_empty": False,
            "allowed_source_types": [],
            "allowed_experience_types": [],
        },
    ]
    results = {
        "q-1": [
            {"chunk_id": "c1"},
            {"chunk_id": "irrelevant"},
            {"chunk_id": "c2"},
            {"chunk_id": "c1"},
        ],
        "q-2": [{"chunk_id": "other"}, {"chunk_id": "c3"}],
    }

    metrics = evaluate_retrieval_metrics(cases, results)

    assert metrics["recall_at_1"] == pytest.approx(0.25)
    assert metrics["recall_at_3"] == pytest.approx(1.0)
    assert metrics["recall_at_5"] == pytest.approx(1.0)
    assert metrics["precision_at_3"] == pytest.approx(0.5)
    assert metrics["precision_at_5"] == pytest.approx(0.3)
    assert metrics["mrr"] == pytest.approx(0.75)
    assert metrics["ndcg_at_5"] == pytest.approx(0.79338, rel=1e-4)
    assert metrics["metric_denominators"]["recall_at_5"] == 2
    assert metrics["metric_denominators"]["ndcg_at_5"] == 2


def test_document_recall_scores_expected_documents_independently_of_chunks():
    cases = [
        {
            "query_id": "document-only",
            "expected_chunk_ids": [],
            "expected_document_ids": ["doc-target"],
            "graded_relevance": {},
            "should_return_empty": False,
            "allowed_source_types": [],
            "allowed_experience_types": [],
        },
        {
            "query_id": "missing-document",
            "expected_chunk_ids": ["chunk-target"],
            "expected_document_ids": ["doc-missing"],
            "graded_relevance": {"chunk-target": 3},
            "should_return_empty": False,
            "allowed_source_types": [],
            "allowed_experience_types": [],
        },
    ]
    results = {
        "document-only": [
            {"chunk_id": "any", "document_id": "doc-target"}
        ],
        "missing-document": [
            {"chunk_id": "chunk-target", "document_id": "doc-wrong"}
        ],
    }

    metrics = evaluate_retrieval_metrics(cases, results)

    assert metrics["document_recall_at_1"] == pytest.approx(0.5)
    assert metrics["document_recall_at_3"] == pytest.approx(0.5)
    assert metrics["document_recall_at_5"] == pytest.approx(0.5)
    assert metrics["metric_denominators"]["document_recall_at_5"] == 2
    assert metrics["metric_denominators"]["recall_at_5"] == 1


def test_metric_zero_denominators_are_none_not_fabricated_zeroes():
    metrics = evaluate_retrieval_metrics([], {})

    for metric_name in (
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "precision_at_3",
        "precision_at_5",
        "mrr",
        "ndcg_at_5",
        "source_type_filter_accuracy",
        "experience_type_filter_accuracy",
        "empty_result_accuracy",
        "document_recall_at_1",
        "document_recall_at_3",
        "document_recall_at_5",
    ):
        assert metrics[metric_name] is None
        assert metrics["metric_denominators"][metric_name] == 0


def test_ndcg_is_unscorable_without_graded_relevance():
    cases = [
        {
            "query_id": "q-1",
            "expected_chunk_ids": ["c1"],
            "graded_relevance": {},
            "should_return_empty": False,
            "allowed_source_types": [],
            "allowed_experience_types": [],
        }
    ]

    metrics = evaluate_retrieval_metrics(cases, {"q-1": [{"chunk_id": "c1"}]})

    assert metrics["ndcg_at_5"] is None
    assert metrics["metric_denominators"]["ndcg_at_5"] == 0


def test_filter_and_empty_result_accuracy_use_only_applicable_cases():
    cases = [
        {
            "query_id": "filtered",
            "expected_chunk_ids": ["c1"],
            "graded_relevance": {"c1": 3},
            "should_return_empty": False,
            "allowed_source_types": ["resume"],
            "allowed_experience_types": ["professional"],
        },
        {
            "query_id": "empty-correct",
            "expected_chunk_ids": [],
            "graded_relevance": {},
            "should_return_empty": True,
            "allowed_source_types": [],
            "allowed_experience_types": [],
        },
        {
            "query_id": "empty-false-positive",
            "expected_chunk_ids": ["c2"],
            "graded_relevance": {"c2": 3},
            "should_return_empty": False,
            "allowed_source_types": [],
            "allowed_experience_types": [],
        },
    ]
    results = {
        "filtered": [
            {
                "chunk_id": "c1",
                "source_type": "resume",
                "experience_type": "professional",
            },
            {
                "chunk_id": "c-hard-negative",
                "source_type": "project",
                "experience_type": "personal_project",
            },
        ],
        "empty-correct": [],
        "empty-false-positive": [],
    }

    metrics = evaluate_retrieval_metrics(cases, results)

    assert metrics["source_type_filter_accuracy"] == pytest.approx(0.5)
    assert metrics["experience_type_filter_accuracy"] == pytest.approx(0.5)
    assert metrics["empty_result_accuracy"] == pytest.approx(2 / 3)
    assert metrics["metric_denominators"]["source_type_filter_accuracy"] == 2
    assert metrics["metric_denominators"]["empty_result_accuracy"] == 3


def test_citation_validity_checks_ids_quote_and_section_resolution():
    indexed_chunks = {
        "c-valid": {
            "document_id": "d-valid",
            "content": "Reduced incident recovery time by 40 percent.",
            "section_title": "Reliability",
        },
        "c-quote": {
            "document_id": "d-valid",
            "content": "Stored source sentence.",
            "section_title": "Experience",
        },
        "c-section": {
            "document_id": "d-valid",
            "content": "Section-scoped evidence.",
            "section_title": "Projects",
        },
    }
    indexed_documents = {"d-valid": {"document_id": "d-valid"}}
    results = {
        "q-1": [
            {
                "chunk_id": "c-valid",
                "document_id": "d-valid",
                "exact_quote": "incident recovery time by 40 percent",
                "section_title": "Reliability",
            },
            {
                "chunk_id": "missing",
                "document_id": "d-valid",
                "exact_quote": "anything",
            },
            {
                "chunk_id": "c-quote",
                "document_id": "d-valid",
                "exact_quote": "invented paraphrase",
                "section_title": "Experience",
            },
            {
                "chunk_id": "c-section",
                "document_id": "d-valid",
                "exact_quote": "Section-scoped evidence.",
                "section_title": "Education",
            },
        ]
    }

    metric = calculate_citation_validity(
        results,
        indexed_chunks=indexed_chunks,
        indexed_documents=indexed_documents,
    )

    assert metric["citation_validity_rate"] == pytest.approx(0.25)
    assert metric["valid_count"] == 1
    assert metric["total_count"] == 4
    assert {failure["reason"] for failure in metric["invalid_citations"]} == {
        "missing_chunk",
        "quote_not_in_source",
        "section_mismatch",
    }


def test_citation_validity_rejects_omitted_indexed_section_metadata():
    indexed_chunks = {
        "c-section": {
            "document_id": "d-valid",
            "content": "Stored section-scoped evidence.",
            "section_title": "Reliability",
            "parent_section_title": "Professional Experience",
        }
    }
    results = {
        "q-1": [
            {
                "chunk_id": "c-section",
                "document_id": "d-valid",
                "exact_quote": "Stored section-scoped evidence.",
            },
            {
                "chunk_id": "c-section",
                "document_id": "d-valid",
                "exact_quote": "Stored section-scoped evidence.",
                "section_title": "Reliability",
            },
        ]
    }

    metric = calculate_citation_validity(
        results,
        indexed_chunks=indexed_chunks,
        indexed_documents={"d-valid": {"document_id": "d-valid"}},
    )

    assert metric["citation_validity_rate"] == 0.0
    assert metric["valid_count"] == 0
    assert [item["reason"] for item in metric["invalid_citations"]] == [
        "section_mismatch",
        "section_mismatch",
    ]


def test_duplicate_rates_separate_exact_and_near_duplicates():
    results = {
        "q-1": [
            {
                "chunk_id": "c1",
                "exact_quote": "Reduced incident recovery time by 40 percent.",
            },
            {
                "chunk_id": "c1-copy",
                "exact_quote": "Reduced incident recovery time by 40 percent.",
            },
            {
                "chunk_id": "c2",
                "exact_quote": "Reduced incident recovery time by forty percent.",
            },
            {"chunk_id": "c3", "exact_quote": "Implemented a queue consumer."},
        ]
    }

    metrics = calculate_duplicate_rates(results, near_duplicate_threshold=0.7)

    assert metrics["exact_duplicate_result_rate"] == pytest.approx(0.25)
    assert metrics["near_duplicate_result_rate"] == pytest.approx(0.25)
    assert metrics["duplicate_result_rate"] == pytest.approx(0.5)
    assert metrics["total_result_count"] == 4


def test_latency_summary_uses_deterministic_nearest_rank_percentiles():
    summary = latency_summary_ms([1.0, 2.0, 3.0, 4.0, 100.0])

    assert summary == {
        "mean_latency_ms": 22.0,
        "p50_latency_ms": 3.0,
        "p95_latency_ms": 100.0,
        "latency_sample_count": 5,
    }
    empty = latency_summary_ms([])
    assert empty["mean_latency_ms"] is None
    assert empty["p50_latency_ms"] is None
    assert empty["p95_latency_ms"] is None
    assert empty["latency_sample_count"] == 0
    assert not any(math.isnan(value) for value in summary.values())


def test_ingestion_metrics_use_explicit_counters_timings_and_denominators():
    metrics = calculate_ingestion_metrics(
        attempted_documents=10,
        parsed_documents=9,
        unchanged_documents=4,
        changed_documents=5,
        reembedded_unchanged_documents=1,
        full_ingestion_latencies_ms=[100.0, 200.0],
        incremental_update_latencies_ms=[20.0, 40.0, 60.0],
    )

    assert metrics["parsing_success_rate"] == pytest.approx(0.9)
    assert metrics["unchanged_document_skip_rate"] == pytest.approx(0.4)
    assert metrics["unnecessary_reembedding_rate"] == pytest.approx(0.25)
    assert metrics["full_ingestion_latency_ms"] == pytest.approx(150.0)
    assert metrics["incremental_update_latency_ms"] == pytest.approx(40.0)
    assert metrics["metric_denominators"] == {
        "parsing_success_rate": 10,
        "unchanged_document_skip_rate": 10,
        "unnecessary_reembedding_rate": 4,
        "full_ingestion_latency_ms": 2,
        "incremental_update_latency_ms": 3,
    }


def test_ingestion_metrics_are_none_when_no_ingestion_was_observed():
    metrics = calculate_ingestion_metrics()

    assert all(
        metrics[name] is None
        for name in (
            "parsing_success_rate",
            "unchanged_document_skip_rate",
            "unnecessary_reembedding_rate",
            "full_ingestion_latency_ms",
            "incremental_update_latency_ms",
        )
    )
    assert not any(metrics["metric_denominators"].values())


def test_ingestion_metrics_reject_incoherent_counters():
    with pytest.raises(ValueError, match="parsed_documents"):
        calculate_ingestion_metrics(attempted_documents=1, parsed_documents=2)
