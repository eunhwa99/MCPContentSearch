import json
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.e2e


def _workload_identity() -> dict:
    return {
        "report_version": 2,
        "input_digests": {
            "dataset_sha256": "1" * 64,
            "corpus_sha256": "2" * 64,
            "configuration_sha256": "3" * 64,
        },
        "execution_path": {"identity": "context-evidence-offline-v1"},
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_compare_cli_writes_report_and_returns_nonzero_for_regression(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    thresholds_path = tmp_path / "thresholds.json"
    output_path = tmp_path / "comparison.json"
    _write_json(
        baseline_path,
        {
            **_workload_identity(),
            "dataset": {
                "name": "retrieval_gold.example.jsonl",
                "label_source": "deterministic_fixture",
                "size": 13,
            },
            "metrics": {"recall_at_5": 0.90, "mrr": 0.80},
        },
    )
    _write_json(
        current_path,
        {
            **_workload_identity(),
            "dataset": {
                "name": "retrieval_gold.example.jsonl",
                "label_source": "deterministic_fixture",
                "size": 13,
            },
            "metrics": {"recall_at_5": 0.88, "mrr": 0.79},
        },
    )
    _write_json(
        thresholds_path,
        {
            "recall_at_5": {"max_drop": 0.05},
            "mrr": {"max_drop": 0.05},
        },
    )

    passing = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.compare",
            "--baseline",
            str(baseline_path),
            "--current",
            str(current_path),
            "--thresholds",
            str(thresholds_path),
            "--output",
            str(output_path),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert passing.returncode == 0, passing.stderr
    assert json.loads(output_path.read_text(encoding="utf-8"))["passed"] is True

    _write_json(
        current_path,
        {
            **_workload_identity(),
            "dataset": {
                "name": "retrieval_gold.example.jsonl",
                "label_source": "deterministic_fixture",
                "size": 13,
            },
            "metrics": {"recall_at_5": 0.80, "mrr": 0.79},
        },
    )
    failing = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.compare",
            "--baseline",
            str(baseline_path),
            "--current",
            str(current_path),
            "--thresholds",
            str(thresholds_path),
            "--output",
            str(output_path),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert failing.returncode == 1
    comparison = json.loads(output_path.read_text(encoding="utf-8"))
    assert comparison["passed"] is False
    assert comparison["violations"] == [
        {"metric": "recall_at_5", "reason": "max_drop_exceeded"}
    ]


def test_compare_cli_rejects_same_named_dataset_with_changed_corpus(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    thresholds_path = tmp_path / "thresholds.json"
    baseline = {
        **_workload_identity(),
        "dataset": {
            "name": "retrieval_gold.example.jsonl",
            "label_source": "deterministic_fixture",
            "size": 13,
        },
        "metrics": {"recall_at_5": 1.0},
    }
    current = json.loads(json.dumps(baseline))
    current["input_digests"]["corpus_sha256"] = "f" * 64
    _write_json(baseline_path, baseline)
    _write_json(current_path, current)
    _write_json(thresholds_path, {"recall_at_5": {"max_drop": 0.05}})

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.compare",
            "--baseline",
            str(baseline_path),
            "--current",
            str(current_path),
            "--thresholds",
            str(thresholds_path),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    comparison = json.loads(completed.stdout)
    assert comparison["violations"] == [
        {"metric": "corpus", "reason": "corpus_digest_mismatch"}
    ]


def test_compare_cli_rejects_invalid_threshold_schema_without_echoing_unknown_key(
    tmp_path,
):
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    thresholds_path = tmp_path / "thresholds.json"
    output_path = tmp_path / "comparison.json"
    _write_json(
        baseline_path,
        {**_workload_identity(), "dataset": {}, "metrics": {"recall_at_5": 1.0}},
    )
    _write_json(
        current_path,
        {**_workload_identity(), "dataset": {}, "metrics": {"recall_at_5": 0.0}},
    )
    _write_json(
        thresholds_path,
        {"private_threshold_do_not_echo": {"min": 1.0}},
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.compare",
            "--baseline",
            str(baseline_path),
            "--current",
            str(current_path),
            "--thresholds",
            str(thresholds_path),
            "--output",
            str(output_path),
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    rendered = completed.stdout + completed.stderr
    assert "private_threshold_do_not_echo" not in rendered
    assert json.loads(output_path.read_text(encoding="utf-8"))["violations"] == [
        {"metric": "threshold_configuration", "reason": "invalid_schema"}
    ]
