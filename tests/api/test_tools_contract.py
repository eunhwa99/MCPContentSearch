import asyncio

import pytest

from api.tools import register_tools
from core.models import ContextSearchResult, DocumentModel
from indexing.background_tasks import BackgroundTaskRegistry


pytestmark = pytest.mark.integration


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(func):
            self.tools[func.__name__] = func
            return func

        return decorator


class FakeIndexer:
    class Status:
        state = "idle"

        def model_dump(self):
            return {"state": "idle"}

    status = Status()

    async def index_documents(self, documents):
        return None


class FakeFailingIndexer(FakeIndexer):
    async def index_documents(self, documents):
        raise RuntimeError("index failed with token=super-secret-value")


class FakeErrorStatusIndexer(FakeIndexer):
    class Status:
        state = "error"

        def model_dump(self):
            return {
                "state": "error",
                "message": (
                    "Error: token=super-secret-value "
                    "AKIAIOSFODNN7EXAMPLE "
                    "Authorization: Basic dXNlcjpwYXNzd29yZA=="
                ),
            }

    status = Status()


class FakeWebSearcher:
    async def search(self, query, n_results=10, platforms=None):
        platform = platforms[0] if platforms else "web"
        return [
            DocumentModel(
                id=f"{platform}-doc",
                title=f"{platform.title()} Doc",
                content="background indexing evidence",
                url=f"https://example.com/{platform}",
                platform=platform,
                date="2026-05-31",
            )
        ]


class FakeLocalSearch:
    async def search(self, query, n_results=10):
        return "Total 0 documents found"


async def wait_for_index_status(mcp):
    status = await mcp.tools["get_index_status"]()
    for _ in range(5):
        if status["background_tasks"] and status["background_tasks"][-1]["state"] in {
            "succeeded",
            "failed",
        }:
            return status
        await asyncio.sleep(0)
        status = await mcp.tools["get_index_status"]()
    return status


class FakeFailingIngestion:
    async def sync_source(self, source_id):
        raise ValueError(f"Unknown source: {source_id}")


class Dumpable:
    def __init__(self, value, **attrs):
        self.value = value
        for key, attr_value in attrs.items():
            setattr(self, key, attr_value)

    def model_dump(self, mode="json"):
        return self.value


class FakeMetadataStore:
    def __init__(self):
        self.source = Dumpable(
            {"source_id": "source_fake", "sync_status": "succeeded"},
            source_id="source_fake",
        )
        self.job = Dumpable({"job_id": "job-1", "status": "succeeded"})
        self.chunk = Dumpable({"chunk_id": "chunk-1", "text": "ContextWiki evidence"})

    def list_sources(self):
        return [self.source]

    def get_latest_sync_job(self, source_id):
        return self.job

    def get_source(self, source_id):
        return self.source

    def get_chunk(self, chunk_id):
        return self.chunk

    def get_document(self, document_id):
        return None

    def list_chunks_for_document(self, document_id):
        return []


class FakeTombstonedMetadataStore(FakeMetadataStore):
    def get_document(self, document_id):
        return Dumpable(
            {
                "document_id": document_id,
                "content": "deleted content",
                "deleted_at": "2026-05-22T00:00:00Z",
            },
            deleted_at="2026-05-22T00:00:00Z",
        )


class RecoveringStatusMetadataStore(FakeMetadataStore):
    def __init__(self):
        super().__init__()
        self.source = Dumpable(
            {"source_id": "source_fake", "sync_status": "running"},
            source_id="source_fake",
        )
        self.recovered = Dumpable(
            {"source_id": "source_fake", "sync_status": "failed"},
            source_id="source_fake",
        )
        self.job = Dumpable({"job_id": "job-stale", "status": "failed"})

    def get_latest_sync_job(self, source_id):
        self.source = self.recovered
        return self.job


class FakeContextSearch:
    async def search_context(self, query, filters=None, top_k=10):
        return {
            "query": query,
            "results": [
                ContextSearchResult(
                    chunk_id="chunk-1",
                    document_id="doc-1",
                    source_id="source_fake",
                    source_type="notion",
                    title="ContextWiki",
                    score=0.9,
                    preview="ContextWiki evidence",
                    text="ContextWiki evidence",
                )
            ],
        }


