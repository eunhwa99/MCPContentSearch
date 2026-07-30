from __future__ import annotations

from typing import Any


def render_rag_report(summary: dict[str, Any]) -> str:
    fixture_metrics = summary.get("fixture_metrics") or {}
    answer_metrics = summary.get("answer_metrics")
    live_metrics = summary.get("live_metrics")
    retrieval_config = summary.get("retrieval_config") or {}
    failures = summary.get("failures") or []
    group_breakdown = summary.get("group_breakdown") or {}
    latency = summary.get("latency_ms") or {}
    baseline_delta = summary.get("baseline_delta")
    limitations = summary.get("limitations") or [
        "Fixture lexical results are not production embedding performance.",
    ]

    lines = [
        "# RAG Evaluation Report",
        "",
        f"- Dataset version: `{summary.get('dataset_version', '')}`",
        f"- Retrieval config: `{retrieval_config}`",
        "",
        "## Fixture / deterministic lexical retrieval metrics",
        "",
        _metrics_table(fixture_metrics, ranking_only=True),
        "",
    ]
    if answer_metrics:
        lines.extend(
            [
                "## Fixture citation / status metrics",
                "",
                _metrics_table(answer_metrics, citation_only=True),
                "",
            ]
        )
    else:
        # Keep citation rows when provided inside fixture_metrics, but label clearly.
        citation_subset = {
            key: fixture_metrics[key]
            for key in (
                "citation_precision",
                "citation_recall",
                "insufficient_status_accuracy",
                "stale_inactive_block_rate",
            )
            if key in fixture_metrics
        }
        if citation_subset:
            citation_subset["scorable_case_count"] = fixture_metrics.get(
                "citation_scorable_case_count",
                fixture_metrics.get("scorable_case_count", ""),
            )
            lines.extend(
                [
                    "## Fixture citation / blocking metrics",
                    "",
                    _metrics_table(citation_subset, citation_only=True),
                    "",
                ]
            )

    lines.extend(["## Live embedding metrics", ""])
    if live_metrics:
        lines.append(_metrics_table(live_metrics))
    else:
        lines.append(
            "Live embedding evaluation was not executed "
            "(requires explicit `--live` and a positive `--max-budget`)."
        )

    lines.extend(["", "## Group breakdown", ""])
    if group_breakdown:
        lines.append("| Group | Passed | Total |")
        lines.append("| --- | ---: | ---: |")
        for group, stats in sorted(group_breakdown.items()):
            lines.append(
                f"| {group} | {stats.get('passed_count', 0)} | {stats.get('total', 0)} |"
            )
    else:
        lines.append("No group breakdown available.")

    lines.extend(
        [
            "",
            "## Latency",
            "",
            f"- Average ms: {_fmt(latency.get('average'))}",
            f"- P95 ms: {_fmt(latency.get('p95'))}",
            "",
            "## Failures",
            "",
        ]
    )
    if failures:
        for failure in failures:
            reasons = failure.get("reasons") or failure.get("failures") or []
            reason_text = ", ".join(str(item) for item in reasons) if reasons else "n/a"
            lines.append(f"- `{failure.get('case_id', '')}`: {reason_text}")
    else:
        lines.append("- None")

    lines.extend(["", "## Baseline delta", ""])
    if baseline_delta:
        for key, value in baseline_delta.items():
            lines.append(f"- {key}: {_fmt(value)}")
    else:
        lines.append("- No prior baseline provided.")

    lines.extend(["", "## Limitations", ""])
    for item in limitations:
        lines.append(f"- {item}")
    if "production" not in " ".join(str(item).lower() for item in limitations):
        lines.append(
            "- Fixture lexical results are not production embedding performance."
        )

    return "\n".join(lines) + "\n"


def _metrics_table(
    metrics: dict[str, Any],
    *,
    ranking_only: bool = False,
    citation_only: bool = False,
) -> str:
    rows = [
        "| Metric | Value | Numerator | Denominator | Scorable |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    scorable = metrics.get("scorable_case_count", "")
    ranking_names = [
        ("Hit@K", "hit_at_k"),
        ("MRR@K", "mrr_at_k"),
        ("Recall@K", "recall_at_k"),
        ("nDCG@K", "ndcg_at_k"),
    ]
    citation_names = [
        ("Citation precision", "citation_precision"),
        ("Citation recall", "citation_recall"),
        ("Insufficient status accuracy", "insufficient_status_accuracy"),
        ("Stale/inactive block rate", "stale_inactive_block_rate"),
    ]
    if ranking_only:
        metric_names = ranking_names
    elif citation_only:
        metric_names = citation_names
    else:
        metric_names = ranking_names + citation_names

    for label, key in metric_names:
        payload = metrics.get(key)
        if not isinstance(payload, dict):
            # Accept hit_at_5 aliases.
            alt = key.replace("_at_k", "_at_5")
            payload = metrics.get(alt)
        if not isinstance(payload, dict):
            continue
        row_scorable = payload.get("denominator", scorable)
        rows.append(
            "| {label} | {value} | {numerator} | {denominator} | {scorable} |".format(
                label=label,
                value=_fmt(payload.get("value")),
                numerator=_fmt(payload.get("numerator")),
                denominator=payload.get("denominator", ""),
                scorable=row_scorable,
            )
        )
    if len(rows) == 2:
        rows.append("| (none) | N/A | 0 | 0 | 0 |")
    return "\n".join(rows)


def _fmt(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)
