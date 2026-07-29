import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from api.tools import register_tools
from core.models import ChunkModel, DocumentModel, SourceModel, SourceType, SyncStatus
from search.context_service import ContextSearchService
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.integration


class Dumpable:
    def __init__(self, value, **attrs):
        self.value = value
        for key, attr_value in attrs.items():
            setattr(self, key, attr_value)

    def model_dump(self, mode="json"):
        return self.value


class FakeSourceRegistry:
    def __init__(self):
        self.sources = [
            SourceModel(
                source_id="source_github",
                source_type=SourceType.GITHUB,
                name="GitHub",
                enabled=True,
                auth_ref="env:GITHUB_TOKEN",
                sync_status=SyncStatus.SUCCEEDED,
                last_synced_at="2026-06-15T00:00:00+00:00",
            ),
            SourceModel(
                source_id="source_obsidian",
                source_type=SourceType.OBSIDIAN,
                name="Obsidian",
                enabled=True,
                auth_ref="env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH",
                sync_status=SyncStatus.SUCCEEDED,
                last_synced_at="2026-06-15T00:00:00+00:00",
            ),
        ]
        self.connectors = {
            source.source_id: Dumpable(
                {},
                source=source,
                supports_stale_cleanup=True,
                disabled_reason="",
                stale_cleanup_disabled_reason="",
            )
            for source in self.sources
        }

    def list_sources(self):
        return list(self.sources)

    def get_connector(self, source_id):
        return self.connectors[source_id]


class FakeMetadataStore:
    def __init__(self, source_registry: FakeSourceRegistry):
        self.sources = {source.source_id: source for source in source_registry.list_sources()}
        self.jobs = {
            source_id: Dumpable(
                {
                    "job_id": f"job-{source_id}",
                    "source_id": source_id,
                    "status": "succeeded",
                    "started_at": "2026-06-15T00:00:00+00:00",
                    "finished_at": "2026-06-15T00:00:01+00:00",
                    "error_message": "",
                }
            )
            for source_id in self.sources
        }
        self.chunk = ChunkModel(
            chunk_id="chunk-1",
            document_id="doc-1",
            source_id="source_github",
            title="ContextWiki contracts",
            text="ContextWiki validates MCP contracts through real call_tool paths.",
            url="https://example.com/contracts",
            path="docs/contracts.md",
            chunk_index=0,
            content_hash="chunk-1",
        )
        self.document = DocumentModel(
            id="doc-1",
            document_id="doc-1",
            external_id="doc-1",
            source_id="source_github",
            title="ContextWiki contracts",
            content="ContextWiki validates MCP contracts through real call_tool paths.",
            url="https://example.com/contracts",
            canonical_url="https://example.com/contracts",
            platform="GitHub",
            path="docs/contracts.md",
            chunk_id="chunk-1",
            published_at="2026-06-01T00:00:00Z",
            modified_at="2026-06-02T00:00:00Z",
            indexed_at="2026-06-03T00:00:00Z",
            date_provenance="test",
        )

    def set_job(self, source_id, job_payload):
        self.jobs[source_id] = Dumpable(job_payload, **job_payload)

    def register_source(self, source):
        self.sources[source.source_id] = source
        return source

    def list_sources(self):
        return list(self.sources.values())

    def get_source(self, source_id):
        return self.sources.get(source_id)

    def get_latest_sync_job(self, source_id):
        return self.jobs.get(source_id)

    def get_source_status_snapshot(self, source_id):
        return {
            "latest_success_at": "2026-06-15T00:00:01+00:00",
            "latest_failure_at": "",
            "latest_failure_reason": "",
            "document_count": 1,
            "chunk_count": 1,
        }

    def get_chunk(self, chunk_id):
        assert chunk_id == "chunk-1"
        return self.chunk

    def get_document(self, document_id):
        assert document_id == "doc-1"
        return self.document

    def list_chunks_for_document(self, document_id):
        assert document_id == "doc-1"
        return [self.chunk]

    def list_documents(
        self,
        filters=None,
        sort_by="indexed_at",
        sort_order="desc",
        page_size=20,
        cursor=None,
    ):
        return {"documents": [self.document][:page_size], "next_cursor": None}


