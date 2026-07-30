import asyncio
import json
import sqlite3
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timedelta, timezone

import pytest
from mcp.server.fastmcp import FastMCP

from api.tools import _search_documents_result_payload, register_tools
from core.models import (
    ChunkModel,
    ContextSearchResult,
    DocumentModel,
    SourceModel,
    SourceType,
    SyncJobStatus,
    SyncStatus,
)
from fetching.connectors import SourceConnector, SourceRegistry
from indexing.chunker import DocumentChunker
from indexing.ingestion_service import IngestionService
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.integration

RETAINED_SOURCE_IDS = (
    "source_github",
    "source_notion",
    "source_obsidian",
    "source_tistory",
)
OBSIDIAN_DISABLED_ERROR = (
    "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
    "is not set or is not an existing directory."
)


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


class FakeFailingIngestion:
    async def enqueue_sync_source(self, source_id):
        raise ValueError(f"Unknown source: {source_id}")


class FakeQueuedIngestion:
    async def enqueue_sync_source(self, source_id):
        return Dumpable(
            {
                "job_id": "job-queued",
                "source_id": source_id,
                "status": "queued",
                "started_at": "2026-07-29T00:00:00+00:00",
                "finished_at": "",
                "error_message": "",
            }
        )


class FakeBlockingOnlyIngestion:
    async def sync_source(self, source_id):
        return Dumpable(
            {
                "job_id": "job-blocking-only",
                "source_id": source_id,
                "status": "succeeded",
                "error_message": "",
            }
        )


class FakeLeakyJobIngestion:
    async def enqueue_sync_source(self, source_id):
        return Dumpable(
            {
                "job_id": "job-leaky",
                "source_id": source_id,
                "status": "failed",
                "phase": "fetching_page_content",
                "upstream_total_pages": 265,
                "upstream_fetched_pages": 18,
                "last_progress_at": "2026-06-15T10:35:53+00:00",
                "status_message": "Fetching Notion page content 18/265 before indexing begins.",
                "error_message": (
                    "Sync failed with token=super-secret-value "
                    "and ghp_secretcredential"
                ),
            }
        )

    async def enqueue_all(self, source_ids=None):
        return {
            "status": "failed",
            "summary": {
                "total_sources": 1,
                "started": 0,
                "already_running": 0,
                "failed": 1,
                "skipped": 0,
                "requested_at": "2026-06-12T00:00:00+00:00",
            },
            "results": [
                {
                    "source_id": "source_github",
                    "launch_outcome": "failed",
                    "job": Dumpable(
                        {
                            "job_id": "job-leaky",
                            "source_id": "source_github",
                            "status": "failed",
                            "phase": "fetching_page_content",
                            "upstream_total_pages": 265,
                            "upstream_fetched_pages": 18,
                            "last_progress_at": "2026-06-15T10:35:53+00:00",
                            "status_message": (
                                "Fetching Notion page content 18/265 before indexing begins."
                            ),
                            "error_message": (
                                "Sync failed with token=super-secret-value "
                                "and ghp_secretcredential"
                            ),
                        }
                    ),
                    "message": "",
                }
            ],
        }


class ObserverCancelledOnceConnector(SourceConnector):
    source = SourceModel(
        source_id="source_github",
        source_type=SourceType.GITHUB,
        name="GitHub",
        enabled=True,
        auth_ref="env:GITHUB_TOKEN",
        sync_status=SyncStatus.IDLE,
    )

    def __init__(self, documents=None):
        self.documents = documents or []
        self.external_stop_signal = object()
        self.progress_callback = self._observer
        self.progress_stop_signal = self.external_stop_signal
        self.cancel_first_run = True

    async def _observer(self, event):
        if event.get("event") == "search_started" and self.cancel_first_run:
            self.cancel_first_run = False
            return self.external_stop_signal
        return None

    async def fetch_documents(self):
        if self.progress_callback is not None:
            result = self.progress_callback({"event": "search_started"})
            if asyncio.iscoroutine(result):
                result = await result
            if result is self.progress_stop_signal:
                from fetching.notion import _StopRequested

                raise _StopRequested
        return self.documents


class FakeCompletedSkippedSyncAllIngestion:
    async def enqueue_all(self, source_ids=None):
        return {
            "status": "accepted",
            "summary": {
                "total_sources": 2,
                "started": 1,
                "already_running": 0,
                "failed": 0,
                "skipped": 1,
                "requested_at": "2026-06-12T00:00:00+00:00",
            },
            "results": [
                {
                    "source_id": "source_github",
                    "launch_outcome": "started",
                    "job": Dumpable(
                        {
                            "job_id": "job-ok",
                            "source_id": "source_github",
                            "status": "running",
                            "error_message": "",
                        }
                    ),
                    "message": "",
                },
                {
                    "source_id": "source_obsidian",
                    "launch_outcome": "skipped",
                    "job": Dumpable(
                        {
                            "job_id": "job-disabled",
                            "source_id": "source_obsidian",
                            "status": "failed",
                            "error_message": OBSIDIAN_DISABLED_ERROR,
                        }
                    ),
                    "message": OBSIDIAN_DISABLED_ERROR,
                },
            ],
        }


class FakePartialSyncAllIngestion:
    async def enqueue_all(self, source_ids=None):
        return {
            "status": "partial",
            "summary": {
                "total_sources": 2,
                "started": 1,
                "already_running": 0,
                "failed": 1,
                "skipped": 0,
                "requested_at": "2026-06-12T00:00:00+00:00",
            },
            "results": [
                {
                    "source_id": "source_github",
                    "launch_outcome": "started",
                    "job": Dumpable(
                        {
                            "job_id": "job-ok",
                            "source_id": "source_github",
                            "status": "running",
                            "error_message": "",
                        }
                    ),
                    "message": "",
                },
                {
                    "source_id": "source_obsidian",
                    "launch_outcome": "failed",
                    "job": Dumpable(
                        {
                            "job_id": "job-failed",
                            "source_id": "source_obsidian",
                            "status": "failed",
                            "error_message": OBSIDIAN_DISABLED_ERROR,
                        }
                    ),
                    "message": OBSIDIAN_DISABLED_ERROR,
                },
            ],
        }


class FakeFailedSyncAllIngestion:
    async def enqueue_all(self, source_ids=None):
        return {
            "status": "failed",
            "summary": {
                "total_sources": 1,
                "started": 0,
                "already_running": 0,
                "failed": 1,
                "skipped": 0,
                "requested_at": "2026-06-12T00:00:00+00:00",
            },
            "results": [
                {
                    "source_id": "source_github",
                    "launch_outcome": "failed",
                    "job": Dumpable(
                        {
                            "job_id": "job-failed",
                            "source_id": "source_github",
                            "status": "failed",
                            "error_message": "boom",
                        }
                    ),
                    "message": "boom",
                }
            ],
        }


class FakePathFailingIngestion:
    async def enqueue_sync_source(self, source_id):
        raise ValueError(
            "Sync failed at /Users/eunhwa/private/vault.md "
            "with token supersecretvalue123456"
        )

    async def enqueue_all(self, source_ids=None):
        raise ValueError(
            "Bulk sync failed at /Users/eunhwa/private/vault.md "
            "with token supersecretvalue123456"
        )


class Dumpable:
    def __init__(self, value, **attrs):
        self.value = value
        for key, attr_value in attrs.items():
            setattr(self, key, attr_value)

    def model_dump(self, mode="json"):
        return self.value


class FakeSourceRegistry:
    def __init__(self, source_ids):
        self.sources = [
            Dumpable({"source_id": source_id}, source_id=source_id)
            for source_id in source_ids
        ]
        self.connectors = {
            source.source_id: Dumpable({}, source=source, supports_stale_cleanup=True, disabled_reason="")
            for source in self.sources
        }

    def list_sources(self):
        return self.sources

    def get_connector(self, source_id):
        return self.connectors[source_id]


class RefreshingObsidianRegistry:
    def __init__(self):
        self.calls = 0
        self.connector = Dumpable(
            {},
            supports_stale_cleanup=False,
            disabled_reason=OBSIDIAN_DISABLED_ERROR,
        )

    def list_sources(self):
        self.calls += 1
        source = SourceModel(
            source_id="source_obsidian",
            source_type=SourceType.OBSIDIAN,
            name="Obsidian",
            enabled=False,
            auth_ref="env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH",
            sync_status=SyncStatus.IDLE,
            last_error=OBSIDIAN_DISABLED_ERROR,
        )
        self.connector.source = source
        return [source]

    def get_connector(self, source_id):
        assert source_id == "source_obsidian"
        return self.connector


class FailingSourceRegistry:
    def __init__(self):
        self.calls = 0

    def list_sources(self):
        self.calls += 1
        if self.calls == 1:
            return [Dumpable({"source_id": "source_github"}, source_id="source_github")]
        raise RuntimeError("registry refresh failed with token=super-secret-value")

    def get_connector(self, source_id):
        raise AssertionError("get_connector should not be called")


def _enabled_obsidian_source() -> SourceModel:
    return SourceModel(
        source_id="source_obsidian",
        source_type=SourceType.OBSIDIAN,
        name="Obsidian",
        enabled=True,
        auth_ref="env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH",
        sync_status=SyncStatus.IDLE,
        last_error="",
    )


def _succeeded_obsidian_source() -> SourceModel:
    return _enabled_obsidian_source().model_copy(
        update={
            "sync_status": SyncStatus.SUCCEEDED,
            "last_synced_at": "2026-06-11T00:00:00+00:00",
        }
    )


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

    def get_source_status_snapshot(self, source_id):
        return {
            "latest_success_at": "",
            "latest_failure_at": "",
            "latest_failure_reason": "",
            "document_count": 0,
            "chunk_count": 0,
        }

    def get_chunk(self, chunk_id):
        return self.chunk

    def get_document(self, document_id):
        return None

    def list_chunks_for_document(self, document_id):
        return []


class FailingSyncAllMetadataStore(FakeMetadataStore):
    def get_source(self, source_id):
        raise RuntimeError("sync_all formatting failed with token=super-secret-value")


def _insert_legacy_web_source_row(store: MetadataStore):
    now = datetime.now(timezone.utc).isoformat()
    store.ensure_schema()
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO sources (
                source_id, source_type, name, enabled, auth_ref, sync_status,
                last_synced_at, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "source_web",
                "web",
                "Legacy Web",
                1,
                "",
                SyncStatus.SUCCEEDED.value,
                now,
                "",
                now,
                now,
            ),
        )


def _mark_legacy_web_job_running(store: MetadataStore, job_id: str):
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with store._connect() as conn:
        conn.execute(
            """
            UPDATE sources SET sync_status = ?, updated_at = ?
            WHERE source_id = ?
            """,
            (SyncStatus.RUNNING.value, old_timestamp, "source_web"),
        )
        conn.execute(
            """
            UPDATE sync_jobs SET status = ?, started_at = ?, heartbeat_at = ?
            WHERE job_id = ?
            """,
            (
                "running",
                old_timestamp,
                old_timestamp,
                job_id,
            ),
        )


