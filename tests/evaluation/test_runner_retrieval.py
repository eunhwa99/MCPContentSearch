import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from search.context_service import ContextSearchService
from search.evidence_service import EvidenceSearchService
from evaluation.runner import (
    PublicFixtureBoundaryError,
    _load_reviewed_public_fixture_inputs,
    _require_reviewed_public_fixture_inputs,
    load_corpus,
    load_dataset,
    retrieve_evidence,
    run_evaluation,
)
from evaluation.retrieval import _FixtureMetadataStore
from evaluation.retrieval import (
    EvaluationInputError,
    _evaluation_failures,
    validate_configuration,
)


pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = REPO_ROOT / "evaluation/datasets/retrieval_gold.example.jsonl"
CORPUS_PATH = REPO_ROOT / "evaluation/datasets/career_corpus.example.jsonl"
PRECISE_GIT_IDENTIFIER = (
    f"commit={'1' * 40};head_tree={'2' * 40};"
    f"worktree_tree={'3' * 40};state=dirty"
)


def _chunk(chunk_id: str, text: str, **metadata) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": metadata.pop("document_id", f"doc-{chunk_id}"),
        "source_type": metadata.pop("source_type", "project"),
        "experience_type": metadata.pop("experience_type", "personal_project"),
        "section_title": metadata.pop("section_title", "Projects"),
        "parent_section_title": metadata.pop("parent_section_title", "Evidence"),
        "exact_quote": text,
        "content": text,
        **metadata,
    }


def test_fixture_metadata_store_supports_production_batch_snapshot_adapter():
    store = _FixtureMetadataStore(
        [
            _chunk("first", "First stored quote."),
            _chunk("second", "Second stored quote."),
        ]
    )

    snapshots = store.get_active_evidence_snapshots(
        ["second", "missing", "first", "second"]
    )

    assert list(snapshots) == ["second", "first"]
    second_chunk, second_document = snapshots["second"]
    assert second_chunk.chunk_id == "second"
    assert second_document.document_id == "doc-second"


def test_failure_analysis_flags_missing_expected_documents_even_when_chunk_matches():
    failures = _evaluation_failures(
        [
            {
                "query_id": "document-mismatch",
                "query": "find target document",
                "expected_chunk_ids": ["chunk-target"],
                "expected_document_ids": ["doc-target"],
                "should_return_empty": False,
                "allowed_source_types": [],
                "allowed_experience_types": [],
            }
        ],
        {
            "document-mismatch": [
                {
                    "chunk_id": "chunk-target",
                    "document_id": "doc-wrong",
                    "source_type": "resume",
                    "experience_type": "professional",
                }
            ]
        },
    )

    assert failures[0]["reason"] == "expected_document_not_in_top_5"
    assert failures[0]["missing_chunk_ids"] == []
    assert failures[0]["missing_document_ids"] == ["doc-target"]
    assert "doc-target" in failures[0]["expected_behavior"]


@pytest.mark.parametrize(
    "configuration",
    [
        {"retrieval_mode": "keyword", "private_token": "do-not-echo"},
        {"retrieval_mode": "keyword", "rrf_k": 60},
        {"retrieval_mode": "keyword", "query_normalization": "false"},
        {"retrieval_mode": "private-do-not-echo"},
    ],
)
def test_configuration_validation_rejects_unknown_mode_specific_and_malformed_fields(
    configuration,
):
    with pytest.raises(EvaluationInputError) as exc_info:
        validate_configuration(configuration)

    rendered = str(exc_info.value)
    assert "do-not-echo" not in rendered
    assert "private_token" not in rendered


def test_all_checked_evaluation_configurations_match_strict_allowlist():
    for path in sorted((REPO_ROOT / "evaluation/configs").glob("*.json")):
        validate_configuration(json.loads(path.read_text(encoding="utf-8")))