class FakeIngestionService:
    def __init__(self, metadata_store: FakeMetadataStore):
        self.metadata_store = metadata_store
        self.calls: dict[str, int] = {}
        self.job_numbers: dict[str, int] = {}
        self.wait_calls: list[dict] = []

    async def start_sync_source(self, source_id):
        self.calls[source_id] = self.calls.get(source_id, 0) + 1
        existing_job = self.metadata_store.get_latest_sync_job(source_id)
        if existing_job and getattr(existing_job, "status", "") == "running":
            return existing_job

        next_job_number = self.job_numbers.get(source_id, 0) + 1
        self.job_numbers[source_id] = next_job_number
        job_payload = {
            "job_id": f"job-{source_id}-{next_job_number}",
            "source_id": source_id,
            "status": "running",
            "started_at": "2026-06-15T00:00:00+00:00",
            "finished_at": "",
            "error_message": "",
        }
        self.metadata_store.set_job(source_id, job_payload)
        return Dumpable(job_payload, **job_payload)

    async def sync_all(self):
        return {
            "status": "accepted",
            "summary": {
                "total_sources": 2,
                "started": 2,
                "already_running": 0,
                "failed": 0,
                "skipped": 0,
                "requested_at": "2026-06-15T00:00:00+00:00",
            },
            "results": [
                {
                    "source_id": "source_github",
                    "launch_outcome": "started",
                    "message": "",
                    "job": Dumpable(
                        {
                            "job_id": "job-source_github",
                            "source_id": "source_github",
                            "status": "running",
                            "started_at": "2026-06-15T00:00:00+00:00",
                            "finished_at": "",
                            "error_message": "",
                        }
                    ),
                },
                {
                    "source_id": "source_obsidian",
                    "launch_outcome": "started",
                    "message": "",
                    "job": Dumpable(
                        {
                            "job_id": "job-source_obsidian",
                            "source_id": "source_obsidian",
                            "status": "running",
                            "started_at": "2026-06-15T00:00:00+00:00",
                            "finished_at": "",
                            "error_message": "",
                        }
                    ),
                },
            ],
        }

    async def wait_for_sync_all(
        self,
        source_ids=None,
        timeout_seconds=300.0,
        poll_interval_seconds=0.25,
    ):
        self.wait_calls.append(
            {
                "source_ids": source_ids,
                "timeout_seconds": timeout_seconds,
                "poll_interval_seconds": poll_interval_seconds,
            }
        )
        return {
            "status": "completed",
            "summary": {
                "total_sources": 2,
                "succeeded": 2,
                "failed": 0,
                "skipped": 0,
                "timed_out": 0,
                "requested_at": "2026-06-15T00:00:00+00:00",
                "completed_at": "2026-06-15T00:00:01+00:00",
            },
            "results": [
                {
                    "source_id": source_id,
                    "launch_outcome": "started",
                    "completion_outcome": "succeeded",
                    "message": "",
                    "job": Dumpable(
                        {
                            "job_id": f"job-{source_id}",
                            "source_id": source_id,
                            "status": "succeeded",
                            "started_at": "2026-06-15T00:00:00+00:00",
                            "finished_at": "2026-06-15T00:00:01+00:00",
                            "error_message": "",
                        }
                    ),
                }
                for source_id in ("source_github", "source_obsidian")
            ],
        }


class FakeMixedWaitIngestionService(FakeIngestionService):
    async def wait_for_sync_all(
        self,
        source_ids=None,
        timeout_seconds=300.0,
        poll_interval_seconds=0.25,
    ):
        self.wait_calls.append(
            {
                "source_ids": source_ids,
                "timeout_seconds": timeout_seconds,
                "poll_interval_seconds": poll_interval_seconds,
            }
        )
        return {
            "status": "failed",
            "summary": {
                "total_sources": 2,
                "succeeded": 0,
                "failed": 1,
                "skipped": 1,
                "timed_out": 0,
                "requested_at": "2026-06-15T00:00:00+00:00",
                "completed_at": "2026-06-15T00:00:01+00:00",
            },
            "results": [
                {
                    "source_id": "source_github",
                    "launch_outcome": "started",
                    "completion_outcome": "failed",
                    "message": "Sync failed. See server logs for details.",
                    "job": Dumpable(
                        {
                            "job_id": "job-source_github",
                            "source_id": "source_github",
                            "status": "failed",
                            "started_at": "2026-06-15T00:00:00+00:00",
                            "finished_at": "2026-06-15T00:00:01+00:00",
                            "error_message": "Sync failed. See server logs for details.",
                        }
                    ),
                },
                {
                    "source_id": "source_obsidian",
                    "launch_outcome": "skipped",
                    "completion_outcome": "skipped",
                    "message": "Source is disabled.",
                    "job": Dumpable(
                        {
                            "job_id": "job-source_obsidian",
                            "source_id": "source_obsidian",
                            "status": "failed",
                            "started_at": "2026-06-15T00:00:00+00:00",
                            "finished_at": "2026-06-15T00:00:01+00:00",
                            "error_message": "Source is disabled.",
                        }
                    ),
                },
            ],
        }