def _legacy_web_status_rows(store: MetadataStore, job_id: str) -> tuple[str, str]:
    with store._connect() as conn:
        source_row = conn.execute(
            "SELECT sync_status FROM sources WHERE source_id = ?",
            ("source_web",),
        ).fetchone()
        job_row = conn.execute(
            "SELECT status FROM sync_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    return source_row["sync_status"], job_row["status"]


def _payload_text(payload: dict) -> str:
    return repr(payload)


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


class RecoveringExactStatusMetadataStore(FakeMetadataStore):
    def __init__(self):
        super().__init__()
        self.source = Dumpable(
            {"source_id": "source_fake", "sync_status": "running"},
            source_id="source_fake",
        )
        self.recovered_source = Dumpable(
            {"source_id": "source_fake", "sync_status": "failed"},
            source_id="source_fake",
        )
        self.running_job = Dumpable(
            {
                "job_id": "job-stale",
                "source_id": "source_fake",
                "status": "running",
            }
        )
        self.failed_job = Dumpable(
            {
                "job_id": "job-stale",
                "source_id": "source_fake",
                "status": "failed",
                "error_message": "Sync job timed out before status observation",
            }
        )
        self.recovered = False

    def get_latest_sync_job(self, source_id):
        assert source_id == "source_fake"
        self.recovered = True
        self.source = self.recovered_source
        return self.failed_job

    def get_sync_job(self, job_id):
        assert job_id == "job-stale"
        return self.failed_job if self.recovered else self.running_job


class QueuedStatusMetadataStore(FakeMetadataStore):
    def __init__(self):
        super().__init__()
        self.source = Dumpable(
            {"source_id": "source_fake", "sync_status": "running"},
            source_id="source_fake",
        )
        self.job = Dumpable(
            {
                "job_id": "job-queued",
                "source_id": "source_fake",
                "status": "queued",
                "started_at": "2026-07-29T00:00:00+00:00",
                "finished_at": "",
                "error_message": "",
            }
        )


class FakeContextSearch:
    async def search_context(self, query, filters=None, top_k=10, include_debug=False):
        debug_payload = {}
        if include_debug:
            debug_payload = {
                "retrieval_queries": [query],
                "initial_top_vector_score": 0.2,
                "final_top_score": 0.9,
            }
        return {
            "query": query,
            "results": [
                ContextSearchResult(
                    chunk_id="chunk-1",
                    document_id="doc-1",
                    source_id="source_github",
                    source_type="notion",
                    title="ContextWiki",
                    score=0.9,
                    preview="ContextWiki evidence",
                    text="ContextWiki evidence",
                )
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
                    "source_type": "notion",
                    "title": "ContextWiki",
                    "score": 0.9,
                    "vector_score": 0.2,
                    "metadata_priority": 1,
                    "matched_context": "ContextWiki evidence",
                    "url": "https://example.com/contextwiki",
                    "path": "ContextWiki",
                }
            ],
        }


class FakeDictContextSearch:
    async def search_context(self, query, filters=None, top_k=10, include_debug=False):
        return {
            "query": query,
            "results": [
                {
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "source_id": "source_github",
                    "source_type": "notion",
                    "title": "ContextWiki",
                    "score": 0.9,
                    "vector_score": 0.2,
                    "preview": "ContextWiki evidence",
                    "text": "ContextWiki evidence",
                }
            ],
        }

    async def search_documents(self, query, filters=None, top_k=10):
        return {
            "query": query,
            "results": [
                {
                    "document_id": "doc-1",
                    "chunk_id": "chunk-1",
                    "source_id": "source_github",
                    "source_type": "notion",
                    "title": "ContextWiki",
                    "score": 0.9,
                    "vector_score": 0.2,
                    "metadata_priority": 1,
                    "preview": "ContextWiki evidence",
                    "text": "Chunk-level text should not leak",
                    "line_start": 10,
                    "line_end": 20,
                    "version_id": "v1",
                    "updated_at": "2026-06-12T00:00:00+00:00",
                    "url": "https://example.com/contextwiki",
                    "path": "ContextWiki",
                }
            ],
        }


class FakeInvalidMatchedContextSearch(FakeDictContextSearch):
    async def search_documents(self, query, filters=None, top_k=10):
        result = await super().search_documents(query, filters=filters, top_k=top_k)
        result["results"][0]["matched_context"] = None
        return result


class FakeEmptyMatchedContextSearch(FakeDictContextSearch):
    async def search_documents(self, query, filters=None, top_k=10):
        result = await super().search_documents(query, filters=filters, top_k=top_k)
        result["results"][0]["matched_context"] = ""
        return result


class PreviewOnlySearchResult:
    preview = "dto-preview-secret"

    def __repr__(self):
        return "PreviewOnlySearchResult(dto-preview-secret)"


class NonMappingDumpSearchResult:
    def model_dump(self, mode="json", include=None):
        return ["model-dump-secret"]


class RaisingModelDumpSearchResult:
    def model_dump(self, mode="json", include=None):
        raise RuntimeError("raising-model-dump-secret")


class RaisingItemsSearchResult(Mapping):
    def __getitem__(self, key):
        raise RuntimeError("raising-items-secret")

    def __iter__(self):
        raise RuntimeError("raising-items-secret")

    def __len__(self):
        return 1


class FakeAnswerService:
    async def answer_with_citations(self, question, filters=None, top_k=5, include_debug=False):
        payload = {
            "question": question,
            "answer": "ContextWiki evidence",
            "evidence_status": "grounded",
            "citations": [{"chunk_id": "chunk-1"}],
            "used_chunks": ["chunk-1"],
        }
        if include_debug:
            payload.update(
                {
                    "answer_mode": "contextwiki_debug",
                    "debug": {
                        "question": question,
                        "retrieval_queries": [question],
                    },
                    "debug_markdown": f"## Query\n- retrieval queries: `{question}`",
                }
            )
        return payload


class CapturingAnswerService(FakeAnswerService):
    def __init__(self):
        self.calls = []

    async def answer_with_citations(self, question, filters=None, top_k=5, include_debug=False):
        self.calls.append(
            {
                "question": question,
                "filters": filters,
                "top_k": top_k,
                "include_debug": include_debug,
            }
        )
        return await super().answer_with_citations(
            question,
            filters=filters,
            top_k=top_k,
            include_debug=include_debug,
        )


class CapturingContextSearch(FakeContextSearch):
    def __init__(self):
        self.calls = []

    async def search_context(self, query, filters=None, top_k=10, include_debug=False):
        self.calls.append(
            {
                "query": query,
                "filters": filters,
                "top_k": top_k,
                "include_debug": include_debug,
            }
        )
        return await super().search_context(
            query,
            filters=filters,
            top_k=top_k,
            include_debug=include_debug,
        )

    async def search_documents(self, query, filters=None, top_k=10):
        self.calls.append(
            {
                "query": query,
                "filters": filters,
                "top_k": top_k,
                "tool": "search_documents",
            }
        )
        return await super().search_documents(query, filters=filters, top_k=top_k)


def test_sync_source_returns_structured_error_for_unknown_source():
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakeFailingIngestion(),
    )

    result = asyncio.run(mcp.tools["sync_source"]("missing"))

    assert result["status"] == "error"
    assert "Unknown source" in result["message"]


def test_sync_source_returns_new_durable_job_as_queued():
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakeQueuedIngestion(),
    )

    result = asyncio.run(mcp.tools["sync_source"]("source_github"))

    assert result == {
        "job_id": "job-queued",
        "source_id": "source_github",
        "status": "queued",
        "started_at": "2026-07-29T00:00:00+00:00",
        "finished_at": "",
        "error_message": "",
    }


def test_sync_source_redacts_secret_like_unknown_source_ids():
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakeFailingIngestion(),
    )

    result = asyncio.run(
        mcp.tools["sync_source"]("source_web?token=super-secret-value")
    )

    assert result["status"] == "error"
    assert "super-secret-value" not in result["message"]
    assert "token=<redacted>" in result["message"]


def test_sync_source_rejects_non_public_source_ids(tmp_path):
    class FakeStartOnlyIngestion:
        async def enqueue_sync_source(self, source_id):
            raise AssertionError("non-public source should be rejected before launch")

    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_private",
            source_type=SourceType.GITHUB,
            name="Private",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.IDLE,
        )
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakeStartOnlyIngestion(),
        metadata_store=store,
        source_registry=FakeSourceRegistry(("source_github",)),
    )

    result = asyncio.run(mcp.tools["sync_source"]("source_private"))

    assert result["status"] == "error"
    assert "Unknown source" in result["message"]


def test_sync_source_redacts_returned_job_error_payload():
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakeLeakyJobIngestion(),
    )

    result = asyncio.run(mcp.tools["sync_source"]("source_github"))
    payload = _payload_text(result)

    assert result["error_message"] == "Sync failed with token=<redacted>"
    assert "super-secret-value" not in payload
    assert "ghp_secretcredential" not in payload
    assert "phase" not in result
    assert "upstream_total_pages" not in result
    assert "upstream_fetched_pages" not in result
    assert "last_progress_at" not in result
    assert "status_message" not in result


def test_sync_source_returns_error_when_durable_enqueue_is_unavailable():
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakeBlockingOnlyIngestion(),
    )

    result = asyncio.run(mcp.tools["sync_source"]("source_github"))

    assert result == {
        "status": "error",
        "message": "ingestion service does not support durable sync enqueue",
    }


def test_sync_source_redacts_public_error_paths_and_whitespace_secrets():
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakePathFailingIngestion(),
    )

    result = asyncio.run(mcp.tools["sync_source"]("source_github"))
    payload = _payload_text(result)

    assert result["status"] == "error"
    assert "/Users/eunhwa/private/vault.md" not in payload
    assert "supersecretvalue123456" not in payload
    assert "token <redacted>" in result["message"]


def test_sync_source_reports_observer_cancelled_worker_failure_before_reenqueue(
    tmp_path,
):
    document = DocumentModel(
        id="doc-1",
        source_id="source_github",
        title="GitHub doc",
        content="Tool-layer replay should return the failed public job once before relaunching.",
        url="https://example.com/doc-1",
        platform="GitHub",
        path="doc-1.md",
    )
    connector = ObserverCancelledOnceConnector([document])
    registry = SourceRegistry([connector])
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=registry,
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=FakeIndexer(),
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=service,
        metadata_store=store,
        source_registry=registry,
    )

    async def run_flow():
        accepted = await mcp.tools["sync_source"]("source_github")
        claimed = store.claim_next_sync_job()
        assert claimed is not None
        completed = await service.run_claimed_sync_job(claimed.job_id)
        terminal_status = await mcp.tools["get_sync_status"]("source_github")
        reaccepted = await mcp.tools["sync_source"]("source_github")
        return accepted, completed, terminal_status, reaccepted

    accepted, completed, terminal_status, reaccepted = asyncio.run(run_flow())

    assert accepted["source_id"] == "source_github"
    assert accepted["status"] == SyncJobStatus.QUEUED.value
    assert "phase" not in accepted
    assert completed.job_id == accepted["job_id"]
    assert completed.status == SyncJobStatus.FAILED
    assert terminal_status["latest_job"]["job_id"] == accepted["job_id"]
    assert terminal_status["latest_job"]["status"] == SyncJobStatus.FAILED.value
    assert terminal_status["latest_job"]["error_message"] == (
        "Sync request was cancelled by a progress observer before completion."
    )
    assert "phase" not in terminal_status["latest_job"]
    assert reaccepted["job_id"] != accepted["job_id"]
    assert reaccepted["status"] == SyncJobStatus.QUEUED.value


