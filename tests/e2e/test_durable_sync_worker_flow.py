import asyncio
import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from llama_index.core import Settings, StorageContext
from llama_index.core.embeddings import MockEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore
from mcp.server.fastmcp import FastMCP

from api.tools import register_tools
from core.models import (
    DocumentModel,
    SourceModel,
    SourceType,
    SyncJobStatus,
    SyncStatus,
)
from environments.config import AppConfig, setup_chroma
from fetching.connectors import GitHubSourceConnector, SourceConnector, SourceRegistry
from indexing.chunker import DocumentChunker
from indexing.indexer import ContentIndexer
from indexing.ingestion_service import IngestionService
from indexing.sync_worker import SyncWorker, _configure_logging
from search.context_service import ContextSearchService
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.e2e

RETAINED_SOURCE_TYPES = {
    "source_notion": SourceType.NOTION,
    "source_tistory": SourceType.TISTORY,
    "source_github": SourceType.GITHUB,
    "source_obsidian": SourceType.OBSIDIAN,
}


class RecordingIndexer:
    def __init__(self):
        self.documents = []

    async def index_documents(self, documents):
        self.documents.extend(documents)

    def delete_documents_by_ids(self, document_ids, source_id=""):
        return None


class RecordingConnector(SourceConnector):
    def __init__(self, source_id: str, source_type: SourceType):
        self.source = SourceModel(
            source_id=source_id,
            source_type=source_type,
            name=source_id,
            enabled=True,
            auth_ref=f"env:FAKE_{source_type.value.upper()}",
            sync_status=SyncStatus.IDLE,
        )
        self.fetch_count = 0

    async def fetch_documents(self):
        self.fetch_count += 1
        return [
            DocumentModel(
                id=f"doc-{self.source.source_id}",
                document_id=f"doc-{self.source.source_id}",
                external_id=f"doc-{self.source.source_id}",
                source_id=self.source.source_id,
                title=f"Durable {self.source.source_type.value} document",
                content=f"Durable worker content for {self.source.source_id}.",
                url=f"https://example.test/{self.source.source_id}",
                canonical_url=f"https://example.test/{self.source.source_id}",
                platform=self.source.source_type.value,
                path=f"{self.source.source_id}.md",
                updated_at="2026-07-29T00:00:00Z",
            )
        ]


class DisabledRecordingConnector(RecordingConnector):
    def __init__(self, source_id: str, source_type: SourceType):
        super().__init__(source_id, source_type)
        self.disabled_reason = f"Source {source_id} is disabled"
        self.source = self.source.model_copy(
            update={
                "enabled": False,
                "last_error": self.disabled_reason,
            }
        )


class BlockingConnector(RecordingConnector):
    def __init__(
        self,
        source_id: str,
        source_type: SourceType,
        *,
        entered: asyncio.Event,
        release: asyncio.Event,
    ):
        super().__init__(source_id, source_type)
        self.entered = entered
        self.release = release

    async def fetch_documents(self):
        self.entered.set()
        await self.release.wait()
        return await super().fetch_documents()


class SensitiveFailingConnector(RecordingConnector):
    def __init__(
        self,
        source_id: str,
        source_type: SourceType,
        *,
        sensitive_unix_path: str,
        sensitive_windows_path: str,
        sensitive_token: str,
        sensitive_unc_path: str = "",
        sensitive_extended_path: str = "",
        sensitive_cookie: str = "",
    ):
        super().__init__(source_id, source_type)
        self.sensitive_unix_path = sensitive_unix_path
        self.sensitive_windows_path = sensitive_windows_path
        self.sensitive_token = sensitive_token
        self.sensitive_unc_path = sensitive_unc_path
        self.sensitive_extended_path = sensitive_extended_path
        self.sensitive_cookie = sensitive_cookie

    async def fetch_documents(self):
        raise RuntimeError(
            f"provider failure path:{self.sensitive_unix_path}, job_id=provider-job; "
            f"file:{self.sensitive_windows_path}; source_id={self.source.source_id} "
            "retry_count=1 "
            f"token={self.sensitive_token}\n"
            f"Cookie: {self.sensitive_cookie}, unknown_cookie=top-secret, "
            "source_id=cookie-source-secret, job_id=cookie-job-secret,\n"
            "\tfolded_cookie=delta, phase=cookie-phase-secret\n"
            f"failed reading {self.sensitive_unc_path}, "
            f"mirror={self.sensitive_extended_path}"
        )


