from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import tempfile
import time
from pathlib import Path
import sys
from typing import Any


def _ensure_repo_root_on_sys_path() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


_ensure_repo_root_on_sys_path()

from core.models import (  # noqa: E402
    ChunkModel,
    DocumentModel,
    SourceModel,
    SourceType,
    SyncStatus,
)
from evals.contextwiki_eval import (  # noqa: E402
    FIXTURE_VECTOR_RETRIEVER_CLASS,
    FixtureIndexer,
    FixtureNode,
)
from evals.metrics import aggregate_suite_metrics, metric_payload  # noqa: E402
from evals.rag_dataset import (  # noqa: E402
    dataset_version,
    load_cases,
    load_documents,
    load_manifest,
)
from evals.reporting import render_rag_report  # noqa: E402
from evals.retrieval_quality import (  # noqa: E402
    RetrievalQualityCase,
    evaluate_search_suite,
)
from search.answer_service import CitationAnswerService  # noqa: E402
from search.context_service import ContextSearchService  # noqa: E402
from search.query_terms import query_term_groups  # noqa: E402
from storage.metadata_store import MetadataStore  # noqa: E402
from indexing.background_tasks import safe_error_message  # noqa: E402


class InactiveAwareFixtureRetriever:
    """Lexical fixture retriever that can inject inactive vector candidates."""

    def __init__(
        self,
        *,
        index: MetadataStore,
        similarity_top_k: int,
        vector_store_query_mode: str | None = None,
        filters=None,
        inactive_chunks: list[ChunkModel] | None = None,
    ):
        self._base = FIXTURE_VECTOR_RETRIEVER_CLASS(
            index=index,
            similarity_top_k=similarity_top_k,
            vector_store_query_mode=vector_store_query_mode,
            filters=filters,
        )
        self.inactive_chunks = list(inactive_chunks or [])
        self.similarity_top_k = similarity_top_k

    def retrieve(self, query: str) -> list[FixtureNode]:
        active_nodes = self._base.retrieve(query)
        term_groups = query_term_groups(query)
        inactive_ranked: list[tuple[float, ChunkModel]] = []
        for chunk in self.inactive_chunks:
            haystack = " ".join(
                [
                    chunk.title or "",
                    chunk.text or "",
                    chunk.path or "",
                    chunk.url or "",
                ]
            ).lower()
            overlap = sum(
                1 for group in term_groups if any(term in haystack for term in group)
            )
            if overlap <= 0:
                continue
            inactive_ranked.append((overlap / max(len(term_groups), 1), chunk))
        inactive_ranked.sort(key=lambda item: (-item[0], item[1].chunk_id))
        inactive_nodes = [
            FixtureNode(chunk, score) for score, chunk in inactive_ranked
        ]
        merged = [*inactive_nodes, *active_nodes]
        merged.sort(key=lambda node: (-float(node.score), str(node.metadata["chunk_id"])))
        return merged[: self.similarity_top_k]


def run_retrieval_benchmark(
    *,
    split: str = "test",
    output_dir: str | Path,
    live: bool = False,
    max_budget: float = 0.0,
    include_hybrid: bool = True,
) -> dict[str, Any]:
    if live and float(max_budget) <= 0.0:
        raise ValueError(
            "--live requires a positive --max-budget spend ceiling "
            "(estimated embedding cost). Refusing to start without an explicit budget."
        )

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    try:
        return _run_retrieval_benchmark_body(
            split=split,
            output_path=output_path,
            live=live,
            max_budget=max_budget,
            include_hybrid=include_hybrid,
        )
    except Exception as exc:
        _write_error_artifacts(output_path, live=live, error=exc)
        raise


