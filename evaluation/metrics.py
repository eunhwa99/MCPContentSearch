from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re
import statistics
from typing import Any


TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")


def evaluate_retrieval_metrics(
    cases: Sequence[Any],
    results_by_query_id: Mapping[str, Sequence[Any]],
    *,
    indexed_chunks: Mapping[str, Any] | None = None,
    indexed_documents: Mapping[str, Any] | None = None,
    latencies_ms: Sequence[float] | None = None,
    near_duplicate_threshold: float = 0.9,
) -> dict[str, Any]:
    recall_values: dict[int, list[float]] = {1: [], 3: [], 5: []}
    document_recall_values: dict[int, list[float]] = {1: [], 3: [], 5: []}
    precision_values: dict[int, list[float]] = {3: [], 5: []}
    reciprocal_ranks: list[float] = []
    ndcg_values: list[float] = []
    source_filter_checks: list[bool] = []
    experience_filter_checks: list[bool] = []
    empty_result_checks: list[bool] = []

    for case in cases:
        query_id = str(_value(case, "query_id", ""))
        results = list(results_by_query_id.get(query_id, ()))
        result_chunk_ids = [
            str(_value(result, "chunk_id", "")) for result in results
        ]
        result_document_ids = [
            str(_value(result, "document_id", "")) for result in results
        ]
        expected_chunk_ids = {
            str(chunk_id)
            for chunk_id in _value(case, "expected_chunk_ids", ())
            if chunk_id
        }
        should_return_empty = bool(_value(case, "should_return_empty", False))
        empty_result_checks.append((not results) == should_return_empty)

        if expected_chunk_ids and not should_return_empty:
            for cutoff in recall_values:
                retrieved_unique = set(result_chunk_ids[:cutoff])
                recall_values[cutoff].append(
                    len(retrieved_unique.intersection(expected_chunk_ids))
                    / len(expected_chunk_ids)
                )
            for cutoff in precision_values:
                retrieved_unique = set(result_chunk_ids[:cutoff])
                precision_values[cutoff].append(
                    len(retrieved_unique.intersection(expected_chunk_ids)) / cutoff
                )
            reciprocal_ranks.append(
                _reciprocal_rank(result_chunk_ids, expected_chunk_ids)
            )

        expected_document_ids = {
            str(document_id)
            for document_id in _value(case, "expected_document_ids", ())
            if document_id
        }
        if expected_document_ids and not should_return_empty:
            for cutoff in document_recall_values:
                retrieved_unique = set(result_document_ids[:cutoff])
                document_recall_values[cutoff].append(
                    len(retrieved_unique.intersection(expected_document_ids))
                    / len(expected_document_ids)
                )

        graded_relevance = _mapping_value(case, "graded_relevance")
        positive_relevance = {
            str(chunk_id): int(relevance)
            for chunk_id, relevance in graded_relevance.items()
            if _numeric(relevance) and int(relevance) > 0
        }
        if positive_relevance and not should_return_empty:
            ndcg_values.append(
                _ndcg_at_k(result_chunk_ids, positive_relevance, cutoff=5)
            )

        allowed_sources = {
            str(value)
            for value in _value(case, "allowed_source_types", ())
            if value
        }
        allowed_experience = {
            str(value)
            for value in _value(case, "allowed_experience_types", ())
            if value
        }
        for result in results:
            if allowed_sources:
                source_filter_checks.append(
                    str(_value(result, "source_type", "")) in allowed_sources
                )
            if allowed_experience:
                experience_filter_checks.append(
                    str(_value(result, "experience_type", ""))
                    in allowed_experience
                )

    metrics: dict[str, Any] = {
        "recall_at_1": _average_or_none(recall_values[1]),
        "recall_at_3": _average_or_none(recall_values[3]),
        "recall_at_5": _average_or_none(recall_values[5]),
        "document_recall_at_1": _average_or_none(document_recall_values[1]),
        "document_recall_at_3": _average_or_none(document_recall_values[3]),
        "document_recall_at_5": _average_or_none(document_recall_values[5]),
        "precision_at_3": _average_or_none(precision_values[3]),
        "precision_at_5": _average_or_none(precision_values[5]),
        "mrr": _average_or_none(reciprocal_ranks),
        "ndcg_at_5": _average_or_none(ndcg_values),
        "source_type_filter_accuracy": _boolean_accuracy(source_filter_checks),
        "experience_type_filter_accuracy": _boolean_accuracy(
            experience_filter_checks
        ),
        "empty_result_accuracy": _boolean_accuracy(empty_result_checks),
        "metric_denominators": {
            "recall_at_1": len(recall_values[1]),
            "recall_at_3": len(recall_values[3]),
            "recall_at_5": len(recall_values[5]),
            "document_recall_at_1": len(document_recall_values[1]),
            "document_recall_at_3": len(document_recall_values[3]),
            "document_recall_at_5": len(document_recall_values[5]),
            "precision_at_3": len(precision_values[3]),
            "precision_at_5": len(precision_values[5]),
            "mrr": len(reciprocal_ranks),
            "ndcg_at_5": len(ndcg_values),
            "source_type_filter_accuracy": len(source_filter_checks),
            "experience_type_filter_accuracy": len(experience_filter_checks),
            "empty_result_accuracy": len(empty_result_checks),
        },
    }

    duplicate_metrics = calculate_duplicate_rates(
        results_by_query_id,
        near_duplicate_threshold=near_duplicate_threshold,
    )
    metrics.update(duplicate_metrics)
    metrics["metric_denominators"]["duplicate_result_rate"] = duplicate_metrics[
        "total_result_count"
    ]

    if indexed_chunks is not None and indexed_documents is not None:
        citation_metrics = calculate_citation_validity(
            results_by_query_id,
            indexed_chunks=indexed_chunks,
            indexed_documents=indexed_documents,
        )
        metrics.update(citation_metrics)
        metrics["metric_denominators"]["citation_validity_rate"] = (
            citation_metrics["total_count"]
        )
    else:
        metrics["citation_validity_rate"] = None
        metrics["metric_denominators"]["citation_validity_rate"] = 0

    latency_metrics = latency_summary_ms(latencies_ms or ())
    metrics.update(latency_metrics)
    metrics["metric_denominators"]["mean_latency_ms"] = latency_metrics[
        "latency_sample_count"
    ]
    metrics["metric_denominators"]["p50_latency_ms"] = latency_metrics[
        "latency_sample_count"
    ]
    metrics["metric_denominators"]["p95_latency_ms"] = latency_metrics[
        "latency_sample_count"
    ]
    return metrics


