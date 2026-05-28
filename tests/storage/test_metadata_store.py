from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest

from core.models import (
    ChunkModel,
    DocumentModel,
    SourceModel,
    SourceType,
    SyncJobStatus,
    SyncStatus,
)
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.integration


def _mark_job_running(
    store: MetadataStore,
    job_id: str,
    *,
    started_at: str | None = None,
    heartbeat_at: str = "",
):
    started_at = started_at or datetime.now(timezone.utc).isoformat()
    with store._connect() as conn:
        conn.execute(
            """
            UPDATE sync_jobs SET
                status = ?,
                started_at = ?,
                heartbeat_at = ?
            WHERE job_id = ?
            """,
            (SyncJobStatus.RUNNING.value, started_at, heartbeat_at, job_id),
        )
    return store.get_sync_job(job_id)


def test_metadata_store_tracks_sources_jobs_documents_and_chunks(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.ensure_schema()

    source = store.upsert_source(
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
    assert started is True
    store.complete_successful_sync(
        job_id=job.job_id,
        source_id="source_notion",
        total_documents=1,
        processed_documents=1,
        indexed_chunks=1,
        skipped_documents=0,
        last_seen_at="",
        cleanup_missing_documents=False,
        deleted_at="",
    )

    document = DocumentModel(
        id="notion_page_1",
        source_id="source_notion",
        title="Architecture Note",
        content="ContextWiki indexes knowledge with citations.",
        url="https://notion.so/page-1",
        platform="Notion",
        path="Architecture Note",
        updated_at="2026-05-20T00:00:00Z",
    )
    store.upsert_document(document)

    chunk = ChunkModel(
        chunk_id="notion_page_1:chunk:0:abc123",
        document_id="notion_page_1",
        source_id="source_notion",
        title="Architecture Note",
        text="ContextWiki indexes knowledge with citations.",
        url="https://notion.so/page-1",
        path="Architecture Note",
        chunk_index=0,
        content_hash="abc123",
        updated_at="2026-05-20T00:00:00Z",
        version_id="page-version-1",
    )
    store.replace_document_chunks("notion_page_1", [chunk])

    assert store.list_sources()[0].source_id == source.source_id
    assert store.get_latest_sync_job("source_notion").status == SyncJobStatus.SUCCEEDED
    assert store.get_document("notion_page_1").title == "Architecture Note"
    assert store.get_chunk(chunk.chunk_id).document_id == "notion_page_1"
    assert store.get_chunk(chunk.chunk_id).version_id == "page-version-1"
    assert store.list_chunks_for_document("notion_page_1") == [chunk]


def test_atomic_document_chunk_commit_rolls_back_when_chunk_insert_fails(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.ensure_schema()
    document = DocumentModel(
        id="doc_atomic",
        source_id="source_notion",
        title="Atomic",
        content="Atomic metadata transaction",
        url="https://notion.so/atomic",
        platform="Notion",
    )
    duplicate_chunk = ChunkModel(
        chunk_id="duplicate",
        document_id="doc_atomic",
        source_id="source_notion",
        title="Atomic",
        text="chunk",
        chunk_index=0,
        content_hash="hash",
    )

    with pytest.raises(Exception):
        store.upsert_document_and_replace_chunks(document, [duplicate_chunk, duplicate_chunk])

    assert store.get_document("doc_atomic") is None
    assert store.list_chunks_for_document("doc_atomic") == []


def test_begin_sync_job_allows_one_running_job_across_connections(tmp_path):
    db_path = tmp_path / "contextwiki.sqlite3"
    store = MetadataStore(db_path)
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    worker_count = 16
    barrier = Barrier(worker_count)

    def begin_from_new_connection():
        local_store = MetadataStore(db_path)
        barrier.wait()
        job, started = local_store.begin_sync_job("source_github")
        return job.job_id, started

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(lambda _: begin_from_new_connection(), range(worker_count)))

    started_results = [result for result in results if result[1]]
    job_ids = {job_id for job_id, _ in results}
    with store._connect() as conn:
        running_count = conn.execute(
            "SELECT COUNT(*) AS count FROM sync_jobs WHERE source_id = ? AND status = ?",
            ("source_github", SyncJobStatus.RUNNING.value),
        ).fetchone()["count"]

    assert len(started_results) == 1
    assert len(job_ids) == 1
    assert running_count == 1


def test_begin_sync_job_uses_running_job_even_when_source_status_is_stale(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    queued = store.create_sync_job("source_github")
    running = _mark_job_running(store, queued.job_id)

    returned, started = store.begin_sync_job("source_github")

    with store._connect() as conn:
        running_count = conn.execute(
            "SELECT COUNT(*) AS count FROM sync_jobs WHERE source_id = ? AND status = ?",
            ("source_github", SyncJobStatus.RUNNING.value),
        ).fetchone()["count"]

    assert started is False
    assert returned.job_id == running.job_id
    assert running_count == 1
    assert store.get_source("source_github").sync_status == SyncStatus.RUNNING


def test_begin_sync_job_recovers_stale_running_job(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3", running_job_timeout_seconds=60)
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    stale = store.create_sync_job("source_github")
    _mark_job_running(
        store,
        stale.job_id,
        started_at="2000-01-01T00:00:00+00:00",
    )

    recovered, started = store.begin_sync_job("source_github")

    with store._connect() as conn:
        running_count = conn.execute(
            "SELECT COUNT(*) AS count FROM sync_jobs WHERE source_id = ? AND status = ?",
            ("source_github", SyncJobStatus.RUNNING.value),
        ).fetchone()["count"]

    assert started is True
    assert recovered.job_id != stale.job_id
    assert running_count == 1
    assert store.get_sync_job(stale.job_id).status == SyncJobStatus.FAILED
    assert "timed out" in store.get_sync_job(stale.job_id).error_message
    assert store.get_source("source_github").sync_status == SyncStatus.RUNNING


def test_begin_sync_job_recovers_all_stale_running_jobs(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3", running_job_timeout_seconds=60)
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    first = store.create_sync_job("source_github")
    second = store.create_sync_job("source_github")
    _mark_job_running(
        store,
        first.job_id,
        started_at="2000-01-01T00:00:01+00:00",
    )
    _mark_job_running(
        store,
        second.job_id,
        started_at="2000-01-01T00:00:02+00:00",
    )

    recovered, started = store.begin_sync_job("source_github")

    with store._connect() as conn:
        running_count = conn.execute(
            "SELECT COUNT(*) AS count FROM sync_jobs WHERE source_id = ? AND status = ?",
            ("source_github", SyncJobStatus.RUNNING.value),
        ).fetchone()["count"]

    assert started is True
    assert recovered.job_id not in {first.job_id, second.job_id}
    assert running_count == 1
    assert store.get_sync_job(first.job_id).status == SyncJobStatus.FAILED
    assert store.get_sync_job(second.job_id).status == SyncJobStatus.FAILED


def test_begin_sync_job_returns_active_running_job_after_failing_stale_duplicate(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3", running_job_timeout_seconds=60)
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    active = store.create_sync_job("source_github")
    stale = store.create_sync_job("source_github")
    _mark_job_running(
        store,
        active.job_id,
        started_at="2000-01-01T00:00:01+00:00",
        heartbeat_at="2999-01-01T00:00:00+00:00",
    )
    _mark_job_running(
        store,
        stale.job_id,
        started_at="2000-01-01T00:00:02+00:00",
    )

    returned, started = store.begin_sync_job("source_github")

    with store._connect() as conn:
        running_count = conn.execute(
            "SELECT COUNT(*) AS count FROM sync_jobs WHERE source_id = ? AND status = ?",
            ("source_github", SyncJobStatus.RUNNING.value),
        ).fetchone()["count"]

    assert started is False
    assert returned.job_id == active.job_id
    assert running_count == 1
    assert store.get_sync_job(stale.job_id).status == SyncJobStatus.FAILED
    assert store.get_source("source_github").sync_status == SyncStatus.RUNNING


def test_latest_sync_job_prefers_active_running_job_over_later_failed_duplicate(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3", running_job_timeout_seconds=60)
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    active = store.create_sync_job("source_github")
    stale = store.create_sync_job("source_github")
    _mark_job_running(
        store,
        active.job_id,
        started_at="2026-05-22T00:00:01+00:00",
        heartbeat_at="2999-01-01T00:00:00+00:00",
    )
    _mark_job_running(
        store,
        stale.job_id,
        started_at="2026-05-22T00:00:02+00:00",
    )

    returned, started = store.begin_sync_job("source_github")
    latest = store.get_latest_sync_job("source_github")

    assert started is False
    assert returned.job_id == active.job_id
    assert store.get_sync_job(stale.job_id).status == SyncJobStatus.FAILED
    assert latest.job_id == active.job_id
    assert latest.status == SyncJobStatus.RUNNING


def test_latest_sync_job_recovers_stale_running_job_without_new_sync(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3", running_job_timeout_seconds=60)
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    stale = store.create_sync_job("source_github")
    _mark_job_running(
        store,
        stale.job_id,
        started_at="2000-01-01T00:00:00+00:00",
    )

    latest = store.get_latest_sync_job("source_github")

    assert latest.job_id == stale.job_id
    assert latest.status == SyncJobStatus.FAILED
    assert "status read" in latest.error_message
    assert store.get_source("source_github").sync_status == SyncStatus.FAILED


def test_update_sync_job_cannot_start_running_job_without_guard(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    job = store.create_sync_job("source_github")

    with pytest.raises(ValueError, match="begin_sync_job"):
        store.update_sync_job(job.job_id, status=SyncJobStatus.RUNNING)

    assert store.get_sync_job(job.job_id).status == SyncJobStatus.QUEUED


def test_update_sync_job_cannot_finish_job_without_guarded_completion(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    job, started = store.begin_sync_job("source_github")
    assert started is True

    with pytest.raises(ValueError, match="complete_successful_sync"):
        store.update_sync_job(job.job_id, status=SyncJobStatus.SUCCEEDED)

    assert store.get_sync_job(job.job_id).status == SyncJobStatus.RUNNING
    assert store.get_source("source_github").sync_status == SyncStatus.RUNNING


def test_running_job_commit_rejects_cross_source_document(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    job, started = store.begin_sync_job("source_github")
    assert started is True
    document = DocumentModel(
        id="wrong-source-doc",
        source_id="source_other",
        title="Wrong Source",
        content="wrong source",
        url="https://example.com/wrong",
        platform="GitHub",
    )
    chunk = ChunkModel(
        chunk_id="wrong-source-doc:chunk:0:hash",
        document_id="wrong-source-doc",
        source_id="source_other",
        title="Wrong Source",
        text="wrong source",
        chunk_index=0,
        content_hash="hash",
    )

    with pytest.raises(ValueError, match="belongs to source_github"):
        store.upsert_document_and_replace_chunks_for_running_job(job.job_id, document, [chunk])

    assert store.get_document("wrong-source-doc") is None


def test_document_upsert_rejects_cross_source_identity_collision(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    first = DocumentModel(
        id="shared-id",
        source_id="source_a",
        title="Source A",
        content="source a content",
        url="https://example.com/a",
        platform="GitHub",
    )
    second = DocumentModel(
        id="shared-id",
        source_id="source_b",
        title="Source B",
        content="source b content",
        url="https://example.com/b",
        platform="GitHub",
    )

    store.upsert_document_and_replace_chunks(
        first,
        [
            ChunkModel(
                chunk_id="shared-id:chunk:0:a",
                document_id="shared-id",
                source_id="source_a",
                title="Source A",
                text="source a content",
                chunk_index=0,
                content_hash="a",
            )
        ],
    )

    with pytest.raises(ValueError, match="already belongs to source_a"):
        store.upsert_document_and_replace_chunks(second, [])

    assert store.get_document("shared-id").source_id == "source_a"
    assert store.list_chunks_for_document("shared-id")[0].source_id == "source_a"


def test_replace_document_chunks_rejects_source_mismatched_chunks(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_document(
        DocumentModel(
            id="shared-id",
            source_id="source_a",
            title="Source A",
            content="source a content",
            url="https://example.com/a",
            platform="GitHub",
        )
    )
    wrong_chunk = ChunkModel(
        chunk_id="shared-id:chunk:0:b",
        document_id="shared-id",
        source_id="source_b",
        title="Wrong Source",
        text="wrong source content",
        chunk_index=0,
        content_hash="b",
    )

    with pytest.raises(ValueError, match="not source_a"):
        store.replace_document_chunks("shared-id", [wrong_chunk])

    assert store.get_chunk("shared-id:chunk:0:b") is None


def test_upsert_document_and_replace_chunks_rejects_document_mismatched_chunk(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    document = DocumentModel(
        id="doc-a",
        source_id="source_a",
        title="Source A",
        content="source a content",
        url="https://example.com/a",
        platform="GitHub",
    )
    wrong_chunk = ChunkModel(
        chunk_id="doc-b:chunk:0:a",
        document_id="doc-b",
        source_id="source_a",
        title="Wrong Document",
        text="wrong document content",
        chunk_index=0,
        content_hash="a",
    )

    with pytest.raises(ValueError, match="belongs to document doc-b"):
        store.upsert_document_and_replace_chunks(document, [wrong_chunk])

    assert store.get_document("doc-a") is None
    assert store.get_chunk("doc-b:chunk:0:a") is None


def test_replace_document_chunks_rejects_missing_document(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    chunk = ChunkModel(
        chunk_id="missing-doc:chunk:0:a",
        document_id="missing-doc",
        source_id="source_a",
        title="Missing Document",
        text="orphan content",
        chunk_index=0,
        content_hash="a",
    )

    with pytest.raises(ValueError, match="Unknown document: missing-doc"):
        store.replace_document_chunks("missing-doc", [chunk])

    assert store.get_chunk("missing-doc:chunk:0:a") is None


def test_superseded_running_job_cannot_commit_metadata(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3", running_job_timeout_seconds=60)
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    older = store.create_sync_job("source_github")
    newer = store.create_sync_job("source_github")
    _mark_job_running(
        store,
        older.job_id,
        started_at="2026-05-22T00:00:01+00:00",
        heartbeat_at="2999-01-01T00:00:00+00:00",
    )
    _mark_job_running(
        store,
        newer.job_id,
        started_at="2026-05-22T00:00:02+00:00",
        heartbeat_at="2999-01-01T00:00:00+00:00",
    )
    document = DocumentModel(
        id="superseded-doc",
        source_id="source_github",
        title="Superseded",
        content="superseded content",
        url="https://example.com/superseded",
        platform="GitHub",
    )
    chunk = ChunkModel(
        chunk_id="superseded-doc:chunk:0:hash",
        document_id="superseded-doc",
        source_id="source_github",
        title="Superseded",
        text="superseded content",
        chunk_index=0,
        content_hash="hash",
    )

    _, current_job = store.upsert_document_and_replace_chunks_for_running_job(
        older.job_id,
        document,
        [chunk],
    )

    assert current_job.status == SyncJobStatus.FAILED
    assert store.get_sync_job(newer.job_id).status == SyncJobStatus.RUNNING
    assert store.get_document("superseded-doc") is None


def test_register_source_does_not_overwrite_running_status_from_stale_read(tmp_path):
    class StaleReadStore(MetadataStore):
        stale_reads = False

        def get_source(self, source_id):
            if self.stale_reads:
                return SourceModel(
                    source_id=source_id,
                    source_type=SourceType.GITHUB,
                    name="GitHub",
                    enabled=True,
                    sync_status=SyncStatus.IDLE,
                    last_synced_at="stale",
                    last_error="stale",
                )
            return super().get_source(source_id)

    store = StaleReadStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    running_job, started = store.begin_sync_job("source_github")
    assert started is True

    store.stale_reads = True
    registered = store.register_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub Renamed",
            enabled=True,
            sync_status=SyncStatus.IDLE,
            last_synced_at="stale",
            last_error="stale",
        )
    )

    persisted = MetadataStore.get_source(store, "source_github")
    assert registered.sync_status == SyncStatus.RUNNING
    assert persisted.sync_status == SyncStatus.RUNNING
    assert persisted.last_error == ""
    assert store.get_sync_job(running_job.job_id).status == SyncJobStatus.RUNNING


def test_metadata_store_persists_identity_lifecycle_fields(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    document = DocumentModel(
        id="blob-sha-1",
        external_id="eunhwa99/MCPContentSearch:api/tools.py",
        source_id="source_github",
        title="api/tools.py",
        content="def sync_source():\n    pass\n",
        url="https://github.com/eunhwa99/MCPContentSearch/blob/main/api/tools.py",
        canonical_url="https://github.com/eunhwa99/MCPContentSearch/blob/main/api/tools.py",
        platform="GitHub",
        path="api/tools.py",
        updated_at="2026-05-22T00:00:00Z",
        last_seen_at="2026-05-22T00:00:01Z",
        last_seen_sync_id="job-1",
        version_id="blob-sha-1",
    )

    store.upsert_document(document)

    persisted = store.get_document("eunhwa99/MCPContentSearch:api/tools.py")
    assert persisted is not None
    assert store.get_document("blob-sha-1") is None
    assert persisted.external_id == "eunhwa99/MCPContentSearch:api/tools.py"
    assert persisted.canonical_url == "https://github.com/eunhwa99/MCPContentSearch/blob/main/api/tools.py"
    assert persisted.last_seen_at == "2026-05-22T00:00:01Z"
    assert persisted.last_seen_sync_id == "job-1"
    assert persisted.deleted_at == ""
    assert persisted.version_id == "blob-sha-1"


def test_ensure_schema_adds_lifecycle_columns_to_legacy_documents_table(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.db_path.parent.mkdir(parents=True, exist_ok=True)
    with store._connect() as conn:
        conn.executescript(
            """
            CREATE TABLE documents (
                document_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                url TEXT NOT NULL,
                platform TEXT NOT NULL,
                date TEXT NOT NULL,
                path TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                content_hash TEXT NOT NULL
            );
            INSERT INTO documents (
                document_id, source_id, title, content, url, platform,
                date, path, updated_at, content_hash
            ) VALUES (
                'legacy-doc', 'source_legacy', 'Legacy', 'legacy content',
                'https://example.com/legacy', 'Legacy', '', 'Legacy',
                '2026-05-20T00:00:00Z', 'hash'
            );
            """
        )

    store.ensure_schema()

    with store._connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
        row = conn.execute(
            """
            SELECT document_id, external_id, canonical_url, last_seen_at,
                last_seen_sync_id, deleted_at, version_id
            FROM documents
            """
        ).fetchone()

    assert {
        "external_id",
        "canonical_url",
        "last_seen_at",
        "last_seen_sync_id",
        "deleted_at",
        "version_id",
    }.issubset(columns)
    assert row["document_id"] == "legacy-doc"
    assert row["external_id"] == ""
    assert row["canonical_url"] == ""
    assert row["last_seen_at"] == ""
    assert row["last_seen_sync_id"] == ""
    assert row["deleted_at"] == ""
    assert row["version_id"] == ""


def test_ensure_schema_adds_version_id_to_legacy_chunks_table(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.db_path.parent.mkdir(parents=True, exist_ok=True)
    with store._connect() as conn:
        conn.executescript(
            """
            CREATE TABLE documents (
                document_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                url TEXT NOT NULL,
                platform TEXT NOT NULL,
                date TEXT NOT NULL,
                path TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                content_hash TEXT NOT NULL
            );
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                url TEXT NOT NULL,
                path TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                line_start INTEGER,
                line_end INTEGER,
                content_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO documents (
                document_id, source_id, title, content, url, platform,
                date, path, updated_at, content_hash
            ) VALUES (
                'legacy-doc', 'source_legacy', 'Legacy', 'legacy content',
                'https://example.com/legacy', 'Legacy', '', 'Legacy',
                '2026-05-20T00:00:00Z', 'hash'
            );
            INSERT INTO chunks (
                chunk_id, document_id, source_id, title, text, url, path,
                chunk_index, line_start, line_end, content_hash, updated_at
            ) VALUES (
                'legacy-chunk', 'legacy-doc', 'source_legacy', 'Legacy',
                'legacy content', 'https://example.com/legacy', 'Legacy',
                0, 1, 1, 'hash', '2026-05-20T00:00:00Z'
            );
            """
        )

    store.ensure_schema()

    with store._connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}

    assert "version_id" in columns
    assert store.get_chunk("legacy-chunk").version_id == ""


def test_successful_sync_finalization_tombstones_documents_not_seen_at(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    job, started = store.begin_sync_job("source_github")
    assert started is True
    marker = "2026-05-22T00:02:00Z"
    keep = DocumentModel(
        id="keep",
        source_id="source_github",
        title="keep.py",
        content="print('keep')",
        url="https://example.com/keep.py",
        platform="GitHub",
        path="keep.py",
        last_seen_at=marker,
    )
    stale = DocumentModel(
        id="stale",
        source_id="source_github",
        title="stale.py",
        content="print('stale')",
        url="https://example.com/stale.py",
        platform="GitHub",
        path="stale.py",
        last_seen_at="2026-05-22T00:00:00Z",
    )
    store.upsert_document_and_replace_chunks(
        keep,
        [
            ChunkModel(
                chunk_id="keep:chunk:0:aaa",
                document_id="keep",
                source_id="source_github",
                title="keep.py",
                text="print('keep')",
                path="keep.py",
                chunk_index=0,
                content_hash="aaa",
            )
        ],
    )
    store.upsert_document_and_replace_chunks(
        stale,
        [
            ChunkModel(
                chunk_id="stale:chunk:0:bbb",
                document_id="stale",
                source_id="source_github",
                title="stale.py",
                text="print('stale')",
                path="stale.py",
                chunk_index=0,
                content_hash="bbb",
            )
        ],
    )

    _, deleted_chunk_ids = store.complete_successful_sync(
        job_id=job.job_id,
        source_id="source_github",
        total_documents=1,
        processed_documents=0,
        indexed_chunks=0,
        skipped_documents=1,
        last_seen_at=marker,
        cleanup_missing_documents=True,
        deleted_at="2026-05-22T00:01:00Z",
    )

    assert deleted_chunk_ids == ["stale:chunk:0:bbb"]
    assert store.get_document("stale").deleted_at == "2026-05-22T00:01:00Z"
    assert store.get_chunk("stale:chunk:0:bbb") is None
    assert store.has_chunk_record("stale:chunk:0:bbb") is True
    assert store.list_chunks_for_document("stale") == []
    assert [chunk.chunk_id for chunk in store.list_chunks()] == ["keep:chunk:0:aaa"]


def test_successful_sync_cleanup_can_be_limited_to_document_id_prefixes(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    job, started = store.begin_sync_job("source_github")
    assert started is True
    marker = "2026-05-22T00:02:00Z"
    kept = DocumentModel(
        id="github:eunhwa99/mcpcontentsearch:README.md",
        source_id="source_github",
        title="README",
        content="current repo document",
        url="https://example.com/README.md",
        platform="GitHub",
        path="README.md",
        last_seen_at=marker,
    )
    stale_configured_repo = DocumentModel(
        id="github:eunhwa99/mcpcontentsearch:old.py",
        source_id="source_github",
        title="old.py",
        content="removed from configured repo",
        url="https://example.com/old.py",
        platform="GitHub",
        path="old.py",
        last_seen_at="2026-05-22T00:00:00Z",
    )
    stale_ad_hoc_repo = DocumentModel(
        id="github:eunhwa99/leetcode:graph.py",
        source_id="source_github",
        title="graph.py",
        content="ad hoc target sync document",
        url="https://example.com/graph.py",
        platform="GitHub",
        path="graph.py",
        last_seen_at="2026-05-22T00:00:00Z",
    )
    for document in (kept, stale_configured_repo, stale_ad_hoc_repo):
        store.upsert_document_and_replace_chunks(
            document,
            [
                ChunkModel(
                    chunk_id=f"{document.id}:chunk:0:aaa",
                    document_id=document.id,
                    source_id="source_github",
                    title=document.title,
                    text=document.content,
                    path=document.path,
                    chunk_index=0,
                    content_hash="aaa",
                )
            ],
        )

    _, deleted_chunk_ids = store.complete_successful_sync(
        job_id=job.job_id,
        source_id="source_github",
        total_documents=1,
        processed_documents=0,
        indexed_chunks=0,
        skipped_documents=1,
        last_seen_at=marker,
        cleanup_missing_documents=True,
        cleanup_document_id_prefixes=("github:eunhwa99/mcpcontentsearch:",),
        deleted_at="2026-05-22T00:03:00Z",
    )

    assert deleted_chunk_ids == ["github:eunhwa99/mcpcontentsearch:old.py:chunk:0:aaa"]
    assert store.get_document(stale_configured_repo.id).deleted_at == "2026-05-22T00:03:00Z"
    assert store.get_document(stale_ad_hoc_repo.id).deleted_at == ""
    assert store.list_chunks_for_document(stale_ad_hoc_repo.id)
    assert [chunk.document_id for chunk in store.list_chunks(["source_github"])] == [
        "github:eunhwa99/leetcode:graph.py",
        "github:eunhwa99/mcpcontentsearch:README.md",
    ]


def test_successful_sync_cleanup_prefix_treats_underscore_literally(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    job, started = store.begin_sync_job("source_github")
    assert started is True
    exact_prefix_document = DocumentModel(
        id="github:eunhwa99/foo_bar:old.py",
        source_id="source_github",
        title="old.py",
        content="configured repo stale file",
        url="https://example.com/old.py",
        platform="GitHub",
        path="old.py",
        last_seen_at="2026-05-22T00:00:00Z",
    )
    wildcard_like_document = DocumentModel(
        id="github:eunhwa99/fooxbar:graph.py",
        source_id="source_github",
        title="graph.py",
        content="different repo that LIKE underscore would match",
        url="https://example.com/graph.py",
        platform="GitHub",
        path="graph.py",
        last_seen_at="2026-05-22T00:00:00Z",
    )
    for document in (exact_prefix_document, wildcard_like_document):
        store.upsert_document_and_replace_chunks(
            document,
            [
                ChunkModel(
                    chunk_id=f"{document.id}:chunk:0:aaa",
                    document_id=document.id,
                    source_id="source_github",
                    title=document.title,
                    text=document.content,
                    path=document.path,
                    chunk_index=0,
                    content_hash="aaa",
                )
            ],
        )

    _, deleted_chunk_ids = store.complete_successful_sync(
        job_id=job.job_id,
        source_id="source_github",
        total_documents=0,
        processed_documents=0,
        indexed_chunks=0,
        skipped_documents=0,
        last_seen_at="2026-05-22T00:02:00Z",
        cleanup_missing_documents=True,
        cleanup_document_id_prefixes=("github:eunhwa99/foo_bar:",),
        deleted_at="2026-05-22T00:03:00Z",
    )

    assert deleted_chunk_ids == ["github:eunhwa99/foo_bar:old.py:chunk:0:aaa"]
    assert store.get_document(exact_prefix_document.id).deleted_at == "2026-05-22T00:03:00Z"
    assert store.get_document(wildcard_like_document.id).deleted_at == ""
    assert store.list_chunks_for_document(wildcard_like_document.id)


def test_successful_sync_cleanup_ignores_source_mismatched_chunks(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    job, started = store.begin_sync_job("source_github")
    assert started is True
    stale = DocumentModel(
        id="stale",
        source_id="source_github",
        title="stale.py",
        content="print('stale')",
        url="https://example.com/stale.py",
        platform="GitHub",
        path="stale.py",
        last_seen_at="2026-05-22T00:00:00Z",
    )
    store.upsert_document(stale)
    mismatched = ChunkModel(
        chunk_id="stale:chunk:0:wrong-source",
        document_id="stale",
        source_id="source_other",
        title="Wrong Source",
        text="wrong source content",
        path="stale.py",
        chunk_index=0,
        content_hash="wrong",
    )
    store.ensure_schema()
    with store._connect() as conn:
        store._insert_chunks(conn, [mismatched])

    _, deleted_chunk_ids = store.complete_successful_sync(
        job_id=job.job_id,
        source_id="source_github",
        total_documents=0,
        processed_documents=0,
        indexed_chunks=0,
        skipped_documents=0,
        last_seen_at="2026-05-22T00:01:00Z",
        cleanup_missing_documents=True,
        deleted_at="2026-05-22T00:02:00Z",
    )

    with store._connect() as conn:
        mismatched_row = conn.execute(
            "SELECT * FROM chunks WHERE chunk_id = ?",
            ("stale:chunk:0:wrong-source",),
        ).fetchone()

    assert deleted_chunk_ids == []
    assert mismatched_row is not None
    assert store.get_document("stale").deleted_at == "2026-05-22T00:02:00Z"


def test_successful_sync_finalization_does_not_revive_failed_job(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    job, started = store.begin_sync_job("source_github")
    assert started is True
    store.complete_failed_sync(
        job_id=job.job_id,
        source_id="source_github",
        error_message="lease expired",
    )
    stale = DocumentModel(
        id="stale",
        source_id="source_github",
        title="stale.py",
        content="print('stale')",
        url="https://example.com/stale.py",
        platform="GitHub",
        path="stale.py",
        last_seen_at="2026-05-22T00:00:00Z",
    )
    store.upsert_document_and_replace_chunks(
        stale,
        [
            ChunkModel(
                chunk_id="stale:chunk:0:bbb",
                document_id="stale",
                source_id="source_github",
                title="stale.py",
                text="print('stale')",
                path="stale.py",
                chunk_index=0,
                content_hash="bbb",
            )
        ],
    )

    completed, deleted_chunk_ids = store.complete_successful_sync(
        job_id=job.job_id,
        source_id="source_github",
        total_documents=0,
        processed_documents=0,
        indexed_chunks=0,
        skipped_documents=0,
        last_seen_at="2026-05-22T00:01:00Z",
        cleanup_missing_documents=True,
        deleted_at="2026-05-22T00:02:00Z",
    )

    assert completed.status == SyncJobStatus.FAILED
    assert deleted_chunk_ids == []
    assert store.get_document("stale").deleted_at == ""
    assert store.list_chunks_for_document("stale")[0].chunk_id == "stale:chunk:0:bbb"


def test_failed_sync_rejects_source_mismatch(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_a",
            source_type=SourceType.GITHUB,
            name="Source A",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    store.upsert_source(
        SourceModel(
            source_id="source_b",
            source_type=SourceType.GITHUB,
            name="Source B",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    job, started = store.begin_sync_job("source_a")
    assert started is True

    with pytest.raises(ValueError, match="belongs to source_a"):
        store.complete_failed_sync(
            job_id=job.job_id,
            source_id="source_b",
            error_message="wrong source",
        )

    assert store.get_sync_job(job.job_id).status == SyncJobStatus.RUNNING
    assert store.get_source("source_a").sync_status == SyncStatus.RUNNING
    assert store.get_source("source_b").sync_status == SyncStatus.IDLE


def test_self_expired_job_marks_source_failed_when_no_replacement_is_active(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3", running_job_timeout_seconds=0)
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    job, started = store.begin_sync_job("source_github")
    assert started is True
    document = DocumentModel(
        id="expired",
        source_id="source_github",
        title="Expired",
        content="expired content",
        url="https://example.com/expired",
        platform="GitHub",
    )

    current_job = store.validate_running_job_document(job.job_id, document)

    assert current_job.status == SyncJobStatus.FAILED
    assert "preflight" in current_job.error_message
    assert store.get_source("source_github").sync_status == SyncStatus.FAILED


def test_stale_cross_source_document_claim_does_not_block_new_source(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3", running_job_timeout_seconds=60)
    store.upsert_source(
        SourceModel(
            source_id="source_a",
            source_type=SourceType.GITHUB,
            name="Source A",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    store.upsert_source(
        SourceModel(
            source_id="source_b",
            source_type=SourceType.GITHUB,
            name="Source B",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    first_job, started = store.begin_sync_job("source_a")
    assert started is True
    first_document = DocumentModel(
        id="shared",
        source_id="source_a",
        title="Shared A",
        content="source a content",
        url="https://example.com/a",
        platform="GitHub",
    )
    store.validate_running_job_document(first_job.job_id, first_document)
    with store._connect() as conn:
        conn.execute(
            "UPDATE sync_jobs SET heartbeat_at = ? WHERE job_id = ?",
            ("2000-01-01T00:00:00+00:00", first_job.job_id),
        )
    second_job, started = store.begin_sync_job("source_b")
    assert started is True
    second_document = DocumentModel(
        id="shared",
        source_id="source_b",
        title="Shared B",
        content="source b content",
        url="https://example.com/b",
        platform="GitHub",
    )

    current_job = store.validate_running_job_document(second_job.job_id, second_document)

    with store._connect() as conn:
        claim = conn.execute(
            "SELECT source_id, job_id FROM document_claims WHERE document_id = ?",
            ("shared",),
        ).fetchone()

    assert current_job.status == SyncJobStatus.RUNNING
    assert store.get_sync_job(first_job.job_id).status == SyncJobStatus.FAILED
    assert store.get_source("source_a").sync_status == SyncStatus.FAILED
    assert claim["source_id"] == "source_b"
    assert claim["job_id"] == second_job.job_id


def test_superseded_running_job_cannot_finalize_stale_cleanup(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3", running_job_timeout_seconds=60)
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    older = store.create_sync_job("source_github")
    newer = store.create_sync_job("source_github")
    _mark_job_running(
        store,
        older.job_id,
        started_at="2026-05-22T00:00:01+00:00",
        heartbeat_at="2999-01-01T00:00:00+00:00",
    )
    _mark_job_running(
        store,
        newer.job_id,
        started_at="2026-05-22T00:00:02+00:00",
        heartbeat_at="2999-01-01T00:00:00+00:00",
    )
    stale = DocumentModel(
        id="stale",
        source_id="source_github",
        title="stale.py",
        content="print('stale')",
        url="https://example.com/stale.py",
        platform="GitHub",
        path="stale.py",
        last_seen_at="2026-05-22T00:00:00Z",
    )
    store.upsert_document_and_replace_chunks(
        stale,
        [
            ChunkModel(
                chunk_id="stale:chunk:0:bbb",
                document_id="stale",
                source_id="source_github",
                title="stale.py",
                text="print('stale')",
                path="stale.py",
                chunk_index=0,
                content_hash="bbb",
            )
        ],
    )

    completed, deleted_chunk_ids = store.complete_successful_sync(
        job_id=older.job_id,
        source_id="source_github",
        total_documents=0,
        processed_documents=0,
        indexed_chunks=0,
        skipped_documents=0,
        last_seen_at="2026-05-22T00:01:00Z",
        cleanup_missing_documents=True,
        deleted_at="2026-05-22T00:02:00Z",
    )

    assert completed.status == SyncJobStatus.FAILED
    assert deleted_chunk_ids == []
    assert store.get_sync_job(newer.job_id).status == SyncJobStatus.RUNNING
    assert store.get_document("stale").deleted_at == ""
    assert store.list_chunks_for_document("stale")[0].chunk_id == "stale:chunk:0:bbb"


def test_orphan_chunks_are_not_active(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    orphan = ChunkModel(
        chunk_id="orphan-chunk",
        document_id="missing-doc",
        source_id="source_fake",
        title="Orphan",
        text="This chunk has no document lifecycle row.",
        url="https://example.com/orphan",
        path="orphan.md",
        chunk_index=0,
        content_hash="hash",
    )
    store.ensure_schema()
    with store._connect() as conn:
        store._insert_chunks(conn, [orphan])

    assert store.get_chunk("orphan-chunk") is None
    assert store.list_chunks_for_document("missing-doc") == []
    assert store.list_chunks() == []


def test_source_mismatched_chunks_are_not_active(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_document(
        DocumentModel(
            id="shared-id",
            source_id="source_a",
            title="Source A",
            content="source a content",
            url="https://example.com/a",
            platform="GitHub",
        )
    )
    mismatched = ChunkModel(
        chunk_id="shared-id:chunk:0:b",
        document_id="shared-id",
        source_id="source_b",
        title="Wrong Source",
        text="wrong source content",
        chunk_index=0,
        content_hash="b",
    )
    store.ensure_schema()
    with store._connect() as conn:
        store._insert_chunks(conn, [mismatched])

    assert store.get_chunk("shared-id:chunk:0:b") is None
    assert store.list_chunks_for_document("shared-id") == []
    assert store.list_chunks() == []


def test_replace_document_chunks_preserves_source_mismatched_inactive_rows(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_document(
        DocumentModel(
            id="shared-id",
            source_id="source_a",
            title="Source A",
            content="source a content",
            url="https://example.com/a",
            platform="GitHub",
        )
    )
    inactive = ChunkModel(
        chunk_id="shared-id:chunk:0:b",
        document_id="shared-id",
        source_id="source_b",
        title="Wrong Source",
        text="wrong source content",
        chunk_index=0,
        content_hash="b",
    )
    replacement = ChunkModel(
        chunk_id="shared-id:chunk:0:a2",
        document_id="shared-id",
        source_id="source_a",
        title="Source A",
        text="replacement",
        chunk_index=0,
        content_hash="a2",
    )
    store.ensure_schema()
    with store._connect() as conn:
        store._insert_chunks(conn, [inactive])

    store.replace_document_chunks("shared-id", [replacement])

    with store._connect() as conn:
        inactive_row = conn.execute(
            "SELECT chunk_id FROM chunks WHERE chunk_id = ?",
            ("shared-id:chunk:0:b",),
        ).fetchone()
    assert inactive_row["chunk_id"] == "shared-id:chunk:0:b"
    assert store.list_chunks_for_document("shared-id") == [replacement]
