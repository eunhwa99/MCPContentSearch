"""Dependency-light validation for deterministic and private evaluation corpora."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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
    {"professional", "academic", "personal_project", "prototype", "unknown"}
)


class EvaluationInputError(ValueError):
    """Raised when offline retrieval inputs are invalid."""


def load_corpus(path: str | Path) -> list[dict[str, Any]]:
    corpus_path = Path(path)
    if not corpus_path.is_file():
        raise EvaluationInputError(f"corpus does not exist: {corpus_path}")
    return load_corpus_text(corpus_path.read_text(encoding="utf-8"))


def load_corpus_text(content: str) -> list[dict[str, Any]]:
    """Validate corpus content already captured from a stable input snapshot."""
    corpus: list[dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()
    for line_number, raw_line in enumerate(
        content.splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            item = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise EvaluationInputError(
                f"corpus line {line_number}: invalid JSON: {exc.msg}"
            ) from exc
        normalized = _validate_corpus_item(item, line_number=line_number)
        chunk_id = normalized["chunk_id"]
        if chunk_id in seen_chunk_ids:
            raise EvaluationInputError(
                f"corpus line {line_number}: duplicate chunk_id: {chunk_id}"
            )
        seen_chunk_ids.add(chunk_id)
        corpus.append(normalized)
    if not corpus:
        raise EvaluationInputError("corpus must contain at least one entry")
    return corpus


def _validate_corpus_item(item: Any, *, line_number: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise EvaluationInputError(
            f"corpus line {line_number}: entry must be an object"
        )
    for key in (
        "chunk_id",
        "document_id",
        "source_type",
        "experience_type",
        "exact_quote",
    ):
        if not isinstance(item.get(key), str) or not item[key].strip():
            raise EvaluationInputError(
                f"corpus line {line_number}: {key} must be a non-empty string"
            )
    if item["source_type"] not in SOURCE_TYPES:
        raise EvaluationInputError(
            f"corpus line {line_number}: unsupported source_type"
        )
    if item["experience_type"] not in EXPERIENCE_TYPES:
        raise EvaluationInputError(
            f"corpus line {line_number}: unsupported experience_type"
        )
    content = item.get("content", item["exact_quote"])
    if not isinstance(content, str) or not content.strip():
        raise EvaluationInputError(
            f"corpus line {line_number}: content must be a non-empty string"
        )
    if item["exact_quote"] not in content:
        raise EvaluationInputError(
            f"corpus line {line_number}: exact_quote must occur in content"
        )
    normalized = dict(item)
    normalized["content"] = content.strip()
    for key in (
        "chunk_id",
        "document_id",
        "source_type",
        "experience_type",
        "exact_quote",
    ):
        normalized[key] = normalized[key].strip()
    for key in (
        "document_version_id",
        "file_name",
        "document_title",
        "section_title",
        "parent_section_title",
    ):
        value = normalized.get(key, "")
        if not isinstance(value, str):
            raise EvaluationInputError(
                f"corpus line {line_number}: {key} must be a string"
            )
        normalized[key] = value.strip()
    return normalized
