from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SECRET_LIKE_RE = re.compile(
    r"("
    r"sk-[A-Za-z0-9_-]{16,}|"
    r"github_pat_[A-Za-z0-9_]{16,}|"
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password)\s*[:=]\s*\S+"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AnswerQualityCase:
    """Expected deterministic checks for one answer payload."""

    case_id: str
    question: str
    group: str = "generic"
    top_k: int = 3
    expected_answer_terms: tuple[str, ...] = ()
    forbidden_answer_terms: tuple[str, ...] = ()
    required_citation_chunk_ids: tuple[str, ...] = ()
    expected_status: str = "grounded"
    min_citation_count: int = 1

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "AnswerQualityCase":
        return cls(
            case_id=str(value["case_id"]),
            question=str(value["question"]),
            group=str(value.get("group", "generic")),
            top_k=int(value.get("top_k", 3)),
            expected_answer_terms=tuple(value.get("expected_answer_terms", ())),
            forbidden_answer_terms=tuple(value.get("forbidden_answer_terms", ())),
            required_citation_chunk_ids=tuple(value.get("required_citation_chunk_ids", ())),
            expected_status=str(value.get("expected_status", "grounded")),
            min_citation_count=int(value.get("min_citation_count", 1)),
        )


@dataclass(frozen=True)
class AnswerQualityResult:
    case_id: str
    passed: bool
    score: float
    checks: dict[str, bool]
    failures: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "score": self.score,
            "checks": self.checks,
            "failures": list(self.failures),
            "details": self.details,
        }


def load_cases(path: str | Path) -> list[AnswerQualityCase]:
    raw_cases = json.loads(Path(path).read_text(encoding="utf-8"))
    return [AnswerQualityCase.from_mapping(item) for item in raw_cases]


def evaluate_answer_payload(
    payload: dict[str, Any],
    case: AnswerQualityCase,
) -> AnswerQualityResult:
    answer_text = str(payload.get("answer") or "")
    answer_text_lower = answer_text.lower()
    evidence_status = str(payload.get("evidence_status") or payload.get("status") or "")
    citations = _as_list(payload.get("citations"))
    used_chunks = _string_set(_as_list(payload.get("used_chunks")))
    citation_chunk_ids = _citation_chunk_ids(citations)
    all_output_text = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    checks = {
        "status_matches": evidence_status == case.expected_status,
        "answer_present": bool(answer_text.strip()) if case.expected_status == "grounded" else True,
        "expected_terms_present": all(
            term.lower() in answer_text_lower for term in case.expected_answer_terms
        ),
        "forbidden_terms_absent": not any(
            term.lower() in answer_text_lower for term in case.forbidden_answer_terms
        ),
        "min_citation_count": len(citation_chunk_ids) >= case.min_citation_count,
        "required_citations_present": set(case.required_citation_chunk_ids).issubset(
            citation_chunk_ids
        ),
        "used_chunks_have_citations": used_chunks.issubset(citation_chunk_ids),
        "no_secret_like_output": not SECRET_LIKE_RE.search(all_output_text),
    }
    failures = tuple(name for name, passed in checks.items() if not passed)
    score = sum(1 for passed in checks.values() if passed) / len(checks)

    return AnswerQualityResult(
        case_id=case.case_id,
        passed=not failures,
        score=score,
        checks=checks,
        failures=failures,
        details={
            "evidence_status": evidence_status,
            "citation_count": len(citation_chunk_ids),
            "raw_citation_count": len(citations),
            "citation_chunk_ids": sorted(citation_chunk_ids),
            "used_chunks": sorted(used_chunks),
        },
    )


