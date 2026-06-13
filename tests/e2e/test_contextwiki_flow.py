import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP
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

    def get_or_create_index(self):
        return object()


class FakeNode:
    def __init__(self, chunk_id, score):
        self.metadata = {"chunk_id": chunk_id, "contextwiki_managed": "true"}
        self.score = score


class FakeQueryRewriter:
    def __init__(self, rewrites):
        self.rewrites = list(rewrites)
        self.calls = []

    async def rewrite_query(self, query, term_groups):
        self.calls.append({"query": query, "term_groups": term_groups})
        return list(self.rewrites)


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


def _call_tool_json(mcp: FastMCP, name: str, arguments: dict | None = None) -> dict:
    blocks = asyncio.run(mcp.call_tool(name, arguments or {}))
    return json.loads(blocks[0].text)


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
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=ingestion,
        context_search_service=context_search,
        answer_service=answer_service,
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
    collection_search = asyncio.run(
        mcp.tools["search_context"](
            "ContextWiki 관련 문서 모아줘",
            filters={"source_ids": ["source_fake_docs"]},
            top_k=5,
            include_debug=True,
        )
    )
    chunk_id = search_result["results"][0]["chunk_id"]
    fetched = asyncio.run(mcp.tools["fetch_context"](chunk_id=chunk_id))
    answer = asyncio.run(mcp.tools["answer_with_citations"]("How does ContextWiki answer?"))
    collection_answer = asyncio.run(
        mcp.tools["answer_with_citations"](
            "ContextWiki 관련 문서 모아줘",
            filters={"source_ids": ["source_fake_docs"]},
            top_k=5,
        )
    )
    unsupported = asyncio.run(mcp.tools["answer_with_citations"]("What is the deployment region?"))

    assert sync_job["status"] == "succeeded"
    assert status["source"]["sync_status"] == "succeeded"
    assert search_result["results"][0]["title"] == "ContextWiki MVP"
    assert collection_search["debug"]["intent"]["name"] == "list"
    assert fetched["chunk"]["text"] == "ContextWiki syncs documents and answers with citations."
    assert answer["evidence_status"] == "grounded"
    assert answer["citations"][0]["chunk_id"] == chunk_id
    assert collection_answer["evidence_status"] == "grounded"
    assert "## Grounded List" in collection_answer["answer"]
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
        mcp = FakeMCP()
        register_tools(
            mcp,
            ingestion_service=ingestion,
            context_search_service=context_search,
            answer_service=answer_service,
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
    finally:
        Settings.embed_model = previous_embed_model


def test_contextwiki_e2e_phase1_alias_expansion_recovers_aws_document(tmp_path):
    class AliasConnector(SourceConnector):
        source = SourceModel(
            source_id="source_alias_docs",
            source_type=SourceType.NOTION,
            name="Alias Docs",
            enabled=True,
            auth_ref="env:FAKE",
            sync_status=SyncStatus.IDLE,
        )

        async def fetch_documents(self):
            return [
                DocumentModel(
                    id="doc_aws_alias",
                    document_id="doc_aws_alias",
                    external_id="doc_aws_alias",
                    source_id="source_alias_docs",
                    title="Cloud deployment checklist",
                    content="Cloud deployment checklist and launch notes.",
                    url="https://example.com/aws-deployment",
                    canonical_url="https://example.com/aws-deployment",
                    platform="Notion",
                    path="Cloud deployment checklist",
                    updated_at="2026-06-13T00:00:00Z",
                )
            ]

    retrieval_queries = []

    class AliasVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            retrieval_queries.append(query)
            if "amazon web services" not in query.lower():
                return []
            node = FakeNode(chunk_id, 0.91)
            node.metadata["document_id"] = "doc_aws_alias"
            node.metadata["source_id"] = "source_alias_docs"
            return [node]

    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    registry = SourceRegistry([AliasConnector()])
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=registry,
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )
    context_search = ContextSearchService(
        metadata_store=store,
        indexer=indexer,
        vector_retriever_cls=AliasVectorRetriever,
    )
    mcp = FastMCP("phase1-alias-expansion")
    register_tools(
        mcp,
        ingestion_service=ingestion,
        context_search_service=context_search,
        metadata_store=store,
        source_registry=registry,
    )

    sync_job = _call_tool_json(mcp, "sync_source", {"source_id": "source_alias_docs"})
    chunk_id = store.list_chunks_for_document("doc_aws_alias")[0].chunk_id
    search_result = _call_tool_json(
        mcp,
        "search_context",
        {
            "query": "AWS에 적은 문서 찾아줘",
            "filters": {"source_id": "source_alias_docs"},
            "top_k": 1,
        },
    )

    assert sync_job["status"] == "succeeded"
    assert len(search_result["results"]) == 1
    assert search_result["results"][0]["title"] == "Cloud deployment checklist"
    assert search_result["results"][0]["chunk_id"] == chunk_id
    assert any("amazon web services" in query.lower() for query in retrieval_queries)


