import json
from datetime import datetime, timezone
from pathlib import Path
import re

import pytest


pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pr_ci_runs_public_offline_retrieval_eval_and_baseline_comparison():
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "Run deterministic career retrieval evaluation" in workflow
    assert "python -m evaluation.runner" in workflow
    assert "evaluation/datasets/retrieval_gold.example.jsonl" in workflow
    assert "artifacts/career-retrieval-evaluation" in workflow
    assert "python -m evaluation.compare" in workflow
    assert "evaluation/reports/retrieval_fixture_baseline.json" in workflow
    assert "evaluation/reports/ci_thresholds.json" in workflow
    assert "--validate-only" not in workflow
    assert "--validate-config-only" not in workflow
    assert workflow.count("--public-only") == 1
    assert "--current" in workflow
    assert 'eval_git_identifier="$(bash scripts/evaluation_git_identifier.sh)"' in workflow
    assert '--git-identifier "${eval_git_identifier}"' in workflow
    assert "retrieval_gold.local.jsonl" not in workflow
    assert "parsing" in workflow
    assert "--cov=parsing" in workflow


def test_pr_ci_uploads_raw_public_report_only_after_successful_comparison():
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    upload_step = workflow.split(
        "      - name: Upload public career retrieval eval artifacts\n",
        maxsplit=1,
    )[1].split("      - name:", maxsplit=1)[0]

    assert "success()" in upload_step
    assert "always()" not in upload_step
    assert "artifacts/career-retrieval-evaluation" in upload_step


def test_local_full_verification_runs_same_eval_and_comparison_gate():
    verify_script = (REPO_ROOT / "scripts" / "verify_all.sh").read_text(
        encoding="utf-8"
    )

    assert "python -m evaluation.runner" in verify_script
    assert "evaluation/datasets/retrieval_gold.example.jsonl" in verify_script
    assert "python -m evaluation.compare" in verify_script
    assert "evaluation/reports/retrieval_fixture_baseline.json" in verify_script
    assert "evaluation/reports/ci_thresholds.json" in verify_script
    assert "--validate-only" not in verify_script
    assert "--validate-config-only" not in verify_script
    assert "--current" in verify_script
    assert "--cov=parsing" in verify_script
    assert 'eval_git_identifier="$(bash "$REPO_ROOT/scripts/evaluation_git_identifier.sh")"' in verify_script
    assert '--git-identifier "${eval_git_identifier}"' in verify_script


def test_optional_workflow_is_manual_and_never_uploads_private_dataset_content():
    workflow_path = REPO_ROOT / ".github" / "workflows" / "retrieval-evaluation.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "workflow_dispatch" in workflow
    assert "retrieval_gold.local.jsonl" not in workflow
    assert "evaluation/datasets" not in _uploaded_artifact_paths(workflow)
    assert "echo $OPENAI_API_KEY" not in workflow
    assert "echo ${OPENAI_API_KEY}" not in workflow
    assert "sk-" not in workflow


def test_public_workflow_uses_only_the_reviewed_public_fixture_combination():
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "retrieval-evaluation.yml"
    ).read_text(encoding="utf-8")
    public_job = workflow.split("  evaluate-public-fixture:\n", maxsplit=1)[1]
    public_job = public_job.split("  require-local-confirmation:\n", maxsplit=1)[0]

    assert (
        "--dataset evaluation/datasets/retrieval_gold.example.jsonl" in public_job
    )
    assert (
        "--corpus evaluation/datasets/career_corpus.example.jsonl" in public_job
    )
    assert (
        "--configuration evaluation/configs/deterministic_fixture.json"
        in public_job
    )
    assert public_job.count("--public-only") == 1
    assert "${{" not in "\n".join(
        line
        for line in public_job.splitlines()
        if "--dataset" in line or "--corpus" in line or "--configuration" in line
    )


