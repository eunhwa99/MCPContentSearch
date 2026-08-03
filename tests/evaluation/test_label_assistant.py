import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import time

import pytest

import evaluation.secure_output as secure_output
import evaluation.label_assistant as label_assistant
from evaluation.label_assistant import (
    CandidateReviewError,
    command_provider,
    generate_private_candidate_set,
    load_candidate_reviews,
    write_private_review_file,
    write_unreviewed_dataset,
)


pytestmark = pytest.mark.unit


def _record() -> dict:
    return {
        "query_id": "private-q-1",
        "query": "local-only question",
        "expected_chunk_id": "private-chunk-1",
        "exact_quote": "local-only exact quote",
        "label_pass_1": "relevant",
        "label_pass_2": "not_relevant",
        "proposed_final_label": "relevant",
        "label_source": "ai_generated_unreviewed",
        "query_category": "technology",
        "expected_document_id": "private-document-1",
        "source_type": "resume",
        "experience_type": "professional",
    }


def test_candidate_review_preserves_unreviewed_provenance_and_disagreement(tmp_path):
    input_path = tmp_path / "candidate.jsonl"
    input_path.write_text(json.dumps(_record()) + "\n", encoding="utf-8")

    records = load_candidate_reviews(input_path)

    assert records[0]["label_source"] == "ai_generated_unreviewed"
    assert records[0]["label_disagreement"] is True


def test_candidate_review_rejects_false_human_review_claim(tmp_path):
    record = _record()
    record["label_source"] = "human_reviewed"
    input_path = tmp_path / "candidate.jsonl"
    input_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(CandidateReviewError, match="ai_generated_unreviewed"):
        load_candidate_reviews(input_path)


def test_private_review_writer_rejects_tracked_public_report_path(tmp_path):
    with pytest.raises(CandidateReviewError, match="private review output"):
        write_private_review_file(
            [_record()],
            tmp_path / "evaluation" / "reports" / "candidate-review.jsonl",
            repository_root=tmp_path,
        )

    private_path = write_private_review_file(
        [_record()],
        tmp_path / "evaluation" / "reports" / "private" / "review.jsonl",
        repository_root=tmp_path,
    )
    assert private_path.is_file()
    assert json.loads(private_path.read_text(encoding="utf-8"))["label_source"] == (
        "ai_generated_unreviewed"
    )


def test_dataset_writer_outputs_only_unreviewed_private_gold_schema(tmp_path):
    output_path = tmp_path / "evaluation" / "datasets" / "retrieval_gold.local.jsonl"
    written = write_unreviewed_dataset(
        [_record()], output_path, repository_root=tmp_path
    )

    case = json.loads(written.read_text(encoding="utf-8"))
    assert case == {
        "query_id": "private-q-1",
        "query": "local-only question",
        "query_category": "technology",
        "expected_chunk_ids": ["private-chunk-1"],
        "expected_document_ids": ["private-document-1"],
        "graded_relevance": {"private-chunk-1": 3},
        "allowed_source_types": ["resume"],
        "allowed_experience_types": ["professional"],
        "should_return_empty": False,
        "label_source": "ai_generated_unreviewed",
        "notes": "AI candidate labels require human review.",
    }


def test_dataset_writer_refuses_a_public_or_human_labeled_destination(tmp_path):
    with pytest.raises(CandidateReviewError, match="retrieval_gold.local.jsonl"):
        write_unreviewed_dataset(
            [_record()],
            tmp_path / "evaluation" / "datasets" / "retrieval_gold.example.jsonl",
            repository_root=tmp_path,
        )


