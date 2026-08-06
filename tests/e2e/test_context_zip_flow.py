import asyncio
import json

import pytest
from mcp.server.fastmcp import FastMCP
from llama_index.core import Settings, StorageContext
from llama_index.core.embeddings import MockEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore

from api.tools import register_tools
from core.models import (
    ChunkModel,
    DocumentModel,
    SourceModel,
    SourceType,
    SyncJobStatus,
    SyncStatus,
)
from environments.config import AppConfig, setup_chroma
from fetching.connectors import SourceConnector, SourceRegistry
from indexing.chunker import DocumentChunker
from indexing.indexer import ContentIndexer
from indexing.ingestion_service import IngestionService
from indexing.sync_worker import SyncWorker
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
                id="doc_context_zip",
                source_id="source_fake_docs",
                title="ContextZip MVP",
                content="ContextZip syncs documents and answers with citations.",
                url="https://example.com/context-zip",
                platform="Notion",
                path="ContextZip MVP",
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
                content="ContextZip unrelated source mentions citations.",
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


class BoolMetadataCollection:
    def __init__(self):
        self.deleted_where = []

    def get(self, include=None):
        return True

    def delete(self, where):
        self.deleted_where.append(where)


class RecordingContentIndexer(ContentIndexer):
    def __init__(self, config, chroma_collection):
        super().__init__(config, chroma_collection, storage_context=None)
        self.indexed_batches = []

    async def _batch_index(self, documents):
        self.indexed_batches.append(list(documents))

    def get_or_create_index(self):
        return object()


class FakeNode:
    def __init__(self, chunk_id, score):
        self.metadata = {"chunk_id": chunk_id, "context_zip_managed": "true"}
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


def _exact_sync_poll_targets(launch_result: dict) -> dict[str, str]:
    return {
        item["source_id"]: item["job"]["job_id"]
        for item in launch_result["results"]
        if item["launch_outcome"] in {"started", "already_running"}
        and item.get("job")
        and item["job"].get("job_id")
    }


async def _wait_for_exact_sync_completion(
    mcp: FastMCP,
    targets: dict[str, str],
    attempts: int = 500,
) -> dict[str, dict]:
    latest_by_source: dict[str, dict] = {}
    for _ in range(attempts):
        for source_id, job_id in targets.items():
            latest_by_source[source_id] = await _call_tool_json_async(
                mcp,
                "get_sync_status",
                {"source_id": source_id, "job_id": job_id},
            )
        if all(
            (latest_by_source[source_id].get("job") or {}).get("status")
            in {"succeeded", "failed"}
            for source_id in targets
        ):
            return latest_by_source
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"Timed out waiting for exact sync completion: {latest_by_source}"
    )


async def _run_next_queued_sync(ingestion: IngestionService):
    claimed = ingestion.metadata_store.claim_next_sync_job()
    assert claimed is not None
    assert claimed.status.value == "running"
    return await ingestion.run_claimed_sync_job(claimed.job_id)


def _build_separate_sync_worker(
    metadata_path,
    connectors,
    *,
    indexer=None,
) -> SyncWorker:
    worker_store = MetadataStore(metadata_path)
    worker_registry = SourceRegistry(connectors)
    worker_ingestion = IngestionService(
        metadata_store=worker_store,
        source_registry=worker_registry,
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer or RecordingIndexer(),
        register_source_config=False,
    )
    return SyncWorker(
        worker_ingestion,
        worker_store,
        source_ids=tuple(
            source.source_id for source in worker_registry.list_sources()
        ),
        poll_interval_seconds=0.1,
    )


pytestmark = pytest.mark.e2e