def calculate_citation_validity(
    results_by_query_id: Mapping[str, Sequence[Any]],
    *,
    indexed_chunks: Mapping[str, Any],
    indexed_documents: Mapping[str, Any],
) -> dict[str, Any]:
    invalid_citations: list[dict[str, str]] = []
    valid_count = 0
    total_count = 0

    for query_id, results in results_by_query_id.items():
        for result in results:
            total_count += 1
            chunk_id = str(_value(result, "chunk_id", ""))
            document_id = str(_value(result, "document_id", ""))
            chunk = indexed_chunks.get(chunk_id)
            if chunk is None:
                invalid_citations.append(
                    _citation_failure(query_id, chunk_id, "missing_chunk")
                )
                continue

            indexed_document_id = str(_value(chunk, "document_id", ""))
            if not document_id or document_id != indexed_document_id:
                invalid_citations.append(
                    _citation_failure(query_id, chunk_id, "document_mismatch")
                )
                continue
            if document_id not in indexed_documents:
                invalid_citations.append(
                    _citation_failure(query_id, chunk_id, "missing_document")
                )
                continue

            exact_quote = str(_value(result, "exact_quote", ""))
            indexed_content = str(
                _value(chunk, "content", _value(chunk, "exact_quote", ""))
            )
            if not exact_quote or exact_quote not in indexed_content:
                invalid_citations.append(
                    _citation_failure(query_id, chunk_id, "quote_not_in_source")
                )
                continue

            if not _section_metadata_matches(result, chunk):
                invalid_citations.append(
                    _citation_failure(query_id, chunk_id, "section_mismatch")
                )
                continue
            valid_count += 1

    return {
        "citation_validity_rate": (
            valid_count / total_count if total_count else None
        ),
        "valid_count": valid_count,
        "total_count": total_count,
        "invalid_citations": invalid_citations,
    }


