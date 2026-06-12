import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from api.tools import register_tools
from core.models import (
    ChunkModel,
    ContextSearchResult,
    DocumentModel,
    SourceModel,
    SourceType,
    SyncStatus,
)
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
    async def sync_source(self, source_id):
        raise ValueError(f"Unknown source: {source_id}")


class FakeLeakyJobIngestion:
    async def sync_source(self, source_id):
        return Dumpable(
            {
                "job_id": "job-leaky",
                "source_id": source_id,
                "status": "failed",
                "error_message": (
                    "Sync failed with token=super-secret-value "
                    "and ghp_secretcredential"
                ),
            }
        )

    async def sync_all(self):
        return {
            "status": "completed",
            "summary": {
                "total_sources": 1,
                "succeeded": 0,
                "failed": 1,
                "blocked": 0,
                "skipped": 0,
                "started_at": "2026-06-12T00:00:00+00:00",
                "finished_at": "2026-06-12T00:00:01+00:00",
            },
            "results": [
                {
                    "source_id": "source_github",
                    "sync_outcome": "failed",
                    "job": Dumpable(
                        {
                            "job_id": "job-leaky",
                            "source_id": "source_github",
                            "status": "failed",
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


class FakePathFailingIngestion:
    async def sync_source(self, source_id):
        raise ValueError(
            "Sync failed at /Users/eunhwa/private/vault.md "
            "with token supersecretvalue123456"
        )

    async def sync_all(self):
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
            "debug": {
                "rewrite_enabled": True,
                "rewrite_attempted": True,
                "rewrite_applied": False,
                "rewrite_skipped_reason": "empty_result",
            },
        }


class FakeDictContextSearch:
    async def search_context(self, query, filters=None, top_k=10):
        return {
            "query": query,
            "results": [
                {
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "source_id": "source_fake",
                    "source_type": "notion",
                    "title": "ContextWiki",
                    "score": 0.9,
                    "vector_score": 0.2,
                    "preview": "ContextWiki evidence",
                    "text": "ContextWiki evidence",
                }
            ],
        }


class FakeAnswerService:
    async def answer_with_citations(self, question, filters=None, top_k=5, include_debug=False):
        return {
            "question": question,
            "answer": "ContextWiki evidence",
            "evidence_status": "grounded",
            "citations": [{"chunk_id": "chunk-1"}],
            "used_chunks": ["chunk-1"],
        }


class CapturingAnswerService(FakeAnswerService):
    def __init__(self):
        self.calls = []

    async def answer_with_citations(self, question, filters=None, top_k=5, include_debug=False):
        self.calls.append(
            {
                "question": question,
                "filters": filters,
                "top_k": top_k,
            }
        )
        return await super().answer_with_citations(question, filters=filters, top_k=top_k)


class CapturingContextSearch(FakeContextSearch):
    def __init__(self):
        self.calls = []

    async def search_context(self, query, filters=None, top_k=10):
        self.calls.append(
            {
                "query": query,
                "filters": filters,
                "top_k": top_k,
            }
        )
        return await super().search_context(query, filters=filters, top_k=top_k)


def test_sync_source_returns_structured_error_for_unknown_source():
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakeFailingIngestion(),
    )

    result = asyncio.run(mcp.tools["sync_source"]("missing"))

    assert result["status"] == "error"
    assert "Unknown source" in result["message"]


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


def test_sync_all_redacts_public_error_paths_and_whitespace_secrets():
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=FakePathFailingIngestion(),
    )

    result = asyncio.run(mcp.tools["sync_all"]())
    payload = _payload_text(result)

    assert result["status"] == "error"
    assert "/Users/eunhwa/private/vault.md" not in payload
    assert "supersecretvalue123456" not in payload
    assert "token <redacted>" in result["message"]


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
    assert result["results"][0]["sync_outcome"] == "failed"
    assert "super-secret-value" not in payload
    assert "ghp_secretcredential" not in payload


def test_status_payloads_redact_persisted_secret_fields(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="basic user:super-secret-value",
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
                "failed reading /Users/eunhwa/private/source.md "
                "with token supersecretvalue123456"
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
                "job failed at ~/private/file.md with api_key anothersecretvalue123456",
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

    assert "/Users/eunhwa/private/source.md" not in payload
    assert "~/private/file.md" not in payload
    assert "supersecretvalue123456" not in payload
    assert "anothersecretvalue123456" not in payload
    assert "token <redacted>" in sources["sources"][0]["last_error"]
    assert "api_key <redacted>" in status["latest_job"]["error_message"]


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
            auth_ref="env:ghp_secretcredential",
            sync_status=SyncStatus.IDLE,
        )
    )
    store.upsert_source(
        SourceModel(
            source_id="source_tistory",
            source_type=SourceType.TISTORY,
            name="Tistory",
            enabled=True,
            auth_ref="env:basic user:super-secret-value",
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
        "fetch_context",
        "answer_with_citations",
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
    fetched = asyncio.run(mcp.tools["fetch_context"](chunk_id="chunk-1"))
    answer = asyncio.run(mcp.tools["answer_with_citations"]("What is ContextWiki?"))

    assert status["sources"][0]["source"]["source_id"] == "source_fake"
    assert "document_count" in status["sources"][0]["source"]
    assert "stale_cleanup_disabled_reason" in status["sources"][0]["source"]
    assert search["results"][0]["chunk_id"] == "chunk-1"
    assert search["debug"]["rewrite_enabled"] is True
    assert "vector_score" not in search["results"][0]
    assert fetched["chunk"]["chunk_id"] == "chunk-1"
    assert answer["evidence_status"] == "grounded"
    assert "debug" not in answer
    assert "debug_markdown" not in answer
    assert "answer_mode" not in answer


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
    filtered_answer = asyncio.run(
        mcp.tools["answer_with_citations"](
            "What did legacy web say?",
            filters={"source_id": "source_web"},
        )
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
    assert filtered_search["debug"]["rewrite_skipped_reason"] == "no_matching_sources"
    assert filtered_answer["evidence_status"] == "insufficient"
    assert filtered_answer["citations"] == []


def test_answer_with_citations_sanitizes_mixed_source_filters():
    answer_service = CapturingAnswerService()
    mcp = FakeMCP()
    register_tools(
        mcp,
        answer_service=answer_service,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    answer = asyncio.run(
        mcp.tools["answer_with_citations"](
            "What does GitHub say?",
            filters={"source_ids": ["source_github", "source_web"], "tag": "docs"},
        )
    )

    assert answer["evidence_status"] == "grounded"
    assert answer_service.calls[0]["filters"] == {
        "source_ids": ["source_github"],
        "tag": "docs",
    }


def test_answer_with_citations_injects_retained_source_filter_when_unfiltered():
    answer_service = CapturingAnswerService()
    mcp = FakeMCP()
    register_tools(
        mcp,
        answer_service=answer_service,
        source_registry=FakeSourceRegistry(RETAINED_SOURCE_IDS),
    )

    answer = asyncio.run(mcp.tools["answer_with_citations"]("What is retained?"))

    assert answer["evidence_status"] == "grounded"
    assert answer_service.calls[0]["filters"] == {
        "source_ids": [
            "source_github",
            "source_notion",
            "source_obsidian",
            "source_tistory",
        ],
    }


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


def test_search_context_contract_strips_vector_score_from_dict_results():
    mcp = FakeMCP()
    register_tools(
        mcp,
        context_search_service=FakeDictContextSearch(),
    )

    search = asyncio.run(mcp.tools["search_context"]("ContextWiki"))

    assert "vector_score" not in search["results"][0]


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
