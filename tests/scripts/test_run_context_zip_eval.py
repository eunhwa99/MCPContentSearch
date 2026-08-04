import json
from pathlib import Path
import subprocess
import sys


def test_run_context_zip_eval_help_runs_from_repo_root_without_pythonpath():
    repo_root = Path(__file__).resolve().parents[2]
    env = {"PATH": str(Path(sys.executable).parent)}

    result = subprocess.run(
        [sys.executable, "scripts/run_context_zip_eval.py", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "Run deterministic ContextZip evals" in result.stdout


def test_run_context_zip_eval_executes_from_repo_root_without_pythonpath(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    output_dir = tmp_path / "eval-artifacts"
    env = {"PATH": str(Path(sys.executable).parent)}

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_context_zip_eval.py",
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