def calculate_duplicate_rates(
    results_by_query_id: Mapping[str, Sequence[Any]],
    *,
    near_duplicate_threshold: float = 0.9,
) -> dict[str, Any]:
    if not 0.0 <= near_duplicate_threshold <= 1.0:
        raise ValueError("near_duplicate_threshold must be between 0 and 1")

    exact_duplicate_count = 0
    near_duplicate_count = 0
    total_result_count = 0
    for results in results_by_query_id.values():
        accepted: list[tuple[str, str, set[str]]] = []
        for result in results:
            total_result_count += 1
            chunk_id = str(_value(result, "chunk_id", ""))
            quote = str(_value(result, "exact_quote", ""))
            normalized_quote = _normalize_quote(quote)
            tokens = set(TOKEN_RE.findall(normalized_quote))

            if any(
                (chunk_id and chunk_id == previous_chunk_id)
                or (normalized_quote and normalized_quote == previous_quote)
                for previous_chunk_id, previous_quote, _ in accepted
            ):
                exact_duplicate_count += 1
                continue

            if tokens and any(
                _jaccard(tokens, previous_tokens) >= near_duplicate_threshold
                for _, _, previous_tokens in accepted
                if previous_tokens
            ):
                near_duplicate_count += 1
                continue

            accepted.append((chunk_id, normalized_quote, tokens))

    duplicate_count = exact_duplicate_count + near_duplicate_count
    return {
        "exact_duplicate_result_rate": (
            exact_duplicate_count / total_result_count
            if total_result_count
            else None
        ),
        "near_duplicate_result_rate": (
            near_duplicate_count / total_result_count
            if total_result_count
            else None
        ),
        "duplicate_result_rate": (
            duplicate_count / total_result_count if total_result_count else None
        ),
        "exact_duplicate_count": exact_duplicate_count,
        "near_duplicate_count": near_duplicate_count,
        "total_result_count": total_result_count,
    }


def latency_summary_ms(latencies_ms: Sequence[float]) -> dict[str, Any]:
    values = [float(value) for value in latencies_ms]
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("latencies must be finite non-negative numbers")
    if not values:
        return {
            "mean_latency_ms": None,
            "p50_latency_ms": None,
            "p95_latency_ms": None,
            "latency_sample_count": 0,
        }
    ordered = sorted(values)
    return {
        "mean_latency_ms": float(statistics.fmean(ordered)),
        "p50_latency_ms": _nearest_rank_percentile(ordered, 0.50),
        "p95_latency_ms": _nearest_rank_percentile(ordered, 0.95),
        "latency_sample_count": len(ordered),
    }