class StaticGitHubSeedConnector(SourceConnector):
    supports_stale_cleanup = False

    def __init__(self):
        self.source = SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            auth_ref="env:FAKE_GITHUB",
            sync_status=SyncStatus.IDLE,
        )

    async def fetch_documents(self):
        return [
            DocumentModel(
                id=document_id,
                document_id=document_id,
                external_id=document_id,
                source_id="source_github",
                title=document_id,
                content=f"historical content for {document_id}",
                url="https://example.test/github-seed",
                canonical_url="https://example.test/github-seed",
                platform="GitHub",
                path=document_id.rsplit(":", 1)[-1],
                updated_at="2026-07-29T00:00:00Z",
            )
            for document_id in (
                "github:eunaverse/populated:old.py",
                "github:eunaverse/empty:old.py",
                "github:eunaverse/historical-private:legacy.py",
            )
        ]


class OwnerScopedGitHubHTTP:
    def __init__(self):
        self.urls = []
        self.content = b"print('current populated')\n"

    async def get_json(self, url, headers=None):
        self.urls.append(url)
        if "/users/eunaverse/repos?" in url:
            return [
                {
                    "name": "populated",
                    "default_branch": "stable",
                    "owner": {"login": "eunaverse"},
                    "size": 1,
                    "pushed_at": "2026-07-29T00:00:00Z",
                },
                {
                    "name": "empty",
                    "default_branch": "main",
                    "owner": {"login": "eunaverse"},
                    "size": 0,
                    "pushed_at": None,
                },
            ]
        if "/repos/eunaverse/empty/commits/" in url:
            raise AssertionError("confirmed empty repository must not request a commit")
        if "/repos/eunaverse/populated/commits/stable" in url:
            return {
                "sha": "a" * 40,
                "commit": {"tree": {"sha": "b" * 40}},
            }
        if f"/repos/eunaverse/populated/git/trees/{'b' * 40}" in url:
            return {
                "tree": [
                    {
                        "path": "current.py",
                        "type": "blob",
                        "sha": "c" * 40,
                        "size": len(self.content),
                    }
                ]
            }
        if f"/repos/eunaverse/populated/git/blobs/{'c' * 40}" in url:
            return {
                "encoding": "base64",
                "content": base64.b64encode(self.content).decode(),
                "size": len(self.content),
            }
        raise AssertionError(f"unexpected GitHub API URL: {url}")


def _registry():
    connectors = {
        source_id: RecordingConnector(source_id, source_type)
        for source_id, source_type in RETAINED_SOURCE_TYPES.items()
    }
    return SourceRegistry(connectors.values()), connectors


async def _call_tool_json(
    mcp: FastMCP,
    name: str,
    arguments: dict | None = None,
) -> dict:
    blocks = await mcp.call_tool(name, arguments or {})
    return json.loads(blocks[0].text)


def test_mcp_registration_and_running_status_never_expose_or_persist_unsafe_lifecycle_metadata(
    tmp_path,
):
    metadata_path = tmp_path / "unsafe-lifecycle.sqlite3"
    raw_auth_ref = "secret_abcdefghijklmnopqrstuvwxyz0123456789"
    raw_phase = (
        "fetching /Users/tester/private vault/notes.md "
        "with ntn_abcdefghijklmnopqrstuvwxyz0123456789"
    )
    connector = RecordingConnector("source_notion", SourceType.NOTION)
    connector.source = connector.source.model_copy(update={"auth_ref": raw_auth_ref})
    registry = SourceRegistry([connector])
    mcp_store = MetadataStore(metadata_path)
    ingestion = IngestionService(
        metadata_store=mcp_store,
        source_registry=registry,
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=RecordingIndexer(),
    )
    mcp = FastMCP("durable-lifecycle-safety-e2e")
    register_tools(
        mcp,
        ingestion_service=ingestion,
        metadata_store=mcp_store,
        source_registry=registry,
    )

    listed = asyncio.run(_call_tool_json(mcp, "list_sources"))
    accepted = asyncio.run(
        _call_tool_json(
            mcp,
            "sync_source",
            {"source_id": "source_notion"},
        )
    )
    worker_store = MetadataStore(metadata_path, sync_owner_id="worker-safe-metadata")
    claimed = worker_store.claim_next_sync_job(["source_notion"])
    assert claimed is not None
    updated = worker_store.update_sync_job(claimed.job_id, phase=raw_phase)
    status = asyncio.run(
        _call_tool_json(
            mcp,
            "get_sync_status",
            {"source_id": "source_notion"},
        )
    )
    with worker_store._connect() as conn:
        source_row = conn.execute(
            "SELECT auth_ref FROM sources WHERE source_id = ?",
            ("source_notion",),
        ).fetchone()
        job_row = conn.execute(
            "SELECT phase FROM sync_jobs WHERE job_id = ?",
            (claimed.job_id,),
        ).fetchone()
    payload = json.dumps(
        {
            "listed": listed,
            "accepted": accepted,
            "status": status,
        }
    )

    assert listed["sources"][0]["auth_ref"] == ""
    assert source_row["auth_ref"] == ""
    assert updated.phase == ""
    assert job_row["phase"] == ""
    assert raw_auth_ref not in payload
    assert raw_phase not in payload
    assert "ntn_" not in payload
    assert "secret_" not in payload
    assert "/Users/tester/private" not in payload
    assert "phase" not in status["latest_job"]