def test_context_zip_fake_e2e_sync_search_fetch_and_answer(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
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
        await _run_next_queued_sync(ingestion)
        status = await _wait_for_sync_completion(mcp, "source_fake_docs")
        search_result = await mcp.tools["search_context"](
            "citations",
            filters={"source_ids": ["source_fake_docs"]},
            top_k=5,
        )
        collection_search = await mcp.tools["search_context"](
            "ContextZip 관련 문서 모아줘",
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
        answer = await answer_service.answer_with_citations("How does ContextZip answer?")
        collection_answer = await answer_service.answer_with_citations(
            "ContextZip 관련 문서 모아줘",
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

    assert sync_job["status"] == "queued"
    assert status["source"]["sync_status"] == "succeeded"
    assert search_result["results"][0]["title"] == "ContextZip MVP"
    assert collection_search["debug"]["intent"]["name"] == "list"
    assert document_search["results"][0]["matched_context"] == (
        "ContextZip syncs documents and answers with citations."
    )
    assert "preview" not in document_search["results"][0]
    assert "vector_score" not in document_search["results"][0]
    assert "metadata_priority" not in document_search["results"][0]
    assert fetched["chunk"]["text"] == "ContextZip syncs documents and answers with citations."
    assert answer["evidence_status"] == "grounded"
    assert answer["citations"][0]["chunk_id"] == chunk_id
    assert collection_answer["evidence_status"] == "grounded"
    assert "## Grounded List" in collection_answer["answer"]
    assert unsupported["evidence_status"] == "insufficient"


def test_context_zip_fake_e2e_sync_survives_bool_existing_chroma_metadata(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    config = AppConfig(batch_size=10)
    indexer = RecordingContentIndexer(config, BoolMetadataCollection())
    registry = SourceRegistry([FakeConnector()])
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=registry,
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )
    mcp = FakeMCP()
    register_tools(
        mcp,
        ingestion_service=ingestion,
        metadata_store=store,
        source_registry=registry,
    )

    async def run_flow():
        launched = await mcp.tools["sync_source"]("source_fake_docs")
        finished = await _run_next_queued_sync(ingestion)
        status = await mcp.tools["get_sync_status"]("source_fake_docs")
        return launched, finished, status

    launched, finished, status = asyncio.run(run_flow())

    assert launched["status"] == "queued"
    assert finished.status == SyncJobStatus.SUCCEEDED
    assert status["latest_job"]["status"] == "succeeded"
    assert len(indexer.indexed_batches) == 1


def test_context_zip_fastmcp_sync_all_queues_for_worker_then_exact_polling_reaches_terminal(
    tmp_path,
):
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
        store = MetadataStore(tmp_path / "sync-all-context-zip.sqlite3")
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
            sync_all_result = await asyncio.wait_for(
                _call_tool_json_async(mcp, "sync_all"),
                timeout=0.5,
            )

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
                ("source_e2e_first", "started", "queued"),
                ("source_e2e_second", "started", "queued"),
            }
            assert not first_entered.is_set()
            assert not second_entered.is_set()
            assert not first_finished.is_set()
            assert not second_finished.is_set()

            first_queued = await _call_tool_json_async(
                mcp,
                "get_sync_status",
                {"source_id": "source_e2e_first"},
            )
            second_queued = await _call_tool_json_async(
                mcp,
                "get_sync_status",
                {"source_id": "source_e2e_second"},
            )
            assert first_queued["latest_job"]["status"] == "queued"
            assert second_queued["latest_job"]["status"] == "queued"

            first_worker_task = asyncio.create_task(_run_next_queued_sync(ingestion))
            await asyncio.wait_for(first_entered.wait(), timeout=1)
            assert not second_entered.is_set()
            first_release.set()
            await first_worker_task

            second_worker_task = asyncio.create_task(_run_next_queued_sync(ingestion))
            await asyncio.wait_for(second_entered.wait(), timeout=1)
            second_release.set()
            await second_worker_task

            exact_targets = _exact_sync_poll_targets(sync_all_result)
            terminal_by_source = await _wait_for_exact_sync_completion(
                mcp,
                exact_targets,
            )
            return exact_targets, terminal_by_source, first_finished, second_finished
        finally:
            first_release.set()
            second_release.set()

    exact_targets, terminal_by_source, first_finished, second_finished = asyncio.run(
        run_flow()
    )
    terminal_jobs = {
        source_id: payload["job"]
        for source_id, payload in terminal_by_source.items()
    }

    assert first_finished.is_set()
    assert second_finished.is_set()
    assert {
        source_id: job["status"]
        for source_id, job in terminal_jobs.items()
    } == {
        "source_e2e_first": "succeeded",
        "source_e2e_second": "succeeded",
    }
    assert {
        source_id: job["job_id"]
        for source_id, job in terminal_jobs.items()
    } == exact_targets


def test_context_zip_fastmcp_sync_all_polling_returns_exact_terminal_jobs(tmp_path):
    async def run_flow():
        metadata_path = tmp_path / "sync-all-poll-success.sqlite3"
        registry = SourceRegistry([FakeConnector(), OtherSourceConnector()])
        store = MetadataStore(metadata_path)
        ingestion = IngestionService(
            metadata_store=store,
            source_registry=registry,
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=RecordingIndexer(),
        )
        mcp = FastMCP("sync-all-poll-success-e2e")
        register_tools(
            mcp,
            ingestion_service=ingestion,
            metadata_store=store,
            source_registry=registry,
        )
        worker = _build_separate_sync_worker(
            metadata_path,
            [FakeConnector(), OtherSourceConnector()],
        )
        worker_stop = asyncio.Event()
        worker_task = asyncio.create_task(worker.run(worker_stop))

        try:
            launched = await _call_tool_json_async(mcp, "sync_all")
            targets = _exact_sync_poll_targets(launched)
            terminal_by_source = await _wait_for_exact_sync_completion(
                mcp,
                targets,
            )
            return launched, targets, terminal_by_source
        finally:
            worker_stop.set()
            await worker_task

    launched, targets, terminal_by_source = asyncio.run(run_flow())

    assert launched["status"] == "accepted"
    assert launched["summary"]["total_sources"] == 2
    assert launched["summary"]["started"] == 2
    assert {
        (
            item["source_id"],
            item["launch_outcome"],
            item["job"]["status"],
        )
        for item in launched["results"]
    } == {
        ("source_fake_docs", "started", "queued"),
        ("source_other", "started", "queued"),
    }
    for item in launched["results"]:
        exact_job = terminal_by_source[item["source_id"]]["job"]
        assert item["job"]["job_id"] == exact_job["job_id"]
        assert exact_job["status"] == "succeeded"
    assert targets == {
        item["source_id"]: item["job"]["job_id"]
        for item in launched["results"]
    }


def test_context_zip_fastmcp_exact_job_polling_survives_latest_job_supersession(
    tmp_path,
):
    class SupersedingConnector(SourceConnector):
        def __init__(
            self,
            entered: list[asyncio.Event],
            release: list[asyncio.Event],
        ):
            self.source = SourceModel(
                source_id="source_superseded",
                source_type=SourceType.NOTION,
                name="Superseded Source",
                enabled=True,
                auth_ref="env:FAKE",
                sync_status=SyncStatus.IDLE,
            )
            self.entered = entered
            self.release = release
            self.run_index = 0

        async def fetch_documents(self):
            run_index = self.run_index
            self.run_index += 1
            self.entered[run_index].set()
            await self.release[run_index].wait()
            return []

    async def run_flow():
        entered = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        metadata_path = tmp_path / "exact-job-supersession.sqlite3"
        registry = SourceRegistry([SupersedingConnector(entered, release)])
        store = MetadataStore(metadata_path)
        ingestion = IngestionService(
            metadata_store=store,
            source_registry=registry,
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=RecordingIndexer(),
        )
        mcp = FastMCP("exact-job-supersession-e2e")
        register_tools(
            mcp,
            ingestion_service=ingestion,
            metadata_store=store,
            source_registry=registry,
        )
        worker = _build_separate_sync_worker(
            metadata_path,
            [SupersedingConnector(entered, release)],
        )
        source_id = "source_superseded"
        worker_task_b = None

        try:
            launch_a = await _call_tool_json_async(mcp, "sync_all")
            job_a = launch_a["results"][0]["job"]
            worker_task_a = asyncio.create_task(worker.run_once())
            await asyncio.wait_for(entered[0].wait(), timeout=1)
            release[0].set()
            await asyncio.wait_for(worker_task_a, timeout=1)

            launch_b = await _call_tool_json_async(mcp, "sync_all")
            job_b = launch_b["results"][0]["job"]
            worker_task_b = asyncio.create_task(worker.run_once())
            await asyncio.wait_for(entered[1].wait(), timeout=1)

            exact_a = await _call_tool_json_async(
                mcp,
                "get_sync_status",
                {"source_id": source_id, "job_id": job_a["job_id"]},
            )
            latest = await _call_tool_json_async(
                mcp,
                "get_sync_status",
                {"source_id": source_id},
            )
            return job_a, job_b, exact_a, latest
        finally:
            for signal in release:
                signal.set()
            if worker_task_b is not None and not worker_task_b.done():
                await asyncio.gather(worker_task_b, return_exceptions=True)

    job_a, job_b, exact_a, latest = asyncio.run(run_flow())

    assert job_a["job_id"] != job_b["job_id"]
    assert exact_a["source"]["source_id"] == "source_superseded"
    assert exact_a["job"]["job_id"] == job_a["job_id"]
    assert exact_a["job"]["status"] == "succeeded"
    assert latest["latest_job"]["job_id"] == job_b["job_id"]
    assert latest["latest_job"]["status"] == "running"


def test_context_zip_fastmcp_exact_job_status_recovers_stale_real_sqlite_job(
    tmp_path,
):
    store = MetadataStore(
        tmp_path / "exact-job-stale-recovery.sqlite3",
        running_job_timeout_seconds=0,
        unowned_running_job_grace_seconds=0,
    )
    store.upsert_source(
        SourceModel(
            source_id="source_stale_exact",
            source_type=SourceType.NOTION,
            name="Stale Exact Source",
            enabled=True,
            auth_ref="env:FAKE",
            sync_status=SyncStatus.IDLE,
        )
    )
    stale_job, started = store.begin_sync_job("source_stale_exact")
    assert started is True
    assert stale_job.status.value == "running"

    mcp = FastMCP("exact-job-stale-recovery-e2e")
    register_tools(mcp, metadata_store=store)

    exact = _call_tool_json(
        mcp,
        "get_sync_status",
        {
            "source_id": "source_stale_exact",
            "job_id": stale_job.job_id,
        },
    )

    assert exact["source"]["sync_status"] == "failed"
    assert exact["job"]["job_id"] == stale_job.job_id
    assert exact["job"]["status"] == "failed"
    assert exact["job"]["error_message"] == (
        "Sync job timed out before status read completed"
    )
    assert store.get_sync_job(stale_job.job_id).status.value == "failed"


def test_context_zip_fastmcp_sync_all_reuses_job_then_exact_polling_observes_it(
    tmp_path,
):
    class ReusableBlockingConnector(SourceConnector):
        def __init__(self, entered: asyncio.Event, release: asyncio.Event):
            self.source = SourceModel(
                source_id="source_poll_reused",
                source_type=SourceType.NOTION,
                name="Reusable Poll Source",
                enabled=True,
                auth_ref="env:FAKE",
                sync_status=SyncStatus.IDLE,
            )
            self.entered = entered
            self.release = release

        async def fetch_documents(self):
            self.entered.set()
            await self.release.wait()
            return []

    async def run_flow():
        entered = asyncio.Event()
        release = asyncio.Event()
        metadata_path = tmp_path / "sync-all-poll-reuse.sqlite3"
        mcp_connector = ReusableBlockingConnector(asyncio.Event(), asyncio.Event())
        registry = SourceRegistry([mcp_connector])
        store = MetadataStore(metadata_path)
        ingestion = IngestionService(
            metadata_store=store,
            source_registry=registry,
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=RecordingIndexer(),
        )
        mcp = FastMCP("sync-all-poll-reuse-e2e")
        register_tools(
            mcp,
            ingestion_service=ingestion,
            metadata_store=store,
            source_registry=registry,
        )
        worker = _build_separate_sync_worker(
            metadata_path,
            [ReusableBlockingConnector(entered, release)],
        )

        try:
            launched = await _call_tool_json_async(
                mcp,
                "sync_source",
                {"source_id": "source_poll_reused"},
            )
            worker_task = asyncio.create_task(worker.run_once())
            await asyncio.wait_for(entered.wait(), timeout=1)
            bulk_launch = await _call_tool_json_async(mcp, "sync_all")
            release.set()
            worker_result = await asyncio.wait_for(worker_task, timeout=1)
            assert worker_result is not None
            assert worker_result.job_id == launched["job_id"]
            terminal_by_source = await _wait_for_exact_sync_completion(
                mcp,
                _exact_sync_poll_targets(bulk_launch),
            )
            return launched, bulk_launch, terminal_by_source
        finally:
            release.set()

    launched, bulk_launch, terminal_by_source = asyncio.run(run_flow())
    bulk_item = bulk_launch["results"][0]
    terminal_item = terminal_by_source["source_poll_reused"]

    assert bulk_launch["status"] == "accepted"
    assert bulk_item["launch_outcome"] == "already_running"
    assert bulk_item["job"]["status"] == "running"
    assert bulk_item["job"]["job_id"] == launched["job_id"]
    assert terminal_item["job"]["status"] == "succeeded"
    assert terminal_item["job"]["job_id"] == launched["job_id"]
    assert launched["status"] == "queued"


def test_context_zip_sync_all_polling_keeps_running_job_after_source_is_disabled(
    tmp_path,
):
    class DisabledAfterLaunchConnector(SourceConnector):
        def __init__(self, entered: asyncio.Event, release: asyncio.Event):
            self.source = SourceModel(
                source_id="source_disabled_after_launch",
                source_type=SourceType.NOTION,
                name="Disabled After Launch",
                enabled=True,
                auth_ref="env:FAKE",
                sync_status=SyncStatus.IDLE,
            )
            self.entered = entered
            self.release = release

        async def fetch_documents(self):
            self.entered.set()
            await self.release.wait()
            return []

    async def run_flow():
        entered = asyncio.Event()
        release = asyncio.Event()
        metadata_path = tmp_path / "sync-all-poll-disabled-running.sqlite3"
        mcp_connector = DisabledAfterLaunchConnector(
            asyncio.Event(),
            asyncio.Event(),
        )
        worker_connector = DisabledAfterLaunchConnector(entered, release)
        registry = SourceRegistry([mcp_connector])
        store = MetadataStore(metadata_path)
        ingestion = IngestionService(
            metadata_store=store,
            source_registry=registry,
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=RecordingIndexer(),
        )
        mcp = FastMCP("sync-all-poll-disabled-running-e2e")
        register_tools(
            mcp,
            ingestion_service=ingestion,
            metadata_store=store,
            source_registry=registry,
        )
        worker = _build_separate_sync_worker(
            metadata_path,
            [worker_connector],
        )

        try:
            launched = await _call_tool_json_async(
                mcp,
                "sync_source",
                {"source_id": mcp_connector.source.source_id},
            )
            worker_task = asyncio.create_task(worker.run_once())
            await asyncio.wait_for(entered.wait(), timeout=1)
            mcp_connector.source = mcp_connector.source.model_copy(
                update={"enabled": False}
            )
            worker_connector.source = worker_connector.source.model_copy(
                update={"enabled": False}
            )

            bulk_launch = await _call_tool_json_async(mcp, "sync_all")

            release.set()
            worker_result = await asyncio.wait_for(worker_task, timeout=1)
            assert worker_result is not None
            assert worker_result.job_id == launched["job_id"]
            terminal_by_source = await _wait_for_exact_sync_completion(
                mcp,
                _exact_sync_poll_targets(bulk_launch),
            )
            return launched, bulk_launch, terminal_by_source
        finally:
            release.set()

    launched, bulk_launch, terminal_by_source = asyncio.run(run_flow())
    bulk_item = bulk_launch["results"][0]
    terminal_item = terminal_by_source["source_disabled_after_launch"]

    assert bulk_launch["status"] == "accepted"
    assert bulk_launch["summary"]["already_running"] == 1
    assert bulk_launch["summary"]["skipped"] == 0
    assert bulk_item["launch_outcome"] == "already_running"
    assert bulk_item["job"]["status"] == "running"
    assert bulk_item["job"]["job_id"] == launched["job_id"]
    assert terminal_item["job"]["status"] == "succeeded"
    assert terminal_item["job"]["job_id"] == launched["job_id"]
    assert launched["status"] == "queued"


def test_context_zip_fastmcp_sync_all_polling_reports_failure_and_disabled_skip(tmp_path):
    class FailingE2EConnector(SourceConnector):
        source = SourceModel(
            source_id="source_poll_failing",
            source_type=SourceType.GITHUB,
            name="Failing Source",
            enabled=True,
            auth_ref="env:FAKE",
            sync_status=SyncStatus.IDLE,
        )

        async def fetch_documents(self):
            raise RuntimeError("token=do-not-expose")

    class DisabledE2EConnector(SourceConnector):
        source = SourceModel(
            source_id="source_poll_disabled",
            source_type=SourceType.OBSIDIAN,
            name="Disabled Source",
            enabled=False,
            auth_ref="env:FAKE",
            sync_status=SyncStatus.IDLE,
        )
        disabled_reason = "credential=do-not-expose"

        async def fetch_documents(self):
            raise AssertionError("disabled source must not be fetched")

    async def run_flow():
        metadata_path = tmp_path / "sync-all-poll-mixed.sqlite3"
        registry = SourceRegistry(
            [FakeConnector(), FailingE2EConnector(), DisabledE2EConnector()]
        )
        store = MetadataStore(metadata_path)
        ingestion = IngestionService(
            metadata_store=store,
            source_registry=registry,
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=RecordingIndexer(),
        )
        mcp = FastMCP("sync-all-poll-mixed-e2e")
        register_tools(
            mcp,
            ingestion_service=ingestion,
            metadata_store=store,
            source_registry=registry,
        )
        worker = _build_separate_sync_worker(
            metadata_path,
            [FakeConnector(), FailingE2EConnector(), DisabledE2EConnector()],
        )
        worker_stop = asyncio.Event()
        worker_task = asyncio.create_task(worker.run(worker_stop))

        try:
            launched = await _call_tool_json_async(mcp, "sync_all")
            targets = _exact_sync_poll_targets(launched)
            terminal_by_source = await _wait_for_exact_sync_completion(
                mcp,
                targets,
            )
            disabled_status = await _call_tool_json_async(
                mcp,
                "get_sync_status",
                {"source_id": "source_poll_disabled"},
            )
            return launched, targets, terminal_by_source, disabled_status
        finally:
            worker_stop.set()
            await worker_task

    launched, targets, terminal_by_source, disabled_status = asyncio.run(run_flow())
    launch_outcomes = {
        item["source_id"]: item["launch_outcome"]
        for item in launched["results"]
    }
    terminal_jobs = {
        source_id: payload["job"]
        for source_id, payload in terminal_by_source.items()
    }

    assert launched["status"] == "accepted"
    assert launched["summary"]["started"] == 2
    assert launched["summary"]["skipped"] == 1
    assert launch_outcomes == {
        "source_fake_docs": "started",
        "source_poll_failing": "started",
        "source_poll_disabled": "skipped",
    }
    assert set(targets) == {"source_fake_docs", "source_poll_failing"}
    assert {
        source_id: job["status"]
        for source_id, job in terminal_jobs.items()
    } == {
        "source_fake_docs": "succeeded",
        "source_poll_failing": "failed",
    }
    assert disabled_status["latest_job"]["status"] == "failed"
    serialized_result = json.dumps(
        {
            "launched": launched,
            "terminal": terminal_by_source,
            "disabled": disabled_status,
        }
    )
    assert "do-not-expose" not in serialized_result
    assert "<redacted>" in serialized_result


def test_context_zip_fastmcp_short_status_request_does_not_cancel_background_job(tmp_path):
    class BlockingPollingConnector(SourceConnector):
        def __init__(self, entered: asyncio.Event, release: asyncio.Event):
            self.source = SourceModel(
                source_id="source_poll_blocking",
                source_type=SourceType.NOTION,
                name="Blocking Poll Source",
                enabled=True,
                auth_ref="env:FAKE",
                sync_status=SyncStatus.IDLE,
            )
            self.entered = entered
            self.release = release

        async def fetch_documents(self):
            self.entered.set()
            await self.release.wait()
            return [
                DocumentModel(
                    id="doc_poll_blocking",
                    source_id=self.source.source_id,
                    title="Poll completion",
                    content="The background sync survives separate status requests.",
                    url="https://example.com/poll",
                    platform="Notion",
                    path="poll.md",
                    updated_at="2026-07-29T00:00:00Z",
                )
            ]

    async def run_flow():
        entered = asyncio.Event()
        release = asyncio.Event()
        metadata_path = tmp_path / "sync-all-short-poll.sqlite3"
        mcp_connector = BlockingPollingConnector(asyncio.Event(), asyncio.Event())
        registry = SourceRegistry([mcp_connector])
        store = MetadataStore(metadata_path)
        ingestion = IngestionService(
            metadata_store=store,
            source_registry=registry,
            chunker=DocumentChunker(max_chars=120, overlap_chars=0),
            indexer=RecordingIndexer(),
        )
        mcp = FastMCP("sync-all-short-poll-e2e")
        register_tools(
            mcp,
            ingestion_service=ingestion,
            metadata_store=store,
            source_registry=registry,
        )
        worker = _build_separate_sync_worker(
            metadata_path,
            [BlockingPollingConnector(entered, release)],
        )
        worker_stop = asyncio.Event()
        worker_task = asyncio.create_task(worker.run(worker_stop))

        try:
            launched = await _call_tool_json_async(mcp, "sync_all")
            await asyncio.wait_for(entered.wait(), timeout=1)
            running = await asyncio.wait_for(
                _call_tool_json_async(
                    mcp,
                    "get_sync_status",
                    {"source_id": ""},
                ),
                timeout=0.5,
            )
            running_item = running["sources"][0]

            assert launched["status"] == "accepted"
            assert running_item["latest_job"]["status"] == "running"
            assert launched["results"][0]["job"]["job_id"] == (
                running_item["latest_job"]["job_id"]
            )
            assert not worker_task.cancelled()
            assert not worker_task.done()
            assert ingestion._background_sync_tasks == {}

            release.set()
            terminal_by_source = await _wait_for_exact_sync_completion(
                mcp,
                _exact_sync_poll_targets(launched),
            )
            return launched, terminal_by_source
        finally:
            release.set()
            worker_stop.set()
            await worker_task

    launched, terminal_by_source = asyncio.run(run_flow())
    terminal_item = terminal_by_source["source_poll_blocking"]

    assert launched["results"][0]["job"]["status"] == "queued"
    assert terminal_item["job"]["status"] == "succeeded"
    assert terminal_item["source"]["sync_status"] == "succeeded"


def test_context_search_applies_source_filter_before_result_limit(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
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
            "ContextZip citations",
            filters={"source_ids": ["source_fake_docs"]},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].source_id == "source_fake_docs"


def test_context_zip_fastmcp_e2e_date_filter_sort_and_list_pagination(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    registry = SourceRegistry([FakeConnector()])
    documents = []
    for document_id, published_at in [
        ("dated-old", "2026-07-01T00:00:00Z"),
        ("dated-new", "2026-07-03T00:00:00Z"),
    ]:
        document = DocumentModel(
            id=document_id,
            source_id="source_fake_docs",
            title=document_id,
            content="ContextZip dated evidence",
            url=f"https://example.com/{document_id}",
            platform="Notion",
            published_at=published_at,
            modified_at=published_at,
            indexed_at=published_at,
            date_provenance="test",
        )
        chunk = ChunkModel(
            chunk_id=f"{document_id}:chunk:0",
            document_id=document_id,
            source_id="source_fake_docs",
            title=document_id,
            text="ContextZip dated evidence",
            url=document.url,
            chunk_index=0,
            content_hash=document_id,
        )
        store.upsert_document_and_replace_chunks(document, [chunk])
        documents.append(chunk.to_document_model(platform="Notion"))

    mcp = FastMCP("date-filter-e2e")
    register_tools(
        mcp,
        context_search_service=ContextSearchService(store, retriever=documents),
        metadata_store=store,
        source_registry=registry,
    )

    filtered = _call_tool_json(
        mcp,
        "search_context",
        {
            "query": "ContextZip",
            "filters": {
                "source_ids": ["source_fake_docs"],
                "published_from": "2026-07-03T00:00:00Z",
                "published_to": "2026-07-03T00:00:00Z",
            },
            "top_k": 2,
        },
    )
    sorted_documents = _call_tool_json(
        mcp,
        "search_documents",
        {
            "query": "ContextZip",
            "filters": {"source_ids": ["source_fake_docs"]},
            "sort_by": "published_at",
            "sort_order": "desc",
            "top_k": 2,
        },
    )
    first_page = _call_tool_json(
        mcp,
        "list_documents",
        {
            "filters": {"source_ids": ["source_fake_docs"]},
            "sort_by": "published_at",
            "sort_order": "desc",
            "page_size": 1,
        },
    )
    second_page = _call_tool_json(
        mcp,
        "list_documents",
        {
            "filters": {"source_ids": ["source_fake_docs"]},
            "sort_by": "published_at",
            "sort_order": "desc",
            "page_size": 1,
            "cursor": first_page["next_cursor"],
        },
    )

    assert [row["document_id"] for row in filtered["results"]] == ["dated-new"]
    assert [row["document_id"] for row in sorted_documents["results"]] == [
        "dated-new",
        "dated-old",
    ]
    assert [row["document_id"] for row in first_page["documents"]] == ["dated-new"]
    assert [row["document_id"] for row in second_page["documents"]] == ["dated-old"]
    assert second_page["next_cursor"] is None


def test_context_zip_fastmcp_e2e_search_documents_preserves_date_microseconds(
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    registry = SourceRegistry([FakeConnector()])
    documents = []
    for document_id, published_at, search_term in [
        (
            "a-asc-newer",
            "9999-12-31T23:59:59.999999Z",
            "AscendingPrecision",
        ),
        (
            "z-asc-older",
            "9999-12-31T23:59:59.999998Z",
            "AscendingPrecision",
        ),
        (
            "a-desc-older",
            "9999-12-31T23:59:59.999998Z",
            "DescendingPrecision",
        ),
        (
            "z-desc-newer",
            "9999-12-31T23:59:59.999999Z",
            "DescendingPrecision",
        ),
    ]:
        document = DocumentModel(
            id=document_id,
            source_id="source_fake_docs",
            title=document_id,
            content=f"{search_term} ContextZip evidence",
            url=f"https://example.com/{document_id}",
            platform="Notion",
            published_at=published_at,
            modified_at=published_at,
            indexed_at=published_at,
            date_provenance="test",
        )
        chunk = ChunkModel(
            chunk_id=f"{document_id}:chunk:0",
            document_id=document_id,
            source_id="source_fake_docs",
            title=document_id,
            text=document.content,
            url=document.url,
            chunk_index=0,
            content_hash=document_id,
        )
        store.upsert_document_and_replace_chunks(document, [chunk])
        documents.append(chunk.to_document_model(platform="Notion"))

    mcp = FastMCP("date-precision-e2e")
    register_tools(
        mcp,
        context_search_service=ContextSearchService(store, retriever=documents),
        metadata_store=store,
        source_registry=registry,
    )

    ascending = _call_tool_json(
        mcp,
        "search_documents",
        {
            "query": "AscendingPrecision",
            "filters": {"source_ids": ["source_fake_docs"]},
            "sort_by": "published_at",
            "sort_order": "asc",
            "top_k": 2,
        },
    )
    descending = _call_tool_json(
        mcp,
        "search_documents",
        {
            "query": "DescendingPrecision",
            "filters": {"source_ids": ["source_fake_docs"]},
            "sort_by": "published_at",
            "sort_order": "desc",
            "top_k": 2,
        },
    )

    assert [row["document_id"] for row in ascending["results"]] == [
        "z-asc-older",
        "a-asc-newer",
    ]
    assert [row["document_id"] for row in descending["results"]] == [
        "z-desc-newer",
        "a-desc-older",
    ]


def test_context_zip_temp_chroma_e2e_sync_search_fetch_and_answer(tmp_path):
    previous_embed_model = Settings.embed_model
    Settings.embed_model = MockEmbedding(embed_dim=8)
    try:
        config = AppConfig(
            chroma_db_path=tmp_path / "chroma",
            metadata_db_path=tmp_path / "context_zip.sqlite3",
            collection_name="context_zip_e2e",
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
                        content="ContextZip citations from an unmanaged legacy document.",
                        url="https://example.com/legacy",
                        platform="Legacy",
                    )
                ]
            )
        )
        async def run_flow():
            other_job = await mcp.tools["sync_source"]("source_other")
            target_job = await mcp.tools["sync_source"]("source_fake_docs")
            await _run_next_queued_sync(ingestion)
            await _run_next_queued_sync(ingestion)
            await _wait_for_sync_completion(mcp, "source_other")
            status = await _wait_for_sync_completion(mcp, "source_fake_docs")
            search_result = await mcp.tools["search_context"](
                "ContextZip citations",
                filters={"source_id": "source_fake_docs"},
                top_k=1,
            )
            chunk_id = search_result["results"][0]["chunk_id"]
            fetched = await mcp.tools["fetch_context"](chunk_id=chunk_id)
            answer = await answer_service.answer_with_citations(
                "How does ContextZip answer?",
                filters={"source_id": "source_fake_docs"},
                top_k=1,
            )
            return other_job, target_job, status, search_result, chunk_id, fetched, answer

        other_job, target_job, status, search_result, chunk_id, fetched, answer = asyncio.run(
            run_flow()
        )
        metadatas = chroma_collection.get(include=["metadatas"])["metadatas"]
        target_vector = chroma_collection.get(
            where={"chunk_id": chunk_id},
            include=["metadatas"],
        )
        legacy_metadata = {
            key: value
            for key, value in target_vector["metadatas"][0].items()
            if key != "context_zip_managed"
        }
        legacy_metadata["context" + "wiki_managed"] = "true"
        chroma_collection.update(
            ids=[target_vector["ids"][0]],
            metadatas=[legacy_metadata],
        )
        legacy_search_result = asyncio.run(
            mcp.tools["search_context"](
                "ContextZip citations",
                filters={"source_id": "source_fake_docs"},
                top_k=1,
            )
        )

        assert other_job["status"] == "queued"
        assert target_job["status"] == "queued"
        assert status["source"]["sync_status"] == "succeeded"
        assert chroma_collection.count() >= 3
        assert any(metadata.get("context_zip_managed") == "false" for metadata in metadatas)
        assert any(metadata.get("context_zip_managed") == "true" for metadata in metadatas)
        assert search_result["results"][0]["source_id"] == "source_fake_docs"
        assert legacy_search_result["results"][0]["chunk_id"] == chunk_id
        assert fetched["chunk"]["text"] == "ContextZip syncs documents and answers with citations."
        assert answer["evidence_status"] == "grounded"
        assert answer["used_chunks"] == [chunk_id]
    finally:
        Settings.embed_model = previous_embed_model


def test_context_zip_e2e_phase1_alias_expansion_recovers_aws_document(tmp_path):
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

    store = MetadataStore(tmp_path / "context_zip.sqlite3")
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
        await _run_next_queued_sync(ingestion)
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

    assert sync_job["status"] == "queued"
    assert len(search_result["results"]) == 1
    assert search_result["results"][0]["title"] == "Cloud deployment checklist"
    assert search_result["results"][0]["chunk_id"] == chunk_id
    assert any("amazon web services" in query.lower() for query in retrieval_queries)


def test_context_zip_e2e_uses_only_deterministic_query_variants(tmp_path):
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

    store = MetadataStore(tmp_path / "context_zip.sqlite3")
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
        await _run_next_queued_sync(ingestion)
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

    assert sync_job["status"] == "queued"
    assert result["results"] == []
    assert "aws virtual machine startup" in retrieval_queries
    assert "aws ec2 setup" not in retrieval_queries


def test_context_zip_e2e_phase3_repository_lookup_prefers_docs_before_code(tmp_path):
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

    store = MetadataStore(tmp_path / "context_zip.sqlite3")
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
        await _run_next_queued_sync(ingestion)
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

    assert sync_job["status"] == "queued"
    assert code_document_count == 64
    assert retrieved_queries
    assert returned_candidate_ids[0] == docs_chunk_id
    assert returned_candidate_ids[1:9] == code_chunk_ids[:8]
    assert set(returned_candidate_ids) == {docs_chunk_id, *code_chunk_ids[:8]}
    assert len(search_result["results"]) >= 2
    assert search_result["results"][0]["path"] == "docs/usage.md"
    assert search_result["results"][0]["title"] == "eunhwa99/ImageGallery docs/usage.md"
    assert any(result["path"].endswith(".java") for result in search_result["results"][1:])
