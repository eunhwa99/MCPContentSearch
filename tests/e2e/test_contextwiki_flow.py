import asyncio

import pytest
from llama_index.core import Settings, StorageContext
from llama_index.core.embeddings import MockEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore

from api.tools import register_tools
from core.models import DocumentModel, SourceModel, SourceType, SyncStatus
from environments.config import AppConfig, setup_chroma
from fetching.connectors import SourceConnector, SourceRegistry
from indexing.chunker import DocumentChunker
from indexing.indexer import ContentIndexer
from indexing.ingestion_service import IngestionService
from search.answer_service import CitationAnswerService
from search.context_service import ContextSearchService
from storage.metadata_store import MetadataStore
from wiki.service import WikiGenerationService


class FakeConnector(SourceConnector):
    source = SourceModel(
        source_id="source_fake_docs",
        source_type=SourceType.NOTION,
        name="Fake Docs",
        enabled=True,
        auth_ref="env:FAKE",
        sync_status=SyncStatus.IDLE,
    )

    async def fetch_documents(self):
        return [
            DocumentModel(
                id="doc_contextwiki",
                source_id="source_fake_docs",
                title="ContextWiki MVP",
                content="ContextWiki syncs documents and answers with citations.",
                url="https://example.com/contextwiki",
                platform="Notion",
                path="ContextWiki MVP",
                updated_at="2026-05-20T00:00:00Z",
            )
        ]


class OtherSourceConnector(SourceConnector):
    source = SourceModel(
        source_id="source_other",
        source_type=SourceType.TISTORY,
        name="Other Source",
        enabled=True,
        auth_ref="env:FAKE",
        sync_status=SyncStatus.IDLE,
    )

    async def fetch_documents(self):
        return [
            DocumentModel(
                id=f"doc_other_{index}",
                source_id="source_other",
                title=f"Other {index}",
                content="ContextWiki unrelated source mentions citations.",
                url=f"https://example.com/other/{index}",
                platform="Tistory",
                path=f"Other {index}",
                updated_at="2026-05-20T00:00:00Z",
            )
            for index in range(3)
        ]


class RecordingIndexer:
    def __init__(self):
        self.documents = []

    async def index_documents(self, documents):
        self.documents.extend(documents)

    def delete_documents_by_ids(self, document_ids, source_id=""):
        return None


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


pytestmark = pytest.mark.e2e


