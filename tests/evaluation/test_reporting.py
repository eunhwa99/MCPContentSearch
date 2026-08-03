import json
import stat

import pytest

import evaluation.secure_output as secure_output
from evaluation.reporting import (
    build_report,
    render_markdown_report,
    sanitize_report_for_ci,
    write_report_artifacts,
)


pytestmark = pytest.mark.integration

FIXTURE_DISCLAIMER = "TEST FIXTURE — NOT PRODUCT PERFORMANCE"
PRIVATE_DISCLAIMER = "AI-LABELED PRIVATE BENCHMARK — REQUIRES HUMAN REVIEW"


def _fixture_report() -> dict:
    report = build_report(
        dataset_name="retrieval_gold.example.jsonl",
        label_source="deterministic_fixture",
        dataset_size=13,
        configuration={
            "retrieval_mode": "hybrid",
            "candidate_multiplier": 4,
            "near_duplicate_threshold": 0.9,
        },
        metrics={
            "recall_at_5": 1.0,
            "mrr": 0.92,
            "p95_latency_ms": 120.0,
        },
        failures=[{"query_id": "q-008", "reason": "ambiguous ranking"}],
        git_identifier="9a2d39c+dirty",
        timestamp="2026-08-03T12:00:00+09:00",
    )
    report["input_digests"] = {
        "dataset_sha256": "1" * 64,
        "corpus_sha256": "2" * 64,
        "configuration_sha256": "3" * 64,
    }
    report["execution_path"] = {
        "identity": "context-evidence-offline-v1",
    }
    return report


def test_fixture_report_contains_required_provenance_and_disclaimer():
    report = _fixture_report()

    assert report["disclaimer"] == FIXTURE_DISCLAIMER
    assert report["dataset"] == {
        "name": "retrieval_gold.example.jsonl",
        "label_source": "deterministic_fixture",
        "size": 13,
    }
    assert report["configuration"]["retrieval_mode"] == "hybrid"
    assert report["git_identifier"] == "9a2d39c+dirty"
    assert report["input_digests"] == {
        "dataset_sha256": "1" * 64,
        "corpus_sha256": "2" * 64,
        "configuration_sha256": "3" * 64,
    }
    assert report["execution_path"]["identity"] == "context-evidence-offline-v1"
    assert report["timestamp"] == "2026-08-03T12:00:00+09:00"
    assert report["metrics"]["recall_at_5"] == 1.0
    assert report["failures"][0]["query_id"] == "q-008"


def test_markdown_report_renders_configuration_metrics_and_failure_evidence():
    markdown = render_markdown_report(_fixture_report())

    assert FIXTURE_DISCLAIMER in markdown
    assert "retrieval_gold.example.jsonl" in markdown
    assert "deterministic_fixture" in markdown
    assert "9a2d39c+dirty" in markdown
    assert "2026-08-03T12:00:00+09:00" in markdown
    assert "dataset_sha256" in markdown
    assert "context-evidence-offline-v1" in markdown
    assert "candidate_multiplier" in markdown
    assert "Recall@5" in markdown
    assert "q-008" in markdown


def test_report_writer_creates_machine_and_human_readable_artifacts(tmp_path):
    paths = write_report_artifacts(
        _fixture_report(), tmp_path, allow_public_output=True
    )

    assert paths["json"] == tmp_path / "report.json"
    assert paths["markdown"] == tmp_path / "report.md"
    assert json.loads(paths["json"].read_text(encoding="utf-8"))["disclaimer"] == (
        FIXTURE_DISCLAIMER
    )
    assert FIXTURE_DISCLAIMER in paths["markdown"].read_text(encoding="utf-8")


@pytest.mark.parametrize("missing_field", ["input_digests", "execution_path"])
def test_public_report_writer_rejects_legacy_report_without_workload_identity(
    tmp_path, missing_field
):
    report = _fixture_report()
    report.pop(missing_field)

    with pytest.raises(ValueError, match="public report requires workload identity"):
        write_report_artifacts(report, tmp_path, allow_public_output=True)

    assert not tmp_path.joinpath("report.json").exists()


def test_report_writer_requires_explicit_authorization_for_public_output(
    tmp_path,
):
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    public_output = repository_root / "artifacts/public-evaluation"

    with pytest.raises(
        ValueError,
        match="evaluation/reports/private or artifacts/private-evaluation",
    ):
        write_report_artifacts(
            _fixture_report(),
            public_output,
            repository_root=repository_root,
        )

    assert not public_output.exists()


