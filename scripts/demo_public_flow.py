from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from llama_index.core import Document, Settings, StorageContext, VectorStoreIndex
from llama_index.core.embeddings import MockEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore

from api.tools import register_tools
from environments.config import AppConfig, setup_chroma
from fetching.connectors import ObsidianSourceConnector, SourceRegistry
from indexing.chunker import DocumentChunker
from indexing.indexer import ContentIndexer
from indexing.ingestion_service import IngestionService
from search.answer_service import CitationAnswerService
from search.context_service import ContextSearchService
from storage.metadata_store import MetadataStore


class DemoMCP:
    def __init__(self):
        self.tools: dict[str, object] = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class DemoContentIndexer(ContentIndexer):
    async def _batch_index(self, documents: list[Document]):
        total = len(documents)

        for index in range(0, total, self.config.batch_size):
            batch = documents[index : index + self.config.batch_size]

            if self.index is None:
                self.index = VectorStoreIndex.from_documents(
                    batch,
                    storage_context=self.storage_context,
                    show_progress=False,
                )
            else:
                for doc in batch:
                    self.index.insert(doc)

            processed = min(total, index + self.config.batch_size)
            self._update_progress(processed, total)
            await asyncio.sleep(0.1)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def build_demo_components(sample_vault: Path, temp_root: Path) -> DemoMCP:
    config = AppConfig(
        chroma_db_path=temp_root / "chroma",
        metadata_db_path=temp_root / "contextwiki.sqlite3",
        collection_name="contextwiki_demo",
        obsidian_vault_path=sample_vault,
        search_multiplier=4,
        search_llm_enabled=False,
    )
    chroma_collection = setup_chroma(config)
    storage_context = StorageContext.from_defaults(
        vector_store=ChromaVectorStore(chroma_collection=chroma_collection)
    )
    indexer = DemoContentIndexer(config, chroma_collection, storage_context)
    metadata_store = MetadataStore(config.metadata_db_path)
    source_registry = SourceRegistry([ObsidianSourceConnector(config)])
    ingestion_service = IngestionService(
        metadata_store=metadata_store,
        source_registry=source_registry,
        chunker=DocumentChunker(max_chars=500, overlap_chars=50),
        indexer=indexer,
    )
    context_search = ContextSearchService(
        metadata_store=metadata_store,
        indexer=indexer,
        config=config,
        default_source_ids=("source_obsidian",),
    )
    answer_service = CitationAnswerService(
        context_search=context_search,
        min_score=0.1,
        min_results=1,
    )
    mcp = DemoMCP()
    register_tools(
        mcp,
        ingestion_service=ingestion_service,
        context_search_service=context_search,
        answer_service=answer_service,
        metadata_store=metadata_store,
        source_registry=source_registry,
    )
    return mcp


async def run_demo(query: str, question: str) -> dict:
    previous_embed_model = Settings.embed_model
    Settings.embed_model = MockEmbedding(embed_dim=8)
    try:
        with tempfile.TemporaryDirectory(prefix="contextwiki-demo-") as temp_dir:
            repo_root = _repo_root()
            sample_vault = repo_root / "sample_vault"
            mcp = build_demo_components(sample_vault, Path(temp_dir))
            sync_payload = await mcp.tools["sync_source"]("source_obsidian")
            status_payload = await mcp.tools["get_sync_status"]("source_obsidian")
            search_payload = await mcp.tools["search_context"](
                query,
                filters={"source_id": "source_obsidian"},
                top_k=3,
            )
            answer_payload = await mcp.tools["answer_with_citations"](
                question,
                filters={"source_id": "source_obsidian"},
                top_k=3,
            )
            return normalize_demo_result(
                {
                "sample_vault": str(sample_vault.relative_to(repo_root)),
                "sync": sync_payload,
                "status": status_payload,
                "search": search_payload,
                "answer": answer_payload,
                }
            )
    finally:
        Settings.embed_model = previous_embed_model


def normalize_demo_result(result: dict) -> dict:
    normalized = json.loads(json.dumps(result))
    sync_payload = normalized.get("sync", {})
    for key in ("job_id", "started_at", "finished_at"):
        if key in sync_payload:
            sync_payload[key] = "<generated>"

    source_payload = normalized.get("status", {}).get("source", {})
    for key in ("last_synced_at", "created_at", "updated_at"):
        if key in source_payload:
            source_payload[key] = "<generated>"

    latest_job = normalized.get("status", {}).get("latest_job", {})
    for key in ("job_id", "started_at", "finished_at"):
        if key in latest_job:
            latest_job[key] = "<generated>"

    for search_item in normalized.get("search", {}).get("results", []):
        if "updated_at" in search_item:
            search_item["updated_at"] = "<generated>"
    return normalized


def render_demo_text(result: dict, query: str, question: str) -> str:
    lines = [
        "ContextWiki Public Demo",
        "=======================",
        f"Sample vault: {result['sample_vault']}",
        "",
        "1. Sync retained source",
        json.dumps(result["sync"], ensure_ascii=False, indent=2),
        "",
        "2. Source status",
        json.dumps(result["status"], ensure_ascii=False, indent=2),
        "",
        f"3. Search query: {query}",
        json.dumps(result["search"], ensure_ascii=False, indent=2),
        "",
        f"4. Grounded question: {question}",
        json.dumps(result["answer"], ensure_ascii=False, indent=2),
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the public ContextWiki demo against the bundled sample vault."
    )
    parser.add_argument(
        "--query",
        default="stale citations",
        help="Search query for the demo search_context step.",
    )
    parser.add_argument(
        "--question",
        default="How does ContextWiki prevent stale citations?",
        help="Grounded question for the demo answer_with_citations step.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON instead of the formatted demo transcript.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = asyncio.run(run_demo(args.query, args.question))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(render_demo_text(result, args.query, args.question))


if __name__ == "__main__":
    main()
