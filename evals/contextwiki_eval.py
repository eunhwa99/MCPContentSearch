from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path

from core.models import (
    ChunkModel,
    DocumentModel,
    SearchFilters,
    SourceModel,
    SourceType,
    SyncStatus,
)
from evals.answer_quality import evaluate_answer_suite, load_cases as load_answer_cases
from evals.document_sort_quality import (
    evaluate_document_sort_suite,
    load_cases as load_document_sort_cases,
)
from evals.retrieval_quality import (
    evaluate_search_suite,
    load_cases as load_retrieval_cases,
)
from search.answer_service import CitationAnswerService
from search.context_service import ContextSearchService
from search.query_terms import query_term_groups
from storage.metadata_store import MetadataStore


FIXTURE_DOCUMENTS_PATH = Path("evals/contextwiki_fixture_documents.json")
RETRIEVAL_CASES_PATH = Path("evals/retrieval_quality_cases.json")
ANSWER_CASES_PATH = Path("evals/contextwiki_answer_quality_cases.json")
DOCUMENT_SORT_CASES_PATH = Path("evals/document_sort_quality_cases.json")


class FixtureIndexer:
    def __init__(self, store: MetadataStore):
        self.store = store

    def get_or_create_index(self) -> MetadataStore:
        return self.store


class FixtureNode:
    def __init__(self, chunk: ChunkModel, score: float):
        self.metadata = {
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.document_id,
            "source_id": chunk.source_id,
            "contextwiki_managed": "true",
        }
        self.score = score


class FixtureVectorIndexRetriever:
    def __init__(
        self,
        *,
        index: MetadataStore,
        similarity_top_k: int,
        vector_store_query_mode: str | None = None,
        filters=None,
    ):
        self.store = index
        self.similarity_top_k = similarity_top_k
        self.filters = filters

    def retrieve(self, query: str) -> list[FixtureNode]:
        required_source_ids = _source_ids_from_filters(self.filters)
        term_groups = query_term_groups(query)
        ranked: list[tuple[float, ChunkModel]] = []
        for chunk in self.store.list_chunks():
            if required_source_ids and chunk.source_id not in required_source_ids:
                continue
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
            ranked.append((overlap / max(len(term_groups), 1), chunk))
        ranked.sort(key=lambda item: (-item[0], item[1].chunk_id))
        return [
            FixtureNode(chunk, score)
            for score, chunk in ranked[: self.similarity_top_k]
        ]


FIXTURE_VECTOR_RETRIEVER_CLASS = FixtureVectorIndexRetriever


def _source_ids_from_filters(filters) -> set[str]:
    if filters is None:
        return set()
    try:
        filter_list = getattr(filters, "filters", None) or []
        source_ids = set()
        for single in filter_list:
            if getattr(single, "key", "") != "source_id":
                continue
            value = getattr(single, "value", None)
            if isinstance(value, (list, tuple, set)):
                source_ids.update(str(item) for item in value if item)
            elif value:
                source_ids.add(str(value))
        return source_ids
    except Exception:
        return set()


def run_contextwiki_eval(
    *,
    fixture_documents_path: str | Path = FIXTURE_DOCUMENTS_PATH,
    retrieval_cases_path: str | Path = RETRIEVAL_CASES_PATH,
    answer_cases_path: str | Path = ANSWER_CASES_PATH,
    document_sort_cases_path: str | Path = DOCUMENT_SORT_CASES_PATH,
    output_dir: str | Path | None = None,
    include_latency: bool = False,
) -> dict:
    documents = json.loads(Path(fixture_documents_path).read_text(encoding="utf-8"))
    retrieval_cases = load_retrieval_cases(retrieval_cases_path)
    answer_cases = load_answer_cases(answer_cases_path)
    document_sort_cases = load_document_sort_cases(document_sort_cases_path)

    with tempfile.TemporaryDirectory(prefix="contextwiki-eval-") as temp_dir:
        store = MetadataStore(Path(temp_dir) / "contextwiki.sqlite3")
        _seed_fixture_documents(store, documents)

        search_service = ContextSearchService(
            store,
            indexer=FixtureIndexer(store),
            vector_retriever_cls=FIXTURE_VECTOR_RETRIEVER_CLASS,
        )
        answer_service = CitationAnswerService(search_service)

        retrieval_payloads, retrieval_latency_ms = _run_retrieval_cases(
            search_service, retrieval_cases
        )
        document_sort_payloads, document_sort_latency_ms = _run_document_sort_cases(
            search_service,
            document_sort_cases,
        )
        answer_payloads, answer_latency_ms = _run_answer_cases(
            answer_service, answer_cases
        )

    retrieval_suite = evaluate_search_suite(
        retrieval_payloads,
        retrieval_cases,
    )
    answer_suite = evaluate_answer_suite(
        answer_payloads,
        answer_cases,
    )
    document_sort_suite = evaluate_document_sort_suite(
        document_sort_payloads,
        document_sort_cases,
    )
    summary = {
        "passed": (
            retrieval_suite["passed"]
            and document_sort_suite["passed"]
            and answer_suite["passed"]
        ),
        "artifact_dir": str(Path(output_dir)) if output_dir is not None else "",
        "retrieval_suite": retrieval_suite,
        "document_sort_suite": document_sort_suite,
        "answer_suite": answer_suite,
    }
    if include_latency:
        summary["runtime_metrics"] = {
            "retrieval_suite": {
                "latency_ms": _latency_summary(list(retrieval_latency_ms.values()))
            },
            "document_sort_suite": {
                "latency_ms": _latency_summary(
                    list(document_sort_latency_ms.values())
                )
            },
            "answer_suite": {
                "latency_ms": _latency_summary(list(answer_latency_ms.values()))
            },
        }
    if output_dir is not None:
        _write_artifacts(Path(output_dir), summary)
    return summary