def _run_retrieval_benchmark_body(
    *,
    split: str,
    output_path: Path,
    live: bool,
    max_budget: float,
    include_hybrid: bool,
) -> dict[str, Any]:
    documents = load_documents()
    cases = load_cases(split)
    version = dataset_version()
    manifest = load_manifest()

    with tempfile.TemporaryDirectory(prefix="rag-benchmark-") as temp_dir:
        store = MetadataStore(Path(temp_dir) / "rag.sqlite3")
        inactive_chunks = _seed_documents(store, documents)
        retriever_cls = _make_retriever_cls(inactive_chunks)
        search_service = ContextSearchService(
            store,
            indexer=FixtureIndexer(store),
            vector_retriever_cls=retriever_cls,
        )
        answer_service = CitationAnswerService(search_service)

        lexical = _run_lexical_config(
            search_service=search_service,
            answer_service=answer_service,
            cases=cases,
            documents=documents,
        )

    configs: dict[str, Any] = {"lexical": lexical}
    if live:
        live_result = _run_live_configs(
            cases=cases,
            documents=documents,
            max_budget=float(max_budget),
            include_hybrid=include_hybrid,
        )
        configs.update(live_result)
    else:
        configs["vector"] = {
            "status": "skipped",
            "reason": "requires --live",
            "metrics": None,
            "failure_kind": None,
        }
        if include_hybrid:
            configs["hybrid"] = {
                "status": "skipped",
                "reason": "requires --live",
                "metrics": None,
                "failure_kind": None,
            }

    summary = {
        "dataset_version": version,
        "split": split,
        "policy": manifest.get("policy", ""),
        "configs": configs,
        "passed": bool(lexical.get("passed", False)),
        "live_requested": live,
        "live_status": _live_run_status(configs, live=live),
    }
    report_summary = {
        "dataset_version": version,
        "retrieval_config": {
            "mode": "lexical",
            "live": live,
            "split": split,
        },
        "fixture_metrics": _rename_at_k(lexical.get("metrics") or {}, k=5),
        "live_metrics": _live_metrics_for_report(configs),
        "group_breakdown": lexical.get("group_breakdown") or {},
        "failures": lexical.get("failures") or [],
        "latency_ms": lexical.get("latency_ms") or {},
        "baseline_delta": None,
        "limitations": [
            "Fixture lexical results are not production embedding performance.",
            "Live embedding rows appear only when --live is explicitly enabled.",
            "Test-split labels must not be used for retrieval tuning.",
            "Live --max-budget uses deterministic 1k-character embedding-unit estimates, not provider invoices.",
        ],
    }
    _write_artifacts(output_path, summary, report_summary)
    return summary


def _write_error_artifacts(output_dir: Path, *, live: bool, error: BaseException) -> None:
    error_type = type(error).__name__
    safe_body = safe_error_message(error)
    message = f"{error_type}: {safe_body}"
    summary = {
        "status": "error",
        "error": safe_body,
        "error_type": error_type,
        "passed": False,
        "live_requested": live,
        "live_status": "error",
        "configs": {},
    }
    report_summary = {
        "dataset_version": "",
        "retrieval_config": {"mode": "error", "live": live},
        "fixture_metrics": {},
        "live_metrics": None,
        "group_breakdown": {},
        "failures": [{"case_id": "runner", "reasons": [message]}],
        "latency_ms": {},
        "baseline_delta": None,
        "limitations": [
            "Benchmark aborted before completion; artifacts are error status only.",
            "Fixture lexical results are not production embedding performance.",
        ],
    }
    try:
        _write_artifacts(output_dir, summary, report_summary)
    except Exception:
        # Preserve the original failure if artifact writing also fails.
        pass