def evaluate_answer_suite(
    payloads_by_case_id: dict[str, dict[str, Any]],
    cases: list[AnswerQualityCase],
) -> dict[str, Any]:
    if not cases:
        return {
            "passed": False,
            "total": 0,
            "passed_count": 0,
            "average_score": 0.0,
            "group_breakdown": {},
            "quality_metrics": _answer_quality_metrics({}, []),
            "results": [],
        }
    raw_results = [
        evaluate_answer_payload(payloads_by_case_id.get(case.case_id, {}), case)
        for case in cases
    ]
    passed = [result for result in raw_results if result.passed]
    average_score = (
        sum(result.score for result in raw_results) / len(raw_results)
        if raw_results
        else 0.0
    )
    return {
        "passed": len(passed) == len(raw_results),
        "total": len(raw_results),
        "passed_count": len(passed),
        "average_score": average_score,
        "group_breakdown": _group_breakdown(cases, raw_results),
        "quality_metrics": _answer_quality_metrics(payloads_by_case_id, cases),
        "results": [result.as_dict() for result in raw_results],
    }


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_set(values: list[Any]) -> set[str]:
    return {str(value) for value in values if value is not None}


def _citation_chunk_ids(citations: list[Any]) -> set[str]:
    chunk_ids: set[str] = set()
    for citation in citations:
        if isinstance(citation, dict) and citation.get("chunk_id"):
            chunk_ids.add(str(citation["chunk_id"]))
    return chunk_ids


def _answer_quality_metrics(
    payloads_by_case_id: dict[str, dict[str, Any]],
    cases: list[AnswerQualityCase],
) -> dict[str, Any]:
    correct_status_count = 0
    required_citation_hits = 0
    required_citation_count = 0
    covered_used_chunk_count = 0
    used_chunk_count = 0
    correct_insufficient_status_count = 0
    insufficient_status_case_count = 0
    required_citation_case_count = 0
    citation_coverage_case_count = 0

    for case in cases:
        payload = payloads_by_case_id.get(case.case_id, {})
        evidence_status = str(
            payload.get("evidence_status") or payload.get("status") or ""
        )
        citations = _citation_chunk_ids(_as_list(payload.get("citations")))
        used_chunks = _string_set(_as_list(payload.get("used_chunks")))
        required_citations = set(case.required_citation_chunk_ids)

        correct_status_count += int(evidence_status == case.expected_status)

        if required_citations:
            required_citation_case_count += 1
            required_citation_hits += len(required_citations.intersection(citations))
            required_citation_count += len(required_citations)

        if used_chunks:
            citation_coverage_case_count += 1
            covered_used_chunk_count += len(used_chunks.intersection(citations))
            used_chunk_count += len(used_chunks)

        if case.expected_status == "insufficient":
            insufficient_status_case_count += 1
            correct_insufficient_status_count += int(
                evidence_status == "insufficient"
            )

    return {
        "scorable_case_counts": {
            "status_accuracy": len(cases),
            "required_citation_recall": required_citation_case_count,
            "citation_coverage": citation_coverage_case_count,
            "insufficient_status_accuracy": insufficient_status_case_count,
        },
        "status_accuracy": _ratio_metric(correct_status_count, len(cases)),
        "required_citation_recall": _ratio_metric(
            required_citation_hits,
            required_citation_count,
        ),
        "citation_coverage": _ratio_metric(
            covered_used_chunk_count,
            used_chunk_count,
        ),
        "insufficient_status_accuracy": _ratio_metric(
            correct_insufficient_status_count,
            insufficient_status_case_count,
        ),
    }


def _ratio_metric(
    numerator: int,
    denominator: int,
) -> dict[str, float | int | None]:
    return {
        "value": numerator / denominator if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
    }


def _group_breakdown(
    cases: list[AnswerQualityCase],
    results: list[AnswerQualityResult],
) -> dict[str, Any]:
    grouped: dict[str, list[AnswerQualityResult]] = {}
    for case, result in zip(cases, results, strict=False):
        grouped.setdefault(case.group, []).append(result)

    breakdown: dict[str, Any] = {}
    for group, group_results in grouped.items():
        passed_count = sum(1 for result in group_results if result.passed)
        breakdown[group] = {
            "total": len(group_results),
            "passed_count": passed_count,
            "average_score": (
                sum(result.score for result in group_results) / len(group_results)
                if group_results
                else 0.0
            ),
        }
    return breakdown