def _seed_fixture_documents(store: MetadataStore, documents: list[dict]) -> None:
    seeded_sources: set[str] = set()
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
                published_at=str(item.get("published_at", "")),
                modified_at=str(item.get("modified_at", "")),
                indexed_at=str(item.get("indexed_at", "")),
                date_provenance=str(item.get("date_provenance", "")),
            ),
            [
                ChunkModel(
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
            ],
        )


def _run_retrieval_cases(
    search_service: ContextSearchService,
    retrieval_cases,
) -> tuple[dict[str, dict], dict[str, float]]:
    payloads: dict[str, dict] = {}
    latency_ms: dict[str, float] = {}
    for case in retrieval_cases:
        started = time.perf_counter()
        filters = SearchFilters.model_validate(case.filters) if case.filters else None
        payloads[case.case_id] = asyncio.run(
            search_service.search_context(
                case.query,
                filters=filters,
                top_k=case.top_k,
            )
        )
        latency_ms[case.case_id] = (time.perf_counter() - started) * 1000.0
    return payloads, latency_ms


def _run_answer_cases(
    answer_service: CitationAnswerService,
    answer_cases,
) -> tuple[dict[str, dict], dict[str, float]]:
    payloads: dict[str, dict] = {}
    latency_ms: dict[str, float] = {}
    for case in answer_cases:
        started = time.perf_counter()
        payloads[case.case_id] = asyncio.run(
            answer_service.answer_with_citations(case.question, top_k=case.top_k)
        )
        latency_ms[case.case_id] = (time.perf_counter() - started) * 1000.0
    return payloads, latency_ms


def _run_document_sort_cases(
    search_service: ContextSearchService,
    document_sort_cases,
) -> tuple[dict[str, dict], dict[str, float]]:
    payloads: dict[str, dict] = {}
    latency_ms: dict[str, float] = {}
    for case in document_sort_cases:
        started = time.perf_counter()
        filters = SearchFilters.model_validate(case.filters) if case.filters else None
        payloads[case.case_id] = asyncio.run(
            search_service.search_documents(
                case.query,
                filters=filters,
                sort_by=case.sort_by,
                sort_order=case.sort_order,
                top_k=case.top_k,
            )
        )
        latency_ms[case.case_id] = (time.perf_counter() - started) * 1000.0
    return payloads, latency_ms


def _write_artifacts(output_dir: Path, summary: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_metrics_path = output_dir / "runtime_metrics.json"
    stable_summary = {
        "passed": summary["passed"],
        "retrieval_suite": summary["retrieval_suite"],
        "document_sort_suite": summary["document_sort_suite"],
        "answer_suite": summary["answer_suite"],
    }
    (output_dir / "summary.json").write_text(
        json.dumps(stable_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "retrieval_suite.json").write_text(
        json.dumps(stable_summary["retrieval_suite"], ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "answer_suite.json").write_text(
        json.dumps(stable_summary["answer_suite"], ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "document_sort_suite.json").write_text(
        json.dumps(
            stable_summary["document_sort_suite"],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if "runtime_metrics" in summary:
        runtime_metrics_path.write_text(
            json.dumps(summary["runtime_metrics"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    elif runtime_metrics_path.exists():
        runtime_metrics_path.unlink()


def _latency_summary(latencies_ms: list[float]) -> dict[str, float]:
    if not latencies_ms:
        return {"total": 0.0, "average": 0.0, "min": 0.0, "max": 0.0}

    return {
        "total": float(sum(latencies_ms)),
        "average": float(sum(latencies_ms) / len(latencies_ms)),
        "min": float(min(latencies_ms)),
        "max": float(max(latencies_ms)),
    }
