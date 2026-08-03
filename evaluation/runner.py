from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from evaluation.corpus import load_corpus_text
from evaluation.reporting import write_report_artifacts
from evaluation.secure_output import (
    require_private_output_destination,
    secure_atomic_write_text,
)
from evaluation.retrieval import (
    EvaluationInputError,
    load_corpus,
    retrieve_evidence as retrieve_evidence,
    run_evaluation,
    validate_configuration,
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
SOURCE_TYPES = frozenset(
    {
        "resume",
        "previous_resume",
        "project",
        "github_readme",
        "behavioral_story",
        "career_note",
        "skills_inventory",
    }
)
EXPERIENCE_TYPES = frozenset(
    {
        "professional",
        "academic",
        "personal_project",
        "prototype",
        "unknown",
    }
)
LABEL_SOURCES = frozenset(
    {
        "deterministic_fixture",
        "ai_generated_unreviewed",
        "ai_generated_reviewed",
        "human_reviewed",
    }
)
FIXTURE_DISCLAIMER = "TEST FIXTURE — NOT PRODUCT PERFORMANCE"
PRECISE_GIT_IDENTIFIER_PATTERN = re.compile(
    r"commit=(?P<commit>[0-9a-f]{40});"
    r"head_tree=(?P<head_tree>[0-9a-f]{40});"
    r"worktree_tree=(?P<worktree_tree>[0-9a-f]{40});"
    r"state=(?P<state>clean|dirty)\Z"
)
PRECISE_GIT_IDENTIFIER_ERROR = (
    "measured execution requires --git-identifier in precise "
    "commit/head_tree/worktree_tree/state format"
)
PUBLIC_FIXTURE_BOUNDARY_ERROR = (
    "public-only mode requires the reviewed public fixture dataset, corpus, "
    "and configuration without symlinked path components"
)
PUBLIC_FIXTURE_DATASET = Path(
    "evaluation/datasets/retrieval_gold.example.jsonl"
)
PUBLIC_FIXTURE_CORPUS = Path(
    "evaluation/datasets/career_corpus.example.jsonl"
)
PUBLIC_FIXTURE_CONFIGURATIONS = frozenset(
    Path("evaluation/configs") / name
    for name in (
        "baseline_keyword.json",
        "candidate_tuning.json",
        "deterministic_fixture.json",
        "exact_dedup.json",
        "hybrid_rrf.json",
        "metadata_filters.json",
        "near_dedup.json",
        "query_normalization.json",
    )
)
PUBLIC_FIXTURE_MANIFEST = Path("evaluation/public_fixture_manifest.json")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class DatasetValidationError(ValueError):
    """Raised when a retrieval JSONL dataset violates its public contract."""


class PublicFixtureBoundaryError(ValueError):
    """Raised when public-only execution is not the reviewed fixture triplet."""


@dataclass(frozen=True)
class RetrievalEvaluationCase:
    query_id: str
    query: str
    query_category: str
    expected_chunk_ids: tuple[str, ...]
    expected_document_ids: tuple[str, ...]
    graded_relevance: dict[str, int]
    allowed_source_types: tuple[str, ...]
    allowed_experience_types: tuple[str, ...]
    should_return_empty: bool
    label_source: str
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "query": self.query,
            "query_category": self.query_category,
            "expected_chunk_ids": list(self.expected_chunk_ids),
            "expected_document_ids": list(self.expected_document_ids),
            "graded_relevance": dict(self.graded_relevance),
            "allowed_source_types": list(self.allowed_source_types),
            "allowed_experience_types": list(self.allowed_experience_types),
            "should_return_empty": self.should_return_empty,
            "label_source": self.label_source,
            "notes": self.notes,
        }


def load_dataset(path: str | Path) -> list[RetrievalEvaluationCase]:
    dataset_path = Path(path)
    if not dataset_path.is_file():
        raise DatasetValidationError(f"dataset does not exist: {dataset_path}")
    return load_dataset_text(dataset_path.read_text(encoding="utf-8"))


