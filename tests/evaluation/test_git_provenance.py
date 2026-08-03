from pathlib import Path
import re
import subprocess

import pytest


pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
IDENTIFIER_SCRIPT = REPO_ROOT / "scripts/evaluation_git_identifier.sh"
IDENTIFIER_PATTERN = re.compile(
    r"commit=(?P<commit>[0-9a-f]{40});"
    r"head_tree=(?P<head_tree>[0-9a-f]{40});"
    r"worktree_tree=(?P<worktree_tree>[0-9a-f]{40});"
    r"state=(?P<state>clean|dirty)\Z"
)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _identifier(repo: Path) -> tuple[str, dict[str, str]]:
    completed = subprocess.run(
        ["bash", str(IDENTIFIER_SCRIPT)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    rendered = completed.stdout.strip()
    match = IDENTIFIER_PATTERN.fullmatch(rendered)
    assert match is not None, rendered
    return rendered, match.groupdict()


def test_git_identifier_tracks_deterministic_clean_and_dirty_worktree_trees(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture Author")
    (repo / "tracked.txt").write_text("tracked-v1\n", encoding="utf-8")
    reports = repo / "evaluation" / "reports"
    reports.mkdir(parents=True)
    (reports / "retrieval_fixture_baseline.json").write_text(
        '{"generated": 1}\n', encoding="utf-8"
    )
    (reports / "ci_thresholds.json").write_text(
        '{"recall_at_5": {"min": 1}}\n', encoding="utf-8"
    )
    (reports / "README.md").write_text("# Report contract\n", encoding="utf-8")
    maintained_inputs = {
        ".agents/docs/architecture.md": "# Architecture v1\n",
        ".github/workflows/ci.yml": "name: fixture-v1\n",
        "docs/application_os_integration.md": "# Integration v1\n",
        "docs/plan/task.md": "# Progress v1\n",
        "evaluation/configs/deterministic_fixture.json": '{"top_k": 5}\n',
        "evaluation/datasets/retrieval.jsonl": '{"query_id": "q1"}\n',
        "tests/test_contract.py": "def test_fixture(): pass\n",
    }
    for relative_path, content in maintained_inputs.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture")
    object_inventory = _git(repo, "count-objects", "-v")

    clean_identifier, clean = _identifier(repo)
    assert clean["commit"] == _git(repo, "rev-parse", "HEAD")
    assert clean["head_tree"] == _git(repo, "rev-parse", "HEAD^{tree}")
    assert clean["worktree_tree"] == clean["head_tree"]
    assert clean["state"] == "clean"
    assert _identifier(repo)[0] == clean_identifier

    (reports / "retrieval_fixture_baseline.json").write_text(
        '{"generated": 2}\n', encoding="utf-8"
    )
    experiment = reports / "experiments" / "new" / "report.json"
    experiment.parent.mkdir(parents=True)
    experiment.write_text('{"generated": 3}\n', encoding="utf-8")
    assert _identifier(repo)[0] == clean_identifier

    (repo / "docs/plan/task.md").write_text("# Progress v2\n", encoding="utf-8")
    (repo / "docs/plan/reviewer-pass.md").write_text(
        "# Reviewer progress\n", encoding="utf-8"
    )
    assert _identifier(repo)[0] == clean_identifier

    (repo / "tracked.txt").write_text("tracked-v2\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("untracked-v1\n", encoding="utf-8")
    dirty_identifier, dirty = _identifier(repo)
    assert dirty["commit"] == clean["commit"]
    assert dirty["head_tree"] == clean["head_tree"]
    assert dirty["worktree_tree"] != dirty["head_tree"]
    assert dirty["state"] == "dirty"
    assert _identifier(repo)[0] == dirty_identifier

    experiment.write_text('{"generated": 4}\n', encoding="utf-8")
    assert _identifier(repo)[0] == dirty_identifier

    (repo / "untracked.txt").write_text("untracked-v2\n", encoding="utf-8")
    changed_identifier, changed = _identifier(repo)
    assert changed_identifier != dirty_identifier
    assert changed["worktree_tree"] != dirty["worktree_tree"]

    (reports / "ci_thresholds.json").write_text(
        '{"recall_at_5": {"min": 0.9}}\n', encoding="utf-8"
    )
    threshold_identifier, _ = _identifier(repo)
    assert threshold_identifier != changed_identifier

    previous_identifier = threshold_identifier
    for relative_path, content in {
        "evaluation/configs/deterministic_fixture.json": '{"top_k": 7}\n',
        "evaluation/datasets/retrieval.jsonl": '{"query_id": "q2"}\n',
        ".agents/docs/architecture.md": "# Architecture v2\n",
        "docs/application_os_integration.md": "# Integration v2\n",
        ".github/workflows/ci.yml": "name: fixture-v2\n",
        "tests/test_contract.py": "def test_fixture_v2(): pass\n",
    }.items():
        (repo / relative_path).write_text(content, encoding="utf-8")
        next_identifier, _ = _identifier(repo)
        assert next_identifier != previous_identifier
        previous_identifier = next_identifier
    assert _git(repo, "count-objects", "-v") == object_inventory
