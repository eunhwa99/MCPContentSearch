import sqlite3
import uuid
from datetime import datetime, timezone
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

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

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
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    url TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    date TEXT NOT NULL,
                    path TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
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
                """
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
        existing = self.get_source(source.source_id)
        if not existing:
            return self.upsert_source(source)
        return self.upsert_source(
            source.model_copy(
                update={
                    "sync_status": existing.sync_status,
                    "last_synced_at": existing.last_synced_at,
                    "last_error": existing.last_error,
                    "created_at": existing.created_at,
                }
            )
        )

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
                    job_id, source_id, status, started_at, finished_at,
                    total_documents, processed_documents, indexed_chunks,
                    skipped_documents, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.source_id,
                    job.status.value,
                    job.started_at,
                    job.finished_at,
                    job.total_documents,
                    job.processed_documents,
                    job.indexed_chunks,
                    job.skipped_documents,
                    job.error_message,
                ),
            )
        return job

    def update_sync_job(self, job_id: str, **updates) -> SyncJobModel:
        job = self.get_sync_job(job_id)
        if not job:
            raise ValueError(f"Unknown sync job: {job_id}")
        if "status" in updates and updates["status"] in {SyncJobStatus.SUCCEEDED, SyncJobStatus.FAILED}:
            updates.setdefault("finished_at", _now())
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

    def get_sync_job(self, job_id: str) -> Optional[SyncJobModel]:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job_from_row(row) if row else None

    def get_latest_sync_job(self, source_id: str) -> Optional[SyncJobModel]:
        self.ensure_schema()
        with self._connect() as conn:
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
        with self._connect() as conn:
            self._upsert_document(conn, normalized)
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            self._insert_chunks(conn, chunk_list)
        return normalized

    def get_document(self, document_id: str) -> Optional[DocumentModel]:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE document_id = ?", (document_id,)).fetchone()
        return self._document_from_row(row) if row else None

    def get_document_content_hash(self, document_id: str) -> str:
        document = self.get_document(document_id)
        return document.content_hash if document else ""

    def replace_document_chunks(self, document_id: str, chunks: Iterable[ChunkModel]):
        self.ensure_schema()
        with self._connect() as conn:
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            self._insert_chunks(conn, list(chunks))

    def get_chunk(self, chunk_id: str) -> Optional[ChunkModel]:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
        return self._chunk_from_row(row) if row else None

    def list_chunks_for_document(self, document_id: str) -> list[ChunkModel]:
        self.ensure_schema()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM chunks WHERE document_id = ? ORDER BY chunk_index",
                (document_id,),
            ).fetchall()
        return [self._chunk_from_row(row) for row in rows]

    def list_chunks(self, source_ids: Optional[list[str]] = None) -> list[ChunkModel]:
        self.ensure_schema()
        with self._connect() as conn:
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                rows = conn.execute(
                    f"SELECT * FROM chunks WHERE source_id IN ({placeholders}) ORDER BY document_id, chunk_index",
                    source_ids,
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM chunks ORDER BY document_id, chunk_index").fetchall()
        return [self._chunk_from_row(row) for row in rows]

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _normalize_document(document: DocumentModel) -> DocumentModel:
        content_hash = document.content_hash or ContentHasher.hash_content(document.content)
        return document.model_copy(
            update={
                "document_id": document.document_id or document.id,
                "path": document.path or document.title,
                "updated_at": document.updated_at or document.date,
                "content_hash": content_hash,
            }
        )

    @staticmethod
    def _upsert_document(conn, document: DocumentModel):
        conn.execute(
            """
            INSERT INTO documents (
                document_id, source_id, title, content, url, platform, date,
                path, updated_at, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                source_id = excluded.source_id,
                title = excluded.title,
                content = excluded.content,
                url = excluded.url,
                platform = excluded.platform,
                date = excluded.date,
                path = excluded.path,
                updated_at = excluded.updated_at,
                content_hash = excluded.content_hash
            """,
            (
                document.document_id or document.id,
                document.source_id,
                document.title,
                document.content,
                document.url,
                document.platform,
                document.date,
                document.path,
                document.updated_at,
                document.content_hash,
            ),
        )

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
            title=row["title"],
            content=row["content"],
            url=row["url"],
            platform=row["platform"],
            date=row["date"],
            path=row["path"],
            updated_at=row["updated_at"],
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