def test_mcp_enqueued_job_failure_does_not_persist_delimiter_paths_in_worker_log(
    monkeypatch,
    tmp_path,
):
    metadata_path = tmp_path / "sensitive-worker.sqlite3"
    log_path = tmp_path / "logs" / "sync-worker.log"
    sensitive_unix_path = "/Users/tester/private,vault;meeting notes.md"
    sensitive_windows_path = r"C:\Users\tester\private,vault;meeting notes.md"
    sensitive_token = (
        "ntn_abcdefghijklmnopqrstuvwxyz0123456789 "
        "secret_abcdefghijklmnopqrstuvwxyz0123456789"
    )
    sensitive_unc_path = r"\\server\private share\meeting notes.md"
    sensitive_extended_path = (
        r"\\?\C:\Users\tester\private vault\meeting notes.md"
    )
    sensitive_cookie = "session=alpha, theme=private, preference=hidden"
    mcp_connector = RecordingConnector("source_notion", SourceType.NOTION)
    mcp_registry = SourceRegistry([mcp_connector])
    mcp_store = MetadataStore(metadata_path)
    mcp_ingestion = IngestionService(
        metadata_store=mcp_store,
        source_registry=mcp_registry,
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=RecordingIndexer(),
    )
    mcp = FastMCP("durable-sensitive-log-e2e")
    register_tools(
        mcp,
        ingestion_service=mcp_ingestion,
        metadata_store=mcp_store,
        source_registry=mcp_registry,
    )
    worker_store = MetadataStore(metadata_path)
    worker_ingestion = IngestionService(
        metadata_store=worker_store,
        source_registry=SourceRegistry(
            [
                SensitiveFailingConnector(
                    "source_notion",
                    SourceType.NOTION,
                    sensitive_unix_path=sensitive_unix_path,
                    sensitive_windows_path=sensitive_windows_path,
                    sensitive_token=sensitive_token,
                    sensitive_unc_path=sensitive_unc_path,
                    sensitive_extended_path=sensitive_extended_path,
                    sensitive_cookie=sensitive_cookie,
                )
            ]
        ),
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=RecordingIndexer(),
        register_source_config=False,
    )
    worker = SyncWorker(
        worker_ingestion,
        worker_store,
        source_ids=("source_notion",),
        poll_interval_seconds=0.1,
    )
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_PATH", str(log_path))
    root_logger = logging.getLogger()
    previous_handlers = list(root_logger.handlers)
    previous_level = root_logger.level

    async def run_flow():
        accepted = await _call_tool_json(
            mcp,
            "sync_source",
            {"source_id": "source_notion"},
        )
        completed = await worker.run_once()
        status = await _call_tool_json(
            mcp,
            "get_sync_status",
            {"source_id": "source_notion"},
        )
        return accepted, completed, status

    try:
        handler = _configure_logging()
        accepted, completed, status = asyncio.run(run_flow())
        handler.flush()
        combined_log = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(log_path.parent.glob("sync-worker.log*"))
        )

        assert accepted["status"] == "queued"
        assert completed is not None
        assert completed.job_id == accepted["job_id"]
        assert completed.status == SyncJobStatus.FAILED
        persisted_job = worker_store.get_sync_job(completed.job_id)
        persisted_source = worker_store.get_source("source_notion")
        assert persisted_job is not None
        assert persisted_source is not None
        public_payload = json.dumps(status, ensure_ascii=False)
        for value in (
            completed.error_message,
            persisted_job.error_message,
            persisted_source.last_error,
            public_payload,
        ):
            assert sensitive_unix_path not in value
            assert sensitive_windows_path not in value
            assert "vault;meeting notes.md" not in value
            assert "notes.md" not in value
            assert sensitive_token not in value
            assert sensitive_unc_path not in value
            assert sensitive_extended_path not in value
            assert sensitive_cookie not in value
            assert "theme=private" not in value
            assert "unknown_cookie=top-secret" not in value
            assert "folded_cookie=delta" not in value
            assert "cookie-source-secret" not in value
            assert "cookie-job-secret" not in value
            assert "cookie-phase-secret" not in value
            assert "ntn_" not in value
            assert "secret_" not in value
            assert "job_id=provider-job" in value
            assert "source_id=source_notion" in value
            assert "retry_count=1" in value
            assert "<redacted" in value
        assert sensitive_unix_path not in combined_log
        assert sensitive_windows_path not in combined_log
        assert "vault;meeting notes.md" not in combined_log
        assert "notes.md" not in combined_log
        assert sensitive_token not in combined_log
        assert sensitive_unc_path not in combined_log
        assert sensitive_extended_path not in combined_log
        assert sensitive_cookie not in combined_log
        assert "theme=private" not in combined_log
        assert "unknown_cookie=top-secret" not in combined_log
        assert "folded_cookie=delta" not in combined_log
        assert "cookie-source-secret" not in combined_log
        assert "cookie-job-secret" not in combined_log
        assert "cookie-phase-secret" not in combined_log
        assert "ntn_" not in combined_log
        assert "secret_" not in combined_log
        assert "job_id=provider-job" in combined_log
        assert "source_id=source_notion" in combined_log
        assert "retry_count=1" in combined_log
        assert "<redacted" in combined_log
    finally:
        for handler in list(root_logger.handlers):
            handler.close()
        root_logger.handlers[:] = previous_handlers
        root_logger.setLevel(previous_level)


