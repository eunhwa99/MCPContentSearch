import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from api.tools import register_tools
from core.models import EvidenceChunk, EvidenceSourceType, SearchEvidenceInput


EXPECTED_SOURCE_TYPES = {
    "resume",
    "previous_resume",
    "project",
    "github_readme",
    "behavioral_story",
    "career_note",
    "skills_inventory",
}
EXPECTED_EXPERIENCE_TYPES = {
    "professional",
    "academic",
    "personal_project",
    "prototype",
    "unknown",
}


class FakeEvidenceSearchService:
    def __init__(self):
        self.requests = []

    async def search_evidence(self, request: SearchEvidenceInput):
        self.requests.append(request)
        return [
            EvidenceChunk(
                chunk_id="chunk-1",
                document_id="doc-1",
                document_version_id="version-1",
                source_type="resume",
                document_title="Backend Resume",
                section_title="Reliability",
                parent_section_title="Experience",
                exact_quote="Reduced deployment failures by 40%.",
                retrieval_score=0.94,
                experience_type="professional",
                file_name="resume.md",
                metadata={"company": "Example Systems"},
            )
        ]


class RawFailingEvidenceSearchService:
    async def search_evidence(self, request: SearchEvidenceInput):
        del request
        raise RuntimeError(
            "token=super-secret-value at /Users/tester/private/career.sqlite3"
        )


def _tool_map(mcp):
    return {tool.name: tool for tool in asyncio.run(mcp.list_tools())}


def _call_json(mcp, arguments):
    blocks = asyncio.run(mcp.call_tool("search_evidence", arguments))
    return json.loads(blocks[0].text)


def test_search_evidence_real_fastmcp_schema_is_truthful_while_handler_owns_validation():
    mcp = FastMCP("search-evidence-schema")
    register_tools(mcp, evidence_search_service=FakeEvidenceSearchService())

    tool = _tool_map(mcp)["search_evidence"]
    schema = tool.inputSchema

    assert set(schema["properties"]) == {
        "query",
        "source_types",
        "experience_types",
        "document_ids",
        "top_k",
    }
    assert schema.get("required", []) == ["query"]
    assert schema["properties"]["query"] == {
        "description": "Required query; 1-4096 characters.",
        "maxLength": 4096,
        "minLength": 1,
        "title": "Query",
        "type": "string",
    }
    assert schema["properties"]["top_k"]["type"] == "integer"
    assert schema["properties"]["top_k"]["minimum"] == 1
    assert schema["properties"]["top_k"]["maximum"] == 50
    assert schema["properties"]["top_k"]["default"] == 5
    source_schema = schema["properties"]["source_types"]
    experience_schema = schema["properties"]["experience_types"]
    document_schema = schema["properties"]["document_ids"]
    source_array = source_schema["anyOf"][0]
    experience_array = experience_schema["anyOf"][0]
    document_array = document_schema["anyOf"][0]
    assert source_schema["anyOf"][1] == {"type": "null"}
    assert source_schema["default"] is None
    assert source_array["type"] == "array"
    assert source_array["maxItems"] == 32
    assert set(source_array["items"]["enum"]) == EXPECTED_SOURCE_TYPES
    assert source_array["items"]["minLength"] == 1
    assert source_array["items"]["maxLength"] == 64
    assert experience_schema["anyOf"][1] == {"type": "null"}
    assert experience_schema["default"] is None
    assert experience_array["type"] == "array"
    assert experience_array["maxItems"] == 32
    assert set(experience_array["items"]["enum"]) == EXPECTED_EXPERIENCE_TYPES
    assert experience_array["items"]["minLength"] == 1
    assert experience_array["items"]["maxLength"] == 64
    assert document_schema["anyOf"][1] == {"type": "null"}
    assert document_schema["default"] is None
    assert document_array["type"] == "array"
    assert document_array["maxItems"] == 100
    assert document_array["items"] == {
        "maxLength": 512,
        "minLength": 1,
        "type": "string",
    }


def test_search_evidence_inventory_adds_only_one_public_tool():
    mcp = FastMCP("search-evidence-inventory")
    register_tools(mcp, evidence_search_service=FakeEvidenceSearchService())

    assert set(_tool_map(mcp)) == {
        "list_sources",
        "sync_source",
        "sync_all",
        "get_sync_status",
        "search_context",
        "search_documents",
        "list_documents",
        "fetch_context",
        "search_evidence",
    }


def test_search_evidence_annotations_match_embedding_and_sqlite_behavior():
    mcp = FastMCP("search-evidence-annotations")
    register_tools(mcp, evidence_search_service=FakeEvidenceSearchService())

    annotations = _tool_map(mcp)["search_evidence"].annotations

    assert annotations is not None
    assert annotations.readOnlyHint is False
    assert annotations.destructiveHint is False
    assert annotations.idempotentHint is False
    assert annotations.openWorldHint is True


