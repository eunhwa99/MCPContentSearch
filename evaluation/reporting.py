from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from evaluation.secure_output import (
    require_private_output_destination,
    secure_atomic_write_text,
)

FIXTURE_DISCLAIMER = "TEST FIXTURE — NOT PRODUCT PERFORMANCE"
PRIVATE_DISCLAIMER = "AI-LABELED PRIVATE BENCHMARK — REQUIRES HUMAN REVIEW"
AI_REVIEWED_DISCLAIMER = "AI-LABELED PRIVATE BENCHMARK — HUMAN REVIEWED"
HUMAN_REVIEWED_DISCLAIMER = "HUMAN-REVIEWED PRIVATE BENCHMARK"
LABEL_SOURCES = frozenset(
    {
        "deterministic_fixture",
        "ai_generated_unreviewed",
        "ai_generated_reviewed",
        "human_reviewed",
    }
)
METRIC_LABELS = {
    "recall_at_1": "Recall@1",
    "recall_at_3": "Recall@3",
    "recall_at_5": "Recall@5",
    "document_recall_at_1": "Document Recall@1",
    "document_recall_at_3": "Document Recall@3",
    "document_recall_at_5": "Document Recall@5",
    "precision_at_3": "Precision@3",
    "precision_at_5": "Precision@5",
    "mrr": "MRR",
    "ndcg_at_5": "nDCG@5",
    "duplicate_result_rate": "Duplicate-result rate",
    "citation_validity_rate": "Citation-validity rate",
    "source_type_filter_accuracy": "Source-type filter accuracy",
    "experience_type_filter_accuracy": "Experience-type filter accuracy",
    "empty_result_accuracy": "Empty-result accuracy",
    "mean_latency_ms": "Mean latency (ms)",
    "p50_latency_ms": "p50 latency (ms)",
    "p95_latency_ms": "p95 latency (ms)",
}


def build_report(
    *,
    dataset_name: str,
    label_source: str,
    dataset_size: int,
    configuration: dict[str, Any],
    metrics: dict[str, Any],
    failures: list[dict[str, Any]],
    git_identifier: str,
    timestamp: str,
) -> dict[str, Any]:
    if label_source not in LABEL_SOURCES:
        raise ValueError(f"unsupported label_source: {label_source}")
    if not dataset_name.strip():
        raise ValueError("dataset_name must be non-empty")
    if dataset_size < 0:
        raise ValueError("dataset_size must be non-negative")
    if not git_identifier.strip():
        raise ValueError("git_identifier must be non-empty")
    if not timestamp.strip():
        raise ValueError("timestamp must be non-empty")

    disclaimer = {
        "deterministic_fixture": FIXTURE_DISCLAIMER,
        "ai_generated_unreviewed": PRIVATE_DISCLAIMER,
        "ai_generated_reviewed": AI_REVIEWED_DISCLAIMER,
        "human_reviewed": HUMAN_REVIEWED_DISCLAIMER,
    }[label_source]
    return {
        "report_version": 2,
        "disclaimer": disclaimer,
        "dataset": {
            "name": dataset_name,
            "label_source": label_source,
            "size": dataset_size,
        },
        "configuration": deepcopy(configuration),
        "git_identifier": git_identifier,
        "timestamp": timestamp,
        "metrics": deepcopy(metrics),
        "failures": deepcopy(failures),
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    dataset = report.get("dataset", {})
    lines = [
        "# Retrieval Evaluation Report",
        "",
        f"**{report.get('disclaimer', '')}**",
        "",
        "## Provenance",
        "",
        f"- Dataset: `{dataset.get('name', '')}`",
        f"- Label source: `{dataset.get('label_source', '')}`",
        f"- Dataset size: `{dataset.get('size', 0)}`",
        f"- Git/tree identifier: `{report.get('git_identifier', '')}`",
        f"- Timestamp: `{report.get('timestamp', '')}`",
        f"- Status: `{report.get('status', 'measured')}`",
    ]
    input_digests = report.get("input_digests", {})
    lines.extend(["", "## Input content digests", ""])
    if isinstance(input_digests, dict) and input_digests:
        for key in sorted(input_digests):
            lines.append(f"- `{key}`: `{_markdown_value(input_digests[key])}`")
    else:
        lines.append("- Not recorded")

    lines.extend(["", "## Configuration", ""])
    configuration = report.get("configuration", {})
    if isinstance(configuration, dict) and configuration:
        for key in sorted(configuration):
            lines.append(f"- `{key}`: `{_markdown_value(configuration[key])}`")
    else:
        lines.append("- None recorded")

    execution_path = report.get("execution_path", {})
    lines.extend(["", "## Execution path", ""])
    if isinstance(execution_path, dict) and execution_path:
        for key in sorted(execution_path):
            lines.append(
                f"- `{key}`: `{_markdown_value(execution_path[key])}`"
            )
    else:
        lines.append("- Not recorded")

    lines.extend(["", "## Metrics", ""])
    metrics = report.get("metrics", {})
    if isinstance(metrics, dict) and metrics:
        for key in _ordered_metric_keys(metrics):
            if key == "metric_denominators":
                continue
            label = METRIC_LABELS.get(key, key)
            lines.append(f"- {label}: `{_markdown_value(metrics[key])}`")
    else:
        lines.append("- No measured metrics")

    ingestion_metrics = report.get("ingestion_metrics", {})
    lines.extend(["", "## Ingestion metrics", ""])
    if isinstance(ingestion_metrics, dict) and ingestion_metrics:
        for key in sorted(ingestion_metrics):
            lines.append(
                f"- `{key}`: `{_markdown_value(ingestion_metrics[key])}`"
            )
    else:
        lines.append("- No ingestion measurements")
    ingestion_note = report.get("ingestion_metrics_note")
    if isinstance(ingestion_note, str) and ingestion_note:
        lines.append(f"- Note: {ingestion_note}")

    resource_cost = report.get("resource_cost", {})
    lines.extend(["", "## Resource and API cost", ""])
    if isinstance(resource_cost, dict) and resource_cost:
        for key in sorted(resource_cost):
            lines.append(
                f"- `{key}`: `{_markdown_value(resource_cost[key])}`"
            )
    else:
        lines.append("- Not recorded")

    limitations = report.get("limitations", [])
    lines.extend(["", "## Limitations", ""])
    if isinstance(limitations, list) and limitations:
        lines.extend(
            f"- {limitation}"
            for limitation in limitations
            if isinstance(limitation, str)
        )
    else:
        lines.append("- None recorded")

    lines.extend(["", "## Failed cases", ""])
    failures = report.get("failures", [])
    if isinstance(failures, list) and failures:
        for failure in failures:
            if not isinstance(failure, dict):
                continue
            query_id = str(failure.get("query_id", "unknown"))
            reason = str(failure.get("reason", "unspecified"))
            lines.append(f"- `{query_id}`: {reason}")
            returned = failure.get("returned_results", [])
            if isinstance(returned, list):
                returned_ids = [
                    str(item.get("chunk_id", ""))
                    for item in returned
                    if isinstance(item, dict) and item.get("chunk_id")
                ]
                if returned_ids:
                    lines.append(
                        f"  - Returned chunk IDs: `{', '.join(returned_ids)}`"
                    )
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def write_report_artifacts(
    report: dict[str, Any],
    output_dir: str | Path,
    *,
    artifact_basename: str = "report",
    repository_root: str | Path | None = None,
    allow_public_output: bool = False,
) -> dict[str, Path]:
    if not artifact_basename or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for character in artifact_basename
    ):
        raise ValueError("artifact_basename must contain only letters, digits, - or _")
    directory = Path(output_dir)
    json_path = directory / f"{artifact_basename}.json"
    markdown_path = directory / f"{artifact_basename}.md"
    dataset = report.get("dataset", {})
    label_source = dataset.get("label_source") if isinstance(dataset, dict) else None
    json_content = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    markdown_content = render_markdown_report(report)
    if allow_public_output:
        if label_source != "deterministic_fixture":
            raise ValueError(
                "public output requires a validated deterministic fixture"
            )
        if not _has_public_workload_identity(report):
            raise ValueError("public report requires workload identity")
        directory.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json_content, encoding="utf-8")
        markdown_path.write_text(markdown_content, encoding="utf-8")
    else:
        enforce_parent_mode = require_private_output_destination(
            directory,
            repository_root=repository_root,
        )
        json_path = secure_atomic_write_text(
            json_path,
            json_content,
            enforce_parent_mode=enforce_parent_mode,
        )
        markdown_path = secure_atomic_write_text(
            markdown_path,
            markdown_content,
            enforce_parent_mode=enforce_parent_mode,
        )
    return {"json": json_path, "markdown": markdown_path}