def test_sync_all_redacts_public_error_paths_and_whitespace_secrets():
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakePathFailingIngestion(),
    )

    result = asyncio.run(mcp.tools["sync_all"]())
    payload = _payload_text(result)

    assert result["status"] == "failed"
    assert "/Users/eunhwa/private/vault.md" not in payload
    assert "supersecretvalue123456" not in payload
    assert "token <redacted>" in result["message"]


def test_sync_all_skips_signature_introspection_when_public_filtering_is_not_needed(
    monkeypatch,
):
    class FakeInspectableIngestion:
        async def enqueue_all(self):
            return {
                "status": "accepted",
                "summary": {
                    "total_sources": 1,
                    "started": 1,
                    "already_running": 0,
                    "failed": 0,
                    "skipped": 0,
                    "requested_at": "2026-06-12T00:00:00+00:00",
                },
                "results": [
                    {
                        "source_id": "source_github",
                        "launch_outcome": "started",
                        "job": None,
                        "message": "",
                    }
                ],
            }

    monkeypatch.setattr(
        "api.tools.inspect.signature",
        lambda _: (_ for _ in ()).throw(TypeError("signature unavailable")),
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakeInspectableIngestion(),
        source_registry=FakeSourceRegistry(("source_github",)),
    )

    result = asyncio.run(mcp.tools["sync_all"]())

    assert result["status"] == "accepted"
    assert result["summary"]["started"] == 1


def test_sync_all_returns_structured_error_when_preflight_source_refresh_fails():
    class FakeSyncAllIngestion:
        async def enqueue_all(self):
            raise AssertionError("sync_all should not be called when preflight fails")

    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakeSyncAllIngestion(),
        source_registry=FailingSourceRegistry(),
    )

    result = asyncio.run(mcp.tools["sync_all"]())

    assert result["status"] == "failed"
    assert "registry refresh failed" in result["message"]
    assert "super-secret-value" not in result["message"]
    assert result["summary"]["total_sources"] == 0
    assert result["summary"]["started"] == 0
    assert result["summary"]["already_running"] == 0
    assert result["summary"]["failed"] == 0
    assert result["summary"]["skipped"] == 0
    assert result["summary"]["requested_at"]
    assert result["results"] == []


def test_sync_all_returns_structured_error_when_ingestion_service_is_missing():
    mcp = FakeMCP()
    register_tools(mcp)

    result = asyncio.run(mcp.tools["sync_all"]())

    assert result["status"] == "failed"
    assert result["message"] == "ingestion service is not configured"
    assert result["summary"]["total_sources"] == 0
    assert result["summary"]["requested_at"]
    assert result["results"] == []


def test_sync_all_returns_structured_error_when_public_filtering_is_unsupported():
    class GrowingSourceRegistry(FakeSourceRegistry):
        def __init__(self):
            super().__init__(("source_github",))
            self.calls = 0

        def list_sources(self):
            self.calls += 1
            if self.calls == 1:
                return self.sources
            return self.sources + [
                Dumpable({"source_id": "source_private"}, source_id="source_private")
            ]

    class FakeNoFilterSupportIngestion:
        async def enqueue_all(self):
            raise AssertionError("legacy no-arg sync_all should not be called")

    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakeNoFilterSupportIngestion(),
        source_registry=GrowingSourceRegistry(),
    )

    result = asyncio.run(mcp.tools["sync_all"]())

    assert result["status"] == "failed"
    assert "does not support public bulk sync filtering" in result["message"]
    assert result["summary"]["total_sources"] == 0
    assert result["summary"]["requested_at"]
    assert result["results"] == []


def test_sync_all_returns_structured_error_when_public_result_formatting_fails():
    class FakeSyncAllIngestion:
        async def enqueue_all(self):
            return {
                "status": "accepted",
                "summary": {
                    "total_sources": 1,
                    "started": 1,
                    "already_running": 0,
                    "failed": 0,
                    "skipped": 0,
                },
                "results": [
                    {
                        "source_id": "source_fake",
                        "launch_outcome": "started",
                        "job": None,
                        "message": "",
                    }
                ],
            }

    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakeSyncAllIngestion(),
        metadata_store=FailingSyncAllMetadataStore(),
        source_registry=FakeSourceRegistry(("source_fake",)),
    )

    result = asyncio.run(mcp.tools["sync_all"]())

    assert result["status"] == "failed"
    assert "sync_all formatting failed" in result["message"]
    assert "super-secret-value" not in result["message"]
    assert result["summary"]["total_sources"] == 0
    assert result["summary"]["requested_at"]
    assert result["results"] == []


def test_sync_all_preserves_upstream_error_status_when_no_public_results(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    mcp = FakeMCP()

    class FakeEmptyErrorSyncAllIngestion:
        async def enqueue_all(self):
            return {"status": "failed", "summary": {}, "results": []}

    register_tools(
        mcp,
        ingestion_service=FakeEmptyErrorSyncAllIngestion(),
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    result = asyncio.run(mcp.tools["sync_all"]())

    assert result["status"] == "failed"
    assert result["summary"] == {
        "total_sources": 0,
        "started": 0,
        "already_running": 0,
        "skipped": 0,
        "failed": 0,
    }
    assert result["results"] == []


def test_sync_all_preserves_upstream_failed_status_when_empty_results_still_report_totals(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    mcp = FakeMCP()

    class FakeEmptyFailedSyncAllIngestion:
        async def enqueue_all(self):
            return {
                "status": "failed",
                "summary": {
                    "total_sources": 1,
                    "started": 0,
                    "already_running": 0,
                    "failed": 1,
                    "skipped": 0,
                },
                "results": [],
            }

    register_tools(
        mcp,
        ingestion_service=FakeEmptyFailedSyncAllIngestion(),
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    result = asyncio.run(mcp.tools["sync_all"]())

    assert result["status"] == "failed"
    assert result["summary"] == {
        "total_sources": 0,
        "started": 0,
        "already_running": 0,
        "failed": 0,
        "skipped": 0,
    }
    assert result["results"] == []


def test_get_sync_status_returns_structured_error_when_preflight_source_refresh_fails(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.IDLE,
        )
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FailingSourceRegistry(),
    )

    result = asyncio.run(mcp.tools["get_sync_status"]())

    assert result["status"] == "error"
    assert result["sources"] == []
    assert "registry refresh failed" in result["message"]
    assert "super-secret-value" not in result["message"]


def test_get_sync_status_single_source_preserves_shape_when_preflight_source_refresh_fails(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.IDLE,
        )
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FailingSourceRegistry(),
    )

    result = asyncio.run(mcp.tools["get_sync_status"]("source_github"))

    assert result["status"] == "error"
    assert result["source"] is None
    assert result["latest_job"] is None
    assert "registry refresh failed" in result["message"]
    assert "super-secret-value" not in result["message"]


def test_list_sources_returns_structured_error_when_preflight_source_refresh_fails():
    mcp = FakeMCP()
    register_tools(
        mcp,
        source_registry=FailingSourceRegistry(),
    )

    result = asyncio.run(mcp.tools["list_sources"]())

    assert result["status"] == "error"
    assert result["sources"] == []
    assert "registry refresh failed" in result["message"]
    assert "super-secret-value" not in result["message"]


def test_sync_all_redacts_returned_job_error_payload(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.FAILED,
            last_error="",
        )
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakeLeakyJobIngestion(),
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    result = asyncio.run(mcp.tools["sync_all"]())
    payload = _payload_text(result)

    assert result["results"][0]["job"]["error_message"] == "Sync failed with token=<redacted>"
    assert result["results"][0]["launch_outcome"] == "failed"
    assert "super-secret-value" not in payload
    assert "ghp_secretcredential" not in payload
    assert "phase" not in result["results"][0]["job"]
    assert "upstream_total_pages" not in result["results"][0]["job"]
    assert "upstream_fetched_pages" not in result["results"][0]["job"]
    assert "last_progress_at" not in result["results"][0]["job"]
    assert "status_message" not in result["results"][0]["job"]


def test_sync_all_filters_non_public_sources_from_results_and_summary(tmp_path):
    class FakeMixedSyncAllIngestion:
        async def enqueue_all(self, source_ids=None):
            assert source_ids is None
            return {
                "status": "partial",
                "summary": {
                    "total_sources": 2,
                    "started": 1,
                    "already_running": 0,
                    "failed": 1,
                    "skipped": 0,
                    "requested_at": "2026-06-12T00:00:00+00:00",
                },
                "results": [
                    {
                        "source_id": "source_github",
                        "launch_outcome": "started",
                        "job": Dumpable(
                            {
                                "job_id": "job-public",
                                "source_id": "source_github",
                                "status": "running",
                                "error_message": "",
                            }
                        ),
                        "message": "",
                    },
                    {
                        "source_id": "source_private",
                        "launch_outcome": "failed",
                        "job": Dumpable(
                            {
                                "job_id": "job-private",
                                "source_id": "source_private",
                                "status": "failed",
                                "error_message": "hidden",
                            }
                        ),
                        "message": "hidden",
                    },
                ],
            }

    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.SUCCEEDED,
        )
    )
    store.upsert_source(
        SourceModel(
            source_id="source_private",
            source_type=SourceType.GITHUB,
            name="Private",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.FAILED,
        )
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakeMixedSyncAllIngestion(),
        metadata_store=store,
        source_registry=FakeSourceRegistry(("source_github",)),
    )

    result = asyncio.run(mcp.tools["sync_all"]())

    assert [item["source_id"] for item in result["results"]] == ["source_github"]
    assert result["status"] == "accepted"
    assert result["summary"]["total_sources"] == 1
    assert result["summary"]["started"] == 1
    assert result["summary"]["failed"] == 0
    assert result["summary"]["requested_at"] == "2026-06-12T00:00:00+00:00"


def test_sync_all_hidden_only_sources_do_not_leak_failed_status(tmp_path):
    class FakeHiddenOnlySyncAllIngestion:
        async def enqueue_all(self, source_ids=None):
            assert source_ids is None
            return {
                "status": "failed",
                "summary": {
                    "total_sources": 1,
                    "started": 0,
                    "already_running": 0,
                    "failed": 1,
                    "skipped": 0,
                    "requested_at": "2026-06-12T00:00:00+00:00",
                },
                "results": [
                    {
                        "source_id": "source_hidden",
                        "launch_outcome": "failed",
                        "job": None,
                        "message": "hidden failure",
                    }
                ],
            }

    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.IDLE,
        )
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakeHiddenOnlySyncAllIngestion(),
        metadata_store=store,
        source_registry=FakeSourceRegistry(("source_github",)),
    )

    result = asyncio.run(mcp.tools["sync_all"]())

    assert result["status"] == "accepted"
    assert result["summary"]["total_sources"] == 0
    assert result["summary"]["requested_at"] == "2026-06-12T00:00:00+00:00"
    assert result["results"] == []