class FakeBlockingOnlyIngestionService:
    async def sync_source(self, source_id):
        return Dumpable(
            {
                "job_id": f"job-{source_id}-blocking-only",
                "source_id": source_id,
                "status": "succeeded",
                "started_at": "2026-06-15T00:00:00+00:00",
                "finished_at": "2026-06-15T00:00:01+00:00",
                "error_message": "",
            },
            job_id=f"job-{source_id}-blocking-only",
            source_id=source_id,
            status="succeeded",
            started_at="2026-06-15T00:00:00+00:00",
            finished_at="2026-06-15T00:00:01+00:00",
            error_message="",
        )


class FakeContextSearchService:
    async def search_context(self, query, filters=None, top_k=10, include_debug=False):
        debug_payload = {}
        if include_debug:
            debug_payload = {
                "retrieval_queries": ["ContextWiki contracts"],
                "selected_results": ["chunk-1"],
            }
        return {
            "query": query,
            "results": [
                {
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "source_id": "source_github",
                    "source_type": "github",
                    "title": "ContextWiki contracts",
                    "score": 0.98,
                    "preview": "ContextWiki validates MCP contracts through real call_tool paths.",
                    "text": "ContextWiki validates MCP contracts through real call_tool paths.",
                }
            ],
            "debug": debug_payload,
        }

    async def search_documents(
        self,
        query,
        filters=None,
        sort_by="relevance",
        sort_order="desc",
        top_k=10,
    ):
        return {
            "query": query,
            "results": [
                {
                    "document_id": "doc-1",
                    "chunk_id": "chunk-1",
                    "source_id": "source_github",
                    "source_type": "github",
                    "title": "ContextWiki contracts",
                    "url": "https://example.com/contracts",
                    "path": "docs/contracts.md",
                    "score": 0.98,
                    "matched_context": "ContextWiki validates MCP contracts through real call_tool paths.",
                }
            ],
        }


class FakeAnswerService:
    async def answer_with_citations(self, question, filters=None, top_k=5, include_debug=False):
        payload = {
            "question": question,
            "answer": "ContextWiki exposes retained MCP tools and validates them through real FastMCP calls.",
            "evidence_status": "grounded",
            "citations": [
                {
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "source_id": "source_github",
                    "title": "ContextWiki contracts",
                }
            ],
            "used_chunks": [
                {
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "source_id": "source_github",
                }
            ],
        }
        if include_debug:
            payload["debug"] = {"selected_chunks": ["chunk-1"]}
            payload["debug_markdown"] = "## Debug\n- chunk-1"
        return payload


def build_contract_harness():
    source_registry = FakeSourceRegistry()
    metadata_store = FakeMetadataStore(source_registry)
    mcp = FastMCP("public-contract-test")
    ingestion_service = FakeIngestionService(metadata_store)
    register_tools(
        mcp,
        ingestion_service=ingestion_service,
        context_search_service=FakeContextSearchService(),
        answer_service=FakeAnswerService(),
        metadata_store=metadata_store,
        source_registry=source_registry,
    )
    return {
        "mcp": mcp,
        "metadata_store": metadata_store,
        "ingestion_service": ingestion_service,
    }


def build_contract_mcp() -> FastMCP:
    return build_contract_harness()["mcp"]


def call_tool_json(mcp: FastMCP, name: str, arguments: dict | None = None) -> dict:
    blocks = asyncio.run(mcp.call_tool(name, arguments or {}))
    return json.loads(blocks[0].text)


def test_search_tool_descriptions_explain_when_the_llm_should_select_each_tool():
    tools = {
        tool.name: tool
        for tool in asyncio.run(build_contract_mcp().list_tools())
    }

    search_context_description = tools["search_context"].description.lower()
    assert "focused" in search_context_description
    assert "chunk evidence" in search_context_description

    search_documents_description = tools["search_documents"].description.lower()
    assert "one row per relevant document" in search_documents_description
    assert "representative matched_context" in search_documents_description

    fetch_context_description = tools["fetch_context"].description.lower()
    assert "optionally drill" in fetch_context_description
    assert "after its id is known" in fetch_context_description