def test_optional_workflow_has_explicit_local_only_private_live_and_larger_modes():
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "retrieval-evaluation.yml"
    ).read_text(encoding="utf-8")
    wrapper = (REPO_ROOT / "evaluation" / "private_workflow.py").read_text(
        encoding="utf-8"
    )

    assert "type: choice" in workflow
    assert "public_fixture" in workflow
    assert "private_local" in workflow
    assert "live_provider" in workflow
    assert "larger_local" in workflow
    assert "confirm_local_only" in workflow
    assert (
        "runs-on: [self-hosted, retrieval-evaluation, ephemeral, isolated]" in workflow
    )
    assert "environment: retrieval-evaluation-private" in workflow
    assert "github.event.repository.default_branch" in workflow
    assert "github.ref == format('refs/heads/{0}'" in workflow
    assert "CONTEXTWIKI_PRIVATE_RETRIEVAL_DATASET" in wrapper
    assert "CONTEXTWIKI_LIVE_RETRIEVAL_DATASET" not in workflow
    assert "CONTEXTWIKI_LIVE_RETRIEVAL_CORPUS" not in workflow
    assert "CONTEXTWIKI_LIVE_RETRIEVAL_CONFIG" not in workflow
    assert "CONTEXTWIKI_LIVE_RETRIEVAL_COMMAND" not in workflow
    assert "/bin/bash -lc" not in workflow
    assert "python -m evaluation.private_workflow" in workflow
    assert workflow.count(
        'eval_git_identifier="$(bash scripts/evaluation_git_identifier.sh)"'
    ) == 2
    assert workflow.count('--git-identifier "${eval_git_identifier}"') == 2
    assert "set +x" in workflow
    assert "umask 077" in workflow
    assert 'run_dir="${RUNNER_TEMP}/contextwiki-private-retrieval-evaluation"' not in workflow
    assert "mktemp" not in workflow
    assert "stdout_log=" not in workflow
    assert "stderr_log=" not in workflow
    assert (
        "Non-public evaluation failed. Inspect runner-local restricted logs."
        in wrapper
    )
    assert 'echo "${dataset_path}"' not in workflow
    assert 'echo "${config_path}"' not in workflow
    assert "secrets." not in workflow
    assert "uses: actions/upload-artifact" not in workflow
    assert workflow.count('CONTEXTWIKI_DISABLE_DOTENV: "1"') == 2
    private_job = workflow.split("  evaluate-local-only:\n", maxsplit=1)[1]
    assert "ref: ${{ github.sha }}" in private_job
    assert "ref: ${{ github.event.repository.default_branch }}" not in private_job
    action_refs = re.findall(r"uses:\s+[^@\s]+@([^\s]+)", private_job)
    assert action_refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)
    assert (
        "uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in private_job
    )


def test_live_provider_workflow_fails_closed_until_adapter_is_implemented():
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "retrieval-evaluation.yml"
    ).read_text(encoding="utf-8")
    private_job = workflow.split("  evaluate-local-only:\n", maxsplit=1)[1]

    assert "python -m evaluation.private_workflow" in private_job
    assert '--mode "${EVALUATION_MODE}"' in private_job
    assert "CONTEXTWIKI_LIVE_RETRIEVAL_DATASET" not in private_job
    assert "CONTEXTWIKI_LIVE_RETRIEVAL_CORPUS" not in private_job
    assert "CONTEXTWIKI_LIVE_RETRIEVAL_CONFIG" not in private_job


def test_private_workflow_docs_require_approval_default_ref_and_ephemeral_runner():
    docs = (REPO_ROOT / "evaluation" / "datasets" / "README.md").read_text(
        encoding="utf-8"
    )

    assert "retrieval-evaluation-private" in docs
    assert "protected environment" in docs
    assert "default branch" in docs
    assert "immutable `github.sha`" in docs
    assert "mutable default-branch tip" in docs
    assert "ephemeral" in docs
    assert "isolated" in docs
    assert "CONTEXTWIKI_LIVE_RETRIEVAL_COMMAND" not in docs
    assert "CONTEXTWIKI_DISABLE_DOTENV=1" in docs
    assert "--provider-env-var NAME" in docs
    assert "unrelated secrets" in docs
    assert "full 40-character commit SHA" in docs
    assert "review the upstream action diff" in docs
    assert "live_provider" in docs
    assert "fails closed" in docs
    assert "no reviewed provider" in docs
    assert "adapter is implemented" in docs


def test_checked_report_docs_do_not_call_transient_runtime_latest():
    reports_readme = (REPO_ROOT / "evaluation/reports/README.md").read_text(
        encoding="utf-8"
    )
    integration_docs = (REPO_ROOT / "docs/application_os_integration.md").read_text(
        encoding="utf-8"
    )

    for content in (reports_readme, integration_docs):
        assert "checked historical" in content.lower()
        assert "Latest same-path" not in content
        assert "2.038208" not in content


