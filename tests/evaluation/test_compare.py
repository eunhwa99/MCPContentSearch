import pytest

from evaluation.compare import compare_reports, validate_threshold_configuration


pytestmark = pytest.mark.unit

THRESHOLDS = {
    "recall_at_5": {"max_drop": 0.05},
    "mrr": {"max_drop": 0.05},
    "citation_validity_rate": {"min": 1.0},
    "duplicate_result_rate": {"max": 0.0},
    "empty_result_accuracy": {"min": 0.95},
    "source_type_filter_accuracy": {"min": 1.0},
    "experience_type_filter_accuracy": {"min": 1.0},
    "p95_latency_ms": {"max": 1000.0},
}


def _report(metrics: dict) -> dict:
    return {
        "report_version": 2,
        "dataset": {
            "name": "retrieval_gold.example.jsonl",
            "label_source": "deterministic_fixture",
            "size": 13,
        },
        "input_digests": {
            "dataset_sha256": "1" * 64,
            "corpus_sha256": "2" * 64,
            "configuration_sha256": "3" * 64,
        },
        "execution_path": {
            "identity": "context-evidence-offline-v1",
        },
        "metrics": metrics,
    }


def test_comparison_passes_within_relative_and_absolute_thresholds():
    baseline = _report(
        {
            "recall_at_5": 0.90,
            "mrr": 0.80,
            "citation_validity_rate": 1.0,
            "duplicate_result_rate": 0.0,
            "empty_result_accuracy": 0.95,
            "source_type_filter_accuracy": 1.0,
            "experience_type_filter_accuracy": 1.0,
            "p95_latency_ms": 700.0,
        }
    )
    current = _report(
        {
            "recall_at_5": 0.86,
            "mrr": 0.76,
            "citation_validity_rate": 1.0,
            "duplicate_result_rate": 0.0,
            "empty_result_accuracy": 1.0,
            "source_type_filter_accuracy": 1.0,
            "experience_type_filter_accuracy": 1.0,
            "p95_latency_ms": 850.0,
        }
    )

    comparison = compare_reports(baseline, current, THRESHOLDS)

    assert comparison["passed"]
    assert comparison["violations"] == []
    assert comparison["deltas"]["recall_at_5"]["absolute"] == pytest.approx(
        -0.04
    )
    assert comparison["deltas"]["mrr"]["absolute"] == pytest.approx(-0.04)


def test_comparison_reports_every_threshold_violation_and_missing_metric():
    baseline = _report(
        {
            "recall_at_5": 0.90,
            "mrr": 0.80,
            "citation_validity_rate": 1.0,
            "duplicate_result_rate": 0.0,
            "empty_result_accuracy": 1.0,
            "source_type_filter_accuracy": 1.0,
            "experience_type_filter_accuracy": 1.0,
            "p95_latency_ms": 700.0,
        }
    )
    current = _report(
        {
            "recall_at_5": 0.84,
            "citation_validity_rate": 0.99,
            "duplicate_result_rate": 0.02,
            "empty_result_accuracy": 0.90,
            "source_type_filter_accuracy": 0.95,
            "experience_type_filter_accuracy": 0.95,
            "p95_latency_ms": 1200.0,
        }
    )

    comparison = compare_reports(baseline, current, THRESHOLDS)

    assert not comparison["passed"]
    violations = {item["metric"]: item["reason"] for item in comparison["violations"]}
    assert violations == {
        "recall_at_5": "max_drop_exceeded",
        "mrr": "missing_current_metric",
        "citation_validity_rate": "below_minimum",
        "duplicate_result_rate": "above_maximum",
        "empty_result_accuracy": "below_minimum",
        "source_type_filter_accuracy": "below_minimum",
        "experience_type_filter_accuracy": "below_minimum",
        "p95_latency_ms": "above_maximum",
    }


def test_comparison_fails_closed_when_baseline_metric_for_delta_is_missing():
    baseline = _report({"recall_at_5": 0.9})
    current = _report({"recall_at_5": 0.9, "mrr": 0.8})

    comparison = compare_reports(
        baseline,
        current,
        {"mrr": {"max_drop": 0.05}},
    )

    assert not comparison["passed"]
    assert comparison["violations"] == [
        {"metric": "mrr", "reason": "missing_baseline_metric"}
    ]


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("dataset_sha256", "dataset_digest_mismatch"),
        ("corpus_sha256", "corpus_digest_mismatch"),
        ("configuration_sha256", "configuration_digest_mismatch"),
        ("execution_path", "execution_path_mismatch"),
    ],
)
def test_comparison_fails_closed_when_workload_identity_changes(field, reason):
    baseline = _report({"recall_at_5": 1.0})
    current = _report({"recall_at_5": 1.0})
    if field == "execution_path":
        current["execution_path"]["identity"] = "different-path-v1"
    else:
        current["input_digests"][field] = "f" * 64

    comparison = compare_reports(
        baseline,
        current,
        {"recall_at_5": {"max_drop": 0.05}},
    )

    assert not comparison["passed"]
    assert {item["reason"] for item in comparison["violations"]} == {reason}


@pytest.mark.parametrize(
    ("missing_field", "reason"),
    [
        ("input_digests", "missing_baseline_input_digests"),
        ("execution_path", "missing_baseline_execution_path_identity"),
    ],
)
def test_comparison_rejects_legacy_baseline_without_workload_identity(
    missing_field, reason
):
    baseline = _report({"recall_at_5": 1.0})
    baseline.pop(missing_field)
    current = _report({"recall_at_5": 1.0})

    comparison = compare_reports(
        baseline,
        current,
        {"recall_at_5": {"max_drop": 0.05}},
    )

    assert not comparison["passed"]
    assert reason in {item["reason"] for item in comparison["violations"]}


@pytest.mark.parametrize(
    "thresholds",
    [
        {"recall_at_5": {"min": 0.5, "private_rule": "do-not-echo"}},
        {"private_metric_do_not_echo": {"min": 0.5}},
        {"recall_at_5": {"min": 0.9, "max": 0.1}},
        {"recall_at_5": {"max_drop": -0.1}},
        {"recall_at_5": []},
        {},
        [],
        None,
    ],
)
def test_comparison_rejects_invalid_threshold_schema_before_metric_comparison(
    thresholds,
):
    baseline = _report({"recall_at_5": 1.0})
    current = {
        **_report({"recall_at_5": 0.0}),
        "dataset": {"name": "wrong-private-dataset"},
    }

    comparison = compare_reports(baseline, current, thresholds)

    assert not comparison["passed"]
    assert comparison["deltas"] == {}
    assert comparison["thresholds"] == {}
    assert comparison["violations"] == [
        {"metric": "threshold_configuration", "reason": "invalid_schema"}
    ]
    assert "do-not-echo" not in str(comparison)
    assert "private_metric" not in str(comparison)
    validation = validate_threshold_configuration(baseline, thresholds)
    assert not validation["passed"]