def test_contextwiki_e2e_phase2_query_rewrite_recovers_rewrite_required_search_hit(tmp_path):
    class RewriteConnector(SourceConnector):
        source = SourceModel(
            source_id="source_rewrite_docs",
            source_type=SourceType.NOTION,
            name="Rewrite Docs",
            enabled=True,
            auth_ref="env:FAKE",
            sync_status=SyncStatus.IDLE,
        )

        async def fetch_documents(self):
            return [
                DocumentModel(
                    id="doc_ec2_setup",
                    document_id="doc_ec2_setup",
                    external_id="doc_ec2_setup",
                    source_id="source_rewrite_docs",
                    title="EC2 setup guide",
                    content="EC2 setup and instance launch notes.",
                    url="https://example.com/ec2-setup",
                    canonical_url="https://example.com/ec2-setup",
                    platform="Notion",
                    path="EC2 setup guide",
                    updated_at="2026-06-13T00:00:00Z",
                )
            ]

    retrieval_queries = []

    class RewriteVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            retrieval_queries.append(query)
            if "ec2" not in query.lower():
                return []
            node = FakeNode(chunk_id, 0.93)
            node.metadata["document_id"] = "doc_ec2_setup"
            node.metadata["source_id"] = "source_rewrite_docs"
            return [node]

    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    registry = SourceRegistry([RewriteConnector()])
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=registry,
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )
    baseline_search = ContextSearchService(
        metadata_store=store,
        indexer=indexer,
        vector_retriever_cls=RewriteVectorRetriever,
    )
    rewriter = FakeQueryRewriter(["aws ec2 setup"])
    rewrite_search = ContextSearchService(
        metadata_store=store,
        indexer=indexer,
        query_rewriter=rewriter,
        vector_retriever_cls=RewriteVectorRetriever,
    )
    baseline_mcp = FastMCP("phase2-baseline")
    rewrite_mcp = FastMCP("phase2-rewrite")
    register_tools(
        baseline_mcp,
        ingestion_service=ingestion,
        context_search_service=baseline_search,
        metadata_store=store,
        source_registry=registry,
    )
    register_tools(
        rewrite_mcp,
        ingestion_service=ingestion,
        context_search_service=rewrite_search,
        metadata_store=store,
        source_registry=registry,
    )

    sync_job = _call_tool_json(rewrite_mcp, "sync_source", {"source_id": "source_rewrite_docs"})
    chunk_id = store.list_chunks_for_document("doc_ec2_setup")[0].chunk_id
    baseline_result = _call_tool_json(
        baseline_mcp,
        "search_context",
        {
            "query": "aws virtual machine startup",
            "filters": {"source_id": "source_rewrite_docs"},
            "top_k": 1,
        },
    )
    baseline_queries = list(retrieval_queries)
    retrieval_queries.clear()
    rewrite_result = _call_tool_json(
        rewrite_mcp,
        "search_context",
        {
            "query": "aws virtual machine startup",
            "filters": {"source_id": "source_rewrite_docs"},
            "top_k": 1,
        },
    )

    assert sync_job["status"] == "succeeded"
    assert baseline_result["results"] == []
    assert "aws virtual machine startup" in baseline_queries
    assert "aws ec2 setup" not in baseline_queries
    assert rewriter.calls
    assert len(rewrite_result["results"]) == 1
    assert rewrite_result["results"][0]["title"] == "EC2 setup guide"
    assert rewrite_result["results"][0]["chunk_id"] == chunk_id
    assert "aws virtual machine startup" in retrieval_queries
    assert "aws ec2 setup" in retrieval_queries
    assert retrieval_queries.index("aws ec2 setup") > retrieval_queries.index("aws virtual machine startup")