def test_date_filters_and_document_listing_have_typed_real_fastmcp_schemas():
    tools = {tool.name: tool for tool in asyncio.run(build_contract_mcp().list_tools())}

    assert "list_documents" in tools
    search_schema = tools["search_context"].inputSchema
    filter_schema = json.dumps(
        search_schema["properties"]["filters"],
        sort_keys=True,
    )
    assert "published_from" in filter_schema
    assert "published_to" in filter_schema
    assert "modified_from" in filter_schema
    assert "indexed_to" in filter_schema
    list_properties = tools["list_documents"].inputSchema["properties"]
    assert {"filters", "sort_by", "sort_order", "page_size", "cursor"} <= set(
        list_properties
    )
    assert search_schema["$defs"]["SearchFilters"]["additionalProperties"] is False


def test_real_fastmcp_annotations_match_metadata_store_write_behavior(tmp_path):
    source_registry = FakeSourceRegistry()
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    mcp = FastMCP("tool-annotation-contract")
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=source_registry,
    )

    source_payload = call_tool_json(mcp, "list_sources")
    status_payload = call_tool_json(mcp, "get_sync_status")
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    assert {source["source_id"] for source in source_payload["sources"]} == {
        "source_github",
        "source_obsidian",
    }
    assert {item["source"]["source_id"] for item in status_payload["sources"]} == {
        "source_github",
        "source_obsidian",
    }
    assert store.get_source("source_github") is not None
    for name in ("list_sources", "get_sync_status"):
        annotations = tools[name].annotations
        assert annotations is None or annotations.readOnlyHint is not True
        assert annotations is None or annotations.idempotentHint is not True

    for name in ("search_context", "search_documents"):
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.readOnlyHint is False
        assert annotations.destructiveHint is False
        assert annotations.idempotentHint is False
        assert annotations.openWorldHint is True
    for name in ("list_documents", "fetch_context"):
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.readOnlyHint is False
        assert annotations.destructiveHint is False
        assert annotations.idempotentHint is False
        assert annotations.openWorldHint is False


def test_typed_date_filters_and_list_documents_use_real_fastmcp_calls():
    mcp = build_contract_mcp()
    search_payload = call_tool_json(
        mcp,
        "search_documents",
        {
            "query": "ContextWiki contracts",
            "filters": {
                "source_ids": ["source_github"],
                "published_from": "2026-06-01T00:00:00Z",
            },
            "sort_by": "published_at",
            "sort_order": "desc",
            "top_k": 3,
        },
    )
    list_payload = call_tool_json(
        mcp,
        "list_documents",
        {
            "filters": {"source_ids": ["source_github"]},
            "sort_by": "indexed_at",
            "sort_order": "desc",
            "page_size": 1,
        },
    )

    assert search_payload["results"][0]["document_id"] == "doc-1"
    assert list_payload["documents"][0]["document_id"] == "doc-1"
    assert "next_cursor" in list_payload


@pytest.mark.parametrize(
    ("filters", "expected_source_ids"),
    [
        ({"source_ids": None}, {"source_github", "source_obsidian"}),
        ({"source_id": None}, {"source_github", "source_obsidian"}),
        ({"source_ids": "source_github"}, {"source_github"}),
        ({"source_ids": ["", "  ", "source_github"]}, {"source_github"}),
        ({"source_ids": ["", "  "]}, {"source_github", "source_obsidian"}),
    ],
)
def test_real_fastmcp_normalizes_compatible_source_filter_shapes(
    filters,
    expected_source_ids,
):
    class CapturingContextSearch(FakeContextSearchService):
        def __init__(self):
            self.filters = None

        async def search_context(
            self,
            query,
            filters=None,
            top_k=10,
            include_debug=False,
        ):
            self.filters = filters
            return await super().search_context(
                query,
                filters=filters,
                top_k=top_k,
                include_debug=include_debug,
            )

    source_registry = FakeSourceRegistry()
    metadata_store = FakeMetadataStore(source_registry)
    context_search = CapturingContextSearch()
    mcp = FastMCP("nullable-scalar-source-filter-contract")
    register_tools(
        mcp,
        context_search_service=context_search,
        metadata_store=metadata_store,
        source_registry=source_registry,
    )

    payload = call_tool_json(
        mcp,
        "search_context",
        {
            "query": "ContextWiki contracts",
            "filters": filters,
            "top_k": 3,
        },
    )

    assert payload["results"][0]["source_id"] == "source_github"
    assert set(context_search.filters["source_ids"]) == expected_source_ids
    assert set(context_search.filters["source_ids"]) <= {
        "source_github",
        "source_obsidian",
    }


