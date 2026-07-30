from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DATASET_ROOT = Path(__file__).resolve().parent / "datasets" / "rag_v1"
_VALID_SPLITS = frozenset({"train", "dev", "test"})


def load_manifest(dataset_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(dataset_root) if dataset_root is not None else DATASET_ROOT
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def load_documents(dataset_root: str | Path | None = None) -> list[dict[str, Any]]:
    root = Path(dataset_root) if dataset_root is not None else DATASET_ROOT
    documents: list[dict[str, Any]] = []
    with (root / "documents.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            documents.append(json.loads(text))
    return documents


def load_cases(
    split: str,
    dataset_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    if split not in _VALID_SPLITS:
        raise ValueError(f"Unknown split: {split}")
    root = Path(dataset_root) if dataset_root is not None else DATASET_ROOT
    cases: list[dict[str, Any]] = []
    with (root / "cases" / f"{split}.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            case = json.loads(text)
            case["split"] = split
            cases.append(case)
    return cases


def dataset_version(dataset_root: str | Path | None = None) -> str:
    root = Path(dataset_root) if dataset_root is not None else DATASET_ROOT
    version_path = root / "VERSION"
    if version_path.exists():
        return version_path.read_text(encoding="utf-8").strip()
    return str(load_manifest(root).get("dataset_version", ""))