def test_query_normalization_changes_ranking_without_provider_calls():
    corpus = [
        _chunk("target", "A personal project flags unhealthy message consumers."),
        _chunk("distractor", "A side project detects formatting errors."),
    ]
    baseline = retrieve_evidence(
        "side project detects unhealthy consumers",
        corpus,
        {"retrieval_mode": "keyword", "top_k": 2},
    )
    normalized = retrieve_evidence(
        "side project detects unhealthy consumers",
        corpus,
        {
            "retrieval_mode": "keyword",
            "query_normalization": True,
            "top_k": 2,
        },
    )

    assert baseline[0]["chunk_id"] == "distractor"
    assert normalized[0]["chunk_id"] == "target"
    assert normalized[0]["retrieval_score"] > normalized[1]["retrieval_score"]


def test_filters_and_exact_plus_near_dedup_are_applied_before_top_k():
    corpus = [
        _chunk(
            "professional",
            "Led a staged database migration with rollback verification.",
            source_type="resume",
            experience_type="professional",
        ),
        _chunk(
            "professional-copy",
            "Led a staged database migration with rollback verification.",
            source_type="resume",
            experience_type="professional",
        ),
        _chunk(
            "professional-near-copy",
            "Led staged database migration with rollback verification checks.",
            source_type="resume",
            experience_type="professional",
        ),
        _chunk(
            "prototype",
            "Built a prototype database migration tool.",
            source_type="project",
            experience_type="prototype",
        ),
    ]

    results = retrieve_evidence(
        "database migration rollback",
        corpus,
        {
            "retrieval_mode": "keyword",
            "metadata_filtering": True,
            "exact_duplicate_removal": True,
            "near_duplicate_removal": True,
            "near_duplicate_threshold": 0.65,
            "top_k": 5,
        },
        allowed_source_types=("resume",),
        allowed_experience_types=("professional",),
    )

    assert [result["chunk_id"] for result in results] == ["professional"]


def test_hybrid_rrf_is_deterministic_and_uses_candidate_count():
    corpus = load_corpus(CORPUS_PATH)
    configuration = {
        "retrieval_mode": "hybrid_rrf",
        "query_normalization": True,
        "candidate_multiplier": 3,
        "rrf_k": 60,
        "top_k": 5,
    }

    first = retrieve_evidence(
        "side project that detects unhealthy message consumers",
        corpus,
        configuration,
    )
    second = retrieve_evidence(
        "side project that detects unhealthy message consumers",
        corpus,
        configuration,
    )

    assert [result["chunk_id"] for result in first] == [
        result["chunk_id"] for result in second
    ]
    assert first[0]["chunk_id"] == "github-readme-kafka"
    assert all(result["retrieval_score"] >= 0 for result in first)


def test_offline_runner_returns_measured_report_with_actual_failure_inputs():
    cases = load_dataset(DATASET_PATH)
    corpus = load_corpus(CORPUS_PATH)
    report = run_evaluation(
        cases=cases,
        corpus=corpus,
        dataset_name=DATASET_PATH.name,
        configuration={
            "name": "selected-test",
            "retrieval_mode": "production_analog",
            "query_normalization": True,
            "metadata_filtering": True,
            "exact_duplicate_removal": True,
            "near_duplicate_removal": True,
            "candidate_multiplier": 3,
            "top_k": 5,
        },
        git_identifier="test-tree",
        timestamp="2026-08-03T00:00:00+00:00",
    )

    assert report["status"] == "measured"
    assert report["dataset"]["size"] == len(cases)
    assert report["metrics"]["metric_denominators"]["recall_at_5"] == 12
    assert report["metrics"]["citation_validity_rate"] == 1.0
    assert report["resource_cost"] == {
        "external_api_calls": 0,
        "estimated_cost_usd": 0.0,
    }
    assert report["ingestion_metrics"]["parsing_success_rate"] is None
    assert report["ingestion_metrics"]["metric_denominators"][
        "parsing_success_rate"
    ] == 0
    assert report["limitations"]
    assert all("query_id" in failure and "reason" in failure for failure in report["failures"])