def test_sync_all_uses_legacy_noarg_when_all_registry_sources_are_public(tmp_path):
    class LegacySyncAllIngestion:
        def __init__(self):
            self.called = False

        async def enqueue_all(self):
            self.called = True
            return {"status": "accepted", "summary": {}, "results": []}

    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.IDLE,
        )
    )
    ingestion = LegacySyncAllIngestion()
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=ingestion,
        metadata_store=store,
        source_registry=FakeSourceRegistry(("source_github",)),
    )

    result = asyncio.run(mcp.tools["sync_all"]())

    assert result["status"] == "accepted"
    assert result["results"] == []
    assert ingestion.called is True


def test_sync_all_preserves_upstream_order_when_all_registry_sources_are_public(tmp_path):
    class OrderedSyncAllIngestion:
        async def enqueue_all(self, source_ids=None):
            assert source_ids is None
            return {
                "status": "accepted",
                "summary": {},
                "results": [
                    {"source_id": "source_b", "launch_outcome": "started", "job": None, "message": ""},
                    {"source_id": "source_a", "launch_outcome": "started", "job": None, "message": ""},
                ],
            }

    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    for source_id in ("source_b", "source_a"):
        store.upsert_source(
            SourceModel(
                source_id=source_id,
                source_type=SourceType.GITHUB,
                name=source_id,
                enabled=True,
                auth_ref="env:GITHUB_TOKEN",
                sync_status=SyncStatus.IDLE,
            )
        )
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=OrderedSyncAllIngestion(),
        metadata_store=store,
        source_registry=FakeSourceRegistry(("source_b", "source_a")),
    )

    result = asyncio.run(mcp.tools["sync_all"]())

    assert [item["source_id"] for item in result["results"]] == ["source_b", "source_a"]


def test_sync_all_passthrough_preserves_accepted_and_skipped_outcomes(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(_succeeded_obsidian_source().model_copy(update={"source_id": "source_github"}))
    store.upsert_source(
        SourceModel(
            source_id="source_obsidian",
            source_type=SourceType.OBSIDIAN,
            name="Obsidian",
            enabled=False,
            auth_ref="env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH",
            sync_status=SyncStatus.FAILED,
            last_error=OBSIDIAN_DISABLED_ERROR,
            stale_cleanup_disabled_reason=OBSIDIAN_DISABLED_ERROR,
        )
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakeCompletedSkippedSyncAllIngestion(),
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    result = asyncio.run(mcp.tools["sync_all"]())

    assert result["status"] == "accepted"
    assert result["summary"]["skipped"] == 1
    assert {
        (item["source_id"], item["launch_outcome"])
        for item in result["results"]
    } == {("source_github", "started"), ("source_obsidian", "skipped")}


def test_sync_all_passthrough_preserves_partial_status(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(_succeeded_obsidian_source().model_copy(update={"source_id": "source_github"}))
    store.upsert_source(
        SourceModel(
            source_id="source_obsidian",
            source_type=SourceType.OBSIDIAN,
            name="Obsidian",
            enabled=True,
            auth_ref="env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH",
            sync_status=SyncStatus.FAILED,
            last_error=OBSIDIAN_DISABLED_ERROR,
        )
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakePartialSyncAllIngestion(),
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    result = asyncio.run(mcp.tools["sync_all"]())

    assert result["status"] == "partial"
    assert result["summary"]["failed"] == 1
    assert {
        (item["source_id"], item["launch_outcome"])
        for item in result["results"]
    } == {("source_github", "started"), ("source_obsidian", "failed")}


def test_sync_all_passthrough_preserves_failed_status(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.FAILED,
            last_error="boom",
        )
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakeFailedSyncAllIngestion(),
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    result = asyncio.run(mcp.tools["sync_all"]())

    assert result["status"] == "failed"
    assert result["summary"]["failed"] == 1
    assert result["results"][0]["launch_outcome"] == "failed"


def test_status_payloads_redact_persisted_secret_fields(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.FAILED,
            last_error=(
                "last sync failed with api_key=super-secret-value "
                "and github_pat_secretcredential"
            ),
        )
    )
    job = store.create_sync_job("source_github")
    now = datetime.now(timezone.utc).isoformat()
    with store._connect() as conn:
        conn.execute(
            "UPDATE sources SET auth_ref = ? WHERE source_id = ?",
            ("basic user:super-secret-value", "source_github"),
        )
        conn.execute(
            """
            UPDATE sync_jobs SET status = ?, finished_at = ?, error_message = ?
            WHERE job_id = ?
            """,
            (
                "failed",
                now,
                "job failed with token=super-secret-value and ghp_secretcredential",
                job.job_id,
            ),
        )

    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    sources = asyncio.run(mcp.tools["list_sources"]())
    single = asyncio.run(mcp.tools["get_sync_status"]("source_github"))
    all_sources = asyncio.run(mcp.tools["get_sync_status"]())
    payload = _payload_text(
        {
            "sources": sources,
            "single": single,
            "all_sources": all_sources,
        }
    )

    assert sources["sources"][0]["auth_ref"] == "<redacted>"
    assert sources["sources"][0]["last_error"] == (
        "last sync failed with api_key=<redacted>"
    )
    assert single["latest_job"]["error_message"] == "job failed with token=<redacted>"
    assert all_sources["sources"][0]["latest_job"]["error_message"] == (
        "job failed with token=<redacted>"
    )
    assert "super-secret-value" not in payload
    assert "github_pat_secretcredential" not in payload
    assert "ghp_secretcredential" not in payload
    assert "basic user:" not in payload


def test_status_payloads_redact_public_error_paths_and_whitespace_secrets(tmp_path):
    notion_tokens = (
        "ntn_abcdefghijklmnopqrstuvwxyz0123456789",
        "secret_abcdefghijklmnopqrstuvwxyz0123456789",
    )
    sensitive_paths = (
        "/Users/eunhwa/private,vault/source notes.md",
        r"C:\Users\eunhwa\private,vault\job notes.md",
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.FAILED,
            last_error=(
                f"failed reading {sensitive_paths[0]}, job_id=job-123; "
                f"source_id=source_github token={notion_tokens[0]}"
            ),
        )
    )
    job = store.create_sync_job("source_github")
    now = datetime.now(timezone.utc).isoformat()
    with store._connect() as conn:
        conn.execute(
            """
            UPDATE sync_jobs SET status = ?, finished_at = ?, error_message = ?
            WHERE job_id = ?
            """,
            (
                "failed",
                now,
                f"job failed at {sensitive_paths[1]}; job_id=job-123, "
                f"source_id=source_github token={notion_tokens[1]}",
                job.job_id,
            ),
        )

    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    sources = asyncio.run(mcp.tools["list_sources"]())
    status = asyncio.run(mcp.tools["get_sync_status"]("source_github"))
    payload = _payload_text({"sources": sources, "status": status})

    assert all(token not in payload for token in notion_tokens)
    assert all(path not in payload for path in sensitive_paths)
    assert "vault/source notes.md" not in payload
    assert "vault\\job notes.md" not in payload
    assert "notes.md" not in payload
    for value in (
        sources["sources"][0]["last_error"],
        status["source"]["last_error"],
        status["latest_job"]["error_message"],
    ):
        assert "job_id=job-123" in value
        assert "source_id=source_github" in value
        assert "<redacted" in value


def test_status_payloads_redact_semicolon_cookie_headers_and_unc_paths(tmp_path):
    raw_values = (
        "session=alpha",
        "theme=private",
        "preference=hidden",
        "sid=bravo",
        "unknown_attribute=top-secret",
        "folded_cookie=delta",
        r"\\server\private share\meeting notes.md",
        r"\\?\C:\Users\tester\private vault\meeting notes.md",
    )
    source_error = (
        "Cookie: session=alpha, theme=private, preference=hidden\n"
        rf"job_id=job-123; failed reading {raw_values[6]}, source_id=source_github"
    )
    job_error = (
        "Set-Cookie: sid=bravo, unknown_attribute=top-secret,\n"
        "\tfolded_cookie=delta\n"
        rf"job_id=job-123; retry_count=2; failed reading {raw_values[7]}, "
        "source_id=source_github"
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.FAILED,
            last_error="placeholder",
        )
    )
    job = store.create_sync_job("source_github")
    now = datetime.now(timezone.utc).isoformat()
    with store._connect() as conn:
        conn.execute(
            "UPDATE sources SET last_error = ? WHERE source_id = ?",
            (source_error, "source_github"),
        )
        conn.execute(
            """
            UPDATE sync_jobs SET status = ?, finished_at = ?, error_message = ?
            WHERE job_id = ?
            """,
            ("failed", now, job_error, job.job_id),
        )

    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    sources = asyncio.run(mcp.tools["list_sources"]())
    status = asyncio.run(mcp.tools["get_sync_status"]("source_github"))
    payload = _payload_text({"sources": sources, "status": status})

    assert all(raw_value not in payload for raw_value in raw_values)
    assert "job_id=job-123" in payload
    assert "source_id=source_github" in payload
    assert "retry_count=2" in payload
    assert "<redacted>" in payload


def test_list_and_status_redact_short_explicit_auth_credentials_from_legacy_rows(
    tmp_path,
):
    source_error = (
        "provider rejected Bearer abc123 while syncing, source_id=source_github; "
        "job_id=job-123"
    )
    job_error = (
        "fallback Basic Og== because retrying; "
        "source_id=source_github; job_id=job-123"
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.FAILED,
            last_error="placeholder",
        )
    )
    job = store.create_sync_job("source_github")
    now = datetime.now(timezone.utc).isoformat()
    with store._connect() as conn:
        conn.execute(
            "UPDATE sources SET last_error = ? WHERE source_id = ?",
            (source_error, "source_github"),
        )
        conn.execute(
            """
            UPDATE sync_jobs SET status = ?, finished_at = ?, error_message = ?
            WHERE job_id = ?
            """,
            ("failed", now, job_error, job.job_id),
        )

    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    sources = asyncio.run(mcp.tools["list_sources"]())
    status = asyncio.run(mcp.tools["get_sync_status"]("source_github"))
    payload = _payload_text({"sources": sources, "status": status})

    assert "abc123" not in payload
    assert "Og==" not in payload
    assert "Bearer <redacted-auth> while syncing," in payload
    assert "Basic <redacted-auth> because retrying;" in payload
    assert "source_id=source_github" in payload
    assert "job_id=job-123" in payload


def test_status_payloads_redact_folded_authorization_credentials_from_legacy_rows(
    tmp_path,
):
    source_error = (
        "Authorization: Bearer\r\n"
        " folded-public-bearer-credential\r\n"
        "source clear diagnostic source_id=source_github job_id=job-123"
    )
    job_error = (
        "Authorization: Basic\r"
        "\tfolded-public-basic-credential\r"
        "job clear diagnostic phase=fetching_page_content retry_count=26"
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.FAILED,
            last_error="placeholder",
        )
    )
    job = store.create_sync_job("source_github")
    now = datetime.now(timezone.utc).isoformat()
    with store._connect() as conn:
        conn.execute(
            "UPDATE sources SET last_error = ? WHERE source_id = ?",
            (source_error, "source_github"),
        )
        conn.execute(
            """
            UPDATE sync_jobs SET status = ?, finished_at = ?, error_message = ?
            WHERE job_id = ?
            """,
            ("failed", now, job_error, job.job_id),
        )

    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    sources = asyncio.run(mcp.tools["list_sources"]())
    status = asyncio.run(mcp.tools["get_sync_status"]("source_github"))
    payload = _payload_text({"sources": sources, "status": status})

    assert "folded-public-bearer-credential" not in payload
    assert "folded-public-basic-credential" not in payload
    assert "source clear diagnostic" in payload
    assert "source_id=source_github" in payload
    assert "job_id=job-123" in payload
    assert "job clear diagnostic" in payload
    assert "phase=fetching_page_content" in payload
    assert "retry_count=26" in payload


