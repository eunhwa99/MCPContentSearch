from __future__ import annotations

from typing import Any


def render_portfolio_report(summary: dict[str, Any]) -> str:
    retrieval_suite = summary["retrieval_suite"]
    answer_suite = summary["answer_suite"]
    retrieval_metrics = retrieval_suite["quality_metrics"]
    answer_metrics = answer_suite["quality_metrics"]

    lines = [
        "# ContextWiki Deterministic Evaluation Report",
        "",
        f"Overall result: **{_status(summary['passed'])}**",
        "",
        "## Fixture snapshot",
        "",
        "| Suite | Passed cases | Total cases | Suite result |",
        "| --- | ---: | ---: | --- |",
        _suite_row("Retrieval", retrieval_suite),
        _suite_row("Answer quality", answer_suite),
        "",
        "## Retrieval quality",
        "",
        (
            "Ranking metrics use only cases with explicit positive chunk labels. "
            f"{retrieval_metrics['scorable_case_count']} of "
            f"{retrieval_suite['total']} cases are scorable; each case is measured "
            "at its configured `top_k`."
        ),
        "",
        "| Metric | Value | Numerator | Denominator |",
        "| --- | ---: | ---: | ---: |",
        _metric_row("Hit rate at k", retrieval_metrics["hit_rate_at_k"]),
        _metric_row("Mean reciprocal rank at k", retrieval_metrics["mrr_at_k"]),
        _metric_row("Recall at k", retrieval_metrics["recall_at_k"]),
        _metric_row("nDCG at k", retrieval_metrics["ndcg_at_k"]),
        "",
        "## Answer quality",
        "",
        (
            "Status accuracy is case-based. Required-citation recall counts labeled "
            "required chunk IDs, citation coverage counts used chunk IDs backed by "
            "a citation, and insufficient-status accuracy uses cases labeled with "
            "the expected `insufficient` status."
        ),
        "",
        "| Metric | Value | Numerator | Denominator | Scorable cases |",
        "| --- | ---: | ---: | ---: | ---: |",
        _answer_metric_row(
            "Status accuracy",
            answer_metrics,
            "status_accuracy",
        ),
        _answer_metric_row(
            "Required-citation recall",
            answer_metrics,
            "required_citation_recall",
        ),
        _answer_metric_row(
            "Citation coverage",
            answer_metrics,
            "citation_coverage",
        ),
        _answer_metric_row(
            "Insufficient-status accuracy",
            answer_metrics,
            "insufficient_status_accuracy",
        ),
        "",
        "## Interpretation boundaries",
        "",
        "- This report measures bundled, deterministic fixture documents and cases.",
        (
            "- The run uses a temporary SQLite database and a deterministic lexical "
            "fixture retriever through the retained search and answer services."
        ),
        (
            "- It does not call live source APIs, embedding providers, or LLMs, and "
            "it does not inspect private Chroma or SQLite data."
        ),
        (
            "- These results are regression evidence for the fixture contracts, not "
            "a production or real-world benchmark."
        ),
        (
            "- Latency is intentionally excluded from this stable report. Optional "
            "runtime measurements are written separately to `runtime_metrics.json`."
        ),
    ]
    lines.extend(_failure_section(retrieval_suite, answer_suite))
    return "\n".join(lines) + "\n"


def _status(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


def _suite_row(name: str, suite: dict[str, Any]) -> str:
    return (
        f"| {name} | {suite['passed_count']} | {suite['total']} | "
        f"{_status(suite['passed'])} |"
    )


def _metric_row(name: str, metric: dict[str, Any]) -> str:
    return (
        f"| {name} | {_format_value(metric['value'])} | "
        f"{_format_number(metric['numerator'])} | {metric['denominator']} |"
    )


def _answer_metric_row(
    name: str,
    quality_metrics: dict[str, Any],
    metric_name: str,
) -> str:
    metric = quality_metrics[metric_name]
    scorable_count = quality_metrics["scorable_case_counts"][metric_name]
    return (
        f"| {name} | {_format_value(metric['value'])} | "
        f"{_format_number(metric['numerator'])} | {metric['denominator']} | "
        f"{scorable_count} |"
    )


def _format_value(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.4f}"


def _format_number(value: Any) -> str:
    numeric_value = float(value)
    return (
        str(int(numeric_value))
        if numeric_value.is_integer()
        else f"{numeric_value:.4f}"
    )


def _failure_section(
    retrieval_suite: dict[str, Any],
    answer_suite: dict[str, Any],
) -> list[str]:
    failed_retrieval = [
        result["case_id"] for result in retrieval_suite["results"] if not result["passed"]
    ]
    failed_answers = [
        result["case_id"] for result in answer_suite["results"] if not result["passed"]
    ]
    if not failed_retrieval and not failed_answers:
        return []

    lines = ["", "## Failed fixture cases", ""]
    if failed_retrieval:
        lines.append(f"- Retrieval: {', '.join(failed_retrieval)}")
    if failed_answers:
        lines.append(f"- Answer quality: {', '.join(failed_answers)}")
    return lines
