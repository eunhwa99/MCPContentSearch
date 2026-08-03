from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any, Callable

from core.exceptions import CareerManifestParsingError, ParsingError
from core.models import DocumentModel, EvidenceSourceType, ExperienceType
from parsing.career_documents import (
    CareerDocumentParser,
    CareerParsingCancelled,
    _LoadedCareerFile,
    _open_trusted_absolute_directory,
    _open_trusted_child_directory,
    _open_trusted_regular_file,
    _open_trusted_regular_file_at,
    _verify_trusted_file_read_snapshot,
    validate_career_document_id,
    validate_career_metadata,
)


_MAX_MANIFEST_BYTES = 1_000_000
_MAX_FILE_BYTES = 50_000_000
_DEFAULT_MAX_FILES = 100
_DEFAULT_MAX_TOTAL_RAW_BYTES = 50_000_000
_DEFAULT_MAX_TOTAL_EXTRACTED_TEXT_BYTES = 100_000_000
_ENTRY_KEYS = frozenset(
    {
        "path",
        "source_type",
        "experience_type",
        "document_id",
        "document_title",
        "company",
        "role",
        "project",
        "start_date",
        "end_date",
    }
)


def career_manifest_disabled_reason(manifest_path: Path | None) -> str:
    if manifest_path is None:
        return (
            "Source source_career is disabled because "
            "CONTEXTWIKI_CAREER_MANIFEST_PATH is not set."
        )
    path = Path(manifest_path)
    if not path.is_absolute():
        return (
            "Source source_career is disabled because "
            "CONTEXTWIKI_CAREER_MANIFEST_PATH must be an absolute path."
        )
    descriptor = -1
    try:
        descriptor = _open_trusted_regular_file(path, path.name or "manifest")
    except ParsingError:
        return (
            "Source source_career is disabled because the configured manifest "
            "must be an existing trusted file with no symlinked path components."
        )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return ""


