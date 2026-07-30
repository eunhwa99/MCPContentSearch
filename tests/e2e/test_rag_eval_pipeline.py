from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def _load_benchmark_module():
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "scripts" / "run_retrieval_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_retrieval_benchmark", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rag_eval_pipeline_writes_deterministic_artifacts(tmp_path):
    module = _load_benchmark_module()
    output_dir = tmp_path / "rag-eval"
    summary = module.run_retrieval_benchmark(split="test", output_dir=output_dir)

    assert summary["passed"] is True
    assert summary["dataset_version"] == "rag_v1"
    assert summary["split"] == "test"
    assert (output_dir / "benchmark_summary.json").is_file()
    assert (output_dir / "benchmark_summary.csv").is_file()
    report = (output_dir / "benchmark_report.md").read_text(encoding="utf-8")
    assert "rag_v1" in report
    assert "not production" in report.lower() or "not a production" in report.lower()

    lexical = summary["configs"]["lexical"]
    assert lexical["status"] == "executed"
    metrics = lexical["metrics"]
    for key in ("hit_at_5", "mrr_at_5", "recall_at_5", "ndcg_at_5", "citation_recall"):
        assert key in metrics
        assert "numerator" in metrics[key]
        assert "denominator" in metrics[key]
    assert metrics["hit_at_5"]["value"] == 1.0
    assert metrics["hit_at_5"]["denominator"] == 8
    assert "citation_precision" in metrics
    assert metrics["citation_precision"]["denominator"] == 8
    # Full ranked top-k as cited must not tautologically yield precision 1.0.
    assert metrics["citation_precision"]["value"] is not None
    assert metrics["citation_precision"]["value"] < 1.0
    assert metrics["stale_inactive_block_rate"]["value"] == 1.0
    assert metrics["insufficient_status_accuracy"]["value"] == 1.0
    assert metrics["embedding_cost_per_query"]["value"] is None
    assert "average" in lexical["latency_ms"]
    assert "p95" in lexical["latency_ms"]
    assert summary["configs"]["vector"]["status"] == "skipped"
    # Nested suite metrics must match authoritative top-level metrics.
    assert lexical["suite"]["quality_metrics"]["insufficient_status_accuracy"] == (
        metrics["insufficient_status_accuracy"]
    )
    assert lexical["suite"]["quality_metrics"]["citation_recall"] == metrics["citation_recall"]
    saved = json.loads((output_dir / "benchmark_summary.json").read_text(encoding="utf-8"))
    assert saved["passed"] is True
    with (output_dir / "benchmark_summary.csv").open(encoding="utf-8") as handle:
        import csv

        rows = list(csv.DictReader(handle))
    assert rows
    assert "citation_precision" in rows[0]
    assert "citation_recall" in rows[0]
    assert rows[0]["citation_precision"]
    assert float(rows[0]["citation_precision"]) < 1.0
