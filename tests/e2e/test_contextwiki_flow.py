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


async def _call_tool_json_async(mcp: FastMCP, name: str, arguments: dict | None = None) -> dict:
    blocks = await mcp.call_tool(name, arguments or {})
    return json.loads(blocks[0].text)


async def _wait_for_sync_completion(mcp, source_id: str, attempts: int = 500) -> dict:
    latest = None
    for _ in range(attempts):
        if hasattr(mcp, "tools"):
            latest = await mcp.tools["get_sync_status"](source_id)
        else:
            latest = await _call_tool_json_async(
                mcp,
                "get_sync_status",
                {"source_id": source_id},
            )
        latest_job = latest.get("latest_job") or {}
        if latest_job.get("status") in {"succeeded", "failed"}:
            return latest
        await asyncio.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {source_id} sync completion: {latest}")


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

    async def run_flow():
        sync_job = await mcp.tools["sync_source"]("source_fake_docs")
        status = await _wait_for_sync_completion(mcp, "source_fake_docs")
        search_result = await mcp.tools["search_context"](
            "citations",
            filters={"source_ids": ["source_fake_docs"]},
            top_k=5,
        )
        collection_search = await mcp.tools["search_context"](
            "ContextWiki 관련 문서 모아줘",
            filters={"source_ids": ["source_fake_docs"]},
            top_k=5,
            include_debug=True,
        )
        document_search = await mcp.tools["search_documents"](
            "citations",
            filters={"source_ids": ["source_fake_docs"]},
            top_k=5,
        )
        chunk_id = search_result["results"][0]["chunk_id"]
        fetched = await mcp.tools["fetch_context"](chunk_id=chunk_id)
        answer = await answer_service.answer_with_citations("How does ContextWiki answer?")
        collection_answer = await answer_service.answer_with_citations(
            "ContextWiki 관련 문서 모아줘",
            filters={"source_ids": ["source_fake_docs"]},
            top_k=5,
        )
        unsupported = await answer_service.answer_with_citations("What is the deployment region?")
        return (
            sync_job,
            status,
            search_result,
            collection_search,
            document_search,
            chunk_id,
            fetched,
            answer,
            collection_answer,
            unsupported,
        )

    (
        sync_job,
        status,
        search_result,
        collection_search,
        document_search,
        chunk_id,
        fetched,
        answer,
        collection_answer,
        unsupported,
    ) = asyncio.run(run_flow())

    assert sync_job["status"] == "running"
    assert status["source"]["sync_status"] == "succeeded"
    assert search_result["results"][0]["title"] == "ContextWiki MVP"
    assert collection_search["debug"]["intent"]["name"] == "list"
    assert document_search["results"][0]["matched_context"] == (
        "ContextWiki syncs documents and answers with citations."
    )
    assert "preview" not in document_search["results"][0]
    assert "vector_score" not in document_search["results"][0]
    assert "metadata_priority" not in document_search["results"][0]
    assert fetched["chunk"]["text"] == "ContextWiki syncs documents and answers with citations."
    assert answer["evidence_status"] == "grounded"
    assert answer["citations"][0]["chunk_id"] == chunk_id
    assert collection_answer["evidence_status"] == "grounded"
    assert "## Grounded List" in collection_answer["answer"]
    assert unsupported["evidence_status"] == "insufficient"


