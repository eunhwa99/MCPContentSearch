import base64
import binascii
import ctypes
import json
import os
import sqlite3
import stat
import sys
import uuid
from contextlib import contextmanager
from errno import EPERM, ESRCH
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Iterable, Iterator, Optional, Sequence

from core.error_sanitizer import sanitize_error_text
from core.models import (
    ChunkModel,
    DocumentSortBy,
    DocumentModel,
    SearchFilters,
    SortOrder,
    SourceModel,
    SourceType,
    SyncJobModel,
    SyncJobStatus,
    SyncStatus,
)
from core.sync_lifecycle import normalize_auth_ref, normalize_sync_job_phase
from core.utils import ContentHasher


ORPHANED_SYNC_JOB_RECOVERY_MESSAGE = (
    "Previous running sync job was recovered after its execution owner stopped "
    "responding; start sync again."
)
_DARWIN_PROC_PIDTBSDINFO = 3
_TRUSTED_STICKY_TEMP_DIRECTORIES = frozenset(
    {
        # Exact trust-policy identities only; this code does not create temp paths.
        Path("/tmp"),  # nosec B108
        Path("/var/tmp"),  # nosec B108
        Path("/private/tmp"),
        Path("/private/var/tmp"),
    }
)


def _current_uid() -> int:
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        raise RuntimeError(
            "Private career storage owner checks are unavailable. "
            "Move or recreate storage on a supported local filesystem."
        )
    return int(getuid())


def _private_storage_error(
    path: Path,
    problem: str,
    guidance: str,
) -> RuntimeError:
    safe_name = path.name or path.anchor or "storage"
    return RuntimeError(
        f"Private career storage {problem} for '{safe_name}'. {guidance}"
    )


def _private_absolute_path(path: Path | str) -> Path:
    raw_path = Path(path)
    if ".." in raw_path.parts:
        raise _private_storage_error(
            raw_path,
            "rejected parent traversal",
            "Move or recreate it at an absolute owner-only path.",
        )
    return Path(os.path.abspath(os.fspath(raw_path)))


def _private_directory_open_flags(path: Path) -> int:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if no_follow is None or directory_flag is None:
        raise _private_storage_error(
            path,
            "cannot perform no-follow directory checks",
            "Move or recreate it on a supported local filesystem.",
        )
    return os.O_RDONLY | directory_flag | no_follow | getattr(os, "O_CLOEXEC", 0)


def _trusted_sticky_temp_directory(path: Path, file_stat) -> bool:
    mode = stat.S_IMODE(file_stat.st_mode)
    return (
        path in _TRUSTED_STICKY_TEMP_DIRECTORIES
        and file_stat.st_uid == 0
        and bool(file_stat.st_mode & stat.S_ISVTX)
        and bool(mode & 0o022)
    )


def _validate_private_ancestor_descriptor(path: Path, descriptor: int) -> None:
    file_stat = os.fstat(descriptor)
    if not stat.S_ISDIR(file_stat.st_mode):
        raise _private_storage_error(
            path,
            "ancestor is not a directory",
            "Move or recreate storage under trusted directories.",
        )
    if file_stat.st_uid not in {0, _current_uid()}:
        raise _private_storage_error(
            path,
            "ancestor is not owned by current user or root",
            "Move or recreate storage under current-user or root-owned directories.",
        )
    if stat.S_IMODE(file_stat.st_mode) & 0o022 and not _trusted_sticky_temp_directory(
        path,
        file_stat,
    ):
        raise _private_storage_error(
            path,
            "ancestor is group/world-writable",
            "Move it or remove group/other write permissions manually.",
        )


def _open_private_directory_tree(path: Path | str) -> tuple[Path, int]:
    """Open/create a directory through no-follow descriptor-relative traversal."""
    resolved = _private_absolute_path(path)
    flags = _private_directory_open_flags(resolved)
    descriptor = os.open(os.sep, flags)
    try:
        current_path = Path(os.sep)
        _validate_private_ancestor_descriptor(current_path, descriptor)
        for component in resolved.parts[1:]:
            try:
                child_descriptor = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                except OSError:
                    raise _private_storage_error(
                        resolved,
                        "directory creation failed",
                        "Move or recreate it as an owner-only directory.",
                    ) from None
                try:
                    child_descriptor = os.open(component, flags, dir_fd=descriptor)
                except OSError:
                    raise _private_storage_error(
                        resolved,
                        "rejected symlink or unsafe directory ancestry",
                        "Move or recreate it without symlinked ancestors.",
                    ) from None
            except OSError:
                raise _private_storage_error(
                    resolved,
                    "rejected symlink or unsafe directory ancestry",
                    "Move or recreate it without symlinked ancestors.",
                ) from None
            os.close(descriptor)
            descriptor = child_descriptor
            current_path = current_path / component
            _validate_private_ancestor_descriptor(current_path, descriptor)
        return resolved, descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _validate_private_directory_descriptor(path: Path, descriptor: int) -> None:
    file_stat = os.fstat(descriptor)
    if not stat.S_ISDIR(file_stat.st_mode):
        raise _private_storage_error(
            path,
            "path is not a directory",
            "Move or recreate it as an owner-only directory.",
        )
    if file_stat.st_uid != _current_uid():
        raise _private_storage_error(
            path,
            "directory is not owned by current user",
            "Move or recreate it under the current user.",
        )
    if stat.S_IMODE(file_stat.st_mode) != 0o700:
        raise _private_storage_error(
            path,
            "directory mode must be 0700",
            "Run chmod 700 manually, move, or recreate it.",
        )


def prepare_private_directory(path: Path | str) -> Path:
    resolved = _private_absolute_path(path)
    for directory in (resolved.parent, resolved):
        validated_path, descriptor = _open_private_directory_tree(directory)
        try:
            _validate_private_directory_descriptor(validated_path, descriptor)
        finally:
            os.close(descriptor)
    return resolved


def prepare_private_sqlite_path(path: Path | str) -> Path:
    resolved = _private_absolute_path(path)
    parent, parent_descriptor = _open_private_directory_tree(resolved.parent)
    try:
        _validate_private_directory_descriptor(parent, parent_descriptor)
    except BaseException:
        os.close(parent_descriptor)
        raise
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        os.close(parent_descriptor)
        raise _private_storage_error(
            resolved,
            "cannot perform no-follow file checks",
            "Move or recreate it on a supported local filesystem.",
        )
    flags = (
        os.O_RDWR
        | no_follow
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = -1
    try:
        try:
            descriptor = os.open(resolved.name, flags, dir_fd=parent_descriptor)
        except FileNotFoundError:
            descriptor = os.open(
                resolved.name,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent_descriptor,
            )
        file_stat = os.fstat(descriptor)
    except OSError:
        raise _private_storage_error(
            resolved,
            "rejected symlink or unsafe file",
            "Move or recreate it as an owner-only regular SQLite file.",
        ) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_descriptor)
    if not stat.S_ISREG(file_stat.st_mode):
        raise _private_storage_error(
            resolved,
            "path is not a regular file",
            "Move or recreate it as an owner-only SQLite file.",
        )
    if file_stat.st_uid != _current_uid():
        raise _private_storage_error(
            resolved,
            "file is not owned by current user",
            "Move or recreate it under the current user.",
        )
    if stat.S_IMODE(file_stat.st_mode) != 0o600:
        raise _private_storage_error(
            resolved,
            "file mode must be 0600",
            "Run chmod 600 manually, move, or recreate it.",
        )
    return resolved


@contextmanager
def private_creation_umask():
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