def test_selected_evaluation_executes_real_context_and_evidence_services(monkeypatch):
    calls = {"context": 0, "evidence": 0}
    no_answer_matching_scores = []
    original_context = ContextSearchService.search_context
    original_evidence = EvidenceSearchService.search_evidence

    async def tracking_context(self, *args, **kwargs):
        calls["context"] += 1
        assert kwargs["top_k"] == 15
        assert kwargs["candidate_budget"] == 15
        response = await original_context(self, *args, **kwargs)
        if args and args[0] == "COBOL mainframe production deployment":
            for result in response["results"]:
                payload = result if isinstance(result, dict) else result.model_dump()
                if payload["chunk_id"].startswith("career-note-"):
                    no_answer_matching_scores.append(payload["score"])
        return response

    async def tracking_evidence(self, *args, **kwargs):
        calls["evidence"] += 1
        return await original_evidence(self, *args, **kwargs)

    monkeypatch.setattr(ContextSearchService, "search_context", tracking_context)
    monkeypatch.setattr(EvidenceSearchService, "search_evidence", tracking_evidence)

    report = run_evaluation(
        cases=load_dataset(DATASET_PATH),
        corpus=load_corpus(CORPUS_PATH),
        dataset_name=DATASET_PATH.name,
        configuration=json.loads(
            (REPO_ROOT / "evaluation/configs/deterministic_fixture.json").read_text(
                encoding="utf-8"
            )
        ),
        git_identifier="test-tree",
        timestamp="2026-08-03T00:00:00+00:00",
    )

    assert calls["evidence"] == 13
    assert calls["context"] >= calls["evidence"]
    assert report["execution_path"] == {
        "identity": (
            "context-search-service+evidence-search-service+"
            "deterministic-offline-candidate-provider:v1"
        ),
        "candidate_budget_per_query": 15,
        "candidate_multiplier": 3,
        "context_service": "ContextSearchService",
        "evidence_service": "EvidenceSearchService",
        "provider": "deterministic_offline_candidate_provider",
    }
    assert report["metrics"]["recall_at_5"] == 1.0
    assert report["metrics"]["empty_result_accuracy"] == 1.0
    assert report["failures"] == []
    assert no_answer_matching_scores
    assert max(no_answer_matching_scores) < 0.2


def test_production_analog_prefilters_taxonomy_before_candidate_cap():
    query = "bounded filter target"
    wrong = [
        _chunk(
            f"wrong-{index}",
            query,
            source_type="project",
            experience_type="personal_project",
        )
        for index in range(3)
    ]
    target = _chunk(
        "target",
        "bounded target evidence",
        document_id="target-doc",
        source_type="resume",
        experience_type="professional",
    )
    report = run_evaluation(
        cases=[
            {
                "query_id": "bounded-prefilter",
                "query": query,
                "query_category": "professional_only",
                "expected_chunk_ids": ["target"],
                "expected_document_ids": ["target-doc"],
                "graded_relevance": {"target": 3},
                "allowed_source_types": ["resume"],
                "allowed_experience_types": ["professional"],
                "should_return_empty": False,
                "label_source": "deterministic_fixture",
                "notes": "Synthetic pre-cap filter regression.",
            }
        ],
        corpus=[*wrong, target],
        dataset_name="bounded-prefilter.jsonl",
        configuration={
            "name": "bounded-prefilter",
            "retrieval_mode": "production_analog",
            "service_execution": True,
            "query_normalization": True,
            "metadata_filtering": True,
            "exact_duplicate_removal": True,
            "near_duplicate_removal": True,
            "candidate_multiplier": 3,
            "top_k": 1,
        },
        git_identifier="test-tree",
        timestamp="2026-08-03T00:00:00+00:00",
    )

    assert report["metrics"]["recall_at_5"] == 1.0
    assert report["metrics"]["source_type_filter_accuracy"] == 1.0
    assert report["metrics"]["experience_type_filter_accuracy"] == 1.0
    assert report["failures"] == []