def test_contextwiki_fake_e2e_sync_search_fetch_and_answer(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    registry = SourceRegistry([FakeConnector()])
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=registry,
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    context_search = ContextSearchService(metadata_store=store, retriever=indexer.documents)
    answer_service = CitationAnswerService(context_search=context_search, min_score=0.1, min_results=1)
    wiki_service = WikiGenerationService(context_search)
    mcp = FakeMCP()
    register_tools(
        mcp,
        indexer=indexer,
        search_service=None,
        dynamic_search=None,
        web_searcher=None,
        ingestion_service=ingestion,
        context_search_service=context_search,
        answer_service=answer_service,
        wiki_service=wiki_service,
        metadata_store=store,
        source_registry=registry,
    )

    sync_job = asyncio.run(mcp.tools["sync_source"]("source_fake_docs"))
    status = asyncio.run(mcp.tools["get_sync_status"]("source_fake_docs"))
    search_result = asyncio.run(
        mcp.tools["search_context"](
            "citations",
            filters={"source_ids": ["source_fake_docs"]},
            top_k=5,
        )
    )
    chunk_id = search_result["results"][0]["chunk_id"]
    fetched = asyncio.run(mcp.tools["fetch_context"](chunk_id=chunk_id))
    answer = asyncio.run(mcp.tools["answer_with_citations"]("How does ContextWiki answer?"))
    wiki_page = asyncio.run(
        mcp.tools["generate_wiki_page"](
            "ContextWiki citations",
            filters={"source_ids": ["source_fake_docs"]},
            top_k=5,
        )
    )
    unsupported = asyncio.run(mcp.tools["answer_with_citations"]("What is the deployment region?"))

    assert sync_job["status"] == "succeeded"
    assert status["source"]["sync_status"] == "succeeded"
    assert search_result["results"][0]["title"] == "ContextWiki MVP"
    assert fetched["chunk"]["text"] == "ContextWiki syncs documents and answers with citations."
    assert answer["evidence_status"] == "grounded"
    assert answer["citations"][0]["chunk_id"] == chunk_id
    assert wiki_page["status"] == "generated"
    assert wiki_page["citations"][0]["chunk_id"] == chunk_id
    assert wiki_page["backlinks"][0]["document_id"] == "doc_contextwiki"
    assert unsupported["evidence_status"] == "insufficient"


def test_context_search_applies_source_filter_before_result_limit(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    registry = SourceRegistry([OtherSourceConnector(), FakeConnector()])
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=registry,
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )
    asyncio.run(ingestion.sync_source("source_other"))
    asyncio.run(ingestion.sync_source("source_fake_docs"))
    context_search = ContextSearchService(metadata_store=store, retriever=indexer.documents)

    result = asyncio.run(
        context_search.search_context(
            "ContextWiki citations",
            filters={"source_ids": ["source_fake_docs"]},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].source_id == "source_fake_docs"


def test_contextwiki_temp_chroma_e2e_sync_search_fetch_and_answer(tmp_path):
    previous_embed_model = Settings.embed_model
    Settings.embed_model = MockEmbedding(embed_dim=8)
    try:
        config = AppConfig(
            chroma_db_path=tmp_path / "chroma",
            metadata_db_path=tmp_path / "contextwiki.sqlite3",
            collection_name="contextwiki_e2e",
            search_multiplier=4,
        )
        chroma_collection = setup_chroma(config)
        storage_context = StorageContext.from_defaults(
            vector_store=ChromaVectorStore(chroma_collection=chroma_collection)
        )
        indexer = ContentIndexer(config, chroma_collection, storage_context)
        store = MetadataStore(config.metadata_db_path)
        registry = SourceRegistry([OtherSourceConnector(), FakeConnector()])
        ingestion = IngestionService(
            metadata_store=store,
            source_registry=registry,
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=indexer,
        )
        context_search = ContextSearchService(metadata_store=store, indexer=indexer, config=config)
        answer_service = CitationAnswerService(context_search=context_search, min_score=0.1, min_results=1)
        wiki_service = WikiGenerationService(context_search)
        mcp = FakeMCP()
        register_tools(
            mcp,
            indexer=indexer,
            search_service=None,
            dynamic_search=None,
            web_searcher=None,
            ingestion_service=ingestion,
            context_search_service=context_search,
            answer_service=answer_service,
            wiki_service=wiki_service,
            metadata_store=store,
            source_registry=registry,
        )

        asyncio.run(
            indexer.index_documents(
                [
                    DocumentModel(
                        id="legacy_raw_doc",
                        title="Legacy raw document",
                        content="ContextWiki citations from an unmanaged legacy document.",
                        url="https://example.com/legacy",
                        platform="Legacy",
                    )
                ]
            )
        )
        other_job = asyncio.run(mcp.tools["sync_source"]("source_other"))
        target_job = asyncio.run(mcp.tools["sync_source"]("source_fake_docs"))
        status = asyncio.run(mcp.tools["get_sync_status"]("source_fake_docs"))
        search_result = asyncio.run(
            mcp.tools["search_context"](
                "ContextWiki citations",
                filters={"source_id": "source_fake_docs"},
                top_k=1,
            )
        )
        chunk_id = search_result["results"][0]["chunk_id"]
        fetched = asyncio.run(mcp.tools["fetch_context"](chunk_id=chunk_id))
        answer = asyncio.run(
            mcp.tools["answer_with_citations"](
                "How does ContextWiki answer?",
                filters={"source_id": "source_fake_docs"},
                top_k=1,
            )
        )
        wiki_page = asyncio.run(
            mcp.tools["generate_wiki_page"](
                "ContextWiki citations",
                filters={"source_id": "source_fake_docs"},
                top_k=1,
            )
        )
        metadatas = chroma_collection.get(include=["metadatas"])["metadatas"]

        assert other_job["status"] == "succeeded"
        assert target_job["status"] == "succeeded"
        assert status["source"]["sync_status"] == "succeeded"
        assert chroma_collection.count() >= 3
        assert any(metadata.get("contextwiki_managed") == "false" for metadata in metadatas)
        assert any(metadata.get("contextwiki_managed") == "true" for metadata in metadatas)
        assert search_result["results"][0]["source_id"] == "source_fake_docs"
        assert fetched["chunk"]["text"] == "ContextWiki syncs documents and answers with citations."
        assert answer["evidence_status"] == "grounded"
        assert answer["used_chunks"] == [chunk_id]
        assert wiki_page["status"] == "generated"
        assert wiki_page["used_chunks"] == [chunk_id]
    finally:
        Settings.embed_model = previous_embed_model