def _single_source_mcp(metadata_path):
    connector = RecordingConnector("source_notion", SourceType.NOTION)
    registry = SourceRegistry([connector])
    store = MetadataStore(metadata_path)
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=registry,
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=RecordingIndexer(),
    )
    mcp = FastMCP("durable-wait-e2e")
    register_tools(
        mcp,
        ingestion_service=ingestion,
        metadata_store=store,
        source_registry=registry,
    )
    return mcp, ingestion, store


def test_disabled_mcp_enqueue_cannot_be_claimed_by_stale_enabled_worker(tmp_path):
    metadata_path = tmp_path / "disabled-enqueue-race.sqlite3"
    mcp_connector = DisabledRecordingConnector("source_notion", SourceType.NOTION)
    mcp_registry = SourceRegistry([mcp_connector])
    mcp_store = MetadataStore(metadata_path, sync_owner_id="mcp")
    mcp_ingestion = IngestionService(
        metadata_store=mcp_store,
        source_registry=mcp_registry,
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=RecordingIndexer(),
        durable_dispatch=True,
    )
    mcp = FastMCP("durable-disabled-race-e2e")
    register_tools(
        mcp,
        ingestion_service=mcp_ingestion,
        metadata_store=mcp_store,
        source_registry=mcp_registry,
    )
    stale_enabled_connector = RecordingConnector("source_notion", SourceType.NOTION)
    worker_store = MetadataStore(metadata_path, sync_owner_id="worker")
    worker_ingestion = IngestionService(
        metadata_store=worker_store,
        source_registry=SourceRegistry([stale_enabled_connector]),
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=RecordingIndexer(),
        register_source_config=False,
    )
    worker = SyncWorker(
        worker_ingestion,
        worker_store,
        source_ids=("source_notion",),
        poll_interval_seconds=0.1,
    )
    original_enqueue = mcp_store.enqueue_sync_job
    enqueue_transaction_finished = Event()
    worker_attempt_finished = Event()

    def pause_after_enqueue_transaction(source_id, **kwargs):
        result = original_enqueue(source_id, **kwargs)
        enqueue_transaction_finished.set()
        assert worker_attempt_finished.wait(timeout=5)
        return result

    mcp_store.enqueue_sync_job = pause_after_enqueue_transaction

    def request_sync():
        return asyncio.run(
            _call_tool_json(
                mcp,
                "sync_source",
                {"source_id": "source_notion"},
            )
        )

    with ThreadPoolExecutor(max_workers=1) as executor:
        request_future = executor.submit(request_sync)
        assert enqueue_transaction_finished.wait(timeout=5)
        completed = asyncio.run(worker.run_once())
        worker_attempt_finished.set()
        accepted = request_future.result(timeout=5)

    status = asyncio.run(
        _call_tool_json(
            mcp,
            "get_sync_status",
            {"source_id": "source_notion"},
        )
    )
    assert accepted["status"] == "failed"
    assert completed is None
    assert stale_enabled_connector.fetch_count == 0
    assert status["source"]["sync_status"] == "failed"
    assert status["latest_job"]["status"] == "failed"
    assert status["latest_job"]["job_id"] == accepted["job_id"]


