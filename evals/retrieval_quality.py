from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.metrics import aggregate_suite_metrics, metric_payload


@dataclass(frozen=True)
class RetrievalQualityCase:
    case_id: str
    query: str
    group: str = "generic"
    top_k: int = 3
    min_result_count: int = 1
    expected_top_chunk_id: str = ""
    expected_source_id: str = ""
    required_chunk_ids: tuple[str, ...] = ()
    forbidden_chunk_ids: tuple[str, ...] = ()
    forbidden_inactive_chunk_ids: tuple[str, ...] = ()
    hard_negative_chunk_ids: tuple[str, ...] = ()
    relevant_chunk_ids: tuple[str, ...] = ()
    no_answer: bool = False
    filters: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RetrievalQualityCase":
        relevant = tuple(value.get("relevant_chunk_ids", ()))
        required = tuple(value.get("required_chunk_ids", ()))
        expected_top = str(value.get("expected_top_chunk_id", ""))
        if not relevant:
            relevant = tuple(
                chunk_id
                for chunk_id in (expected_top, *required)
                if chunk_id
            )
            # Preserve order while deduplicating.
            seen: set[str] = set()
            ordered: list[str] = []
            for chunk_id in relevant:
                if chunk_id in seen:
                    continue
                seen.add(chunk_id)
                ordered.append(chunk_id)
            relevant = tuple(ordered)
        no_answer = bool(value.get("no_answer", False))
        if "min_result_count" in value:
            min_result_count = int(value["min_result_count"])
        else:
            # Empty results are the success path for no_answer cases.
            min_result_count = 0 if no_answer else 1
        return cls(
            case_id=str(value["case_id"]),
            query=str(value["query"]),
            group=str(value.get("group", "generic")),
            top_k=int(value.get("top_k", 3)),
            min_result_count=min_result_count,
            expected_top_chunk_id=expected_top,
            expected_source_id=str(value.get("expected_source_id", "")),
            required_chunk_ids=required,
            forbidden_chunk_ids=tuple(value.get("forbidden_chunk_ids", ())),
            forbidden_inactive_chunk_ids=tuple(
                value.get("forbidden_inactive_chunk_ids", ())
            ),
            hard_negative_chunk_ids=tuple(value.get("hard_negative_chunk_ids", ())),
            relevant_chunk_ids=relevant,
            no_answer=no_answer,
            filters=dict(value.get("filters", {})),
        )


@dataclass(frozen=True)
class RetrievalQualityResult:
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


def load_cases(path: str | Path) -> list[RetrievalQualityCase]:
    raw_cases = json.loads(Path(path).read_text(encoding="utf-8"))
    return [RetrievalQualityCase.from_mapping(item) for item in raw_cases]


def evaluate_search_payload(
    payload: dict[str, Any],
    case: RetrievalQualityCase,
) -> RetrievalQualityResult:
    results = _as_list(payload.get("results"))
    top_chunk_id = str(_item_value(results[0], "chunk_id")) if results else ""
    top_source_id = str(_item_value(results[0], "source_id")) if results else ""
    chunk_ids = [str(_item_value(item, "chunk_id")) for item in results if _item_value(item, "chunk_id")]
    source_ids = [str(_item_value(item, "source_id")) for item in results if _item_value(item, "source_id")]

    hard_neg_ok = True
    if case.hard_negative_chunk_ids and case.relevant_chunk_ids:
        relevant_set = set(case.relevant_chunk_ids)
        hard_set = set(case.hard_negative_chunk_ids)
        first_relevant = next(
            (index for index, chunk_id in enumerate(chunk_ids) if chunk_id in relevant_set),
            None,
        )
        first_hard = next(
            (index for index, chunk_id in enumerate(chunk_ids) if chunk_id in hard_set),
            None,
        )
        # Ordering constraint applies only when both sides appear in ranked results.
        # Missing relevants are covered by required/top checks separately.
        if first_relevant is not None and first_hard is not None and first_hard < first_relevant:
            hard_neg_ok = False

    inactive_forbidden = case.forbidden_inactive_chunk_ids or tuple(
        chunk_id
        for chunk_id in case.forbidden_chunk_ids
        if case.group in {"stale-block", "inactive"}
    )

    checks = {
        "min_result_count": len(results) >= case.min_result_count,
        "expected_top_chunk_id": not case.expected_top_chunk_id
        or (bool(results) and top_chunk_id == case.expected_top_chunk_id),
        "expected_source_id": not case.expected_source_id
        or (bool(results) and top_source_id == case.expected_source_id),
        "required_chunk_ids_present": set(case.required_chunk_ids).issubset(set(chunk_ids)),
        "forbidden_chunk_ids_absent": not set(case.forbidden_chunk_ids).intersection(
            set(chunk_ids)
        ),
        "hard_negative_not_above_relevant": hard_neg_ok,
        "inactive_forbidden_absent": not set(inactive_forbidden).intersection(set(chunk_ids)),
        # no_answer cases must stay empty; do not rely only on outer quality gates.
        "no_answer_empty_results": (not case.no_answer) or (len(results) == 0),
    }
    failures = tuple(name for name, passed in checks.items() if not passed)
    score = sum(1 for passed in checks.values() if passed) / len(checks)

    return RetrievalQualityResult(
        case_id=case.case_id,
        passed=not failures,
        score=score,
        checks=checks,
        failures=failures,
        details={
            "result_count": len(results),
            "chunk_ids": chunk_ids,
            "source_ids": source_ids,
        },
    )


