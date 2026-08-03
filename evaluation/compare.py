from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


SUPPORTED_THRESHOLD_METRICS = frozenset(
    {
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "document_recall_at_1",
        "document_recall_at_3",
        "document_recall_at_5",
        "precision_at_3",
        "precision_at_5",
        "mrr",
        "ndcg_at_5",
        "citation_validity_rate",
        "duplicate_result_rate",
        "empty_result_accuracy",
        "source_type_filter_accuracy",
        "experience_type_filter_accuracy",
        "mean_latency_ms",
        "p50_latency_ms",
        "p95_latency_ms",
    }
)
THRESHOLD_RULE_KEYS = frozenset({"max_drop", "min", "max"})
DIGEST_FIELDS = (
    ("dataset_sha256", "dataset"),
    ("corpus_sha256", "corpus"),
    ("configuration_sha256", "configuration"),
)
REPORT_VERSION = 2


def compare_reports(
    baseline_report: dict[str, Any],
    current_report: dict[str, Any],
    thresholds: Any,
) -> dict[str, Any]:
    if not _valid_threshold_schema(thresholds):
        return {
            "passed": False,
            "dataset": {},
            "thresholds": {},
            "deltas": {},
            "violations": [
                {
                    "metric": "threshold_configuration",
                    "reason": "invalid_schema",
                }
            ],
        }
    baseline_metrics = _metrics(baseline_report)
    current_metrics = _metrics(current_report)
    violations: list[dict[str, str]] = []
    deltas: dict[str, dict[str, float | None]] = {}

    if baseline_report.get("report_version") != REPORT_VERSION:
        violations.append(
            {"metric": "report_schema", "reason": "invalid_baseline_report_version"}
        )
    if current_report.get("report_version") != REPORT_VERSION:
        violations.append(
            {"metric": "report_schema", "reason": "invalid_current_report_version"}
        )
    if baseline_report.get("dataset") != current_report.get("dataset"):
        violations.append({"metric": "dataset", "reason": "dataset_mismatch"})
    violations.extend(_workload_identity_violations(baseline_report, current_report))

    for metric_name, raw_rule in thresholds.items():
        if not isinstance(raw_rule, dict):
            violations.append(
                {"metric": str(metric_name), "reason": "invalid_threshold_rule"}
            )
            continue
        rule = dict(raw_rule)
        current_value = _finite_number(current_metrics.get(metric_name))
        baseline_value = _finite_number(baseline_metrics.get(metric_name))
        if current_value is None:
            violations.append(
                {"metric": str(metric_name), "reason": "missing_current_metric"}
            )
            continue
        if "max_drop" in rule and baseline_value is None:
            violations.append(
                {"metric": str(metric_name), "reason": "missing_baseline_metric"}
            )
            continue

        if baseline_value is not None:
            absolute = current_value - baseline_value
            relative = (
                absolute / baseline_value if baseline_value != 0.0 else None
            )
            deltas[str(metric_name)] = {
                "baseline": baseline_value,
                "current": current_value,
                "absolute": absolute,
                "relative": relative,
            }

        reason = _threshold_violation_reason(
            current_value=current_value,
            baseline_value=baseline_value,
            rule=rule,
        )
        if reason:
            violations.append({"metric": str(metric_name), "reason": reason})

    return {
        "passed": not violations,
        "dataset": current_report.get("dataset", {}),
        "thresholds": thresholds,
        "deltas": deltas,
        "violations": violations,
    }


def validate_threshold_configuration(
    baseline_report: dict[str, Any], thresholds: Any
) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    if not _valid_threshold_schema(thresholds):
        violations.append(
            {"metric": "threshold_configuration", "reason": "invalid_schema"}
        )

    baseline_status = str(baseline_report.get("status", ""))
    measured = bool(_metrics(baseline_report))
    if baseline_status == "not_measured" and measured:
        violations.append(
            {"metric": "baseline", "reason": "placeholder_contains_metrics"}
        )
    if baseline_status not in {"not_measured", "measured"}:
        violations.append(
            {"metric": "baseline", "reason": "invalid_baseline_status"}
        )
    if baseline_report.get("report_version") != REPORT_VERSION:
        violations.append(
            {"metric": "report_schema", "reason": "invalid_baseline_report_version"}
        )
    if _input_digests(baseline_report) is None:
        violations.append(
            {"metric": "workload", "reason": "missing_baseline_input_digests"}
        )
    if _execution_path(baseline_report) is None:
        violations.append(
            {
                "metric": "execution_path",
                "reason": "missing_baseline_execution_path_identity",
            }
        )
    return {
        "passed": not violations,
        "baseline_status": baseline_status,
        "threshold_count": len(thresholds) if isinstance(thresholds, dict) else 0,
        "violations": violations,
    }