def test_sync_all_without_worker_exposes_exact_job_still_queued(tmp_path):
    metadata_path = tmp_path / "no-worker-exact-status.sqlite3"
    mcp, mcp_ingestion, store = _single_source_mcp(metadata_path)

    async def run_flow():
        result = await _call_tool_json(mcp, "sync_all")
        item = result["results"][0]
        status = await _call_tool_json(
            mcp,
            "get_sync_status",
            {
                "source_id": "source_notion",
                "job_id": item["job"]["job_id"],
            },
        )
        return result, status

    result, status = asyncio.run(run_flow())
    item = result["results"][0]

    assert result["status"] == "accepted"
    assert result["summary"]["started"] == 1
    assert item["launch_outcome"] == "started"
    assert item["job"]["status"] == "queued"
    assert status["source"]["sync_status"] == "running"
    assert status["job"]["status"] == "queued"
    assert status["job"]["job_id"] == item["job"]["job_id"]
    assert store.get_sync_job(item["job"]["job_id"]).status == SyncJobStatus.QUEUED
    assert mcp_ingestion._background_sync_tasks == {}


def test_queued_exact_job_can_be_observed_then_completed_by_separate_worker(tmp_path):
    metadata_path = tmp_path / "resumed-exact-observation.sqlite3"
    mcp, mcp_ingestion, mcp_store = _single_source_mcp(metadata_path)

    async def run_flow():
        launched = await _call_tool_json(mcp, "sync_all")
        launched_item = launched["results"][0]
        job_id = launched_item["job"]["job_id"]
        queued_status = await _call_tool_json(
            mcp,
            "get_sync_status",
            {"source_id": "source_notion", "job_id": job_id},
        )
        accepted_job = mcp_store.get_sync_job(job_id)
        assert accepted_job is not None
        assert accepted_job.status == SyncJobStatus.QUEUED
        assert queued_status["job"]["job_id"] == job_id
        assert queued_status["job"]["status"] == "queued"
        assert mcp_ingestion._background_sync_tasks == {}

        worker_store = MetadataStore(metadata_path)
        worker_ingestion = IngestionService(
            metadata_store=worker_store,
            source_registry=SourceRegistry(
                [RecordingConnector("source_notion", SourceType.NOTION)]
            ),
            chunker=DocumentChunker(max_chars=160, overlap_chars=0),
            indexer=RecordingIndexer(),
            register_source_config=False,
        )
        worker = SyncWorker(
            worker_ingestion,
            worker_store,
            source_ids=("source_notion",),
            poll_interval_seconds=0.1,
        )
        completed = await worker.run_once()
        terminal_status = await _call_tool_json(
            mcp,
            "get_sync_status",
            {"source_id": "source_notion", "job_id": job_id},
        )
        return accepted_job, completed, terminal_status

    accepted_job, completed, terminal_status = asyncio.run(run_flow())

    assert completed is not None
    assert completed.job_id == accepted_job.job_id
    assert completed.status == SyncJobStatus.SUCCEEDED
    assert terminal_status["job"]["job_id"] == accepted_job.job_id
    assert terminal_status["job"]["status"] == "succeeded"
    assert terminal_status["source"]["sync_status"] == "succeeded"