def test_ai_generated_private_report_keeps_truthful_label_and_disclaimer():
    report = build_report(
        dataset_name="retrieval_gold.local.jsonl",
        label_source="ai_generated_unreviewed",
        dataset_size=40,
        configuration={"retrieval_mode": "hybrid"},
        metrics={"recall_at_5": 0.8},
        failures=[],
        git_identifier="9a2d39c",
        timestamp="2026-08-03T12:00:00+09:00",
    )

    assert report["dataset"]["label_source"] == "ai_generated_unreviewed"
    assert report["disclaimer"] == PRIVATE_DISCLAIMER


def test_private_report_writer_uses_restricted_atomic_outputs(tmp_path):
    report = build_report(
        dataset_name="retrieval_gold.local.jsonl",
        label_source="ai_generated_unreviewed",
        dataset_size=1,
        configuration={"retrieval_mode": "keyword"},
        metrics={},
        failures=[],
        git_identifier="test-tree",
        timestamp="2026-08-03T12:00:00+09:00",
    )

    paths = write_report_artifacts(report, tmp_path / "private/nested")

    assert stat.S_IMODE(paths["json"].stat().st_mode) == 0o600
    assert stat.S_IMODE(paths["markdown"].stat().st_mode) == 0o600
    assert stat.S_IMODE(paths["json"].parent.stat().st_mode) == 0o700


def test_private_report_writer_rejects_git_trackable_in_repo_output(
    tmp_path, monkeypatch
):
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    monkeypatch.chdir(repository_root)
    output_dir = repository_root / "docs/private-results"

    with pytest.raises(
        ValueError,
        match="evaluation/reports/private or artifacts/private-evaluation",
    ):
        write_report_artifacts(
            _private_report(), output_dir, repository_root=repository_root
        )

    assert not output_dir.exists()


def test_private_report_writer_rejects_repo_output_when_called_from_subdirectory(
    tmp_path, monkeypatch
):
    repository_root = tmp_path / "repository"
    (repository_root / ".git").mkdir(parents=True)
    working_directory = repository_root / "nested/working-directory"
    working_directory.mkdir(parents=True)
    monkeypatch.chdir(working_directory)
    output_dir = repository_root / "docs/private-results"

    with pytest.raises(
        ValueError,
        match="evaluation/reports/private or artifacts/private-evaluation",
    ):
        write_report_artifacts(
            _private_report(), output_dir, repository_root=repository_root
        )

    assert not output_dir.exists()


@pytest.mark.parametrize(
    "relative_output",
    [
        "evaluation/reports/private/measured",
        "artifacts/private-evaluation/measured",
    ],
)
def test_private_report_writer_accepts_reviewed_ignored_in_repo_outputs(
    tmp_path, monkeypatch, relative_output
):
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    monkeypatch.chdir(repository_root)

    paths = write_report_artifacts(
        _private_report(), repository_root / relative_output
    )

    assert stat.S_IMODE(paths["json"].stat().st_mode) == 0o600
    assert stat.S_IMODE(paths["json"].parent.stat().st_mode) == 0o700


def test_private_report_writer_accepts_owner_safe_external_output(
    tmp_path, monkeypatch
):
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    external_output = tmp_path / "external-private-output"
    external_output.mkdir(mode=0o700)
    external_output.chmod(0o700)
    monkeypatch.chdir(repository_root)

    paths = write_report_artifacts(_private_report(), external_output)

    assert stat.S_IMODE(paths["json"].stat().st_mode) == 0o600
    assert stat.S_IMODE(paths["json"].parent.stat().st_mode) == 0o700


def test_private_report_writer_uses_module_repository_root_outside_cwd(
    tmp_path, monkeypatch
):
    repository_root = tmp_path / "trusted-repository"
    (repository_root / "evaluation").mkdir(parents=True)
    (repository_root / ".git").mkdir()
    forbidden_output = repository_root / "docs/private-results"
    forbidden_output.mkdir(parents=True, mode=0o700)
    forbidden_output.chmod(0o700)
    outside_cwd = tmp_path / "outside-cwd"
    outside_cwd.mkdir(mode=0o700)
    outside_cwd.chmod(0o700)
    monkeypatch.chdir(outside_cwd)
    monkeypatch.setattr(
        secure_output,
        "__file__",
        str(repository_root / "evaluation/secure_output.py"),
    )

    with pytest.raises(
        ValueError,
        match="evaluation/reports/private or artifacts/private-evaluation",
    ):
        write_report_artifacts(_private_report(), forbidden_output)

    assert list(forbidden_output.iterdir()) == []


