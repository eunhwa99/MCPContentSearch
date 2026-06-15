import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP

from api.tools import register_tools
from core.models import ChunkModel, DocumentModel, SourceModel, SourceType, SyncStatus


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
        )

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


class FakeIngestionService:
    async def sync_source(self, source_id):
        return Dumpable(
            {
                "job_id": f"job-{source_id}",
                "source_id": source_id,
                "status": "succeeded",
                "started_at": "2026-06-15T00:00:00+00:00",
                "finished_at": "2026-06-15T00:00:01+00:00",
                "error_message": "",
            }
        )

    async def sync_all(self):
        return {
            "status": "completed",
            "summary": {
                "total_sources": 2,
                "succeeded": 2,
                "failed": 0,
                "blocked": 0,
                "skipped": 0,
            },
            "results": [
                {
                    "source_id": "source_github",
                    "sync_outcome": "succeeded",
                    "message": "",
                    "job": Dumpable(
                        {
                            "job_id": "job-source_github",
                            "source_id": "source_github",
                            "status": "succeeded",
                            "started_at": "2026-06-15T00:00:00+00:00",
                            "finished_at": "2026-06-15T00:00:01+00:00",
                            "error_message": "",
                        }
                    ),
                },
                {
                    "source_id": "source_obsidian",
                    "sync_outcome": "succeeded",
                    "message": "",
                    "job": Dumpable(
                        {
                            "job_id": "job-source_obsidian",
                            "source_id": "source_obsidian",
                            "status": "succeeded",
                            "started_at": "2026-06-15T00:00:00+00:00",
                            "finished_at": "2026-06-15T00:00:01+00:00",
                            "error_message": "",
                        }
                    ),
                },
            ],
        }


class FakeContextSearchService:
    async def search_context(self, query, filters=None, top_k=10, include_debug=False):
        debug_payload = {}
        if include_debug:
            debug_payload = {
                "rewrite_enabled": False,
                "rewrite_attempted": False,
                "rewrite_applied": False,
                "rewrite_skipped_reason": "disabled",
                "rewritten_queries": [],
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

    async def search_documents(self, query, filters=None, top_k=10):
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
                    "preview": "ContextWiki validates MCP contracts through real call_tool paths.",
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


def build_contract_mcp() -> FastMCP:
    source_registry = FakeSourceRegistry()
    metadata_store = FakeMetadataStore(source_registry)
    mcp = FastMCP("public-contract-test")
    register_tools(
        mcp,
        ingestion_service=FakeIngestionService(),
        context_search_service=FakeContextSearchService(),
        answer_service=FakeAnswerService(),
        metadata_store=metadata_store,
        source_registry=source_registry,
    )
    return mcp


def call_tool_json(mcp: FastMCP, name: str, arguments: dict | None = None) -> dict:
    blocks = asyncio.run(mcp.call_tool(name, arguments or {}))
    return json.loads(blocks[0].text)


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

    assert payload["status"] == "succeeded"
    assert payload["source_id"] == "source_github"


def test_sync_all_contract_uses_real_fastmcp_call_tool():
    payload = call_tool_json(build_contract_mcp(), "sync_all")

    assert payload["status"] == "completed"
    assert payload["summary"]["total_sources"] == 2
    assert [item["source_id"] for item in payload["results"]] == [
        "source_github",
        "source_obsidian",
    ]


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
    assert payload["debug"]["rewrite_skipped_reason"] == "disabled"


def test_search_context_no_matching_sources_keeps_public_debug_contract():
    payload = call_tool_json(
        build_contract_mcp(),
        "search_context",
        {"query": "ContextWiki contracts", "filters": {"source_id": "source_missing"}, "top_k": 3},
    )

    assert payload["results"] == []
    assert payload["debug"]["rewrite_skipped_reason"] == "no_matching_sources"
    assert payload["debug"]["rewrite_attempted"] is False


def test_search_documents_contract_uses_real_fastmcp_call_tool():
    payload = call_tool_json(
        build_contract_mcp(),
        "search_documents",
        {"query": "ContextWiki contracts", "top_k": 3},
    )

    assert payload["query"] == "ContextWiki contracts"
    assert payload["results"][0]["document_id"] == "doc-1"


def test_fetch_context_contract_uses_real_fastmcp_call_tool():
    payload = call_tool_json(
        build_contract_mcp(),
        "fetch_context",
        {"chunk_id": "chunk-1"},
    )

    assert payload["chunk"]["chunk_id"] == "chunk-1"
    assert "real call_tool paths" in payload["chunk"]["text"]


def test_answer_with_citations_contract_uses_real_fastmcp_call_tool():
    payload = call_tool_json(
        build_contract_mcp(),
        "answer_with_citations",
        {"question": "How does ContextWiki validate contracts?", "top_k": 3, "include_debug": True},
    )

    assert payload["evidence_status"] == "grounded"
    assert payload["citations"][0]["chunk_id"] == "chunk-1"
    assert payload["debug_markdown"].startswith("## Debug")