def test_runner_cli_executes_and_writes_json_and_markdown(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.runner",
            "--dataset",
            str(DATASET_PATH),
            "--corpus",
            str(CORPUS_PATH),
            "--configuration",
            str(REPO_ROOT / "evaluation/configs/deterministic_fixture.json"),
            "--output-dir",
            str(tmp_path),
            "--git-identifier",
            PRECISE_GIT_IDENTIFIER,
            "--timestamp",
            "2026-08-03T00:00:00+00:00",
            "--public-only",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "measured"
    assert report["git_identifier"] == PRECISE_GIT_IDENTIFIER
    assert report["input_digests"] == {
        "dataset_sha256": hashlib.sha256(DATASET_PATH.read_bytes()).hexdigest(),
        "corpus_sha256": hashlib.sha256(CORPUS_PATH.read_bytes()).hexdigest(),
        "configuration_sha256": hashlib.sha256(
            (
                REPO_ROOT / "evaluation/configs/deterministic_fixture.json"
            ).read_bytes()
        ).hexdigest(),
    }
    assert report["execution_path"]["identity"] == (
        "context-search-service+evidence-search-service+"
        "deterministic-offline-candidate-provider:v1"
    )
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "TEST FIXTURE" in markdown
    assert "## Ingestion metrics" in markdown
    assert "## Execution path" in markdown
    assert "ContextSearchService" in markdown
    assert "EvidenceSearchService" in markdown
    assert "No ingestion run" in markdown
    assert "## Limitations" in markdown


@pytest.mark.parametrize(
    "configuration_name",
    [
        "baseline_keyword.json",
        "candidate_tuning.json",
        "exact_dedup.json",
        "hybrid_rrf.json",
        "metadata_filters.json",
        "near_dedup.json",
        "query_normalization.json",
    ],
)
def test_public_only_cli_accepts_reviewed_checked_in_variant_configurations(
    tmp_path,
    configuration_name,
):
    output_dir = tmp_path / configuration_name.removesuffix(".json")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.runner",
            "--dataset",
            str(DATASET_PATH),
            "--corpus",
            str(CORPUS_PATH),
            "--configuration",
            str(REPO_ROOT / "evaluation/configs" / configuration_name),
            "--output-dir",
            str(output_dir),
            "--git-identifier",
            PRECISE_GIT_IDENTIFIER,
            "--public-only",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    assert report["configuration"] == json.loads(
        (REPO_ROOT / "evaluation/configs" / configuration_name).read_text(
            encoding="utf-8"
        )
    )


def test_public_fixture_boundary_rejects_symlinked_reviewed_input(tmp_path):
    repository_root = tmp_path / "repository"
    datasets = repository_root / "evaluation/datasets"
    configs = repository_root / "evaluation/configs"
    datasets.mkdir(parents=True)
    configs.mkdir(parents=True)
    dataset = datasets / "retrieval_gold.example.jsonl"
    corpus = datasets / "career_corpus.example.jsonl"
    configuration = configs / "deterministic_fixture.json"
    dataset.write_text("{}\n", encoding="utf-8")
    private_corpus = tmp_path / "private-corpus.jsonl"
    private_corpus.write_text("{}\n", encoding="utf-8")
    corpus.symlink_to(private_corpus)
    configuration.write_text("{}\n", encoding="utf-8")

    with pytest.raises(
        PublicFixtureBoundaryError,
        match="reviewed public fixture dataset, corpus, and configuration",
    ):
        _require_reviewed_public_fixture_inputs(
            dataset,
            corpus,
            configuration,
            repository_root=repository_root,
        )


@pytest.mark.parametrize("substituted_input", ["dataset", "corpus", "configuration"])
def test_public_fixture_boundary_rejects_in_place_content_substitution(
    tmp_path,
    substituted_input,
):
    repository_root = tmp_path / "repository"
    datasets = repository_root / "evaluation/datasets"
    configs = repository_root / "evaluation/configs"
    datasets.mkdir(parents=True)
    configs.mkdir(parents=True)
    dataset = datasets / "retrieval_gold.example.jsonl"
    corpus = datasets / "career_corpus.example.jsonl"
    configuration = configs / "deterministic_fixture.json"
    inputs = {
        "dataset": dataset,
        "corpus": corpus,
        "configuration": configuration,
    }
    reviewed_contents = {
        "dataset": DATASET_PATH.read_bytes(),
        "corpus": CORPUS_PATH.read_bytes(),
        "configuration": (
            REPO_ROOT / "evaluation/configs/deterministic_fixture.json"
        ).read_bytes(),
    }
    for name, path in inputs.items():
        path.write_bytes(reviewed_contents[name])
    manifest = {
        "version": 1,
        "dataset": {
            "path": "evaluation/datasets/retrieval_gold.example.jsonl",
            "sha256": hashlib.sha256(reviewed_contents["dataset"]).hexdigest(),
        },
        "corpus": {
            "path": "evaluation/datasets/career_corpus.example.jsonl",
            "sha256": hashlib.sha256(reviewed_contents["corpus"]).hexdigest(),
        },
        "configurations": {
            f"evaluation/configs/{path.name}": hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in (REPO_ROOT / "evaluation/configs").glob("*.json")
        },
    }
    (repository_root / "evaluation/public_fixture_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    cases, loaded_corpus, loaded_configuration, _ = (
        _load_reviewed_public_fixture_inputs(
            dataset,
            corpus,
            configuration,
            repository_root=repository_root,
        )
    )
    assert cases
    assert loaded_corpus
    assert loaded_configuration
    inputs[substituted_input].write_text(
        "PRIVATE SUBSTITUTED CONTENT MUST NOT REACH PUBLIC OUTPUT\n",
        encoding="utf-8",
    )

    with pytest.raises(
        PublicFixtureBoundaryError,
        match="reviewed public fixture dataset, corpus, and configuration",
    ):
        _load_reviewed_public_fixture_inputs(
            dataset,
            corpus,
            configuration,
            repository_root=repository_root,
        )


def test_public_only_cli_rejects_renamed_deterministic_private_inputs(tmp_path):
    private_quote = "Private compensation evidence must never reach public output."
    raw_case = json.loads(DATASET_PATH.read_text(encoding="utf-8").splitlines()[0])
    raw_case.update(
        {
            "query_id": "private-q",
            "query": "private compensation evidence",
            "expected_chunk_ids": ["expected-other"],
            "expected_document_ids": ["expected-other-doc"],
            "graded_relevance": {"expected-other": 3},
            "allowed_source_types": [],
            "allowed_experience_types": [],
            "label_source": "deterministic_fixture",
        }
    )
    dataset = tmp_path / "renamed-deterministic-dataset.jsonl"
    dataset.write_text(json.dumps(raw_case) + "\n", encoding="utf-8")
    corpus = tmp_path / "renamed-private-corpus.jsonl"
    corpus.write_text(
        json.dumps(
            {
                "chunk_id": "private-chunk",
                "document_id": "private-doc",
                "source_type": "resume",
                "experience_type": "professional",
                "exact_quote": private_quote,
                "content": private_quote,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    configuration = tmp_path / "renamed-config.json"
    configuration.write_text(
        (REPO_ROOT / "evaluation/configs/deterministic_fixture.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "public-output"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.runner",
            "--dataset",
            str(dataset),
            "--corpus",
            str(corpus),
            "--configuration",
            str(configuration),
            "--output-dir",
            str(output_dir),
            "--git-identifier",
            PRECISE_GIT_IDENTIFIER,
            "--public-only",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "reviewed public fixture dataset, corpus, and configuration" in (
        completed.stderr
    )
    assert private_quote not in completed.stdout
    assert private_quote not in completed.stderr
    assert not (output_dir / "report.json").exists()


@pytest.mark.parametrize("substituted_input", ["dataset", "corpus", "configuration"])
def test_public_only_cli_rejects_each_substituted_fixture_input(
    tmp_path,
    substituted_input,
):
    inputs = {
        "dataset": DATASET_PATH,
        "corpus": CORPUS_PATH,
        "configuration": (
            REPO_ROOT / "evaluation/configs/deterministic_fixture.json"
        ),
    }
    replacement = tmp_path / f"private-{substituted_input}"
    replacement.write_text(
        inputs[substituted_input].read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    inputs[substituted_input] = replacement
    output_dir = tmp_path / "public-output"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.runner",
            "--dataset",
            str(inputs["dataset"]),
            "--corpus",
            str(inputs["corpus"]),
            "--configuration",
            str(inputs["configuration"]),
            "--output-dir",
            str(output_dir),
            "--git-identifier",
            PRECISE_GIT_IDENTIFIER,
            "--public-only",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "reviewed public fixture dataset, corpus, and configuration" in (
        completed.stderr
    )
    assert not output_dir.exists()


@pytest.mark.parametrize(
    "identifier_args",
    [
        [],
        ["--git-identifier", "test-tree"],
        [
            "--git-identifier",
            f"commit={'1' * 40};head_tree={'2' * 40};"
            f"worktree_tree={'2' * 40};state=dirty",
        ],
    ],
)
def test_runner_cli_rejects_missing_or_imprecise_git_identifier(
    tmp_path, identifier_args
):
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.runner",
            "--dataset",
            str(DATASET_PATH),
            "--corpus",
            str(CORPUS_PATH),
            "--configuration",
            str(REPO_ROOT / "evaluation/configs/deterministic_fixture.json"),
            "--output-dir",
            str(tmp_path),
            *identifier_args,
            "--public-only",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "precise commit/head_tree/worktree_tree/state format" in completed.stderr
    assert not (tmp_path / "report.json").exists()


def test_private_validate_only_rejects_git_trackable_in_repo_output(tmp_path):
    repository_root, dataset = _private_validation_fixture(tmp_path)
    (repository_root / ".git").mkdir()
    output_dir = repository_root / "docs/private-results"

    completed = _run_private_validation(repository_root, dataset, output_dir)

    assert completed.returncode != 0
    assert "untrusted Git repository" in completed.stderr
    assert not output_dir.exists()


@pytest.mark.parametrize(
    "relative_output",
    [
        "evaluation/reports/private/validation",
        "artifacts/private-evaluation/validation",
    ],
)
def test_private_validate_only_rejects_allowlisted_path_in_untrusted_git_repo(
    tmp_path, relative_output
):
    repository_root, dataset = _private_validation_fixture(tmp_path)
    (repository_root / ".git").mkdir()
    output_dir = repository_root / relative_output

    completed = _run_private_validation(repository_root, dataset, output_dir)

    assert completed.returncode != 0
    assert "untrusted Git repository" in completed.stderr
    assert not output_dir.exists()


def test_private_validate_only_accepts_owner_safe_external_output(tmp_path):
    repository_root, dataset = _private_validation_fixture(tmp_path)
    output_dir = tmp_path / "external-private-validation"
    output_dir.mkdir(mode=0o700)
    output_dir.chmod(0o700)

    completed = _run_private_validation(repository_root, dataset, output_dir)

    assert completed.returncode == 0, completed.stderr
    assert stat.S_IMODE((output_dir / "validation.json").stat().st_mode) == 0o600


def _private_validation_fixture(tmp_path):
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    raw_case = json.loads(DATASET_PATH.read_text(encoding="utf-8").splitlines()[0])
    raw_case["label_source"] = "ai_generated_unreviewed"
    dataset = repository_root / "retrieval_gold.local.jsonl"
    dataset.write_text(json.dumps(raw_case) + "\n", encoding="utf-8")
    return repository_root, dataset


def _run_private_validation(repository_root, dataset, output_dir):
    python_path = os.pathsep.join(
        value
        for value in (str(REPO_ROOT), os.environ.get("PYTHONPATH", ""))
        if value
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.runner",
            "--dataset",
            str(dataset),
            "--output-dir",
            str(output_dir),
            "--validate-only",
        ],
        cwd=repository_root,
        env={**os.environ, "PYTHONPATH": python_path},
        capture_output=True,
        text=True,
        check=False,
    )