def test_real_fastmcp_rejects_unknown_filter_keys():
    with pytest.raises(ToolError) as exc_info:
        asyncio.run(
            build_contract_mcp().call_tool(
                "search_context",
                {
                    "query": "ContextWiki contracts",
                    "filters": {"tag": "docs"},
                },
            )
        )

    assert "filters.tag" in str(exc_info.value)
    assert "Extra inputs are not permitted" in str(exc_info.value)


def test_real_fastmcp_rejects_utc_overflow_filter_without_raw_backend_error():
    with pytest.raises(ToolError) as exc_info:
        asyncio.run(
            build_contract_mcp().call_tool(
                "search_context",
                {
                    "query": "ContextWiki contracts",
                    "filters": {
                        "published_from": "9999-12-31T23:59:59-01:00",
                    },
                },
            )
        )

    message = str(exc_info.value)
    assert "Date filters must be valid ISO 8601 timestamps" in message
    assert "OverflowError" not in message
    assert "/Users/" not in message
    assert "token=" not in message


def test_real_fastmcp_hides_secret_like_invalid_filter_input():
    with pytest.raises(ToolError) as exc_info:
        asyncio.run(
            build_contract_mcp().call_tool(
                "search_context",
                {
                    "query": "ContextWiki contracts",
                    "filters": {
                        "published_from": (
                            "/Users/eunhwa/private/contextwiki.sqlite3"
                            "?token=super-secret-value"
                        ),
                    },
                },
            )
        )

    message = str(exc_info.value)
    assert "filters.published_from" in message
    assert "Date filters must be valid ISO 8601 timestamps" in message
    assert "/Users/eunhwa/private" not in message
    assert "super-secret-value" not in message
    assert "input_value" not in message