def test_status_payloads_redact_multistage_folded_authorization_from_legacy_rows(
    tmp_path,
):
    source_error = (
        "Authorization:\r\n"
        " Bearer\r\n"
        " multistage-public-bearer-credential\r\n"
        "source clear diagnostic source_id=source_github job_id=job-123"
    )
    job_error = (
        "Authorization=\r"
        "\tBasic\r"
        "\tmultistage-public-basic-credential\r"
        "job clear diagnostic phase=fetching_page_content retry_count=28"
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.FAILED,
            last_error="placeholder",
        )
    )
    job = store.create_sync_job("source_github")
    now = datetime.now(timezone.utc).isoformat()
    with store._connect() as conn:
        conn.execute(
            "UPDATE sources SET last_error = ? WHERE source_id = ?",
            (source_error, "source_github"),
        )
        conn.execute(
            """
            UPDATE sync_jobs SET status = ?, finished_at = ?, error_message = ?
            WHERE job_id = ?
            """,
            ("failed", now, job_error, job.job_id),
        )

    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    sources = asyncio.run(mcp.tools["list_sources"]())
    status = asyncio.run(mcp.tools["get_sync_status"]("source_github"))
    payload = _payload_text({"sources": sources, "status": status})

    assert "multistage-public-bearer-credential" not in payload
    assert "multistage-public-basic-credential" not in payload
    assert "source clear diagnostic" in payload
    assert "source_id=source_github" in payload
    assert "job_id=job-123" in payload
    assert "job clear diagnostic" in payload
    assert "phase=fetching_page_content" in payload
    assert "retry_count=28" in payload


def test_status_payloads_redact_bare_name_folded_authorization_from_legacy_rows(
    tmp_path,
):
    source_error = (
        "Authorization\r\n"
        " Bearer\r\n"
        " bare-name-public-bearer-credential\r\n"
        "source clear diagnostic source_id=source_github job_id=job-123"
    )
    job_error = (
        "Authorization\r"
        "\tBasic\r"
        "\tbare-name-public-basic-credential\r"
        "job clear diagnostic phase=fetching_page_content retry_count=34"
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.FAILED,
            last_error="placeholder",
        )
    )
    job = store.create_sync_job("source_github")
    now = datetime.now(timezone.utc).isoformat()
    with store._connect() as conn:
        conn.execute(
            "UPDATE sources SET last_error = ? WHERE source_id = ?",
            (source_error, "source_github"),
        )
        conn.execute(
            """
            UPDATE sync_jobs SET status = ?, finished_at = ?, error_message = ?
            WHERE job_id = ?
            """,
            ("failed", now, job_error, job.job_id),
        )

    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    sources = asyncio.run(mcp.tools["list_sources"]())
    status = asyncio.run(mcp.tools["get_sync_status"]("source_github"))
    payload = _payload_text({"sources": sources, "status": status})

    assert "bare-name-public-bearer-credential" not in payload
    assert "bare-name-public-basic-credential" not in payload
    assert "source clear diagnostic" in payload
    assert "source_id=source_github" in payload
    assert "job_id=job-123" in payload
    assert "job clear diagnostic" in payload
    assert "phase=fetching_page_content" in payload
    assert "retry_count=34" in payload


def test_status_payloads_preserve_lone_cr_clear_diagnostic_after_legacy_path(
    tmp_path,
):
    sensitive_path = "/Users/tester/private vault/observability notes.md"
    raw_error = (
        f"provider failure {sensitive_path}\r"
        "clear diagnostic source_id=source_github "
        "job_id=job-123 retry_count=25"
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.FAILED,
            last_error="placeholder",
        )
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE sources SET last_error = ? WHERE source_id = ?",
            (raw_error, "source_github"),
        )

    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    sources = asyncio.run(mcp.tools["list_sources"]())
    payload = _payload_text(sources)

    assert sensitive_path not in payload
    assert "observability notes.md" not in payload
    assert "clear diagnostic" in payload
    assert "source_id=source_github" in payload
    assert "job_id=job-123" in payload
    assert "retry_count=25" in payload


def test_status_payloads_fail_closed_for_cookie_names_that_match_diagnostic_fields(
    tmp_path,
):
    cookie_error = (
        "Set-Cookie: source_id=cookie-source-secret; job_id=cookie-job-secret; "
        "phase=cookie-phase-secret\n"
        "ordinary diagnostic, source_id=source_github; job_id=job-123; "
        "phase=fetching_page_content"
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.FAILED,
            last_error="placeholder",
        )
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE sources SET last_error = ? WHERE source_id = ?",
            (cookie_error, "source_github"),
        )

    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    sources = asyncio.run(mcp.tools["list_sources"]())
    payload = _payload_text(sources)

    for secret in (
        "cookie-source-secret",
        "cookie-job-secret",
        "cookie-phase-secret",
    ):
        assert secret not in payload
    assert "source_id=source_github" in payload
    assert "job_id=job-123" in payload
    assert "phase=fetching_page_content" in payload


def test_status_payloads_redact_name_only_cookie_header_folded_lines(tmp_path):
    cookie_error = (
        "Set-Cookie\r"
        " source_id=folded-cookie-source-secret; "
        "job_id=folded-cookie-job-secret\r"
        "\tphase=folded-cookie-phase-secret\r"
        "ordinary diagnostic, source_id=source_github; job_id=job-123; "
        "phase=fetching_page_content"
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.FAILED,
            last_error="placeholder",
        )
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE sources SET last_error = ? WHERE source_id = ?",
            (cookie_error, "source_github"),
        )

    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    sources = asyncio.run(mcp.tools["list_sources"]())
    payload = _payload_text(sources)

    for secret in (
        "folded-cookie-source-secret",
        "folded-cookie-job-secret",
        "folded-cookie-phase-secret",
    ):
        assert secret not in payload
    assert "source_id=source_github" in payload
    assert "job_id=job-123" in payload
    assert "phase=fetching_page_content" in payload


def test_status_payloads_redact_lone_cr_cookie_value_continuations_from_legacy_rows(
    tmp_path,
):
    cookie_error = (
        "Cookie: initial-alpha-value\r"
        " folded-alpha-value, folded-delta-value\r"
        "\tfolded-beta-value\r"
        "first clear diagnostic, source_id=source_github; job_id=job-123\r"
        "Set-Cookie: initial-delta-value\r"
        "\tfolded-gamma-value\r"
        "second clear diagnostic, phase=fetching_page_content"
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.FAILED,
            last_error="placeholder",
        )
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE sources SET last_error = ? WHERE source_id = ?",
            (cookie_error, "source_github"),
        )

    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    sources = asyncio.run(mcp.tools["list_sources"]())
    payload = _payload_text(sources)

    for secret in (
        "initial-alpha-value",
        "folded-alpha-value",
        "folded-delta-value",
        "folded-beta-value",
        "initial-delta-value",
        "folded-gamma-value",
    ):
        assert secret not in payload
    assert "source_id=source_github" in payload
    assert "job_id=job-123" in payload
    assert "phase=fetching_page_content" in payload


def test_status_payloads_preserve_lone_cr_clear_diagnostic_from_legacy_rows(
    tmp_path,
):
    cookie_error = (
        "Set-Cookie: initial-alpha-value\r"
        "clear diagnostic source_id=source_github "
        "job_id=job-123 retry_count=24"
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.FAILED,
            last_error="placeholder",
        )
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE sources SET last_error = ? WHERE source_id = ?",
            (cookie_error, "source_github"),
        )

    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    sources = asyncio.run(mcp.tools["list_sources"]())
    payload = _payload_text(sources)

    assert "initial-alpha-value" not in payload
    assert "clear diagnostic" in payload
    assert "source_id=source_github" in payload
    assert "job_id=job-123" in payload
    assert "retry_count=24" in payload


def test_get_sync_status_keeps_direct_storage_failure_sanitized_at_rest(tmp_path):
    raw_error = (
        "provider failed "
        "ntn_abcdefghijklmnopqrstuvwxyz0123456789 "
        "secret_abcdefghijklmnopqrstuvwxyz0123456789 "
        "path:/Users/tester/private vault/meeting notes.md, job_id=job-123; "
        r"file:C:\Users\tester\private vault\meeting notes.md; "
        "source_id=source_notion"
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            auth_ref="env:NOTION_API_KEY",
            sync_status=SyncStatus.IDLE,
        )
    )
    queued = store.create_sync_job("source_notion")
    completed = store.complete_failed_sync(
        job_id=queued.job_id,
        source_id="source_notion",
        error_message=raw_error,
        stale_cleanup_disabled_reason=raw_error,
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    status = asyncio.run(mcp.tools["get_sync_status"]("source_notion"))

    with store._connect() as conn:
        job_row = conn.execute(
            "SELECT status_message, error_message FROM sync_jobs WHERE job_id = ?",
            (queued.job_id,),
        ).fetchone()
        source_row = conn.execute(
            """
            SELECT last_error, stale_cleanup_disabled_reason
            FROM sources WHERE source_id = ?
            """,
            ("source_notion",),
        ).fetchone()
    values = (
        completed.status_message,
        completed.error_message,
        job_row["status_message"],
        job_row["error_message"],
        source_row["last_error"],
        source_row["stale_cleanup_disabled_reason"],
        status["source"]["last_error"],
        status["latest_job"]["error_message"],
    )
    for value in values:
        assert "ntn_" not in value
        assert "secret_" not in value
        assert "/Users/tester/private" not in value
        assert r"C:\Users\tester\private" not in value
        assert "meeting notes.md" not in value
        assert "job_id=job-123" in value
        assert "source_id=source_notion" in value
        assert "<redacted>" in value


def test_source_payload_keeps_only_valid_env_auth_refs(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.IDLE,
        )
    )
    store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            auth_ref="env:NOTION_API_KEY",
            sync_status=SyncStatus.IDLE,
        )
    )
    store.upsert_source(
        SourceModel(
            source_id="source_tistory",
            source_type=SourceType.TISTORY,
            name="Tistory",
            enabled=True,
            auth_ref="env:TISTORY_BLOG_NAME",
            sync_status=SyncStatus.IDLE,
        )
    )
    store.upsert_source(
        SourceModel(
            source_id="source_obsidian",
            source_type=SourceType.OBSIDIAN,
            name="Obsidian",
            enabled=False,
            auth_ref="env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH",
            sync_status=SyncStatus.IDLE,
        )
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE sources SET auth_ref = ? WHERE source_id = ?",
            ("env:ghp_secretcredential", "source_notion"),
        )
        conn.execute(
            "UPDATE sources SET auth_ref = ? WHERE source_id = ?",
            ("env:basic user:super-secret-value", "source_tistory"),
        )
    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    sources = asyncio.run(mcp.tools["list_sources"]())
    auth_refs = {
        source["source_id"]: source["auth_ref"]
        for source in sources["sources"]
    }
    payload = _payload_text(sources)

    assert auth_refs["source_github"] == "env:GITHUB_TOKEN"
    assert auth_refs["source_notion"] == "<redacted>"
    assert auth_refs["source_obsidian"] == "env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH"
    assert auth_refs["source_tistory"] == "<redacted>"
    assert "ghp_secretcredential" not in payload
    assert "super-secret-value" not in payload