@pytest.mark.parametrize(
    "writer,relative_path",
    [
        (
            write_private_review_file,
            "evaluation/reports/private/nested/review.jsonl",
        ),
        (
            write_unreviewed_dataset,
            "evaluation/datasets/retrieval_gold.local.jsonl",
        ),
    ],
)
def test_private_writers_create_restricted_files_and_new_parents(
    tmp_path, writer, relative_path
):
    destination = tmp_path / relative_path

    written = writer([_record()], destination, repository_root=tmp_path)

    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    "writer,relative_path",
    [
        (
            write_private_review_file,
            "evaluation/reports/private/review.jsonl",
        ),
        (
            write_unreviewed_dataset,
            "evaluation/datasets/retrieval_gold.local.jsonl",
        ),
    ],
)
def test_private_writers_restrict_existing_final_parent(
    tmp_path, writer, relative_path
):
    destination = tmp_path / relative_path
    destination.parent.mkdir(parents=True, mode=0o755)
    destination.parent.chmod(0o755)

    written = writer([_record()], destination, repository_root=tmp_path)

    assert stat.S_IMODE(destination.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(written.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "writer,filename",
    [
        (write_private_review_file, "review.jsonl"),
        (write_unreviewed_dataset, "retrieval_gold.local.jsonl"),
    ],
)
def test_private_writers_do_not_restrict_existing_external_parent(
    tmp_path, writer, filename
):
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    external_parent = tmp_path / "external-output"
    external_parent.mkdir(mode=0o755)
    external_parent.chmod(0o755)
    destination = external_parent / filename

    with pytest.raises(CandidateReviewError, match="securely"):
        writer([_record()], destination, repository_root=repository_root)

    assert stat.S_IMODE(external_parent.stat().st_mode) == 0o755
    assert not destination.exists()


@pytest.mark.parametrize(
    "writer,relative_path",
    [
        (
            write_private_review_file,
            "docs/private-results/review.jsonl",
        ),
        (
            write_unreviewed_dataset,
            "docs/private-results/retrieval_gold.local.jsonl",
        ),
    ],
)
def test_private_writers_use_module_repository_root_outside_cwd(
    tmp_path, monkeypatch, writer, relative_path
):
    repository_root = tmp_path / "trusted-repository"
    (repository_root / "evaluation").mkdir(parents=True)
    (repository_root / ".git").mkdir()
    destination = repository_root / relative_path
    destination.parent.mkdir(parents=True, mode=0o700)
    destination.parent.chmod(0o700)
    outside_cwd = tmp_path / "outside-cwd"
    outside_cwd.mkdir(mode=0o700)
    outside_cwd.chmod(0o700)
    monkeypatch.chdir(outside_cwd)
    monkeypatch.setattr(
        secure_output,
        "__file__",
        str(repository_root / "evaluation/secure_output.py"),
    )

    with pytest.raises(CandidateReviewError, match="inside the repository"):
        writer([_record()], destination)

    assert not destination.exists()


@pytest.mark.parametrize(
    "writer,filename",
    [
        (write_private_review_file, "review.jsonl"),
        (write_unreviewed_dataset, "retrieval_gold.local.jsonl"),
    ],
)
def test_private_writers_reject_output_inside_another_git_repository(
    tmp_path, writer, filename
):
    trusted_root = tmp_path / "trusted-repository"
    trusted_root.mkdir()
    other_repository = tmp_path / "external/nested-repository"
    (other_repository / ".git").mkdir(parents=True)
    output_dir = other_repository / "private-output"
    output_dir.mkdir(mode=0o700)
    output_dir.chmod(0o700)
    destination = output_dir / filename

    with pytest.raises(CandidateReviewError, match="untrusted Git repository"):
        writer([_record()], destination, repository_root=trusted_root)

    assert not destination.exists()


@pytest.mark.parametrize(
    "in_repository",
    [True, False],
    ids=["enforce-parent-mode", "external-parent"],
)
def test_private_writer_rejects_foreign_owned_0700_parent(
    tmp_path, monkeypatch, in_repository
):
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    if in_repository:
        parent = repository_root / "evaluation/reports/private"
    else:
        parent = tmp_path / "external-output"
    parent.mkdir(parents=True, mode=0o700)
    parent.chmod(0o700)
    destination = parent / "review.jsonl"

    real_fstat = secure_output.os.fstat

    class ForeignOwnedMetadata:
        def __init__(self, file_descriptor):
            metadata = real_fstat(file_descriptor)
            self.st_mode = metadata.st_mode
            self.st_uid = secure_output.os.geteuid() + 1

    monkeypatch.setattr(
        secure_output.os,
        "fstat",
        lambda file_descriptor: ForeignOwnedMetadata(file_descriptor),
    )

    with pytest.raises(CandidateReviewError, match="securely"):
        write_private_review_file(
            [_record()],
            destination,
            repository_root=repository_root,
        )

    assert not destination.exists()


def test_secure_output_never_restricts_filesystem_root(monkeypatch):
    class RootMetadata:
        st_mode = stat.S_IFDIR | 0o755
        st_uid = 0

    chmod_calls = []
    monkeypatch.setattr(secure_output.os, "fstat", lambda _: RootMetadata())
    monkeypatch.setattr(secure_output.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        secure_output.os,
        "fchmod",
        lambda file_descriptor, mode: chmod_calls.append((file_descriptor, mode)),
    )

    with pytest.raises(secure_output.SecureOutputError):
        secure_output.secure_atomic_write_text(
            Path("/") / ".contextwiki-private-output-must-not-be-created",
            "test-only content",
            enforce_parent_mode=True,
        )

    assert chmod_calls == []


def test_command_provider_passes_only_runtime_and_explicit_environment(
    tmp_path,
    monkeypatch,
):
    adapter = tmp_path / "provider adapter.py"
    adapter.write_text(
        """\
import json
import os

print(json.dumps({
    "dotenv_disabled": os.getenv("CONTEXTWIKI_DISABLE_DOTENV"),
    "explicit": os.getenv("CONTEXTWIKI_TEST_PROVIDER_TOKEN"),
    "home_present": bool(os.getenv("HOME")),
    "path_present": bool(os.getenv("PATH")),
    "unrelated": os.getenv("UNRELATED_SECRET_SENTINEL"),
}))
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONTEXTWIKI_TEST_PROVIDER_TOKEN", "explicit-test-token")
    monkeypatch.setenv("UNRELATED_SECRET_SENTINEL", "must-not-reach-child")

    provider = command_provider(
        f"{shlex.quote(sys.executable)} {shlex.quote(str(adapter))}",
        provider_env_vars=("CONTEXTWIKI_TEST_PROVIDER_TOKEN",),
    )

    environment = provider("inspect_environment", {})

    assert environment == {
        "dotenv_disabled": "1",
        "explicit": "explicit-test-token",
        "home_present": bool(os.getenv("HOME")),
        "path_present": bool(os.getenv("PATH")),
        "unrelated": None,
    }


def test_private_label_import_and_generation_never_load_repository_dotenv(
    tmp_path,
):
    fake_module_dir = tmp_path / "fake-modules"
    fake_module_dir.mkdir()
    marker = tmp_path / "dotenv-loaded"
    (fake_module_dir / "dotenv.py").write_text(
        """\
import os
from pathlib import Path

def load_dotenv(*args, **kwargs):
    Path(os.environ["FAKE_DOTENV_MARKER"]).write_text("loaded", encoding="utf-8")
    os.environ["UNRELATED_DOTENV_SECRET"] = "must-not-load"
    return True

def dotenv_values(*args, **kwargs):
    return {}

def find_dotenv(*args, **kwargs):
    return ""
""",
        encoding="utf-8",
    )
    script = """\
import json
import os
from evaluation.label_assistant import CandidateReviewError, generate_private_candidate_set

after_import = os.getenv("UNRELATED_DOTENV_SECRET")
try:
    generate_private_candidate_set(
        corpus_path="missing-private-corpus.jsonl",
        provider=lambda phase, payload: [],
        candidate_count=30,
        review_output="unused-review.jsonl",
        dataset_output="unused-dataset.jsonl",
    )
except CandidateReviewError:
    pass
print(json.dumps({
    "after_import": after_import,
    "after_generation": os.getenv("UNRELATED_DOTENV_SECRET"),
}))
"""
    child_environment = {
        name: os.environ[name]
        for name in ("HOME", "LANG", "LC_ALL", "LC_CTYPE", "PATH", "TMPDIR")
        if name in os.environ
    }
    child_environment.update(
        {
            "FAKE_DOTENV_MARKER": str(marker),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": os.pathsep.join(
                (str(fake_module_dir), str(Path(__file__).resolve().parents[2]))
            ),
        }
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=child_environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "after_import": None,
        "after_generation": None,
    }
    assert not marker.exists()


@pytest.mark.parametrize("overflow_stream", ["stdout", "stderr"])
def test_command_provider_stops_process_on_bounded_output_overflow(
    tmp_path,
    monkeypatch,
    overflow_stream,
):
    adapter = _write_long_running_adapter(tmp_path)
    pid_path = tmp_path / f"{overflow_stream}.pid"
    monkeypatch.setattr(label_assistant, "PROVIDER_STDOUT_MAX_BYTES", 64)
    monkeypatch.setattr(label_assistant, "PROVIDER_STDERR_MAX_BYTES", 64)
    provider = command_provider(
        " ".join(
            (
                shlex.quote(sys.executable),
                shlex.quote(str(adapter)),
                shlex.quote(str(pid_path)),
                overflow_stream,
            )
        )
    )

    with pytest.raises(CandidateReviewError) as exc_info:
        provider("label_pass_1", {})

    rendered = str(exc_info.value)
    assert "byte limit" in rendered
    assert str(adapter) not in rendered
    assert "overflow-secret-sentinel" not in rendered
    _assert_process_gone(int(pid_path.read_text(encoding="utf-8")))


def test_command_provider_kills_timed_out_process_without_output_leak(
    tmp_path,
    monkeypatch,
):
    adapter = _write_long_running_adapter(tmp_path)
    pid_path = tmp_path / "timeout.pid"
    launched_process_ids = []
    original_popen = label_assistant.subprocess.Popen

    def tracking_popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        launched_process_ids.append(process.pid)
        return process

    monkeypatch.setattr(label_assistant.subprocess, "Popen", tracking_popen)
    monkeypatch.setattr(label_assistant, "PROVIDER_TIMEOUT_SECONDS", 0.05)
    provider = command_provider(
        " ".join(
            (
                shlex.quote(sys.executable),
                shlex.quote(str(adapter)),
                shlex.quote(str(pid_path)),
                "timeout",
            )
        )
    )

    with pytest.raises(CandidateReviewError) as exc_info:
        provider("label_pass_2", {})

    rendered = str(exc_info.value)
    assert "timed out" in rendered
    assert str(adapter) not in rendered
    assert "overflow-secret-sentinel" not in rendered
    assert len(launched_process_ids) == 1
    _assert_process_gone(launched_process_ids[0])


def test_command_provider_sanitizes_launch_failure(tmp_path):
    missing_adapter = tmp_path / "private-secret-adapter-path"
    provider = command_provider(str(missing_adapter))

    with pytest.raises(CandidateReviewError) as exc_info:
        provider("generate_candidates", {})

    rendered = str(exc_info.value)
    assert rendered == "provider adapter could not be started"
    assert str(missing_adapter) not in rendered


def _write_long_running_adapter(tmp_path: Path) -> Path:
    adapter = tmp_path / "bounded-provider-adapter.py"
    adapter.write_text(
        """\
import os
from pathlib import Path
import sys
import time

pid_path = Path(sys.argv[1])
mode = sys.argv[2]
pid_path.write_text(str(os.getpid()), encoding="utf-8")
if mode == "stdout":
    os.write(sys.stdout.fileno(), b"overflow-secret-sentinel" * 100)
elif mode == "stderr":
    os.write(sys.stderr.fileno(), b"overflow-secret-sentinel" * 100)
time.sleep(0.4)
""",
        encoding="utf-8",
    )
    return adapter


def _assert_process_gone(process_id: int) -> None:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.kill(process_id, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    pytest.fail("provider adapter process was not reaped")


@pytest.mark.parametrize(
    "writer,relative_path",
    [
        (
            write_private_review_file,
            "evaluation/reports/private/review.jsonl",
        ),
        (
            write_unreviewed_dataset,
            "evaluation/datasets/retrieval_gold.local.jsonl",
        ),
    ],
)
def test_private_writers_reject_symlink_target_without_leaking_path_or_content(
    tmp_path, writer, relative_path
):
    destination = tmp_path / relative_path
    destination.parent.mkdir(parents=True)
    outside = tmp_path / "private-secret-target.jsonl"
    outside.write_text("do-not-overwrite\n", encoding="utf-8")
    destination.symlink_to(outside)

    with pytest.raises(CandidateReviewError) as exc_info:
        writer([_record()], destination, repository_root=tmp_path)

    rendered = str(exc_info.value)
    assert "private-secret-target" not in rendered
    assert _record()["exact_quote"] not in rendered
    assert outside.read_text(encoding="utf-8") == "do-not-overwrite\n"


@pytest.mark.parametrize(
    "writer,relative_path",
    [
        (
            write_private_review_file,
            "evaluation/reports/private/review.jsonl",
        ),
        (
            write_unreviewed_dataset,
            "evaluation/datasets/retrieval_gold.local.jsonl",
        ),
    ],
)
def test_private_writers_reject_symlink_parent_and_resolved_escape(
    tmp_path, writer, relative_path
):
    outside = tmp_path / "outside-private"
    outside.mkdir()
    link = tmp_path / "evaluation"
    link.symlink_to(outside, target_is_directory=True)
    destination = tmp_path / relative_path

    with pytest.raises(CandidateReviewError, match="private output"):
        writer([_record()], destination, repository_root=tmp_path)

    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    "writer,relative_path",
    [
        (
            write_private_review_file,
            "evaluation/reports/private/review.jsonl",
        ),
        (
            write_unreviewed_dataset,
            "evaluation/datasets/retrieval_gold.local.jsonl",
        ),
    ],
)
def test_private_writers_reject_intermediate_ancestor_swap(
    tmp_path, monkeypatch, writer, relative_path
):
    destination = tmp_path / relative_path
    destination.parent.mkdir(parents=True)
    destination.parent.chmod(0o700)
    outside = tmp_path / "outside-private"
    outside.mkdir(mode=0o700)
    moved_evaluation = tmp_path / "evaluation-before-swap"
    original_precheck = secure_output._reject_symlink_components

    def swap_ancestor_after_precheck(path):
        original_precheck(path)
        (tmp_path / "evaluation").rename(moved_evaluation)
        (tmp_path / "evaluation").symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(
        secure_output,
        "_reject_symlink_components",
        swap_ancestor_after_precheck,
    )

    with pytest.raises(CandidateReviewError, match="securely"):
        writer([_record()], destination, repository_root=tmp_path)

    assert list(outside.iterdir()) == []
    assert not any(moved_evaluation.rglob("*.jsonl"))


def test_private_writer_keeps_existing_file_and_cleans_temp_on_atomic_replace_failure(
    tmp_path, monkeypatch
):
    destination = tmp_path / "evaluation/reports/private/review.jsonl"
    destination.parent.mkdir(parents=True)
    destination.write_text("old-private-output\n", encoding="utf-8")

    def fail_replace(*args, **kwargs):
        raise OSError("private filesystem path must not leak")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(CandidateReviewError) as exc_info:
        write_private_review_file([_record()], destination, repository_root=tmp_path)

    assert "private filesystem path" not in str(exc_info.value)
    assert destination.read_text(encoding="utf-8") == "old-private-output\n"
    assert [path.name for path in destination.parent.iterdir()] == [destination.name]


def test_private_generator_runs_two_label_passes_and_adjudication_offline(tmp_path):
    corpus_path = tmp_path / "private-corpus.jsonl"
    corpus_path.write_text(
        json.dumps(
            {
                "chunk_id": "private-chunk-1",
                "document_id": "private-document-1",
                "source_type": "resume",
                "experience_type": "professional",
                "exact_quote": "Private source sentence.",
                "content": "Private source sentence.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    categories = [
        "exact_keyword",
        "semantic_paraphrase",
        "technology",
        "scale_or_metric",
        "professional_only",
        "personal_project_only",
        "section_specific",
        "ambiguous",
        "no_answer",
    ]
    phases = []

    def provider(phase, payload):
        phases.append(phase)
        if phase == "generate_candidates":
            return [
                {
                    "query_id": f"private-q-{index:02d}",
                    "query": f"private generated query {index}",
                    "query_category": categories[index % len(categories)],
                    "expected_chunk_id": "private-chunk-1",
                    "expected_document_id": "private-document-1",
                    "source_type": "resume",
                    "experience_type": "professional",
                    "exact_quote": "Private source sentence.",
                }
                for index in range(payload["candidate_count"])
            ]
        if phase == "label_pass_1":
            return [
                {
                    "query_id": item["query_id"],
                    "label": (
                        "not_relevant"
                        if item["query_category"] == "no_answer"
                        else "relevant"
                    ),
                }
                for item in payload["candidates"]
            ]
        if phase == "label_pass_2":
            return [
                {
                    "query_id": item["query_id"],
                    "label": (
                        "not_relevant"
                        if index == 0 or item["query_category"] == "no_answer"
                        else "relevant"
                    ),
                }
                for index, item in enumerate(payload["candidates"])
            ]
        assert phase == "adjudicate"
        return [
            {"query_id": item["query_id"], "label": "relevant"}
            for item in payload["disagreements"]
        ]

    review_path = tmp_path / "evaluation/reports/private/review.jsonl"
    dataset_path = tmp_path / "evaluation/datasets/retrieval_gold.local.jsonl"
    result = generate_private_candidate_set(
        corpus_path=corpus_path,
        provider=provider,
        candidate_count=36,
        review_output=review_path,
        dataset_output=dataset_path,
        repository_root=tmp_path,
    )

    assert phases == [
        "generate_candidates",
        "label_pass_1",
        "label_pass_2",
        "adjudicate",
    ]
    assert result["candidate_count"] == 36
    assert result["disagreement_count"] == 1
    assert review_path.is_file()
    assert dataset_path.is_file()
    assert all(
        json.loads(line)["label_source"] == "ai_generated_unreviewed"
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
    )


def test_private_generator_enforces_30_to_50_candidates(tmp_path):
    with pytest.raises(CandidateReviewError, match="between 30 and 50"):
        generate_private_candidate_set(
            corpus_path=tmp_path / "explicit.jsonl",
            provider=lambda phase, payload: [],
            candidate_count=29,
            review_output=tmp_path / "review.jsonl",
            dataset_output=tmp_path / "dataset.jsonl",
            repository_root=tmp_path,
        )


def test_private_generator_rejects_candidates_not_grounded_in_explicit_corpus(
    tmp_path,
):
    private_sentence = "Never reveal this private sentence."
    corpus_path = tmp_path / "private-corpus.jsonl"
    corpus_path.write_text(
        json.dumps(
            {
                "chunk_id": "real-private-chunk",
                "document_id": "real-private-document",
                "source_type": "resume",
                "experience_type": "professional",
                "exact_quote": private_sentence,
                "content": private_sentence,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    categories = sorted(
        {
            "exact_keyword",
            "semantic_paraphrase",
            "technology",
            "scale_or_metric",
            "professional_only",
            "personal_project_only",
            "section_specific",
            "ambiguous",
            "no_answer",
        }
    )

    def provider(phase, payload):
        assert phase == "generate_candidates"
        return [
            {
                "query_id": f"private-q-{index:02d}",
                "query": "private query",
                "query_category": categories[index % len(categories)],
                "expected_chunk_id": "hallucinated-chunk",
                "expected_document_id": "real-private-document",
                "source_type": "resume",
                "experience_type": "professional",
                "exact_quote": private_sentence,
            }
            for index in range(payload["candidate_count"])
        ]

    with pytest.raises(CandidateReviewError) as exc_info:
        generate_private_candidate_set(
            corpus_path=corpus_path,
            provider=provider,
            candidate_count=36,
            review_output=tmp_path / "evaluation/reports/private/review.jsonl",
            dataset_output=(
                tmp_path / "evaluation/datasets/retrieval_gold.local.jsonl"
            ),
            repository_root=tmp_path,
        )

    assert "unknown expected_chunk_id" in str(exc_info.value)
    assert private_sentence not in str(exc_info.value)
    assert str(corpus_path) not in str(exc_info.value)
    (generate_private_candidate_set,)