def test_experiment_summary_marks_cross_path_latency_delta_not_comparable():
    summary = json.loads(
        (REPO_ROOT / "evaluation/reports/experiment_summary.json").read_text(
            encoding="utf-8"
        )
    )
    markdown = (REPO_ROOT / "evaluation/reports/experiment_summary.md").read_text(
        encoding="utf-8"
    )

    latency = summary["latency_analysis"]
    assert latency["cross_path_delta_ms"] is None
    assert latency["cross_path_delta_status"] == "n/a_non_comparable_paths"
    assert latency["proxy_path"] == "direct_offline_fixture_scorer"
    assert latency["selected_path"] == "context_and_evidence_services"
    comparison = json.loads(
        (
            REPO_ROOT / "evaluation/reports/retrieval_fixture_baseline_comparison.json"
        ).read_text(encoding="utf-8")
    )
    baseline = json.loads(
        (REPO_ROOT / "evaluation/reports/retrieval_fixture_baseline.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["timestamp"] == baseline["timestamp"]
    assert comparison["passed"] is True
    assert comparison["violations"] == []
    assert (
        latency["same_path_baseline_p95_latency_ms"]
        == comparison["deltas"]["p95_latency_ms"]["baseline"]
    )
    assert (
        latency["same_path_current_p95_latency_ms"]
        == comparison["deltas"]["p95_latency_ms"]["current"]
    )
    assert "Cross-path latency delta: `n/a`" in markdown
    assert "Same-path service comparison" in markdown
    assert "p95 increased" not in markdown


def test_checked_public_baseline_timestamp_is_not_in_the_future():
    baseline = json.loads(
        (REPO_ROOT / "evaluation/reports/retrieval_fixture_baseline.json").read_text(
            encoding="utf-8"
        )
    )

    timestamp = datetime.fromisoformat(baseline["timestamp"].replace("Z", "+00:00"))

    assert timestamp <= datetime.now(timezone.utc)
    assert baseline["configuration"] == json.loads(
        (REPO_ROOT / "evaluation/configs/deterministic_fixture.json").read_text(
            encoding="utf-8"
        )
    )
    assert re.fullmatch(
        r"commit=[0-9a-f]{40};head_tree=[0-9a-f]{40};"
        r"worktree_tree=[0-9a-f]{40};state=(clean|dirty)",
        baseline["git_identifier"],
    )


def test_all_checked_reports_and_summary_have_exact_matching_provenance():
    summary = json.loads(
        (REPO_ROOT / "evaluation/reports/experiment_summary.json").read_text(
            encoding="utf-8"
        )
    )
    identifier_pattern = re.compile(
        r"commit=[0-9a-f]{40};head_tree=[0-9a-f]{40};"
        r"worktree_tree=[0-9a-f]{40};state=(clean|dirty)"
    )
    baseline = json.loads(
        (REPO_ROOT / "evaluation/reports/retrieval_fixture_baseline.json").read_text(
            encoding="utf-8"
        )
    )
    assert identifier_pattern.fullmatch(summary["git_identifier"])
    assert summary["git_identifier"] == baseline["git_identifier"]
    assert summary["timestamp"] == baseline["timestamp"]

    proxy_identifier = summary["proxy_variant_git_identifier"]
    proxy_timestamp = summary["proxy_variant_reports_timestamp"]
    assert identifier_pattern.fullmatch(proxy_identifier)
    for experiment_name, metrics in summary["variants"].items():
        if experiment_name == "production_analog":
            report = baseline
        else:
            report = json.loads(
                (
                    REPO_ROOT
                    / "evaluation/reports/experiments"
                    / experiment_name
                    / "report.json"
                ).read_text(encoding="utf-8")
            )
            assert report["git_identifier"] == proxy_identifier
            assert report["timestamp"] == proxy_timestamp
        assert metrics["p95_latency_ms"] == report["metrics"]["p95_latency_ms"]
        assert metrics["failure_count"] == len(report["failures"])


def _uploaded_artifact_paths(workflow: str) -> str:
    uploaded_paths = []
    lines = workflow.splitlines()
    for index, line in enumerate(lines):
        if "uses: actions/upload-artifact" not in line:
            continue
        for candidate in lines[index + 1 : index + 12]:
            stripped = candidate.strip()
            if stripped.startswith("path:"):
                uploaded_paths.append(stripped)
            if candidate and not candidate.startswith(" "):
                break
    return "\n".join(uploaded_paths)
