from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys


def _ensure_repo_root_on_sys_path() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


_ensure_repo_root_on_sys_path()

from api.tools import register_tools
from llama_index.core import Settings, StorageContext
from llama_index.vector_stores.chroma import ChromaVectorStore

from environments.config import AppConfig, setup_chroma
from environments.runtime_env import get_env_secret
from environments.token import NOTION_API_KEY, TISTORY_BLOG_NAME
from search import debug_redaction
from fetching.connectors import build_source_registry
from indexing.chunker import DocumentChunker
from indexing.ingestion_service import IngestionService
from indexing.indexer import ContentIndexer
from search.answer_service import CitationAnswerService
from search.context_service import ContextSearchService
from storage.metadata_store import MetadataStore


class SmokeMCP:
    def __init__(self):
        self.tools: dict[str, object] = {}
        self.answer_service: CitationAnswerService | None = None

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


def build_runtime_mcp():
    config = AppConfig()

    chroma_collection = setup_chroma(config)
    storage_context = StorageContext.from_defaults(
        vector_store=ChromaVectorStore(chroma_collection=chroma_collection)
    )
    Settings.cache_dir = config.cache_dir

    indexer = ContentIndexer(config, chroma_collection, storage_context)
    metadata_store = MetadataStore(config.metadata_db_path)
    source_registry = build_source_registry(
        config=config,
        notion_api_key=NOTION_API_KEY,
        tistory_blog_name=TISTORY_BLOG_NAME,
        github_token=get_env_secret(config.github_token_env_var),
    )
    retained_source_ids = [source.source_id for source in source_registry.list_sources()]
    ingestion_service = IngestionService(
        metadata_store=metadata_store,
        source_registry=source_registry,
        chunker=DocumentChunker(),
        indexer=indexer,
    )
    context_search = ContextSearchService(
        metadata_store=metadata_store,
        indexer=indexer,
        config=config,
        default_source_ids=retained_source_ids,
    )
    answer_service = CitationAnswerService(context_search)
    mcp = SmokeMCP()
    register_tools(
        mcp,
        ingestion_service=ingestion_service,
        context_search_service=context_search,
        answer_service=answer_service,
        metadata_store=metadata_store,
        source_registry=source_registry,
    )
    mcp.answer_service = answer_service
    return mcp


async def run_live_query_smoke(
    *,
    query: str,
    question: str,
    source_id: str | None,
    top_k: int,
) -> dict:
    mcp = build_runtime_mcp()
    filters = {"source_id": source_id} if source_id else None
    search_payload = await mcp.tools["search_context"](
        query,
        filters=filters,
        top_k=top_k,
        include_debug=True,
    )
    answer_payload = await mcp.answer_service.answer_with_citations(
        question,
        filters=filters,
        top_k=top_k,
        include_debug=True,
    )
    return {
        "query": query,
        "question": question,
        "source_id": source_id,
        "top_k": top_k,
        "search": search_payload,
        "answer": answer_payload,
    }


def redact_live_query_result(result: dict) -> dict:
    def _redact_debug_value(value):
        if isinstance(value, list):
            return [_redact_debug_value(item) for item in value]
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                if key in {"text", "preview", "path", "url"}:
                    continue
                redacted[key] = _redact_debug_value(item)
            return redacted
        return value

    def _redact_debug_markdown(value: str) -> str:
        lines = []
        for line in value.splitlines():
            if "preview:" in line.lower():
                prefix, _, _ = line.partition("preview:")
                lines.append(f"{prefix}preview: [REDACTED]")
                continue
            lines.append(line)
        return "\n".join(lines)

    def _redact_used_chunks(value):
        if isinstance(value, list):
            return [_redact_used_chunks(item) for item in value]
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                if key in {"text", "preview", "path", "url"}:
                    continue
                redacted[key] = _redact_used_chunks(item)
            return redacted
        return value

    search_results = []
    for item in result.get("search", {}).get("results", []):
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json")
        elif isinstance(item, dict):
            item = dict(item)
        else:
            item = {"value": str(item)}
        item.pop("text", None)
        item.pop("preview", None)
        item.pop("path", None)
        item.pop("url", None)
        search_results.append(item)

    answer_payload = result.get("answer", {})
    sanitized_answer = {
        "evidence_status": answer_payload.get("evidence_status"),
        "answer": answer_payload.get("answer"),
        "used_chunks": _redact_used_chunks(list(answer_payload.get("used_chunks", []))),
        "citations": [],
    }
    for item in answer_payload.get("citations", []):
        redacted_item = dict(item)
        redacted_item.pop("title", None)
        redacted_item.pop("path", None)
        redacted_item.pop("url", None)
        sanitized_answer["citations"].append(redacted_item)
    if "debug" in answer_payload:
        sanitized_answer["debug"] = _redact_debug_value(dict(answer_payload.get("debug", {})))
    if "debug_markdown" in answer_payload:
        sanitized_answer["debug_markdown"] = _redact_debug_markdown(
            str(answer_payload.get("debug_markdown", ""))
        )

    return {
        "query": debug_redaction.redact_debug_query_text(str(result.get("query", ""))),
        "question": debug_redaction.redact_debug_query_text(str(result.get("question", ""))),
        "same_input": str(result.get("query", "")) == str(result.get("question", "")),
        "source_id": result.get("source_id"),
        "top_k": result.get("top_k"),
        "search": {
            "results": search_results,
            "debug": _redact_debug_value(dict(result.get("search", {}).get("debug", {}))),
        },
        "answer": sanitized_answer,
    }


