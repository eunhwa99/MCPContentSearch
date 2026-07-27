from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RetrievalQualityCase":
        return cls(
            case_id=str(value["case_id"]),
            query=str(value["query"]),
            group=str(value.get("group", "generic")),
            top_k=int(value.get("top_k", 3)),
            min_result_count=int(value.get("min_result_count", 1)),
            expected_top_chunk_id=str(value.get("expected_top_chunk_id", "")),
            expected_source_id=str(value.get("expected_source_id", "")),
            required_chunk_ids=tuple(value.get("required_chunk_ids", ())),
            forbidden_chunk_ids=tuple(value.get("forbidden_chunk_ids", ())),
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

    checks = {
        "min_result_count": len(results) >= case.min_result_count,
        "expected_top_chunk_id": not case.expected_top_chunk_id
        or (bool(results) and top_chunk_id == case.expected_top_chunk_id),
        "expected_source_id": not case.expected_source_id
        or (bool(results) and top_source_id == case.expected_source_id),
        "required_chunk_ids_present": set(case.required_chunk_ids).issubset(set(chunk_ids)),
        "forbidden_chunk_ids_absent": not set(case.forbidden_chunk_ids).intersection(set(chunk_ids)),
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
            "quality_metrics": _retrieval_quality_metrics({}, []),
            "results": [],
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
        "quality_metrics": _retrieval_quality_metrics(payloads_by_case_id, cases),
        "results": [result.as_dict() for result in raw_results],
    }


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _item_value(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _retrieval_quality_metrics(
    payloads_by_case_id: dict[str, dict[str, Any]],
    cases: list[RetrievalQualityCase],
) -> dict[str, Any]:
    case_values: list[dict[str, float]] = []
    for case in cases:
        relevant_ids = {
            chunk_id for chunk_id in case.required_chunk_ids if chunk_id
        }
        if case.expected_top_chunk_id:
            relevant_ids.add(case.expected_top_chunk_id)
        if not relevant_ids:
            continue

        payload = payloads_by_case_id.get(case.case_id, {})
        ranked_ids = [
            str(_item_value(item, "chunk_id") or "")
            for item in _as_list(payload.get("results"))[: max(case.top_k, 0)]
        ]
        case_values.append(_ranking_values(ranked_ids, relevant_ids, case.top_k))

    scorable_count = len(case_values)
    return {
        "scorable_case_count": scorable_count,
        "unscorable_case_count": len(cases) - scorable_count,
        "cutoff": "case_top_k",
        "hit_rate_at_k": _aggregate_metric(case_values, "hit"),
        "mrr_at_k": _aggregate_metric(case_values, "reciprocal_rank"),
        "recall_at_k": _aggregate_metric(case_values, "recall"),
        "ndcg_at_k": _aggregate_metric(case_values, "ndcg"),
    }


def _ranking_values(
    ranked_ids: list[str],
    relevant_ids: set[str],
    top_k: int,
) -> dict[str, float]:
    seen_ids: set[str] = set()
    first_relevant_rank = 0
    relevant_retrieved: set[str] = set()
    discounted_gain = 0.0

    for rank, chunk_id in enumerate(ranked_ids, start=1):
        if chunk_id in seen_ids:
            continue
        seen_ids.add(chunk_id)
        if chunk_id not in relevant_ids:
            continue
        relevant_retrieved.add(chunk_id)
        if not first_relevant_rank:
            first_relevant_rank = rank
        discounted_gain += 1.0 / math.log2(rank + 1)

    ideal_relevant_count = min(len(relevant_ids), max(top_k, 0))
    ideal_discounted_gain = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, ideal_relevant_count + 1)
    )
    return {
        "hit": float(bool(relevant_retrieved)),
        "reciprocal_rank": (
            1.0 / first_relevant_rank if first_relevant_rank else 0.0
        ),
        "recall": len(relevant_retrieved) / len(relevant_ids),
        "ndcg": (
            discounted_gain / ideal_discounted_gain
            if ideal_discounted_gain
            else 0.0
        ),
    }


def _aggregate_metric(
    case_values: list[dict[str, float]],
    metric_name: str,
) -> dict[str, float | int | None]:
    denominator = len(case_values)
    numerator = sum(values[metric_name] for values in case_values)
    return {
        "value": numerator / denominator if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
    }


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
