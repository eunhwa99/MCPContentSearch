from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.metrics import aggregate_suite_metrics, metric_payload


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
        empty = metric_payload(numerator=0.0, denominator=0)
        return {
            "passed": False,
            "total": 0,
            "passed_count": 0,
            "average_score": 0.0,
            "group_breakdown": {},
            "results": [],
            "quality_metrics": {
                "scorable_case_count": 0,
                "citation_precision": dict(empty),
                "citation_recall": dict(empty),
                "insufficient_status_accuracy": dict(empty),
            },
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
        "results": [result.as_dict() for result in raw_results],
        "quality_metrics": _answer_quality_metrics(payloads_by_case_id, cases),
    }


def _answer_quality_metrics(
    payloads_by_case_id: dict[str, dict[str, Any]],
    cases: list[AnswerQualityCase],
) -> dict[str, Any]:
    case_results: list[dict[str, Any]] = []
    for case in cases:
        payload = payloads_by_case_id.get(case.case_id, {})
        citations = _as_list(payload.get("citations"))
        cited = sorted(_citation_chunk_ids(citations))
        case_results.append(
            {
                "case_id": case.case_id,
                "no_answer": case.expected_status == "insufficient",
                "relevant_chunk_ids": [],
                "ranked_chunk_ids": [],
                "forbidden_inactive_chunk_ids": [],
                "cited_chunk_ids": cited,
                "required_citation_chunk_ids": list(case.required_citation_chunk_ids),
                "expected_status": case.expected_status,
                "evidence_status": str(
                    payload.get("evidence_status") or payload.get("status") or ""
                ),
            }
        )
    metrics = aggregate_suite_metrics(case_results, k=5)
    citation_scorable = int(metrics["citation_recall"]["denominator"] or 0)
    return {
        "scorable_case_count": citation_scorable,
        "citation_precision": metrics["citation_precision"],
        "citation_recall": metrics["citation_recall"],
        "insufficient_status_accuracy": metrics["insufficient_status_accuracy"],
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
