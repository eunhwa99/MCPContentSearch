import sqlite3
import os
import uuid
from errno import EPERM, ESRCH
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


ORPHANED_SYNC_JOB_RECOVERY_MESSAGE = (
    "Previous running sync job was recovered after server restart; start sync again."
)
OBSIDIAN_REFRESH_CLEARABLE_ERRORS = (
    "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
    "is not set or is not an existing directory.",
    "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
    "must be an absolute path.",
    "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
    "must not be a symlink.",
)
OBSIDIAN_INCOMPLETE_SNAPSHOT_PUBLIC_ERROR = (
    "Obsidian vault snapshot was incomplete because one or more notes could not be read."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MetadataStore:
    """SQLite-backed metadata store for ContextWiki sources, jobs, docs, and chunks."""

    def __init__(
        self,
        db_path: Path | str,
        running_job_timeout_seconds: int = 24 * 60 * 60,
        sync_owner_id: str | None = None,
        unowned_running_job_grace_seconds: int = 60,
    ):
        self.db_path = Path(db_path)
        self.running_job_timeout_seconds = running_job_timeout_seconds
        self.sync_owner_id = sync_owner_id or str(uuid.uuid4())
        self.unowned_running_job_grace_seconds = unowned_running_job_grace_seconds

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
                    owner_id TEXT NOT NULL DEFAULT '',
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

                CREATE TABLE IF NOT EXISTS sync_job_owners (
                    owner_id TEXT PRIMARY KEY,
                    process_id INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL
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
                    version_id TEXT NOT NULL DEFAULT '',
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
                    "owner_id": "TEXT NOT NULL DEFAULT ''",
                    "heartbeat_at": "TEXT NOT NULL DEFAULT ''",
                },
            )
            self._ensure_columns(
                conn,
                "chunks",
                {
                    "version_id": "TEXT NOT NULL DEFAULT ''",
                },
            )
            self._touch_sync_owner(conn, _now())

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
                    sync_status = CASE
                        WHEN excluded.enabled = 0 AND excluded.last_error != ''
                        THEN ?
                        ELSE sources.sync_status
                    END,
                    last_error = CASE
                        WHEN excluded.enabled = 0 AND excluded.last_error != '' THEN excluded.last_error
                        WHEN sources.last_error = ? AND excluded.enabled = 1 THEN sources.last_error
                        WHEN excluded.enabled = 1 AND sources.enabled = 0 THEN excluded.last_error
                        WHEN excluded.enabled = 1
                            AND sources.last_error IN (?, ?, ?)
                        THEN excluded.last_error
                        ELSE sources.last_error
                    END,
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
                    SyncStatus.FAILED.value,
                    OBSIDIAN_INCOMPLETE_SNAPSHOT_PUBLIC_ERROR,
                    *OBSIDIAN_REFRESH_CLEARABLE_ERRORS,
                ),
            )
            row = conn.execute(
                "SELECT * FROM sources WHERE source_id = ?",
                (source.source_id,),
            ).fetchone()
        registered = self._source_from_row(row)
        if registered is None:
            raise ValueError(f"Registered source has unsupported type: {source.source_id}")
        return registered

    def get_source(self, source_id: str) -> Optional[SourceModel]:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone()
        return self._source_from_row(row) if row else None

    def list_sources(self) -> list[SourceModel]:
        self.ensure_schema()
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM sources ORDER BY source_id").fetchall()
        sources = []
        for row in rows:
            source = self._source_from_row(row)
            if source is not None:
                sources.append(source)
        return sources

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
                    job_id, source_id, owner_id, status, started_at, heartbeat_at, finished_at,
                    total_documents, processed_documents, indexed_chunks,
                    skipped_documents, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.source_id,
                    "",
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
                    job_id, source_id, owner_id, status, started_at, heartbeat_at, finished_at,
                    total_documents, processed_documents, indexed_chunks,
                    skipped_documents, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.source_id,
                    self.sync_owner_id,
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

    def recover_orphaned_running_jobs(
        self,
        *,
        started_before: str,
        error_message: str,
        source_ids: Iterable[str] | None = None,
    ) -> int:
        """Fail restart-orphaned jobs without stealing a live owned sync."""
        self.ensure_schema()
        cutoff = self._parse_timestamp(started_before)
        if not cutoff:
            raise ValueError("started_before must be an ISO-8601 timestamp")
        scoped_source_ids = tuple(
            dict.fromkeys(str(source_id) for source_id in source_ids or () if source_id)
        )
        if source_ids is not None and not scoped_source_ids:
            return 0
        finished_at = _now()
        recovered_job_ids: list[str] = []
        affected_source_ids: set[str] = set()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            running_rows = conn.execute(
                """
                SELECT * FROM sync_jobs
                WHERE status = ?
                ORDER BY started_at, job_id
                """,
                (SyncJobStatus.RUNNING.value,),
            ).fetchall()
            scoped_source_id_set = set(scoped_source_ids)
            for row in running_rows:
                if scoped_source_id_set and row["source_id"] not in scoped_source_id_set:
                    continue
                job_started_at = self._parse_timestamp(row["started_at"])
                if job_started_at and job_started_at >= cutoff:
                    continue
                if not self._should_recover_startup_running_job(conn, row):
                    continue
                self._fail_sync_job_row(
                    conn,
                    row["job_id"],
                    finished_at,
                    error_message,
                )
                recovered_job_ids.append(row["job_id"])
                affected_source_ids.add(row["source_id"])

            for source_id in affected_source_ids:
                active_row = conn.execute(
                    """
                    SELECT job_id FROM sync_jobs
                    WHERE source_id = ? AND status = ?
                    LIMIT 1
                    """,
                    (source_id, SyncJobStatus.RUNNING.value),
                ).fetchone()
                if active_row:
                    continue
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

        return len(recovered_job_ids)

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
                # Dynamic SQL is limited to generated placeholders; source IDs
                # remain parameterized in the execute call below.
                query = "\n".join(
                    [
                        "SELECT c.* FROM chunks c",
                        "JOIN documents d ON d.document_id = c.document_id",
                        "    AND d.source_id = c.source_id",
                        "WHERE c.source_id IN (" + placeholders + ")",
                        "  AND COALESCE(d.deleted_at, '') = ''",
                        "ORDER BY c.document_id, c.chunk_index",
                    ]
                )
                rows = conn.execute(
                    query,
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

    def list_chunks_matching_metadata_terms(
        self,
        terms: Iterable[str],
        source_ids: Optional[list[str]] = None,
        limit: int = 200,
        require_document_like: bool = False,
        include_text: bool = False,
        require_all_terms: bool = False,
        metadata_only_terms: Optional[Iterable[str]] = None,
    ) -> list[ChunkModel]:
        """Return active chunks whose citation metadata contains any term."""
        self.ensure_schema()
        normalized_terms = [term.lower() for term in terms if term]
        normalized_metadata_only_terms = [
            term.lower() for term in (metadata_only_terms or []) if term
        ]
        if not normalized_terms and not normalized_metadata_only_terms:
            return []

        metadata_fields = [
            "c.chunk_id",
            "c.document_id",
            "c.title",
            "c.url",
            "c.path",
            "d.external_id",
            "d.title",
            "d.url",
            "d.canonical_url",
            "d.path",
            "d.platform",
        ]
        searchable_fields = [*metadata_fields]
        if include_text:
            searchable_fields.append("c.text")
        term_clauses = []
        params: list[str | int] = []
        for term in normalized_terms:
            term_clauses.append(
                "(" + " OR ".join(f"INSTR(LOWER({field}), ?) > 0" for field in searchable_fields) + ")"
            )
            params.extend([term for _ in searchable_fields])

        term_operator = " AND " if require_all_terms else " OR "
        where_clauses = ["COALESCE(d.deleted_at, '') = ''"]
        if term_clauses:
            where_clauses.append("(" + term_operator.join(term_clauses) + ")")
        for term in normalized_metadata_only_terms:
            where_clauses.append(
                "(" + " OR ".join(f"INSTR(LOWER({field}), ?) > 0" for field in metadata_fields) + ")"
            )
            params.extend([term for _ in metadata_fields])
        if require_document_like:
            document_like_clause = """
                (
                    LOWER(c.document_id) LIKE '%/readme.%'
                    OR LOWER(c.document_id) LIKE '%/readme/%'
                    OR LOWER(c.document_id) LIKE '%:readme.%'
                    OR LOWER(c.document_id) LIKE '%:readme/%'
                    OR LOWER(c.document_id) LIKE '%/docs/%'
                    OR LOWER(c.document_id) LIKE '%:docs/%'
                    OR LOWER(c.document_id) LIKE '%/documentation/%'
                    OR LOWER(c.document_id) LIKE '%:documentation/%'
                    OR LOWER(c.document_id) LIKE '%.md'
                    OR LOWER(c.document_id) LIKE '%.mdx'
                    OR LOWER(c.document_id) LIKE '%.markdown'
                    OR LOWER(c.document_id) LIKE '%.rst'
                    OR LOWER(c.document_id) LIKE '%.txt'
                    OR LOWER(c.path) = 'readme'
                    OR LOWER(c.path) LIKE 'readme.%'
                    OR LOWER(c.path) LIKE 'readme/%'
                    OR LOWER(c.path) LIKE '%/readme.%'
                    OR LOWER(c.path) LIKE '%/readme/%'
                    OR LOWER(c.path) LIKE 'docs/%'
                    OR LOWER(c.path) LIKE '%/docs/%'
                    OR LOWER(c.path) LIKE 'documentation/%'
                    OR LOWER(c.path) LIKE '%/documentation/%'
                    OR LOWER(c.path) LIKE '%.md'
                    OR LOWER(c.path) LIKE '%.mdx'
                    OR LOWER(c.path) LIKE '%.markdown'
                    OR LOWER(c.path) LIKE '%.rst'
                    OR LOWER(c.path) LIKE '%.txt'
                    OR LOWER(d.document_id) LIKE '%/readme.%'
                    OR LOWER(d.document_id) LIKE '%/readme/%'
                    OR LOWER(d.document_id) LIKE '%:readme.%'
                    OR LOWER(d.document_id) LIKE '%:readme/%'
                    OR LOWER(d.document_id) LIKE '%/docs/%'
                    OR LOWER(d.document_id) LIKE '%:docs/%'
                    OR LOWER(d.document_id) LIKE '%/documentation/%'
                    OR LOWER(d.document_id) LIKE '%:documentation/%'
                    OR LOWER(d.document_id) LIKE '%.md'
                    OR LOWER(d.document_id) LIKE '%.mdx'
                    OR LOWER(d.document_id) LIKE '%.markdown'
                    OR LOWER(d.document_id) LIKE '%.rst'
                    OR LOWER(d.document_id) LIKE '%.txt'
                    OR LOWER(d.path) = 'readme'
                    OR LOWER(d.path) LIKE 'readme.%'
                    OR LOWER(d.path) LIKE 'readme/%'
                    OR LOWER(d.path) LIKE '%/readme.%'
                    OR LOWER(d.path) LIKE '%/readme/%'
                    OR LOWER(d.path) LIKE 'docs/%'
                    OR LOWER(d.path) LIKE '%/docs/%'
                    OR LOWER(d.path) LIKE 'documentation/%'
                    OR LOWER(d.path) LIKE '%/documentation/%'
                    OR LOWER(d.path) LIKE '%.md'
                    OR LOWER(d.path) LIKE '%.mdx'
                    OR LOWER(d.path) LIKE '%.markdown'
                    OR LOWER(d.path) LIKE '%.rst'
                    OR LOWER(d.path) LIKE '%.txt'
                )
            """
            where_clauses.append(f"(c.source_id != 'source_github' OR {document_like_clause})")
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            where_clauses.append(f"c.source_id IN ({placeholders})")
            params.extend(source_ids)
        params.append(limit)

        with self._connect() as conn:
            # The WHERE fragments are selected from fixed SQL templates above;
            # caller values are appended to params and bound with placeholders.
            query = "\n".join(
                [
                    "SELECT c.* FROM chunks c",
                    "JOIN documents d ON d.document_id = c.document_id",
                    "    AND d.source_id = c.source_id",
                    "WHERE " + " AND ".join(where_clauses),
                    "ORDER BY c.document_id, c.chunk_index",
                    "LIMIT ?",
                ]
            )
            rows = conn.execute(
                query,
                params,
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
        cleanup_document_id_prefixes: Iterable[str] | None = None,
    ) -> tuple[SyncJobModel, list[str]]:
        """Atomically finalize a successful sync and optional stale cleanup."""
        self.ensure_schema()
        finished_at = _now()
        source_updated_at = _now()
        cleanup_prefixes = tuple(
            prefix for prefix in (cleanup_document_id_prefixes or ()) if prefix
        )
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
                    cleanup_prefixes,
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
            if self._should_fail_active_running_job(conn, running_row):
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

    def _should_fail_active_running_job(self, conn, row) -> bool:
        if self._is_stale_running_job(row):
            return True
        owner_id = row["owner_id"] if "owner_id" in row.keys() else ""
        if not owner_id:
            return self._should_recover_unowned_running_job(row)
        if owner_id == self.sync_owner_id:
            return False
        owner_row = conn.execute(
            "SELECT * FROM sync_job_owners WHERE owner_id = ?",
            (owner_id,),
        ).fetchone()
        if not owner_row:
            return False
        return not self._is_process_alive(owner_row["process_id"])

    def _should_recover_startup_running_job(self, conn, row) -> bool:
        owner_id = row["owner_id"] if "owner_id" in row.keys() else ""
        if owner_id:
            owner_row = conn.execute(
                "SELECT * FROM sync_job_owners WHERE owner_id = ?",
                (owner_id,),
            ).fetchone()
            if owner_row:
                if owner_id == self.sync_owner_id:
                    return self._is_stale_running_job(row)
                return not self._is_process_alive(owner_row["process_id"])
            return self._is_stale_running_job(row)
        return self._should_recover_unowned_running_job(row)

    def _should_recover_unowned_running_job(self, row) -> bool:
        if self.unowned_running_job_grace_seconds <= 0:
            return True
        timestamp = row["heartbeat_at"] or row["started_at"]
        parsed = self._parse_timestamp(timestamp)
        if not parsed:
            return True
        return datetime.now(timezone.utc) - parsed > timedelta(
            seconds=self.unowned_running_job_grace_seconds
        )

    def _touch_sync_owner(self, conn, timestamp: str):
        process_id = os.getpid()
        conn.execute(
            """
            INSERT INTO sync_job_owners (owner_id, process_id, started_at, heartbeat_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(owner_id) DO UPDATE SET
                process_id = excluded.process_id,
                heartbeat_at = excluded.heartbeat_at
            """,
            (self.sync_owner_id, process_id, timestamp, timestamp),
        )

    @staticmethod
    def _is_process_alive(process_id: int) -> bool:
        try:
            os.kill(int(process_id), 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError as error:
            if error.errno == ESRCH:
                return False
            if error.errno == EPERM:
                return True
            return False
        except ValueError:
            return False
        return True

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
        document_id_prefixes: tuple[str, ...] = (),
    ) -> list[str]:
        marker_column = "last_seen_sync_id" if last_seen_sync_id else "last_seen_at"
        marker_value = last_seen_sync_id or last_seen_at
        chunk_prefix_clause = ""
        document_prefix_clause = ""
        prefix_params: tuple[object, ...] = ()
        if document_id_prefixes:
            chunk_prefix_clause = (
                "AND ("
                + " OR ".join(
                    "substr(d.document_id, 1, ?) = ?" for _ in document_id_prefixes
                )
                + ")"
            )
            document_prefix_clause = (
                "AND ("
                + " OR ".join(
                    "substr(document_id, 1, ?) = ?" for _ in document_id_prefixes
                )
                + ")"
            )
            prefix_params = tuple(
                item
                for prefix in document_id_prefixes
                for item in (len(prefix), prefix)
            )
        # marker_column is selected from a fixed allowlist; prefix clauses are
        # generated from substr placeholders and bind prefix values separately.
        stale_chunk_lines = [
            "SELECT c.chunk_id FROM chunks c",
            "JOIN documents d ON d.document_id = c.document_id",
            "    AND d.source_id = c.source_id",
            "WHERE d.source_id = ?",
            "  AND COALESCE(d.deleted_at, '') = ''",
            "  AND COALESCE(d." + marker_column + ", '') != ?",
        ]
        if chunk_prefix_clause:
            stale_chunk_lines.append(chunk_prefix_clause)
        stale_chunk_lines.append("ORDER BY c.document_id, c.chunk_index")
        stale_chunk_query = "\n".join(stale_chunk_lines)
        chunk_rows = conn.execute(
            stale_chunk_query,
            (source_id, marker_value, *prefix_params),
        ).fetchall()
        tombstone_lines = [
            "UPDATE documents",
            "SET deleted_at = ?",
            "WHERE source_id = ?",
            "  AND COALESCE(deleted_at, '') = ''",
            "  AND COALESCE(" + marker_column + ", '') != ?",
        ]
        if document_prefix_clause:
            tombstone_lines.append(document_prefix_clause)
        tombstone_query = "\n".join(tombstone_lines)
        conn.execute(
            tombstone_query,
            (deleted_at, source_id, marker_value, *prefix_params),
        )
        return [row["chunk_id"] for row in chunk_rows]

    @staticmethod
    def _insert_chunks(conn, chunks: list[ChunkModel]):
        conn.executemany(
            """
            INSERT INTO chunks (
                chunk_id, document_id, source_id, title, text, url, path,
                chunk_index, line_start, line_end, version_id, content_hash, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    chunk.version_id,
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
    def _source_from_row(row) -> SourceModel | None:
        try:
            source_type = SourceType(row["source_type"])
            sync_status = SyncStatus(row["sync_status"])
        except ValueError:
            return None
        return SourceModel(
            source_id=row["source_id"],
            source_type=source_type,
            name=row["name"],
            enabled=bool(row["enabled"]),
            auth_ref=row["auth_ref"],
            sync_status=sync_status,
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
            version_id=row["version_id"],
            content_hash=row["content_hash"],
            updated_at=row["updated_at"],
        )