def test_contextwiki_fastmcp_sync_all_returns_before_connectors_finish(tmp_path):
    class BlockingE2EConnector(SourceConnector):
        def __init__(
            self,
            *,
            source_id: str,
            source_type: SourceType,
            entered: asyncio.Event,
            release: asyncio.Event,
            finished: asyncio.Event,
        ):
            self.source = SourceModel(
                source_id=source_id,
                source_type=source_type,
                name=source_id,
                enabled=True,
                auth_ref="env:FAKE",
                sync_status=SyncStatus.IDLE,
            )
            self.entered = entered
            self.release = release
            self.finished = finished

        async def fetch_documents(self):
            self.entered.set()
            await self.release.wait()
            self.finished.set()
            return [
                DocumentModel(
                    id=f"doc_{self.source.source_id}",
                    source_id=self.source.source_id,
                    title=f"Document for {self.source.source_id}",
                    content=f"Content from {self.source.source_id}.",
                    url=f"https://example.com/{self.source.source_id}",
                    platform=self.source.source_type.value,
                    path=f"{self.source.source_id}.md",
                    updated_at="2026-07-29T00:00:00Z",
                )
            ]

    async def run_flow():
        first_entered = asyncio.Event()
        first_release = asyncio.Event()
        first_finished = asyncio.Event()
        second_entered = asyncio.Event()
        second_release = asyncio.Event()
        second_finished = asyncio.Event()
        registry = SourceRegistry(
            [
                BlockingE2EConnector(
                    source_id="source_e2e_first",
                    source_type=SourceType.NOTION,
                    entered=first_entered,
                    release=first_release,
                    finished=first_finished,
                ),
                BlockingE2EConnector(
                    source_id="source_e2e_second",
                    source_type=SourceType.TISTORY,
                    entered=second_entered,
                    release=second_release,
                    finished=second_finished,
                ),
            ]
        )
        store = MetadataStore(tmp_path / "sync-all-contextwiki.sqlite3")
        ingestion = IngestionService(
            metadata_store=store,
            source_registry=registry,
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=RecordingIndexer(),
        )
        mcp = FastMCP("background-sync-all-e2e")
        register_tools(
            mcp,
            ingestion_service=ingestion,
            metadata_store=store,
            source_registry=registry,
        )

        try:
            sync_all_call = asyncio.create_task(_call_tool_json_async(mcp, "sync_all"))
            await asyncio.wait_for(
                asyncio.gather(first_entered.wait(), second_entered.wait()),
                timeout=1,
            )
            sync_all_result = await asyncio.wait_for(sync_all_call, timeout=0.5)

            assert sync_all_result["status"] == "accepted"
            assert sync_all_result["summary"]["started"] == 2
            assert {
                (
                    item["source_id"],
                    item["launch_outcome"],
                    item["job"]["status"],
                )
                for item in sync_all_result["results"]
            } == {
                ("source_e2e_first", "started", "running"),
                ("source_e2e_second", "started", "running"),
            }
            assert not first_finished.is_set()
            assert not second_finished.is_set()

            first_running = await _call_tool_json_async(
                mcp,
                "get_sync_status",
                {"source_id": "source_e2e_first"},
            )
            second_running = await _call_tool_json_async(
                mcp,
                "get_sync_status",
                {"source_id": "source_e2e_second"},
            )
            assert first_running["latest_job"]["status"] == "running"
            assert second_running["latest_job"]["status"] == "running"

            first_release.set()
            second_release.set()
            first_terminal, second_terminal = await asyncio.gather(
                _wait_for_sync_completion(mcp, "source_e2e_first"),
                _wait_for_sync_completion(mcp, "source_e2e_second"),
            )
            return first_terminal, second_terminal, first_finished, second_finished
        finally:
            first_release.set()
            second_release.set()
            if ingestion._background_sync_tasks:
                await asyncio.gather(
                    *ingestion._background_sync_tasks.values(),
                    return_exceptions=True,
                )

    first_terminal, second_terminal, first_finished, second_finished = asyncio.run(run_flow())

    assert first_finished.is_set()
    assert second_finished.is_set()
    assert first_terminal["source"]["sync_status"] == "succeeded"
    assert first_terminal["latest_job"]["status"] == "succeeded"
    assert second_terminal["source"]["sync_status"] == "succeeded"
    assert second_terminal["latest_job"]["status"] == "succeeded"


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
        async def run_flow():
            other_job = await mcp.tools["sync_source"]("source_other")
            target_job = await mcp.tools["sync_source"]("source_fake_docs")
            await _wait_for_sync_completion(mcp, "source_other")
            status = await _wait_for_sync_completion(mcp, "source_fake_docs")
            search_result = await mcp.tools["search_context"](
                "ContextWiki citations",
                filters={"source_id": "source_fake_docs"},
                top_k=1,
            )
            chunk_id = search_result["results"][0]["chunk_id"]
            fetched = await mcp.tools["fetch_context"](chunk_id=chunk_id)
            answer = await answer_service.answer_with_citations(
                "How does ContextWiki answer?",
                filters={"source_id": "source_fake_docs"},
                top_k=1,
            )
            return other_job, target_job, status, search_result, chunk_id, fetched, answer

        other_job, target_job, status, search_result, chunk_id, fetched, answer = asyncio.run(
            run_flow()
        )
        metadatas = chroma_collection.get(include=["metadatas"])["metadatas"]

        assert other_job["status"] == "running"
        assert target_job["status"] == "running"
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

    async def run_flow():
        sync_job = await _call_tool_json_async(mcp, "sync_source", {"source_id": "source_alias_docs"})
        await _wait_for_sync_completion(mcp, "source_alias_docs")
        return sync_job

    sync_job = asyncio.run(run_flow())
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

    assert sync_job["status"] == "running"
    assert len(search_result["results"]) == 1
    assert search_result["results"][0]["title"] == "Cloud deployment checklist"
    assert search_result["results"][0]["chunk_id"] == chunk_id
    assert any("amazon web services" in query.lower() for query in retrieval_queries)