def test_durable_worker_completes_exact_jobs_after_mcp_request_owner_is_cancelled(
    tmp_path,
):
    metadata_path = tmp_path / "shared-contextwiki.sqlite3"
    mcp_registry, _ = _registry()
    mcp_store = MetadataStore(metadata_path)
    mcp_ingestion = IngestionService(
        metadata_store=mcp_store,
        source_registry=mcp_registry,
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=RecordingIndexer(),
    )
    mcp = FastMCP("durable-worker-e2e")
    register_tools(
        mcp,
        ingestion_service=mcp_ingestion,
        metadata_store=mcp_store,
        source_registry=mcp_registry,
    )

    worker_registry, worker_connectors = _registry()
    worker_store = MetadataStore(metadata_path)
    worker_indexer = RecordingIndexer()
    worker_ingestion = IngestionService(
        metadata_store=worker_store,
        source_registry=worker_registry,
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=worker_indexer,
    )
    worker = SyncWorker(
        worker_ingestion,
        worker_store,
        source_ids=tuple(RETAINED_SOURCE_TYPES),
        poll_interval_seconds=0.1,
    )

    async def run_flow():
        accepted = {}
        request_returned = asyncio.Event()
        keep_request_owner_alive = asyncio.Event()

        async def mcp_request_owner():
            accepted.update(await _call_tool_json(mcp, "sync_all"))
            request_returned.set()
            await keep_request_owner_alive.wait()

        request_owner = asyncio.create_task(mcp_request_owner())
        await asyncio.wait_for(request_returned.wait(), timeout=1)

        assert accepted["status"] == "accepted"
        assert accepted["summary"]["started"] == 4
        accepted_jobs = {item["source_id"]: item["job"] for item in accepted["results"]}
        assert set(accepted_jobs) == set(RETAINED_SOURCE_TYPES)
        assert {job["status"] for job in accepted_jobs.values()} == {"queued"}
        assert mcp_ingestion._background_sync_tasks == {}

        duplicate = await _call_tool_json(
            mcp,
            "sync_source",
            {"source_id": "source_notion"},
        )
        assert duplicate["job_id"] == accepted_jobs["source_notion"]["job_id"]
        assert duplicate["status"] == "queued"

        queued_status = await _call_tool_json(
            mcp,
            "get_sync_status",
            {"source_id": "source_notion"},
        )
        assert queued_status["source"]["sync_status"] == "running"
        assert queued_status["latest_job"]["status"] == "queued"

        request_owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_owner

        completed = []
        for _ in RETAINED_SOURCE_TYPES:
            result = await worker.run_once()
            assert result is not None
            completed.append(result)
        assert await worker.run_once() is None

        return accepted_jobs, completed

    accepted_jobs, completed = asyncio.run(run_flow())

    assert {job.source_id for job in completed} == set(RETAINED_SOURCE_TYPES)
    assert {job.status for job in completed} == {SyncJobStatus.SUCCEEDED}
    assert {job.job_id for job in completed} == {
        job["job_id"] for job in accepted_jobs.values()
    }
    for source_id, connector in worker_connectors.items():
        assert connector.fetch_count == 1
        exact_job = worker_store.get_sync_job(accepted_jobs[source_id]["job_id"])
        assert exact_job is not None
        assert exact_job.status == SyncJobStatus.SUCCEEDED
        assert worker_store.get_document(f"doc-{source_id}") is not None
    assert {document.source_id for document in worker_indexer.documents} == set(
        RETAINED_SOURCE_TYPES
    )
    with worker_store._connect() as conn:
        owner_ids = {
            row["owner_id"]
            for row in conn.execute(
                "SELECT owner_id FROM sync_job_owners"
            ).fetchall()
        }
    assert owner_ids == {worker_store.sync_owner_id}