def test_status_payload_drops_legacy_noncanonical_phase_and_auth_ref(tmp_path):
    raw_auth_ref = "ntn_abcdefghijklmnopqrstuvwxyz0123456789"
    raw_phase = (
        "fetching /Users/tester/private vault/notes.md "
        "with secret_abcdefghijklmnopqrstuvwxyz0123456789"
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            auth_ref="env:NOTION_API_KEY",
            sync_status=SyncStatus.IDLE,
        )
    )
    job, started = store.begin_sync_job("source_notion")
    with store._connect() as conn:
        conn.execute(
            "UPDATE sources SET auth_ref = ? WHERE source_id = ?",
            (raw_auth_ref, "source_notion"),
        )
        conn.execute(
            "UPDATE sync_jobs SET phase = ? WHERE job_id = ?",
            (raw_phase, job.job_id),
        )
    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    listed = asyncio.run(mcp.tools["list_sources"]())
    status = asyncio.run(mcp.tools["get_sync_status"]("source_notion"))
    payload = _payload_text({"listed": listed, "status": status})

    assert started is True
    assert listed["sources"][0]["auth_ref"] == "<redacted>"
    assert "phase" not in status["latest_job"]
    assert raw_auth_ref not in payload
    assert raw_phase not in payload
    assert "ntn_" not in payload
    assert "secret_" not in payload
    assert "/Users/tester/private" not in payload


def test_disabled_obsidian_source_is_visible_in_public_status_payloads(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_obsidian",
            source_type=SourceType.OBSIDIAN,
            name="Obsidian",
            enabled=False,
            auth_ref="env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH",
            sync_status=SyncStatus.IDLE,
            last_error=OBSIDIAN_DISABLED_ERROR,
        )
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    sources = asyncio.run(mcp.tools["list_sources"]())
    status = asyncio.run(mcp.tools["get_sync_status"]("source_obsidian"))

    assert [source["source_id"] for source in sources["sources"]] == ["source_obsidian"]
    assert sources["sources"][0]["enabled"] is False
    assert sources["sources"][0]["auth_ref"] == "env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH"
    assert sources["sources"][0]["last_error"] == OBSIDIAN_DISABLED_ERROR
    assert status["source"]["source_id"] == "source_obsidian"
    assert status["source"]["enabled"] is False
    assert status["latest_job"] is None


def test_list_sources_and_status_refresh_dynamic_registry_state_without_sync(tmp_path):
    list_store = MetadataStore(tmp_path / "list.sqlite3")
    list_store.upsert_source(_succeeded_obsidian_source())
    list_registry = RefreshingObsidianRegistry()
    list_mcp = FakeMCP()
    register_tools(
        list_mcp,
        metadata_store=list_store,
        source_registry=list_registry,
    )

    assert list_store.get_source("source_obsidian").enabled is True

    list_calls_after_registration = list_registry.calls
    listed = asyncio.run(list_mcp.tools["list_sources"]())

    assert list_registry.calls > list_calls_after_registration
    assert listed["sources"][0]["source_id"] == "source_obsidian"
    assert listed["sources"][0]["enabled"] is False
    assert listed["sources"][0]["sync_status"] == "failed"
    assert listed["sources"][0]["last_synced_at"] == "2026-06-11T00:00:00+00:00"
    assert listed["sources"][0]["latest_success_at"] == "2026-06-11T00:00:00+00:00"
    assert listed["sources"][0]["last_error"] == OBSIDIAN_DISABLED_ERROR
    assert listed["sources"][0]["latest_failure_reason"] == OBSIDIAN_DISABLED_ERROR
    assert listed["sources"][0]["stale_cleanup_disabled_reason"] == OBSIDIAN_DISABLED_ERROR

    status_store = MetadataStore(tmp_path / "status.sqlite3")
    status_store.upsert_source(_succeeded_obsidian_source())
    status_registry = RefreshingObsidianRegistry()
    status_mcp = FakeMCP()
    register_tools(
        status_mcp,
        metadata_store=status_store,
        source_registry=status_registry,
    )

    assert status_store.get_source("source_obsidian").enabled is True

    status_calls_after_registration = status_registry.calls
    status = asyncio.run(status_mcp.tools["get_sync_status"]("source_obsidian"))

    assert status_registry.calls > status_calls_after_registration
    assert status["source"]["source_id"] == "source_obsidian"
    assert status["source"]["enabled"] is False
    assert status["source"]["sync_status"] == "failed"
    assert status["source"]["last_error"] == OBSIDIAN_DISABLED_ERROR
    assert status["latest_job"] is None

    exact_store = MetadataStore(tmp_path / "exact.sqlite3")
    exact_store.upsert_source(_succeeded_obsidian_source())
    exact_job = exact_store.create_sync_job("source_obsidian")
    exact_registry = RefreshingObsidianRegistry()
    exact_mcp = FakeMCP()
    register_tools(
        exact_mcp,
        metadata_store=exact_store,
        source_registry=exact_registry,
    )

    assert exact_store.get_source("source_obsidian").enabled is True

    exact_calls_after_registration = exact_registry.calls
    exact = asyncio.run(
        exact_mcp.tools["get_sync_status"]("source_obsidian", exact_job.job_id)
    )
    listed_overlay = listed["sources"][0]
    latest_overlay = status["source"]

    assert exact_registry.calls > exact_calls_after_registration
    assert exact["job"]["job_id"] == exact_job.job_id
    assert exact["source"]["source_id"] == listed_overlay["source_id"]
    assert exact["source"]["enabled"] is False
    assert exact["source"]["enabled"] == latest_overlay["enabled"]
    assert exact["source"]["sync_status"] == "failed"
    assert exact["source"]["sync_status"] == latest_overlay["sync_status"]
    assert exact["source"]["last_error"] == OBSIDIAN_DISABLED_ERROR
    assert exact["source"]["last_error"] == latest_overlay["last_error"]
    persisted = exact_store.get_source("source_obsidian")
    assert persisted.enabled is True
    assert persisted.last_error != OBSIDIAN_DISABLED_ERROR
    assert persisted.sync_status != SyncStatus.FAILED


def test_fetch_context_hides_tombstoned_documents():
    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=FakeTombstonedMetadataStore(),
    )

    result = asyncio.run(mcp.tools["fetch_context"](document_id="deleted-doc"))

    assert result["document"] is None
    assert result["chunks"] == []


def test_contextwiki_mcp_tools_are_registered():
    mcp = FakeMCP()
    register_tools(
        mcp,
    )

    assert {
        "list_sources",
        "sync_source",
        "sync_all",
        "get_sync_status",
        "search_context",
        "search_documents",
        "list_documents",
        "fetch_context",
    } == set(mcp.tools)


def test_contextwiki_mcp_tools_return_contract_shapes():
    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=FakeMetadataStore(),
        context_search_service=FakeContextSearch(),
        answer_service=FakeAnswerService(),
    )

    status = asyncio.run(mcp.tools["get_sync_status"]())
    search = asyncio.run(mcp.tools["search_context"]("ContextWiki"))
    document_search = asyncio.run(mcp.tools["search_documents"]("ContextWiki"))
    fetched = asyncio.run(mcp.tools["fetch_context"](chunk_id="chunk-1"))
    assert status["sources"][0]["source"]["source_id"] == "source_fake"
    assert "document_count" in status["sources"][0]["source"]
    assert "stale_cleanup_disabled_reason" in status["sources"][0]["source"]
    assert search["results"][0]["chunk_id"] == "chunk-1"
    assert search["results"][0]["preview"] == "ContextWiki evidence"
    assert search["debug"] == {}
    assert "vector_score" not in search["results"][0]
    assert document_search["results"][0]["document_id"] == "doc-1"
    assert document_search["results"][0]["chunk_id"] == "chunk-1"
    assert document_search["results"][0]["matched_context"] == "ContextWiki evidence"
    assert "preview" not in document_search["results"][0]
    assert "vector_score" not in document_search["results"][0]
    assert "metadata_priority" not in document_search["results"][0]
    assert fetched["chunk"]["chunk_id"] == "chunk-1"


def test_search_context_can_include_structured_debug_payload():
    mcp = FakeMCP()
    register_tools(
        mcp,
        context_search_service=FakeContextSearch(),
    )

    search = asyncio.run(mcp.tools["search_context"]("ContextWiki", include_debug=True))

    assert search["results"][0]["chunk_id"] == "chunk-1"
    assert search["debug"]["retrieval_queries"] == ["ContextWiki"]
    assert search["debug"]["initial_top_vector_score"] == 0.2
    assert search["debug"]["final_top_score"] == 0.9


def test_answer_with_citations_is_not_registered_as_public_mcp_tool():
    mcp = FakeMCP()
    register_tools(
        mcp,
        answer_service=CapturingAnswerService(),
    )

    assert "answer_with_citations" not in mcp.tools


def test_public_tools_filter_legacy_removed_source_rows_when_registry_is_available(tmp_path):
    store = MetadataStore(
        tmp_path / "contextwiki.sqlite3",
        running_job_timeout_seconds=0,
        unowned_running_job_grace_seconds=0,
    )
    _insert_legacy_web_source_row(store)
    legacy_job = store.create_sync_job("source_web")
    _mark_legacy_web_job_running(store, legacy_job.job_id)
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            sync_status=SyncStatus.IDLE,
        )
    )
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id="legacy-doc",
            document_id="legacy-doc",
            source_id="source_web",
            title="Legacy Web Doc",
            content="Legacy web content should not be public.",
            url="https://example.com/legacy",
            platform="Web",
            path="/legacy",
        ),
        [
            ChunkModel(
                chunk_id="legacy-chunk",
                document_id="legacy-doc",
                source_id="source_web",
                title="Legacy Web Doc",
                text="Legacy web content should not be public.",
                url="https://example.com/legacy",
                path="/legacy",
                chunk_index=0,
                content_hash="legacy-hash",
            )
        ],
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        context_search_service=FakeContextSearch(),
        answer_service=FakeAnswerService(),
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    sources = asyncio.run(mcp.tools["list_sources"]())
    all_statuses = asyncio.run(mcp.tools["get_sync_status"]())
    legacy_status = asyncio.run(mcp.tools["get_sync_status"]("source_web"))
    fetched_chunk = asyncio.run(mcp.tools["fetch_context"](chunk_id="legacy-chunk"))
    fetched_document = asyncio.run(mcp.tools["fetch_context"](document_id="legacy-doc"))
    filtered_search = asyncio.run(
        mcp.tools["search_context"]("legacy web", filters={"source_id": "source_web"})
    )
    filtered_search_with_debug = asyncio.run(
        mcp.tools["search_context"](
            "legacy web",
            filters={"source_id": "source_web"},
            include_debug=True,
        )
    )
    filtered_document_search = asyncio.run(
        mcp.tools["search_documents"]("legacy web", filters={"source_id": "source_web"})
    )
    assert [source["source_id"] for source in sources["sources"]] == ["source_github"]
    assert [
        status["source"]["source_id"]
        for status in all_statuses["sources"]
    ] == ["source_github"]
    assert legacy_status == {"source": None, "latest_job": None}
    assert _legacy_web_status_rows(store, legacy_job.job_id) == (
        SyncStatus.RUNNING.value,
        "running",
    )
    assert fetched_chunk["chunk"] is None
    assert fetched_document == {"document": None, "chunks": []}
    assert filtered_search["results"] == []
    assert filtered_search["debug"] == {
        "retrieval_queries": [],
        "effective_term_groups": [],
    }
    assert filtered_search_with_debug["debug"] == filtered_search["debug"]
    assert filtered_document_search["results"] == []


