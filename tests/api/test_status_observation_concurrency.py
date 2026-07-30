import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import pytest
from mcp.server.fastmcp import FastMCP

from api.tools import register_tools
from core.models import DocumentModel, SourceModel, SourceType, SyncStatus
from fetching.connectors import SourceConnector, SourceRegistry
from indexing.chunker import DocumentChunker
from indexing.ingestion_service import IngestionService
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.integration


class StaticConnector(SourceConnector):
    supports_stale_cleanup = True

    def __init__(self, source_id: str = "source_github"):
        self.source = SourceModel(
            source_id=source_id,
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.IDLE,
        )

    async def fetch_documents(self) -> list[DocumentModel]:
        return []


class NoopIndexer:
    async def index_documents(self, documents):
        return None


async def _call_tool_json(
    mcp: FastMCP,
    name: str,
    arguments: dict | None = None,
) -> dict:
    blocks = await mcp.call_tool(name, arguments or {})
    return json.loads(blocks[0].text)


def _source_updated_at(store: MetadataStore, source_id: str) -> str:
    with store._connect() as conn:
        row = conn.execute(
            "SELECT updated_at FROM sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
    assert row is not None
    return str(row["updated_at"])


@pytest.mark.parametrize(
    ("tool_name", "arguments", "enqueue_job"),
    (
        pytest.param("list_sources", {}, False, id="list-sources"),
        pytest.param(
            "get_sync_status",
            {"source_id": "source_github"},
            False,
            id="single-source-status",
        ),
        pytest.param("get_sync_status", {}, False, id="all-source-status"),
        pytest.param(
            "get_sync_status",
            {"source_id": "source_github"},
            True,
            id="exact-job-status",
        ),
    ),
)
def test_real_fastmcp_status_observation_is_read_only_during_unrelated_write(
    tmp_path,
    tool_name,
    arguments,
    enqueue_job,
):
    db_path = tmp_path / f"{tool_name}-{len(arguments)}-{enqueue_job}.sqlite3"
    store = MetadataStore(db_path)
    registry = SourceRegistry([StaticConnector()])
    store.register_source(registry.list_sources()[0])
    store.upsert_source(
        SourceModel(
            source_id="source_unrelated",
            source_type=SourceType.NOTION,
            name="Unrelated writer",
            enabled=True,
        )
    )
    call_arguments = dict(arguments)
    if enqueue_job:
        queued_job, enqueued = store.enqueue_sync_job("source_github")
        assert enqueued is True
        call_arguments["job_id"] = queued_job.job_id
    before_updated_at = _source_updated_at(store, "source_github")
    before_source = store.get_source("source_github")
    mcp = FastMCP(f"read-only-{tool_name}-{len(arguments)}-{enqueue_job}")
    register_tools(mcp, metadata_store=store, source_registry=registry)

    def call_observation_tool() -> dict:
        return asyncio.run(_call_tool_json(mcp, tool_name, call_arguments))

    writer = sqlite3.connect(db_path)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(
        "UPDATE sources SET updated_at = updated_at WHERE source_id = ?",
        ("source_unrelated",),
    )
    timed_out = False
    result = None
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(call_observation_tool)
            try:
                result = future.result(timeout=0.75)
            except FutureTimeoutError:
                timed_out = True
            finally:
                writer.rollback()
            if result is None:
                result = future.result(timeout=5)
    finally:
        writer.close()

    assert timed_out is False
    assert _source_updated_at(store, "source_github") == before_updated_at
    after_source = store.get_source("source_github")
    assert after_source is not None
    assert before_source is not None
    assert after_source.enabled == before_source.enabled
    assert after_source.sync_status == before_source.sync_status
    assert after_source.last_error == before_source.last_error
    assert after_source.updated_at == before_source.updated_at
    if tool_name == "list_sources":
        assert [source["source_id"] for source in result["sources"]] == [
            "source_github"
        ]
    elif enqueue_job:
        assert result["source"]["source_id"] == "source_github"
        assert result["job"]["job_id"] == call_arguments["job_id"]
    elif arguments:
        assert result["source"]["source_id"] == "source_github"
        assert result["latest_job"] is None
    else:
        assert [item["source"]["source_id"] for item in result["sources"]] == [
            "source_github"
        ]


def test_startup_registration_and_sync_enqueue_still_persist_lifecycle_state(tmp_path):
    store = MetadataStore(tmp_path / "registration-and-enqueue.sqlite3")
    registry = SourceRegistry([StaticConnector()])
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=registry,
        chunker=DocumentChunker(),
        indexer=NoopIndexer(),
        durable_dispatch=True,
    )

    registered = store.get_source("source_github")
    assert registered is not None
    registered_updated_at = registered.updated_at

    mcp = FastMCP("registration-and-enqueue")
    register_tools(
        mcp,
        ingestion_service=ingestion,
        metadata_store=store,
        source_registry=registry,
    )
    listed = asyncio.run(_call_tool_json(mcp, "list_sources"))
    observed = store.get_source("source_github")

    assert listed["sources"][0]["source_id"] == "source_github"
    assert observed is not None
    assert observed.updated_at == registered_updated_at

    accepted = asyncio.run(
        _call_tool_json(
            mcp,
            "sync_source",
            {"source_id": "source_github"},
        )
    )
    persisted = store.get_latest_sync_job("source_github")

    assert accepted["status"] == "queued"
    assert persisted is not None
    assert persisted.job_id == accepted["job_id"]