def test_contextwiki_e2e_uses_only_deterministic_query_variants(tmp_path):
    class DeterministicConnector(SourceConnector):
        source = SourceModel(
            source_id="source_deterministic_docs",
            source_type=SourceType.NOTION,
            name="Deterministic Docs",
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
                    source_id="source_deterministic_docs",
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

    class DeterministicVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            retrieval_queries.append(query)
            if "ec2" not in query.lower():
                return []
            node = FakeNode("ec2-chunk", 0.93)
            node.metadata["document_id"] = "doc_ec2_setup"
            node.metadata["source_id"] = "source_deterministic_docs"
            return [node]

    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    registry = SourceRegistry([DeterministicConnector()])
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=registry,
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )
    search = ContextSearchService(
        metadata_store=store,
        indexer=indexer,
        vector_retriever_cls=DeterministicVectorRetriever,
    )
    mcp = FastMCP("deterministic-query-variants")
    register_tools(
        mcp,
        ingestion_service=ingestion,
        context_search_service=search,
        metadata_store=store,
        source_registry=registry,
    )

    async def run_flow():
        sync_job = await _call_tool_json_async(
            mcp,
            "sync_source",
            {"source_id": "source_deterministic_docs"},
        )
        await _wait_for_sync_completion(mcp, "source_deterministic_docs")
        return sync_job

    sync_job = asyncio.run(run_flow())
    result = _call_tool_json(
        mcp,
        "search_context",
        {
            "query": "aws virtual machine startup",
            "filters": {"source_id": "source_deterministic_docs"},
            "top_k": 1,
        },
    )

    assert sync_job["status"] == "running"
    assert result["results"] == []
    assert "aws virtual machine startup" in retrieval_queries
    assert "aws ec2 setup" not in retrieval_queries


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

    async def run_flow():
        sync_job = await _call_tool_json_async(
            mcp,
            "sync_source",
            {"source_id": "source_github_docs_intent"},
        )
        await _wait_for_sync_completion(mcp, "source_github_docs_intent")
        return sync_job

    sync_job = asyncio.run(run_flow())
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

    assert sync_job["status"] == "running"
    assert code_document_count == 64
    assert retrieved_queries
    assert returned_candidate_ids[0] == docs_chunk_id
    assert returned_candidate_ids[1:9] == code_chunk_ids[:8]
    assert set(returned_candidate_ids) == {docs_chunk_id, *code_chunk_ids[:8]}
    assert len(search_result["results"]) >= 2
    assert search_result["results"][0]["path"] == "docs/usage.md"
    assert search_result["results"][0]["title"] == "eunhwa99/ImageGallery docs/usage.md"
    assert any(result["path"].endswith(".java") for result in search_result["results"][1:])
