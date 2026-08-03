from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import os
from pathlib import Path
import re
import signal
import shlex
import subprocess
import threading
import time
from typing import Any
from typing import BinaryIO

from evaluation.corpus import EvaluationInputError, load_corpus
from evaluation.secure_output import (
    SecureOutputError,
    require_private_dataset_destination,
    require_private_output_destination,
    secure_atomic_write_text,
)


PRIVATE_LABEL_SOURCE = "ai_generated_unreviewed"
REQUIRED_FIELDS = (
    "query_id",
    "query",
    "expected_chunk_id",
    "exact_quote",
    "label_pass_1",
    "label_pass_2",
    "proposed_final_label",
)
DATASET_METADATA_FIELDS = (
    "query_category",
    "expected_document_id",
    "source_type",
    "experience_type",
)
QUERY_CATEGORIES = frozenset(
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
PROVIDER_RUNTIME_ENV_VARS = (
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
)
PROVIDER_ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
PROVIDER_STDOUT_MAX_BYTES = 4 * 1024 * 1024
PROVIDER_STDERR_MAX_BYTES = 64 * 1024
PROVIDER_TIMEOUT_SECONDS = 600.0
PROVIDER_TERMINATION_GRACE_SECONDS = 0.5


class CandidateReviewError(ValueError):
    """Raised when local candidate-label review data is malformed or unsafe."""


Provider = Callable[[str, dict[str, Any]], Any]


def load_candidate_reviews(path: str | Path) -> list[dict[str, Any]]:
    input_path = Path(path)
    if not input_path.is_file():
        raise CandidateReviewError(
            f"candidate review input does not exist: {input_path}"
        )
    records: list[dict[str, Any]] = []
    seen_query_ids: set[str] = set()
    for line_number, line in enumerate(
        input_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CandidateReviewError(
                f"line {line_number}: invalid JSON: {exc.msg}"
            ) from exc
        if not isinstance(record, dict):
            raise CandidateReviewError(f"line {line_number}: record must be an object")
        normalized = _validate_record(record, line_number=line_number)
        query_id = normalized["query_id"]
        if query_id in seen_query_ids:
            raise CandidateReviewError(
                f"line {line_number}: duplicate query_id: {query_id}"
            )
        seen_query_ids.add(query_id)
        records.append(normalized)
    if not records:
        raise CandidateReviewError("candidate review input must not be empty")
    return records


def write_private_review_file(
    records: list[dict[str, Any]],
    output_path: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> Path:
    destination = Path(output_path)
    enforce_parent_mode = _require_private_destination(
        destination,
        repository_root=repository_root,
    )
    normalized = [
        _validate_record(record, line_number=index)
        for index, record in enumerate(records, start=1)
    ]
    _secure_atomic_private_write(
        destination,
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in normalized
        ),
        enforce_parent_mode=enforce_parent_mode,
    )
    return destination


def write_unreviewed_dataset(
    records: list[dict[str, Any]],
    output_path: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> Path:
    destination = Path(output_path)
    enforce_parent_mode = _require_local_dataset_destination(
        destination,
        repository_root=repository_root,
    )
    cases: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        normalized = _validate_record(record, line_number=index)
        for field in DATASET_METADATA_FIELDS:
            value = normalized.get(field)
            if not isinstance(value, str) or not value:
                raise CandidateReviewError(
                    f"line {index}: {field} is required for dataset output"
                )
        query_category = normalized["query_category"]
        if query_category not in QUERY_CATEGORIES:
            raise CandidateReviewError(
                f"line {index}: unsupported query_category: {query_category}"
            )
        proposed = normalized["proposed_final_label"].casefold()
        if proposed not in {"relevant", "not_relevant"}:
            raise CandidateReviewError(
                f"line {index}: proposed_final_label must be relevant or not_relevant"
            )
        is_relevant = proposed == "relevant"
        if not is_relevant and query_category != "no_answer":
            raise CandidateReviewError(
                f"line {index}: not_relevant output requires query_category=no_answer"
            )
        chunk_id = normalized["expected_chunk_id"]
        document_id = normalized["expected_document_id"]
        cases.append(
            {
                "query_id": normalized["query_id"],
                "query": normalized["query"],
                "query_category": query_category,
                "expected_chunk_ids": [chunk_id] if is_relevant else [],
                "expected_document_ids": [document_id] if is_relevant else [],
                "graded_relevance": {chunk_id: 3} if is_relevant else {},
                "allowed_source_types": [normalized["source_type"]],
                "allowed_experience_types": [normalized["experience_type"]],
                "should_return_empty": not is_relevant,
                "label_source": PRIVATE_LABEL_SOURCE,
                "notes": "AI candidate labels require human review.",
            }
        )
    _secure_atomic_private_write(
        destination,
        "".join(
            json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n"
            for case in cases
        ),
        enforce_parent_mode=enforce_parent_mode,
    )
    return destination


def generate_private_candidate_set(
    *,
    corpus_path: str | Path,
    provider: Provider,
    candidate_count: int,
    review_output: str | Path,
    dataset_output: str | Path,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Run explicit provider phases over one user-supplied private corpus."""
    if not 30 <= candidate_count <= 50:
        raise CandidateReviewError("candidate_count must be between 30 and 50")
    try:
        corpus = load_corpus(corpus_path)
    except EvaluationInputError as exc:
        raise CandidateReviewError(str(exc)) from exc
    generated = provider(
        "generate_candidates",
        {
            "candidate_count": candidate_count,
            "required_query_categories": sorted(QUERY_CATEGORIES),
            "corpus": corpus,
        },
    )
    candidates = _validate_candidates(generated, expected_count=candidate_count)
    _validate_candidate_grounding(candidates, corpus=corpus)
    pass_1 = _labels_by_query_id(
        provider("label_pass_1", {"candidates": candidates, "corpus": corpus}),
        candidates=candidates,
        phase="label_pass_1",
    )
    pass_2 = _labels_by_query_id(
        provider("label_pass_2", {"candidates": candidates, "corpus": corpus}),
        candidates=candidates,
        phase="label_pass_2",
    )
    disagreements = [
        candidate
        for candidate in candidates
        if pass_1[candidate["query_id"]] != pass_2[candidate["query_id"]]
    ]
    adjudicated = _adjudicated_labels(
        provider(
            "adjudicate",
            {
                "disagreements": disagreements,
                "label_pass_1": pass_1,
                "label_pass_2": pass_2,
                "corpus": corpus,
            },
        ),
        disagreements=disagreements,
    )

    review_records: list[dict[str, Any]] = []
    for candidate in candidates:
        query_id = candidate["query_id"]
        proposed = (
            adjudicated[query_id] if query_id in adjudicated else pass_1[query_id]
        )
        review_records.append(
            {
                **candidate,
                "label_pass_1": pass_1[query_id],
                "label_pass_2": pass_2[query_id],
                "proposed_final_label": proposed,
                "label_source": PRIVATE_LABEL_SOURCE,
            }
        )
    write_private_review_file(
        review_records,
        review_output,
        repository_root=repository_root,
    )
    write_unreviewed_dataset(
        review_records,
        dataset_output,
        repository_root=repository_root,
    )
    return {
        "status": "generated_local_only",
        "label_source": PRIVATE_LABEL_SOURCE,
        "candidate_count": len(review_records),
        "disagreement_count": len(disagreements),
        "query_categories": sorted(
            {record["query_category"] for record in review_records}
        ),
    }


def command_provider(
    command: str,
    *,
    provider_env_vars: tuple[str, ...] = (),
) -> Provider:
    try:
        arguments = shlex.split(command)
    except ValueError:
        raise CandidateReviewError("provider command is invalid") from None
    if not arguments:
        raise CandidateReviewError("provider command must not be empty")
    environment = _provider_environment(provider_env_vars)

    def invoke(phase: str, payload: dict[str, Any]) -> Any:
        return_code, stdout = _run_provider_adapter(
            [*arguments, phase],
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            environment,
            phase=phase,
        )
        if return_code != 0:
            raise CandidateReviewError(
                f"provider adapter failed during {phase} with exit code {return_code}"
            )
        try:
            return json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CandidateReviewError(
                f"provider adapter returned invalid JSON during {phase}"
            ) from exc

    return invoke


def _run_provider_adapter(
    arguments: list[str],
    input_bytes: bytes,
    environment: dict[str, str],
    *,
    phase: str,
) -> tuple[int, bytes]:
    try:
        process = subprocess.Popen(  # noqa: S603 - explicit reviewed adapter
            arguments,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            bufsize=0,
            start_new_session=os.name == "posix",
        )
    except (OSError, ValueError):
        raise CandidateReviewError("provider adapter could not be started") from None

    if process.stdin is None or process.stdout is None or process.stderr is None:
        _stop_provider_process(process)
        raise CandidateReviewError("provider adapter could not be started")

    stdout = bytearray()
    overflow = threading.Event()
    readers = (
        threading.Thread(
            target=_read_bounded_stream,
            args=(
                process.stdout,
                PROVIDER_STDOUT_MAX_BYTES,
                stdout,
                overflow,
            ),
            daemon=True,
        ),
        threading.Thread(
            target=_read_bounded_stream,
            args=(
                process.stderr,
                PROVIDER_STDERR_MAX_BYTES,
                None,
                overflow,
            ),
            daemon=True,
        ),
    )
    writer = threading.Thread(
        target=_write_provider_input,
        args=(process.stdin, input_bytes),
        daemon=True,
    )
    timed_out = False
    deadline = time.monotonic() + PROVIDER_TIMEOUT_SECONDS
    try:
        for thread in (*readers, writer):
            thread.start()
        while process.poll() is None:
            if overflow.is_set():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            overflow.wait(min(remaining, 0.01))
        if overflow.is_set() or timed_out:
            _stop_provider_process(process)
        else:
            process.wait()
        for thread in (*readers, writer):
            thread.join(PROVIDER_TERMINATION_GRACE_SECONDS)
    finally:
        if process.poll() is None:
            _stop_provider_process(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass

    if overflow.is_set():
        raise CandidateReviewError(
            f"provider adapter output exceeded byte limit during {phase}"
        )
    if timed_out:
        raise CandidateReviewError(f"provider adapter timed out during {phase}")
    return process.returncode, bytes(stdout)


def _read_bounded_stream(
    stream: BinaryIO,
    byte_limit: int,
    output: bytearray | None,
    overflow: threading.Event,
) -> None:
    total = 0
    try:
        while chunk := stream.read(64 * 1024):
            total += len(chunk)
            if total > byte_limit:
                overflow.set()
                return
            if output is not None:
                output.extend(chunk)
    except (OSError, ValueError):
        return


def _write_provider_input(stream: BinaryIO, input_bytes: bytes) -> None:
    try:
        stream.write(input_bytes)
        stream.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _stop_provider_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        process.wait()
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except OSError:
        pass
    try:
        process.wait(timeout=PROVIDER_TERMINATION_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except OSError:
        pass
    process.wait()


def _provider_environment(provider_env_vars: tuple[str, ...]) -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in PROVIDER_RUNTIME_ENV_VARS
        if name in os.environ
    }
    for name in provider_env_vars:
        if not PROVIDER_ENV_VAR_RE.fullmatch(name):
            raise CandidateReviewError(
                "provider environment variable names must be uppercase identifiers"
            )
        if name in os.environ:
            environment[name] = os.environ[name]
    environment["CONTEXTWIKI_DISABLE_DOTENV"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _validate_candidates(value: Any, *, expected_count: int) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != expected_count:
        raise CandidateReviewError(
            f"generate_candidates must return exactly {expected_count} candidates"
        )
    required = (
        "query_id",
        "query",
        "query_category",
        "expected_chunk_id",
        "expected_document_id",
        "source_type",
        "experience_type",
        "exact_quote",
    )
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise CandidateReviewError(f"candidate {index} must be an object")
        candidate: dict[str, str] = {}
        for field in required:
            field_value = item.get(field)
            if not isinstance(field_value, str) or not field_value.strip():
                raise CandidateReviewError(
                    f"candidate {index}: {field} must be a non-empty string"
                )
            candidate[field] = field_value.strip()
        if candidate["query_id"] in seen:
            raise CandidateReviewError(
                f"duplicate generated query_id: {candidate['query_id']}"
            )
        seen.add(candidate["query_id"])
        if candidate["query_category"] not in QUERY_CATEGORIES:
            raise CandidateReviewError(f"candidate {index}: unsupported query_category")
        candidates.append(candidate)
    categories = {candidate["query_category"] for candidate in candidates}
    missing = QUERY_CATEGORIES - categories
    if missing:
        raise CandidateReviewError(
            f"generated candidates missing query categories: {sorted(missing)}"
        )
    return candidates


def _validate_candidate_grounding(
    candidates: list[dict[str, str]], *, corpus: list[dict[str, Any]]
) -> None:
    chunks = {str(chunk["chunk_id"]): chunk for chunk in corpus}
    for index, candidate in enumerate(candidates, start=1):
        chunk = chunks.get(candidate["expected_chunk_id"])
        if chunk is None:
            raise CandidateReviewError(f"candidate {index}: unknown expected_chunk_id")
        for candidate_field, chunk_field in (
            ("expected_document_id", "document_id"),
            ("source_type", "source_type"),
            ("experience_type", "experience_type"),
        ):
            if candidate[candidate_field] != str(chunk.get(chunk_field, "")):
                raise CandidateReviewError(
                    f"candidate {index}: grounded {candidate_field} mismatch"
                )
        stored_quote = str(chunk.get("exact_quote", ""))
        stored_content = str(chunk.get("content", ""))
        if (
            candidate["exact_quote"] != stored_quote
            or not stored_quote
            or stored_quote not in stored_content
        ):
            raise CandidateReviewError(
                f"candidate {index}: exact_quote does not match stored chunk evidence"
            )


def _labels_by_query_id(
    value: Any, *, candidates: list[dict[str, str]], phase: str
) -> dict[str, str]:
    expected = {candidate["query_id"] for candidate in candidates}
    labels = _validate_label_records(value, phase=phase)
    if set(labels) != expected:
        raise CandidateReviewError(f"{phase} must label every candidate exactly once")
    return labels


def _adjudicated_labels(
    value: Any, *, disagreements: list[dict[str, str]]
) -> dict[str, str]:
    expected = {candidate["query_id"] for candidate in disagreements}
    labels = _validate_label_records(value, phase="adjudicate")
    if set(labels) != expected:
        raise CandidateReviewError(
            "adjudicate must label every disagreement exactly once"
        )
    return labels


def _validate_label_records(value: Any, *, phase: str) -> dict[str, str]:
    if not isinstance(value, list):
        raise CandidateReviewError(f"{phase} must return a list")
    labels: dict[str, str] = {}
    for item in value:
        if not isinstance(item, dict):
            raise CandidateReviewError(f"{phase} labels must be objects")
        query_id = item.get("query_id")
        label = item.get("label")
        if not isinstance(query_id, str) or not query_id.strip():
            raise CandidateReviewError(f"{phase} query_id must be non-empty")
        if label not in {"relevant", "not_relevant"}:
            raise CandidateReviewError(
                f"{phase} label must be relevant or not_relevant"
            )
        if query_id in labels:
            raise CandidateReviewError(f"{phase} duplicate query_id: {query_id}")
        labels[query_id] = label
    return labels


def _validate_record(record: dict[str, Any], *, line_number: int) -> dict[str, Any]:
    for field in REQUIRED_FIELDS:
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise CandidateReviewError(
                f"line {line_number}: {field} must be a non-empty string"
            )
    label_source = record.get("label_source", PRIVATE_LABEL_SOURCE)
    if label_source != PRIVATE_LABEL_SOURCE:
        raise CandidateReviewError(
            f"line {line_number}: label_source must remain {PRIVATE_LABEL_SOURCE}"
        )
    normalized: dict[str, Any] = {
        field: str(record[field]).strip() for field in REQUIRED_FIELDS
    }
    for field in DATASET_METADATA_FIELDS:
        value = record.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise CandidateReviewError(
                f"line {line_number}: {field} must be a non-empty string"
            )
        normalized[field] = value.strip()
    normalized["label_disagreement"] = (
        normalized["label_pass_1"] != normalized["label_pass_2"]
    )
    normalized["label_source"] = PRIVATE_LABEL_SOURCE
    return normalized


def _require_private_destination(
    destination: Path, *, repository_root: str | Path | None
) -> bool:
    try:
        return require_private_output_destination(
            destination,
            repository_root=repository_root,
        )
    except SecureOutputError as exc:
        if "untrusted Git repository" in str(exc):
            raise CandidateReviewError(str(exc)) from None
        raise CandidateReviewError(
            "private review output inside the repository must be under "
            "evaluation/reports/private or artifacts/private-evaluation"
        ) from None


def _require_local_dataset_destination(
    destination: Path, *, repository_root: str | Path | None
) -> bool:
    try:
        return require_private_dataset_destination(
            destination,
            repository_root=repository_root,
        )
    except SecureOutputError as exc:
        if "untrusted Git repository" in str(exc):
            raise CandidateReviewError(str(exc)) from None
        raise CandidateReviewError(
            "private dataset output inside the repository must be "
            "evaluation/datasets/retrieval_gold.local.jsonl"
        ) from None


def _secure_atomic_private_write(
    destination: Path,
    content: str,
    *,
    enforce_parent_mode: bool,
) -> None:
    try:
        secure_atomic_write_text(
            destination,
            content,
            enforce_parent_mode=enforce_parent_mode,
        )
    except SecureOutputError:
        raise CandidateReviewError(
            "private output could not be written securely"
        ) from None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate two-pass private candidate labels and write a local-only "
            "review file. This command does not call an AI provider."
        )
    )
    parser.add_argument("--input", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--dataset-output", default="")
    parser.add_argument("--private-corpus", default="")
    parser.add_argument("--provider-command", default="")
    parser.add_argument("--provider-env-var", action="append", default=[])
    parser.add_argument("--candidate-count", type=int, default=36)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    try:
        if args.private_corpus:
            if not args.provider_command or not args.dataset_output:
                raise CandidateReviewError(
                    "private generation requires --provider-command and "
                    "--dataset-output"
                )
            result = generate_private_candidate_set(
                corpus_path=args.private_corpus,
                provider=command_provider(
                    args.provider_command,
                    provider_env_vars=tuple(args.provider_env_var),
                ),
                candidate_count=args.candidate_count,
                review_output=args.output,
                dataset_output=args.dataset_output,
            )
            print(json.dumps(result, ensure_ascii=False))
            return
        if not args.input:
            raise CandidateReviewError(
                "--input is required unless --private-corpus is provided"
            )
        records = load_candidate_reviews(args.input)
        output = write_private_review_file(records, args.output)
        dataset_output = None
        if args.dataset_output:
            dataset_output = write_unreviewed_dataset(records, args.dataset_output)
    except CandidateReviewError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        json.dumps(
            {
                "status": "validated_local_only",
                "label_source": PRIVATE_LABEL_SOURCE,
                "record_count": len(records),
                "output_file": output.name,
                "dataset_output_file": (
                    dataset_output.name if dataset_output is not None else None
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