def test_search_context_injects_retained_source_filter_when_unfiltered():
    context_search = CapturingContextSearch()
    mcp = FakeMCP()
    register_tools(
        mcp,
        context_search_service=context_search,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    asyncio.run(mcp.tools["search_context"]("What is retained?"))

    assert context_search.calls[0]["filters"] == {
        "source_ids": [
            "source_github",
            "source_notion",
            "source_obsidian",
            "source_tistory",
        ],
    }
    assert context_search.calls[0]["include_debug"] is False


def test_search_documents_sanitizes_mixed_source_filters():
    context_search = CapturingContextSearch()
    mcp = FakeMCP()
    register_tools(
        mcp,
        context_search_service=context_search,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    search = asyncio.run(
        mcp.tools["search_documents"](
            "What does GitHub say?",
            filters={"source_ids": ["source_github", "source_web"], "tag": "docs"},
        )
    )

    assert search["results"][0]["document_id"] == "doc-1"
    assert context_search.calls[0]["filters"] == {
        "source_ids": ["source_github"],
        "tag": "docs",
    }
    assert context_search.calls[0]["tool"] == "search_documents"


def test_search_documents_redacts_query_text_when_service_is_missing():
    mcp = FakeMCP()
    register_tools(mcp)

    search = asyncio.run(
        mcp.tools["search_documents"]("find token=super-secret-value in docs")
    )

    assert search["results"] == []
    assert "super-secret-value" not in search["query"]
    assert "token=[REDACTED]" in search["query"]


def test_search_documents_redacts_query_text_when_no_public_source_matches():
    mcp = FakeMCP()
    register_tools(
        mcp,
        context_search_service=CapturingContextSearch(),
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    search = asyncio.run(
        mcp.tools["search_documents"](
            "find token=super-secret-value in docs",
            filters={"source_id": "source_web"},
        )
    )

    assert search["results"] == []
    assert "super-secret-value" not in search["query"]
    assert "token=[REDACTED]" in search["query"]


def test_search_documents_injects_retained_source_filter_when_unfiltered():
    context_search = CapturingContextSearch()
    mcp = FakeMCP()
    register_tools(
        mcp,
        context_search_service=context_search,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    asyncio.run(mcp.tools["search_documents"]("What is retained?"))

    assert context_search.calls[0]["filters"] == {
        "source_ids": [
            "source_github",
            "source_notion",
            "source_obsidian",
            "source_tistory",
        ],
    }
    assert context_search.calls[0]["tool"] == "search_documents"


def test_search_documents_redacts_query_text_in_normal_public_payload():
    mcp = FakeMCP()
    register_tools(
        mcp,
        context_search_service=FakeContextSearch(),
    )

    search = asyncio.run(
        mcp.tools["search_documents"]("find token=super-secret-value in docs")
    )

    assert search["results"][0]["document_id"] == "doc-1"
    assert "super-secret-value" not in search["query"]
    assert "token=[REDACTED]" in search["query"]


def test_search_context_contract_strips_vector_score_from_dict_results():
    mcp = FakeMCP()
    register_tools(
        mcp,
        context_search_service=FakeDictContextSearch(),
    )

    search = asyncio.run(mcp.tools["search_context"]("ContextWiki"))

    assert "vector_score" not in search["results"][0]


def test_search_documents_contract_rejects_legacy_result_without_matched_context():
    mcp = FakeMCP()
    register_tools(
        mcp,
        context_search_service=FakeDictContextSearch(),
    )

    with pytest.raises(
        ValueError,
        match="missing required field 'matched_context'",
    ) as exc_info:
        asyncio.run(mcp.tools["search_documents"]("ContextWiki"))

    assert "ContextWiki evidence" not in str(exc_info.value)
    assert "Chunk-level text should not leak" not in str(exc_info.value)


def test_search_documents_contract_rejects_non_string_matched_context():
    mcp = FakeMCP()
    register_tools(
        mcp,
        context_search_service=FakeInvalidMatchedContextSearch(),
    )

    with pytest.raises(
        TypeError,
        match="field 'matched_context' must be a string",
    ):
        asyncio.run(mcp.tools["search_documents"]("ContextWiki"))


def test_search_documents_contract_accepts_explicit_empty_matched_context():
    mcp = FakeMCP()
    register_tools(
        mcp,
        context_search_service=FakeEmptyMatchedContextSearch(),
    )

    search = asyncio.run(mcp.tools["search_documents"]("ContextWiki"))

    assert search["results"][0]["matched_context"] == ""
    assert "preview" not in search["results"][0]
    assert "text" not in search["results"][0]
    assert "line_start" not in search["results"][0]
    assert "line_end" not in search["results"][0]
    assert "version_id" not in search["results"][0]
    assert "updated_at" not in search["results"][0]
    assert "vector_score" not in search["results"][0]
    assert "metadata_priority" not in search["results"][0]


@pytest.mark.parametrize(
    ("unsupported_result", "secret"),
    [
        ("raw-string-secret", "raw-string-secret"),
        (["raw-list-secret"], "raw-list-secret"),
        (PreviewOnlySearchResult(), "dto-preview-secret"),
        (NonMappingDumpSearchResult(), "model-dump-secret"),
    ],
)
def test_search_documents_payload_rejects_unsupported_result_types_without_leaking_content(
    unsupported_result,
    secret,
):
    with pytest.raises(
        TypeError,
        match="search_documents result must serialize to a mapping",
    ) as exc_info:
        _search_documents_result_payload(unsupported_result)

    assert secret not in str(exc_info.value)


@pytest.mark.parametrize(
    ("failing_result", "secret"),
    [
        (RaisingModelDumpSearchResult(), "raising-model-dump-secret"),
        (RaisingItemsSearchResult(), "raising-items-secret"),
    ],
)
def test_search_documents_payload_normalizes_serialization_failures_without_leaking_content(
    failing_result,
    secret,
):
    with pytest.raises(
        TypeError,
        match="search_documents result must serialize to a mapping",
    ) as exc_info:
        _search_documents_result_payload(failing_result)

    error = exc_info.value
    assert str(error) == "search_documents result must serialize to a mapping"
    assert secret not in str(error)
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


def test_get_sync_status_returns_source_after_status_recovery():
    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=RecoveringStatusMetadataStore(),
    )

    single = asyncio.run(mcp.tools["get_sync_status"]("source_fake"))
    all_sources = asyncio.run(mcp.tools["get_sync_status"]())

    assert single["source"]["sync_status"] == "failed"
    assert single["latest_job"]["status"] == "failed"
    assert all_sources["sources"][0]["source"]["sync_status"] == "failed"
    assert all_sources["sources"][0]["latest_job"]["status"] == "failed"


def test_get_sync_status_exact_job_triggers_stale_running_job_recovery():
    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=RecoveringExactStatusMetadataStore(),
    )

    exact = asyncio.run(
        mcp.tools["get_sync_status"]("source_fake", "job-stale")
    )

    assert exact["source"]["sync_status"] == "failed"
    assert exact["job"]["job_id"] == "job-stale"
    assert exact["job"]["status"] == "failed"
    assert exact["job"]["error_message"] == (
        "Sync job timed out before status observation"
    )


def test_get_sync_status_exact_job_mode_is_additive_and_never_crosses_source_boundary(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    for source_id in ("source_github", "source_notion", "source_private"):
        store.upsert_source(
            SourceModel(
                source_id=source_id,
                source_type=SourceType.GITHUB,
                name=source_id,
                enabled=True,
                auth_ref="env:GITHUB_TOKEN",
                sync_status=SyncStatus.RUNNING,
            )
        )
    public_job = store.create_sync_job("source_github")
    hidden_job = store.create_sync_job("source_private")
    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    exact = asyncio.run(
        mcp.tools["get_sync_status"]("source_github", public_job.job_id)
    )
    mismatched = asyncio.run(
        mcp.tools["get_sync_status"]("source_notion", public_job.job_id)
    )
    missing = asyncio.run(
        mcp.tools["get_sync_status"]("source_github", "job-does-not-exist")
    )
    hidden = asyncio.run(
        mcp.tools["get_sync_status"]("source_private", hidden_job.job_id)
    )
    missing_source = asyncio.run(
        mcp.tools["get_sync_status"]("", public_job.job_id)
    )
    latest = asyncio.run(mcp.tools["get_sync_status"]("source_github"))
    all_sources = asyncio.run(mcp.tools["get_sync_status"]())

    assert set(exact) == {"source", "job"}
    assert exact["source"]["source_id"] == "source_github"
    assert exact["job"]["job_id"] == public_job.job_id
    assert mismatched == {"source": None, "job": None}
    assert missing == {"source": None, "job": None}
    assert hidden == {"source": None, "job": None}
    assert missing_source == {"source": None, "job": None}
    assert set(latest) == {"source", "latest_job"}
    assert set(all_sources) == {"sources"}


@pytest.mark.parametrize(
    ("arguments", "expected_source_count"),
    [
        ({"source_id": "source_notion"}, 1),
        ({}, 2),
    ],
)
def test_real_fastmcp_get_sync_status_does_not_wait_for_unrelated_writer(
    tmp_path,
    arguments,
    expected_source_count,
):
    db_path = tmp_path / "status-contention.sqlite3"
    store = MetadataStore(db_path, sync_owner_id="status-owner")
    for source_id, source_type in (
        ("source_github", SourceType.GITHUB),
        ("source_notion", SourceType.NOTION),
    ):
        store.upsert_source(
            SourceModel(
                source_id=source_id,
                source_type=source_type,
                name=source_id,
                enabled=True,
            )
        )
    queued_job, enqueued = store.enqueue_sync_job("source_notion")
    assert enqueued is True
    terminal_job, started = store.begin_sync_job("source_github")
    assert started is True
    terminal_job, deleted_chunk_ids = store.complete_successful_sync(
        job_id=terminal_job.job_id,
        source_id="source_github",
        total_documents=0,
        processed_documents=0,
        indexed_chunks=0,
        skipped_documents=0,
        last_seen_at=datetime.now(timezone.utc).isoformat(),
        cleanup_missing_documents=False,
        deleted_at=datetime.now(timezone.utc).isoformat(),
    )
    assert deleted_chunk_ids == []

    mcp = FastMCP("status-contention-contract")
    register_tools(mcp, metadata_store=store)

    def call_status_tool():
        blocks = asyncio.run(mcp.call_tool("get_sync_status", arguments))
        return json.loads(blocks[0].text)

    writer_conn = sqlite3.connect(db_path)
    writer_conn.execute("BEGIN IMMEDIATE")
    writer_conn.execute(
        "UPDATE sources SET updated_at = updated_at WHERE source_id = ?",
        ("source_github",),
    )
    writer_closed = False
    timed_out = False
    started_at = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            status_future = executor.submit(call_status_tool)
            try:
                status = status_future.result(timeout=0.75)
            except FutureTimeoutError:
                timed_out = True
            finally:
                writer_conn.rollback()
                writer_conn.close()
                writer_closed = True
            status_future.result(timeout=5)
    finally:
        if not writer_closed:
            if writer_conn.in_transaction:
                writer_conn.rollback()
            writer_conn.close()

    assert timed_out is False
    assert time.monotonic() - started_at < 1.25
    if arguments:
        assert expected_source_count == 1
        assert status["source"]["source_id"] == "source_notion"
        assert status["source"]["sync_status"] == "running"
        assert status["latest_job"]["job_id"] == queued_job.job_id
        assert status["latest_job"]["status"] == "queued"
    else:
        assert len(status["sources"]) == expected_source_count
        statuses_by_source = {
            item["source"]["source_id"]: item
            for item in status["sources"]
        }
        assert statuses_by_source["source_notion"]["source"]["sync_status"] == "running"
        assert (
            statuses_by_source["source_notion"]["latest_job"]["job_id"]
            == queued_job.job_id
        )
        assert statuses_by_source["source_notion"]["latest_job"]["status"] == "queued"
        assert statuses_by_source["source_github"]["source"]["sync_status"] == "succeeded"
        assert (
            statuses_by_source["source_github"]["latest_job"]["job_id"]
            == terminal_job.job_id
        )
        assert statuses_by_source["source_github"]["latest_job"]["status"] == "succeeded"


def test_get_sync_status_exposes_queued_job_without_running_progress_hints():
    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=QueuedStatusMetadataStore(),
    )

    single = asyncio.run(mcp.tools["get_sync_status"]("source_fake"))

    assert single["source"]["sync_status"] == "running"
    assert single["latest_job"]["job_id"] == "job-queued"
    assert single["latest_job"]["status"] == "queued"
    assert single["latest_job"]["started_at"] == "2026-07-29T00:00:00+00:00"
    assert "phase" not in single["latest_job"]
    assert "last_progress_at" not in single["latest_job"]


def test_get_sync_status_exposes_running_phase_hints(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            auth_ref="env:NOTION_API_KEY",
            sync_status=SyncStatus.RUNNING,
        )
    )
    job, _ = store.begin_sync_job("source_notion")
    store.update_sync_job(
        job.job_id,
        total_documents=265,
        phase="fetching_page_content",
        upstream_total_pages=265,
        upstream_fetched_pages=18,
        last_progress_at="2026-06-15T10:35:53+00:00",
        status_message="Fetching Notion page content 18/265 before indexing begins.",
    )

    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    status = asyncio.run(mcp.tools["get_sync_status"]("source_notion"))
    exact = asyncio.run(
        mcp.tools["get_sync_status"]("source_notion", job.job_id)
    )

    assert status["latest_job"]["status"] == "running"
    assert status["latest_job"]["phase"] == "fetching_page_content"
    assert status["latest_job"]["upstream_total_pages"] == 265
    assert status["latest_job"]["upstream_fetched_pages"] == 18
    assert status["latest_job"]["last_progress_at"] == "2026-06-15T10:35:53+00:00"
    assert (
        status["latest_job"]["status_message"]
        == "Fetching Notion page content 18/265 before indexing begins."
    )
    assert exact["job"]["job_id"] == job.job_id
    assert exact["job"]["status"] == "running"
    assert exact["job"]["phase"] == "fetching_page_content"
    assert exact["job"]["upstream_total_pages"] == 265
    assert exact["job"]["upstream_fetched_pages"] == 18
    assert exact["job"]["last_progress_at"] == "2026-06-15T10:35:53+00:00"
    assert (
        exact["job"]["status_message"]
        == "Fetching Notion page content 18/265 before indexing begins."
    )


def test_get_sync_status_redacts_progress_status_message(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            auth_ref="env:NOTION_API_KEY",
            sync_status=SyncStatus.RUNNING,
        )
    )
    job, _ = store.begin_sync_job("source_notion")
    store.update_sync_job(
        job.job_id,
        phase="fetching_page_content",
        upstream_total_pages=10,
        upstream_fetched_pages=3,
        last_progress_at="2026-06-15T10:35:53+00:00",
        status_message=(
            "fetching /Users/eunhwa/private/vault.md with token supersecretvalue123456"
        ),
    )

    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    status = asyncio.run(mcp.tools["get_sync_status"]("source_notion"))

    assert "/Users/eunhwa/private/vault.md" not in status["latest_job"]["status_message"]
    assert "supersecretvalue123456" not in status["latest_job"]["status_message"]
    assert "token <redacted>" in status["latest_job"]["status_message"]