def format_smoke_summary(
    *,
    query: str,
    question: str,
    same_input: bool,
    source_id: str | None,
    top_k: int,
    search_payload: dict,
    answer_payload: dict,
) -> str:
    redacted_query = debug_redaction.redact_debug_query_text(query)
    redacted_question = debug_redaction.redact_debug_query_text(question)
    result_lines = [
        "ContextZip Live Query Smoke",
        "============================",
        f"search query: {redacted_query}",
        f"answer question: {redacted_question}",
        f"source filter: {source_id or '-'}",
        f"top_k: {top_k}",
        f"hits: {len(search_payload.get('results', []))}",
    ]
    for index, item in enumerate(search_payload.get("results", []), start=1):
        result_lines.append(
            "hit "
            f"{index}: "
            f"{item.get('source_id', '-')} | "
            f"{item.get('title', item.get('chunk_id', '-'))} | "
            f"{item.get('chunk_id', '-')} | "
            f"score={float(item.get('score', 0.0)):.3f}"
        )
    result_lines.append(
        f"helper answer preview: {answer_payload.get('evidence_status', '-')}"
    )
    result_lines.append(f"citations: {len(answer_payload.get('citations', []))}")
    for index, item in enumerate(answer_payload.get("citations", []), start=1):
        result_lines.append(
            "citation "
            f"{index}: "
            f"{item.get('title', item.get('chunk_id', '-'))} | "
            f"{item.get('chunk_id', '-')}"
        )
    result_lines.append(
        "inspect helper output: citations, used_chunks, debug, debug_markdown"
    )
    result_lines.append(
        "tip: use --json to inspect used_chunks, debug, and debug_markdown safely"
    )
    if same_input:
        result_lines.append(
            "same-input smoke path: retrieval and helper answer preview use the same input text above."
        )
    else:
        result_lines.append(
            "separate probes: retrieval summary describes the search query above, "
            "while helper answer status and citations describe the answer question."
        )
    return "\n".join(result_lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a live local retrieval and helper-answer smoke against the configured ContextZip environment."
    )
    parser.add_argument("--query", required=True, help="Search query for search_context.")
    parser.add_argument(
        "--question",
        help="Question for the helper answer preview. Defaults to the same text as --query.",
    )
    parser.add_argument(
        "--source-id",
        help="Optional retained source filter such as source_github or source_obsidian.",
    )
    parser.add_argument("--top-k", type=int, default=5, help="Top K for both search and answer calls.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print partially redacted JSON payloads for local debugging.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    question = args.question or args.query
    result = asyncio.run(
        run_live_query_smoke(
            query=args.query,
            question=question,
            source_id=args.source_id,
            top_k=args.top_k,
        )
    )
    if args.json:
        print(json.dumps(redact_live_query_result(result), ensure_ascii=False, indent=2))
        return
    print(
        format_smoke_summary(
            query=result["query"],
            question=result["question"],
            same_input=args.query == question,
            source_id=result["source_id"],
            top_k=result["top_k"],
            search_payload=result["search"],
            answer_payload=result["answer"],
        )
    )


if __name__ == "__main__":
    main()