def calculate_ingestion_metrics(
    *,
    attempted_documents: int = 0,
    parsed_documents: int = 0,
    unchanged_documents: int = 0,
    changed_documents: int = 0,
    reembedded_unchanged_documents: int = 0,
    full_ingestion_latencies_ms: Sequence[float] = (),
    incremental_update_latencies_ms: Sequence[float] = (),
) -> dict[str, Any]:
    """Calculate ingestion metrics only from observed counters and timings."""
    counters = {
        "attempted_documents": attempted_documents,
        "parsed_documents": parsed_documents,
        "unchanged_documents": unchanged_documents,
        "changed_documents": changed_documents,
        "reembedded_unchanged_documents": reembedded_unchanged_documents,
    }
    for name, value in counters.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if parsed_documents > attempted_documents:
        raise ValueError("parsed_documents cannot exceed attempted_documents")
    if unchanged_documents + changed_documents > attempted_documents:
        raise ValueError(
            "unchanged_documents plus changed_documents cannot exceed "
            "attempted_documents"
        )
    if reembedded_unchanged_documents > unchanged_documents:
        raise ValueError(
            "reembedded_unchanged_documents cannot exceed unchanged_documents"
        )

    full = _validated_latencies(full_ingestion_latencies_ms)
    incremental = _validated_latencies(incremental_update_latencies_ms)
    return {
        "parsing_success_rate": (
            parsed_documents / attempted_documents if attempted_documents else None
        ),
        "unchanged_document_skip_rate": (
            unchanged_documents / attempted_documents
            if attempted_documents
            else None
        ),
        "unnecessary_reembedding_rate": (
            reembedded_unchanged_documents / unchanged_documents
            if unchanged_documents
            else None
        ),
        "full_ingestion_latency_ms": _average_or_none(full),
        "incremental_update_latency_ms": _average_or_none(incremental),
        "metric_denominators": {
            "parsing_success_rate": attempted_documents,
            "unchanged_document_skip_rate": attempted_documents,
            "unnecessary_reembedding_rate": unchanged_documents,
            "full_ingestion_latency_ms": len(full),
            "incremental_update_latency_ms": len(incremental),
        },
    }


def _validated_latencies(values: Sequence[float]) -> list[float]:
    normalized = [float(value) for value in values]
    if any(not math.isfinite(value) or value < 0 for value in normalized):
        raise ValueError("latencies must be finite non-negative numbers")
    return normalized


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _mapping_value(item: Any, key: str) -> Mapping[Any, Any]:
    value = _value(item, key, {})
    return value if isinstance(value, Mapping) else {}


def _numeric(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _average_or_none(values: Sequence[float]) -> float | None:
    return float(statistics.fmean(values)) if values else None


def _boolean_accuracy(values: Sequence[bool]) -> float | None:
    return (
        sum(1 for value in values if value) / len(values)
        if values
        else None
    )


def _reciprocal_rank(
    result_chunk_ids: Sequence[str], expected_chunk_ids: set[str]
) -> float:
    for rank, chunk_id in enumerate(result_chunk_ids, start=1):
        if chunk_id in expected_chunk_ids:
            return 1.0 / rank
    return 0.0


def _ndcg_at_k(
    result_chunk_ids: Sequence[str], graded_relevance: Mapping[str, int], cutoff: int
) -> float:
    seen: set[str] = set()
    gains: list[int] = []
    for chunk_id in result_chunk_ids[:cutoff]:
        if chunk_id in seen:
            gains.append(0)
            continue
        seen.add(chunk_id)
        gains.append(int(graded_relevance.get(chunk_id, 0)))
    ideal_gains = sorted(graded_relevance.values(), reverse=True)[:cutoff]
    ideal_dcg = _discounted_gain(ideal_gains)
    return _discounted_gain(gains) / ideal_dcg if ideal_dcg else 0.0


def _discounted_gain(relevance_values: Sequence[int]) -> float:
    return sum(
        ((2**relevance) - 1) / math.log2(rank + 1)
        for rank, relevance in enumerate(relevance_values, start=1)
    )


def _section_metadata_matches(result: Any, chunk: Any) -> bool:
    for key in ("section_title", "parent_section_title"):
        result_value = str(_value(result, key, "") or "")
        indexed_value = str(_value(chunk, key, "") or "")
        if result_value != indexed_value:
            return False
    return True


def _citation_failure(query_id: Any, chunk_id: str, reason: str) -> dict[str, str]:
    return {
        "query_id": str(query_id),
        "chunk_id": chunk_id,
        "reason": reason,
    }


def _normalize_quote(value: str) -> str:
    return " ".join(value.casefold().split())


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left.union(right)
    return len(left.intersection(right)) / len(union) if union else 0.0


def _nearest_rank_percentile(ordered: Sequence[float], percentile: float) -> float:
    rank = max(1, math.ceil(percentile * len(ordered)))
    return float(ordered[rank - 1])
