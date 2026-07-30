from __future__ import annotations

import csv
import json
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration


def test_run_retrieval_benchmark_help_documents_live_budget_and_outputs():
    repo_root = Path(__file__).resolve().parents[2]
    env = {"PATH": str(Path(sys.executable).parent)}

    result = subprocess.run(
        [sys.executable, "scripts/run_retrieval_benchmark.py", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    help_text = result.stdout.lower()
    assert "--live" in help_text
    assert "--max-budget" in help_text
    assert "json" in help_text
    assert "csv" in help_text
    assert "markdown" in help_text or "md" in help_text


def test_run_retrieval_benchmark_default_is_offline_and_skips_unrun_providers(
    tmp_path,
):
    repo_root = Path(__file__).resolve().parents[2]
    output_dir = tmp_path / "benchmark"
    env = {"PATH": str(Path(sys.executable).parent)}

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_retrieval_benchmark.py",
            "--split",
            "test",
            "--output-dir",
            str(output_dir),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    summary_path = output_dir / "benchmark_summary.json"
    assert summary_path.is_file()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert "lexical" in summary["configs"]
    lexical = summary["configs"]["lexical"]
    assert lexical.get("status") == "executed"
    assert "hit_at_5" in lexical["metrics"] or "hit_at_k" in lexical["metrics"]

    for name in ("vector", "hybrid"):
        if name in summary["configs"]:
            cfg = summary["configs"][name]
            assert cfg.get("status") in {"skipped", "not_run"}
            # Unrun providers must not be recorded as zero-quality wins/losses.
            metrics = cfg.get("metrics")
            if metrics:
                assert all(
                    not isinstance(metric, dict)
                    or metric.get("value") is None
                    for metric in metrics.values()
                )
            else:
                assert metrics in (None, {})

    assert (output_dir / "benchmark_summary.csv").is_file()
    assert (output_dir / "benchmark_report.md").is_file()
    with (output_dir / "benchmark_summary.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert "config" in rows[0]


def test_run_retrieval_benchmark_live_requires_positive_max_budget(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    output_dir = tmp_path / "benchmark-live"
    env = {"PATH": str(Path(sys.executable).parent)}

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_retrieval_benchmark.py",
            "--split",
            "test",
            "--output-dir",
            str(output_dir),
            "--live",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode != 0
    combined = f"{result.stdout}\n{result.stderr}".lower()
    assert "max-budget" in combined or "max_budget" in combined
    assert "live" in combined


def test_run_retrieval_benchmark_live_with_budget_does_not_score_unconfigured_as_zero(
    tmp_path,
):
    repo_root = Path(__file__).resolve().parents[2]
    output_dir = tmp_path / "benchmark-live-budget"
    env = {"PATH": str(Path(sys.executable).parent)}

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_retrieval_benchmark.py",
            "--split",
            "test",
            "--output-dir",
            str(output_dir),
            "--live",
            "--max-budget",
            "1.0",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    # Provider may be unavailable; must not treat that as zero quality scores.
    assert output_dir.is_dir()
    summary = json.loads((output_dir / "benchmark_summary.json").read_text(encoding="utf-8"))
    for name in ("vector", "hybrid"):
        cfg = summary["configs"][name]
        assert cfg.get("status") in {"provider_error", "skipped", "not_run", "budget_exceeded"}
        assert cfg.get("metrics") in (None, {})
        assert cfg.get("failure_kind") in {None, "provider_error", "budget_exceeded"}
    # Offline lexical still executes even on live attempts.
    assert summary["configs"]["lexical"]["status"] == "executed"
    assert result.returncode in {0, 1, 3}
    assert summary["live_status"] in {"provider_error", "budget_exceeded", "not_executed"}


def test_live_budget_estimate_includes_corpus_and_query_units():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "run_retrieval_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_retrieval_benchmark", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    cases = [
        {"case_id": "c0", "query": "short"},
        {"case_id": "c1", "query": "q" * 1000},
    ]
    documents = [
        {"document_id": "a", "active": True, "text": "x" * 1000},
        {"document_id": "b", "active": True, "text": "y" * 1500},
        {"document_id": "inactive", "active": False, "text": "z" * 9000},
    ]
    # index units: ceil(1000/1000)+ceil(1500/1000)=1+2=3
    # query units: (ceil(5/1000)+ceil(1000/1000))*2 hybrid = (1+1)*2=4
    # total 7 * 0.001 = 0.007
    assert module._estimate_live_embedding_cost(
        cases=cases,
        documents=documents,
        include_hybrid=True,
    ) == pytest.approx(0.007)

    # Longer text must cost more than empty/short text with the same doc+case counts.
    short_docs = [
        {"document_id": "a", "active": True, "text": "a"},
        {"document_id": "b", "active": True, "text": "b"},
    ]
    short_cost = module._estimate_live_embedding_cost(
        cases=[{"case_id": "c0", "query": "q"}, {"case_id": "c1", "query": "r"}],
        documents=short_docs,
        include_hybrid=True,
    )
    long_cost = module._estimate_live_embedding_cost(
        cases=cases,
        documents=documents,
        include_hybrid=True,
    )
    assert long_cost > short_cost

    exceeded = module._run_live_configs(
        cases=cases,
        documents=documents,
        max_budget=0.001,
        include_hybrid=True,
    )
    assert exceeded["vector"]["status"] == "budget_exceeded"
    assert exceeded["vector"]["metrics"] is None


def test_quality_gate_fails_closed_when_required_denominator_is_zero():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "run_retrieval_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_retrieval_benchmark", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert (
        module._quality_gate_passed(
            {
                "hit_at_k": {"value": None, "denominator": 0},
                "stale_inactive_block_rate": {"value": 1.0, "denominator": 2},
                "insufficient_status_accuracy": {"value": 1.0, "denominator": 1},
            }
        )
        is False
    )
    assert (
        module._quality_gate_passed(
            {
                "hit_at_k": {"value": 1.0, "denominator": 1},
                "stale_inactive_block_rate": {"value": None, "denominator": 0},
                "insufficient_status_accuracy": {"value": 1.0, "denominator": 1},
            }
        )
        is False
    )
    assert (
        module._quality_gate_passed(
            {
                "hit_at_k": {"value": 1.0, "denominator": 1},
                "stale_inactive_block_rate": {"value": 1.0, "denominator": 1},
                "insufficient_status_accuracy": {"value": 1.0, "denominator": 1},
            }
        )
        is True
    )


def test_run_retrieval_benchmark_writes_error_artifacts_on_unexpected_failure(
    tmp_path, monkeypatch
):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "run_retrieval_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_retrieval_benchmark", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    output_dir = tmp_path / "crash-artifacts"
    monkeypatch.setattr(
        module,
        "load_cases",
        lambda split: (_ for _ in ()).throw(
            RuntimeError(
                "seed boom leaked sk-proj-abcdefghijklmnopqrstuvwxyz123456 "
                "at /Users/eunhwa/.env"
            )
        ),
    )

    with pytest.raises(RuntimeError, match="seed boom"):
        module.run_retrieval_benchmark(split="test", output_dir=output_dir)

    summary_path = output_dir / "benchmark_summary.json"
    assert summary_path.is_file()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary.get("status") == "error"
    assert summary.get("error_type") == "RuntimeError"
    error_text = str(summary.get("error") or "")
    assert "seed boom" in error_text
    assert "sk-proj-abcdefghijklmnopqrstuvwxyz123456" not in error_text
    assert "/Users/eunhwa" not in error_text
    assert "<redacted>" in error_text
    report = (output_dir / "benchmark_report.md").read_text(encoding="utf-8")
    assert "sk-proj-abcdefghijklmnopqrstuvwxyz123456" not in report
    assert "/Users/eunhwa" not in report
    assert (output_dir / "rag_report.md").is_file()


def test_live_budget_compare_uses_integer_units_not_float_multiply():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "run_retrieval_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_retrieval_benchmark", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # 9 units * 0.001 can be > 0.009 in float; exact ceiling must still allow.
    cases = [{"case_id": f"c{i}", "query": "q"} for i in range(3)]
    documents = [{"document_id": f"d{i}", "active": True, "text": "x"} for i in range(3)]
    # index 3 + query 3*2 hybrid = 9 units -> 0.009
    assert module._live_embedding_units(
        cases=cases, documents=documents, include_hybrid=True
    ) == 9
    allowed = module._run_live_configs(
        cases=cases,
        documents=documents,
        max_budget=0.009,
        include_hybrid=True,
    )
    assert allowed["vector"]["status"] != "budget_exceeded"
    assert allowed["vector"]["estimated_cost"] == pytest.approx(0.009)

    blocked = module._run_live_configs(
        cases=cases,
        documents=documents,
        max_budget=0.008,
        include_hybrid=True,
    )
    assert blocked["vector"]["status"] == "budget_exceeded"


def test_stale_block_and_unlabeled_cases_use_zero_min_result_count():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "run_retrieval_benchmark.py"
    spec = importlib.util.spec_from_file_location("run_retrieval_benchmark", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert (
        module._retrieval_min_result_count(
            {
                "no_answer": False,
                "relevant_chunk_ids": [],
                "group": "stale-block",
            }
        )
        == 0
    )
    assert (
        module._retrieval_min_result_count(
            {"no_answer": True, "relevant_chunk_ids": [], "group": "no-answer"}
        )
        == 0
    )
    assert (
        module._retrieval_min_result_count(
            {
                "no_answer": False,
                "relevant_chunk_ids": ["aurora-readme-chunk"],
                "group": "readme",
            }
        )
        == 1
    )