def test_separate_worker_chroma_client_updates_already_running_mcp_search_runtime(
    tmp_path,
):
    previous_embed_model = Settings.embed_model
    Settings.embed_model = MockEmbedding(embed_dim=8)
    try:
        config = AppConfig(
            chroma_db_path=tmp_path / "chroma",
            metadata_db_path=tmp_path / "contextwiki.sqlite3",
            collection_name="durable_worker_visibility",
            search_multiplier=4,
        )
        mcp_collection = setup_chroma(config)
        mcp_storage_context = StorageContext.from_defaults(
            vector_store=ChromaVectorStore(chroma_collection=mcp_collection)
        )
        mcp_indexer = ContentIndexer(
            config,
            mcp_collection,
            mcp_storage_context,
        )
        mcp_store = MetadataStore(config.metadata_db_path)
        mcp_registry = SourceRegistry(
            [RecordingConnector("source_notion", SourceType.NOTION)]
        )
        mcp_ingestion = IngestionService(
            metadata_store=mcp_store,
            source_registry=mcp_registry,
            chunker=DocumentChunker(max_chars=160, overlap_chars=0),
            indexer=mcp_indexer,
            durable_dispatch=True,
        )
        mcp_search = ContextSearchService(
            metadata_store=mcp_store,
            indexer=mcp_indexer,
            config=config,
        )
        mcp = FastMCP("durable-independent-chroma-e2e")
        register_tools(
            mcp,
            ingestion_service=mcp_ingestion,
            context_search_service=mcp_search,
            metadata_store=mcp_store,
            source_registry=mcp_registry,
        )

        worker_collection = setup_chroma(config)
        worker_storage_context = StorageContext.from_defaults(
            vector_store=ChromaVectorStore(chroma_collection=worker_collection)
        )
        worker_indexer = ContentIndexer(
            config,
            worker_collection,
            worker_storage_context,
        )
        worker_store = MetadataStore(config.metadata_db_path)
        worker_ingestion = IngestionService(
            metadata_store=worker_store,
            source_registry=SourceRegistry(
                [RecordingConnector("source_notion", SourceType.NOTION)]
            ),
            chunker=DocumentChunker(max_chars=160, overlap_chars=0),
            indexer=worker_indexer,
            register_source_config=False,
            durable_dispatch=True,
        )
        worker = SyncWorker(
            worker_ingestion,
            worker_store,
            source_ids=("source_notion",),
            poll_interval_seconds=0.1,
        )

        async def run_flow():
            before_sync = await _call_tool_json(
                mcp,
                "search_context",
                {
                    "query": "Durable worker content",
                    "filters": {"source_id": "source_notion"},
                    "top_k": 1,
                },
            )
            accepted = await _call_tool_json(
                mcp,
                "sync_source",
                {"source_id": "source_notion"},
            )
            completed = await worker.run_once()
            status = await _call_tool_json(
                mcp,
                "get_sync_status",
                {"source_id": "source_notion"},
            )
            search_result = await _call_tool_json(
                mcp,
                "search_context",
                {
                    "query": "Durable worker content",
                    "filters": {"source_id": "source_notion"},
                    "top_k": 1,
                },
            )
            chunk_id = search_result["results"][0]["chunk_id"]
            fetched = await _call_tool_json(
                mcp,
                "fetch_context",
                {"chunk_id": chunk_id},
            )
            return (
                before_sync,
                accepted,
                completed,
                status,
                search_result,
                chunk_id,
                fetched,
            )

        (
            before_sync,
            accepted,
            completed,
            status,
            search_result,
            chunk_id,
            fetched,
        ) = asyncio.run(run_flow())

        assert mcp_collection is not worker_collection
        assert before_sync["results"] == []
        assert accepted["status"] == "queued"
        assert completed is not None
        assert completed.job_id == accepted["job_id"]
        assert completed.status == SyncJobStatus.SUCCEEDED
        assert status["latest_job"]["job_id"] == accepted["job_id"]
        assert status["latest_job"]["status"] == "succeeded"
        assert search_result["results"][0]["source_id"] == "source_notion"
        assert fetched["chunk"]["chunk_id"] == chunk_id
        assert fetched["chunk"]["text"] == ("Durable worker content for source_notion.")
    finally:
        Settings.embed_model = previous_embed_model


def test_durable_worker_preserves_owner_discovery_cleanup_scope_across_registries(
    tmp_path,
):
    metadata_path = tmp_path / "owner-wide-durable.sqlite3"
    seed_store = MetadataStore(metadata_path)
    seed_service = IngestionService(
        metadata_store=seed_store,
        source_registry=SourceRegistry([StaticGitHubSeedConnector()]),
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=RecordingIndexer(),
    )
    seed_job = asyncio.run(seed_service.sync_source("source_github"))
    assert seed_job.status == SyncJobStatus.SUCCEEDED

    config = AppConfig(github_max_files=5, github_max_file_bytes=1000)
    mcp_connector = GitHubSourceConnector(
        repositories=("eunaverse",),
        config=config,
        http_client=OwnerScopedGitHubHTTP(),
    )
    mcp_store = MetadataStore(metadata_path)
    mcp_registry = SourceRegistry([mcp_connector])
    mcp_ingestion = IngestionService(
        metadata_store=mcp_store,
        source_registry=mcp_registry,
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=RecordingIndexer(),
        durable_dispatch=True,
    )
    mcp = FastMCP("owner-wide-durable-worker-e2e")
    register_tools(
        mcp,
        ingestion_service=mcp_ingestion,
        metadata_store=mcp_store,
        source_registry=mcp_registry,
    )

    worker_http = OwnerScopedGitHubHTTP()
    worker_connector = GitHubSourceConnector(
        repositories=("eunaverse",),
        config=config,
        http_client=worker_http,
    )
    worker_store = MetadataStore(metadata_path)
    worker_ingestion = IngestionService(
        metadata_store=worker_store,
        source_registry=SourceRegistry([worker_connector]),
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=RecordingIndexer(),
        register_source_config=False,
        durable_dispatch=True,
    )
    worker = SyncWorker(
        worker_ingestion,
        worker_store,
        source_ids=("source_github",),
        poll_interval_seconds=0.1,
    )

    async def run_flow():
        accepted = await _call_tool_json(
            mcp,
            "sync_source",
            {"source_id": "source_github"},
        )
        completed = await worker.run_once()
        status = await _call_tool_json(
            mcp,
            "get_sync_status",
            {"source_id": "source_github"},
        )
        return accepted, completed, status

    accepted, completed, status = asyncio.run(run_flow())

    assert accepted["status"] == "queued"
    assert completed is not None
    assert completed.job_id == accepted["job_id"]
    assert completed.status == SyncJobStatus.SUCCEEDED
    assert status["latest_job"]["job_id"] == accepted["job_id"]
    assert status["latest_job"]["status"] == "succeeded"
    assert worker_connector.cleanup_document_id_prefixes == (
        "github:eunaverse/populated:",
        "github:eunaverse/empty:",
    )
    assert worker_connector.supports_stale_cleanup is True
    assert (
        worker_store.get_document("github:eunaverse/populated:old.py").deleted_at != ""
    )
    assert worker_store.get_document("github:eunaverse/empty:old.py").deleted_at != ""
    assert (
        worker_store.get_document(
            "github:eunaverse/historical-private:legacy.py"
        ).deleted_at
        == ""
    )
    assert (
        worker_store.get_document("github:eunaverse/populated:current.py").deleted_at
        == ""
    )
    assert not any("/repos/eunaverse/empty/commits/" in url for url in worker_http.urls)