def test_search_evidence_real_fastmcp_call_returns_bare_structured_list():
    service = FakeEvidenceSearchService()
    mcp = FastMCP("search-evidence-call")
    register_tools(mcp, evidence_search_service=service)

    payload = _call_json(
        mcp,
        {
            "query": "Kubernetes reliability evidence",
            "source_types": ["resume"],
            "experience_types": ["professional"],
            "document_ids": ["doc-1"],
            "top_k": 3,
        },
    )

    assert isinstance(payload, list)
    assert payload == [
        {
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "document_version_id": "version-1",
            "source_type": "resume",
            "document_title": "Backend Resume",
            "section_title": "Reliability",
            "parent_section_title": "Experience",
            "exact_quote": "Reduced deployment failures by 40%.",
            "retrieval_score": 0.94,
            "experience_type": "professional",
            "file_name": "resume.md",
            "metadata": {"company": "Example Systems"},
        }
    ]
    request_payload = service.requests[0].model_dump(mode="json")
    assert request_payload == {
        "query": "Kubernetes reliability evidence",
        "source_types": ["resume"],
        "experience_types": ["professional"],
        "document_ids": ["doc-1"],
        "top_k": 3,
    }


def test_search_evidence_real_fastmcp_call_returns_bare_empty_list():
    class EmptyEvidenceSearchService:
        async def search_evidence(self, request: SearchEvidenceInput):
            del request
            return []

    mcp = FastMCP("search-evidence-empty")
    register_tools(mcp, evidence_search_service=EmptyEvidenceSearchService())

    assert _call_json(mcp, {"query": "no matching evidence"}) == []


def test_search_evidence_real_fastmcp_accepts_explicit_null_optional_filters():
    service = FakeEvidenceSearchService()
    mcp = FastMCP("search-evidence-null-filters")
    register_tools(mcp, evidence_search_service=service)

    _call_json(
        mcp,
        {
            "query": "Kubernetes reliability evidence",
            "source_types": None,
            "experience_types": None,
            "document_ids": None,
        },
    )

    assert service.requests[0].source_types is None
    assert service.requests[0].experience_types is None
    assert service.requests[0].document_ids is None


def test_search_evidence_missing_service_is_typed_internal_error_not_false_empty():
    mcp = FastMCP("search-evidence-missing-service")
    register_tools(mcp)

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(
            mcp.call_tool(
                "search_evidence",
                {"query": "Kubernetes reliability evidence"},
            )
        )

    message = str(exc_info.value)
    assert "[internal_error] Evidence retrieval failed" in message
    assert "not configured" not in message


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": "   "},
        {"query": "valid", "source_types": ["private-secret-source"]},
        {"query": "valid", "experience_types": ["private-secret-experience"]},
        {"query": "valid", "document_ids": [""]},
        {"query": "valid", "top_k": 0},
        {"query": "valid", "top_k": 51},
        {"query": "valid", "top_k": "3"},
        {"query": "valid", "top_k": 3.0},
        {"query": "valid", "top_k": True},
        {"query": "q" * 4097},
        {"query": "valid", "document_ids": ["d" * 513]},
        {"query": "valid", "document_ids": [f"doc-{index}" for index in range(101)]},
        {
            "query": "한" * 4096,
            "document_ids": [
                f"{index:03}-" + "한" * 500 for index in range(100)
            ],
        },
    ],
)
def test_search_evidence_fastmcp_unifies_invalid_inputs_in_sanitized_typed_error(
    arguments,
):
    mcp = FastMCP("search-evidence-invalid")
    service = FakeEvidenceSearchService()
    register_tools(mcp, evidence_search_service=service)

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(mcp.call_tool("search_evidence", arguments))

    message = str(exc_info.value)
    assert "[invalid_request] Invalid evidence request" in message
    assert "private-secret-source" not in message
    assert "private-secret-experience" not in message
    assert "input_value" not in message
    assert service.requests == []


def test_search_evidence_fastmcp_internal_error_is_typed_and_sanitized():
    mcp = FastMCP("search-evidence-error")
    register_tools(mcp, evidence_search_service=RawFailingEvidenceSearchService())

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(
            mcp.call_tool(
                "search_evidence",
                {"query": "Kubernetes reliability evidence"},
            )
        )

    message = str(exc_info.value)
    assert "internal_error" in message
    assert "Evidence retrieval failed" in message
    assert "super-secret-value" not in message
    assert "/Users/tester/private" not in message


@pytest.mark.parametrize("retrieval_score", [float("nan"), float("inf"), float("-inf")])
def test_search_evidence_fastmcp_defensively_rejects_non_finite_json_scores(
    retrieval_score,
):
    class NonFiniteEvidenceSearchService:
        async def search_evidence(self, request: SearchEvidenceInput):
            del request
            return [
                EvidenceChunk.model_construct(
                    chunk_id="chunk-non-finite",
                    document_id="doc-non-finite",
                    source_type=EvidenceSourceType.RESUME,
                    exact_quote="Stored quote.",
                    retrieval_score=retrieval_score,
                )
            ]

    mcp = FastMCP("search-evidence-non-finite")
    register_tools(mcp, evidence_search_service=NonFiniteEvidenceSearchService())

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(
            mcp.call_tool(
                "search_evidence",
                {"query": "Kubernetes reliability evidence"},
            )
        )

    message = str(exc_info.value)
    assert "[internal_error] Evidence retrieval failed" in message
    assert "NaN" not in message
    assert "Infinity" not in message