def test_real_fastmcp_unions_singular_and_plural_source_filters(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    source_registry = FakeSourceRegistry()
    documents = []
    for source in source_registry.list_sources():
        store.register_source(source)
        document_id = f"doc-{source.source_id}"
        document = DocumentModel(
            id=document_id,
            source_id=source.source_id,
            title=f"ContextWiki {source.name}",
            content="ContextWiki union source filtering evidence.",
            url=f"https://example.com/{document_id}",
            platform=source.source_type.value,
        )
        chunk = ChunkModel(
            chunk_id=f"{document_id}:chunk:0",
            document_id=document_id,
            source_id=source.source_id,
            title=document.title,
            text=document.content,
            url=document.url,
            chunk_index=0,
            content_hash=document_id,
        )
        store.upsert_document_and_replace_chunks(document, [chunk])
        documents.append(chunk.to_document_model(platform=source.source_type.value))

    mcp = FastMCP("source-filter-union-contract")
    register_tools(
        mcp,
        context_search_service=ContextSearchService(store, retriever=documents),
        metadata_store=store,
        source_registry=source_registry,
    )
    filters = {
        "source_id": "source_github",
        "source_ids": ["source_obsidian"],
    }

    search_payload = call_tool_json(
        mcp,
        "search_context",
        {"query": "ContextWiki", "filters": filters, "top_k": 5},
    )
    list_payload = call_tool_json(
        mcp,
        "list_documents",
        {"filters": filters, "page_size": 5},
    )

    assert {item["source_id"] for item in search_payload["results"]} == {
        "source_github",
        "source_obsidian",
    }
    assert {item["source_id"] for item in list_payload["documents"]} == {
        "source_github",
        "source_obsidian",
    }


@pytest.mark.parametrize("prefix", ["published", "modified", "indexed"])
def test_real_fastmcp_rejects_reversed_date_filter_ranges(prefix):
    with pytest.raises(ToolError) as exc_info:
        asyncio.run(
            build_contract_mcp().call_tool(
                "search_context",
                {
                    "query": "ContextWiki contracts",
                    "filters": {
                        f"{prefix}_from": "2026-07-02T00:00:00Z",
                        f"{prefix}_to": "2026-07-01T00:00:00Z",
                    },
                },
            )
        )

    assert f"{prefix}_from must be before or equal to {prefix}_to" in str(
        exc_info.value
    )


def test_real_fastmcp_rejects_unsupported_document_search_sort():
    with pytest.raises(ToolError) as exc_info:
        asyncio.run(
            build_contract_mcp().call_tool(
                "search_documents",
                {
                    "query": "ContextWiki contracts",
                    "sort_by": "created_at",
                },
            )
        )

    assert "sort_by" in str(exc_info.value)
    assert "published_at" in str(exc_info.value)


@pytest.mark.parametrize("page_size", [0, 51])
def test_real_fastmcp_rejects_unsafe_document_page_sizes(page_size):
    with pytest.raises(ToolError) as exc_info:
        asyncio.run(
            build_contract_mcp().call_tool(
                "list_documents",
                {"page_size": page_size},
            )
        )

    assert "page_size" in str(exc_info.value)
    assert "greater than or equal to 1" in str(exc_info.value) or (
        "less than or equal to 50" in str(exc_info.value)
    )


def test_real_fastmcp_rejects_invalid_document_cursor_safely(tmp_path):
    mcp = FastMCP("invalid-document-cursor-contract")
    register_tools(
        mcp,
        metadata_store=MetadataStore(tmp_path / "contextwiki.sqlite3"),
    )

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(
            mcp.call_tool(
                "list_documents",
                {"cursor": "definitely-not-valid"},
            )
        )

    assert str(exc_info.value).endswith("Invalid document cursor")
    assert "definitely-not-valid" not in str(exc_info.value)


def test_real_fastmcp_rejects_structurally_valid_forged_cursor_anchor(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    for document_id in ("a", "b", "c"):
        store.upsert_document(
            DocumentModel(
                id=document_id,
                source_id="source_notion",
                title=document_id,
                content=document_id,
                url=f"https://example.com/{document_id}",
                platform="Notion",
                published_at="2026-07-01T00:00:00Z",
            )
        )
    mcp = FastMCP("forged-document-cursor-contract")
    register_tools(mcp, metadata_store=store)
    first_page = call_tool_json(
        mcp,
        "list_documents",
        {
            "sort_by": "published_at",
            "sort_order": "asc",
            "page_size": 1,
        },
    )
    payload = store._decode_document_cursor(first_page["next_cursor"])
    forged_cursor = store._encode_document_cursor(
        {**payload, "document_id": "bb-forged-anchor"}
    )

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(
            mcp.call_tool(
                "list_documents",
                {
                    "sort_by": "published_at",
                    "sort_order": "asc",
                    "page_size": 1,
                    "cursor": forged_cursor,
                },
            )
        )

    assert str(exc_info.value).endswith("Invalid document cursor")
    assert forged_cursor not in str(exc_info.value)


def test_real_fastmcp_redacts_unexpected_list_documents_backend_errors():
    source_registry = FakeSourceRegistry()

    class FailingListMetadataStore(FakeMetadataStore):
        def list_documents(self, **_kwargs):
            raise RuntimeError(
                "backend failed at /Users/eunhwa/private/contextwiki.sqlite3 "
                "with token=super-secret-value"
            )

    mcp = FastMCP("safe-list-documents-error-contract")
    register_tools(
        mcp,
        metadata_store=FailingListMetadataStore(source_registry),
        source_registry=source_registry,
    )

    payload = call_tool_json(mcp, "list_documents")

    assert payload["status"] == "error"
    assert payload["documents"] == []
    assert payload["next_cursor"] is None
    assert "<redacted>" in payload["message"]
    assert "/Users/eunhwa/private" not in payload["message"]
    assert "super-secret-value" not in payload["message"]


def test_real_fastmcp_redacts_list_documents_source_filter_lookup_errors():
    class FailingSourceLookupMetadataStore:
        def get_source(self, _source_id):
            raise RuntimeError(
                "source lookup failed at /Users/eunhwa/private/contextwiki.sqlite3 "
                "with token=super-secret-value"
            )

        def list_documents(self, **_kwargs):
            raise AssertionError("listing must not run after source-filter lookup fails")

    mcp = FastMCP("safe-list-documents-filter-error-contract")
    register_tools(
        mcp,
        metadata_store=FailingSourceLookupMetadataStore(),
    )

    payload = call_tool_json(
        mcp,
        "list_documents",
        {"filters": {"source_id": "source_private"}},
    )

    assert payload["status"] == "error"
    assert payload["documents"] == []
    assert payload["next_cursor"] is None
    assert "<redacted>" in payload["message"]
    assert "/Users/eunhwa/private" not in payload["message"]
    assert "super-secret-value" not in payload["message"]


def test_list_sources_contract_uses_real_fastmcp_call_tool():
    payload = call_tool_json(build_contract_mcp(), "list_sources")

    assert [source["source_id"] for source in payload["sources"]] == [
        "source_github",
        "source_obsidian",
    ]
    assert payload["sources"][0]["auth_ref"] == "env:GITHUB_TOKEN"


def test_sync_source_contract_uses_real_fastmcp_call_tool():
    payload = call_tool_json(
        build_contract_mcp(),
        "sync_source",
        {"source_id": "source_github"},
    )

    assert payload["status"] == "running"
    assert payload["source_id"] == "source_github"


def test_sync_source_contract_reuses_running_job_and_polls_to_terminal_status():
    harness = build_contract_harness()
    mcp = harness["mcp"]
    metadata_store = harness["metadata_store"]
    ingestion_service = harness["ingestion_service"]

    first_payload = call_tool_json(mcp, "sync_source", {"source_id": "source_github"})
    second_payload = call_tool_json(mcp, "sync_source", {"source_id": "source_github"})

    assert first_payload["status"] == "running"
    assert second_payload["status"] == "running"
    assert second_payload["job_id"] == first_payload["job_id"]
    assert ingestion_service.calls["source_github"] == 2

    metadata_store.set_job(
        "source_github",
        {
            "job_id": first_payload["job_id"],
            "source_id": "source_github",
            "status": "succeeded",
            "started_at": first_payload["started_at"],
            "finished_at": "2026-06-15T00:00:01+00:00",
            "error_message": "",
        },
    )
    status_payload = call_tool_json(mcp, "get_sync_status", {"source_id": "source_github"})

    assert status_payload["latest_job"]["job_id"] == first_payload["job_id"]
    assert status_payload["latest_job"]["status"] == "succeeded"


def test_sync_source_contract_returns_error_without_background_launcher():
    source_registry = FakeSourceRegistry()
    metadata_store = FakeMetadataStore(source_registry)
    mcp = FastMCP("public-contract-test-no-launcher")
    register_tools(
        mcp,
        ingestion_service=FakeBlockingOnlyIngestionService(),
        context_search_service=FakeContextSearchService(),
        answer_service=FakeAnswerService(),
        metadata_store=metadata_store,
        source_registry=source_registry,
    )

    payload = call_tool_json(mcp, "sync_source", {"source_id": "source_github"})

    assert payload == {
        "status": "error",
        "message": "ingestion service does not support background sync launch",
    }


def test_sync_all_contract_uses_real_fastmcp_call_tool():
    payload = call_tool_json(build_contract_mcp(), "sync_all")

    assert payload["status"] == "accepted"
    assert payload["summary"]["total_sources"] == 2
    assert payload["summary"]["started"] == 2
    assert [item["source_id"] for item in payload["results"]] == [
        "source_github",
        "source_obsidian",
    ]


def test_wait_for_sync_all_contract_returns_terminal_results_through_real_fastmcp():
    harness = build_contract_harness()

    payload = call_tool_json(
        harness["mcp"],
        "wait_for_sync_all",
        {"timeout_seconds": 12.5, "poll_interval_seconds": 0.1},
    )

    assert payload["status"] == "completed"
    assert payload["summary"] == {
        "total_sources": 2,
        "succeeded": 2,
        "failed": 0,
        "skipped": 0,
        "timed_out": 0,
        "requested_at": "2026-06-15T00:00:00+00:00",
        "completed_at": "2026-06-15T00:00:01+00:00",
    }
    assert [
        (
            item["source_id"],
            item["launch_outcome"],
            item["completion_outcome"],
            item["job"]["status"],
        )
        for item in payload["results"]
    ] == [
        ("source_github", "started", "succeeded", "succeeded"),
        ("source_obsidian", "started", "succeeded", "succeeded"),
    ]
    assert harness["ingestion_service"].wait_calls == [
        {
            "source_ids": None,
            "timeout_seconds": 12.5,
            "poll_interval_seconds": 0.1,
        }
    ]


def test_wait_for_sync_all_contract_preserves_failed_and_skipped_outcomes():
    source_registry = FakeSourceRegistry()
    metadata_store = FakeMetadataStore(source_registry)
    ingestion_service = FakeMixedWaitIngestionService(metadata_store)
    mcp = FastMCP("public-mixed-wait-contract-test")
    register_tools(
        mcp,
        ingestion_service=ingestion_service,
        metadata_store=metadata_store,
        source_registry=source_registry,
    )

    payload = call_tool_json(mcp, "wait_for_sync_all")

    assert payload["status"] == "failed"
    assert payload["summary"]["failed"] == 1
    assert payload["summary"]["skipped"] == 1
    assert [
        (
            item["source_id"],
            item["launch_outcome"],
            item["completion_outcome"],
            item["job"]["status"],
        )
        for item in payload["results"]
    ] == [
        ("source_github", "started", "failed", "failed"),
        ("source_obsidian", "skipped", "skipped", "failed"),
    ]


@pytest.mark.parametrize(
    ("arguments", "expected_fragment"),
    [
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": 601}, "timeout_seconds"),
        ({"poll_interval_seconds": 0.099}, "poll_interval_seconds"),
        ({"poll_interval_seconds": 6}, "poll_interval_seconds"),
    ],
)
def test_wait_for_sync_all_contract_rejects_unbounded_wait_parameters(
    arguments,
    expected_fragment,
):
    harness = build_contract_harness()

    payload = call_tool_json(harness["mcp"], "wait_for_sync_all", arguments)

    assert payload["status"] == "error"
    assert expected_fragment in payload["message"]
    assert payload["summary"]["total_sources"] == 0
    assert payload["results"] == []
    assert harness["ingestion_service"].wait_calls == []


@pytest.mark.parametrize(
    ("arguments", "field_name"),
    [
        ({"timeout_seconds": True}, "timeout_seconds"),
        ({"poll_interval_seconds": True}, "poll_interval_seconds"),
    ],
)
def test_wait_for_sync_all_contract_rejects_booleans_before_any_launch_side_effect(
    arguments,
    field_name,
):
    harness = build_contract_harness()
    metadata_store = harness["metadata_store"]
    ingestion_service = harness["ingestion_service"]
    jobs_before = {
        source_id: job.model_dump()
        for source_id, job in metadata_store.jobs.items()
    }

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(
            harness["mcp"].call_tool(
                "wait_for_sync_all",
                arguments,
            )
        )

    assert field_name in str(exc_info.value)
    assert "valid number" in str(exc_info.value)
    assert ingestion_service.wait_calls == []
    assert ingestion_service.calls == {}
    assert ingestion_service.job_numbers == {}
    assert {
        source_id: job.model_dump()
        for source_id, job in metadata_store.jobs.items()
    } == jobs_before


def test_get_sync_status_contract_uses_real_fastmcp_call_tool():
    payload = call_tool_json(
        build_contract_mcp(),
        "get_sync_status",
        {"source_id": "source_github"},
    )

    assert payload["source"]["source_id"] == "source_github"
    assert payload["latest_job"]["status"] == "succeeded"


def test_search_context_contract_uses_real_fastmcp_call_tool():
    default_payload = call_tool_json(
        build_contract_mcp(),
        "search_context",
        {"query": "ContextWiki contracts", "top_k": 3},
    )
    payload = call_tool_json(
        build_contract_mcp(),
        "search_context",
        {"query": "ContextWiki contracts", "top_k": 3, "include_debug": True},
    )

    assert default_payload["query"] == "ContextWiki contracts"
    assert default_payload["debug"] == {}
    assert payload["query"] == "ContextWiki contracts"
    assert payload["results"][0]["chunk_id"] == "chunk-1"
    assert payload["debug"]["retrieval_queries"] == ["ContextWiki contracts"]


def test_search_context_no_matching_sources_keeps_public_debug_contract():
    payload = call_tool_json(
        build_contract_mcp(),
        "search_context",
        {"query": "ContextWiki contracts", "filters": {"source_id": "source_missing"}, "top_k": 3},
    )

    assert payload["results"] == []
    assert payload["debug"] == {
        "retrieval_queries": [],
        "effective_term_groups": [],
    }


def test_search_documents_contract_uses_real_fastmcp_call_tool():
    payload = call_tool_json(
        build_contract_mcp(),
        "search_documents",
        {"query": "ContextWiki contracts", "top_k": 3},
    )

    assert payload["query"] == "ContextWiki contracts"
    assert payload["results"][0]["document_id"] == "doc-1"
    assert payload["results"][0]["matched_context"] == (
        "ContextWiki validates MCP contracts through real call_tool paths."
    )
    assert "preview" not in payload["results"][0]


def test_fetch_context_contract_uses_real_fastmcp_call_tool():
    payload = call_tool_json(
        build_contract_mcp(),
        "fetch_context",
        {"chunk_id": "chunk-1"},
    )

    assert payload["chunk"]["chunk_id"] == "chunk-1"
    assert "real call_tool paths" in payload["chunk"]["text"]