def test_running_worker_job_is_reused_and_survives_mcp_request_cancellation(tmp_path):
    metadata_path = tmp_path / "running-reuse.sqlite3"
    mcp_registry, _ = _registry()
    mcp_store = MetadataStore(metadata_path)
    mcp_ingestion = IngestionService(
        metadata_store=mcp_store,
        source_registry=mcp_registry,
        chunker=DocumentChunker(max_chars=160, overlap_chars=0),
        indexer=RecordingIndexer(),
    )
    mcp = FastMCP("durable-running-reuse-e2e")
    register_tools(
        mcp,
        ingestion_service=mcp_ingestion,
        metadata_store=mcp_store,
        source_registry=mcp_registry,
    )

    async def run_flow():
        entered = asyncio.Event()
        release = asyncio.Event()
        worker_connector = BlockingConnector(
            "source_notion",
            SourceType.NOTION,
            entered=entered,
            release=release,
        )
        worker_registry = SourceRegistry([worker_connector])
        worker_store = MetadataStore(metadata_path)
        worker_ingestion = IngestionService(
            metadata_store=worker_store,
            source_registry=worker_registry,
            chunker=DocumentChunker(max_chars=160, overlap_chars=0),
            indexer=RecordingIndexer(),
            register_source_config=False,
        )
        worker = SyncWorker(
            worker_ingestion,
            worker_store,
            source_ids=("source_notion",),
            poll_interval_seconds=0.1,
        )

        observed = {}
        duplicate_returned = asyncio.Event()
        keep_mcp_request_owner_alive = asyncio.Event()

        async def mcp_request_owner():
            observed["accepted"] = await _call_tool_json(
                mcp,
                "sync_source",
                {"source_id": "source_notion"},
            )
            worker_task = asyncio.create_task(worker.run_once())
            observed["worker_task"] = worker_task
            await asyncio.wait_for(entered.wait(), timeout=1)
            observed["duplicate"] = await _call_tool_json(
                mcp,
                "sync_source",
                {"source_id": "source_notion"},
            )
            duplicate_returned.set()
            await keep_mcp_request_owner_alive.wait()

        request_owner = asyncio.create_task(mcp_request_owner())
        await asyncio.wait_for(duplicate_returned.wait(), timeout=1)
        assert mcp_ingestion._background_sync_tasks == {}
        request_owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_owner

        assert not observed["worker_task"].done()
        release.set()
        completed = await asyncio.wait_for(observed["worker_task"], timeout=1)
        return observed["accepted"], observed["duplicate"], completed

    accepted, duplicate, completed = asyncio.run(run_flow())

    assert accepted["status"] == "queued"
    assert duplicate["status"] == "running"
    assert duplicate["job_id"] == accepted["job_id"]
    assert completed.job_id == accepted["job_id"]
    assert completed.status == SyncJobStatus.SUCCEEDED