def _make_retriever_cls(inactive_chunks: list[ChunkModel]):
    class BoundRetriever(InactiveAwareFixtureRetriever):
        def __init__(self, *args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["inactive_chunks"] = inactive_chunks
            super().__init__(*args, **kwargs)

    return BoundRetriever


def _seed_documents(
    store: MetadataStore,
    documents: list[dict[str, Any]],
) -> list[ChunkModel]:
    seeded_sources: set[str] = set()
    inactive_chunks: list[ChunkModel] = []

    for item in documents:
        source_id = str(item["source_id"])
        source_type = SourceType(str(item["source_type"]))
        if source_id not in seeded_sources:
            store.upsert_source(
                SourceModel(
                    source_id=source_id,
                    source_type=source_type,
                    name=source_id,
                    sync_status=SyncStatus.IDLE,
                )
            )
            seeded_sources.add(source_id)

        document_id = str(item["document_id"])
        chunk_id = str(item["chunk_id"])
        title = str(item["title"])
        text = str(item["text"])
        url = str(item.get("url", ""))
        path = str(item.get("path", title))
        chunk = ChunkModel(
            chunk_id=chunk_id,
            document_id=document_id,
            source_id=source_id,
            title=title,
            text=text,
            url=url,
            path=path,
            chunk_index=0,
            content_hash=chunk_id,
        )
        is_active = bool(item.get("active", True))
        store.upsert_document_and_replace_chunks(
            DocumentModel(
                id=document_id,
                document_id=document_id,
                external_id=document_id,
                source_id=source_id,
                title=title,
                content=text,
                url=url,
                canonical_url=url,
                platform=source_type.value,
                path=path,
                chunk_id=chunk_id,
                deleted_at="" if is_active else "2026-07-30T00:01:00Z",
            ),
            [chunk] if is_active else [],
        )
        if not is_active:
            # Ensure tombstone exists even when seeded without live chunks.
            store.replace_document_chunks(document_id, [])
            inactive_chunks.append(chunk)
    return inactive_chunks


def _result_chunk_id(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("chunk_id") or "")
    return str(getattr(item, "chunk_id", "") or "")


async def _run_lexical_cases_async(
    *,
    search_service: ContextSearchService,
    cases: list[dict[str, Any]],
    inactive_ids: set[str],
) -> tuple[dict[str, dict[str, Any]], list[float], list[dict[str, Any]]]:
    payloads: dict[str, dict[str, Any]] = {}
    latencies: list[float] = []
    metric_cases: list[dict[str, Any]] = []

    for case in cases:
        started = time.perf_counter()
        # One retrieval per case: ranking and citation-candidate metrics share it.
        payload = await search_service.search_context(
            case["query"],
            top_k=int(case["top_k"]),
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        latencies.append(latency_ms)
        payloads[case["case_id"]] = payload

        ranked = [
            chunk_id
            for chunk_id in (
                _result_chunk_id(item) for item in payload.get("results") or []
            )
            if chunk_id
        ]
        relevant = [str(item) for item in case.get("relevant_chunk_ids") or [] if item]
        # Citation precision uses the full ranked list as "cited" candidates so
        # distractors in top-k reduce precision; recall still uses required labels.
        cited = list(ranked)
        relevant_hits = [chunk_id for chunk_id in ranked if chunk_id in set(relevant)]
        forbidden = [str(item) for item in case.get("forbidden_chunk_ids") or []]
        hard_negatives = [
            str(item) for item in case.get("hard_negative_chunk_ids") or [] if item
        ]
        inactive_forbidden = [
            chunk_id for chunk_id in forbidden if chunk_id in inactive_ids
        ]
        explicit_inactive = [
            str(item)
            for item in case.get("forbidden_inactive_chunk_ids") or []
            if item
        ]
        if explicit_inactive:
            inactive_forbidden = explicit_inactive
        elif case.get("group") in {"stale-block", "inactive"}:
            inactive_forbidden = forbidden

        no_answer = bool(case.get("no_answer"))
        if no_answer:
            # Spurious hits must not count as successful insufficient answers.
            evidence_status = "insufficient" if not ranked else "grounded"
        else:
            evidence_status = "grounded" if relevant_hits else "insufficient"

        metric_cases.append(
            {
                "case_id": case["case_id"],
                "no_answer": no_answer,
                "top_k": int(case["top_k"]),
                "relevant_chunk_ids": relevant,
                "ranked_chunk_ids": ranked,
                "forbidden_inactive_chunk_ids": inactive_forbidden,
                "hard_negative_chunk_ids": hard_negatives,
                "cited_chunk_ids": cited,
                "required_citation_chunk_ids": relevant,
                "expected_status": ("insufficient" if no_answer else "grounded"),
                "evidence_status": evidence_status,
            }
        )
    return payloads, latencies, metric_cases


def _retrieval_min_result_count(case: dict[str, Any]) -> int:
    """Empty/stale/no-answer cases must not require a non-empty hit list."""
    if case.get("no_answer"):
        return 0
    if not (case.get("relevant_chunk_ids") or []):
        return 0
    if case.get("group") in {"stale-block", "inactive"}:
        return 0
    return 1


def _run_lexical_config(
    *,
    search_service: ContextSearchService,
    answer_service: CitationAnswerService,
    cases: list[dict[str, Any]],
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    del answer_service  # retained for call-site compatibility; unused after single-retrieve path
    inactive_ids = {
        str(item["chunk_id"])
        for item in documents
        if not item.get("active", True)
    }
    retrieval_cases = [
        RetrievalQualityCase.from_mapping(
            {
                **case,
                "min_result_count": _retrieval_min_result_count(case),
                "required_chunk_ids": case.get("relevant_chunk_ids") or [],
                "expected_top_chunk_id": (
                    (case.get("relevant_chunk_ids") or [""])[0]
                    if (case.get("relevant_chunk_ids") or [])
                    and not case.get("no_answer")
                    else ""
                ),
                "forbidden_inactive_chunk_ids": case.get("forbidden_inactive_chunk_ids")
                or [
                    chunk_id
                    for chunk_id in (case.get("forbidden_chunk_ids") or [])
                    if chunk_id in inactive_ids
                ],
                "hard_negative_chunk_ids": case.get("hard_negative_chunk_ids") or [],
            }
        )
        for case in cases
    ]

    payloads, latencies, metric_cases = asyncio.run(
        _run_lexical_cases_async(
            search_service=search_service,
            cases=cases,
            inactive_ids=inactive_ids,
        )
    )

    suite = evaluate_search_suite(payloads, retrieval_cases)
    metrics = aggregate_suite_metrics(metric_cases, k=5)
    metrics_at_5 = {
        "hit_at_k": metrics["hit_at_k"],
        "mrr_at_k": metrics["mrr_at_k"],
        "recall_at_k": metrics["recall_at_k"],
        "ndcg_at_k": metrics["ndcg_at_k"],
        "hit_at_5": metrics["hit_at_k"],
        "mrr_at_5": metrics["mrr_at_k"],
        "recall_at_5": metrics["recall_at_k"],
        "ndcg_at_5": metrics["ndcg_at_k"],
        "citation_precision": metrics["citation_precision"],
        "citation_recall": metrics["citation_recall"],
        "insufficient_status_accuracy": metrics["insufficient_status_accuracy"],
        "stale_inactive_block_rate": metrics["stale_inactive_block_rate"],
        "scorable_case_count": metrics["scorable_case_count"],
        "embedding_cost_per_query": metric_payload(numerator=0.0, denominator=0),
        "k": 5,
    }

    failures = [
        {"case_id": item["case_id"], "reasons": item.get("failures") or []}
        for item in suite["results"]
        if not item["passed"]
    ]
    quality_ok = _quality_gate_passed(metrics_at_5)
    if not quality_ok:
        failures.append(
            {
                "case_id": "_quality_gate",
                "reasons": ["labeled quality metric below required threshold"],
            }
        )
    passed = bool(suite["passed"] and quality_ok)
    # Avoid embedding contradictory retrieval-only quality_metrics under suite.
    suite_for_artifact = {
        key: value
        for key, value in suite.items()
        if key != "quality_metrics"
    }
    suite_for_artifact["quality_metrics"] = metrics_at_5
    return {
        "status": "executed",
        "passed": passed,
        "metrics": metrics_at_5,
        "group_breakdown": suite["group_breakdown"],
        "failures": failures,
        "latency_ms": _latency_summary(latencies),
        "failure_kind": "quality_failure" if not passed else None,
        "suite": suite_for_artifact,
    }


def _quality_gate_passed(metrics: dict[str, Any]) -> bool:
    required = (
        "hit_at_k",
        "stale_inactive_block_rate",
        "insufficient_status_accuracy",
    )
    for key in required:
        payload = metrics.get(key) or {}
        value = payload.get("value")
        denominator = int(payload.get("denominator") or 0)
        # Fail closed: missing/unscorable required metrics must not pass CI.
        if denominator <= 0 or value is None:
            return False
        if float(value) < 1.0:
            return False
    return True


def _run_live_configs(
    *,
    cases: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    max_budget: float,
    include_hybrid: bool,
) -> dict[str, Any]:
    # Live path is opt-in and records provider failures separately from quality.
    # Estimated cost is checked before any provider call; unconfigured providers
    # must not be scored as zero-quality results.
    estimated_units = _live_embedding_units(
        cases=cases,
        documents=documents,
        include_hybrid=include_hybrid,
    )
    estimated_cost = estimated_units / 1000.0
    max_units = _max_budget_units(max_budget)
    if estimated_units > max_units:
        skipped = {
            "status": "budget_exceeded",
            "reason": (
                f"Estimated embedding cost {estimated_cost:.4f} exceeds "
                f"--max-budget {max_budget:.4f}; live providers were not executed."
            ),
            "metrics": None,
            "failure_kind": "budget_exceeded",
            "estimated_cost": estimated_cost,
            "max_budget": max_budget,
        }
        result = {"vector": dict(skipped)}
        if include_hybrid:
            result["hybrid"] = dict(skipped)
        return result

    result = {
        "vector": {
            "status": "provider_error",
            "reason": "Live embedding provider is not configured in this runner yet.",
            "metrics": None,
            "failure_kind": "provider_error",
            "estimated_cost": estimated_cost,
            "max_budget": max_budget,
        }
    }
    if include_hybrid:
        result["hybrid"] = {
            "status": "provider_error",
            "reason": "Live hybrid retrieval requires a configured embedding provider.",
            "metrics": None,
            "failure_kind": "provider_error",
            "estimated_cost": estimated_cost,
            "max_budget": max_budget,
        }
    return result


def _live_embedding_units(
    *,
    cases: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    include_hybrid: bool,
) -> int:
    active_docs = [item for item in documents if item.get("active", True)]
    index_units = sum(
        _embedding_units_for_text(str(item.get("text") or "")) for item in active_docs
    )
    query_modes = 1 + (1 if include_hybrid else 0)
    query_units = (
        sum(_embedding_units_for_text(str(case.get("query") or "")) for case in cases)
        * query_modes
    )
    return int(index_units + query_units)


def _max_budget_units(max_budget: float) -> int:
    # Convert dollar ceiling to 1k-char units without float multiply drift.
    return max(0, math.floor(float(max_budget) * 1000 + 1e-9))


def _estimate_live_embedding_cost(
    *,
    cases: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    include_hybrid: bool,
) -> float:
    """Deterministic offline estimate used only for budget gating.

    Prices both corpus indexing (active document text) and per-query embedding
    text by rounded 1k-character units so --max-budget cannot ignore document
    or query size. Real provider invoices are never read and secrets are not
    required. Callers that gate execution should compare integer units via
    `_live_embedding_units` / `_max_budget_units` rather than float multiply.
    """
    return _live_embedding_units(
        cases=cases,
        documents=documents,
        include_hybrid=include_hybrid,
    ) / 1000.0


def _embedding_units_for_text(text: str) -> int:
    length = len(text or "")
    if length <= 0:
        return 1
    return max(1, math.ceil(length / 1000))


def _latency_summary(latencies_ms: list[float]) -> dict[str, float]:
    if not latencies_ms:
        return {"average": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0, "total": 0.0}
    ordered = sorted(latencies_ms)
    index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return {
        "average": float(sum(ordered) / len(ordered)),
        "p95": float(ordered[index]),
        "min": float(ordered[0]),
        "max": float(ordered[-1]),
        "total": float(sum(ordered)),
    }


def _rename_at_k(metrics: dict[str, Any], k: int) -> dict[str, Any]:
    renamed = dict(metrics)
    mapping = {
        f"hit_at_{k}": "hit_at_k",
        f"mrr_at_{k}": "mrr_at_k",
        f"recall_at_{k}": "recall_at_k",
        f"ndcg_at_{k}": "ndcg_at_k",
    }
    for src, dst in mapping.items():
        if src in renamed and dst not in renamed:
            renamed[dst] = renamed[src]
    return renamed


def _live_metrics_for_report(configs: dict[str, Any]) -> dict[str, Any] | None:
    vector = configs.get("vector") or {}
    if vector.get("status") == "executed" and vector.get("metrics"):
        return _rename_at_k(vector["metrics"], k=5)
    return None


def _write_artifacts(
    output_dir: Path,
    summary: dict[str, Any],
    report_summary: dict[str, Any],
) -> None:
    (output_dir / "benchmark_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = render_rag_report(report_summary)
    (output_dir / "benchmark_report.md").write_text(report, encoding="utf-8")
    (output_dir / "rag_report.md").write_text(report, encoding="utf-8")

    rows: list[dict[str, Any]] = []
    for config_name, config in summary.get("configs", {}).items():
        metrics = config.get("metrics") or {}
        row = {
            "config": config_name,
            "status": config.get("status"),
            "failure_kind": config.get("failure_kind") or "",
            "hit_at_5": _metric_value(metrics.get("hit_at_5")),
            "mrr_at_5": _metric_value(metrics.get("mrr_at_5")),
            "recall_at_5": _metric_value(metrics.get("recall_at_5")),
            "ndcg_at_5": _metric_value(metrics.get("ndcg_at_5")),
            "citation_precision": _metric_value(metrics.get("citation_precision")),
            "citation_recall": _metric_value(metrics.get("citation_recall")),
            "avg_latency_ms": (config.get("latency_ms") or {}).get("average", ""),
            "p95_latency_ms": (config.get("latency_ms") or {}).get("p95", ""),
        }
        rows.append(row)
    fieldnames = list(rows[0].keys()) if rows else ["config", "status"]
    with (output_dir / "benchmark_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _metric_value(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get("value")
    return "" if value is None else f"{float(value):.6f}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run Aurora Relay retrieval benchmarks. "
            "Outputs JSON, CSV, and Markdown reports. "
            "Default mode is offline lexical only."
        )
    )
    parser.add_argument("--split", default="test", choices=["train", "dev", "test"])
    parser.add_argument("--output-dir", required=True, help="Directory for JSON/CSV/Markdown artifacts.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable live embedding/vector (and hybrid) providers. Default is offline.",
    )
    parser.add_argument(
        "--max-budget",
        type=float,
        default=0.0,
        help=(
            "Required positive estimated embedding spend ceiling when using --live. "
            "Estimate uses deterministic 1k-character index+query units (not invoices). "
            "Live mode is rejected when this value is missing or <= 0."
        ),
    )
    args = parser.parse_args()
    try:
        summary = run_retrieval_benchmark(
            split=args.split,
            output_dir=args.output_dir,
            live=args.live,
            max_budget=args.max_budget,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not summary.get("passed", False):
        raise SystemExit(1)
    if summary.get("live_requested") and summary.get("live_status") != "executed":
        raise SystemExit(3)


def _live_run_status(configs: dict[str, Any], *, live: bool) -> str:
    if not live:
        return "not_requested"
    live_configs = [
        configs[name]
        for name in ("vector", "hybrid")
        if name in configs
    ]
    if any(cfg.get("status") == "executed" for cfg in live_configs):
        return "executed"
    if any(cfg.get("status") == "budget_exceeded" for cfg in live_configs):
        return "budget_exceeded"
    if any(cfg.get("status") == "provider_error" for cfg in live_configs):
        return "provider_error"
    return "not_executed"


if __name__ == "__main__":
    main()