def evaluate_search_suite(
    payloads_by_case_id: dict[str, dict[str, Any]],
    cases: list[RetrievalQualityCase],
) -> dict[str, Any]:
    if not cases:
        return {
            "passed": False,
            "total": 0,
            "passed_count": 0,
            "average_score": 0.0,
            "group_breakdown": {},
            "results": [],
            "quality_metrics": _empty_quality_metrics(),
        }
    raw_results = [
        evaluate_search_payload(payloads_by_case_id.get(case.case_id, {}), case)
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
        "quality_metrics": _retrieval_quality_metrics(payloads_by_case_id, cases),
    }


def _empty_quality_metrics() -> dict[str, Any]:
    empty = metric_payload(numerator=0.0, denominator=0)
    return {
        "scorable_case_count": 0,
        "hit_at_k": dict(empty),
        "mrr_at_k": dict(empty),
        "recall_at_k": dict(empty),
        "ndcg_at_k": dict(empty),
        "citation_precision": dict(empty),
        "citation_recall": dict(empty),
        "insufficient_status_accuracy": dict(empty),
        "stale_inactive_block_rate": dict(empty),
    }


def _retrieval_quality_metrics(
    payloads_by_case_id: dict[str, dict[str, Any]],
    cases: list[RetrievalQualityCase],
) -> dict[str, Any]:
    case_results: list[dict[str, Any]] = []
    for case in cases:
        payload = payloads_by_case_id.get(case.case_id, {})
        ranked_ids = [
            str(_item_value(item, "chunk_id"))
            for item in _as_list(payload.get("results"))[: max(case.top_k, 0)]
            if _item_value(item, "chunk_id")
        ]
        relevant_ids = list(case.relevant_chunk_ids)
        if not relevant_ids and case.expected_top_chunk_id:
            relevant_ids = [case.expected_top_chunk_id, *case.required_chunk_ids]
        elif not relevant_ids:
            relevant_ids = list(case.required_chunk_ids)

        inactive_forbidden = list(case.forbidden_inactive_chunk_ids)
        if not inactive_forbidden and case.group in {"stale-block", "inactive"}:
            inactive_forbidden = list(case.forbidden_chunk_ids)

        case_results.append(
            {
                "case_id": case.case_id,
                "no_answer": case.no_answer,
                "top_k": case.top_k,
                "relevant_chunk_ids": relevant_ids,
                "ranked_chunk_ids": ranked_ids,
                "forbidden_inactive_chunk_ids": inactive_forbidden,
                "cited_chunk_ids": [],
                "required_citation_chunk_ids": [],
                # Retrieval-only aggregation does not observe answer status.
                "expected_status": "",
                "evidence_status": "",
            }
        )

    return aggregate_suite_metrics(case_results, k=5)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _item_value(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _group_breakdown(
    cases: list[RetrievalQualityCase],
    results: list[RetrievalQualityResult],
) -> dict[str, Any]:
    grouped: dict[str, list[RetrievalQualityResult]] = {}
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
