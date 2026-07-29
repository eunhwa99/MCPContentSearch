from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DocumentSortQualityCase:
    """Expected deterministic document ordering for one search_documents call."""

    case_id: str
    query: str
    sort_by: str
    sort_order: str
    expected_document_ids: tuple[str, ...]
    top_k: int = 10
    filters: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "DocumentSortQualityCase":
        return cls(
            case_id=str(value["case_id"]),
            query=str(value["query"]),
            sort_by=str(value["sort_by"]),
            sort_order=str(value["sort_order"]),
            expected_document_ids=tuple(
                str(document_id)
                for document_id in value.get("expected_document_ids", ())
            ),
            top_k=int(value.get("top_k", 10)),
            filters=dict(value.get("filters", {})),
        )


@dataclass(frozen=True)
class DocumentSortQualityResult:
    case_id: str
    passed: bool
    score: float
    checks: dict[str, bool]
    failures: tuple[str, ...]
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "score": self.score,
            "checks": self.checks,
            "failures": list(self.failures),
            "details": self.details,
        }


def load_cases(path: str | Path) -> list[DocumentSortQualityCase]:
    raw_cases = json.loads(Path(path).read_text(encoding="utf-8"))
    return [DocumentSortQualityCase.from_mapping(item) for item in raw_cases]


def evaluate_document_sort_payload(
    payload: dict[str, Any],
    case: DocumentSortQualityCase,
) -> DocumentSortQualityResult:
    raw_results = payload.get("results")
    results = raw_results if isinstance(raw_results, list) else []
    document_ids = [
        str(
            item.get("document_id")
            if isinstance(item, dict)
            else getattr(item, "document_id", "")
        )
        for item in results
    ]
    checks = {
        "document_count": len(document_ids) == len(case.expected_document_ids),
        "document_order": document_ids == list(case.expected_document_ids),
    }
    failures = tuple(name for name, passed in checks.items() if not passed)
    return DocumentSortQualityResult(
        case_id=case.case_id,
        passed=not failures,
        score=sum(checks.values()) / len(checks),
        checks=checks,
        failures=failures,
        details={
            "document_ids": document_ids,
            "sort_by": case.sort_by,
            "sort_order": case.sort_order,
        },
    )


def evaluate_document_sort_suite(
    payloads_by_case_id: dict[str, dict[str, Any]],
    cases: list[DocumentSortQualityCase],
) -> dict[str, Any]:
    if not cases:
        return {
            "passed": False,
            "total": 0,
            "passed_count": 0,
            "average_score": 0.0,
            "results": [],
        }
    results = [
        evaluate_document_sort_payload(
            payloads_by_case_id.get(case.case_id, {}),
            case,
        )
        for case in cases
    ]
    return {
        "passed": all(result.passed for result in results),
        "total": len(results),
        "passed_count": sum(result.passed for result in results),
        "average_score": sum(result.score for result in results) / len(results),
        "results": [result.as_dict() for result in results],
    }