def _valid_threshold_schema(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    for metric_name, rule in value.items():
        if metric_name not in SUPPORTED_THRESHOLD_METRICS:
            return False
        if not isinstance(rule, dict) or not rule:
            return False
        if set(rule) - THRESHOLD_RULE_KEYS:
            return False
        normalized: dict[str, float] = {}
        for key, raw_value in rule.items():
            number = _finite_number(raw_value)
            if number is None:
                return False
            normalized[key] = number
        if normalized.get("max_drop", 0.0) < 0.0:
            return False
        if (
            "min" in normalized
            and "max" in normalized
            and normalized["min"] > normalized["max"]
        ):
            return False
    return True


def _metrics(report: dict[str, Any]) -> dict[str, Any]:
    value = report.get("metrics", {})
    return value if isinstance(value, dict) else {}


def _workload_identity_violations(
    baseline_report: dict[str, Any], current_report: dict[str, Any]
) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    baseline_digests = _input_digests(baseline_report)
    current_digests = _input_digests(current_report)
    if baseline_digests is None:
        violations.append(
            {"metric": "workload", "reason": "missing_baseline_input_digests"}
        )
    if current_digests is None:
        violations.append(
            {"metric": "workload", "reason": "missing_current_input_digests"}
        )
    if baseline_digests is not None and current_digests is not None:
        for field, metric in DIGEST_FIELDS:
            if baseline_digests[field] != current_digests[field]:
                violations.append(
                    {"metric": metric, "reason": f"{metric}_digest_mismatch"}
                )

    baseline_execution = _execution_path(baseline_report)
    current_execution = _execution_path(current_report)
    if baseline_execution is None:
        violations.append(
            {
                "metric": "execution_path",
                "reason": "missing_baseline_execution_path_identity",
            }
        )
    if current_execution is None:
        violations.append(
            {
                "metric": "execution_path",
                "reason": "missing_current_execution_path_identity",
            }
        )
    if (
        baseline_execution is not None
        and current_execution is not None
        and baseline_execution != current_execution
    ):
        violations.append(
            {"metric": "execution_path", "reason": "execution_path_mismatch"}
        )
    return violations


def _input_digests(report: dict[str, Any]) -> dict[str, str] | None:
    value = report.get("input_digests")
    if not isinstance(value, dict) or set(value) != {field for field, _ in DIGEST_FIELDS}:
        return None
    if any(not _is_sha256(value.get(field)) for field, _ in DIGEST_FIELDS):
        return None
    return {field: str(value[field]) for field, _ in DIGEST_FIELDS}


def _execution_path(report: dict[str, Any]) -> dict[str, Any] | None:
    value = report.get("execution_path")
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("identity"), str)
        or not value["identity"].strip()
    ):
        return None
    return value


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) else None


def _threshold_violation_reason(
    *,
    current_value: float,
    baseline_value: float | None,
    rule: dict[str, Any],
) -> str:
    if "max_drop" in rule and baseline_value is not None:
        max_drop = _finite_number(rule["max_drop"])
        if max_drop is None:
            return "invalid_threshold_value"
        if current_value < baseline_value - max_drop:
            return "max_drop_exceeded"
    if "min" in rule:
        minimum = _finite_number(rule["min"])
        if minimum is None:
            return "invalid_threshold_value"
        if current_value < minimum:
            return "below_minimum"
    if "max" in rule:
        maximum = _finite_number(rule["max"])
        if maximum is None:
            return "invalid_threshold_value"
        if current_value > maximum:
            return "above_maximum"
    return ""


def _read_object(path: str | Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid {label}: expected JSON object")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare retrieval evaluation reports against CI thresholds."
    )
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--current", default="")
    parser.add_argument("--thresholds", required=True)
    parser.add_argument("--output", default="")
    parser.add_argument("--validate-config-only", action="store_true")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    try:
        baseline = _read_object(args.baseline, "baseline report")
        thresholds = _read_object(args.thresholds, "threshold configuration")
        if args.validate_config_only:
            comparison = validate_threshold_configuration(baseline, thresholds)
        else:
            if not args.current:
                raise ValueError("--current is required unless --validate-config-only")
            current = _read_object(args.current, "current report")
            comparison = compare_reports(baseline, current, thresholds)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    rendered = json.dumps(comparison, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if not comparison["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
