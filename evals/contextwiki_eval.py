from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from core.models import ChunkModel, DocumentModel, SourceModel, SourceType, SyncStatus
from evals.answer_quality import evaluate_answer_suite, load_cases as load_answer_cases
from evals.retrieval_quality import (
    evaluate_search_suite,
    load_cases as load_retrieval_cases,
)
from search.answer_service import CitationAnswerService
from search.context_service import ContextSearchService
from storage.metadata_store import MetadataStore


FIXTURE_DOCUMENTS_PATH = Path("evals/contextwiki_fixture_documents.json")
RETRIEVAL_CASES_PATH = Path("evals/retrieval_quality_cases.json")
ANSWER_CASES_PATH = Path("evals/contextwiki_answer_quality_cases.json")


def run_contextwiki_eval(
    *,
    fixture_documents_path: str | Path = FIXTURE_DOCUMENTS_PATH,
    retrieval_cases_path: str | Path = RETRIEVAL_CASES_PATH,
    answer_cases_path: str | Path = ANSWER_CASES_PATH,
) -> dict:
    documents = json.loads(Path(fixture_documents_path).read_text(encoding="utf-8"))
    retrieval_cases = load_retrieval_cases(retrieval_cases_path)
    answer_cases = load_answer_cases(answer_cases_path)

    with tempfile.TemporaryDirectory(prefix="contextwiki-eval-") as temp_dir:
        store = MetadataStore(Path(temp_dir) / "contextwiki.sqlite3")
        _seed_fixture_documents(store, documents)
        retriever_documents = _list_search_documents(store)
        search_service = ContextSearchService(
            store,
            retriever=retriever_documents,
            query_rewriter=None,
        )
        answer_service = CitationAnswerService(search_service)

        retrieval_payloads = {
            case.case_id: asyncio.run(
                search_service.search_context(case.query, top_k=case.top_k)
            )
            for case in retrieval_cases
        }
        answer_payloads = {
            case.case_id: asyncio.run(
                answer_service.answer_with_citations(case.question, top_k=case.top_k)
            )
            for case in answer_cases
        }

    retrieval_suite = evaluate_search_suite(retrieval_payloads, retrieval_cases)
    answer_suite = evaluate_answer_suite(answer_payloads, answer_cases)
    return {
        "passed": retrieval_suite["passed"] and answer_suite["passed"],
        "retrieval_suite": retrieval_suite,
        "answer_suite": answer_suite,
    }


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


def _list_search_documents(store: MetadataStore) -> list[DocumentModel]:
    return [
        chunk.to_document_model(platform="Test")
        for chunk in store.list_chunks()
    ]