def sanitize_report_for_ci(report: dict[str, Any]) -> dict[str, Any]:
    sanitized = {
        "report_version": report.get("report_version", 1),
        "disclaimer": report.get("disclaimer", ""),
        "dataset": _sanitize_dataset(report.get("dataset", {})),
        "configuration": _sanitize_mapping(report.get("configuration", {})),
        "git_identifier": report.get("git_identifier", ""),
        "timestamp": report.get("timestamp", ""),
        "metrics": deepcopy(report.get("metrics", {})),
        "failures": [],
    }
    failures = report.get("failures", [])
    if isinstance(failures, list):
        for failure in failures:
            if not isinstance(failure, dict):
                continue
            safe_failure = {
                key: str(failure[key])
                for key in ("query_id", "reason")
                if failure.get(key) is not None
            }
            if safe_failure:
                sanitized["failures"].append(safe_failure)
    return sanitized


def _sanitize_dataset(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: deepcopy(value[key])
        for key in ("name", "label_source", "size")
        if key in value
    }


def _sanitize_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    sanitized: dict[str, Any] = {}
    sensitive_terms = ("path", "query", "quote", "content", "result")
    for key, item in value.items():
        normalized_key = str(key).casefold()
        if any(term in normalized_key for term in sensitive_terms):
            continue
        if isinstance(item, dict):
            sanitized[str(key)] = _sanitize_mapping(item)
        elif isinstance(item, list):
            sanitized[str(key)] = [
                element
                for element in item
                if isinstance(element, (str, int, float, bool, type(None)))
            ]
        elif isinstance(item, (str, int, float, bool, type(None))):
            sanitized[str(key)] = item
    return sanitized


def _ordered_metric_keys(metrics: dict[str, Any]) -> list[str]:
    known = [key for key in METRIC_LABELS if key in metrics]
    unknown = sorted(key for key in metrics if key not in METRIC_LABELS)
    return [*known, *unknown]


def _has_public_workload_identity(report: dict[str, Any]) -> bool:
    if report.get("report_version") != 2:
        return False
    digests = report.get("input_digests")
    if not isinstance(digests, dict) or set(digests) != {
        "dataset_sha256",
        "corpus_sha256",
        "configuration_sha256",
    }:
        return False
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in digests.values()
    ):
        return False
    execution_path = report.get("execution_path")
    return (
        isinstance(execution_path, dict)
        and isinstance(execution_path.get("identity"), str)
        and bool(execution_path["identity"].strip())
    )


def _markdown_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)