def load_dataset_text(content: str) -> list[RetrievalEvaluationCase]:
    """Validate dataset content already captured from a stable input snapshot."""
    cases: list[RetrievalEvaluationCase] = []
    seen_query_ids: set[str] = set()
    for line_number, raw_line in enumerate(
        content.splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            raw_case = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise DatasetValidationError(
                f"line {line_number}: invalid JSON: {exc.msg}"
            ) from exc
        if not isinstance(raw_case, dict):
            raise DatasetValidationError(f"line {line_number}: case must be an object")
        case = _validate_case(raw_case, line_number=line_number)
        if case.query_id in seen_query_ids:
            raise DatasetValidationError(
                f"line {line_number}: duplicate query_id: {case.query_id}"
            )
        seen_query_ids.add(case.query_id)
        cases.append(case)

    if not cases:
        raise DatasetValidationError("dataset must contain at least one case")
    return cases


def _validate_case(
    raw_case: dict[str, Any], *, line_number: int
) -> RetrievalEvaluationCase:
    prefix = f"line {line_number}"
    query_id = _required_string(raw_case, "query_id", prefix)
    query = _required_string(raw_case, "query", prefix)
    query_category = _required_string(raw_case, "query_category", prefix)
    if query_category not in QUERY_CATEGORIES:
        raise DatasetValidationError(
            f"{prefix}: unsupported query_category: {query_category}"
        )

    label_source = _required_string(raw_case, "label_source", prefix)
    if label_source not in LABEL_SOURCES:
        raise DatasetValidationError(
            f"{prefix}: unsupported label_source: {label_source}"
        )

    expected_chunk_ids = _string_list(raw_case, "expected_chunk_ids", prefix)
    expected_document_ids = _string_list(
        raw_case, "expected_document_ids", prefix
    )
    allowed_source_types = _string_list(
        raw_case, "allowed_source_types", prefix
    )
    unsupported_sources = set(allowed_source_types) - SOURCE_TYPES
    if unsupported_sources:
        raise DatasetValidationError(
            f"{prefix}: unsupported source_type: {sorted(unsupported_sources)[0]}"
        )

    allowed_experience_types = _string_list(
        raw_case, "allowed_experience_types", prefix
    )
    unsupported_experience = set(allowed_experience_types) - EXPERIENCE_TYPES
    if unsupported_experience:
        raise DatasetValidationError(
            f"{prefix}: unsupported experience_type: "
            f"{sorted(unsupported_experience)[0]}"
        )

    graded_relevance = _graded_relevance(raw_case, prefix)
    should_return_empty = raw_case.get("should_return_empty")
    if not isinstance(should_return_empty, bool):
        raise DatasetValidationError(
            f"{prefix}: should_return_empty must be a boolean"
        )

    if should_return_empty:
        if expected_chunk_ids or expected_document_ids or graded_relevance:
            raise DatasetValidationError(
                f"{prefix}: should_return_empty cases cannot declare expected evidence"
            )
        if query_category != "no_answer":
            raise DatasetValidationError(
                f"{prefix}: should_return_empty requires query_category=no_answer"
            )
    elif not expected_chunk_ids and not expected_document_ids:
        raise DatasetValidationError(
            f"{prefix}: non-empty cases require expected chunk or document ids"
        )

    notes = raw_case.get("notes", "")
    if not isinstance(notes, str):
        raise DatasetValidationError(f"{prefix}: notes must be a string")

    return RetrievalEvaluationCase(
        query_id=query_id,
        query=query,
        query_category=query_category,
        expected_chunk_ids=expected_chunk_ids,
        expected_document_ids=expected_document_ids,
        graded_relevance=graded_relevance,
        allowed_source_types=allowed_source_types,
        allowed_experience_types=allowed_experience_types,
        should_return_empty=should_return_empty,
        label_source=label_source,
        notes=notes,
    )


def _required_string(raw_case: dict[str, Any], key: str, prefix: str) -> str:
    value = raw_case.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DatasetValidationError(f"{prefix}: {key} must be a non-empty string")
    return value.strip()


def _string_list(
    raw_case: dict[str, Any], key: str, prefix: str
) -> tuple[str, ...]:
    value = raw_case.get(key)
    if not isinstance(value, list):
        raise DatasetValidationError(f"{prefix}: {key} must be a list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise DatasetValidationError(
            f"{prefix}: {key} entries must be non-empty strings"
        )
    normalized = tuple(item.strip() for item in value)
    if len(set(normalized)) != len(normalized):
        raise DatasetValidationError(f"{prefix}: {key} contains duplicates")
    return normalized


def _graded_relevance(raw_case: dict[str, Any], prefix: str) -> dict[str, int]:
    value = raw_case.get("graded_relevance")
    if not isinstance(value, dict):
        raise DatasetValidationError(f"{prefix}: graded_relevance must be an object")
    normalized: dict[str, int] = {}
    for chunk_id, relevance in value.items():
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            raise DatasetValidationError(
                f"{prefix}: graded_relevance keys must be non-empty strings"
            )
        if isinstance(relevance, bool) or not isinstance(relevance, int):
            raise DatasetValidationError(
                f"{prefix}: graded_relevance values must be integers"
            )
        if relevance < 0 or relevance > 3:
            raise DatasetValidationError(
                f"{prefix}: graded_relevance values must be between 0 and 3"
            )
        normalized[chunk_id.strip()] = relevance
    return normalized


def _validate_corpus(path: Path) -> int:
    return len(load_corpus(path))


def _require_reviewed_public_fixture_inputs(
    dataset: str | Path,
    corpus: str | Path,
    configuration: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> tuple[Path, Path, Path]:
    root = Path(
        os.path.abspath(
            os.fspath(
                repository_root
                if repository_root is not None
                else Path(__file__).resolve().parents[1]
            )
        )
    )
    supplied = tuple(
        Path(os.path.abspath(os.fspath(path)))
        for path in (dataset, corpus, configuration)
    )
    reviewed_configurations = {
        root / relative_path
        for relative_path in PUBLIC_FIXTURE_CONFIGURATIONS
    }
    if (
        supplied[0] != root / PUBLIC_FIXTURE_DATASET
        or supplied[1] != root / PUBLIC_FIXTURE_CORPUS
        or supplied[2] not in reviewed_configurations
    ):
        raise PublicFixtureBoundaryError(PUBLIC_FIXTURE_BOUNDARY_ERROR)
    for path in supplied:
        file_descriptor = _open_regular_file_without_symlink_components(path)
        os.close(file_descriptor)
    return supplied[0], supplied[1], supplied[2]


def _open_regular_file_without_symlink_components(path: Path) -> int:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    current_fd = -1
    try:
        current_fd = os.open(path.anchor, directory_flags)
        for index, part in enumerate(path.parts[1:], start=1):
            is_final = index == len(path.parts) - 1
            next_fd = os.open(
                part,
                file_flags if is_final else directory_flags,
                dir_fd=current_fd,
            )
            os.close(current_fd)
            current_fd = next_fd
        if not stat.S_ISREG(os.fstat(current_fd).st_mode):
            raise PublicFixtureBoundaryError(PUBLIC_FIXTURE_BOUNDARY_ERROR)
        result = current_fd
        current_fd = -1
        return result
    except PublicFixtureBoundaryError:
        raise
    except OSError:
        raise PublicFixtureBoundaryError(PUBLIC_FIXTURE_BOUNDARY_ERROR) from None
    finally:
        if current_fd >= 0:
            os.close(current_fd)


def _load_reviewed_public_fixture_inputs(
    dataset: str | Path,
    corpus: str | Path,
    configuration: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> tuple[
    list[RetrievalEvaluationCase],
    list[dict[str, Any]],
    dict[str, Any] | None,
    dict[str, str],
]:
    root = Path(
        os.path.abspath(
            os.fspath(
                repository_root
                if repository_root is not None
                else Path(__file__).resolve().parents[1]
            )
        )
    )
    paths = _require_reviewed_public_fixture_inputs(
        dataset,
        corpus,
        configuration,
        repository_root=root,
    )
    file_descriptors: list[int] = []
    try:
        manifest_fd = _open_regular_file_without_symlink_components(
            root / PUBLIC_FIXTURE_MANIFEST
        )
        file_descriptors.append(manifest_fd)
        input_fds = tuple(
            _open_regular_file_without_symlink_components(path) for path in paths
        )
        file_descriptors.extend(input_fds)
        manifest_content = _read_utf8_descriptor(manifest_fd, label="manifest")
        input_contents = tuple(
            _read_utf8_descriptor(file_descriptor, label=label)
            for file_descriptor, label in zip(
                input_fds,
                ("dataset", "corpus", "configuration"),
                strict=True,
            )
        )
        if len(input_contents) != 3:
            raise PublicFixtureBoundaryError(PUBLIC_FIXTURE_BOUNDARY_ERROR)
        _verify_reviewed_public_fixture_digests(
            manifest_content,
            root=root,
            paths=paths,
            contents=input_contents,
        )
        return _load_evaluation_input_contents(*input_contents)
    except PublicFixtureBoundaryError:
        raise
    except (DatasetValidationError, EvaluationInputError):
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise PublicFixtureBoundaryError(PUBLIC_FIXTURE_BOUNDARY_ERROR) from None
    finally:
        for file_descriptor in file_descriptors:
            try:
                os.close(file_descriptor)
            except OSError:
                pass


def _verify_reviewed_public_fixture_digests(
    manifest_content: str,
    *,
    root: Path,
    paths: tuple[Path, Path, Path],
    contents: tuple[str, str, str],
) -> None:
    try:
        manifest = json.loads(manifest_content)
        if not isinstance(manifest, dict) or set(manifest) != {
            "version",
            "dataset",
            "corpus",
            "configurations",
        }:
            raise ValueError
        if manifest["version"] != 1:
            raise ValueError
        dataset_entry = manifest["dataset"]
        corpus_entry = manifest["corpus"]
        configurations = manifest["configurations"]
        if not isinstance(dataset_entry, dict) or set(dataset_entry) != {
            "path",
            "sha256",
        }:
            raise ValueError
        if not isinstance(corpus_entry, dict) or set(corpus_entry) != {
            "path",
            "sha256",
        }:
            raise ValueError
        expected_configuration_paths = {
            path.as_posix() for path in PUBLIC_FIXTURE_CONFIGURATIONS
        }
        if (
            dataset_entry["path"] != PUBLIC_FIXTURE_DATASET.as_posix()
            or corpus_entry["path"] != PUBLIC_FIXTURE_CORPUS.as_posix()
            or not isinstance(configurations, dict)
            or set(configurations) != expected_configuration_paths
        ):
            raise ValueError
        configured_digests = [
            dataset_entry["sha256"],
            corpus_entry["sha256"],
            *configurations.values(),
        ]
        if any(
            not isinstance(digest, str)
            or SHA256_PATTERN.fullmatch(digest) is None
            for digest in configured_digests
        ):
            raise ValueError
        configuration_relative = paths[2].relative_to(root).as_posix()
        expected_digests = (
            dataset_entry["sha256"],
            corpus_entry["sha256"],
            configurations[configuration_relative],
        )
        actual_digests = tuple(_sha256_text(content) for content in contents)
        if actual_digests != expected_digests:
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise PublicFixtureBoundaryError(PUBLIC_FIXTURE_BOUNDARY_ERROR) from None


def _load_evaluation_inputs(
    dataset_path: Path,
    corpus_path: Path | None,
    configuration_path: Path | None,
) -> tuple[
    list[RetrievalEvaluationCase],
    list[dict[str, Any]],
    dict[str, Any] | None,
    dict[str, str],
]:
    dataset_content = _read_utf8_snapshot(dataset_path, label="dataset")
    corpus_content = (
        _read_utf8_snapshot(corpus_path, label="corpus")
        if corpus_path is not None
        else None
    )
    configuration_content = (
        _read_utf8_snapshot(configuration_path, label="configuration")
        if configuration_path is not None
        else None
    )
    return _load_evaluation_input_contents(
        dataset_content,
        corpus_content,
        configuration_content,
    )


def _load_evaluation_input_contents(
    dataset_content: str,
    corpus_content: str | None,
    configuration_content: str | None,
) -> tuple[
    list[RetrievalEvaluationCase],
    list[dict[str, Any]],
    dict[str, Any] | None,
    dict[str, str],
]:
    cases = load_dataset_text(dataset_content)
    corpus = load_corpus_text(corpus_content) if corpus_content is not None else []
    configuration = None
    if configuration_content is not None:
        configuration = json.loads(configuration_content)
        if not isinstance(configuration, dict):
            raise DatasetValidationError("configuration must be a JSON object")
        validate_configuration(configuration)
    input_digests = {
        "dataset_sha256": _sha256_text(dataset_content),
        "corpus_sha256": _sha256_text(corpus_content or ""),
        "configuration_sha256": _sha256_text(configuration_content or ""),
    }
    return cases, corpus, configuration, input_digests


def _read_utf8_snapshot(path: Path, *, label: str) -> str:
    content = path.read_bytes()
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DatasetValidationError(f"{label} must be valid UTF-8") from exc


def _read_utf8_descriptor(file_descriptor: int, *, label: str) -> str:
    os.lseek(file_descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(file_descriptor, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DatasetValidationError(f"{label} must be valid UTF-8") from exc


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or run deterministic career retrieval evaluations."
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--corpus", default="")
    parser.add_argument("--configuration", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--artifact-basename", default="report")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--public-only", action="store_true")
    parser.add_argument("--git-identifier", default="")
    parser.add_argument("--timestamp", default="")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    dataset_path = Path(args.dataset)
    corpus_path = Path(args.corpus) if args.corpus else None
    configuration_path = Path(args.configuration) if args.configuration else None
    allow_public_output = False
    if args.public_only:
        try:
            dataset_path, corpus_path, configuration_path = (
                _require_reviewed_public_fixture_inputs(
                    dataset_path,
                    corpus_path or Path(""),
                    configuration_path or Path(""),
                )
            )
        except PublicFixtureBoundaryError as exc:
            raise SystemExit(str(exc)) from exc
        allow_public_output = True
    if not args.validate_only:
        match = PRECISE_GIT_IDENTIFIER_PATTERN.fullmatch(args.git_identifier)
        if match is None or (
            (match["state"] == "clean")
            != (match["head_tree"] == match["worktree_tree"])
        ):
            raise SystemExit(PRECISE_GIT_IDENTIFIER_ERROR)

    try:
        if allow_public_output:
            assert corpus_path is not None
            assert configuration_path is not None
            cases, corpus, configuration, input_digests = (
                _load_reviewed_public_fixture_inputs(
                    dataset_path,
                    corpus_path,
                    configuration_path,
                )
            )
        else:
            cases, corpus, configuration, input_digests = _load_evaluation_inputs(
                dataset_path,
                corpus_path,
                configuration_path,
            )
        corpus_size = len(corpus) if corpus_path is not None else None
    except (
        DatasetValidationError,
        EvaluationInputError,
        PublicFixtureBoundaryError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise SystemExit(str(exc)) from exc

    if args.public_only and any(
        case.label_source != "deterministic_fixture" for case in cases
    ):
        raise SystemExit("public-only mode requires deterministic_fixture labels")

    if args.validate_only:
        summary = {
            "status": "validated_not_measured",
            "disclaimer": FIXTURE_DISCLAIMER,
            "dataset": {
                "name": dataset_path.name,
                "size": len(cases),
                "label_sources": sorted({case.label_source for case in cases}),
                "query_categories": sorted(
                    {case.query_category for case in cases}
                ),
            },
            "corpus_size": corpus_size,
            "configuration": configuration,
            "input_digests": input_digests,
        }
        if args.output_dir:
            output_dir = Path(args.output_dir)
            rendered = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
            if allow_public_output:
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "validation.json").write_text(
                    rendered,
                    encoding="utf-8",
                )
            else:
                try:
                    enforce_parent_mode = require_private_output_destination(
                        output_dir
                    )
                    secure_atomic_write_text(
                        output_dir / "validation.json",
                        rendered,
                        enforce_parent_mode=enforce_parent_mode,
                    )
                except ValueError as exc:
                    raise SystemExit(str(exc)) from exc
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    if corpus_path is None or configuration is None or not args.output_dir:
        raise SystemExit(
            "measured execution requires --corpus, --configuration, and --output-dir"
        )
    try:
        report = run_evaluation(
            cases=cases,
            corpus=corpus,
            dataset_name=dataset_path.name,
            configuration=configuration,
            git_identifier=args.git_identifier,
            timestamp=args.timestamp or None,
            input_digests=input_digests,
        )
    except EvaluationInputError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        paths = write_report_artifacts(
            report,
            args.output_dir,
            artifact_basename=args.artifact_basename,
            allow_public_output=allow_public_output,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        json.dumps(
            {
                "status": report["status"],
                "dataset": report["dataset"],
                "configuration": report["configuration"],
                "metrics": report["metrics"],
                "failure_count": len(report["failures"]),
                "artifacts": {
                    "json": paths["json"].name,
                    "markdown": paths["markdown"].name,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