def test_contextwiki_e2e_phase3_repository_lookup_prefers_docs_before_code(tmp_path):
    class GitHubDocsConnector(SourceConnector):
        source = SourceModel(
            source_id="source_github_docs_intent",
            source_type=SourceType.GITHUB,
            name="GitHub Docs Intent",
            enabled=True,
            auth_ref="env:FAKE",
            sync_status=SyncStatus.IDLE,
        )

        async def fetch_documents(self):
            documents = [
                DocumentModel(
                    id="github:eunhwa99/other:README.md",
                    document_id="github:eunhwa99/other:README.md",
                    external_id="github:eunhwa99/other:README.md",
                    source_id="source_github_docs_intent",
                    title="eunhwa99/other README",
                    content="Unrelated docs.",
                    url="https://github.com/eunhwa99/other/blob/main/README.md",
                    canonical_url="https://github.com/eunhwa99/other/blob/main/README.md",
                    platform="GitHub",
                    path="README.md",
                    updated_at="2026-06-13T00:00:00Z",
                )
            ]
            for index in range(64):
                path = f"src/aaa{index:03}.java"
                documents.append(
                    DocumentModel(
                        id=f"github:eunhwa99/ImageGallery:{path}",
                        document_id=f"github:eunhwa99/ImageGallery:{path}",
                        external_id=f"github:eunhwa99/ImageGallery:{path}",
                        source_id="source_github_docs_intent",
                        title=f"eunhwa99/ImageGallery {path}",
                        content="class Component {}",
                        url=f"https://github.com/eunhwa99/ImageGallery/blob/main/{path}",
                        canonical_url=f"https://github.com/eunhwa99/ImageGallery/blob/main/{path}",
                        platform="GitHub",
                        path=path,
                        updated_at="2026-06-13T00:00:00Z",
                    )
                )
            documents.append(
                DocumentModel(
                    id="github:eunhwa99/ImageGallery:docs/usage.md",
                    document_id="github:eunhwa99/ImageGallery:docs/usage.md",
                    external_id="github:eunhwa99/ImageGallery:docs/usage.md",
                    source_id="source_github_docs_intent",
                    title="eunhwa99/ImageGallery docs/usage.md",
                    content="Component usage notes.",
                    url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                    canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                    platform="GitHub",
                    path="docs/usage.md",
                    updated_at="2026-06-13T00:00:00Z",
                )
            )
            return documents

    retrieved_queries = []
    returned_candidate_ids = []

    class RepositoryVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            retrieved_queries.append(query)
            nodes = []
            docs_chunk = store.get_chunk(docs_chunk_id)
            docs_node = FakeNode(docs_chunk_id, 0.25)
            docs_node.metadata["document_id"] = docs_chunk.document_id
            docs_node.metadata["source_id"] = docs_chunk.source_id
            nodes.append(docs_node)
            returned_candidate_ids.append(docs_chunk_id)
            for chunk_id in code_chunk_ids[:8]:
                node = FakeNode(chunk_id, 0.95)
                chunk = store.get_chunk(chunk_id)
                node.metadata["document_id"] = chunk.document_id
                node.metadata["source_id"] = chunk.source_id
                nodes.append(node)
                returned_candidate_ids.append(chunk_id)
            return nodes

    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    registry = SourceRegistry([GitHubDocsConnector()])
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=registry,
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )
    context_search = ContextSearchService(
        metadata_store=store,
        indexer=indexer,
        vector_retriever_cls=RepositoryVectorRetriever,
    )
    mcp = FastMCP("phase3-docs-before-code")
    register_tools(
        mcp,
        ingestion_service=ingestion,
        context_search_service=context_search,
        metadata_store=store,
        source_registry=registry,
    )

    sync_job = _call_tool_json(mcp, "sync_source", {"source_id": "source_github_docs_intent"})
    code_document_count = sum(
        1
        for document in indexer.documents
        if document.source_id == "source_github_docs_intent" and document.path.endswith(".java")
    )
    code_chunk_ids = [
        store.list_chunks_for_document(document.document_id)[0].chunk_id
        for document in indexer.documents
        if document.source_id == "source_github_docs_intent" and document.path.endswith(".java")
    ]
    docs_chunk_id = store.list_chunks_for_document("github:eunhwa99/ImageGallery:docs/usage.md")[0].chunk_id
    search_result = _call_tool_json(
        mcp,
        "search_context",
        {
            "query": "ImageGallery",
            "filters": {"source_id": "source_github_docs_intent"},
            "top_k": 3,
        },
    )

    assert sync_job["status"] == "succeeded"
    assert code_document_count == 64
    assert retrieved_queries
    assert returned_candidate_ids[0] == docs_chunk_id
    assert returned_candidate_ids[1:9] == code_chunk_ids[:8]
    assert set(returned_candidate_ids) == {docs_chunk_id, *code_chunk_ids[:8]}
    assert len(search_result["results"]) >= 2
    assert search_result["results"][0]["path"] == "docs/usage.md"
    assert search_result["results"][0]["title"] == "eunhwa99/ImageGallery docs/usage.md"
    assert any(result["path"].endswith(".java") for result in search_result["results"][1:])
