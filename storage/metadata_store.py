import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from core.models import (
    ChunkModel,
    DocumentModel,
    SourceModel,
    SourceType,
    SyncJobModel,
    SyncJobStatus,
    SyncStatus,
)
from core.utils import ContentHasher


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MetadataStore:
    """SQLite-backed metadata store for ContextWiki sources, jobs, docs, and chunks."""

    def __init__(self, db_path: Path | str, running_job_timeout_seconds: int = 24 * 60 * 60):
        self.db_path = Path(db_path)
        self.running_job_timeout_seconds = running_job_timeout_seconds

    def ensure_schema(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    auth_ref TEXT NOT NULL,
                    sync_status TEXT NOT NULL,
                    last_synced_at TEXT NOT NULL,
                    last_error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sync_jobs (
                    job_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL,
                    total_documents INTEGER NOT NULL,
                    processed_documents INTEGER NOT NULL,
                    indexed_chunks INTEGER NOT NULL,
                    skipped_documents INTEGER NOT NULL,
                    error_message TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    external_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    url TEXT NOT NULL,
                    canonical_url TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL,
                    date TEXT NOT NULL,
                    path TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL DEFAULT '',
                    last_seen_sync_id TEXT NOT NULL DEFAULT '',
                    deleted_at TEXT NOT NULL DEFAULT '',
                    version_id TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chunks (
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

                CREATE TABLE IF NOT EXISTS document_claims (
                    document_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    claimed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chunk_tombstones (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                """
            )
            self._ensure_columns(
                conn,
                "documents",
                {
                    "external_id": "TEXT NOT NULL DEFAULT ''",
                    "canonical_url": "TEXT NOT NULL DEFAULT ''",
                    "last_seen_at": "TEXT NOT NULL DEFAULT ''",
                    "last_seen_sync_id": "TEXT NOT NULL DEFAULT ''",
                    "deleted_at": "TEXT NOT NULL DEFAULT ''",
                    "version_id": "TEXT NOT NULL DEFAULT ''",
                },
            )
            self._ensure_columns(
                conn,
                "sync_jobs",
                {
                    "heartbeat_at": "TEXT NOT NULL DEFAULT ''",
                },
            )

    def upsert_source(self, source: SourceModel) -> SourceModel:
        self.ensure_schema()
        existing = self.get_source(source.source_id)
        created_at = source.created_at or (existing.created_at if existing else _now())
        updated_at = _now()
        normalized = source.model_copy(update={"created_at": created_at, "updated_at": updated_at})
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sources (
                    source_id, source_type, name, enabled, auth_ref, sync_status,
                    last_synced_at, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    source_type = excluded.source_type,
                    name = excluded.name,
                    enabled = excluded.enabled,
                    auth_ref = excluded.auth_ref,
                    sync_status = excluded.sync_status,
                    last_synced_at = excluded.last_synced_at,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized.source_id,
                    normalized.source_type.value,
                    normalized.name,
                    int(normalized.enabled),
                    normalized.auth_ref,
                    normalized.sync_status.value,
                    normalized.last_synced_at,
                    normalized.last_error,
                    normalized.created_at,
                    normalized.updated_at,
                ),
            )
        return normalized

    def register_source(self, source: SourceModel) -> SourceModel:
        """Register static source config while preserving operational status."""
        self.ensure_schema()
        created_at = source.created_at or _now()
        updated_at = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sources (
                    source_id, source_type, name, enabled, auth_ref, sync_status,
                    last_synced_at, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    source_type = excluded.source_type,
                    name = excluded.name,
                    enabled = excluded.enabled,
                    auth_ref = excluded.auth_ref,
                    updated_at = excluded.updated_at
                """,
                (
                    source.source_id,
                    source.source_type.value,
                    source.name,
                    int(source.enabled),
                    source.auth_ref,
                    source.sync_status.value,
                    source.last_synced_at,
                    source.last_error,
                    created_at,
                    updated_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM sources WHERE source_id = ?",
                (source.source_id,),
            ).fetchone()
        return self._source_from_row(row)

    def get_source(self, source_id: str) -> Optional[SourceModel]:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone()
        return self._source_from_row(row) if row else None

    def list_sources(self) -> list[SourceModel]:
        self.ensure_schema()
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM sources ORDER BY source_id").fetchall()
        return [self._source_from_row(row) for row in rows]

    def update_source_status(
        self,
        source_id: str,
        sync_status: SyncStatus,
        *,
        last_error: str = "",
        last_synced_at: str = "",
    ) -> Optional[SourceModel]:
        source = self.get_source(source_id)
        if not source:
            return None
        updated = source.model_copy(
            update={
                "sync_status": sync_status,
                "last_error": last_error,
                "last_synced_at": last_synced_at or source.last_synced_at,
            }
        )
        return self.upsert_source(updated)

    def create_sync_job(self, source_id: str) -> SyncJobModel:
        self.ensure_schema()
        job = SyncJobModel(
            job_id=str(uuid.uuid4()),
            source_id=source_id,
            status=SyncJobStatus.QUEUED,
            started_at=_now(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_jobs (
                    job_id, source_id, status, started_at, heartbeat_at, finished_at,
                    total_documents, processed_documents, indexed_chunks,
                    skipped_documents, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.source_id,
                    job.status.value,
                    job.started_at,
                    "",
                    job.finished_at,
                    job.total_documents,
                    job.processed_documents,
                    job.indexed_chunks,
                    job.skipped_documents,
                    job.error_message,
                ),
            )
        return job

    def begin_sync_job(self, source_id: str) -> tuple[SyncJobModel, bool]:
        """Atomically start a sync job or return the active running job."""
        self.ensure_schema()
        started_at = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            source_row = conn.execute(
                "SELECT * FROM sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if not source_row:
                raise ValueError(f"Unknown source: {source_id}")
            active_row = self._resolve_active_running_job(conn, source_id, started_at)
            if active_row:
                conn.execute(
                    """
                    UPDATE sources SET
                        sync_status = ?,
                        last_error = '',
                        updated_at = ?
                    WHERE source_id = ?
                    """,
                    (SyncStatus.RUNNING.value, _now(), source_id),
                )
                return self._job_from_row(active_row), False

            job = SyncJobModel(
                job_id=str(uuid.uuid4()),
                source_id=source_id,
                status=SyncJobStatus.RUNNING,
                started_at=started_at,
            )
            conn.execute(
                """
                INSERT INTO sync_jobs (
                    job_id, source_id, status, started_at, heartbeat_at, finished_at,
                    total_documents, processed_documents, indexed_chunks,
                    skipped_documents, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.source_id,
                    job.status.value,
                    job.started_at,
                    started_at,
                    job.finished_at,
                    job.total_documents,
                    job.processed_documents,
                    job.indexed_chunks,
                    job.skipped_documents,
                    job.error_message,
                ),
            )
            conn.execute(
                """
                UPDATE sources SET
                    sync_status = ?,
                    last_error = '',
                    updated_at = ?
                WHERE source_id = ?
                """,
                (SyncStatus.RUNNING.value, _now(), source_id),
            )
        return job, True

    def touch_sync_job(self, job_id: str) -> Optional[SyncJobModel]:
        """Refresh a running job heartbeat without changing the public job contract."""
        self.ensure_schema()
        heartbeat_at = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            if row["status"] != SyncJobStatus.RUNNING.value:
                return self._job_from_row(row)
            active_row = self._resolve_active_running_job(
                conn,
                row["source_id"],
                heartbeat_at,
                failure_reason="Sync job timed out before heartbeat refresh completed",
            )
            if not active_row or active_row["job_id"] != job_id:
                self._reconcile_source_after_inactive_job(
                    conn,
                    row["source_id"],
                    heartbeat_at,
                    "Sync job is no longer active",
                )
                row = conn.execute("SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)).fetchone()
                return self._job_from_row(row)
            conn.execute(
                """
                UPDATE sync_jobs SET heartbeat_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (heartbeat_at, job_id, SyncJobStatus.RUNNING.value),
            )
            row = conn.execute("SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job_from_row(row) if row else None

    def validate_running_job_document(self, job_id: str, document: DocumentModel) -> Optional[SyncJobModel]:
        """Preflight a document before vector writes for the owning running sync."""
        self.ensure_schema()
        heartbeat_at = _now()
        normalized = self._normalize_document(document)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job_row = conn.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not job_row:
                raise ValueError(f"Unknown sync job: {job_id}")
            if job_row["status"] != SyncJobStatus.RUNNING.value:
                return self._job_from_row(job_row)
            if job_row["source_id"] != normalized.source_id:
                raise ValueError(
                    f"Sync job {job_id} belongs to {job_row['source_id']}, "
                    f"not {normalized.source_id}"
                )
            active_row = self._resolve_active_running_job(
                conn,
                normalized.source_id,
                heartbeat_at,
                failure_reason=(
                    "Sync job timed out before document metadata preflight completed"
                ),
            )
            if not active_row or active_row["job_id"] != job_id:
                self._reconcile_source_after_inactive_job(
                    conn,
                    normalized.source_id,
                    heartbeat_at,
                    "Sync job is no longer active",
                )
                row = conn.execute("SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)).fetchone()
                return self._job_from_row(row)

            self._validate_document_owner(conn, normalized)
            self._claim_document(conn, normalized, job_id, heartbeat_at)
            conn.execute(
                """
                UPDATE sync_jobs SET heartbeat_at = ?
                WHERE job_id = ?
                """,
                (heartbeat_at, job_id),
            )
            row = conn.execute("SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job_from_row(row)

    def upsert_document_and_replace_chunks_for_running_job(
        self,
        job_id: str,
        document: DocumentModel,
        chunks: Iterable[ChunkModel],
    ) -> tuple[Optional[DocumentModel], Optional[SyncJobModel]]:
        """Commit chunk metadata only while the owning sync job is still running."""
        self.ensure_schema()
        heartbeat_at = _now()
        normalized = self._normalize_document(document)
        chunk_list = list(chunks)
        document_id = normalized.document_id or normalized.id
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job_row = conn.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not job_row:
                raise ValueError(f"Unknown sync job: {job_id}")
            if job_row["status"] != SyncJobStatus.RUNNING.value:
                return None, self._job_from_row(job_row)
            if job_row["source_id"] != normalized.source_id:
                raise ValueError(
                    f"Sync job {job_id} belongs to {job_row['source_id']}, "
                    f"not {normalized.source_id}"
                )
            self._validate_chunks_for_document(chunk_list, document_id, normalized.source_id)
            active_row = self._resolve_active_running_job(
                conn,
                normalized.source_id,
                heartbeat_at,
                failure_reason="Sync job timed out before chunk metadata commit completed",
            )
            if not active_row or active_row["job_id"] != job_id:
                self._reconcile_source_after_inactive_job(
                    conn,
                    normalized.source_id,
                    heartbeat_at,
                    "Sync job is no longer active",
                )
                row = conn.execute("SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)).fetchone()
                return None, self._job_from_row(row)

            self._claim_document(conn, normalized, job_id, heartbeat_at)
            conn.execute(
                """
                UPDATE sync_jobs SET heartbeat_at = ?
                WHERE job_id = ?
                """,
                (heartbeat_at, job_id),
            )
            self._upsert_document(conn, normalized)
            self._record_chunk_tombstones_for_document(conn, document_id, normalized.source_id)
            conn.execute(
                "DELETE FROM chunks WHERE document_id = ? AND source_id = ?",
                (document_id, normalized.source_id),
            )
            self._insert_chunks(conn, chunk_list)
            job_row = conn.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return normalized, self._job_from_row(job_row)

    def update_sync_job(self, job_id: str, **updates) -> SyncJobModel:
        job = self.get_sync_job(job_id)
        if not job:
            raise ValueError(f"Unknown sync job: {job_id}")
        if updates.get("status") in {SyncJobStatus.RUNNING, SyncJobStatus.RUNNING.value}:
            raise ValueError("Use begin_sync_job() to start a running sync job")
        if updates.get("status") in {
            SyncJobStatus.SUCCEEDED,
            SyncJobStatus.SUCCEEDED.value,
            SyncJobStatus.FAILED,
            SyncJobStatus.FAILED.value,
        }:
            raise ValueError("Use complete_successful_sync() or complete_failed_sync()")
        updated = job.model_copy(update=updates)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sync_jobs SET
                    status = ?, started_at = ?, finished_at = ?, total_documents = ?,
                    processed_documents = ?, indexed_chunks = ?, skipped_documents = ?,
                    error_message = ?
                WHERE job_id = ?
                """,
                (
                    updated.status.value,
                    updated.started_at,
                    updated.finished_at,
                    updated.total_documents,
                    updated.processed_documents,
                    updated.indexed_chunks,
                    updated.skipped_documents,
                    updated.error_message,
                    updated.job_id,
                ),
            )
        return updated

    def complete_failed_sync(
        self,
        *,
        job_id: str,
        source_id: str,
        error_message: str,
    ) -> SyncJobModel:
        """Fail a queued/running sync without clobbering another active job."""
        self.ensure_schema()
        finished_at = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                raise ValueError(f"Unknown sync job: {job_id}")
            if row["source_id"] != source_id:
                raise ValueError(f"Sync job {job_id} belongs to {row['source_id']}, not {source_id}")
            if row["status"] not in {SyncJobStatus.QUEUED.value, SyncJobStatus.RUNNING.value}:
                return self._job_from_row(row)

            conn.execute(
                """
                UPDATE sync_jobs SET
                    status = ?,
                    finished_at = ?,
                    error_message = ?
                WHERE job_id = ?
                """,
                (SyncJobStatus.FAILED.value, finished_at, error_message, job_id),
            )
            conn.execute("DELETE FROM document_claims WHERE job_id = ?", (job_id,))
            active_row = self._resolve_active_running_job(conn, source_id, finished_at)
            if not active_row:
                conn.execute(
                    """
                    UPDATE sources SET
                        sync_status = ?,
                        last_error = ?,
                        updated_at = ?
                    WHERE source_id = ?
                    """,
                    (SyncStatus.FAILED.value, error_message, finished_at, source_id),
                )
            row = conn.execute("SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job_from_row(row)

    def get_sync_job(self, job_id: str) -> Optional[SyncJobModel]:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job_from_row(row) if row else None

    def get_latest_sync_job(self, source_id: str) -> Optional[SyncJobModel]:
        self.ensure_schema()
        checked_at = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            source_row = conn.execute(
                "SELECT sync_status FROM sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            active_row = self._resolve_active_running_job(
                conn,
                source_id,
                checked_at,
                failure_reason="Sync job timed out before status read completed",
            )
            if active_row:
                if not source_row or source_row["sync_status"] != SyncStatus.RUNNING.value:
                    conn.execute(
                        """
                        UPDATE sources SET
                            sync_status = ?,
                            last_error = '',
                            updated_at = ?
                        WHERE source_id = ?
                        """,
                        (SyncStatus.RUNNING.value, checked_at, source_id),
                    )
                return self._job_from_row(active_row)
            if source_row and source_row["sync_status"] == SyncStatus.RUNNING.value:
                self._reconcile_source_after_inactive_job(
                    conn,
                    source_id,
                    checked_at,
                    "Sync job timed out before status read completed",
                )
            row = conn.execute(
                """
                SELECT * FROM sync_jobs
                WHERE source_id = ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (source_id,),
            ).fetchone()
        return self._job_from_row(row) if row else None

    def upsert_document(self, document: DocumentModel) -> DocumentModel:
        self.ensure_schema()
        normalized = self._normalize_document(document)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._upsert_document(conn, normalized)
        return normalized

    def upsert_document_and_replace_chunks(
        self,
        document: DocumentModel,
        chunks: Iterable[ChunkModel],
    ) -> DocumentModel:
        """Atomically commit document hash and its citation chunks."""
        self.ensure_schema()
        normalized = self._normalize_document(document)
        chunk_list = list(chunks)
        document_id = normalized.document_id or normalized.id
        self._validate_chunks_for_document(chunk_list, document_id, normalized.source_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._upsert_document(conn, normalized)
            self._record_chunk_tombstones_for_document(conn, document_id, normalized.source_id)
            conn.execute(
                "DELETE FROM chunks WHERE document_id = ? AND source_id = ?",
                (document_id, normalized.source_id),
            )
            self._insert_chunks(conn, chunk_list)
        return normalized

    def get_document(self, document_id: str) -> Optional[DocumentModel]:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE document_id = ?", (document_id,)).fetchone()
        return self._document_from_row(row) if row else None

    def get_document_by_url(self, url: str) -> Optional[DocumentModel]:
        if not url:
            return None
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM documents
                WHERE canonical_url = ? OR url = ?
                ORDER BY updated_at DESC, document_id
                LIMIT 1
                """,
                (url, url),
            ).fetchone()
        return self._document_from_row(row) if row else None

    def get_document_content_hash(self, document_id: str) -> str:
        document = self.get_document(document_id)
        if not document or document.deleted_at:
            return ""
        return document.content_hash

    def replace_document_chunks(self, document_id: str, chunks: Iterable[ChunkModel]):
        self.ensure_schema()
        chunk_list = list(chunks)
        with self._connect() as conn:
            document_row = conn.execute(
                "SELECT source_id FROM documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if not document_row and chunk_list:
                raise ValueError(f"Unknown document: {document_id}")
            if document_row:
                self._validate_chunks_for_document(
                    chunk_list,
                    document_id,
                    document_row["source_id"],
                )
            source_id = document_row["source_id"] if document_row else ""
            self._record_chunk_tombstones_for_document(conn, document_id, source_id)
            conn.execute(
                "DELETE FROM chunks WHERE document_id = ? AND source_id = ?",
                (document_id, source_id),
            )
            self._insert_chunks(conn, chunk_list)

    def get_chunk(self, chunk_id: str) -> Optional[ChunkModel]:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT c.* FROM chunks c
                JOIN documents d ON d.document_id = c.document_id
                    AND d.source_id = c.source_id
                WHERE c.chunk_id = ? AND COALESCE(d.deleted_at, '') = ''
                """,
                (chunk_id,),
            ).fetchone()
        return self._chunk_from_row(row) if row else None

    def has_chunk_record(self, chunk_id: str) -> bool:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM chunks WHERE chunk_id = ? LIMIT 1",
                (chunk_id,),
            ).fetchone()
            if row:
                return True
            row = conn.execute(
                "SELECT 1 FROM chunk_tombstones WHERE chunk_id = ? LIMIT 1",
                (chunk_id,),
            ).fetchone()
        return row is not None

    def list_chunks_for_document(self, document_id: str) -> list[ChunkModel]:
        self.ensure_schema()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT c.* FROM chunks c
                JOIN documents d ON d.document_id = c.document_id
                    AND d.source_id = c.source_id
                WHERE c.document_id = ? AND COALESCE(d.deleted_at, '') = ''
                ORDER BY c.chunk_index
                """,
                (document_id,),
            ).fetchall()
        return [self._chunk_from_row(row) for row in rows]

    def list_chunks(self, source_ids: Optional[list[str]] = None) -> list[ChunkModel]:
        self.ensure_schema()
        with self._connect() as conn:
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                rows = conn.execute(
                    f"""
                    SELECT c.* FROM chunks c
                    JOIN documents d ON d.document_id = c.document_id
                        AND d.source_id = c.source_id
                    WHERE c.source_id IN ({placeholders}) AND COALESCE(d.deleted_at, '') = ''
                    ORDER BY c.document_id, c.chunk_index
                    """,
                    source_ids,
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT c.* FROM chunks c
                    JOIN documents d ON d.document_id = c.document_id
                        AND d.source_id = c.source_id
                    WHERE COALESCE(d.deleted_at, '') = ''
                    ORDER BY c.document_id, c.chunk_index
                    """
                ).fetchall()
        return [self._chunk_from_row(row) for row in rows]

    def complete_successful_sync(
        self,
        *,
        job_id: str,
        source_id: str,
        total_documents: int,
        processed_documents: int,
        indexed_chunks: int,
        skipped_documents: int,
        last_seen_at: str,
        cleanup_missing_documents: bool,
        deleted_at: str,
        last_seen_sync_id: str = "",
    ) -> tuple[SyncJobModel, list[str]]:
        """Atomically finalize a successful sync and optional stale cleanup."""
        self.ensure_schema()
        finished_at = _now()
        source_updated_at = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current_job = conn.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not current_job:
                raise ValueError(f"Unknown sync job: {job_id}")
            if current_job["status"] != SyncJobStatus.RUNNING.value:
                return self._job_from_row(current_job), []
            if current_job["source_id"] != source_id:
                raise ValueError(
                    f"Sync job {job_id} belongs to {current_job['source_id']}, not {source_id}"
                )
            active_row = self._resolve_active_running_job(
                conn,
                source_id,
                finished_at,
                failure_reason="Sync job timed out before successful finalization completed",
            )
            if not active_row or active_row["job_id"] != job_id:
                self._reconcile_source_after_inactive_job(
                    conn,
                    source_id,
                    finished_at,
                    "Sync job is no longer active",
                )
                row = conn.execute("SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)).fetchone()
                return self._job_from_row(row), []

            deleted_chunk_ids = []
            if cleanup_missing_documents:
                deleted_chunk_ids = self._tombstone_documents_not_seen_at(
                    conn,
                    source_id,
                    last_seen_at,
                    deleted_at,
                    last_seen_sync_id,
                )

            job_cursor = conn.execute(
                """
                UPDATE sync_jobs SET
                    status = ?, finished_at = ?, total_documents = ?,
                    processed_documents = ?, indexed_chunks = ?,
                    skipped_documents = ?, error_message = ''
                WHERE job_id = ?
                """,
                (
                    SyncJobStatus.SUCCEEDED.value,
                    finished_at,
                    total_documents,
                    processed_documents,
                    indexed_chunks,
                    skipped_documents,
                    job_id,
                ),
            )
            if job_cursor.rowcount == 0:
                raise ValueError(f"Unknown sync job: {job_id}")
            conn.execute("DELETE FROM document_claims WHERE job_id = ?", (job_id,))

            source_cursor = conn.execute(
                """
                UPDATE sources SET
                    sync_status = ?,
                    last_synced_at = ?,
                    last_error = '',
                    updated_at = ?
                WHERE source_id = ?
                """,
                (
                    SyncStatus.SUCCEEDED.value,
                    finished_at,
                    source_updated_at,
                    source_id,
                ),
            )
            if source_cursor.rowcount == 0:
                raise ValueError(f"Unknown source: {source_id}")

            row = conn.execute("SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)).fetchone()

        return self._job_from_row(row), deleted_chunk_ids

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _resolve_active_running_job(
        self,
        conn,
        source_id: str,
        finished_at: str,
        *,
        failure_reason: str | None = None,
    ):
        running_rows = conn.execute(
            """
            SELECT * FROM sync_jobs
            WHERE source_id = ? AND status = ?
            ORDER BY started_at DESC, job_id DESC
            """,
            (source_id, SyncJobStatus.RUNNING.value),
        ).fetchall()
        active_running_rows = []
        for running_row in running_rows:
            if self._is_stale_running_job(running_row):
                self._fail_sync_job_row(
                    conn,
                    running_row["job_id"],
                    finished_at,
                    failure_reason
                    or (
                        "Previous running sync job timed out after "
                        f"{self.running_job_timeout_seconds} seconds"
                    ),
                )
            else:
                active_running_rows.append(running_row)

        if not active_running_rows:
            return None

        active_row = active_running_rows[0]
        for superseded_row in active_running_rows[1:]:
            self._fail_sync_job_row(
                conn,
                superseded_row["job_id"],
                finished_at,
                "Superseded by another running sync job for the same source",
            )
        return active_row

    def _reconcile_source_after_inactive_job(
        self,
        conn,
        source_id: str,
        finished_at: str,
        error_message: str,
    ):
        active_row = self._resolve_active_running_job(conn, source_id, finished_at)
        if active_row:
            return
        conn.execute(
            """
            UPDATE sources SET
                sync_status = ?,
                last_error = ?,
                updated_at = ?
            WHERE source_id = ?
            """,
            (SyncStatus.FAILED.value, error_message, finished_at, source_id),
        )

    @staticmethod
    def _fail_sync_job_row(conn, job_id: str, finished_at: str, error_message: str):
        conn.execute(
            """
            UPDATE sync_jobs SET
                status = ?,
                finished_at = ?,
                error_message = ?
            WHERE job_id = ?
            """,
            (
                SyncJobStatus.FAILED.value,
                finished_at,
                error_message,
                job_id,
            ),
        )
        conn.execute("DELETE FROM document_claims WHERE job_id = ?", (job_id,))

    def _claim_document(self, conn, document: DocumentModel, job_id: str, claimed_at: str):
        document_id = document.document_id or document.id
        claim_row = conn.execute(
            "SELECT source_id, job_id FROM document_claims WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        if claim_row and claim_row["source_id"] != document.source_id:
            claim_job = conn.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?",
                (claim_row["job_id"],),
            ).fetchone()
            remove_claim = claim_job is None or claim_job["status"] != SyncJobStatus.RUNNING.value
            if claim_job and claim_job["status"] == SyncJobStatus.RUNNING.value:
                if self._is_stale_running_job(claim_job):
                    self._fail_sync_job_row(
                        conn,
                        claim_job["job_id"],
                        claimed_at,
                        "Document claim expired with stale sync job",
                    )
                    self._reconcile_source_after_inactive_job(
                        conn,
                        claim_job["source_id"],
                        claimed_at,
                        "Document claim expired with stale sync job",
                    )
                    remove_claim = True
            if remove_claim:
                conn.execute(
                    "DELETE FROM document_claims WHERE document_id = ? AND job_id = ?",
                    (document_id, claim_row["job_id"]),
                )
            else:
                raise ValueError(
                    f"Document {document_id} is already claimed by "
                    f"{claim_row['source_id']}, not {document.source_id}"
                )
        conn.execute(
            """
            INSERT INTO document_claims (document_id, source_id, job_id, claimed_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                source_id = excluded.source_id,
                job_id = excluded.job_id,
                claimed_at = excluded.claimed_at
            """,
            (document_id, document.source_id, job_id, claimed_at),
        )

    def _is_stale_running_job(self, row) -> bool:
        if self.running_job_timeout_seconds <= 0:
            return True
        timestamp = row["heartbeat_at"] or row["started_at"]
        parsed = self._parse_timestamp(timestamp)
        if not parsed:
            return True
        return datetime.now(timezone.utc) - parsed > timedelta(
            seconds=self.running_job_timeout_seconds
        )

    @staticmethod
    def _parse_timestamp(value: str) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _ensure_columns(conn, table_name: str, columns: dict[str, str]):
        existing_columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for column_name, column_definition in columns.items():
            if column_name not in existing_columns:
                conn.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
                )

    @staticmethod
    def _normalize_document(document: DocumentModel) -> DocumentModel:
        content_hash = document.content_hash or ContentHasher.hash_content(document.content)
        document_id = document.external_id or document.document_id or document.id
        return document.model_copy(
            update={
                "document_id": document_id,
                "external_id": document.external_id,
                "canonical_url": document.canonical_url or document.url,
                "path": document.path or document.title,
                "updated_at": document.updated_at or document.date,
                "last_seen_sync_id": document.last_seen_sync_id,
                "deleted_at": document.deleted_at,
                "content_hash": content_hash,
            }
        )

    @staticmethod
    def _upsert_document(conn, document: DocumentModel):
        MetadataStore._validate_document_owner(conn, document)
        document_id = document.document_id or document.id
        cursor = conn.execute(
            """
            INSERT INTO documents (
                document_id, source_id, external_id, title, content, url,
                canonical_url, platform, date, path, updated_at, last_seen_at,
                last_seen_sync_id, deleted_at, version_id, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                source_id = excluded.source_id,
                external_id = excluded.external_id,
                title = excluded.title,
                content = excluded.content,
                url = excluded.url,
                canonical_url = excluded.canonical_url,
                platform = excluded.platform,
                date = excluded.date,
                path = excluded.path,
                updated_at = excluded.updated_at,
                last_seen_at = excluded.last_seen_at,
                last_seen_sync_id = excluded.last_seen_sync_id,
                deleted_at = excluded.deleted_at,
                version_id = excluded.version_id,
                content_hash = excluded.content_hash
            WHERE documents.source_id = excluded.source_id
            """,
            (
                document_id,
                document.source_id,
                document.external_id,
                document.title,
                document.content,
                document.url,
                document.canonical_url,
                document.platform,
                document.date,
                document.path,
                document.updated_at,
                document.last_seen_at,
                document.last_seen_sync_id,
                document.deleted_at,
                document.version_id,
                document.content_hash,
            ),
        )
        if cursor.rowcount == 0:
            existing_row = conn.execute(
                "SELECT source_id FROM documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            existing_source = existing_row["source_id"] if existing_row else "unknown"
            raise ValueError(
                f"Document {document_id} already belongs to "
                f"{existing_source}, not {document.source_id}"
            )

    @staticmethod
    def _validate_document_owner(conn, document: DocumentModel):
        document_id = document.document_id or document.id
        existing_row = conn.execute(
            "SELECT source_id FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        if existing_row and existing_row["source_id"] != document.source_id:
            raise ValueError(
                f"Document {document_id} already belongs to "
                f"{existing_row['source_id']}, not {document.source_id}"
            )

    @staticmethod
    def _validate_chunks_for_document(
        chunks: list[ChunkModel],
        document_id: str,
        source_id: str,
    ):
        for chunk in chunks:
            if chunk.document_id != document_id:
                raise ValueError(
                    f"Chunk {chunk.chunk_id} belongs to document {chunk.document_id}, "
                    f"not {document_id}"
                )
            if chunk.source_id != source_id:
                raise ValueError(
                    f"Chunk {chunk.chunk_id} belongs to {chunk.source_id}, "
                    f"not {source_id}"
                )

    @staticmethod
    def _tombstone_documents_not_seen_at(
        conn,
        source_id: str,
        last_seen_at: str,
        deleted_at: str,
        last_seen_sync_id: str = "",
    ) -> list[str]:
        marker_column = "last_seen_sync_id" if last_seen_sync_id else "last_seen_at"
        marker_value = last_seen_sync_id or last_seen_at
        chunk_rows = conn.execute(
            f"""
            SELECT c.chunk_id FROM chunks c
            JOIN documents d ON d.document_id = c.document_id
                AND d.source_id = c.source_id
            WHERE d.source_id = ?
              AND COALESCE(d.deleted_at, '') = ''
              AND COALESCE(d.{marker_column}, '') != ?
            ORDER BY c.document_id, c.chunk_index
            """,
            (source_id, marker_value),
        ).fetchall()
        conn.execute(
            f"""
            UPDATE documents
            SET deleted_at = ?
            WHERE source_id = ?
              AND COALESCE(deleted_at, '') = ''
              AND COALESCE({marker_column}, '') != ?
            """,
            (deleted_at, source_id, marker_value),
        )
        return [row["chunk_id"] for row in chunk_rows]

    @staticmethod
    def _insert_chunks(conn, chunks: list[ChunkModel]):
        conn.executemany(
            """
            INSERT INTO chunks (
                chunk_id, document_id, source_id, title, text, url, path,
                chunk_index, line_start, line_end, content_hash, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    chunk.chunk_id,
                    chunk.document_id,
                    chunk.source_id,
                    chunk.title,
                    chunk.text,
                    chunk.url,
                    chunk.path,
                    chunk.chunk_index,
                    chunk.line_start,
                    chunk.line_end,
                    chunk.content_hash,
                    chunk.updated_at,
                )
                for chunk in chunks
            ],
        )

    @staticmethod
    def _record_chunk_tombstones_for_document(conn, document_id: str, source_id: str):
        if not document_id or not source_id:
            return
        conn.execute(
            """
            INSERT OR IGNORE INTO chunk_tombstones (
                chunk_id, document_id, source_id, recorded_at
            )
            SELECT chunk_id, document_id, source_id, ?
            FROM chunks
            WHERE document_id = ? AND source_id = ?
            """,
            (_now(), document_id, source_id),
        )

    @staticmethod
    def _source_from_row(row) -> SourceModel:
        return SourceModel(
            source_id=row["source_id"],
            source_type=SourceType(row["source_type"]),
            name=row["name"],
            enabled=bool(row["enabled"]),
            auth_ref=row["auth_ref"],
            sync_status=SyncStatus(row["sync_status"]),
            last_synced_at=row["last_synced_at"],
            last_error=row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _job_from_row(row) -> SyncJobModel:
        return SyncJobModel(
            job_id=row["job_id"],
            source_id=row["source_id"],
            status=SyncJobStatus(row["status"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            total_documents=row["total_documents"],
            processed_documents=row["processed_documents"],
            indexed_chunks=row["indexed_chunks"],
            skipped_documents=row["skipped_documents"],
            error_message=row["error_message"],
        )

    @staticmethod
    def _document_from_row(row) -> DocumentModel:
        return DocumentModel(
            id=row["document_id"],
            document_id=row["document_id"],
            source_id=row["source_id"],
            external_id=row["external_id"],
            title=row["title"],
            content=row["content"],
            url=row["url"],
            canonical_url=row["canonical_url"],
            platform=row["platform"],
            date=row["date"],
            path=row["path"],
            updated_at=row["updated_at"],
            last_seen_at=row["last_seen_at"],
            last_seen_sync_id=row["last_seen_sync_id"],
            deleted_at=row["deleted_at"],
            version_id=row["version_id"],
            content_hash=row["content_hash"],
        )

    @staticmethod
    def _chunk_from_row(row) -> ChunkModel:
        return ChunkModel(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            source_id=row["source_id"],
            title=row["title"],
            text=row["text"],
            url=row["url"],
            path=row["path"],
            chunk_index=row["chunk_index"],
            line_start=row["line_start"],
            line_end=row["line_end"],
            content_hash=row["content_hash"],
            updated_at=row["updated_at"],
        )