def test_get_sync_status_hides_progress_hints_for_terminal_jobs(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            auth_ref="env:NOTION_API_KEY",
            sync_status=SyncStatus.SUCCEEDED,
        )
    )
    job, _ = store.begin_sync_job("source_notion")
    store.update_sync_job(
        job.job_id,
        phase="fetching_page_content",
        upstream_total_pages=10,
        upstream_fetched_pages=3,
        last_progress_at="2026-06-15T10:35:53+00:00",
        status_message="Fetching Notion page content 3/10 before indexing begins.",
    )
    store.complete_successful_sync(
        job_id=job.job_id,
        source_id="source_notion",
        total_documents=10,
        processed_documents=10,
        indexed_chunks=10,
        skipped_documents=0,
        last_seen_at="2026-06-15T10:35:53+00:00",
        cleanup_missing_documents=False,
        deleted_at="2026-06-15T10:35:53+00:00",
    )

    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    status = asyncio.run(mcp.tools["get_sync_status"]("source_notion"))

    assert status["latest_job"]["status"] == "succeeded"
    assert "phase" not in status["latest_job"]
    assert "upstream_total_pages" not in status["latest_job"]
    assert "upstream_fetched_pages" not in status["latest_job"]
    assert "last_progress_at" not in status["latest_job"]
    assert "status_message" not in status["latest_job"]


def test_status_payloads_include_richer_source_fields(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_tistory",
            source_type=SourceType.TISTORY,
            name="Tistory",
            enabled=True,
            auth_ref="env:TISTORY_BLOG_NAME",
            sync_status=SyncStatus.SUCCEEDED,
            last_synced_at="2026-06-11T00:00:00+00:00",
        )
    )
    succeeded = store.create_sync_job("source_tistory")
    failed = store.create_sync_job("source_tistory")
    with store._connect() as conn:
        conn.execute(
            """
            UPDATE sync_jobs SET status = ?, finished_at = ?, error_message = ?
            WHERE job_id = ?
            """,
            (
                "succeeded",
                "2026-06-11T00:00:00+00:00",
                "",
                succeeded.job_id,
            ),
        )
        conn.execute(
            """
            UPDATE sync_jobs SET status = ?, finished_at = ?, error_message = ?
            WHERE job_id = ?
            """,
            (
                "failed",
                "2026-06-12T00:00:00+00:00",
                "partial failure with token=secret-value",
                failed.job_id,
            ),
        )
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id="tistory-doc",
            document_id="tistory-doc",
            source_id="source_tistory",
            title="Tistory Doc",
            content="hello",
            url="https://example.com/post",
            platform="Tistory",
            path="post",
        ),
        [
            ChunkModel(
                chunk_id="tistory-doc:chunk:0:hash",
                document_id="tistory-doc",
                source_id="source_tistory",
                title="Tistory Doc",
                text="hello",
                url="https://example.com/post",
                path="post",
                chunk_index=0,
                content_hash="hash",
            )
        ],
    )
    registry = FakeSourceRegistry(RETAINED_SOURCE_IDS)
    registry.connectors["source_tistory"] = Dumpable(
        {},
        source=Dumpable({"source_id": "source_tistory"}, source_id="source_tistory"),
        supports_stale_cleanup=False,
        disabled_reason="",
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=registry,
    )

    listed = asyncio.run(mcp.tools["list_sources"]())
    source = listed["sources"][0]

    assert source["latest_success_at"] == "2026-06-11T00:00:00+00:00"
    assert source["latest_failure_at"] == "2026-06-12T00:00:00+00:00"
    assert source["latest_failure_reason"] == "partial failure with token=<redacted>"
    assert source["document_count"] == 1
    assert source["chunk_count"] == 1
    assert source["stale_cleanup_disabled_reason"] == (
        "Stale cleanup is disabled because this source connector does not guarantee complete snapshots."
    )


def test_status_payload_prefers_persisted_stale_cleanup_reason_for_disabled_github(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=False,
            auth_ref="env:GITHUB_TOKEN",
            sync_status=SyncStatus.FAILED,
            last_error="Source source_github is disabled because no GitHub repositories are configured in CONTEXTWIKI_GITHUB_REPOSITORIES.",
            stale_cleanup_disabled_reason="Source source_github is disabled because no GitHub repositories are configured in CONTEXTWIKI_GITHUB_REPOSITORIES.",
        )
    )
    registry = FakeSourceRegistry(RETAINED_SOURCE_IDS)
    registry.connectors["source_github"] = Dumpable(
        {},
        source=Dumpable({"source_id": "source_github"}, source_id="source_github"),
        supports_stale_cleanup=True,
        disabled_reason="",
        stale_cleanup_disabled_reason="",
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=registry,
    )

    status = asyncio.run(mcp.tools["get_sync_status"]("source_github"))

    assert status["source"]["stale_cleanup_disabled_reason"] == (
        "Source source_github is disabled because no GitHub repositories are configured in CONTEXTWIKI_GITHUB_REPOSITORIES."
    )


def test_status_payload_prefers_persisted_stale_cleanup_reason_after_incomplete_snapshot(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_obsidian",
            source_type=SourceType.OBSIDIAN,
            name="Obsidian",
            enabled=True,
            auth_ref="env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH",
            sync_status=SyncStatus.FAILED,
            last_error="Obsidian vault snapshot was incomplete because one or more notes could not be read.",
            stale_cleanup_disabled_reason="Obsidian vault snapshot was incomplete because one or more notes could not be read.",
        )
    )
    registry = FakeSourceRegistry(RETAINED_SOURCE_IDS)
    registry.connectors["source_obsidian"] = Dumpable(
        {},
        source=Dumpable({"source_id": "source_obsidian"}, source_id="source_obsidian"),
        supports_stale_cleanup=True,
        disabled_reason="",
        stale_cleanup_disabled_reason="",
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        metadata_store=store,
        source_registry=registry,
    )

    listed = asyncio.run(mcp.tools["list_sources"]())

    assert listed["sources"][0]["stale_cleanup_disabled_reason"] == (
        "Obsidian vault snapshot was incomplete because one or more notes could not be read."
    )