class FakeAnswerService:
    async def answer_with_citations(self, question, filters=None, top_k=5):
        return {
            "question": question,
            "answer": "ContextWiki evidence",
            "evidence_status": "grounded",
            "citations": [{"chunk_id": "chunk-1"}],
            "used_chunks": ["chunk-1"],
        }


class FakeWikiService:
    async def generate_wiki_page(self, topic, filters=None, top_k=8):
        return {
            "topic": topic,
            "status": "generated",
            "title": f"{topic} Wiki",
            "markdown": "# ContextWiki Wiki\n\nContextWiki evidence [C1]\n",
            "sections": [{"heading": "Overview", "content": "ContextWiki evidence [C1]"}],
            "citations": [{"marker": "C1", "chunk_id": "chunk-1"}],
            "backlinks": [{"document_id": "doc-1", "chunk_ids": ["chunk-1"]}],
            "used_chunks": ["chunk-1"],
        }


def test_sync_source_returns_structured_error_for_unknown_source():
    mcp = FakeMCP()
    register_tools(
        mcp,
        indexer=FakeIndexer(),
        search_service=None,
        dynamic_search=None,
        web_searcher=None,
        ingestion_service=FakeFailingIngestion(),
    )

    result = asyncio.run(mcp.tools["sync_source"]("missing"))

    assert result["status"] == "error"
    assert "Unknown source" in result["message"]


def test_fetch_context_hides_tombstoned_documents():
    mcp = FakeMCP()
    register_tools(
        mcp,
        indexer=FakeIndexer(),
        search_service=None,
        dynamic_search=None,
        web_searcher=None,
        metadata_store=FakeTombstonedMetadataStore(),
    )

    result = asyncio.run(mcp.tools["fetch_context"](document_id="deleted-doc"))

    assert result["document"] is None
    assert result["chunks"] == []


def test_contextwiki_mcp_tools_are_registered():
    mcp = FakeMCP()
    register_tools(
        mcp,
        indexer=FakeIndexer(),
        search_service=None,
        dynamic_search=None,
        web_searcher=None,
    )

    assert {
        "list_sources",
        "sync_source",
        "get_sync_status",
        "search_context",
        "fetch_context",
        "answer_with_citations",
        "generate_wiki_page",
    }.issubset(mcp.tools)


def test_contextwiki_mcp_tools_return_contract_shapes():
    mcp = FakeMCP()
    register_tools(
        mcp,
        indexer=FakeIndexer(),
        search_service=None,
        dynamic_search=None,
        web_searcher=None,
        metadata_store=FakeMetadataStore(),
        context_search_service=FakeContextSearch(),
        answer_service=FakeAnswerService(),
        wiki_service=FakeWikiService(),
    )

    status = asyncio.run(mcp.tools["get_sync_status"]())
    search = asyncio.run(mcp.tools["search_context"]("ContextWiki"))
    fetched = asyncio.run(mcp.tools["fetch_context"](chunk_id="chunk-1"))
    answer = asyncio.run(mcp.tools["answer_with_citations"]("What is ContextWiki?"))
    wiki = asyncio.run(mcp.tools["generate_wiki_page"]("ContextWiki"))

    assert status["sources"][0]["source"]["source_id"] == "source_fake"
    assert search["results"][0]["chunk_id"] == "chunk-1"
    assert fetched["chunk"]["chunk_id"] == "chunk-1"
    assert answer["evidence_status"] == "grounded"
    assert wiki["status"] == "generated"
    assert wiki["citations"][0]["chunk_id"] == "chunk-1"


def test_generate_wiki_page_returns_configured_error_without_service():
    mcp = FakeMCP()
    register_tools(
        mcp,
        indexer=FakeIndexer(),
        search_service=None,
        dynamic_search=None,
        web_searcher=None,
    )

    result = asyncio.run(mcp.tools["generate_wiki_page"]("ContextWiki"))

    assert result["status"] == "not_configured"
    assert result["citations"] == []
    assert "not configured" in result["message"]