def load_career_manifest(
    manifest_path: Path,
    *,
    max_file_bytes: int,
    max_files: int = _DEFAULT_MAX_FILES,
    max_total_raw_bytes: int = _DEFAULT_MAX_TOTAL_RAW_BYTES,
    max_total_extracted_text_bytes: int = _DEFAULT_MAX_TOTAL_EXTRACTED_TEXT_BYTES,
    cancel_check: Callable[[], bool] | None = None,
) -> list[DocumentModel]:
    """Parse only files explicitly listed by one configured local manifest."""
    if (
        isinstance(max_file_bytes, bool)
        or not isinstance(max_file_bytes, int)
        or max_file_bytes <= 0
    ):
        raise ValueError("max_file_bytes must be a positive integer")
    if max_file_bytes > _MAX_FILE_BYTES:
        raise ValueError(f"max_file_bytes exceeds maximum {_MAX_FILE_BYTES}")
    for limit_name, limit_value in (
        ("max_files", max_files),
        ("max_total_raw_bytes", max_total_raw_bytes),
        ("max_total_extracted_text_bytes", max_total_extracted_text_bytes),
    ):
        if (
            isinstance(limit_value, bool)
            or not isinstance(limit_value, int)
            or limit_value <= 0
        ):
            raise ValueError(f"{limit_name} must be a positive integer")
    parsing_started_at = time.perf_counter()
    attempted_documents = 0
    completed_documents = 0
    path = Path(manifest_path)
    manifest_parent_descriptor = -1
    root_descriptor = -1
    parser: CareerDocumentParser | None = None
    try:
        disabled_reason = career_manifest_disabled_reason(manifest_path)
        if disabled_reason:
            raise ParsingError(disabled_reason)

        (
            raw,
            manifest_parent_descriptor,
            manifest_file_stat,
        ) = _read_bound_manifest_bounded(path)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ParsingError(f"Could not parse career manifest {path.name}") from exc
        if not isinstance(payload, dict):
            raise ParsingError(f"Career manifest must be an object: {path.name}")
        _verify_manifest_parent_binding(path, manifest_parent_descriptor)

        root, root_descriptor = _manifest_root(
            path,
            payload.get("root", "."),
            manifest_parent_descriptor=manifest_parent_descriptor,
        )
        entries = payload.get("documents")
        if not isinstance(entries, list):
            raise ParsingError(f"Career manifest documents must be a list: {path.name}")
        if len(entries) > max_files:
            raise ParsingError(f"Career manifest file count limit exceeded: {path.name}")

        parser = CareerDocumentParser(
            root=root,
            root_descriptor=root_descriptor,
            max_file_bytes=max_file_bytes,
            cancel_check=cancel_check,
        )
        validated_entries: list[tuple[int, Path, dict[str, str]]] = []
        listed_paths: set[str] = set()
        for index, entry in enumerate(entries, start=1):
            attempted_documents = index
            if not isinstance(entry, dict):
                raise ParsingError(f"Career manifest entry {index} must be an object")
            unknown_keys = set(entry) - _ENTRY_KEYS
            if unknown_keys:
                raise ParsingError(f"Career manifest entry {index} has unsupported fields")
            raw_relative_path = entry.get("path")
            if isinstance(raw_relative_path, str) and not raw_relative_path.isprintable():
                raise ParsingError(f"Career manifest entry {index} has an unsafe path")
            relative_path = _required_text(entry, "path", index)
            if not relative_path.isprintable():
                raise ParsingError(f"Career manifest entry {index} has an unsafe path")
            requested_path = Path(relative_path)
            if requested_path.is_absolute() or ".." in requested_path.parts:
                raise ParsingError(f"Career manifest entry {index} has an unsafe path")
            canonical_relative_path = requested_path.as_posix()
            if canonical_relative_path in listed_paths:
                raise ParsingError(f"Career manifest entry {index} repeats a file path")
            listed_paths.add(canonical_relative_path)

            source_type = _required_text(entry, "source_type", index)
            experience_type = _optional_text(entry, "experience_type") or "unknown"
            try:
                EvidenceSourceType(source_type)
                ExperienceType(experience_type)
            except ValueError as exc:
                raise ParsingError(
                    f"Career manifest entry {index} has invalid taxonomy"
                ) from exc
            metadata = {
                field_name: validate_career_metadata(
                    field_name,
                    _optional_text(entry, field_name),
                )
                for field_name in (
                    "document_title",
                    "company",
                    "role",
                    "project",
                    "start_date",
                    "end_date",
                )
            }
            metadata.update(
                {
                    "source_type": source_type,
                    "experience_type": experience_type,
                    "document_id": validate_career_document_id(
                        _optional_text(entry, "document_id")
                    ),
                }
            )
            validated_entries.append((index, requested_path, metadata))

        attempted_documents = 0
        aggregate_raw_bytes = 0
        listed_file_identities: set[tuple[int, int]] = set()
        loaded_entries: list[tuple[int, _LoadedCareerFile, dict[str, str]]] = []
        for index, requested_path, metadata in validated_entries:
            attempted_documents = index
            if cancel_check is not None and cancel_check():
                raise CareerParsingCancelled("Career manifest parsing cancelled")
            loaded = parser.read_file(requested_path)
            file_identity = (loaded.file_stat.st_dev, loaded.file_stat.st_ino)
            if file_identity in listed_file_identities:
                raise ParsingError(
                    f"Career manifest entry {index} repeats a physical file"
                )
            listed_file_identities.add(file_identity)
            aggregate_raw_bytes += len(loaded.raw)
            if aggregate_raw_bytes > max_total_raw_bytes:
                raise ParsingError(
                    f"Career manifest aggregate raw byte limit exceeded: {path.name}"
                )
            loaded_entries.append((index, loaded, metadata))

        attempted_documents = 0
        documents: list[DocumentModel] = []
        listed_document_ids: set[str] = set()
        aggregate_extracted_text_bytes = 0
        for index, loaded, metadata in loaded_entries:
            attempted_documents = index
            if cancel_check is not None and cancel_check():
                raise CareerParsingCancelled("Career manifest parsing cancelled")
            document = parser.parse_loaded_file(loaded, **metadata)
            completed_documents += 1
            aggregate_extracted_text_bytes += len(document.content.encode("utf-8"))
            if aggregate_extracted_text_bytes > max_total_extracted_text_bytes:
                raise ParsingError(
                    "Career manifest aggregate extracted text byte limit exceeded: "
                    f"{path.name}"
                )
            if document.document_id in listed_document_ids:
                raise ParsingError(f"Career manifest entry {index} repeats a document id")
            listed_document_ids.add(document.document_id)
            documents.append(document)
        _verify_manifest_parent_binding(path, manifest_parent_descriptor)
        for _index, loaded, _metadata in loaded_entries:
            parser.revalidate_loaded_file(loaded)
        _verify_manifest_file_snapshot(
            path,
            manifest_parent_descriptor,
            manifest_file_stat,
        )
        return documents
    except CareerParsingCancelled:
        raise
    except ParsingError as exc:
        raise CareerManifestParsingError(
            str(exc),
            attempted_documents=attempted_documents,
            completed_documents=completed_documents,
            parsing_latency_ms=(time.perf_counter() - parsing_started_at) * 1000,
        ) from exc
    finally:
        if parser is not None:
            parser.close()
        if root_descriptor >= 0:
            os.close(root_descriptor)
        if manifest_parent_descriptor >= 0:
            os.close(manifest_parent_descriptor)


