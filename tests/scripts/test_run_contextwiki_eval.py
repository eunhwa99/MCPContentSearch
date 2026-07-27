import json
from pathlib import Path
import subprocess
import sys

import pytest

from evals.contextwiki_eval import run_contextwiki_eval


def test_run_contextwiki_eval_help_runs_from_repo_root_without_pythonpath():
    repo_root = Path(__file__).resolve().parents[2]
    env = {"PATH": str(Path(sys.executable).parent)}

    result = subprocess.run(
        [sys.executable, "scripts/run_contextwiki_eval.py", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "Run deterministic ContextWiki evals" in result.stdout


def test_run_contextwiki_eval_executes_from_repo_root_without_pythonpath(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    output_dir = tmp_path / "eval-artifacts"
    env = {"PATH": str(Path(sys.executable).parent)}

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_contextwiki_eval.py",
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
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["artifact_dir"] == str(output_dir)
    assert (output_dir / "summary.json").is_file()
    assert (output_dir / "retrieval_suite.json").is_file()
    assert (output_dir / "answer_suite.json").is_file()
    report_path = output_dir / "portfolio_report.md"
    assert report_path.is_file()

    report = report_path.read_text(encoding="utf-8")
    assert report.startswith("# ContextWiki Deterministic Evaluation Report\n")
    for heading in (
        "## Fixture snapshot",
        "## Retrieval quality",
        "## Answer quality",
        "## Interpretation boundaries",
    ):
        assert heading in report
    assert "| Answer quality |" in report
    assert "Grounded answer" not in report
    for metric_label in (
        "Hit rate at k",
        "Mean reciprocal rank at k",
        "Recall at k",
        "nDCG at k",
        "Status accuracy",
        "Required-citation recall",
        "Citation coverage",
        "Insufficient-status accuracy",
    ):
        assert metric_label in report
    assert "not a production or real-world benchmark" in report
    assert str(output_dir) not in report


def test_failed_cli_run_writes_json_and_report_before_exiting(
    tmp_path,
    monkeypatch,
    capsys,
):
    from scripts import run_contextwiki_eval as runner

    repo_root = Path(__file__).resolve().parents[2]
    output_dir = tmp_path / "failed-eval-artifacts"
    retrieval_cases_path = tmp_path / "failing-retrieval-cases.json"
    answer_cases_path = tmp_path / "failing-answer-cases.json"
    failing_retrieval_case_id = "portfolio-report-known-retrieval-failure"
    failing_answer_case_id = "portfolio-report-insufficient-status-failure"
    retrieval_cases_path.write_text(
        json.dumps(
            [
                {
                    "case_id": failing_retrieval_case_id,
                    "query": "github sync 문서",
                    "top_k": 3,
                    "expected_top_chunk_id": "intentionally-missing-chunk",
                    "required_chunk_ids": ["intentionally-missing-chunk"],
                }
            ]
        ),
        encoding="utf-8",
    )
    answer_cases_path.write_text(
        json.dumps(
            [
                {
                    "case_id": failing_answer_case_id,
                    "question": "github sync 문서",
                    "top_k": 3,
                    "expected_status": "insufficient",
                    "min_citation_count": 0,
                }
            ]
        ),
        encoding="utf-8",
    )

    def run_failing_fixture_eval(**kwargs):
        return run_contextwiki_eval(
            fixture_documents_path=repo_root
            / "evals"
            / "contextwiki_fixture_documents.json",
            retrieval_cases_path=retrieval_cases_path,
            answer_cases_path=answer_cases_path,
            **kwargs,
        )

    monkeypatch.setattr(runner, "run_contextwiki_eval", run_failing_fixture_eval)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_contextwiki_eval.py",
            "--output-dir",
            str(output_dir),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        runner.main()

    assert exc_info.value.code == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False
    for artifact_name in (
        "summary.json",
        "retrieval_suite.json",
        "answer_suite.json",
        "portfolio_report.md",
    ):
        assert (output_dir / artifact_name).is_file()

    written_summary = json.loads(
        (output_dir / "summary.json").read_text(encoding="utf-8")
    )
    report = (output_dir / "portfolio_report.md").read_text(encoding="utf-8")
    assert written_summary["passed"] is False
    assert "Overall result: **FAIL**" in report
    assert "## Failed fixture cases" in report
    assert f"- Retrieval: {failing_retrieval_case_id}" in report
    assert f"- Answer quality: {failing_answer_case_id}" in report
    assert "Grounded answer" not in report