def test_get_sync_status_returns_source_after_status_recovery():
    mcp = FakeMCP()
    register_tools(
        mcp,
        indexer=FakeIndexer(),
        search_service=None,
        dynamic_search=None,
        web_searcher=None,
        metadata_store=RecoveringStatusMetadataStore(),
    )

    single = asyncio.run(mcp.tools["get_sync_status"]("source_fake"))
    all_sources = asyncio.run(mcp.tools["get_sync_status"]())

    assert single["source"]["sync_status"] == "failed"
    assert single["latest_job"]["status"] == "failed"
    assert all_sources["sources"][0]["source"]["sync_status"] == "failed"
    assert all_sources["sources"][0]["latest_job"]["status"] == "failed"


@pytest.mark.parametrize(
    ("tool_name", "expected_label"),
    [
        ("search_notion", "search_notion"),
        ("search_tistory", "search_tistory"),
    ],
)
def test_get_index_status_reports_legacy_search_background_failures(tool_name, expected_label):
    async def run_tool_and_status():
        mcp = FakeMCP()
        register_tools(
            mcp,
            indexer=FakeFailingIndexer(),
            search_service=None,
            dynamic_search=None,
            web_searcher=FakeWebSearcher(),
            background_task_registry=BackgroundTaskRegistry(),
        )

        await mcp.tools[tool_name]("background status")
        return await wait_for_index_status(mcp)

    status = asyncio.run(run_tool_and_status())

    assert status["state"] == "idle"
    task = status["background_tasks"][-1]
    assert task["label"] == expected_label
    assert task["state"] == "failed"
    assert task["total_docs"] == 1
    assert task["processed_docs"] == 0
    assert "super-secret-value" not in task["error"]
    assert "token=<redacted>" in task["error"]


def test_get_index_status_redacts_top_level_error_message():
    mcp = FakeMCP()
    register_tools(
        mcp,
        indexer=FakeErrorStatusIndexer(),
        search_service=None,
        dynamic_search=None,
        web_searcher=None,
        background_task_registry=BackgroundTaskRegistry(),
    )

    status = asyncio.run(mcp.tools["get_index_status"]())

    assert status["state"] == "error"
    assert "super-secret-value" not in status["message"]
    assert "AKIAIOSFODNN7EXAMPLE" not in status["message"]
    assert "Basic dXNlcjpwYXNzd29yZA==" not in status["message"]
    assert "token=<redacted>" in status["message"]


def test_get_index_status_reports_search_content_fallback_background_task():
    from search.dynamic_search import DynamicSearchService

    async def run_tool_and_status():
        mcp = FakeMCP()
        registry = BackgroundTaskRegistry()
        dynamic_search = DynamicSearchService(
            local_search=FakeLocalSearch(),
            web_searcher=FakeWebSearcher(),
            indexer=FakeIndexer(),
            background_task_registry=registry,
        )
        register_tools(
            mcp,
            indexer=FakeIndexer(),
            search_service=None,
            dynamic_search=dynamic_search,
            web_searcher=FakeWebSearcher(),
            background_task_registry=registry,
        )

        await mcp.tools["search_content"]("fallback topic")
        return await wait_for_index_status(mcp)

    status = asyncio.run(run_tool_and_status())

    task = status["background_tasks"][-1]
    assert task["label"] == "search_content_fallback"
    assert task["state"] == "succeeded"
    assert task["total_docs"] == 1
    assert task["processed_docs"] == 1


def test_get_index_status_reports_trigger_index_all_content_task(monkeypatch):
    class FakeFetcher:
        def __init__(self, config, notion_api_key, tistory_blog_name):
            pass

        async def fetch_all(self):
            return [
                DocumentModel(
                    id="full-index-doc",
                    title="Full Index Doc",
                    content="background indexing evidence",
                    url="https://example.com/full-index",
                    platform="web",
                )
            ]

    async def run_tool_and_status():
        mcp = FakeMCP()
        registry = BackgroundTaskRegistry()
        monkeypatch.setattr("api.tools.DocumentFetcher", FakeFetcher)
        register_tools(
            mcp,
            indexer=FakeIndexer(),
            search_service=None,
            dynamic_search=None,
            web_searcher=FakeWebSearcher(),
            background_task_registry=registry,
        )

        await mcp.tools["trigger_index_all_content"]()
        return await wait_for_index_status(mcp)

    status = asyncio.run(run_tool_and_status())

    task = status["background_tasks"][-1]
    assert task["label"] == "trigger_index_all_content"
    assert task["state"] == "succeeded"
    assert task["total_docs"] == 1
    assert task["processed_docs"] == 1