def _manifest_root(
    manifest_path: Path,
    raw_root: Any,
    *,
    manifest_parent_descriptor: int,
) -> tuple[Path, int]:
    if (
        not isinstance(raw_root, str)
        or not raw_root.strip()
        or not raw_root.isprintable()
    ):
        raise ParsingError(f"Career manifest root is invalid: {manifest_path.name}")
    requested = Path(raw_root.strip()).expanduser()
    if ".." in requested.parts:
        raise ParsingError(f"Career manifest root is unsafe: {manifest_path.name}")
    candidate = requested if requested.is_absolute() else manifest_path.parent / requested
    candidate = Path(os.path.abspath(os.fspath(candidate)))
    descriptor = -1
    try:
        if requested.is_absolute():
            descriptor = _open_trusted_absolute_directory(
                candidate,
                manifest_path.name,
            )
        else:
            descriptor = os.dup(manifest_parent_descriptor)
            logical_path = manifest_path.parent
            for component in requested.parts:
                logical_path = logical_path / component
                next_descriptor = _open_trusted_child_directory(
                    descriptor,
                    component,
                    logical_path,
                    manifest_path.name,
                )
                os.close(descriptor)
                descriptor = next_descriptor
    except (OSError, ValueError, ParsingError) as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise ParsingError(
            f"Career manifest root is unavailable: {manifest_path.name}"
        ) from exc
    return candidate, descriptor


def _read_manifest_bounded(path: Path) -> bytes:
    raw, parent_descriptor, _file_stat = _read_bound_manifest_bounded(path)
    os.close(parent_descriptor)
    return raw


def _verify_manifest_parent_binding(path: Path, bound_descriptor: int) -> None:
    current_descriptor = -1
    try:
        current_descriptor = _open_trusted_absolute_directory(
            path.parent,
            path.name or "manifest",
        )
        bound_stat = os.fstat(bound_descriptor)
        current_stat = os.fstat(current_descriptor)
        if (bound_stat.st_dev, bound_stat.st_ino) != (
            current_stat.st_dev,
            current_stat.st_ino,
        ):
            raise ParsingError(f"Could not read career manifest {path.name}")
    except (OSError, ValueError) as exc:
        raise ParsingError(f"Could not read career manifest {path.name}") from exc
    finally:
        if current_descriptor >= 0:
            os.close(current_descriptor)


def _verify_manifest_file_snapshot(
    path: Path,
    parent_descriptor: int,
    initial_file_stat: os.stat_result,
) -> None:
    descriptor = -1
    try:
        descriptor = _open_trusted_regular_file_at(
            parent_descriptor,
            path.name,
            path.name or "manifest",
        )
        current_file_stat = os.fstat(descriptor)
        _verify_trusted_file_read_snapshot(
            initial_file_stat,
            current_file_stat,
            f"career manifest {path.name}",
        )
    except ParsingError:
        raise
    except (OSError, ValueError) as exc:
        raise ParsingError(f"Could not read career manifest {path.name}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_bound_manifest_bounded(path: Path) -> tuple[bytes, int, os.stat_result]:
    parent_descriptor = -1
    descriptor = -1
    try:
        parent_descriptor = _open_trusted_absolute_directory(
            path.parent,
            path.name or "manifest",
        )
        descriptor = _open_trusted_regular_file_at(
            parent_descriptor,
            path.name,
            path.name or "manifest",
        )
        file_stat = os.fstat(descriptor)
        if file_stat.st_size > _MAX_MANIFEST_BYTES:
            raise ParsingError(f"Career manifest exceeds byte limit: {path.name}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            raw = handle.read(_MAX_MANIFEST_BYTES + 1)
            completed_file_stat = os.fstat(handle.fileno())
        _verify_trusted_file_read_snapshot(
            file_stat,
            completed_file_stat,
            f"career manifest {path.name}",
        )
        if len(raw) > _MAX_MANIFEST_BYTES:
            raise ParsingError(f"Career manifest exceeds byte limit: {path.name}")
        return raw, parent_descriptor, file_stat
    except ParsingError:
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        raise
    except (OSError, ValueError) as exc:
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
        raise ParsingError(f"Could not read career manifest {path.name}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _required_text(entry: dict[str, Any], key: str, index: int) -> str:
    value = _optional_text(entry, key)
    if not value:
        raise ParsingError(f"Career manifest entry {index} requires {key}")
    return value


def _optional_text(entry: dict[str, Any], key: str) -> str:
    value = entry.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ParsingError(f"Career manifest field {key} must be text")
    return value.strip()