class _DarwinProcBSDInfo(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("xstatus", ctypes.c_uint32),
        ("pid", ctypes.c_uint32),
        ("ppid", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("ruid", ctypes.c_uint32),
        ("rgid", ctypes.c_uint32),
        ("svuid", ctypes.c_uint32),
        ("svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("comm", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("nfiles", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32),
        ("pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("nice", ctypes.c_int32),
        ("start_tvsec", ctypes.c_uint64),
        ("start_tvusec", ctypes.c_uint64),
    ]


OBSIDIAN_REFRESH_CLEARABLE_ERRORS = (
    "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
    "is not set or is not an existing directory.",
    "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
    "must be an absolute path.",
    "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
    "must not be a symlink.",
)
OBSIDIAN_INCOMPLETE_SNAPSHOT_PUBLIC_ERROR = "Obsidian vault snapshot was incomplete because one or more notes could not be read."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_lifecycle_text(value: object) -> str:
    if not value:
        return ""
    return sanitize_error_text(value)


def _sanitize_source_lifecycle(source: SourceModel) -> SourceModel:
    return source.model_copy(
        update={
            "auth_ref": normalize_auth_ref(source.auth_ref),
            "last_error": _sanitize_lifecycle_text(source.last_error),
            "stale_cleanup_disabled_reason": _sanitize_lifecycle_text(
                source.stale_cleanup_disabled_reason
            ),
        }
    )


class MetadataStore:
    """SQLite-backed metadata store for ContextWiki sources, jobs, docs, and chunks."""

    def __init__(
        self,
        db_path: Path | str,
        running_job_timeout_seconds: int = 24 * 60 * 60,
        sync_owner_id: str | None = None,
        unowned_running_job_grace_seconds: int = 60,
        max_concurrent_sync_jobs: int = 1,
        require_private: bool = False,
    ):
        # Default stays single-flight for non-worker MetadataStore callers.
        # The durable sync worker raises this to CONTEXTWIKI_SYNC_WORKER_MAX_CONCURRENT
        # (default 2) in create_worker.
        self.require_private = require_private
        self.db_path = (
            prepare_private_sqlite_path(db_path) if require_private else Path(db_path)
        )
        self.running_job_timeout_seconds = running_job_timeout_seconds
        self.sync_owner_id = sync_owner_id or str(uuid.uuid4())
        self.unowned_running_job_grace_seconds = unowned_running_job_grace_seconds
        self._max_concurrent_sync_jobs = self._validate_max_concurrent_sync_jobs(
            max_concurrent_sync_jobs
        )
        self._cached_process_id = 0
        self._cached_process_start_id = ""
        self._schema_ready = False
        self._schema_lock = RLock()

    @staticmethod
    def _validate_max_concurrent_sync_jobs(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 8:
            raise ValueError(
                "max_concurrent_sync_jobs must be an integer between 1 and 8"
            )
        return value

    @property
    def max_concurrent_sync_jobs(self) -> int:
        return self._max_concurrent_sync_jobs

    @max_concurrent_sync_jobs.setter
    def max_concurrent_sync_jobs(self, value: int) -> None:
        self._max_concurrent_sync_jobs = self._validate_max_concurrent_sync_jobs(value)

    def ensure_schema(self):
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            if self.require_private:
                prepare_private_sqlite_path(self.db_path)
            else:
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
                    stale_cleanup_disabled_reason TEXT NOT NULL DEFAULT '',
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
                    parsed_documents INTEGER NOT NULL DEFAULT 0,
                    updated_documents INTEGER NOT NULL DEFAULT 0,
                    created_chunks INTEGER NOT NULL DEFAULT 0,
                    updated_chunks INTEGER NOT NULL DEFAULT 0,
                    skipped_chunks INTEGER NOT NULL DEFAULT 0,
                    embeddings_generated INTEGER NOT NULL DEFAULT 0,
                    embeddings_reused INTEGER NOT NULL DEFAULT 0,
                    parsing_failures INTEGER NOT NULL DEFAULT 0,
                    indexing_latency_ms REAL NOT NULL DEFAULT 0,
                    phase TEXT NOT NULL DEFAULT '',
                    upstream_total INTEGER NOT NULL DEFAULT 0,
                    upstream_done INTEGER NOT NULL DEFAULT 0,
                    upstream_total_pages INTEGER NOT NULL DEFAULT 0,
                    upstream_fetched_pages INTEGER NOT NULL DEFAULT 0,
                    last_progress_at TEXT NOT NULL DEFAULT '',
                    status_message TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sync_job_owners (
                    owner_id TEXT PRIMARY KEY,
                    process_id INTEGER NOT NULL,
                    process_start_id TEXT NOT NULL DEFAULT '',
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
                    published_at TEXT NOT NULL DEFAULT '',
                    modified_at TEXT NOT NULL DEFAULT '',
                    indexed_at TEXT NOT NULL DEFAULT '',
                    date_provenance TEXT NOT NULL DEFAULT '',
                    last_seen_at TEXT NOT NULL DEFAULT '',
                    last_seen_sync_id TEXT NOT NULL DEFAULT '',
                    deleted_at TEXT NOT NULL DEFAULT '',
                    version_id TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL,
                    document_version_id TEXT NOT NULL DEFAULT '',
                    evidence_source_type TEXT NOT NULL DEFAULT '',
                    experience_type TEXT NOT NULL DEFAULT 'unknown',
                    file_name TEXT NOT NULL DEFAULT '',
                    document_title TEXT NOT NULL DEFAULT '',
                    section_title TEXT NOT NULL DEFAULT '',
                    parent_section_title TEXT NOT NULL DEFAULT '',
                    exact_quote TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    company TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT '',
                    project TEXT NOT NULL DEFAULT '',
                    start_date TEXT NOT NULL DEFAULT '',
                    end_date TEXT NOT NULL DEFAULT ''
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
                    updated_at TEXT NOT NULL,
                    document_version_id TEXT NOT NULL DEFAULT '',
                    evidence_source_type TEXT NOT NULL DEFAULT '',
                    experience_type TEXT NOT NULL DEFAULT 'unknown',
                    file_name TEXT NOT NULL DEFAULT '',
                    document_title TEXT NOT NULL DEFAULT '',
                    section_title TEXT NOT NULL DEFAULT '',
                    parent_section_title TEXT NOT NULL DEFAULT '',
                    exact_quote TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    company TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT '',
                    project TEXT NOT NULL DEFAULT '',
                    start_date TEXT NOT NULL DEFAULT '',
                    end_date TEXT NOT NULL DEFAULT ''
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
                    recorded_at TEXT NOT NULL,
                    vector_cleanup_at TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS vector_write_intents (
                    chunk_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (chunk_id, source_id)
                );

                CREATE TABLE IF NOT EXISTS vector_metadata_refresh_intents (
                    chunk_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (chunk_id, source_id)
                );
                """
                )
                # executescript may commit before running its statements. Acquire the
                # migration write lock afterward so every check-and-alter stays atomic.
                conn.execute("BEGIN IMMEDIATE")
                self._ensure_columns(
                    conn,
                    "sources",
                    {
                        "stale_cleanup_disabled_reason": "TEXT NOT NULL DEFAULT ''",
                    },
                )
                self._ensure_columns(
                    conn,
                    "documents",
                    {
                        "external_id": "TEXT NOT NULL DEFAULT ''",
                        "canonical_url": "TEXT NOT NULL DEFAULT ''",
                        "published_at": "TEXT NOT NULL DEFAULT ''",
                        "modified_at": "TEXT NOT NULL DEFAULT ''",
                        "indexed_at": "TEXT NOT NULL DEFAULT ''",
                        "date_provenance": "TEXT NOT NULL DEFAULT ''",
                        "last_seen_at": "TEXT NOT NULL DEFAULT ''",
                        "last_seen_sync_id": "TEXT NOT NULL DEFAULT ''",
                        "deleted_at": "TEXT NOT NULL DEFAULT ''",
                        "version_id": "TEXT NOT NULL DEFAULT ''",
                        "document_version_id": "TEXT NOT NULL DEFAULT ''",
                        "evidence_source_type": "TEXT NOT NULL DEFAULT ''",
                        "experience_type": "TEXT NOT NULL DEFAULT 'unknown'",
                        "file_name": "TEXT NOT NULL DEFAULT ''",
                        "document_title": "TEXT NOT NULL DEFAULT ''",
                        "section_title": "TEXT NOT NULL DEFAULT ''",
                        "parent_section_title": "TEXT NOT NULL DEFAULT ''",
                        "exact_quote": "TEXT NOT NULL DEFAULT ''",
                        "created_at": "TEXT NOT NULL DEFAULT ''",
                        "company": "TEXT NOT NULL DEFAULT ''",
                        "role": "TEXT NOT NULL DEFAULT ''",
                        "project": "TEXT NOT NULL DEFAULT ''",
                        "start_date": "TEXT NOT NULL DEFAULT ''",
                        "end_date": "TEXT NOT NULL DEFAULT ''",
                    },
                )
                self._ensure_columns(
                    conn,
                    "sync_jobs",
                    {
                        "owner_id": "TEXT NOT NULL DEFAULT ''",
                        "heartbeat_at": "TEXT NOT NULL DEFAULT ''",
                        "phase": "TEXT NOT NULL DEFAULT ''",
                        "upstream_total": "INTEGER NOT NULL DEFAULT 0",
                        "upstream_done": "INTEGER NOT NULL DEFAULT 0",
                        "upstream_total_pages": "INTEGER NOT NULL DEFAULT 0",
                        "upstream_fetched_pages": "INTEGER NOT NULL DEFAULT 0",
                        "last_progress_at": "TEXT NOT NULL DEFAULT ''",
                        "status_message": "TEXT NOT NULL DEFAULT ''",
                        "parsed_documents": "INTEGER NOT NULL DEFAULT 0",
                        "updated_documents": "INTEGER NOT NULL DEFAULT 0",
                        "created_chunks": "INTEGER NOT NULL DEFAULT 0",
                        "updated_chunks": "INTEGER NOT NULL DEFAULT 0",
                        "skipped_chunks": "INTEGER NOT NULL DEFAULT 0",
                        "embeddings_generated": "INTEGER NOT NULL DEFAULT 0",
                        "embeddings_reused": "INTEGER NOT NULL DEFAULT 0",
                        "parsing_failures": "INTEGER NOT NULL DEFAULT 0",
                        "indexing_latency_ms": "REAL NOT NULL DEFAULT 0",
                    },
                )
                # Prefer new columns after migrate; copy legacy page counters when
                # additive columns still hold their DEFAULT 0 from ALTER TABLE.
                conn.execute(
                    """
                    UPDATE sync_jobs
                    SET upstream_total = upstream_total_pages
                    WHERE upstream_total = 0 AND upstream_total_pages > 0
                    """
                )
                conn.execute(
                    """
                    UPDATE sync_jobs
                    SET upstream_done = upstream_fetched_pages
                    WHERE upstream_done = 0 AND upstream_fetched_pages > 0
                    """
                )
                self._ensure_columns(
                    conn,
                    "sync_job_owners",
                    {
                        "process_start_id": "TEXT NOT NULL DEFAULT ''",
                    },
                )
                self._ensure_columns(
                    conn,
                    "chunks",
                    {
                        "version_id": "TEXT NOT NULL DEFAULT ''",
                        "document_version_id": "TEXT NOT NULL DEFAULT ''",
                        "evidence_source_type": "TEXT NOT NULL DEFAULT ''",
                        "experience_type": "TEXT NOT NULL DEFAULT 'unknown'",
                        "file_name": "TEXT NOT NULL DEFAULT ''",
                        "document_title": "TEXT NOT NULL DEFAULT ''",
                        "section_title": "TEXT NOT NULL DEFAULT ''",
                        "parent_section_title": "TEXT NOT NULL DEFAULT ''",
                        "exact_quote": "TEXT NOT NULL DEFAULT ''",
                        "created_at": "TEXT NOT NULL DEFAULT ''",
                        "company": "TEXT NOT NULL DEFAULT ''",
                        "role": "TEXT NOT NULL DEFAULT ''",
                        "project": "TEXT NOT NULL DEFAULT ''",
                        "start_date": "TEXT NOT NULL DEFAULT ''",
                        "end_date": "TEXT NOT NULL DEFAULT ''",
                    },
                )
                self._ensure_columns(
                    conn,
                    "chunk_tombstones",
                    {
                        "vector_cleanup_at": "TEXT NOT NULL DEFAULT ''",
                    },
                )
                # Speeds batch fetch-reuse MIN(line_start) joins by document.
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_chunks_document_source
                    ON chunks(document_id, source_id)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_chunk_tombstones_cleanup
                    ON chunk_tombstones(source_id, vector_cleanup_at, recorded_at)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_chunk_tombstones_pending
                    ON chunk_tombstones(source_id, recorded_at, chunk_id)
                    WHERE vector_cleanup_at = ''
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_vector_write_intents_pending
                    ON vector_write_intents(source_id, recorded_at, chunk_id)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_vector_metadata_refresh_pending
                    ON vector_metadata_refresh_intents(
                        source_id, document_id, recorded_at, chunk_id
                    )
                    """
                )
            self._schema_ready = True

    def upsert_source(self, source: SourceModel) -> SourceModel:
        self.ensure_schema()
        source = _sanitize_source_lifecycle(source)
        existing = self.get_source(source.source_id)
        created_at = source.created_at or (existing.created_at if existing else _now())
        updated_at = _now()
        normalized = source.model_copy(
            update={"created_at": created_at, "updated_at": updated_at}
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sources (
                    source_id, source_type, name, enabled, auth_ref, sync_status,
                    last_synced_at, last_error, stale_cleanup_disabled_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    source_type = excluded.source_type,
                    name = excluded.name,
                    enabled = excluded.enabled,
                    auth_ref = excluded.auth_ref,
                    sync_status = excluded.sync_status,
                    last_synced_at = excluded.last_synced_at,
                    last_error = excluded.last_error,
                    stale_cleanup_disabled_reason = excluded.stale_cleanup_disabled_reason,
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
                    normalized.stale_cleanup_disabled_reason,
                    normalized.created_at,
                    normalized.updated_at,
                ),
            )
        return normalized

    def register_source(self, source: SourceModel) -> SourceModel:
        """Register static source config while preserving operational status."""
        self.ensure_schema()
        source = _sanitize_source_lifecycle(source)
        created_at = source.created_at or _now()
        updated_at = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sources (
                    source_id, source_type, name, enabled, auth_ref, sync_status,
                    last_synced_at, last_error, stale_cleanup_disabled_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    stale_cleanup_disabled_reason = CASE
                        WHEN excluded.enabled = 0 AND excluded.stale_cleanup_disabled_reason != ''
                        THEN excluded.stale_cleanup_disabled_reason
                        WHEN excluded.enabled = 1 AND sources.enabled = 0
                        THEN excluded.stale_cleanup_disabled_reason
                        ELSE sources.stale_cleanup_disabled_reason
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
                    source.stale_cleanup_disabled_reason,
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
            raise ValueError(
                f"Registered source has unsupported type: {source.source_id}"
            )
        return registered

    def get_source(self, source_id: str) -> Optional[SourceModel]:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sources WHERE source_id = ?", (source_id,)
            ).fetchone()
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
                    skipped_documents, phase, upstream_total, upstream_done,
                    upstream_total_pages, upstream_fetched_pages,
                    last_progress_at, status_message, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    job.phase,
                    job.upstream_total,
                    job.upstream_done,
                    job.upstream_total,
                    job.upstream_done,
                    job.last_progress_at,
                    job.status_message,
                    job.error_message,
                ),
            )
        return job

    def enqueue_sync_job(
        self,
        source_id: str,
        *,
        disabled_error_message: str = "",
        disabled_stale_cleanup_reason: str = "",
    ) -> tuple[SyncJobModel, bool]:
        """Atomically reuse active work or create a terminal/queued sync job."""
        self.ensure_schema()
        enqueued_at = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            source_row = conn.execute(
                "SELECT * FROM sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if not source_row:
                raise ValueError(f"Unknown source: {source_id}")

            active_row = self._resolve_active_running_job(conn, source_id, enqueued_at)
            if active_row is None:
                active_row = self._get_queued_sync_job(conn, source_id)
            if active_row is not None:
                self._mark_source_sync_active(conn, source_id, enqueued_at)
                return self._job_from_row(active_row), False

            if not bool(source_row["enabled"]):
                error_message = _sanitize_lifecycle_text(
                    disabled_error_message
                    or source_row["last_error"]
                    or source_row["stale_cleanup_disabled_reason"]
                    or f"Source {source_id} is disabled"
                )
                stale_cleanup_disabled_reason = _sanitize_lifecycle_text(
                    disabled_stale_cleanup_reason
                    or source_row["stale_cleanup_disabled_reason"]
                )
                job = SyncJobModel(
                    job_id=str(uuid.uuid4()),
                    source_id=source_id,
                    status=SyncJobStatus.FAILED,
                    started_at=enqueued_at,
                    finished_at=enqueued_at,
                    phase="failed",
                    last_progress_at=enqueued_at,
                    status_message=error_message,
                    error_message=error_message,
                )
                conn.execute(
                    """
                    INSERT INTO sync_jobs (
                        job_id, source_id, owner_id, status, started_at, heartbeat_at, finished_at,
                        total_documents, processed_documents, indexed_chunks,
                        skipped_documents, phase, upstream_total, upstream_done,
                        upstream_total_pages, upstream_fetched_pages,
                        last_progress_at, status_message, error_message
                    ) VALUES (?, ?, '', ?, ?, '', ?, 0, 0, 0, 0, ?, 0, 0, 0, 0, ?, ?, ?)
                    """,
                    (
                        job.job_id,
                        job.source_id,
                        job.status.value,
                        job.started_at,
                        job.finished_at,
                        job.phase,
                        job.last_progress_at,
                        job.status_message,
                        job.error_message,
                    ),
                )
                conn.execute(
                    """
                    UPDATE sources SET
                        sync_status = ?,
                        last_error = ?,
                        stale_cleanup_disabled_reason = ?,
                        updated_at = ?
                    WHERE source_id = ?
                    """,
                    (
                        SyncStatus.FAILED.value,
                        error_message,
                        stale_cleanup_disabled_reason,
                        enqueued_at,
                        source_id,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM sync_jobs WHERE job_id = ?",
                    (job.job_id,),
                ).fetchone()
                return self._job_from_row(row), True

            job = SyncJobModel(
                job_id=str(uuid.uuid4()),
                source_id=source_id,
                status=SyncJobStatus.QUEUED,
                started_at=enqueued_at,
            )
            conn.execute(
                """
                INSERT INTO sync_jobs (
                    job_id, source_id, owner_id, status, started_at, heartbeat_at, finished_at,
                    total_documents, processed_documents, indexed_chunks,
                    skipped_documents, phase, upstream_total, upstream_done,
                    upstream_total_pages, upstream_fetched_pages,
                    last_progress_at, status_message, error_message
                ) VALUES (?, ?, '', ?, ?, '', '', 0, 0, 0, 0, '', 0, 0, 0, 0, '', '', '')
                """,
                (
                    job.job_id,
                    job.source_id,
                    job.status.value,
                    job.started_at,
                ),
            )
            self._mark_source_sync_active(conn, source_id, enqueued_at)
            row = conn.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?",
                (job.job_id,),
            ).fetchone()
        return self._job_from_row(row), True

    def claim_next_sync_job(
        self,
        source_ids: Iterable[str] | None = None,
    ) -> Optional[SyncJobModel]:
        """Atomically claim the oldest queued job under the concurrency budget."""
        self.ensure_schema()
        max_concurrent = self._validate_max_concurrent_sync_jobs(
            self.max_concurrent_sync_jobs
        )
        scoped_source_ids = tuple(
            dict.fromkeys(str(source_id) for source_id in source_ids or () if source_id)
        )
        if source_ids is not None and not scoped_source_ids:
            return None
        claimed_at = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            scoped_source_id_set = set(scoped_source_ids)
            self._reconcile_global_running_jobs(conn, claimed_at)
            running_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM sync_jobs
                WHERE status = ?
                """,
                (SyncJobStatus.RUNNING.value,),
            ).fetchone()["count"]
            if running_count >= max_concurrent:
                return None
            rows = conn.execute(
                """
                SELECT j.*
                FROM sync_jobs j
                JOIN sources s ON s.source_id = j.source_id
                WHERE j.status = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM sync_jobs active
                      WHERE active.source_id = j.source_id
                        AND active.status = ?
                )
                ORDER BY j.started_at, j.rowid
                """,
                (
                    SyncJobStatus.QUEUED.value,
                    SyncJobStatus.RUNNING.value,
                ),
            ).fetchall()
            row = next(
                (
                    candidate
                    for candidate in rows
                    if not scoped_source_id_set
                    or candidate["source_id"] in scoped_source_id_set
                ),
                None,
            )
            if row is None:
                return None

            cursor = conn.execute(
                """
                UPDATE sync_jobs SET
                    owner_id = ?,
                    status = ?,
                    heartbeat_at = ?,
                    phase = ?,
                    last_progress_at = ?,
                    status_message = ?,
                    error_message = ''
                WHERE job_id = ? AND status = ?
                """,
                (
                    self.sync_owner_id,
                    SyncJobStatus.RUNNING.value,
                    claimed_at,
                    "starting",
                    claimed_at,
                    "Sync worker claimed the queued job.",
                    row["job_id"],
                    SyncJobStatus.QUEUED.value,
                ),
            )
            if cursor.rowcount != 1:
                return None
            self._touch_sync_owner(conn, claimed_at)
            self._mark_source_sync_active(conn, row["source_id"], claimed_at)
            claimed_row = conn.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()
        return self._job_from_row(claimed_row) if claimed_row else None

    def _reconcile_global_running_jobs(
        self,
        conn: sqlite3.Connection,
        checked_at: str,
    ) -> None:
        """Recover only definitively inactive owners before the concurrency claim gate."""
        running_source_rows = conn.execute(
            """
            SELECT DISTINCT source_id
            FROM sync_jobs
            WHERE status = ?
            """,
            (SyncJobStatus.RUNNING.value,),
        ).fetchall()
        for running_source_row in running_source_rows:
            running_source_id = running_source_row["source_id"]
            active_row = self._resolve_active_running_job(
                conn,
                running_source_id,
                checked_at,
                failure_reason=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
            )
            if active_row is None:
                self._reconcile_source_after_inactive_job(
                    conn,
                    running_source_id,
                    checked_at,
                    ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
                )

    def get_owned_running_sync_job(self, job_id: str) -> Optional[SyncJobModel]:
        """Return a running job only when this store's worker owner claimed it."""
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM sync_jobs
                WHERE job_id = ? AND status = ? AND owner_id = ?
                """,
                (
                    job_id,
                    SyncJobStatus.RUNNING.value,
                    self.sync_owner_id,
                ),
            ).fetchone()
        return self._job_from_row(row) if row else None

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
            if active_row is None:
                active_row = self._get_queued_sync_job(conn, source_id)
            if active_row:
                self._mark_source_sync_active(conn, source_id, started_at)
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
                    skipped_documents, phase, upstream_total, upstream_done,
                    upstream_total_pages, upstream_fetched_pages,
                    last_progress_at, status_message, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    job.phase,
                    job.upstream_total,
                    job.upstream_done,
                    job.upstream_total,
                    job.upstream_done,
                    job.last_progress_at,
                    job.status_message,
                    job.error_message,
                ),
            )
            self._touch_sync_owner(conn, started_at)
            self._mark_source_sync_active(conn, source_id, started_at)
        return job, True

    def touch_sync_job(self, job_id: str) -> Optional[SyncJobModel]:
        """Refresh a running job heartbeat without changing the public job contract."""
        self.ensure_schema()
        heartbeat_at = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
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
                row = conn.execute(
                    "SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                return self._job_from_row(row)
            conn.execute(
                """
                UPDATE sync_jobs SET heartbeat_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (heartbeat_at, job_id, SyncJobStatus.RUNNING.value),
            )
            self._touch_sync_owner(conn, heartbeat_at)
            row = conn.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._job_from_row(row) if row else None

    def validate_running_job_document(
        self, job_id: str, document: DocumentModel
    ) -> Optional[SyncJobModel]:
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
                row = conn.execute(
                    "SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
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
            self._touch_sync_owner(conn, heartbeat_at)
            row = conn.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
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
            self._validate_chunks_for_document(
                chunk_list, document_id, normalized.source_id
            )
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
                row = conn.execute(
                    "SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                return None, self._job_from_row(row)

            self._claim_document(conn, normalized, job_id, heartbeat_at)
            conn.execute(
                """
                UPDATE sync_jobs SET heartbeat_at = ?
                WHERE job_id = ?
                """,
                (heartbeat_at, job_id),
            )
            self._touch_sync_owner(conn, heartbeat_at)
            self._upsert_document(conn, normalized)
            self._record_chunk_tombstones_for_document(
                conn, document_id, normalized.source_id
            )
            conn.execute(
                "DELETE FROM chunks WHERE document_id = ? AND source_id = ?",
                (document_id, normalized.source_id),
            )
            self._insert_chunks(conn, chunk_list)
            self._resolve_vector_state_for_active_chunks(
                conn,
                chunk_list,
                normalized.source_id,
            )
            job_row = conn.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return normalized, self._job_from_row(job_row)

    def update_sync_job(self, job_id: str, **updates) -> SyncJobModel:
        job = self.get_sync_job(job_id)
        if not job:
            raise ValueError(f"Unknown sync job: {job_id}")
        if updates.get("status") in {
            SyncJobStatus.RUNNING,
            SyncJobStatus.RUNNING.value,
        }:
            raise ValueError("Use begin_sync_job() to start a running sync job")
        if updates.get("status") in {
            SyncJobStatus.SUCCEEDED,
            SyncJobStatus.SUCCEEDED.value,
            SyncJobStatus.FAILED,
            SyncJobStatus.FAILED.value,
        }:
            raise ValueError("Use complete_successful_sync() or complete_failed_sync()")
        updates = dict(updates)
        if "phase" in updates:
            updates["phase"] = normalize_sync_job_phase(updates["phase"])
        for field in ("status_message", "error_message"):
            if field in updates:
                updates[field] = _sanitize_lifecycle_text(updates[field])
        updated = job.model_copy(update=updates)
        updated = updated.model_copy(
            update={
                "phase": normalize_sync_job_phase(updated.phase),
                "status_message": _sanitize_lifecycle_text(updated.status_message),
                "error_message": _sanitize_lifecycle_text(updated.error_message),
            }
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE sync_jobs SET
                    status = ?, started_at = ?, finished_at = ?, total_documents = ?,
                    processed_documents = ?, indexed_chunks = ?, skipped_documents = ?,
                    parsed_documents = ?, updated_documents = ?,
                    created_chunks = ?, updated_chunks = ?, skipped_chunks = ?,
                    embeddings_generated = ?, embeddings_reused = ?,
                    parsing_failures = ?, indexing_latency_ms = ?,
                    phase = ?, upstream_total = ?, upstream_done = ?,
                    upstream_total_pages = ?, upstream_fetched_pages = ?,
                    last_progress_at = ?, status_message = ?, error_message = ?
                WHERE job_id = ? AND status IN (?, ?)
                """,
                (
                    updated.status.value,
                    updated.started_at,
                    updated.finished_at,
                    updated.total_documents,
                    updated.processed_documents,
                    updated.indexed_chunks,
                    updated.skipped_documents,
                    updated.parsed_documents,
                    updated.updated_documents,
                    updated.created_chunks,
                    updated.updated_chunks,
                    updated.skipped_chunks,
                    updated.embeddings_generated,
                    updated.embeddings_reused,
                    updated.parsing_failures,
                    updated.indexing_latency_ms,
                    updated.phase,
                    updated.upstream_total,
                    updated.upstream_done,
                    updated.upstream_total,
                    updated.upstream_done,
                    updated.last_progress_at,
                    updated.status_message,
                    updated.error_message,
                    updated.job_id,
                    SyncJobStatus.QUEUED.value,
                    SyncJobStatus.RUNNING.value,
                ),
            )
            row = conn.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if cursor.rowcount == 0:
            if not row:
                raise ValueError(f"Unknown sync job: {job_id}")
            return self._job_from_row(row)
        return self._job_from_row(row)

    def complete_failed_sync(
        self,
        *,
        job_id: str,
        source_id: str,
        error_message: str,
        stale_cleanup_disabled_reason: str = "",
        total_documents: int | None = None,
        processed_documents: int | None = None,
        indexed_chunks: int | None = None,
        skipped_documents: int | None = None,
        parsed_documents: int | None = None,
        updated_documents: int | None = None,
        created_chunks: int | None = None,
        updated_chunks: int | None = None,
        skipped_chunks: int | None = None,
        embeddings_generated: int | None = None,
        embeddings_reused: int | None = None,
        parsing_failures: int | None = None,
        indexing_latency_ms: float | None = None,
    ) -> SyncJobModel:
        """Atomically fail a sync and persist its final available metrics."""
        self.ensure_schema()
        error_message = _sanitize_lifecycle_text(error_message)
        stale_cleanup_disabled_reason = _sanitize_lifecycle_text(
            stale_cleanup_disabled_reason
        )
        finished_at = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Unknown sync job: {job_id}")
            if row["source_id"] != source_id:
                raise ValueError(
                    f"Sync job {job_id} belongs to {row['source_id']}, not {source_id}"
                )
            if row["status"] not in {
                SyncJobStatus.QUEUED.value,
                SyncJobStatus.RUNNING.value,
            }:
                return self._job_from_row(row)

            conn.execute(
                """
                UPDATE sync_jobs SET
                    status = ?,
                    finished_at = ?,
                    total_documents = ?,
                    processed_documents = ?,
                    indexed_chunks = ?,
                    skipped_documents = ?,
                    parsed_documents = ?,
                    updated_documents = ?,
                    created_chunks = ?,
                    updated_chunks = ?,
                    skipped_chunks = ?,
                    embeddings_generated = ?,
                    embeddings_reused = ?,
                    parsing_failures = ?,
                    indexing_latency_ms = ?,
                    phase = ?,
                    last_progress_at = ?,
                    status_message = ?,
                    error_message = ?
                WHERE job_id = ?
                """,
                (
                    SyncJobStatus.FAILED.value,
                    finished_at,
                    (
                        row["total_documents"]
                        if total_documents is None
                        else total_documents
                    ),
                    (
                        row["processed_documents"]
                        if processed_documents is None
                        else processed_documents
                    ),
                    row["indexed_chunks"] if indexed_chunks is None else indexed_chunks,
                    (
                        row["skipped_documents"]
                        if skipped_documents is None
                        else skipped_documents
                    ),
                    (
                        row["parsed_documents"]
                        if parsed_documents is None
                        else parsed_documents
                    ),
                    (
                        row["updated_documents"]
                        if updated_documents is None
                        else updated_documents
                    ),
                    row["created_chunks"] if created_chunks is None else created_chunks,
                    row["updated_chunks"] if updated_chunks is None else updated_chunks,
                    row["skipped_chunks"] if skipped_chunks is None else skipped_chunks,
                    (
                        row["embeddings_generated"]
                        if embeddings_generated is None
                        else embeddings_generated
                    ),
                    (
                        row["embeddings_reused"]
                        if embeddings_reused is None
                        else embeddings_reused
                    ),
                    (
                        row["parsing_failures"]
                        if parsing_failures is None
                        else parsing_failures
                    ),
                    (
                        row["indexing_latency_ms"]
                        if indexing_latency_ms is None
                        else indexing_latency_ms
                    ),
                    "failed",
                    finished_at,
                    error_message,
                    error_message,
                    job_id,
                ),
            )
            conn.execute("DELETE FROM document_claims WHERE job_id = ?", (job_id,))
            active_row = self._resolve_active_running_job(conn, source_id, finished_at)
            queued_row = self._get_queued_sync_job(conn, source_id)
            if active_row or queued_row:
                self._mark_source_sync_active(conn, source_id, finished_at)
            else:
                conn.execute(
                    """
                    UPDATE sources SET
                        sync_status = ?,
                        last_error = ?,
                        stale_cleanup_disabled_reason = ?,
                        updated_at = ?
                    WHERE source_id = ?
                    """,
                    (
                        SyncStatus.FAILED.value,
                        error_message,
                        stale_cleanup_disabled_reason,
                        finished_at,
                        source_id,
                    ),
                )
            row = conn.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
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
        error_message = _sanitize_lifecycle_text(error_message)
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
                if (
                    scoped_source_id_set
                    and row["source_id"] not in scoped_source_id_set
                ):
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
                    WHERE source_id = ? AND status IN (?, ?)
                    LIMIT 1
                    """,
                    (
                        source_id,
                        SyncJobStatus.RUNNING.value,
                        SyncJobStatus.QUEUED.value,
                    ),
                ).fetchone()
                if active_row:
                    self._mark_source_sync_active(conn, source_id, finished_at)
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
            row = conn.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._job_from_row(row) if row else None

    def get_latest_sync_job(self, source_id: str) -> Optional[SyncJobModel]:
        self.ensure_schema()
        with self._connect() as conn:
            row, reconciliation_required = self._read_latest_sync_job_candidate(
                conn,
                source_id,
            )
        if not reconciliation_required:
            return self._job_from_row(row) if row else None

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._reconcile_latest_sync_job(
                conn,
                source_id,
                _now(),
            )
        return self._job_from_row(row) if row else None

    def _read_latest_sync_job_candidate(
        self,
        conn: sqlite3.Connection,
        source_id: str,
    ) -> tuple[sqlite3.Row | None, bool]:
        source_row = conn.execute(
            "SELECT sync_status FROM sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        running_rows = conn.execute(
            """
            SELECT * FROM sync_jobs
            WHERE source_id = ? AND status = ?
            ORDER BY started_at DESC, rowid DESC
            """,
            (source_id, SyncJobStatus.RUNNING.value),
        ).fetchall()
        if running_rows:
            if len(running_rows) > 1 or any(
                self._should_fail_active_running_job(conn, running_row)
                for running_row in running_rows
            ):
                return None, True
            if not source_row or source_row["sync_status"] != SyncStatus.RUNNING.value:
                return None, True
            return running_rows[0], False

        queued_row = self._get_queued_sync_job(conn, source_id)
        if queued_row:
            if not source_row or source_row["sync_status"] != SyncStatus.RUNNING.value:
                return None, True
            return queued_row, False

        if source_row and source_row["sync_status"] == SyncStatus.RUNNING.value:
            return None, True
        return (
            conn.execute(
                """
                SELECT * FROM sync_jobs
                WHERE source_id = ?
                ORDER BY started_at DESC, rowid DESC
                LIMIT 1
                """,
                (source_id,),
            ).fetchone(),
            False,
        )

    def _reconcile_latest_sync_job(
        self,
        conn: sqlite3.Connection,
        source_id: str,
        checked_at: str,
    ) -> sqlite3.Row | None:
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
                self._mark_source_sync_active(conn, source_id, checked_at)
            return active_row
        queued_row = self._get_queued_sync_job(conn, source_id)
        if queued_row:
            if not source_row or source_row["sync_status"] != SyncStatus.RUNNING.value:
                self._mark_source_sync_active(conn, source_id, checked_at)
            return queued_row
        if source_row and source_row["sync_status"] == SyncStatus.RUNNING.value:
            self._reconcile_source_after_inactive_job(
                conn,
                source_id,
                checked_at,
                "Sync job timed out before status read completed",
            )
        return conn.execute(
            """
            SELECT * FROM sync_jobs
            WHERE source_id = ?
            ORDER BY started_at DESC, rowid DESC
            LIMIT 1
            """,
            (source_id,),
        ).fetchone()

    def get_source_status_snapshot(self, source_id: str) -> dict[str, object]:
        self.ensure_schema()
        with self._connect() as conn:
            latest_success = conn.execute(
                """
                SELECT finished_at
                FROM sync_jobs
                WHERE source_id = ? AND status = ? AND finished_at != ''
                ORDER BY finished_at DESC, started_at DESC, rowid DESC
                LIMIT 1
                """,
                (source_id, SyncJobStatus.SUCCEEDED.value),
            ).fetchone()
            latest_failure = conn.execute(
                """
                SELECT finished_at, error_message
                FROM sync_jobs
                WHERE source_id = ? AND status = ? AND finished_at != ''
                ORDER BY finished_at DESC, started_at DESC, rowid DESC
                LIMIT 1
                """,
                (source_id, SyncJobStatus.FAILED.value),
            ).fetchone()
            document_row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM documents
                WHERE source_id = ? AND COALESCE(deleted_at, '') = ''
                """,
                (source_id,),
            ).fetchone()
            chunk_row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM chunks c
                JOIN documents d ON d.document_id = c.document_id
                    AND d.source_id = c.source_id
                WHERE c.source_id = ? AND COALESCE(d.deleted_at, '') = ''
                """,
                (source_id,),
            ).fetchone()
        return {
            "latest_success_at": latest_success["finished_at"]
            if latest_success
            else "",
            "latest_failure_at": latest_failure["finished_at"]
            if latest_failure
            else "",
            "latest_failure_reason": latest_failure["error_message"]
            if latest_failure
            else "",
            "document_count": int(document_row["count"]) if document_row else 0,
            "chunk_count": int(chunk_row["count"]) if chunk_row else 0,
        }

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
        self._validate_chunks_for_document(
            chunk_list, document_id, normalized.source_id
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._upsert_document(conn, normalized)
            self._record_chunk_tombstones_for_document(
                conn, document_id, normalized.source_id
            )
            conn.execute(
                "DELETE FROM chunks WHERE document_id = ? AND source_id = ?",
                (document_id, normalized.source_id),
            )
            self._insert_chunks(conn, chunk_list)
            self._resolve_vector_state_for_active_chunks(
                conn,
                chunk_list,
                normalized.source_id,
            )
        return normalized

    def get_document(self, document_id: str) -> Optional[DocumentModel]:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        return self._document_from_row(row) if row else None

    def get_documents_for_fetch_reuse(
        self, document_ids: Sequence[str]
    ) -> dict[str, DocumentModel]:
        """Batch-load skip/reuse fields including title and citation line_start.

        ``line_start`` is derived from ``MIN(chunks.line_start)`` for the
        document, adjusted for leading blank lines in stored content so it
        matches the original DocumentModel body base used when re-chunking.
        """
        unique_ids: list[str] = []
        seen: set[str] = set()
        for document_id in document_ids:
            if not document_id or document_id in seen:
                continue
            seen.add(document_id)
            unique_ids.append(document_id)
        if not unique_ids:
            return {}

        self.ensure_schema()
        loaded: dict[str, DocumentModel] = {}
        chunk_size = 500
        with self._connect() as conn:
            for offset in range(0, len(unique_ids), chunk_size):
                chunk = unique_ids[offset : offset + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                # Dynamic SQL is limited to generated placeholders; document IDs
                # remain parameterized in the execute call below. Prefer a single
                # grouped JOIN over a per-row correlated MIN subquery.
                query = "\n".join(
                    [
                        "SELECT documents.document_id, documents.external_id,",
                        "       documents.source_id, documents.content,",
                        "       documents.title, documents.modified_at,",
                        "       documents.version_id, documents.content_hash,",
                        "       documents.deleted_at,",
                        "       chunk_mins.min_line_start AS line_start",
                        "FROM documents",
                        "LEFT JOIN (",
                        "    SELECT document_id, source_id,",
                        "           MIN(line_start) AS min_line_start",
                        "    FROM chunks",
                        "    WHERE document_id IN (" + placeholders + ")",
                        "    GROUP BY document_id, source_id",
                        ") AS chunk_mins",
                        "  ON chunk_mins.document_id = documents.document_id",
                        " AND chunk_mins.source_id = documents.source_id",
                        "WHERE documents.document_id IN (" + placeholders + ")",
                    ]
                )
                rows = conn.execute(
                    query,
                    tuple(chunk) + tuple(chunk),
                ).fetchall()
                for row in rows:
                    document_id = row["document_id"]
                    content = row["content"] or ""
                    raw_line_start = row["line_start"]
                    chunk_min_line_start = (
                        None if raw_line_start is None else int(raw_line_start)
                    )
                    loaded[document_id] = DocumentModel(
                        id=document_id,
                        document_id=document_id,
                        source_id=row["source_id"] or "",
                        external_id=row["external_id"] or "",
                        title=row["title"] or "",
                        content=content,
                        url="",
                        platform="",
                        modified_at=row["modified_at"] or "",
                        version_id=row["version_id"] or "",
                        content_hash=row["content_hash"] or "",
                        deleted_at=row["deleted_at"] or "",
                        line_start=self._document_line_start_from_chunk_min(
                            content, chunk_min_line_start
                        ),
                    )
        return loaded

    @staticmethod
    def _document_line_start_from_chunk_min(
        content: str, min_chunk_line_start: int | None
    ) -> int | None:
        """Map MIN(chunk.line_start) back to DocumentModel.line_start base.

        Chunkers skip leading blank lines before assigning citation line numbers,
        so reuse must subtract those blanks to restore the original body base.
        """
        if min_chunk_line_start is None:
            return None
        leading_blank_lines = 0
        for line in content.splitlines():
            if line.strip():
                break
            leading_blank_lines += 1
        return int(min_chunk_line_start) - leading_blank_lines

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

    def document_matches_filters(
        self,
        document: DocumentModel,
        filters: SearchFilters | None = None,
    ) -> bool:
        """Apply the authoritative active/source/date gate to a hydrated document."""
        normalized_filters = self._normalize_search_filters(filters)
        if document.deleted_at:
            return False
        effective_source_ids = normalized_filters.effective_source_ids
        if effective_source_ids and document.source_id not in effective_source_ids:
            return False

        for field_name, prefix in (
            ("published_at", "published"),
            ("modified_at", "modified"),
            ("indexed_at", "indexed"),
        ):
            lower = self._parse_timestamp(getattr(normalized_filters, f"{prefix}_from"))
            upper = self._parse_timestamp(getattr(normalized_filters, f"{prefix}_to"))
            if lower is None and upper is None:
                continue
            value = self._parse_timestamp(getattr(document, field_name))
            if value is None:
                return False
            if lower is not None and value < lower:
                return False
            if upper is not None:
                # Second-precision inclusive upper bounds cover the whole second.
                effective_upper = (
                    upper.replace(microsecond=999999)
                    if upper.microsecond == 0
                    else upper
                )
                if value > effective_upper:
                    return False
        return True

    def list_documents(
        self,
        *,
        filters: SearchFilters | None = None,
        sort_by: DocumentSortBy | str = DocumentSortBy.INDEXED_AT,
        sort_order: SortOrder | str = SortOrder.DESC,
        page_size: int = 20,
        cursor: str | None = None,
    ) -> dict[str, object]:
        """List active documents with deterministic normalized-date keyset pagination."""
        normalized_filters = self._normalize_search_filters(filters)
        try:
            normalized_sort_by = DocumentSortBy(sort_by)
            normalized_sort_order = SortOrder(sort_order)
        except ValueError as exc:
            raise ValueError("Unsupported document sort") from exc
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 100
        ):
            raise ValueError("page_size must be between 1 and 100")

        query_shape = {
            "filters": self._document_filter_cursor_payload(normalized_filters),
            "sort_by": normalized_sort_by.value,
            "sort_order": normalized_sort_order.value,
        }
        cursor_payload = None
        validated_cursor_key = None
        if cursor:
            cursor_payload = self._decode_document_cursor(cursor)
            if cursor_payload.get("query") != query_shape:
                raise ValueError("Invalid document cursor")
            validated_cursor_key = self._validated_cursor_timestamp_key(cursor_payload)

        self.ensure_schema()
        sort_column = normalized_sort_by.value
        sort_value_sql = self._canonical_timestamp_sql(sort_column)
        where_clauses = ["COALESCE(deleted_at, '') = ''"]
        query_params: list[object] = []

        effective_source_ids = normalized_filters.effective_source_ids
        if effective_source_ids:
            placeholders = ",".join("?" for _ in effective_source_ids)
            where_clauses.append(f"source_id IN ({placeholders})")
            query_params.extend(effective_source_ids)

        for field_name, prefix in (
            ("published_at", "published"),
            ("modified_at", "modified"),
            ("indexed_at", "indexed"),
        ):
            value_sql = self._canonical_timestamp_sql(field_name)
            lower = getattr(normalized_filters, f"{prefix}_from")
            upper = getattr(normalized_filters, f"{prefix}_to")
            if lower:
                where_clauses.append(f"{value_sql} >= ?")
                query_params.append(self._canonical_timestamp_key(lower))
            if upper:
                where_clauses.append(f"{value_sql} <= ?")
                query_params.append(self._canonical_upper_timestamp_key(upper))

        anchor_query = None
        anchor_params: list[object] = []
        if cursor_payload is not None:
            cursor_document_id = str(cursor_payload["document_id"])
            anchor_clauses = [*where_clauses, "document_id = ?"]
            anchor_params = [*query_params, cursor_document_id]
            if cursor_payload["is_null"]:
                anchor_clauses.append(f"{sort_value_sql} IS NULL")
                where_clauses.append(f"({sort_value_sql} IS NULL AND document_id > ?)")
                query_params.append(cursor_document_id)
            else:
                if validated_cursor_key is None:
                    raise ValueError("Invalid document cursor")
                anchor_clauses.append(f"{sort_value_sql} = ?")
                anchor_params.append(validated_cursor_key)
                comparison = ">" if normalized_sort_order == SortOrder.ASC else "<"
                where_clauses.append(
                    "("
                    f"{sort_value_sql} {comparison} ?"
                    f" OR ({sort_value_sql} = ? AND document_id > ?)"
                    f" OR {sort_value_sql} IS NULL"
                    ")"
                )
                query_params.extend(
                    (
                        validated_cursor_key,
                        validated_cursor_key,
                        cursor_document_id,
                    )
                )
            anchor_where_sql = " AND ".join(anchor_clauses)
            # SQL fragments come only from enum-selected columns and internal clauses.
            anchor_query = f"""
                SELECT 1
                FROM documents
                WHERE {anchor_where_sql}
                LIMIT 1
            """  # nosec B608

        order_direction = "ASC" if normalized_sort_order == SortOrder.ASC else "DESC"
        where_sql = " AND ".join(where_clauses)
        query_params.append(page_size + 1)
        # Only enum/internal SQL fragments are interpolated; caller values stay parameterized.
        browse_query = f"""
            SELECT
                document_id, source_id, title, url, canonical_url, platform,
                published_at, modified_at, indexed_at, date_provenance
            FROM documents
            WHERE {where_sql}
            ORDER BY
                CASE WHEN {sort_value_sql} IS NULL THEN 1 ELSE 0 END ASC,
                {sort_value_sql} {order_direction},
                document_id ASC
            LIMIT ?
        """  # nosec B608
        with self._connect() as conn:
            if (
                anchor_query is not None
                and conn.execute(anchor_query, tuple(anchor_params)).fetchone() is None
            ):
                raise ValueError("Invalid document cursor")
            rows = conn.execute(
                browse_query,
                tuple(query_params),
            ).fetchall()

        page = [self._browse_document_from_row(row) for row in rows]
        has_more = len(page) > page_size
        page = page[:page_size]
        next_cursor = None
        if has_more and page:
            last_document = page[-1]
            last_timestamp = getattr(last_document, normalized_sort_by.value)
            parsed = self._parse_timestamp(last_timestamp)
            next_cursor = self._encode_document_cursor(
                {
                    "version": 1,
                    "query": query_shape,
                    "is_null": parsed is None,
                    "timestamp": (
                        parsed.isoformat().replace("+00:00", "Z") if parsed else ""
                    ),
                    "document_id": last_document.document_id or last_document.id,
                }
            )
        return {"documents": page, "next_cursor": next_cursor}

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
            self._resolve_vector_state_for_active_chunks(
                conn,
                chunk_list,
                source_id,
            )

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

    def get_evidence_chunk(self, chunk_id: str) -> Optional[ChunkModel]:
        """Return authoritative stored evidence metadata for an active chunk."""
        return self.get_chunk(chunk_id)

    def get_active_chunk_ids(
        self,
        chunk_ids: Sequence[str],
        source_id: str = "",
    ) -> set[str]:
        """Batch-load active chunk IDs from one source-scoped SQLite snapshot."""
        unique_ids = list(dict.fromkeys(chunk_id for chunk_id in chunk_ids if chunk_id))
        if not unique_ids:
            return set()
        self.ensure_schema()
        active_ids: set[str] = set()
        batch_size = 800
        with self._connect() as conn:
            conn.execute("BEGIN")
            for offset in range(0, len(unique_ids), batch_size):
                batch = unique_ids[offset : offset + batch_size]
                placeholders = ",".join("?" for _ in batch)
                source_clause = " AND c.source_id = ?" if source_id else ""
                query = "\n".join(
                    [
                        "SELECT c.chunk_id FROM chunks c",
                        "JOIN documents d ON d.document_id = c.document_id",
                        "    AND d.source_id = c.source_id",
                        f"WHERE c.chunk_id IN ({placeholders})",
                        "  AND COALESCE(d.deleted_at, '') = ''" + source_clause,
                    ]
                )
                params = [*batch, source_id] if source_id else batch
                active_ids.update(
                    str(row["chunk_id"])
                    for row in conn.execute(query, params).fetchall()
                )
        return active_ids

    def list_pending_vector_cleanup_ids(
        self,
        source_id: str,
        *,
        limit: int = 5_000,
    ) -> list[str]:
        """Return inactive tombstoned chunks whose vector deletion is pending."""
        if not source_id or limit <= 0:
            return []
        self.ensure_schema()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT candidate.chunk_id, MIN(candidate.recorded_at) AS first_recorded_at
                FROM (
                    SELECT t.chunk_id, t.recorded_at
                    FROM chunk_tombstones t
                    WHERE t.source_id = ?
                      AND t.vector_cleanup_at = ''
                    UNION ALL
                    SELECT i.chunk_id, i.recorded_at
                    FROM vector_write_intents i
                    WHERE i.source_id = ?
                ) AS candidate
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM chunks c
                    JOIN documents d ON d.document_id = c.document_id
                        AND d.source_id = c.source_id
                    WHERE c.chunk_id = candidate.chunk_id
                      AND c.source_id = ?
                      AND d.deleted_at = ''
                )
                GROUP BY candidate.chunk_id
                ORDER BY first_recorded_at, candidate.chunk_id
                LIMIT ?
                """,
                (source_id, source_id, source_id, limit),
            ).fetchall()
        return [str(row["chunk_id"]) for row in rows]

    def record_vector_write_intents(
        self,
        chunk_ids: Sequence[str],
        *,
        source_id: str,
        document_id: str,
        job_id: str,
    ) -> None:
        """Durably record new vector IDs before the external vector mutation."""
        unique_ids = list(dict.fromkeys(chunk_id for chunk_id in chunk_ids if chunk_id))
        if not unique_ids:
            return
        if not source_id or not document_id or not job_id:
            raise ValueError(
                "Vector write intent requires source, document, and job IDs"
            )
        self.ensure_schema()
        recorded_at = _now()
        batch_size = 800
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for offset in range(0, len(unique_ids), batch_size):
                batch = unique_ids[offset : offset + batch_size]
                conn.executemany(
                    """
                    INSERT INTO vector_write_intents (
                        chunk_id, source_id, document_id, job_id, recorded_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id, source_id) DO UPDATE SET
                        document_id = excluded.document_id,
                        job_id = excluded.job_id,
                        recorded_at = excluded.recorded_at
                    """,
                    [
                        (chunk_id, source_id, document_id, job_id, recorded_at)
                        for chunk_id in batch
                    ],
                )

    def record_vector_metadata_refresh_intents(
        self,
        chunk_ids: Sequence[str],
        *,
        source_id: str,
        document_id: str,
        job_id: str,
    ) -> None:
        """Durably mark vector metadata that may diverge before SQLite commits."""
        unique_ids = list(dict.fromkeys(chunk_id for chunk_id in chunk_ids if chunk_id))
        if not unique_ids:
            return
        if not source_id or not document_id or not job_id:
            raise ValueError(
                "Vector metadata refresh intent requires source, document, and job IDs"
            )
        self.ensure_schema()
        recorded_at = _now()
        batch_size = 800
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for offset in range(0, len(unique_ids), batch_size):
                batch = unique_ids[offset : offset + batch_size]
                conn.executemany(
                    """
                    INSERT INTO vector_metadata_refresh_intents (
                        chunk_id, source_id, document_id, job_id, recorded_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_id, source_id) DO UPDATE SET
                        document_id = excluded.document_id,
                        job_id = excluded.job_id,
                        recorded_at = excluded.recorded_at
                    """,
                    [
                        (chunk_id, source_id, document_id, job_id, recorded_at)
                        for chunk_id in batch
                    ],
                )

    def list_pending_vector_metadata_refresh_ids(
        self,
        source_id: str,
        *,
        document_id: str,
        limit: int = 5_000,
    ) -> list[str]:
        """Return active authoritative chunks with unresolved vector metadata."""
        if not source_id or not document_id or limit <= 0:
            return []
        self.ensure_schema()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT i.chunk_id
                FROM vector_metadata_refresh_intents i
                JOIN chunks c ON c.chunk_id = i.chunk_id
                    AND c.source_id = i.source_id
                    AND c.document_id = i.document_id
                JOIN documents d ON d.document_id = c.document_id
                    AND d.source_id = c.source_id
                WHERE i.source_id = ?
                  AND i.document_id = ?
                  AND COALESCE(d.deleted_at, '') = ''
                ORDER BY i.recorded_at, i.chunk_id
                LIMIT ?
                """,
                (source_id, document_id, limit),
            ).fetchall()
        return [str(row["chunk_id"]) for row in rows]

    def mark_vector_metadata_refresh_complete(
        self,
        chunk_ids: Sequence[str],
        *,
        source_id: str,
    ) -> None:
        """Acknowledge vector metadata restored from authoritative SQLite."""
        unique_ids = list(dict.fromkeys(chunk_id for chunk_id in chunk_ids if chunk_id))
        if not unique_ids or not source_id:
            return
        self.ensure_schema()
        batch_size = 800
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for offset in range(0, len(unique_ids), batch_size):
                batch = unique_ids[offset : offset + batch_size]
                placeholders = ",".join("?" for _ in batch)
                conn.execute(
                    f"""
                    DELETE FROM vector_metadata_refresh_intents
                    WHERE source_id = ?
                      AND chunk_id IN ({placeholders})
                    """,  # nosec B608
                    (source_id, *batch),
                )

    def record_pending_vector_cleanup_ids(
        self,
        chunk_ids: Sequence[str],
        source_id: str = "",
    ) -> None:
        """Durably ledger vectors that have no active SQLite chunk."""
        unique_ids = list(dict.fromkeys(chunk_id for chunk_id in chunk_ids if chunk_id))
        if not unique_ids or not source_id:
            return
        self.ensure_schema()
        recorded_at = _now()
        batch_size = 800
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for offset in range(0, len(unique_ids), batch_size):
                batch = unique_ids[offset : offset + batch_size]
                conn.executemany(
                    """
                    INSERT INTO chunk_tombstones (
                        chunk_id, document_id, source_id, recorded_at,
                        vector_cleanup_at
                    ) VALUES (?, '', ?, ?, '')
                    ON CONFLICT(chunk_id) DO UPDATE SET
                        source_id = excluded.source_id,
                        recorded_at = excluded.recorded_at,
                        vector_cleanup_at = ''
                    """,
                    [(chunk_id, source_id, recorded_at) for chunk_id in batch],
                )

    def mark_vector_cleanup_complete(
        self,
        chunk_ids: Sequence[str],
        source_id: str = "",
    ) -> None:
        """Acknowledge successful source-scoped vector cleanup in batches."""
        unique_ids = list(dict.fromkeys(chunk_id for chunk_id in chunk_ids if chunk_id))
        if not unique_ids or not source_id:
            return
        self.ensure_schema()
        cleaned_at = _now()
        batch_size = 800
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for offset in range(0, len(unique_ids), batch_size):
                batch = unique_ids[offset : offset + batch_size]
                placeholders = ",".join("?" for _ in batch)
                query = f"""
                    UPDATE chunk_tombstones
                    SET vector_cleanup_at = ?
                    WHERE source_id = ?
                      AND chunk_id IN ({placeholders})
                """  # nosec B608
                conn.execute(query, (cleaned_at, source_id, *batch))
                conn.execute(
                    f"""
                    DELETE FROM vector_write_intents
                    WHERE source_id = ?
                      AND chunk_id IN ({placeholders})
                    """,  # nosec B608
                    (source_id, *batch),
                )
                conn.execute(
                    f"""
                    DELETE FROM vector_metadata_refresh_intents
                    WHERE source_id = ?
                      AND chunk_id IN ({placeholders})
                    """,  # nosec B608
                    (source_id, *batch),
                )

    def get_active_evidence_snapshots(
        self,
        chunk_ids: Sequence[str],
    ) -> dict[str, tuple[ChunkModel, DocumentModel]]:
        """Batch-load active chunk/document pairs from one SQLite snapshot."""
        unique_ids: list[str] = []
        seen: set[str] = set()
        for chunk_id in chunk_ids:
            if not chunk_id or chunk_id in seen:
                continue
            seen.add(chunk_id)
            unique_ids.append(chunk_id)
        if not unique_ids:
            return {}

        self.ensure_schema()
        chunk_rows = []
        document_rows = []
        batch_size = 800
        with self._connect() as conn:
            conn.execute("BEGIN")
            for offset in range(0, len(unique_ids), batch_size):
                batch = unique_ids[offset : offset + batch_size]
                placeholders = ",".join("?" for _ in batch)
                query = "\n".join(
                    [
                        "SELECT c.* FROM chunks c",
                        "JOIN documents d ON d.document_id = c.document_id",
                        "    AND d.source_id = c.source_id",
                        f"WHERE c.chunk_id IN ({placeholders})",
                        "  AND COALESCE(d.deleted_at, '') = ''",
                    ]
                )
                chunk_rows.extend(conn.execute(query, batch).fetchall())

            document_ids = list(
                dict.fromkeys(str(row["document_id"]) for row in chunk_rows)
            )
            for offset in range(0, len(document_ids), batch_size):
                batch = document_ids[offset : offset + batch_size]
                placeholders = ",".join("?" for _ in batch)
                # Dynamic SQL contains generated placeholders only; IDs stay bound.
                query = f"""
                    SELECT * FROM documents
                    WHERE document_id IN ({placeholders})
                      AND COALESCE(deleted_at, '') = ''
                """  # nosec B608
                document_rows.extend(conn.execute(query, batch).fetchall())

        chunks = {str(row["chunk_id"]): self._chunk_from_row(row) for row in chunk_rows}
        documents = {
            str(row["document_id"]): self._document_from_row(row)
            for row in document_rows
        }
        return {
            chunk_id: (chunk, document)
            for chunk_id in unique_ids
            if (chunk := chunks.get(chunk_id)) is not None
            and (document := documents.get(chunk.document_id)) is not None
        }

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
                "("
                + " OR ".join(
                    f"INSTR(LOWER({field}), ?) > 0" for field in searchable_fields
                )
                + ")"
            )
            params.extend([term for _ in searchable_fields])

        term_operator = " AND " if require_all_terms else " OR "
        where_clauses = ["COALESCE(d.deleted_at, '') = ''"]
        if term_clauses:
            where_clauses.append("(" + term_operator.join(term_clauses) + ")")
        for term in normalized_metadata_only_terms:
            where_clauses.append(
                "("
                + " OR ".join(
                    f"INSTR(LOWER({field}), ?) > 0" for field in metadata_fields
                )
                + ")"
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
            where_clauses.append(
                f"(c.source_id != 'source_github' OR {document_like_clause})"
            )
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
        stale_cleanup_disabled_reason: str = "",
        parsed_documents: int | None = None,
        updated_documents: int | None = None,
        created_chunks: int | None = None,
        updated_chunks: int | None = None,
        skipped_chunks: int | None = None,
        embeddings_generated: int | None = None,
        embeddings_reused: int | None = None,
        parsing_failures: int | None = None,
        indexing_latency_ms: float | None = None,
    ) -> tuple[SyncJobModel, list[str]]:
        """Atomically finalize a successful sync and optional stale cleanup."""
        self.ensure_schema()
        stale_cleanup_disabled_reason = _sanitize_lifecycle_text(
            stale_cleanup_disabled_reason
        )
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
                row = conn.execute(
                    "SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
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
                    skipped_documents = ?, parsed_documents = ?,
                    updated_documents = ?, created_chunks = ?,
                    updated_chunks = ?, skipped_chunks = ?,
                    embeddings_generated = ?, embeddings_reused = ?,
                    parsing_failures = ?, indexing_latency_ms = ?,
                    phase = ?, last_progress_at = ?, status_message = ?,
                    error_message = ''
                WHERE job_id = ?
                """,
                (
                    SyncJobStatus.SUCCEEDED.value,
                    finished_at,
                    total_documents,
                    processed_documents,
                    indexed_chunks,
                    skipped_documents,
                    (
                        current_job["parsed_documents"]
                        if parsed_documents is None
                        else parsed_documents
                    ),
                    (
                        current_job["updated_documents"]
                        if updated_documents is None
                        else updated_documents
                    ),
                    (
                        current_job["created_chunks"]
                        if created_chunks is None
                        else created_chunks
                    ),
                    (
                        current_job["updated_chunks"]
                        if updated_chunks is None
                        else updated_chunks
                    ),
                    (
                        current_job["skipped_chunks"]
                        if skipped_chunks is None
                        else skipped_chunks
                    ),
                    (
                        current_job["embeddings_generated"]
                        if embeddings_generated is None
                        else embeddings_generated
                    ),
                    (
                        current_job["embeddings_reused"]
                        if embeddings_reused is None
                        else embeddings_reused
                    ),
                    (
                        current_job["parsing_failures"]
                        if parsing_failures is None
                        else parsing_failures
                    ),
                    (
                        current_job["indexing_latency_ms"]
                        if indexing_latency_ms is None
                        else indexing_latency_ms
                    ),
                    "completed",
                    finished_at,
                    (
                        "Sync completed. "
                        f"Indexed {processed_documents}/{total_documents} documents; "
                        f"skipped {skipped_documents}."
                    ),
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
                    stale_cleanup_disabled_reason = ?,
                    updated_at = ?
                WHERE source_id = ?
                """,
                (
                    SyncStatus.SUCCEEDED.value,
                    finished_at,
                    stale_cleanup_disabled_reason,
                    source_updated_at,
                    source_id,
                ),
            )
            if source_cursor.rowcount == 0:
                raise ValueError(f"Unknown source: {source_id}")

            row = conn.execute(
                "SELECT * FROM sync_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()

        return self._job_from_row(row), deleted_chunk_ids

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            with conn:
                yield conn
        finally:
            conn.close()

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
            ORDER BY started_at DESC, rowid DESC
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
        error_message = _sanitize_lifecycle_text(error_message)
        active_row = self._resolve_active_running_job(conn, source_id, finished_at)
        if active_row:
            return
        queued_row = self._get_queued_sync_job(conn, source_id)
        if queued_row:
            self._mark_source_sync_active(conn, source_id, finished_at)
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
    def _get_queued_sync_job(conn, source_id: str):
        return conn.execute(
            """
            SELECT * FROM sync_jobs
            WHERE source_id = ? AND status = ?
            ORDER BY started_at, rowid
            LIMIT 1
            """,
            (source_id, SyncJobStatus.QUEUED.value),
        ).fetchone()

    @staticmethod
    def _mark_source_sync_active(conn, source_id: str, updated_at: str):
        conn.execute(
            """
            UPDATE sources SET
                sync_status = ?,
                last_error = '',
                updated_at = ?
            WHERE source_id = ?
            """,
            (SyncStatus.RUNNING.value, updated_at, source_id),
        )

    @staticmethod
    def _fail_sync_job_row(conn, job_id: str, finished_at: str, error_message: str):
        error_message = _sanitize_lifecycle_text(error_message)
        conn.execute(
            """
            UPDATE sync_jobs SET
                status = ?,
                finished_at = ?,
                phase = ?,
                last_progress_at = ?,
                status_message = ?,
                error_message = ?
            WHERE job_id = ?
            """,
            (
                SyncJobStatus.FAILED.value,
                finished_at,
                "failed",
                finished_at,
                error_message,
                error_message,
                job_id,
            ),
        )
        conn.execute("DELETE FROM document_claims WHERE job_id = ?", (job_id,))

    def _claim_document(
        self, conn, document: DocumentModel, job_id: str, claimed_at: str
    ):
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
            remove_claim = (
                claim_job is None or claim_job["status"] != SyncJobStatus.RUNNING.value
            )
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
        if not self._is_process_alive(owner_row["process_id"]):
            return self._dead_owner_is_definitive_in_current_scope(owner_row)
        process_instance_matches = self._owner_process_instance_matches(owner_row)
        if process_instance_matches is False:
            return True
        if self._owner_pid_matches_current_process(owner_row):
            return self._is_stale_running_job(row)
        return False

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
                if not self._is_process_alive(owner_row["process_id"]):
                    return self._dead_owner_is_definitive_in_current_scope(owner_row)
                process_instance_matches = self._owner_process_instance_matches(
                    owner_row
                )
                if process_instance_matches is False:
                    return True
                if self._owner_pid_matches_current_process(owner_row):
                    return self._is_stale_running_job(row)
                return False
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

    @staticmethod
    def _owner_pid_matches_current_process(row) -> bool:
        try:
            return int(row["process_id"]) == os.getpid()
        except (TypeError, ValueError):
            return False

    @classmethod
    def _owner_process_instance_matches(cls, row) -> Optional[bool]:
        stored_identity = (
            str(row["process_start_id"] or "")
            if "process_start_id" in row.keys()
            else ""
        )
        if not stored_identity:
            return None
        current_identity = cls._get_process_start_identity(row["process_id"])
        if not current_identity:
            return None
        if stored_identity == current_identity:
            return True

        stored_linux_identity = cls._parse_linux_process_start_identity(stored_identity)
        current_linux_identity = cls._parse_linux_process_start_identity(
            current_identity
        )
        if stored_linux_identity or current_linux_identity:
            if not stored_linux_identity or not current_linux_identity:
                return None
            stored_boot_id, stored_namespace, _ = stored_linux_identity
            current_boot_id, current_namespace, _ = current_linux_identity
            if (
                not stored_boot_id
                or not stored_namespace
                or not current_boot_id
                or not current_namespace
            ):
                return None
            if (stored_boot_id, stored_namespace) != (
                current_boot_id,
                current_namespace,
            ):
                return None
            return False

        stored_darwin_identity = cls._parse_darwin_process_start_identity(
            stored_identity
        )
        current_darwin_identity = cls._parse_darwin_process_start_identity(
            current_identity
        )
        if stored_identity.startswith("darwin:") or current_identity.startswith(
            "darwin:"
        ):
            if stored_darwin_identity is None or current_darwin_identity is None:
                return None
            return False
        return None

    @classmethod
    def _dead_owner_is_definitive_in_current_scope(cls, row) -> bool:
        stored_identity = (
            str(row["process_start_id"] or "")
            if "process_start_id" in row.keys()
            else ""
        )
        current_identity = cls._get_process_start_identity(os.getpid())
        current_linux_identity = cls._parse_linux_process_start_identity(
            current_identity
        )
        stored_linux_identity = cls._parse_linux_process_start_identity(stored_identity)
        if stored_linux_identity is not None or current_linux_identity is not None:
            if stored_linux_identity is None or current_linux_identity is None:
                return False
            stored_boot_id, stored_namespace, _ = stored_linux_identity
            current_boot_id, current_namespace, _ = current_linux_identity
            return (stored_boot_id, stored_namespace) == (
                current_boot_id,
                current_namespace,
            )

        stored_darwin_identity = cls._parse_darwin_process_start_identity(
            stored_identity
        )
        current_darwin_identity = cls._parse_darwin_process_start_identity(
            current_identity
        )
        if stored_darwin_identity is not None or current_darwin_identity is not None:
            return (
                stored_darwin_identity is not None
                and current_darwin_identity is not None
            )
        return False

    @staticmethod
    def _parse_linux_process_start_identity(
        identity: str,
    ) -> Optional[tuple[str, str, str]]:
        parts = identity.split("|")
        if (
            len(parts) != 4
            or parts[0] != "linux-v2"
            or not parts[1]
            or not parts[2]
            or not parts[3]
            or not MetadataStore._is_ascii_digits(parts[3])
            or parts[3].startswith("0")
        ):
            return None
        return parts[1], parts[2], parts[3]

    @staticmethod
    def _parse_darwin_process_start_identity(
        identity: str,
    ) -> Optional[tuple[str, str]]:
        parts = identity.split(":")
        if (
            len(parts) != 3
            or parts[0] != "darwin"
            or not MetadataStore._is_ascii_digits(parts[1])
            or not MetadataStore._is_ascii_digits(parts[2])
            or parts[1].startswith("0")
            or (len(parts[2]) > 1 and parts[2].startswith("0"))
        ):
            return None
        normalized_microseconds = parts[2].lstrip("0") or "0"
        if len(normalized_microseconds) > 6:
            return None
        return parts[1], parts[2]

    @staticmethod
    def _is_ascii_digits(value: str) -> bool:
        return bool(value) and value.isascii() and value.isdecimal()

    def _touch_sync_owner(self, conn, timestamp: str):
        process_id = os.getpid()
        if self._cached_process_id != process_id or not self._cached_process_start_id:
            self._cached_process_id = process_id
            self._cached_process_start_id = self._get_process_start_identity(process_id)
        process_start_id = self._cached_process_start_id
        conn.execute(
            """
            INSERT INTO sync_job_owners (
                owner_id, process_id, process_start_id, started_at, heartbeat_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(owner_id) DO UPDATE SET
                process_start_id = CASE
                    WHEN sync_job_owners.process_id != excluded.process_id
                    THEN excluded.process_start_id
                    WHEN excluded.process_start_id != ''
                    THEN excluded.process_start_id
                    ELSE sync_job_owners.process_start_id
                END,
                process_id = excluded.process_id,
                heartbeat_at = excluded.heartbeat_at
            """,
            (
                self.sync_owner_id,
                process_id,
                process_start_id,
                timestamp,
                timestamp,
            ),
        )
        conn.execute(
            """
            DELETE FROM sync_job_owners
            WHERE owner_id != ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM sync_jobs
                  WHERE sync_jobs.owner_id = sync_job_owners.owner_id
                    AND sync_jobs.status = ?
              )
            """,
            (self.sync_owner_id, SyncJobStatus.RUNNING.value),
        )

    @staticmethod
    def _get_process_start_identity(process_id: int) -> str:
        try:
            normalized_process_id = int(process_id)
        except (TypeError, ValueError):
            return ""
        if normalized_process_id <= 0:
            return ""

        proc_stat_path = Path(f"/proc/{normalized_process_id}/stat")
        try:
            stat_value = proc_stat_path.read_text(encoding="utf-8")
            closing_parenthesis = stat_value.rfind(")")
            stat_fields = stat_value[closing_parenthesis + 2 :].split()
            start_ticks = stat_fields[19]
            try:
                boot_id = (
                    Path("/proc/sys/kernel/random/boot_id")
                    .read_text(encoding="utf-8")
                    .strip()
                )
            except OSError:
                boot_id = ""
            if not boot_id:
                try:
                    boot_time_row = next(
                        row
                        for row in Path("/proc/stat")
                        .read_text(encoding="utf-8")
                        .splitlines()
                        if row.startswith("btime ")
                    )
                    boot_id = boot_time_row.split(maxsplit=1)[1]
                except (IndexError, OSError, StopIteration):
                    return ""
            try:
                pid_namespace = os.readlink(f"/proc/{normalized_process_id}/ns/pid")
            except OSError:
                pid_namespace = ""
            return f"linux-v2|{boot_id}|{pid_namespace}|{start_ticks}"
        except (IndexError, OSError):
            pass

        if sys.platform != "darwin":
            return ""
        try:
            process_info = _DarwinProcBSDInfo()
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            proc_pidinfo = libproc.proc_pidinfo
            proc_pidinfo.argtypes = [
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint64,
                ctypes.c_void_p,
                ctypes.c_int,
            ]
            proc_pidinfo.restype = ctypes.c_int
            copied_bytes = proc_pidinfo(
                normalized_process_id,
                _DARWIN_PROC_PIDTBSDINFO,
                0,
                ctypes.byref(process_info),
                ctypes.sizeof(process_info),
            )
        except (AttributeError, OSError):
            return ""
        if copied_bytes != ctypes.sizeof(process_info):
            return ""
        return f"darwin:{process_info.start_tvsec}:{process_info.start_tvusec}"

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
        try:
            return parsed.astimezone(timezone.utc)
        except (OverflowError, ValueError):
            return None

    @staticmethod
    def _normalize_search_filters(
        filters: SearchFilters | None,
    ) -> SearchFilters:
        if filters is None:
            return SearchFilters()
        if isinstance(filters, SearchFilters):
            return filters
        return SearchFilters.model_validate(filters)

    @staticmethod
    def _document_filter_cursor_payload(filters: SearchFilters) -> dict[str, object]:
        return {
            "source_ids": list(filters.effective_source_ids),
            "published_from": filters.published_from,
            "published_to": filters.published_to,
            "modified_from": filters.modified_from,
            "modified_to": filters.modified_to,
            "indexed_from": filters.indexed_from,
            "indexed_to": filters.indexed_to,
        }

    @staticmethod
    def _canonical_timestamp_sql(column_name: str) -> str:
        return (
            f"(CASE WHEN NULLIF({column_name}, '') IS NULL THEN NULL "
            f"WHEN instr({column_name}, '.') = 0 "
            f"THEN substr({column_name}, 1, 19) || '.000000Z' "
            f"ELSE {column_name} END)"
        )

    @staticmethod
    def _canonical_timestamp_key(value: str) -> str:
        if "." in value:
            return value
        return f"{value[:-1]}.000000Z"

    @staticmethod
    def _canonical_upper_timestamp_key(value: str) -> str:
        """Inclusive upper bound key; second-precision covers the whole second."""
        if "." in value:
            return value
        return f"{value[:-1]}.999999Z"

    @staticmethod
    def _encode_document_cursor(payload: dict[str, object]) -> str:
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_document_cursor(cursor: str) -> dict[str, object]:
        try:
            if not cursor or len(cursor) > 4096:
                raise ValueError
            raw_cursor = cursor.encode("ascii")
            padding = b"=" * (-len(raw_cursor) % 4)
            decoded = base64.b64decode(
                raw_cursor + padding,
                altchars=b"-_",
                validate=True,
            )
            canonical_cursor = (
                base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
            )
            if canonical_cursor != cursor:
                raise ValueError
            payload = json.loads(decoded.decode("utf-8"))
            query = payload.get("query") if isinstance(payload, dict) else None
            if (
                not isinstance(payload, dict)
                or set(payload)
                != {
                    "version",
                    "query",
                    "is_null",
                    "timestamp",
                    "document_id",
                }
                or payload.get("version") != 1
                or not isinstance(query, dict)
                or set(query) != {"filters", "sort_by", "sort_order"}
                or not isinstance(query.get("filters"), dict)
                or not isinstance(query.get("sort_by"), str)
                or not isinstance(query.get("sort_order"), str)
                or not isinstance(payload.get("is_null"), bool)
                or not isinstance(payload.get("timestamp"), str)
                or not isinstance(payload.get("document_id"), str)
            ):
                raise ValueError
            return payload
        except (
            ValueError,
            TypeError,
            UnicodeDecodeError,
            UnicodeEncodeError,
            json.JSONDecodeError,
            binascii.Error,
        ) as exc:
            raise ValueError("Invalid document cursor") from exc

    @classmethod
    def _validated_cursor_timestamp_key(
        cls,
        payload: dict[str, object],
    ) -> Optional[str]:
        if payload["is_null"]:
            if payload["timestamp"]:
                raise ValueError("Invalid document cursor")
            return None
        raw_timestamp = str(payload["timestamp"])
        parsed = cls._parse_timestamp(raw_timestamp)
        if parsed is None:
            raise ValueError("Invalid document cursor")
        canonical_timestamp = parsed.isoformat().replace("+00:00", "Z")
        if raw_timestamp != canonical_timestamp:
            raise ValueError("Invalid document cursor")
        return cls._canonical_timestamp_key(canonical_timestamp)

    @staticmethod
    def _ensure_columns(
        conn,
        table_name: str,
        columns: dict[str, str],
    ) -> set[str]:
        existing_columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        added_columns: set[str] = set()
        for column_name, column_definition in columns.items():
            if column_name not in existing_columns:
                conn.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
                )
                added_columns.add(column_name)
        return added_columns

    @staticmethod
    def _normalize_document(document: DocumentModel) -> DocumentModel:
        content_hash = document.content_hash or ContentHasher.hash_content(
            document.content
        )
        document_id = (
            document.document_id
            if document.evidence_source_type and document.document_id
            else document.external_id or document.document_id or document.id
        )
        published_at = MetadataStore._canonical_document_timestamp(
            document.published_at
        )
        modified_at = MetadataStore._canonical_document_timestamp(document.modified_at)
        indexed_at = MetadataStore._canonical_document_timestamp(
            document.indexed_at
        ) or MetadataStore._canonical_document_timestamp(_now())
        date_provenance = (
            document.date_provenance if published_at or modified_at else ""
        )
        return document.model_copy(
            update={
                "document_id": document_id,
                "external_id": document.external_id,
                "canonical_url": document.canonical_url or document.url,
                "path": document.path or document.title,
                "updated_at": document.updated_at or document.date,
                "published_at": published_at,
                "modified_at": modified_at,
                "indexed_at": indexed_at,
                "date_provenance": date_provenance,
                "last_seen_sync_id": document.last_seen_sync_id,
                "deleted_at": document.deleted_at,
                "document_version_id": document.document_version_id,
                "content_hash": content_hash,
            }
        )

    @classmethod
    def canonical_document_timestamp(cls, value: str) -> str:
        parsed = cls._parse_timestamp(value)
        if parsed is None:
            return ""
        return parsed.isoformat().replace("+00:00", "Z")

    @classmethod
    def _canonical_document_timestamp(cls, value: str) -> str:
        return cls.canonical_document_timestamp(value)

    @staticmethod
    def _upsert_document(conn, document: DocumentModel):
        MetadataStore._validate_document_owner(conn, document)
        document_id = document.document_id or document.id
        cursor = conn.execute(
            """
            INSERT INTO documents (
                document_id, source_id, external_id, title, content, url,
                canonical_url, platform, date, path, updated_at, published_at,
                modified_at, indexed_at, date_provenance, last_seen_at,
                last_seen_sync_id, deleted_at, version_id, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                published_at = excluded.published_at,
                modified_at = excluded.modified_at,
                indexed_at = CASE
                    WHEN documents.content_hash = excluded.content_hash
                         AND COALESCE(documents.indexed_at, '') != ''
                    THEN documents.indexed_at
                    ELSE excluded.indexed_at
                END,
                date_provenance = excluded.date_provenance,
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
                document.published_at,
                document.modified_at,
                document.indexed_at,
                document.date_provenance,
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
        evidence_source_type = document.evidence_source_type
        is_evidence = evidence_source_type is not None
        conn.execute(
            """
            UPDATE documents SET
                document_version_id = ?, evidence_source_type = ?,
                experience_type = ?, file_name = ?, document_title = ?,
                section_title = ?, parent_section_title = ?, exact_quote = ?,
                created_at = ?, company = ?, role = ?, project = ?,
                start_date = ?, end_date = ?
            WHERE document_id = ? AND source_id = ?
            """,
            (
                document.document_version_id if is_evidence else "",
                evidence_source_type.value if evidence_source_type is not None else "",
                document.experience_type.value if is_evidence else "unknown",
                document.file_name if is_evidence else "",
                document.document_title if is_evidence else "",
                document.section_title if is_evidence else "",
                document.parent_section_title if is_evidence else "",
                document.exact_quote if is_evidence else "",
                document.created_at if is_evidence else "",
                document.company if is_evidence else "",
                document.role if is_evidence else "",
                document.project if is_evidence else "",
                document.start_date if is_evidence else "",
                document.end_date if is_evidence else "",
                document_id,
                document.source_id,
            ),
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
            "SELECT c.chunk_id, c.document_id, c.source_id FROM chunks c",
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
        recorded_at = _now()
        conn.executemany(
            """
            INSERT OR REPLACE INTO chunk_tombstones (
                chunk_id, document_id, source_id, recorded_at, vector_cleanup_at
            ) VALUES (?, ?, ?, ?, '')
            """,
            [
                (
                    row["chunk_id"],
                    row["document_id"],
                    row["source_id"],
                    recorded_at,
                )
                for row in chunk_rows
            ],
        )
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
        conn.executemany(
            """
            UPDATE chunks SET
                document_version_id = ?, evidence_source_type = ?,
                experience_type = ?, file_name = ?, document_title = ?,
                section_title = ?, parent_section_title = ?, exact_quote = ?,
                created_at = ?, company = ?, role = ?, project = ?,
                start_date = ?, end_date = ?
            WHERE chunk_id = ?
            """,
            [
                (
                    chunk.document_version_id if chunk.evidence_source_type else "",
                    (
                        chunk.evidence_source_type.value
                        if chunk.evidence_source_type
                        else ""
                    ),
                    (
                        chunk.experience_type.value
                        if chunk.evidence_source_type
                        else "unknown"
                    ),
                    chunk.file_name if chunk.evidence_source_type else "",
                    chunk.document_title if chunk.evidence_source_type else "",
                    chunk.section_title if chunk.evidence_source_type else "",
                    (chunk.parent_section_title if chunk.evidence_source_type else ""),
                    chunk.exact_quote if chunk.evidence_source_type else "",
                    chunk.created_at if chunk.evidence_source_type else "",
                    chunk.company if chunk.evidence_source_type else "",
                    chunk.role if chunk.evidence_source_type else "",
                    chunk.project if chunk.evidence_source_type else "",
                    chunk.start_date if chunk.evidence_source_type else "",
                    chunk.end_date if chunk.evidence_source_type else "",
                    chunk.chunk_id,
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
            INSERT OR REPLACE INTO chunk_tombstones (
                chunk_id, document_id, source_id, recorded_at, vector_cleanup_at
            )
            SELECT chunk_id, document_id, source_id, ?, ''
            FROM chunks
            WHERE document_id = ? AND source_id = ?
            """,
            (_now(), document_id, source_id),
        )

    @staticmethod
    def _resolve_vector_state_for_active_chunks(
        conn,
        chunks: Sequence[ChunkModel],
        source_id: str,
    ) -> None:
        """Resolve write intents and pending history in the active-chunk commit."""
        chunk_ids = list(
            dict.fromkeys(chunk.chunk_id for chunk in chunks if chunk.chunk_id)
        )
        if not chunk_ids or not source_id:
            return
        resolved_at = _now()
        batch_size = 800
        for offset in range(0, len(chunk_ids), batch_size):
            batch = chunk_ids[offset : offset + batch_size]
            placeholders = ",".join("?" for _ in batch)
            conn.execute(
                f"""
                UPDATE chunk_tombstones
                SET vector_cleanup_at = ?
                WHERE source_id = ?
                  AND vector_cleanup_at = ''
                  AND chunk_id IN ({placeholders})
                """,  # nosec B608
                (resolved_at, source_id, *batch),
            )
            conn.execute(
                f"""
                DELETE FROM vector_write_intents
                WHERE source_id = ?
                  AND chunk_id IN ({placeholders})
                """,  # nosec B608
                (source_id, *batch),
            )
            conn.execute(
                f"""
                DELETE FROM vector_metadata_refresh_intents
                WHERE source_id = ?
                  AND chunk_id IN ({placeholders})
                """,  # nosec B608
                (source_id, *batch),
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
            stale_cleanup_disabled_reason=row["stale_cleanup_disabled_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _upstream_progress_from_row(row) -> tuple[int, int]:
        keys = set(row.keys())

        def _prefer(primary: str, legacy: str) -> int:
            # Prefer primary whenever the column exists — including intentional
            # zeros (e.g. search_completed resets upstream_done). Legacy columns
            # are only for pre-migration rows; one-time ensure_schema backfill
            # copies legacy into primary so reads need not fall back at zero.
            if primary in keys:
                return int(row[primary] or 0)
            if legacy in keys:
                return int(row[legacy] or 0)
            return 0

        return _prefer("upstream_total", "upstream_total_pages"), _prefer(
            "upstream_done", "upstream_fetched_pages"
        )

    @staticmethod
    def _job_from_row(row) -> SyncJobModel:
        upstream_total, upstream_done = MetadataStore._upstream_progress_from_row(row)
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
            parsed_documents=row["parsed_documents"],
            updated_documents=row["updated_documents"],
            created_chunks=row["created_chunks"],
            updated_chunks=row["updated_chunks"],
            skipped_chunks=row["skipped_chunks"],
            embeddings_generated=row["embeddings_generated"],
            embeddings_reused=row["embeddings_reused"],
            parsing_failures=row["parsing_failures"],
            indexing_latency_ms=row["indexing_latency_ms"],
            phase=normalize_sync_job_phase(row["phase"]),
            upstream_total=upstream_total,
            upstream_done=upstream_done,
            last_progress_at=row["last_progress_at"],
            status_message=row["status_message"],
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
            published_at=row["published_at"],
            modified_at=row["modified_at"],
            indexed_at=row["indexed_at"],
            date_provenance=row["date_provenance"],
            last_seen_at=row["last_seen_at"],
            last_seen_sync_id=row["last_seen_sync_id"],
            deleted_at=row["deleted_at"],
            version_id=row["version_id"],
            document_version_id=row["document_version_id"],
            content_hash=row["content_hash"],
            evidence_source_type=row["evidence_source_type"] or None,
            experience_type=row["experience_type"] or "unknown",
            file_name=row["file_name"],
            document_title=row["document_title"],
            section_title=row["section_title"],
            parent_section_title=row["parent_section_title"],
            exact_quote=row["exact_quote"],
            created_at=row["created_at"],
            company=row["company"],
            role=row["role"],
            project=row["project"],
            start_date=row["start_date"],
            end_date=row["end_date"],
        )

    @staticmethod
    def _browse_document_from_row(row) -> DocumentModel:
        return DocumentModel(
            id=row["document_id"],
            document_id=row["document_id"],
            source_id=row["source_id"],
            title=row["title"],
            content="",
            url=row["url"],
            canonical_url=row["canonical_url"],
            platform=row["platform"],
            published_at=row["published_at"],
            modified_at=row["modified_at"],
            indexed_at=row["indexed_at"],
            date_provenance=row["date_provenance"],
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
            document_version_id=row["document_version_id"],
            content_hash=row["content_hash"],
            updated_at=row["updated_at"],
            evidence_source_type=row["evidence_source_type"] or None,
            experience_type=row["experience_type"] or "unknown",
            file_name=row["file_name"],
            document_title=row["document_title"],
            section_title=row["section_title"],
            parent_section_title=row["parent_section_title"],
            exact_quote=row["exact_quote"],
            created_at=row["created_at"],
            company=row["company"],
            role=row["role"],
            project=row["project"],
            start_date=row["start_date"],
            end_date=row["end_date"],
        )