def test_private_report_writer_rejects_output_inside_another_git_repository(
    tmp_path,
):
    trusted_root = tmp_path / "trusted-repository"
    trusted_root.mkdir()
    other_repository = tmp_path / "external/nested-repository"
    (other_repository / ".git").mkdir(parents=True)
    output_dir = other_repository / "private-output"
    output_dir.mkdir(mode=0o700)
    output_dir.chmod(0o700)

    with pytest.raises(ValueError, match="untrusted Git repository"):
        write_report_artifacts(
            _private_report(),
            output_dir,
            repository_root=trusted_root,
        )

    assert list(output_dir.iterdir()) == []


def test_private_report_writer_rejects_symlink_output_without_overwrite(tmp_path):
    report = build_report(
        dataset_name="retrieval_gold.local.jsonl",
        label_source="human_reviewed",
        dataset_size=1,
        configuration={"retrieval_mode": "keyword"},
        metrics={},
        failures=[],
        git_identifier="test-tree",
        timestamp="2026-08-03T12:00:00+09:00",
    )
    output_dir = tmp_path / "private"
    output_dir.mkdir()
    outside = tmp_path / "outside-private.json"
    outside.write_text("old-private-data\n", encoding="utf-8")
    (output_dir / "report.json").symlink_to(outside)

    with pytest.raises(ValueError, match="securely") as exc_info:
        write_report_artifacts(report, output_dir)

    assert "outside-private" not in str(exc_info.value)
    assert outside.read_text(encoding="utf-8") == "old-private-data\n"


@pytest.mark.parametrize(
    ("label_source", "required_text", "forbidden_text"),
    [
        ("ai_generated_reviewed", "HUMAN REVIEWED", "REQUIRES HUMAN REVIEW"),
        ("human_reviewed", "HUMAN-REVIEWED", "AI-LABELED"),
    ],
)
def test_reviewed_private_reports_do_not_use_unreviewed_ai_disclaimer(
    label_source, required_text, forbidden_text
):
    report = build_report(
        dataset_name="retrieval_gold.local.jsonl",
        label_source=label_source,
        dataset_size=1,
        configuration={"retrieval_mode": "hybrid"},
        metrics={},
        failures=[],
        git_identifier="9a2d39c",
        timestamp="2026-08-03T12:00:00+09:00",
    )

    assert required_text in report["disclaimer"]
    assert forbidden_text not in report["disclaimer"]


def test_ci_sanitizer_removes_private_queries_quotes_results_and_paths():
    private_report = build_report(
        dataset_name="retrieval_gold.local.jsonl",
        label_source="ai_generated_unreviewed",
        dataset_size=1,
        configuration={"retrieval_mode": "hybrid"},
        metrics={"recall_at_5": 0.0},
        failures=[
            {
                "query_id": "private-q-1",
                "query": "private career question",
                "exact_quote": "private resume sentence",
                "source_path": "/private/resume.pdf",
                "returned_results": [{"exact_quote": "private result"}],
                "reason": "missing evidence",
            }
        ],
        git_identifier="9a2d39c",
        timestamp="2026-08-03T12:00:00+09:00",
    )

    sanitized = sanitize_report_for_ci(private_report)
    serialized = json.dumps(sanitized, ensure_ascii=False)

    assert sanitized["failures"] == [
        {"query_id": "private-q-1", "reason": "missing evidence"}
    ]
    assert "private career question" not in serialized
    assert "private resume sentence" not in serialized
    assert "/private/resume.pdf" not in serialized
    assert "private result" not in serialized


def _private_report() -> dict:
    return build_report(
        dataset_name="retrieval_gold.local.jsonl",
        label_source="ai_generated_unreviewed",
        dataset_size=1,
        configuration={"retrieval_mode": "keyword"},
        metrics={},
        failures=[],
        git_identifier="test-tree",
        timestamp="2026-08-03T12:00:00+09:00",
    )
