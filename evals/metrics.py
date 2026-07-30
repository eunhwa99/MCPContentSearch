from __future__ import annotations

import math
from typing import Any


def metric_payload(*, numerator: float, denominator: int) -> dict[str, float | int | None]:
    return {
        "value": (numerator / denominator) if denominator else None,
        "numerator": float(numerator),
        "denominator": int(denominator),
    }


def compute_ranking_metrics(
    ranked_chunk_ids: list[str],
    relevant_chunk_ids: list[str] | set[str] | tuple[str, ...],
    k: int,
) -> dict[str, float]:
    relevant_ids = {str(item) for item in relevant_chunk_ids if item}
    if not relevant_ids:
        return {"hit": 0.0, "mrr": 0.0, "recall": 0.0, "ndcg": 0.0}

    top = [str(item) for item in ranked_chunk_ids[: max(k, 0)]]
    seen: set[str] = set()
    first_relevant_rank = 0
    relevant_retrieved: set[str] = set()
    discounted_gain = 0.0

    for rank, chunk_id in enumerate(top, start=1):
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        if chunk_id not in relevant_ids:
            continue
        relevant_retrieved.add(chunk_id)
        if not first_relevant_rank:
            first_relevant_rank = rank
        discounted_gain += 1.0 / math.log2(rank + 1)

    ideal_count = min(len(relevant_ids), max(k, 0))
    ideal_discounted_gain = sum(
        1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1)
    )
    return {
        "hit": float(bool(relevant_retrieved)),
        "mrr": (1.0 / first_relevant_rank) if first_relevant_rank else 0.0,
        "recall": len(relevant_retrieved) / len(relevant_ids),
        "ndcg": (
            discounted_gain / ideal_discounted_gain if ideal_discounted_gain else 0.0
        ),
    }


def aggregate_suite_metrics(
    case_results: list[dict[str, Any]],
    k: int = 5,
) -> dict[str, Any]:
    ranking_values: list[dict[str, float]] = []
    citation_precision_hits = 0.0
    citation_precision_total = 0
    citation_recall_hits = 0.0
    citation_recall_total = 0
    insufficient_hits = 0.0
    insufficient_total = 0
    stale_block_hits = 0.0
    stale_block_total = 0

    for case in case_results:
        relevant = [str(item) for item in case.get("relevant_chunk_ids") or [] if item]
        ranked = [str(item) for item in case.get("ranked_chunk_ids") or [] if item]
        no_answer = bool(case.get("no_answer"))
        if relevant and not no_answer:
            ranking_values.append(
                compute_ranking_metrics(ranked, relevant, int(case.get("top_k") or k))
            )

        required_citations = [
            str(item) for item in case.get("required_citation_chunk_ids") or [] if item
        ]
        cited = [str(item) for item in case.get("cited_chunk_ids") or [] if item]
        if required_citations:
            required_set = set(required_citations)
            cited_set = set(cited)
            citation_recall_hits += len(required_set & cited_set) / len(required_set)
            citation_recall_total += 1
            if cited:
                citation_precision_hits += len(cited_set & required_set) / len(cited_set)
                citation_precision_total += 1

        expected_status = str(case.get("expected_status") or "")
        if expected_status == "insufficient":
            insufficient_total += 1
            if str(case.get("evidence_status") or "") == "insufficient":
                insufficient_hits += 1.0

        inactive_forbidden = [
            str(item)
            for item in case.get("forbidden_inactive_chunk_ids") or []
            if item
        ]
        if inactive_forbidden:
            stale_block_total += 1
            ranked_set = set(ranked)
            if not ranked_set.intersection(inactive_forbidden):
                stale_block_hits += 1.0

    return {
        "scorable_case_count": len(ranking_values),
        "hit_at_k": _aggregate(ranking_values, "hit"),
        "mrr_at_k": _aggregate(ranking_values, "mrr"),
        "recall_at_k": _aggregate(ranking_values, "recall"),
        "ndcg_at_k": _aggregate(ranking_values, "ndcg"),
        "citation_precision": metric_payload(
            numerator=citation_precision_hits,
            denominator=citation_precision_total,
        ),
        "citation_recall": metric_payload(
            numerator=citation_recall_hits,
            denominator=citation_recall_total,
        ),
        "insufficient_status_accuracy": metric_payload(
            numerator=insufficient_hits,
            denominator=insufficient_total,
        ),
        "stale_inactive_block_rate": metric_payload(
            numerator=stale_block_hits,
            denominator=stale_block_total,
        ),
    }


def _aggregate(
    case_values: list[dict[str, float]],
    metric_name: str,
) -> dict[str, float | int | None]:
    denominator = len(case_values)
    numerator = sum(values[metric_name] for values in case_values)
    return metric_payload(numerator=numerator, denominator=denominator)
