import gc
import os
import sqlite3
import time
import warnings
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timedelta, timezone
from threading import Barrier, BrokenBarrierError, Event

import pytest
from pydantic import ValidationError

from core.models import (
    ChunkModel,
    DocumentModel,
    SearchFilters,
    SourceModel,
    SourceType,
    SyncJobStatus,
    SyncStatus,
)
from storage.metadata_store import MetadataStore, ORPHANED_SYNC_JOB_RECOVERY_MESSAGE


pytestmark = pytest.mark.integration


def _mark_job_running(
    store: MetadataStore,
    job_id: str,
    *,
    started_at: str | None = None,
    heartbeat_at: str = "",
    owner_id: str | None = None,
):
    started_at = started_at or datetime.now(timezone.utc).isoformat()
    owner_clause = ", owner_id = ?" if owner_id is not None else ""
    params = [SyncJobStatus.RUNNING.value, started_at, heartbeat_at]
    if owner_id is not None:
        params.append(owner_id)
    params.append(job_id)
    with store._connect() as conn:
        conn.execute(
            f"""
            UPDATE sync_jobs SET
                status = ?,
                started_at = ?,
                heartbeat_at = ?
                {owner_clause}
            WHERE job_id = ?
            """,
            tuple(params),
        )
    return store.get_sync_job(job_id)


def _mark_owner_heartbeat(
    store: MetadataStore,
    owner_id: str,
    *,
    started_at: str | None = None,
    heartbeat_at: str | None = None,
):
    started_at = started_at or datetime.now(timezone.utc).isoformat()
    heartbeat_at = heartbeat_at or started_at
    with store._connect() as conn:
        conn.execute(
            """
            UPDATE sync_job_owners SET
                started_at = ?,
                heartbeat_at = ?
            WHERE owner_id = ?
            """,
            (started_at, heartbeat_at, owner_id),
        )


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


def _raw_source_and_job_status(store: MetadataStore, source_id: str, job_id: str) -> tuple[str, str]:
    with store._connect() as conn:
        source_row = conn.execute(
            "SELECT sync_status FROM sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        job_row = conn.execute(
            "SELECT status FROM sync_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    return source_row["sync_status"], job_row["status"]


def _assert_sanitized_lifecycle_text(
    value: str,
    *,
    structured_fields: tuple[str, ...] = (),
) -> None:
    assert "ntn_" not in value
    assert "secret_" not in value
    assert "/Users/tester/private" not in value
    assert r"C:\Users\tester\private" not in value
    assert "meeting notes.md" not in value
    assert "<redacted>" in value
    for field in structured_fields:
        assert field in value


def _sensitive_lifecycle_text(source_id: str) -> str:
    return (
        "provider failed "
        "ntn_abcdefghijklmnopqrstuvwxyz0123456789 "
        "secret_abcdefghijklmnopqrstuvwxyz0123456789 "
        "path:/Users/tester/private vault/meeting notes.md, job_id=job-123; "
        rf"file:C:\Users\tester\private vault\meeting notes.md; source_id={source_id}"
    )


def test_metadata_store_redacts_short_explicit_auth_credentials_at_rest(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    raw = (
        "provider rejected Bearer abc123 while syncing, source_id=source_notion; "
        "fallback Basic Og== because retrying; job_id=job-123"
    )

    stored = store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            sync_status=SyncStatus.FAILED,
            last_error=raw,
        )
    )
    with store._connect() as conn:
        persisted = conn.execute(
            "SELECT last_error FROM sources WHERE source_id = ?",
            ("source_notion",),
        ).fetchone()["last_error"]

    for value in (stored.last_error, persisted):
        assert "abc123" not in value
        assert "Og==" not in value
        assert "Bearer <redacted-auth> while syncing," in value
        assert "Basic <redacted-auth> because retrying;" in value
        assert "source_id=source_notion" in value
        assert "job_id=job-123" in value


def test_metadata_store_redacts_folded_authorization_credentials_at_rest(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    raw = (
        "Authorization: Bearer\r\n"
        " folded-store-bearer-credential\r\n"
        "first clear diagnostic source_id=source_notion job_id=job-123\r"
        "Authorization: Basic\r"
        "\tfolded-store-basic-credential\r"
        "second clear diagnostic phase=fetching_page_content retry_count=26"
    )

    stored = store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            sync_status=SyncStatus.FAILED,
            last_error=raw,
        )
    )
    with store._connect() as conn:
        persisted = conn.execute(
            "SELECT last_error FROM sources WHERE source_id = ?",
            ("source_notion",),
        ).fetchone()["last_error"]

    for value in (stored.last_error, persisted):
        assert "folded-store-bearer-credential" not in value
        assert "folded-store-basic-credential" not in value
        assert "first clear diagnostic" in value
        assert "source_id=source_notion" in value
        assert "job_id=job-123" in value
        assert "second clear diagnostic" in value
        assert "phase=fetching_page_content" in value
        assert "retry_count=26" in value


def test_metadata_store_redacts_multistage_folded_authorization_credentials_at_rest(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    raw = (
        "Authorization:\r\n"
        " Bearer\r\n"
        " multistage-store-bearer-credential\r\n"
        "first clear diagnostic source_id=source_notion job_id=job-123\r"
        "Authorization=\r"
        "\tBasic\r"
        "\tmultistage-store-basic-credential\r"
        "second clear diagnostic phase=fetching_page_content retry_count=28"
    )

    stored = store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            sync_status=SyncStatus.FAILED,
            last_error=raw,
        )
    )
    with store._connect() as conn:
        persisted = conn.execute(
            "SELECT last_error FROM sources WHERE source_id = ?",
            ("source_notion",),
        ).fetchone()["last_error"]

    for value in (stored.last_error, persisted):
        assert "multistage-store-bearer-credential" not in value
        assert "multistage-store-basic-credential" not in value
        assert "first clear diagnostic" in value
        assert "source_id=source_notion" in value
        assert "job_id=job-123" in value
        assert "second clear diagnostic" in value
        assert "phase=fetching_page_content" in value
        assert "retry_count=28" in value


def test_metadata_store_redacts_bare_name_folded_authorization_credentials_at_rest(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    raw = (
        "Authorization\r\n"
        " Bearer\r\n"
        " bare-name-store-bearer-credential\r\n"
        "first clear diagnostic source_id=source_notion job_id=job-123\r"
        "Authorization\r"
        "\tBasic\r"
        "\tbare-name-store-basic-credential\r"
        "second clear diagnostic phase=fetching_page_content retry_count=34"
    )

    stored = store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            sync_status=SyncStatus.FAILED,
            last_error=raw,
        )
    )
    with store._connect() as conn:
        persisted = conn.execute(
            "SELECT last_error FROM sources WHERE source_id = ?",
            ("source_notion",),
        ).fetchone()["last_error"]

    for value in (stored.last_error, persisted):
        assert "bare-name-store-bearer-credential" not in value
        assert "bare-name-store-basic-credential" not in value
        assert "first clear diagnostic" in value
        assert "source_id=source_notion" in value
        assert "job_id=job-123" in value
        assert "second clear diagnostic" in value
        assert "phase=fetching_page_content" in value
        assert "retry_count=34" in value


def test_metadata_store_preserves_lone_cr_clear_diagnostic_after_labeled_path(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    sensitive_path = "/Users/tester/private vault/observability notes.md"
    raw = (
        f"provider failure path:{sensitive_path}\r"
        "clear diagnostic source_id=source_notion "
        "job_id=job-123 retry_count=25"
    )

    stored = store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            sync_status=SyncStatus.FAILED,
            last_error=raw,
        )
    )
    with store._connect() as conn:
        persisted = conn.execute(
            "SELECT last_error FROM sources WHERE source_id = ?",
            ("source_notion",),
        ).fetchone()["last_error"]

    for value in (stored.last_error, persisted):
        assert sensitive_path not in value
        assert "observability notes.md" not in value
        assert "clear diagnostic" in value
        assert "source_id=source_notion" in value
        assert "job_id=job-123" in value
        assert "retry_count=25" in value


def test_metadata_store_sanitizes_cookie_headers_and_unc_paths_at_rest(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    raw = (
        "Cookie: session=alpha, theme=private, preference=hidden\n"
        "job_id=job-123\n"
        "Set-Cookie: sid=bravo, unknown_attribute=top-secret,\n"
        "\tfolded_cookie=delta\n"
        "source_id=source_notion; retry_count=2\n"
        rf"failed reading {raw_values[6]}, mirror={raw_values[7]}"
    )

    stored = store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            sync_status=SyncStatus.FAILED,
            last_error=raw,
        )
    )
    with store._connect() as conn:
        persisted = conn.execute(
            "SELECT last_error FROM sources WHERE source_id = ?",
            ("source_notion",),
        ).fetchone()["last_error"]

    for value in (stored.last_error, persisted):
        assert all(raw_value not in value for raw_value in raw_values)
        assert "job_id=job-123" in value
        assert "source_id=source_notion" in value
        assert "retry_count=2" in value
        assert "<redacted>" in value


def test_metadata_store_fails_closed_for_cookie_names_that_match_diagnostic_fields(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    raw = (
        "Cookie: source_id=cookie-source-secret; job_id=cookie-job-secret; "
        "phase=cookie-phase-secret\n"
        "ordinary diagnostic, source_id=source_notion; job_id=job-123; "
        "phase=fetching_page_content"
    )

    stored = store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            sync_status=SyncStatus.FAILED,
            last_error=raw,
        )
    )
    with store._connect() as conn:
        persisted = conn.execute(
            "SELECT last_error FROM sources WHERE source_id = ?",
            ("source_notion",),
        ).fetchone()["last_error"]

    for value in (stored.last_error, persisted):
        assert "cookie-source-secret" not in value
        assert "cookie-job-secret" not in value
        assert "cookie-phase-secret" not in value
        assert "source_id=source_notion" in value
        assert "job_id=job-123" in value
        assert "phase=fetching_page_content" in value


def test_metadata_store_redacts_name_only_cookie_header_folded_lines_at_rest(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    raw = (
        "Cookie\r"
        " source_id=folded-cookie-source-secret; "
        "job_id=folded-cookie-job-secret\r"
        "\tphase=folded-cookie-phase-secret\r"
        "ordinary diagnostic, source_id=source_notion; job_id=job-123; "
        "phase=fetching_page_content"
    )

    stored = store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            sync_status=SyncStatus.FAILED,
            last_error=raw,
        )
    )
    with store._connect() as conn:
        persisted = conn.execute(
            "SELECT last_error FROM sources WHERE source_id = ?",
            ("source_notion",),
        ).fetchone()["last_error"]

    for value in (stored.last_error, persisted):
        assert "folded-cookie-source-secret" not in value
        assert "folded-cookie-job-secret" not in value
        assert "folded-cookie-phase-secret" not in value
        assert "source_id=source_notion" in value
        assert "job_id=job-123" in value
        assert "phase=fetching_page_content" in value


def test_metadata_store_redacts_lone_cr_cookie_value_continuations_at_rest(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    raw = (
        "Cookie: initial-alpha-value\r"
        " folded-alpha-value, folded-delta-value\r"
        "\tfolded-beta-value\r"
        "first clear diagnostic, source_id=source_notion; job_id=job-123\r"
        "Set-Cookie: initial-delta-value\r"
        "\tfolded-gamma-value\r"
        "second clear diagnostic, phase=fetching_page_content"
    )

    stored = store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            sync_status=SyncStatus.FAILED,
            last_error=raw,
        )
    )
    with store._connect() as conn:
        persisted = conn.execute(
            "SELECT last_error FROM sources WHERE source_id = ?",
            ("source_notion",),
        ).fetchone()["last_error"]

    for value in (stored.last_error, persisted):
        for secret in (
            "initial-alpha-value",
            "folded-alpha-value",
            "folded-delta-value",
            "folded-beta-value",
            "initial-delta-value",
            "folded-gamma-value",
        ):
            assert secret not in value
        assert "source_id=source_notion" in value
        assert "job_id=job-123" in value
        assert "phase=fetching_page_content" in value


def test_metadata_store_preserves_lone_cr_clear_diagnostic_without_comma(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    raw = (
        "Cookie: initial-alpha-value\r"
        "clear diagnostic source_id=source_notion "
        "job_id=job-123 retry_count=24"
    )

    stored = store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            sync_status=SyncStatus.FAILED,
            last_error=raw,
        )
    )
    with store._connect() as conn:
        persisted = conn.execute(
            "SELECT last_error FROM sources WHERE source_id = ?",
            ("source_notion",),
        ).fetchone()["last_error"]

    for value in (stored.last_error, persisted):
        assert "initial-alpha-value" not in value
        assert "clear diagnostic" in value
        assert "source_id=source_notion" in value
        assert "job_id=job-123" in value
        assert "retry_count=24" in value


def test_metadata_store_sanitizes_direct_source_lifecycle_writes(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    raw_upsert = _sensitive_lifecycle_text("source_notion")
    raw_register = _sensitive_lifecycle_text("source_obsidian")

    upserted = store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            sync_status=SyncStatus.FAILED,
            last_error=raw_upsert,
            stale_cleanup_disabled_reason=raw_upsert,
        )
    )
    registered = store.register_source(
        SourceModel(
            source_id="source_obsidian",
            source_type=SourceType.OBSIDIAN,
            name="Obsidian",
            enabled=False,
            sync_status=SyncStatus.FAILED,
            last_error=raw_register,
            stale_cleanup_disabled_reason=raw_register,
        )
    )

    _assert_sanitized_lifecycle_text(
        upserted.last_error,
        structured_fields=("job_id=job-123", "source_id=source_notion"),
    )
    _assert_sanitized_lifecycle_text(
        registered.last_error,
        structured_fields=("job_id=job-123", "source_id=source_obsidian"),
    )
    _assert_sanitized_lifecycle_text(upserted.stale_cleanup_disabled_reason)
    _assert_sanitized_lifecycle_text(registered.stale_cleanup_disabled_reason)

    with store._connect() as conn:
        rows = conn.execute(
            """
            SELECT source_id, last_error, stale_cleanup_disabled_reason
            FROM sources
            ORDER BY source_id
            """
        ).fetchall()
    for row in rows:
        _assert_sanitized_lifecycle_text(
            row["last_error"],
            structured_fields=("job_id=job-123", f"source_id={row['source_id']}"),
        )
        _assert_sanitized_lifecycle_text(row["stale_cleanup_disabled_reason"])


def test_metadata_store_sanitizes_direct_job_status_failure_and_cleanup_writes(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    source_id = "source_notion"
    raw = _sensitive_lifecycle_text(source_id)
    store.upsert_source(
        SourceModel(
            source_id=source_id,
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )

    queued = store.create_sync_job(source_id)
    updated = store.update_sync_job(
        queued.job_id,
        status_message=raw,
        error_message=raw,
    )
    _assert_sanitized_lifecycle_text(
        updated.status_message,
        structured_fields=("job_id=job-123", f"source_id={source_id}"),
    )
    _assert_sanitized_lifecycle_text(updated.error_message)

    failed = store.complete_failed_sync(
        job_id=queued.job_id,
        source_id=source_id,
        error_message=raw,
        stale_cleanup_disabled_reason=raw,
    )
    _assert_sanitized_lifecycle_text(
        failed.error_message,
        structured_fields=("job_id=job-123", f"source_id={source_id}"),
    )
    failed_source = store.get_source(source_id)
    assert failed_source is not None
    _assert_sanitized_lifecycle_text(failed_source.last_error)
    _assert_sanitized_lifecycle_text(failed_source.stale_cleanup_disabled_reason)

    cleanup_job, started = store.begin_sync_job(source_id)
    assert started is True
    completed, _ = store.complete_successful_sync(
        job_id=cleanup_job.job_id,
        source_id=source_id,
        total_documents=0,
        processed_documents=0,
        indexed_chunks=0,
        skipped_documents=0,
        last_seen_at="",
        cleanup_missing_documents=False,
        deleted_at="",
        stale_cleanup_disabled_reason=raw,
    )
    assert completed.status == SyncJobStatus.SUCCEEDED
    completed_source = store.get_source(source_id)
    assert completed_source is not None
    _assert_sanitized_lifecycle_text(
        completed_source.stale_cleanup_disabled_reason,
        structured_fields=("job_id=job-123", f"source_id={source_id}"),
    )

    with store._connect() as conn:
        failed_row = conn.execute(
            "SELECT status_message, error_message FROM sync_jobs WHERE job_id = ?",
            (failed.job_id,),
        ).fetchone()
        source_row = conn.execute(
            """
            SELECT last_error, stale_cleanup_disabled_reason
            FROM sources WHERE source_id = ?
            """,
            (source_id,),
        ).fetchone()
    _assert_sanitized_lifecycle_text(failed_row["status_message"])
    _assert_sanitized_lifecycle_text(failed_row["error_message"])
    assert source_row["last_error"] == ""
    _assert_sanitized_lifecycle_text(source_row["stale_cleanup_disabled_reason"])


def test_metadata_store_sanitizes_direct_disabled_enqueue_and_recovery_writes(tmp_path):
    store = MetadataStore(
        tmp_path / "contextwiki.sqlite3",
        unowned_running_job_grace_seconds=0,
    )
    raw_disabled = _sensitive_lifecycle_text("source_obsidian")
    store.upsert_source(
        SourceModel(
            source_id="source_obsidian",
            source_type=SourceType.OBSIDIAN,
            name="Obsidian",
            enabled=False,
            sync_status=SyncStatus.IDLE,
        )
    )

    disabled, created = store.enqueue_sync_job(
        "source_obsidian",
        disabled_error_message=raw_disabled,
        disabled_stale_cleanup_reason=raw_disabled,
    )

    assert created is True
    assert disabled.status == SyncJobStatus.FAILED
    _assert_sanitized_lifecycle_text(
        disabled.error_message,
        structured_fields=("job_id=job-123", "source_id=source_obsidian"),
    )
    disabled_source = store.get_source("source_obsidian")
    assert disabled_source is not None
    _assert_sanitized_lifecycle_text(disabled_source.last_error)
    _assert_sanitized_lifecycle_text(disabled_source.stale_cleanup_disabled_reason)

    source_id = "source_notion"
    raw_recovery = _sensitive_lifecycle_text(source_id)
    store.upsert_source(
        SourceModel(
            source_id=source_id,
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    orphan = store.create_sync_job(source_id)
    old_started_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    _mark_job_running(
        store,
        orphan.job_id,
        started_at=old_started_at,
        owner_id="missing-owner",
    )

    recovered = store.recover_orphaned_running_jobs(
        started_before=datetime.now(timezone.utc).isoformat(),
        error_message=raw_recovery,
        source_ids=(source_id,),
    )

    assert recovered == 1
    recovered_job = store.get_sync_job(orphan.job_id)
    recovered_source = store.get_source(source_id)
    assert recovered_job is not None
    assert recovered_source is not None
    _assert_sanitized_lifecycle_text(
        recovered_job.error_message,
        structured_fields=("job_id=job-123", f"source_id={source_id}"),
    )
    _assert_sanitized_lifecycle_text(recovered_source.last_error)

    with store._connect() as conn:
        rows = conn.execute(
            """
            SELECT status_message, error_message
            FROM sync_jobs
            WHERE job_id IN (?, ?)
            ORDER BY job_id
            """,
            (disabled.job_id, orphan.job_id),
        ).fetchall()
    for row in rows:
        _assert_sanitized_lifecycle_text(row["status_message"])
        _assert_sanitized_lifecycle_text(row["error_message"])


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


def test_get_latest_sync_job_breaks_started_at_ties_by_rowid(tmp_path):
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
    first = store.create_sync_job("source_github")
    second = store.create_sync_job("source_github")
    tied_started_at = "2026-06-15T00:00:00+00:00"
    with store._connect() as conn:
        conn.execute(
            "UPDATE sync_jobs SET started_at = ?, status = ? WHERE job_id IN (?, ?)",
            (
                tied_started_at,
                SyncJobStatus.FAILED.value,
                first.job_id,
                second.job_id,
            ),
        )

    latest = store.get_latest_sync_job("source_github")

    assert latest.job_id == second.job_id


def test_get_latest_sync_job_prefers_newest_running_row_when_started_at_ties(tmp_path):
    store = MetadataStore(
        tmp_path / "contextwiki.sqlite3",
        running_job_timeout_seconds=24 * 60 * 60,
        unowned_running_job_grace_seconds=24 * 60 * 60,
    )
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
    first = store.create_sync_job("source_github")
    second = store.create_sync_job("source_github")
    tied_started_at = datetime.now(timezone.utc).isoformat()
    _mark_job_running(store, first.job_id, started_at=tied_started_at)
    _mark_job_running(store, second.job_id, started_at=tied_started_at)

    latest = store.get_latest_sync_job("source_github")
    first_job = store.get_sync_job(first.job_id)
    second_job = store.get_sync_job(second.job_id)

    assert latest.job_id == second.job_id
    assert second_job.status == SyncJobStatus.RUNNING
    assert first_job.status == SyncJobStatus.FAILED


def test_source_status_snapshot_prefers_newest_finished_rows_when_timestamps_tie(tmp_path):
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
    success_first = store.create_sync_job("source_github")
    success_second = store.create_sync_job("source_github")
    failure_first = store.create_sync_job("source_github")
    failure_second = store.create_sync_job("source_github")
    tied_finished_at = "2026-06-15T00:00:00+00:00"
    with store._connect() as conn:
        conn.execute(
            """
            UPDATE sync_jobs
            SET status = ?, started_at = ?, finished_at = ?, error_message = ''
            WHERE job_id IN (?, ?)
            """,
            (
                SyncJobStatus.SUCCEEDED.value,
                tied_finished_at,
                tied_finished_at,
                success_first.job_id,
                success_second.job_id,
            ),
        )
        conn.execute(
            """
            UPDATE sync_jobs
            SET status = ?, started_at = ?, finished_at = ?, error_message = ?
            WHERE job_id = ?
            """,
            (
                SyncJobStatus.FAILED.value,
                tied_finished_at,
                tied_finished_at,
                "older failure",
                failure_first.job_id,
            ),
        )
        conn.execute(
            """
            UPDATE sync_jobs
            SET status = ?, started_at = ?, finished_at = ?, error_message = ?
            WHERE job_id = ?
            """,
            (
                SyncJobStatus.FAILED.value,
                tied_finished_at,
                tied_finished_at,
                "newer failure",
                failure_second.job_id,
            ),
        )

    snapshot = store.get_source_status_snapshot("source_github")

    assert snapshot["latest_success_at"] == tied_finished_at
    assert snapshot["latest_failure_at"] == tied_finished_at
    assert snapshot["latest_failure_reason"] == "newer failure"


def test_metadata_store_loads_obsidian_source_rows(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")

    source = store.upsert_source(
        SourceModel(
            source_id="source_obsidian",
            source_type=SourceType.OBSIDIAN,
            name="Obsidian",
            enabled=False,
            auth_ref="env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH",
            sync_status=SyncStatus.IDLE,
            last_error=(
                "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
                "is not set or is not an existing directory."
            ),
        )
    )

    persisted = store.get_source("source_obsidian")

    assert source.source_id == "source_obsidian"
    assert persisted is not None
    assert persisted.source_type == SourceType.OBSIDIAN
    assert persisted.enabled is False
    assert persisted.auth_ref == "env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH"
    assert store.list_sources() == [persisted]


@pytest.mark.parametrize("write_method", ["upsert_source", "register_source"])
@pytest.mark.parametrize(
    "unsafe_auth_ref",
    [
        "ntn_abcdefghijklmnopqrstuvwxyz0123456789",
        "secret_abcdefghijklmnopqrstuvwxyz0123456789",
        "env:lowercase_secret",
        "env:NOTION_API_KEY trailing-secret",
    ],
)
def test_metadata_store_never_persists_noncanonical_auth_refs(
    tmp_path,
    write_method,
    unsafe_auth_ref,
):
    store = MetadataStore(tmp_path / f"{write_method}.sqlite3")
    persisted = getattr(store, write_method)(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            auth_ref=unsafe_auth_ref,
            sync_status=SyncStatus.IDLE,
        )
    )

    with store._connect() as conn:
        row = conn.execute(
            "SELECT auth_ref FROM sources WHERE source_id = ?",
            ("source_notion",),
        ).fetchone()

    assert persisted.auth_ref == ""
    assert store.get_source("source_notion").auth_ref == ""
    assert row["auth_ref"] == ""
    assert "ntn_" not in row["auth_ref"]
    assert "secret_" not in row["auth_ref"]


@pytest.mark.parametrize("write_method", ["upsert_source", "register_source"])
def test_metadata_store_preserves_canonical_auth_refs(tmp_path, write_method):
    store = MetadataStore(tmp_path / f"{write_method}.sqlite3")

    persisted = getattr(store, write_method)(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            auth_ref="env:NOTION_API_KEY",
            sync_status=SyncStatus.IDLE,
        )
    )

    with store._connect() as conn:
        row = conn.execute(
            "SELECT auth_ref FROM sources WHERE source_id = ?",
            ("source_notion",),
        ).fetchone()

    assert persisted.auth_ref == "env:NOTION_API_KEY"
    assert row["auth_ref"] == "env:NOTION_API_KEY"


@pytest.mark.parametrize(
    "phase",
    [
        "fetching /Users/tester/private vault/notes.md",
        r"fetching C:\Users\tester\private vault\notes.md",
        "ntn_abcdefghijklmnopqrstuvwxyz0123456789",
        "secret_abcdefghijklmnopqrstuvwxyz0123456789",
        "fetching_page_content trailing-data",
    ],
)
def test_metadata_store_never_persists_noncanonical_job_phase(tmp_path, phase):
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

    updated = store.update_sync_job(job.job_id, phase=phase)

    with store._connect() as conn:
        row = conn.execute(
            "SELECT phase FROM sync_jobs WHERE job_id = ?",
            (job.job_id,),
        ).fetchone()
    assert started is True
    assert updated.phase == ""
    assert row["phase"] == ""
    assert phase not in row["phase"]


@pytest.mark.parametrize(
    "phase",
    [
        "",
        "starting",
        "discovering_pages",
        "fetching_page_content",
        "indexing_documents",
        "completed",
        "failed",
    ],
)
def test_metadata_store_preserves_canonical_job_phases(tmp_path, phase):
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

    updated = store.update_sync_job(job.job_id, phase=phase)

    assert started is True
    assert updated.phase == phase


def test_legacy_removed_source_rows_are_skipped_without_deleting_data(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    _insert_legacy_web_source_row(store)
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id="legacy-doc",
            document_id="legacy-doc",
            source_id="source_web",
            title="Legacy Web Doc",
            content="Legacy web content should stay in storage.",
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
                text="Legacy web content should stay in storage.",
                url="https://example.com/legacy",
                path="/legacy",
                chunk_index=0,
                content_hash="legacy-hash",
            )
        ],
    )

    assert store.get_source("source_web") is None
    assert store.list_sources() == []
    assert store.get_document("legacy-doc").source_id == "source_web"
    assert store.get_chunk("legacy-chunk").source_id == "source_web"
    with store._connect() as conn:
        row = conn.execute(
            "SELECT source_type FROM sources WHERE source_id = ?",
            ("source_web",),
        ).fetchone()
    assert row["source_type"] == "web"


def test_scoped_orphan_recovery_does_not_mutate_legacy_removed_sources(tmp_path):
    store = MetadataStore(
        tmp_path / "contextwiki.sqlite3",
        running_job_timeout_seconds=0,
        unowned_running_job_grace_seconds=0,
    )
    _insert_legacy_web_source_row(store)
    legacy_job = store.create_sync_job("source_web")
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    _mark_job_running(
        store,
        legacy_job.job_id,
        started_at=old_timestamp,
        heartbeat_at=old_timestamp,
    )
    with store._connect() as conn:
        conn.execute(
            """
            UPDATE sources SET sync_status = ?, updated_at = ?
            WHERE source_id = ?
            """,
            (SyncStatus.RUNNING.value, old_timestamp, "source_web"),
        )

    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    retained_job = store.create_sync_job("source_github")
    _mark_job_running(
        store,
        retained_job.job_id,
        started_at=old_timestamp,
        heartbeat_at=old_timestamp,
    )

    recovered_count = store.recover_orphaned_running_jobs(
        started_before=datetime.now(timezone.utc).isoformat(),
        error_message="restart recovery",
        source_ids=["source_github"],
    )

    assert recovered_count == 1
    assert _raw_source_and_job_status(store, "source_web", legacy_job.job_id) == (
        SyncStatus.RUNNING.value,
        SyncJobStatus.RUNNING.value,
    )
    assert store.get_source("source_github").sync_status == SyncStatus.FAILED
    assert store.get_sync_job(retained_job.job_id).status == SyncJobStatus.FAILED


def test_scoped_claim_recovers_stale_out_of_scope_legacy_source_without_mutating_content(
    tmp_path,
):
    store = MetadataStore(
        tmp_path / "contextwiki.sqlite3",
        sync_owner_id="retained-worker",
        running_job_timeout_seconds=3600,
        unowned_running_job_grace_seconds=0,
    )
    _insert_legacy_web_source_row(store)
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id="legacy-doc",
            document_id="legacy-doc",
            source_id="source_web",
            title="Legacy Web Doc",
            content="Legacy content must not change during job recovery.",
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
                text="Legacy content must not change during job recovery.",
                url="https://example.com/legacy",
                path="/legacy",
                chunk_index=0,
                content_hash="legacy-hash",
            )
        ],
    )
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO chunk_tombstones (
                chunk_id, document_id, source_id, recorded_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                "legacy-tombstone",
                "legacy-deleted-doc",
                "source_web",
                "2000-01-01T00:00:00+00:00",
            ),
        )
    legacy_job = store.create_sync_job("source_web")
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    _mark_job_running(
        store,
        legacy_job.job_id,
        started_at=old_timestamp,
        heartbeat_at=old_timestamp,
    )
    with store._connect() as conn:
        conn.execute(
            """
            UPDATE sources SET sync_status = ?, last_error = '', updated_at = ?
            WHERE source_id = ?
            """,
            (SyncStatus.RUNNING.value, old_timestamp, "source_web"),
        )
        before_documents = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM documents WHERE source_id = ? ORDER BY document_id",
                ("source_web",),
            ).fetchall()
        ]
        before_chunks = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM chunks WHERE source_id = ? ORDER BY chunk_id",
                ("source_web",),
            ).fetchall()
        ]
        before_tombstones = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM chunk_tombstones
                WHERE source_id = ?
                ORDER BY chunk_id
                """,
                ("source_web",),
            ).fetchall()
        ]

    store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
        )
    )
    retained_job, enqueued = store.enqueue_sync_job("source_notion")

    claimed = store.claim_next_sync_job(["source_notion"])

    assert enqueued is True
    assert claimed is not None
    assert claimed.job_id == retained_job.job_id
    assert claimed.status == SyncJobStatus.RUNNING
    assert _raw_source_and_job_status(store, "source_web", legacy_job.job_id) == (
        SyncStatus.FAILED.value,
        SyncJobStatus.FAILED.value,
    )
    assert (
        store.get_sync_job(legacy_job.job_id).error_message
        == ORPHANED_SYNC_JOB_RECOVERY_MESSAGE
    )
    assert store.get_sync_job(retained_job.job_id).status == SyncJobStatus.RUNNING
    with store._connect() as conn:
        assert [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM documents WHERE source_id = ? ORDER BY document_id",
                ("source_web",),
            ).fetchall()
        ] == before_documents
        assert [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM chunks WHERE source_id = ? ORDER BY chunk_id",
                ("source_web",),
            ).fetchall()
        ] == before_chunks
        assert [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM chunk_tombstones
                WHERE source_id = ?
                ORDER BY chunk_id
                """,
                ("source_web",),
            ).fetchall()
        ] == before_tombstones


def test_scoped_claim_preserves_live_out_of_scope_global_running_blocker(
    tmp_path,
    monkeypatch,
):
    store = MetadataStore(
        tmp_path / "contextwiki.sqlite3",
        sync_owner_id="retained-worker",
        running_job_timeout_seconds=3600,
    )
    _insert_legacy_web_source_row(store)
    legacy_job = store.create_sync_job("source_web")
    _mark_job_running(
        store,
        legacy_job.job_id,
        owner_id="legacy-worker",
    )
    with store._connect() as conn:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO sync_job_owners (
                owner_id, process_id, process_start_id, started_at, heartbeat_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("legacy-worker", 4242, "linux:boot-a:100", now, now),
        )
        conn.execute(
            """
            UPDATE sources SET sync_status = ?, last_error = '', updated_at = ?
            WHERE source_id = ?
            """,
            (SyncStatus.RUNNING.value, now, "source_web"),
        )
    monkeypatch.setattr(
        MetadataStore,
        "_is_process_alive",
        staticmethod(lambda process_id: process_id == 4242),
    )
    monkeypatch.setattr(
        MetadataStore,
        "_get_process_start_identity",
        staticmethod(lambda process_id: "linux:boot-a:100"),
    )

    store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
        )
    )
    retained_job, enqueued = store.enqueue_sync_job("source_notion")

    claimed = store.claim_next_sync_job(["source_notion"])

    assert enqueued is True
    assert claimed is None
    assert _raw_source_and_job_status(store, "source_web", legacy_job.job_id) == (
        SyncStatus.RUNNING.value,
        SyncJobStatus.RUNNING.value,
    )
    assert store.get_sync_job(retained_job.job_id).status == SyncJobStatus.QUEUED


def test_initialized_metadata_reads_do_not_wait_for_unrelated_writer(tmp_path):
    db_path = tmp_path / "contextwiki.sqlite3"
    writer_store = MetadataStore(db_path, sync_owner_id="writer")
    writer_store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
        )
    )
    job = writer_store.create_sync_job("source_notion")
    observer_store = MetadataStore(db_path, sync_owner_id="observer")
    observer_store.ensure_schema()

    writer_conn = sqlite3.connect(db_path)
    writer_conn.execute("BEGIN IMMEDIATE")
    writer_conn.execute(
        "UPDATE sources SET updated_at = updated_at WHERE source_id = ?",
        ("source_notion",),
    )
    writer_closed = False
    timed_out = False
    started_at = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            source_future = executor.submit(observer_store.list_sources)
            job_future = executor.submit(writer_store.get_sync_job, job.job_id)
            try:
                sources = source_future.result(timeout=0.5)
                observed_job = job_future.result(timeout=0.5)
            except FutureTimeoutError:
                timed_out = True
            finally:
                writer_conn.rollback()
                writer_conn.close()
                writer_closed = True
            source_future.result(timeout=5)
            job_future.result(timeout=5)
    finally:
        if not writer_closed:
            if writer_conn.in_transaction:
                writer_conn.rollback()
            writer_conn.close()

    assert timed_out is False
    assert time.monotonic() - started_at < 1.0
    assert [source.source_id for source in sources] == ["source_notion"]
    assert observed_job.job_id == job.job_id


@pytest.mark.parametrize(
    ("job_state", "expected_status"),
    [
        ("queued", SyncJobStatus.QUEUED),
        ("running", SyncJobStatus.RUNNING),
        ("terminal", SyncJobStatus.SUCCEEDED),
    ],
)
def test_latest_sync_job_observation_does_not_wait_for_unrelated_writer(
    tmp_path,
    job_state,
    expected_status,
):
    db_path = tmp_path / f"{job_state}.sqlite3"
    store = MetadataStore(db_path, sync_owner_id="status-owner")
    store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
        )
    )
    if job_state == "queued":
        expected_job, enqueued = store.enqueue_sync_job("source_notion")
        assert enqueued is True
    else:
        expected_job, started = store.begin_sync_job("source_notion")
        assert started is True
        if job_state == "terminal":
            expected_job, deleted_chunk_ids = store.complete_successful_sync(
                job_id=expected_job.job_id,
                source_id="source_notion",
                total_documents=0,
                processed_documents=0,
                indexed_chunks=0,
                skipped_documents=0,
                last_seen_at=datetime.now(timezone.utc).isoformat(),
                cleanup_missing_documents=False,
                deleted_at=datetime.now(timezone.utc).isoformat(),
            )
            assert deleted_chunk_ids == []

    writer_conn = sqlite3.connect(db_path)
    writer_conn.execute("BEGIN IMMEDIATE")
    writer_conn.execute(
        "UPDATE sources SET updated_at = updated_at WHERE source_id = ?",
        ("source_notion",),
    )
    writer_closed = False
    timed_out = False
    started_at = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            status_future = executor.submit(
                store.get_latest_sync_job,
                "source_notion",
            )
            try:
                observed_job = status_future.result(timeout=0.5)
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
    assert time.monotonic() - started_at < 1.0
    assert observed_job is not None
    assert observed_job.job_id == expected_job.job_id
    assert observed_job.status == expected_status


def test_latest_sync_job_stale_reconciliation_is_atomic(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "stale-status.sqlite3"
    store = MetadataStore(
        db_path,
        sync_owner_id="status-owner",
        running_job_timeout_seconds=0,
    )
    store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
        )
    )
    stale_job, started = store.begin_sync_job("source_notion")
    assert started is True
    observer = MetadataStore(db_path, sync_owner_id="observer")
    observer.ensure_schema()

    reconciliation_started = Event()
    release_reconciliation = Event()
    original_reconcile = store._reconcile_source_after_inactive_job

    def pause_before_source_reconciliation(
        conn,
        source_id,
        finished_at,
        error_message,
    ):
        reconciliation_started.set()
        assert release_reconciliation.wait(timeout=5)
        return original_reconcile(
            conn,
            source_id,
            finished_at,
            error_message,
        )

    monkeypatch.setattr(
        store,
        "_reconcile_source_after_inactive_job",
        pause_before_source_reconciliation,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        status_future = executor.submit(
            store.get_latest_sync_job,
            "source_notion",
        )
        assert reconciliation_started.wait(timeout=5)
        before_commit = _raw_source_and_job_status(
            observer,
            "source_notion",
            stale_job.job_id,
        )
        release_reconciliation.set()
        reconciled_job = status_future.result(timeout=5)

    assert before_commit == (
        SyncStatus.RUNNING.value,
        SyncJobStatus.RUNNING.value,
    )
    assert reconciled_job is not None
    assert reconciled_job.job_id == stale_job.job_id
    assert reconciled_job.status == SyncJobStatus.FAILED
    assert _raw_source_and_job_status(
        observer,
        "source_notion",
        stale_job.job_id,
    ) == (
        SyncStatus.FAILED.value,
        SyncJobStatus.FAILED.value,
    )


def test_initialized_waiter_reads_while_worker_claim_transaction_is_open(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "contextwiki.sqlite3"
    requester = MetadataStore(db_path, sync_owner_id="requester")
    requester.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
        )
    )
    queued_job, enqueued = requester.enqueue_sync_job("source_notion")
    worker = MetadataStore(db_path, sync_owner_id="worker")
    worker.ensure_schema()
    transaction_open = Event()
    release_transaction = Event()
    original_touch_sync_owner = worker._touch_sync_owner

    def pause_claim_transaction(conn, timestamp):
        original_touch_sync_owner(conn, timestamp)
        transaction_open.set()
        assert release_transaction.wait(timeout=5)

    monkeypatch.setattr(worker, "_touch_sync_owner", pause_claim_transaction)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claim_future = executor.submit(
            worker.claim_next_sync_job,
            ["source_notion"],
        )
        assert transaction_open.wait(timeout=5)
        waiter_future = executor.submit(requester.get_sync_job, queued_job.job_id)
        try:
            observed_job = waiter_future.result(timeout=0.5)
        finally:
            release_transaction.set()
        claimed_job = claim_future.result(timeout=5)

    assert enqueued is True
    assert observed_job.status == SyncJobStatus.QUEUED
    assert claimed_job is not None
    assert claimed_job.job_id == queued_job.job_id
    assert requester.get_sync_job(queued_job.job_id).status == SyncJobStatus.RUNNING


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


def test_connection_context_commits_rolls_back_and_closes(tmp_path, monkeypatch):
    opened_connections = []
    original_connect = sqlite3.connect

    class TrackingConnection(sqlite3.Connection):
        close_count = 0

        def close(self):
            self.close_count += 1
            super().close()

    def tracking_connect(*args, **kwargs):
        kwargs["factory"] = TrackingConnection
        connection = original_connect(*args, **kwargs)
        opened_connections.append(connection)
        return connection

    monkeypatch.setattr(
        "storage.metadata_store.sqlite3.connect",
        tracking_connect,
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")

    with store._connect() as conn:
        conn.execute("CREATE TABLE transaction_probe (value TEXT NOT NULL)")
        conn.execute("INSERT INTO transaction_probe VALUES ('committed')")

    with pytest.raises(RuntimeError, match="rollback probe"):
        with store._connect() as conn:
            conn.execute("INSERT INTO transaction_probe VALUES ('rolled back')")
            raise RuntimeError("rollback probe")

    with store._connect() as conn:
        values = [
            row["value"]
            for row in conn.execute(
                "SELECT value FROM transaction_probe ORDER BY rowid"
            ).fetchall()
        ]

    assert values == ["committed"]
    assert opened_connections
    assert all(connection.close_count == 1 for connection in opened_connections)
    for connection in opened_connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")


def test_repeated_metadata_access_emits_no_unclosed_sqlite_resource_warning(tmp_path):
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

    with warnings.catch_warnings(record=True) as caught_warnings:
        warnings.simplefilter("always", ResourceWarning)
        for _ in range(25):
            assert store.get_source("source_github") is not None
            assert len(store.list_sources()) == 1
        gc.collect()

    unclosed_sqlite_warnings = [
        warning
        for warning in caught_warnings
        if issubclass(warning.category, ResourceWarning)
        and "sqlite3.Connection" in str(warning.message)
    ]
    assert unclosed_sqlite_warnings == []


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
        return job.job_id, started, local_store.sync_owner_id

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(lambda _: begin_from_new_connection(), range(worker_count)))

    started_results = [result for result in results if result[1]]
    job_ids = {job_id for job_id, _, _ in results}
    with store._connect() as conn:
        running_row = conn.execute(
            """
            SELECT job_id, owner_id
            FROM sync_jobs
            WHERE source_id = ? AND status = ?
            """,
            ("source_github", SyncJobStatus.RUNNING.value),
        ).fetchone()
        owner_ids = {
            row["owner_id"]
            for row in conn.execute(
                "SELECT owner_id FROM sync_job_owners"
            ).fetchall()
        }

    assert len(started_results) == 1
    assert len(job_ids) == 1
    assert running_row["job_id"] in job_ids
    assert running_row["owner_id"] == started_results[0][2]
    assert owner_ids == {running_row["owner_id"]}


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


def test_recover_orphaned_running_jobs_fails_old_job_and_allows_fresh_sync(tmp_path):
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
    orphan, started = store.begin_sync_job("source_github")
    assert started is True
    document = DocumentModel(
        id="doc-claimed",
        source_id="source_github",
        title="Claimed",
        content="Claimed by an orphan job",
        url="https://github.com/example/repo/blob/main/README.md",
        platform="GitHub",
    )
    store.validate_running_job_document(orphan.job_id, document)
    _mark_job_running(
        store,
        orphan.job_id,
        started_at="2026-06-01T00:00:00+00:00",
        heartbeat_at="2026-06-01T00:00:00+00:00",
    )

    recovered_count = store.recover_orphaned_running_jobs(
        started_before="2026-06-02T00:00:00+00:00",
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
    )
    recovered_source = store.get_source("source_github")
    fresh, fresh_started = store.begin_sync_job("source_github")

    with store._connect() as conn:
        claim_count = conn.execute(
            "SELECT COUNT(*) AS count FROM document_claims WHERE job_id = ?",
            (orphan.job_id,),
        ).fetchone()["count"]

    failed_orphan = store.get_sync_job(orphan.job_id)
    source = store.get_source("source_github")
    assert recovered_count == 1
    assert failed_orphan.status == SyncJobStatus.FAILED
    assert "execution owner stopped responding" in failed_orphan.error_message
    assert claim_count == 0
    assert recovered_source.sync_status == SyncStatus.FAILED
    assert "execution owner stopped responding" in recovered_source.last_error
    assert source.sync_status == SyncStatus.RUNNING
    assert source.last_error == ""
    assert fresh_started is True
    assert fresh.job_id != orphan.job_id


def test_recover_orphaned_running_jobs_preserves_jobs_started_after_cutoff(tmp_path):
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
    active, started = store.begin_sync_job("source_github")
    assert started is True
    recent_timestamp = datetime.now(timezone.utc).isoformat()
    _mark_job_running(
        store,
        active.job_id,
        started_at="2026-06-02T00:00:01+00:00",
        heartbeat_at=recent_timestamp,
    )

    recovered_count = store.recover_orphaned_running_jobs(
        started_before="2026-06-02T00:00:00+00:00",
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
    )
    returned, started = store.begin_sync_job("source_github")

    assert recovered_count == 0
    assert store.get_sync_job(active.job_id).status == SyncJobStatus.RUNNING
    assert store.get_source("source_github").sync_status == SyncStatus.RUNNING
    assert started is False
    assert returned.job_id == active.job_id


def test_recover_orphaned_running_jobs_preserves_fresh_owned_job_started_before_cutoff(tmp_path):
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
    active, started = store.begin_sync_job("source_github")
    assert started is True
    _mark_job_running(
        store,
        active.job_id,
        started_at="2000-01-01T00:00:00+00:00",
        heartbeat_at=datetime.now(timezone.utc).isoformat(),
    )

    recovered_count = store.recover_orphaned_running_jobs(
        started_before="2026-06-02T00:00:00+00:00",
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
    )
    returned, started = store.begin_sync_job("source_github")

    assert recovered_count == 0
    assert store.get_sync_job(active.job_id).status == SyncJobStatus.RUNNING
    assert started is False
    assert returned.job_id == active.job_id


def test_recover_orphaned_running_jobs_recovers_dead_previous_owner(tmp_path, monkeypatch):
    store = MetadataStore(
        tmp_path / "contextwiki.sqlite3",
        running_job_timeout_seconds=24 * 60 * 60,
        sync_owner_id="current-owner",
    )
    previous_store = MetadataStore(
        store.db_path,
        running_job_timeout_seconds=24 * 60 * 60,
        sync_owner_id="previous-owner",
    )
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    previous_job, started = previous_store.begin_sync_job("source_github")
    assert started is True
    _mark_job_running(
        store,
        previous_job.job_id,
        started_at="2026-06-01T00:00:00+00:00",
        heartbeat_at=datetime.now(timezone.utc).isoformat(),
    )
    monkeypatch.setattr(MetadataStore, "_is_process_alive", staticmethod(lambda process_id: False))

    recovered_count = store.recover_orphaned_running_jobs(
        started_before="2026-06-02T00:00:00+00:00",
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
    )
    fresh, fresh_started = store.begin_sync_job("source_github")

    assert recovered_count == 1
    assert store.get_sync_job(previous_job.job_id).status == SyncJobStatus.FAILED
    assert fresh_started is True
    assert fresh.job_id != previous_job.job_id


def test_recover_orphaned_running_jobs_recovers_stale_previous_owner_even_if_pid_looks_alive(
    tmp_path,
    monkeypatch,
):
    store = MetadataStore(
        tmp_path / "contextwiki.sqlite3",
        running_job_timeout_seconds=24 * 60 * 60,
        sync_owner_id="current-owner",
    )
    previous_store = MetadataStore(
        store.db_path,
        running_job_timeout_seconds=24 * 60 * 60,
        sync_owner_id="previous-owner",
    )
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    previous_job, started = previous_store.begin_sync_job("source_github")
    assert started is True
    stale_owner_heartbeat = "2026-06-01T00:00:00+00:00"
    _mark_job_running(
        store,
        previous_job.job_id,
        started_at=stale_owner_heartbeat,
        heartbeat_at=stale_owner_heartbeat,
    )
    _mark_owner_heartbeat(
        store,
        "previous-owner",
        started_at=stale_owner_heartbeat,
        heartbeat_at=stale_owner_heartbeat,
    )
    monkeypatch.setattr(MetadataStore, "_is_process_alive", staticmethod(lambda process_id: True))

    recovered_count = store.recover_orphaned_running_jobs(
        started_before="2026-06-02T00:00:00+00:00",
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
    )
    fresh, fresh_started = store.begin_sync_job("source_github")

    assert recovered_count == 1
    assert store.get_sync_job(previous_job.job_id).status == SyncJobStatus.FAILED
    assert fresh_started is True
    assert fresh.job_id != previous_job.job_id


def test_recover_orphaned_running_jobs_preserves_same_pid_previous_owner_when_job_heartbeat_is_fresh(
    tmp_path,
    monkeypatch,
):
    store = MetadataStore(
        tmp_path / "contextwiki.sqlite3",
        running_job_timeout_seconds=24 * 60 * 60,
        sync_owner_id="current-owner",
    )
    previous_store = MetadataStore(
        store.db_path,
        running_job_timeout_seconds=24 * 60 * 60,
        sync_owner_id="previous-owner",
    )
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    previous_job, started = previous_store.begin_sync_job("source_github")
    assert started is True
    fresh_job_heartbeat = datetime.now(timezone.utc).isoformat()
    stale_owner_heartbeat = "2026-06-01T00:00:00+00:00"
    _mark_job_running(
        store,
        previous_job.job_id,
        started_at="2026-06-01T00:00:00+00:00",
        heartbeat_at=fresh_job_heartbeat,
    )
    _mark_owner_heartbeat(
        store,
        "previous-owner",
        started_at=stale_owner_heartbeat,
        heartbeat_at=stale_owner_heartbeat,
    )
    with store._connect() as conn:
        conn.execute(
            """
            UPDATE sync_job_owners
            SET process_start_id = ''
            WHERE owner_id = ?
            """,
            ("previous-owner",),
        )
    monkeypatch.setattr(MetadataStore, "_is_process_alive", staticmethod(lambda process_id: True))
    monkeypatch.setattr(
        MetadataStore,
        "_get_process_start_identity",
        staticmethod(lambda process_id: "different-process-instance"),
    )

    recovered_count = store.recover_orphaned_running_jobs(
        started_before="2026-06-02T00:00:00+00:00",
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
    )
    returned, started = store.begin_sync_job("source_github")

    assert recovered_count == 0
    assert store.get_sync_job(previous_job.job_id).status == SyncJobStatus.RUNNING
    assert started is False
    assert returned.job_id == previous_job.job_id


def test_begin_sync_job_preserves_same_pid_previous_owner_when_job_heartbeat_is_fresh(
    tmp_path,
    monkeypatch,
):
    store = MetadataStore(
        tmp_path / "contextwiki.sqlite3",
        running_job_timeout_seconds=24 * 60 * 60,
        sync_owner_id="current-owner",
    )
    previous_store = MetadataStore(
        store.db_path,
        running_job_timeout_seconds=24 * 60 * 60,
        sync_owner_id="previous-owner",
    )
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    previous_job, started = previous_store.begin_sync_job("source_github")
    assert started is True
    fresh_job_heartbeat = datetime.now(timezone.utc).isoformat()
    stale_owner_heartbeat = "2026-06-01T00:00:00+00:00"
    _mark_job_running(
        store,
        previous_job.job_id,
        started_at="2026-06-01T00:00:00+00:00",
        heartbeat_at=fresh_job_heartbeat,
    )
    _mark_owner_heartbeat(
        store,
        "previous-owner",
        started_at=stale_owner_heartbeat,
        heartbeat_at=stale_owner_heartbeat,
    )
    monkeypatch.setattr(MetadataStore, "_is_process_alive", staticmethod(lambda process_id: True))

    returned, started = store.begin_sync_job("source_github")

    assert started is False
    assert returned.job_id == previous_job.job_id
    assert store.get_sync_job(previous_job.job_id).status == SyncJobStatus.RUNNING


def test_begin_sync_job_recovers_stale_same_pid_previous_owner_and_starts_fresh_job(
    tmp_path,
    monkeypatch,
):
    store = MetadataStore(
        tmp_path / "contextwiki.sqlite3",
        running_job_timeout_seconds=24 * 60 * 60,
        sync_owner_id="current-owner",
    )
    previous_store = MetadataStore(
        store.db_path,
        running_job_timeout_seconds=24 * 60 * 60,
        sync_owner_id="previous-owner",
    )
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    previous_job, started = previous_store.begin_sync_job("source_github")
    assert started is True
    stale_timestamp = "2026-06-01T00:00:00+00:00"
    _mark_job_running(
        store,
        previous_job.job_id,
        started_at=stale_timestamp,
        heartbeat_at=stale_timestamp,
    )
    _mark_owner_heartbeat(
        store,
        "previous-owner",
        started_at=stale_timestamp,
        heartbeat_at=stale_timestamp,
    )
    monkeypatch.setattr(MetadataStore, "_is_process_alive", staticmethod(lambda process_id: True))

    fresh, fresh_started = store.begin_sync_job("source_github")

    assert fresh_started is True
    assert fresh.job_id != previous_job.job_id
    assert store.get_sync_job(previous_job.job_id).status == SyncJobStatus.FAILED


def test_recover_orphaned_running_jobs_preserves_live_previous_owner(tmp_path, monkeypatch):
    store = MetadataStore(
        tmp_path / "contextwiki.sqlite3",
        running_job_timeout_seconds=24 * 60 * 60,
        sync_owner_id="current-owner",
    )
    previous_store = MetadataStore(
        store.db_path,
        running_job_timeout_seconds=24 * 60 * 60,
        sync_owner_id="previous-owner",
    )
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    previous_job, started = previous_store.begin_sync_job("source_github")
    assert started is True
    _mark_job_running(
        store,
        previous_job.job_id,
        started_at="2026-06-01T00:00:00+00:00",
        heartbeat_at=datetime.now(timezone.utc).isoformat(),
    )
    monkeypatch.setattr(MetadataStore, "_is_process_alive", staticmethod(lambda process_id: True))

    recovered_count = store.recover_orphaned_running_jobs(
        started_before="2026-06-02T00:00:00+00:00",
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
    )
    returned, started = store.begin_sync_job("source_github")

    assert recovered_count == 0
    assert store.get_sync_job(previous_job.job_id).status == SyncJobStatus.RUNNING
    assert started is False
    assert returned.job_id == previous_job.job_id


def test_begin_sync_job_preserves_owned_job_after_unowned_grace(tmp_path):
    store = MetadataStore(
        tmp_path / "contextwiki.sqlite3",
        running_job_timeout_seconds=24 * 60 * 60,
        unowned_running_job_grace_seconds=60,
    )
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    active, started = store.begin_sync_job("source_github")
    assert started is True
    older_than_unowned_grace = (
        datetime.now(timezone.utc) - timedelta(seconds=120)
    ).isoformat()
    _mark_job_running(
        store,
        active.job_id,
        started_at=older_than_unowned_grace,
        heartbeat_at=older_than_unowned_grace,
    )

    latest = store.get_latest_sync_job("source_github")
    returned, started = store.begin_sync_job("source_github")

    assert latest.status == SyncJobStatus.RUNNING
    assert started is False
    assert returned.job_id == active.job_id


def test_begin_sync_job_recovers_previous_owner_that_dies_after_startup(tmp_path, monkeypatch):
    store = MetadataStore(
        tmp_path / "contextwiki.sqlite3",
        running_job_timeout_seconds=24 * 60 * 60,
        sync_owner_id="current-owner",
    )
    previous_store = MetadataStore(
        store.db_path,
        running_job_timeout_seconds=24 * 60 * 60,
        sync_owner_id="previous-owner",
    )
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    previous_job, started = previous_store.begin_sync_job("source_github")
    assert started is True
    _mark_job_running(
        store,
        previous_job.job_id,
        started_at="2026-06-01T00:00:00+00:00",
        heartbeat_at=datetime.now(timezone.utc).isoformat(),
    )
    alive = {"value": True}
    monkeypatch.setattr(
        MetadataStore,
        "_is_process_alive",
        staticmethod(lambda process_id: alive["value"]),
    )
    recovered_count = store.recover_orphaned_running_jobs(
        started_before="2026-06-02T00:00:00+00:00",
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
    )
    alive["value"] = False

    latest = store.get_latest_sync_job("source_github")
    fresh, fresh_started = store.begin_sync_job("source_github")

    assert recovered_count == 0
    assert latest.status == SyncJobStatus.FAILED
    assert store.get_sync_job(previous_job.job_id).status == SyncJobStatus.FAILED
    assert fresh_started is True
    assert fresh.job_id != previous_job.job_id


def test_recover_orphaned_running_jobs_recovers_unowned_legacy_job_after_grace(tmp_path):
    store = MetadataStore(
        tmp_path / "contextwiki.sqlite3",
        unowned_running_job_grace_seconds=60,
    )
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    legacy, started = store.begin_sync_job("source_github")
    assert started is True
    _mark_job_running(
        store,
        legacy.job_id,
        started_at="2000-01-01T00:00:00+00:00",
        heartbeat_at="2000-01-01T00:00:00+00:00",
        owner_id="",
    )

    recovered_count = store.recover_orphaned_running_jobs(
        started_before="2026-06-02T00:00:00+00:00",
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
    )

    assert recovered_count == 1
    assert store.get_sync_job(legacy.job_id).status == SyncJobStatus.FAILED


def test_begin_sync_job_recovers_unowned_legacy_job_after_startup_grace(tmp_path):
    store = MetadataStore(
        tmp_path / "contextwiki.sqlite3",
        running_job_timeout_seconds=24 * 60 * 60,
        unowned_running_job_grace_seconds=60,
    )
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.RUNNING,
        )
    )
    legacy, started = store.begin_sync_job("source_github")
    assert started is True
    recent_timestamp = datetime.now(timezone.utc).isoformat()
    _mark_job_running(
        store,
        legacy.job_id,
        started_at="2026-06-01T23:59:30+00:00",
        heartbeat_at=recent_timestamp,
        owner_id="",
    )
    recovered_count = store.recover_orphaned_running_jobs(
        started_before="2026-06-02T00:00:00+00:00",
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
    )

    _mark_job_running(
        store,
        legacy.job_id,
        started_at="2000-01-01T00:00:00+00:00",
        heartbeat_at="2000-01-01T00:00:00+00:00",
        owner_id="",
    )
    fresh, fresh_started = store.begin_sync_job("source_github")

    assert recovered_count == 0
    assert store.get_sync_job(legacy.job_id).status == SyncJobStatus.FAILED
    assert fresh_started is True
    assert fresh.job_id != legacy.job_id


def test_is_process_alive_treats_permission_error_as_alive(monkeypatch):
    def raise_permission_error(pid, signal_number):
        raise PermissionError()

    monkeypatch.setattr("storage.metadata_store.os.kill", raise_permission_error)

    assert MetadataStore._is_process_alive(12345) is True


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


def test_update_sync_job_does_not_clobber_terminal_job_after_completion(tmp_path):
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

    finished, _ = store.complete_successful_sync(
        job_id=job.job_id,
        source_id="source_github",
        total_documents=1,
        processed_documents=1,
        indexed_chunks=1,
        skipped_documents=0,
        last_seen_at="2026-06-15T00:00:00+00:00",
        cleanup_missing_documents=False,
        deleted_at="2026-06-15T00:00:00+00:00",
    )

    updated = store.update_sync_job(
        job.job_id,
        phase="fetching_page_content",
        last_progress_at="2026-06-15T01:00:00+00:00",
        status_message="late progress should not win",
    )

    assert finished.status == SyncJobStatus.SUCCEEDED
    assert updated.status == SyncJobStatus.SUCCEEDED
    assert updated.phase == "completed"
    assert updated.status_message == "Sync completed. Indexed 1/1 documents; skipped 0."
    assert updated.last_progress_at == finished.last_progress_at


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


@pytest.mark.parametrize("prefix", ["published", "modified", "indexed"])
def test_search_filters_compare_fractional_ranges_as_datetimes(prefix):
    filters = SearchFilters(
        **{
            f"{prefix}_from": "2026-07-01T00:00:00Z",
            f"{prefix}_to": "2026-07-01T00:00:00.1Z",
        }
    )

    assert getattr(filters, f"{prefix}_from") == "2026-07-01T00:00:00Z"
    assert getattr(filters, f"{prefix}_to") == "2026-07-01T00:00:00.100000Z"

    with pytest.raises(ValueError, match=f"{prefix}_from must be before or equal"):
        SearchFilters(
            **{
                f"{prefix}_from": "2026-07-01T00:00:00.1Z",
                f"{prefix}_to": "2026-07-01T00:00:00Z",
            }
        )


@pytest.mark.parametrize("prefix", ["published", "modified", "indexed"])
def test_search_filters_compare_offset_and_equal_ranges_as_utc_datetimes(prefix):
    filters = SearchFilters(
        **{
            f"{prefix}_from": "2026-07-01T09:00:00+09:00",
            f"{prefix}_to": "2026-07-01T00:00:00Z",
        }
    )

    assert getattr(filters, f"{prefix}_from") == "2026-07-01T00:00:00Z"
    assert getattr(filters, f"{prefix}_to") == "2026-07-01T00:00:00Z"

    with pytest.raises(ValueError, match=f"{prefix}_from must be before or equal"):
        SearchFilters(
            **{
                f"{prefix}_from": "2026-07-01T00:00:01+00:00",
                f"{prefix}_to": "2026-07-01T09:00:00+09:00",
            }
        )


def test_search_filters_reject_unknown_fields():
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        SearchFilters(unknown_date_filter="2026-07-01T00:00:00Z")


def test_search_filters_normalize_null_and_scalar_source_aliases():
    null_filters = SearchFilters(source_id=None, source_ids=None)
    scalar_filters = SearchFilters(source_ids="source_a")

    assert null_filters.source_id == ""
    assert null_filters.source_ids == []
    assert null_filters.effective_source_ids == ()
    assert scalar_filters.source_ids == ["source_a"]
    assert scalar_filters.effective_source_ids == ("source_a",)


def test_search_filters_accept_tuple_sources_and_skip_blank_entries():
    filters = SearchFilters(
        source_id="source_b",
        source_ids=(" source_a ", "", " ", "source_b", "source_a"),
    )
    all_blank = SearchFilters(source_ids=(" ", ""))

    assert filters.source_ids == ["source_a", "source_b"]
    assert filters.effective_source_ids == ("source_a", "source_b")
    assert all_blank.source_ids == []
    assert all_blank.effective_source_ids == ()


def test_search_filter_validation_errors_hide_secret_like_input():
    secret_like_timestamp = "sk-secret-token-/private/contextwiki/token"

    with pytest.raises(ValidationError) as error:
        SearchFilters(published_from=secret_like_timestamp)

    rendered_error = str(error.value)
    assert secret_like_timestamp not in rendered_error
    assert "sk-secret-token" not in rendered_error
    assert "/private/contextwiki/token" not in rendered_error


@pytest.mark.parametrize("prefix", ["published", "modified", "indexed"])
def test_search_filters_reject_utc_conversion_overflow_deterministically(prefix):
    with pytest.raises(
        ValueError,
        match="Date filters must be valid ISO 8601 timestamps",
    ):
        SearchFilters(
            **{f"{prefix}_from": "0001-01-01T00:00:00+14:00"}
        )


def test_metadata_store_persists_normalized_document_times_and_adds_legacy_columns(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    document = DocumentModel(
        id="dated-doc",
        source_id="source_notion",
        title="Dated document",
        content="normalized timestamps",
        url="https://example.com/dated",
        platform="Notion",
        published_at="2026-07-01T00:00:00Z",
        modified_at="2026-07-02T00:00:00Z",
        indexed_at="2026-07-03T00:00:00Z",
        date_provenance="notion",
    )

    store.upsert_document(document)

    persisted = store.get_document("dated-doc")
    assert persisted.published_at == "2026-07-01T00:00:00Z"
    assert persisted.modified_at == "2026-07-02T00:00:00Z"
    assert persisted.indexed_at == "2026-07-03T00:00:00Z"
    assert persisted.date_provenance == "notion"
    with store._connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    assert {"published_at", "modified_at", "indexed_at", "date_provenance"} <= columns


def test_metadata_store_canonicalizes_document_times_before_sql_keyset_pagination(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    for document_id, published_at, modified_at, indexed_at, provenance in (
        (
            "zulu",
            "2026-07-01T00:00:00Z",
            "2026-07-01T00:00:00Z",
            "2026-07-01T00:00:00Z",
            "test",
        ),
        (
            "basic",
            "20260701T120000+0900",
            "20260701T120000+0900",
            "20260701T120000+0900",
            "test",
        ),
        ("blank", "", "", "", ""),
        ("null", None, "not-a-time", None, "upstream"),
    ):
        store.upsert_document(
            DocumentModel(
                id=document_id,
                source_id="source_notion",
                title=document_id,
                content=document_id,
                url=f"https://example.com/{document_id}",
                platform="Notion",
                published_at=published_at,
                modified_at=modified_at,
                indexed_at=indexed_at,
                date_provenance=provenance,
            )
        )

    document_ids = []
    cursor = None
    while True:
        page = store.list_documents(
            sort_by="published_at",
            sort_order="asc",
            page_size=2,
            cursor=cursor,
        )
        document_ids.extend(document.document_id for document in page["documents"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert document_ids == ["zulu", "basic", "blank", "null"]
    assert len(document_ids) == len(set(document_ids))
    basic = store.get_document("basic")
    assert basic.published_at == "2026-07-01T03:00:00Z"
    assert basic.modified_at == "2026-07-01T03:00:00Z"
    assert basic.indexed_at == "2026-07-01T03:00:00Z"
    invalid = store.get_document("null")
    assert invalid.published_at == ""
    assert invalid.modified_at == ""
    assert invalid.indexed_at.endswith("Z")
    assert invalid.date_provenance == ""

    for prefix in ("published", "modified"):
        filters = SearchFilters(
            **{
                f"{prefix}_from": "2026-07-01T02:00:00Z",
                f"{prefix}_to": "2026-07-01T04:00:00Z",
            }
        )
        sql_matches = {
            document.document_id
            for document in store.list_documents(
                filters=filters,
                sort_by=f"{prefix}_at",
            )["documents"]
        }
        python_matches = {
            document_id
            for document_id in ("zulu", "basic", "blank", "null")
            if store.document_matches_filters(
                store.get_document(document_id),
                filters,
            )
        }
        assert sql_matches == python_matches == {"basic"}


def test_metadata_store_canonicalizes_python_date_only_timestamp(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")

    stored = store.upsert_document(
        DocumentModel(
            id="date-only",
            source_id="source_notion",
            title="date-only",
            content="date-only",
            url="https://example.com/date-only",
            platform="Notion",
            published_at="20260701",
            date_provenance="test",
        )
    )

    assert stored.published_at == "2026-07-01T00:00:00Z"


def test_metadata_store_blanks_utc_conversion_overflow_without_failing_sync(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")

    stored = store.upsert_document(
        DocumentModel(
            id="overflow",
            source_id="source_notion",
            title="overflow",
            content="overflow",
            url="https://example.com/overflow",
            platform="Notion",
            published_at="0001-01-01T00:00:00+14:00",
            modified_at="9999-12-31T23:59:59-14:00",
            indexed_at="0001-01-01T00:00:00+14:00",
            date_provenance="upstream",
        )
    )

    assert stored.published_at == ""
    assert stored.modified_at == ""
    assert stored.indexed_at.endswith("Z")
    assert stored.date_provenance == ""


def test_list_documents_preserves_submillisecond_keyset_order_and_filter_parity(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    for document_id, published_at in (
        ("z-earlier", "2026-07-01T00:00:00.000100Z"),
        ("a-later", "2026-07-01T00:00:00.000200Z"),
        ("m-next", "2026-07-01T00:00:00.001100Z"),
    ):
        store.upsert_document(
            DocumentModel(
                id=document_id,
                source_id="source_notion",
                title=document_id,
                content=document_id,
                url=f"https://example.com/{document_id}",
                platform="Notion",
                published_at=published_at,
                date_provenance="test",
            )
        )

    ordered_ids = []
    cursor = None
    while True:
        page = store.list_documents(
            sort_by="published_at",
            sort_order="asc",
            page_size=1,
            cursor=cursor,
        )
        ordered_ids.extend(document.document_id for document in page["documents"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    filters = SearchFilters(
        published_from="2026-07-01T00:00:00.000150Z",
        published_to="2026-07-01T00:00:00.000250Z",
    )
    sql_matches = {
        document.document_id
        for document in store.list_documents(
            filters=filters,
            sort_by="published_at",
        )["documents"]
    }
    python_matches = {
        document_id
        for document_id in ("z-earlier", "a-later", "m-next")
        if store.document_matches_filters(store.get_document(document_id), filters)
    }

    assert ordered_ids == ["z-earlier", "a-later", "m-next"]
    assert len(ordered_ids) == len(set(ordered_ids))
    assert sql_matches == python_matches == {"a-later"}


def test_list_documents_filters_active_rows_sorts_dates_and_paginates_with_cursor(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    for document_id, published_at, deleted_at in [
        ("newest", "2026-07-03T00:00:00Z", ""),
        ("middle", "2026-07-02T00:00:00Z", ""),
        ("oldest", "2026-07-01T00:00:00Z", ""),
        ("deleted", "2026-07-04T00:00:00Z", "2026-07-05T00:00:00Z"),
    ]:
        store.upsert_document(
            DocumentModel(
                id=document_id,
                source_id="source_notion",
                title=document_id,
                content=document_id,
                url=f"https://example.com/{document_id}",
                platform="Notion",
                published_at=published_at,
                modified_at=published_at,
                indexed_at=published_at,
                date_provenance="notion",
                deleted_at=deleted_at,
            )
        )

    filters = SearchFilters(
        source_ids=["source_notion"],
        published_from="2026-07-01T00:00:00Z",
        published_to="2026-07-03T00:00:00Z",
    )
    first_page = store.list_documents(
        filters=filters,
        sort_by="published_at",
        sort_order="desc",
        page_size=2,
    )
    second_page = store.list_documents(
        filters=filters,
        sort_by="published_at",
        sort_order="desc",
        page_size=2,
        cursor=first_page["next_cursor"],
    )

    assert [document.document_id for document in first_page["documents"]] == [
        "newest",
        "middle",
    ]
    assert first_page["next_cursor"]
    assert [document.document_id for document in second_page["documents"]] == ["oldest"]
    assert second_page["next_cursor"] is None


def test_document_filter_source_aliases_form_one_effective_union(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    documents = []
    for source_id in ("source_a", "source_b", "source_c"):
        document = DocumentModel(
            id=source_id,
            source_id=source_id,
            title=source_id,
            content=source_id,
            url=f"https://example.com/{source_id}",
            platform="Test",
        )
        documents.append(store.upsert_document(document))

    filters = SearchFilters(source_id="source_b", source_ids=["source_a", "source_b"])

    assert [
        store.document_matches_filters(document, filters)
        for document in documents
    ] == [True, True, False]
    assert {
        document.source_id
        for document in store.list_documents(filters=filters)["documents"]
    } == {"source_a", "source_b"}


def test_list_documents_cursor_binds_to_canonical_source_alias_union(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    for document_id, source_id in (
        ("a-1", "source_a"),
        ("a-2", "source_a"),
        ("b-1", "source_b"),
    ):
        store.upsert_document(
            DocumentModel(
                id=document_id,
                source_id=source_id,
                title=document_id,
                content=document_id,
                url=f"https://example.com/{document_id}",
                platform="Test",
            )
        )

    first_page = store.list_documents(
        filters=SearchFilters(source_id="source_a", source_ids=["source_b"]),
        page_size=1,
    )
    second_page = store.list_documents(
        filters=SearchFilters(source_id="source_b", source_ids=["source_a"]),
        page_size=1,
        cursor=first_page["next_cursor"],
    )

    assert first_page["documents"]
    assert second_page["documents"]


@pytest.mark.parametrize(
    ("sort_order", "expected"),
    [
        ("asc", ["a", "b", "c", "null-a", "null-b"]),
        ("desc", ["c", "a", "b", "null-a", "null-b"]),
    ],
)
def test_list_documents_keyset_keeps_ties_and_nulls_last(tmp_path, sort_order, expected):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    for document_id, published_at in (
        ("a", "2026-07-01T00:00:00Z"),
        ("b", "2026-07-01T00:00:00+00:00"),
        ("c", "2026-07-02T00:00:00Z"),
        ("null-a", ""),
        ("null-b", "not-a-date"),
    ):
        store.upsert_document(
            DocumentModel(
                id=document_id,
                source_id="source_notion",
                title=document_id,
                content=document_id,
                url=f"https://example.com/{document_id}",
                platform="Notion",
                published_at=published_at,
            )
        )

    document_ids = []
    cursor = None
    while True:
        page = store.list_documents(
            sort_by="published_at",
            sort_order=sort_order,
            page_size=2,
            cursor=cursor,
        )
        document_ids.extend(
            document.document_id for document in page["documents"]
        )
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert document_ids == expected


def test_list_documents_limits_browse_safe_sql_rows(tmp_path, monkeypatch):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    for index in range(5):
        store.upsert_document(
            DocumentModel(
                id=f"doc-{index}",
                source_id="source_notion",
                title=f"doc-{index}",
                content="private full content",
                url=f"https://example.com/{index}",
                platform="Notion",
            )
        )
    statements = []
    original_connect = store._connect

    @contextmanager
    def traced_connect():
        with original_connect() as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(store, "_connect", traced_connect)

    page = store.list_documents(page_size=1)

    browse_select = next(
        statement
        for statement in statements
        if "SELECT" in statement and "FROM documents" in statement
    )
    assert "content" not in browse_select.lower()
    assert "LIMIT 2" in browse_select
    assert len(page["documents"]) == 1
    assert page["documents"][0].content == ""


def test_list_documents_rejects_noncanonical_cursor_characters(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    for document_id in ("first", "second"):
        store.upsert_document(
            DocumentModel(
                id=document_id,
                source_id="source_notion",
                title=document_id,
                content=document_id,
                url=f"https://example.com/{document_id}",
                platform="Notion",
                indexed_at="2026-07-01T00:00:00Z",
            )
        )
    first_page = store.list_documents(page_size=1)
    cursor = first_page["next_cursor"]

    with pytest.raises(ValueError, match="Invalid document cursor"):
        store.list_documents(page_size=1, cursor=f"{cursor}*")


@pytest.mark.parametrize(
    "forged_timestamp",
    [
        "2026-07-01T01:00:00.100000+01:00",
        "2026-07-01T00:00:00.100000",
        "2026-07-01T00:00:00.1Z",
    ],
)
def test_list_documents_rejects_noncanonical_cursor_timestamp_spellings(
    tmp_path,
    forged_timestamp,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    for document_id, published_at in (
        ("first", "2026-07-01T00:00:00.100000Z"),
        ("second", "2026-07-01T00:00:00.200000Z"),
    ):
        store.upsert_document(
            DocumentModel(
                id=document_id,
                source_id="source_notion",
                title=document_id,
                content=document_id,
                url=f"https://example.com/{document_id}",
                platform="Notion",
                published_at=published_at,
            )
        )
    first_page = store.list_documents(
        sort_by="published_at",
        sort_order="asc",
        page_size=1,
    )
    valid_cursor = first_page["next_cursor"]
    payload = store._decode_document_cursor(valid_cursor)
    forged_cursor = store._encode_document_cursor(
        {**payload, "timestamp": forged_timestamp}
    )

    with pytest.raises(ValueError, match="Invalid document cursor"):
        store.list_documents(
            sort_by="published_at",
            sort_order="asc",
            page_size=1,
            cursor=forged_cursor,
        )

    valid_next_page = store.list_documents(
        sort_by="published_at",
        sort_order="asc",
        page_size=1,
        cursor=valid_cursor,
    )
    assert [item.document_id for item in first_page["documents"]] == ["first"]
    assert [item.document_id for item in valid_next_page["documents"]] == ["second"]


@pytest.mark.parametrize(
    "payload_override",
    [
        {"document_id": "bb-forged-anchor"},
        {"timestamp": "2026-07-01T00:00:01Z"},
        {"is_null": True, "timestamp": ""},
    ],
)
def test_list_documents_rejects_cursor_when_anchor_does_not_match_filtered_row(
    tmp_path,
    payload_override,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    for document_id in ("a", "b", "c"):
        store.upsert_document(
            DocumentModel(
                id=document_id,
                source_id="source_notion",
                title=document_id,
                content=document_id,
                url=f"https://example.com/{document_id}",
                platform="Notion",
                published_at="2026-07-01T00:00:00Z",
            )
        )

    first_page = store.list_documents(
        sort_by="published_at",
        sort_order="asc",
        page_size=1,
    )
    valid_cursor = first_page["next_cursor"]
    payload = store._decode_document_cursor(valid_cursor)
    forged_cursor = store._encode_document_cursor(
        {**payload, **payload_override}
    )

    with pytest.raises(ValueError, match="Invalid document cursor"):
        store.list_documents(
            sort_by="published_at",
            sort_order="asc",
            page_size=1,
            cursor=forged_cursor,
        )

    valid_next_page = store.list_documents(
        sort_by="published_at",
        sort_order="asc",
        page_size=1,
        cursor=valid_cursor,
    )
    assert [item.document_id for item in first_page["documents"]] == ["a"]
    assert [item.document_id for item in valid_next_page["documents"]] == ["b"]


def test_list_documents_rejects_cursor_anchor_outside_current_filter_scope(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    for document_id, source_id in (
        ("a", "source_notion"),
        ("b", "source_notion"),
        ("z-existing-other-source", "source_other"),
    ):
        store.upsert_document(
            DocumentModel(
                id=document_id,
                source_id=source_id,
                title=document_id,
                content=document_id,
                url=f"https://example.com/{document_id}",
                platform="Test",
                published_at="2026-07-01T00:00:00Z",
            )
        )
    filters = SearchFilters(source_ids=["source_notion"])
    first_page = store.list_documents(
        filters=filters,
        sort_by="published_at",
        sort_order="asc",
        page_size=1,
    )
    payload = store._decode_document_cursor(first_page["next_cursor"])
    forged_cursor = store._encode_document_cursor(
        {**payload, "document_id": "z-existing-other-source"}
    )

    with pytest.raises(ValueError, match="Invalid document cursor"):
        store.list_documents(
            filters=filters,
            sort_by="published_at",
            sort_order="asc",
            page_size=1,
            cursor=forged_cursor,
        )


def test_list_documents_rejects_cursor_anchor_that_is_no_longer_active(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    for document_id in ("a", "b"):
        store.upsert_document(
            DocumentModel(
                id=document_id,
                source_id="source_notion",
                title=document_id,
                content=document_id,
                url=f"https://example.com/{document_id}",
                platform="Notion",
                published_at="2026-07-01T00:00:00Z",
            )
        )
    first_page = store.list_documents(
        sort_by="published_at",
        sort_order="asc",
        page_size=1,
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE documents SET deleted_at = ? WHERE document_id = ?",
            ("2026-07-02T00:00:00Z", "a"),
        )

    with pytest.raises(ValueError, match="Invalid document cursor"):
        store.list_documents(
            sort_by="published_at",
            sort_order="asc",
            page_size=1,
            cursor=first_page["next_cursor"],
        )


@pytest.mark.parametrize(
    ("changed_kwargs"),
    [
        {"filters": SearchFilters(source_ids=["other-source"])},
        {"sort_by": "published_at"},
        {"sort_order": "asc"},
    ],
)
def test_list_documents_rejects_cursor_query_shape_mismatch(tmp_path, changed_kwargs):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    for document_id in ("first", "second"):
        store.upsert_document(
            DocumentModel(
                id=document_id,
                source_id="source_notion",
                title=document_id,
                content=document_id,
                url=f"https://example.com/{document_id}",
                platform="Notion",
            )
        )
    first_page = store.list_documents(page_size=1)

    with pytest.raises(ValueError, match="Invalid document cursor"):
        store.list_documents(
            page_size=1,
            cursor=first_page["next_cursor"],
            **changed_kwargs,
        )


@pytest.mark.parametrize("page_size", [0, -1, 101, True, 1.5])
def test_list_documents_rejects_unsafe_internal_page_sizes(tmp_path, page_size):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")

    with pytest.raises(ValueError, match="page_size must be between 1 and 100"):
        store.list_documents(page_size=page_size)


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
                last_seen_sync_id, deleted_at, version_id, published_at,
                modified_at, indexed_at, date_provenance
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
        "published_at",
        "modified_at",
        "indexed_at",
        "date_provenance",
    }.issubset(columns)
    assert row["document_id"] == "legacy-doc"
    assert row["external_id"] == ""
    assert row["canonical_url"] == ""
    assert row["last_seen_at"] == ""
    assert row["last_seen_sync_id"] == ""
    assert row["deleted_at"] == ""
    assert row["version_id"] == ""
    assert row["published_at"] == ""
    assert row["modified_at"] == ""
    assert row["indexed_at"] == ""
    assert row["date_provenance"] == ""


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


def test_ensure_schema_adds_owner_id_to_legacy_sync_jobs_table(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.db_path.parent.mkdir(parents=True, exist_ok=True)
    with store._connect() as conn:
        conn.executescript(
            """
            CREATE TABLE sync_jobs (
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
            INSERT INTO sync_jobs (
                job_id, source_id, status, started_at, heartbeat_at,
                finished_at, total_documents, processed_documents,
                indexed_chunks, skipped_documents, error_message
            ) VALUES (
                'legacy-job', 'source_github', 'running',
                '2000-01-01T00:00:00+00:00',
                '2000-01-01T00:00:00+00:00',
                '', 0, 0, 0, 0, ''
            );
            """
        )

    store.ensure_schema()

    with store._connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(sync_jobs)").fetchall()}
        row = conn.execute(
            "SELECT job_id, owner_id FROM sync_jobs WHERE job_id = ?",
            ("legacy-job",),
        ).fetchone()

    assert "owner_id" in columns
    assert row["job_id"] == "legacy-job"
    assert row["owner_id"] == ""


def test_ensure_schema_adds_process_start_id_to_legacy_owner_table(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3", sync_owner_id="current-owner")
    store.db_path.parent.mkdir(parents=True, exist_ok=True)
    with store._connect() as conn:
        conn.executescript(
            """
            CREATE TABLE sync_job_owners (
                owner_id TEXT PRIMARY KEY,
                process_id INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL
            );
            INSERT INTO sync_job_owners (
                owner_id, process_id, started_at, heartbeat_at
            ) VALUES (
                'legacy-owner', 123,
                '2000-01-01T00:00:00+00:00',
                '2000-01-01T00:00:00+00:00'
            );
            """
        )

    store.ensure_schema()

    with store._connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(sync_job_owners)").fetchall()
        }
        legacy_row = conn.execute(
            """
            SELECT process_start_id
            FROM sync_job_owners
            WHERE owner_id = ?
            """,
            ("legacy-owner",),
        ).fetchone()
        current_row = conn.execute(
            """
            SELECT process_start_id
            FROM sync_job_owners
            WHERE owner_id = ?
            """,
            ("current-owner",),
        ).fetchone()

    assert "process_start_id" in columns
    assert legacy_row["process_start_id"] == ""
    assert current_row is None


def test_repeated_read_only_store_initialization_does_not_register_sync_owners(
    tmp_path,
):
    db_path = tmp_path / "contextwiki.sqlite3"

    for index in range(25):
        reader = MetadataStore(db_path, sync_owner_id=f"reader-{index}")
        reader.ensure_schema()
        assert reader.get_source("missing-source") is None

    with MetadataStore(db_path)._connect() as conn:
        owner_count = conn.execute(
            "SELECT COUNT(*) AS count FROM sync_job_owners"
        ).fetchone()["count"]

    assert owner_count == 0


def test_claim_and_heartbeat_register_owner_and_prune_unreferenced_owners(
    tmp_path,
):
    db_path = tmp_path / "contextwiki.sqlite3"
    requester = MetadataStore(db_path, sync_owner_id="requester")
    requester.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
        )
    )
    queued, created = requester.enqueue_sync_job("source_notion")
    assert created is True

    worker = MetadataStore(db_path, sync_owner_id="worker")
    claimed = worker.claim_next_sync_job(["source_notion"])
    assert claimed is not None
    assert claimed.job_id == queued.job_id

    stale_timestamp = "2000-01-01T00:00:00+00:00"
    with worker._connect() as conn:
        conn.executemany(
            """
            INSERT INTO sync_job_owners (
                owner_id, process_id, process_start_id, started_at, heartbeat_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    f"stale-unreferenced-{index}",
                    999_000 + index,
                    "",
                    stale_timestamp,
                    stale_timestamp,
                )
                for index in range(25)
            ],
        )
        conn.execute(
            """
            UPDATE sync_job_owners
            SET heartbeat_at = ?
            WHERE owner_id = ?
            """,
            (stale_timestamp, worker.sync_owner_id),
        )

    touched = worker.touch_sync_job(claimed.job_id)

    assert touched is not None
    assert touched.status == SyncJobStatus.RUNNING
    with worker._connect() as conn:
        owner_rows = conn.execute(
            """
            SELECT owner_id, started_at, heartbeat_at
            FROM sync_job_owners
            ORDER BY owner_id
            """
        ).fetchall()

    assert [row["owner_id"] for row in owner_rows] == [worker.sync_owner_id]
    assert owner_rows[0]["started_at"]
    assert owner_rows[0]["heartbeat_at"] != stale_timestamp


def test_ensure_schema_serializes_concurrent_legacy_owner_migrations(
    tmp_path,
    monkeypatch,
):
    real_connect = sqlite3.connect

    class MigrationBarrierConnection(sqlite3.Connection):
        migration_barrier: Barrier | None = None

        def execute(self, sql, parameters=(), /):
            if "PRAGMA table_info(sync_job_owners)" in sql:
                barrier = self.migration_barrier
                if barrier is not None:
                    try:
                        barrier.wait(timeout=0.1)
                    except BrokenBarrierError:
                        pass
            return super().execute(sql, parameters)

    current_barrier: Barrier | None = None

    def coordinated_connect(*args, **kwargs):
        kwargs["factory"] = MigrationBarrierConnection
        conn = real_connect(*args, **kwargs)
        conn.migration_barrier = current_barrier
        return conn

    monkeypatch.setattr(sqlite3, "connect", coordinated_connect)

    for attempt in range(5):
        db_path = tmp_path / f"legacy-owner-{attempt}.sqlite3"
        setup_store = MetadataStore(db_path)
        setup_store.db_path.parent.mkdir(parents=True, exist_ok=True)
        with setup_store._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE sync_job_owners (
                    owner_id TEXT PRIMARY KEY,
                    process_id INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL
                );
                INSERT INTO sync_job_owners (
                    owner_id, process_id, started_at, heartbeat_at
                ) VALUES (
                    'legacy-owner', 123,
                    '2000-01-01T00:00:00+00:00',
                    '2000-01-01T00:00:00+00:00'
                );
                """
            )

        current_barrier = Barrier(2)
        stores = (
            MetadataStore(db_path, sync_owner_id=f"worker-{attempt}-one"),
            MetadataStore(db_path, sync_owner_id=f"worker-{attempt}-two"),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(store.ensure_schema) for store in stores]
            for future in futures:
                future.result()

        with stores[0]._connect() as conn:
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(sync_job_owners)").fetchall()
            }
            owners = {
                row["owner_id"]: row["process_start_id"]
                for row in conn.execute(
                    """
                    SELECT owner_id, process_start_id
                    FROM sync_job_owners
                    """
                ).fetchall()
            }

        assert "process_start_id" in columns
        assert owners["legacy-owner"] == ""
        assert f"worker-{attempt}-one" not in owners
        assert f"worker-{attempt}-two" not in owners

        source = stores[0].register_source(
            SourceModel(
                source_id="source_github",
                source_type=SourceType.GITHUB,
                name="GitHub",
                enabled=True,
            )
        )
        assert source.source_id == "source_github"
        assert stores[1].get_source("source_github") is not None


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


def test_enqueue_sync_job_reuses_queued_and_running_jobs(tmp_path):
    db_path = tmp_path / "contextwiki.sqlite3"
    requester = MetadataStore(db_path, sync_owner_id="requester")
    worker = MetadataStore(db_path, sync_owner_id="worker")
    requester.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
        )
    )

    queued, enqueued = requester.enqueue_sync_job("source_notion")
    reused_queued, enqueued_again = requester.enqueue_sync_job("source_notion")

    assert enqueued is True
    assert enqueued_again is False
    assert reused_queued.job_id == queued.job_id
    assert reused_queued.status == SyncJobStatus.QUEUED
    assert requester.get_source("source_notion").sync_status == SyncStatus.RUNNING
    assert requester.get_latest_sync_job("source_notion").job_id == queued.job_id

    claimed = worker.claim_next_sync_job(["source_notion"])
    reused_running, enqueued_while_running = requester.enqueue_sync_job("source_notion")

    assert claimed is not None
    assert claimed.job_id == queued.job_id
    assert claimed.status == SyncJobStatus.RUNNING
    assert worker.get_owned_running_sync_job(claimed.job_id) == claimed
    assert requester.get_owned_running_sync_job(claimed.job_id) is None
    assert enqueued_while_running is False
    assert reused_running.job_id == claimed.job_id
    assert reused_running.status == SyncJobStatus.RUNNING


def test_disabled_source_enqueue_and_worker_claim_race_never_claims_new_job(tmp_path):
    db_path = tmp_path / "contextwiki.sqlite3"
    requester = MetadataStore(db_path, sync_owner_id="requester")
    requester.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=False,
            sync_status=SyncStatus.FAILED,
            last_error="Source source_notion is disabled",
        )
    )
    worker = MetadataStore(db_path, sync_owner_id="worker")
    worker.ensure_schema()
    start_barrier = Barrier(2)
    enqueue_finished = Event()

    def enqueue_disabled_source():
        start_barrier.wait()
        try:
            return requester.enqueue_sync_job("source_notion")
        finally:
            enqueue_finished.set()

    def claim_after_enqueue_transaction():
        start_barrier.wait()
        assert enqueue_finished.wait(timeout=5)
        return worker.claim_next_sync_job(["source_notion"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        enqueue_future = executor.submit(enqueue_disabled_source)
        claim_future = executor.submit(claim_after_enqueue_transaction)
        job, created = enqueue_future.result(timeout=5)
        claimed = claim_future.result(timeout=5)

    source = requester.get_source("source_notion")
    assert created is True
    assert job.status == SyncJobStatus.FAILED
    assert job.finished_at
    assert "disabled" in job.error_message.lower()
    assert claimed is None
    assert source.sync_status == SyncStatus.FAILED
    assert "disabled" in source.last_error.lower()


@pytest.mark.parametrize("active_status", [SyncJobStatus.QUEUED, SyncJobStatus.RUNNING])
def test_disabled_source_enqueue_reuses_existing_active_job(tmp_path, active_status):
    db_path = tmp_path / active_status.value / "contextwiki.sqlite3"
    requester = MetadataStore(db_path, sync_owner_id="requester")
    enabled_source = SourceModel(
        source_id="source_notion",
        source_type=SourceType.NOTION,
        name="Notion",
        enabled=True,
    )
    requester.upsert_source(enabled_source)
    active_job, created = requester.enqueue_sync_job("source_notion")
    assert created is True
    if active_status == SyncJobStatus.RUNNING:
        worker = MetadataStore(db_path, sync_owner_id="worker")
        worker.ensure_schema()
        active_job = worker.claim_next_sync_job(["source_notion"])
        assert active_job is not None

    requester.register_source(
        enabled_source.model_copy(
            update={
                "enabled": False,
                "last_error": "Source source_notion is disabled",
            }
        )
    )
    returned, created_again = requester.enqueue_sync_job("source_notion")

    assert created_again is False
    assert returned.job_id == active_job.job_id
    assert returned.status == active_status
    assert requester.get_source("source_notion").sync_status == SyncStatus.RUNNING


def test_two_workers_racing_claim_one_queued_job_have_one_winner(tmp_path):
    db_path = tmp_path / "contextwiki.sqlite3"
    requester = MetadataStore(db_path, sync_owner_id="requester")
    requester.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
        )
    )
    queued, _ = requester.enqueue_sync_job("source_notion")
    workers = [
        MetadataStore(db_path, sync_owner_id="worker-a"),
        MetadataStore(db_path, sync_owner_id="worker-b"),
    ]
    for worker in workers:
        worker.ensure_schema()
    barrier = Barrier(2)

    def claim(worker):
        barrier.wait()
        return worker.claim_next_sync_job(["source_notion"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, workers))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0].job_id == queued.job_id
    assert requester.get_sync_job(queued.job_id).status == SyncJobStatus.RUNNING


def test_two_requesters_racing_enqueue_reuse_one_queued_job(tmp_path):
    db_path = tmp_path / "contextwiki.sqlite3"
    setup_store = MetadataStore(db_path, sync_owner_id="setup")
    setup_store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
        )
    )
    requesters = [
        MetadataStore(db_path, sync_owner_id="requester-a"),
        MetadataStore(db_path, sync_owner_id="requester-b"),
    ]
    for requester in requesters:
        requester.ensure_schema()
    barrier = Barrier(2)

    def enqueue(requester):
        barrier.wait()
        return requester.enqueue_sync_job("source_notion")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(enqueue, requesters))

    assert sum(1 for _, enqueued in results if enqueued) == 1
    assert len({job.job_id for job, _ in results}) == 1
    with setup_store._connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM sync_jobs
            WHERE source_id = ? AND status = ?
            """,
            ("source_notion", SyncJobStatus.QUEUED.value),
        ).fetchone()
    assert row["count"] == 1


def test_two_workers_cannot_claim_different_sources_concurrently(tmp_path):
    db_path = tmp_path / "contextwiki.sqlite3"
    requester = MetadataStore(db_path, sync_owner_id="requester")
    for source_id in ("source_a", "source_b"):
        requester.upsert_source(
            SourceModel(
                source_id=source_id,
                source_type=SourceType.GITHUB,
                name=source_id,
                enabled=True,
            )
        )
        requester.enqueue_sync_job(source_id)

    workers = [
        MetadataStore(db_path, sync_owner_id="worker-a"),
        MetadataStore(db_path, sync_owner_id="worker-b"),
    ]
    for worker in workers:
        worker.ensure_schema()
    barrier = Barrier(2)

    def claim(worker):
        barrier.wait()
        return worker.claim_next_sync_job(["source_a", "source_b"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_results = list(executor.map(claim, workers))

    winners = [
        (index, result)
        for index, result in enumerate(first_results)
        if result is not None
    ]
    assert len(winners) == 1
    winner_index, first_job = winners[0]
    assert first_job.status == SyncJobStatus.RUNNING
    assert sum(
        1
        for source_id in ("source_a", "source_b")
        if requester.get_latest_sync_job(source_id).status == SyncJobStatus.RUNNING
    ) == 1

    workers[winner_index].complete_failed_sync(
        job_id=first_job.job_id,
        source_id=first_job.source_id,
        error_message="test terminalization",
    )
    second_job = workers[1 - winner_index].claim_next_sync_job(
        ["source_a", "source_b"]
    )

    assert second_job is not None
    assert second_job.job_id != first_job.job_id
    assert second_job.source_id != first_job.source_id
    assert second_job.status == SyncJobStatus.RUNNING


@pytest.mark.parametrize(
    ("stored_identity", "observed_identity"),
    [
        (
            "linux-v2|boot-a|pidns-a|100",
            "linux-v2|boot-a|pidns-a|200",
        ),
        (
            "darwin:100:0",
            "darwin:200:0",
        ),
    ],
)
def test_global_claim_recovers_live_pid_with_changed_valid_process_birth_identity(
    tmp_path,
    monkeypatch,
    stored_identity,
    observed_identity,
):
    db_path = tmp_path / "contextwiki.sqlite3"
    previous_worker = MetadataStore(db_path, sync_owner_id="previous-worker")
    requester = MetadataStore(db_path, sync_owner_id="requester")
    next_worker = MetadataStore(db_path, sync_owner_id="next-worker")
    for source_id in ("source_a", "source_b"):
        requester.upsert_source(
            SourceModel(
                source_id=source_id,
                source_type=SourceType.GITHUB,
                name=source_id,
                enabled=True,
            )
        )

    source_a_job, started = previous_worker.begin_sync_job("source_a")
    assert started is True
    source_b_job, enqueued = requester.enqueue_sync_job("source_b")
    assert enqueued is True
    with requester._connect() as conn:
        conn.execute(
            """
            UPDATE sync_job_owners
            SET process_start_id = ?
            WHERE owner_id = ?
            """,
            (stored_identity, "previous-worker"),
        )

    monkeypatch.setattr(
        MetadataStore,
        "_is_process_alive",
        staticmethod(lambda process_id: True),
    )
    monkeypatch.setattr(
        MetadataStore,
        "_get_process_start_identity",
        staticmethod(lambda process_id: observed_identity),
    )

    claimed = next_worker.claim_next_sync_job(["source_a", "source_b"])

    assert claimed is not None
    assert claimed.job_id == source_b_job.job_id
    assert claimed.status == SyncJobStatus.RUNNING
    recovered = requester.get_sync_job(source_a_job.job_id)
    assert recovered.status == SyncJobStatus.FAILED
    assert recovered.error_message == ORPHANED_SYNC_JOB_RECOVERY_MESSAGE


@pytest.mark.parametrize(
    ("stored_identity", "observed_identity"),
    [
        (
            "linux-v2|boot-a|pidns-a|100",
            "linux-v2|boot-a|pidns-b|200",
        ),
        (
            "linux-v2|boot-a||100",
            "linux-v2|boot-a|pidns-b|200",
        ),
        (
            "linux:boot-a:100",
            "linux-v2|boot-a|pidns-b|200",
        ),
    ],
)
def test_global_claim_preserves_fresh_owner_across_unknown_linux_scope(
    tmp_path,
    monkeypatch,
    stored_identity,
    observed_identity,
):
    db_path = tmp_path / "contextwiki.sqlite3"
    previous_worker = MetadataStore(db_path, sync_owner_id="previous-worker")
    requester = MetadataStore(db_path, sync_owner_id="requester")
    next_worker = MetadataStore(db_path, sync_owner_id="next-worker")
    for source_id in ("source_a", "source_b"):
        requester.upsert_source(
            SourceModel(
                source_id=source_id,
                source_type=SourceType.GITHUB,
                name=source_id,
                enabled=True,
            )
        )

    source_a_job, started = previous_worker.begin_sync_job("source_a")
    assert started is True
    source_b_job, enqueued = requester.enqueue_sync_job("source_b")
    assert enqueued is True
    with requester._connect() as conn:
        conn.execute(
            """
            UPDATE sync_job_owners
            SET process_start_id = ?
            WHERE owner_id = ?
            """,
            (
                stored_identity,
                "previous-worker",
            ),
        )

    monkeypatch.setattr(
        MetadataStore,
        "_is_process_alive",
        staticmethod(lambda process_id: True),
    )
    monkeypatch.setattr(
        MetadataStore,
        "_get_process_start_identity",
        staticmethod(lambda process_id: observed_identity),
    )

    claimed = next_worker.claim_next_sync_job(["source_a", "source_b"])

    assert claimed is None
    assert requester.get_sync_job(source_a_job.job_id).status == SyncJobStatus.RUNNING
    assert requester.get_sync_job(source_b_job.job_id).status == SyncJobStatus.QUEUED


@pytest.mark.parametrize(
    "observer_identity",
    [
        "linux-v2|boot-a|pidns-b|300",
        "",
    ],
)
def test_global_claim_preserves_fresh_linux_owner_when_pid_is_invisible_outside_same_scope(
    tmp_path,
    monkeypatch,
    observer_identity,
):
    db_path = tmp_path / "contextwiki.sqlite3"
    previous_worker = MetadataStore(db_path, sync_owner_id="previous-worker")
    requester = MetadataStore(db_path, sync_owner_id="requester")
    next_worker = MetadataStore(db_path, sync_owner_id="next-worker")
    for source_id in ("source_a", "source_b"):
        requester.upsert_source(
            SourceModel(
                source_id=source_id,
                source_type=SourceType.GITHUB,
                name=source_id,
                enabled=True,
            )
        )

    source_a_job, started = previous_worker.begin_sync_job("source_a")
    assert started is True
    source_b_job, enqueued = requester.enqueue_sync_job("source_b")
    assert enqueued is True
    with requester._connect() as conn:
        conn.execute(
            """
            UPDATE sync_job_owners
            SET process_start_id = ?
            WHERE owner_id = ?
            """,
            ("linux-v2|boot-a|pidns-a|100", "previous-worker"),
        )

    monkeypatch.setattr(
        MetadataStore,
        "_is_process_alive",
        staticmethod(lambda process_id: False),
    )
    monkeypatch.setattr(
        MetadataStore,
        "_get_process_start_identity",
        staticmethod(
            lambda process_id: observer_identity
            if process_id == os.getpid()
            else ""
        ),
    )

    claimed = next_worker.claim_next_sync_job(["source_a", "source_b"])

    assert claimed is None
    assert requester.get_sync_job(source_a_job.job_id).status == SyncJobStatus.RUNNING
    assert requester.get_sync_job(source_b_job.job_id).status == SyncJobStatus.QUEUED


def test_global_claim_recovers_fresh_linux_owner_when_pid_is_dead_in_same_scope(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "contextwiki.sqlite3"
    previous_worker = MetadataStore(db_path, sync_owner_id="previous-worker")
    requester = MetadataStore(db_path, sync_owner_id="requester")
    next_worker = MetadataStore(db_path, sync_owner_id="next-worker")
    for source_id in ("source_a", "source_b"):
        requester.upsert_source(
            SourceModel(
                source_id=source_id,
                source_type=SourceType.GITHUB,
                name=source_id,
                enabled=True,
            )
        )

    source_a_job, started = previous_worker.begin_sync_job("source_a")
    assert started is True
    source_b_job, enqueued = requester.enqueue_sync_job("source_b")
    assert enqueued is True
    with requester._connect() as conn:
        conn.execute(
            """
            UPDATE sync_job_owners
            SET process_start_id = ?
            WHERE owner_id = ?
            """,
            ("linux-v2|boot-a|pidns-a|100", "previous-worker"),
        )

    monkeypatch.setattr(
        MetadataStore,
        "_is_process_alive",
        staticmethod(lambda process_id: False),
    )
    monkeypatch.setattr(
        MetadataStore,
        "_get_process_start_identity",
        staticmethod(
            lambda process_id: "linux-v2|boot-a|pidns-a|300"
            if process_id == os.getpid()
            else ""
        ),
    )

    claimed = next_worker.claim_next_sync_job(["source_a", "source_b"])

    assert claimed is not None
    assert claimed.job_id == source_b_job.job_id
    assert requester.get_sync_job(source_a_job.job_id).status == SyncJobStatus.FAILED


@pytest.mark.parametrize(
    "observer_identity",
    [
        "linux-v2|boot-a|pidns-b|300",
        "",
    ],
)
def test_startup_recovery_preserves_fresh_linux_owner_when_pid_scope_is_not_same(
    tmp_path,
    monkeypatch,
    observer_identity,
):
    db_path = tmp_path / "contextwiki.sqlite3"
    previous_worker = MetadataStore(
        db_path,
        sync_owner_id="previous-worker",
        running_job_timeout_seconds=24 * 60 * 60,
    )
    observer = MetadataStore(
        db_path,
        sync_owner_id="observer",
        running_job_timeout_seconds=24 * 60 * 60,
    )
    previous_worker.upsert_source(
        SourceModel(
            source_id="source_a",
            source_type=SourceType.GITHUB,
            name="source_a",
            enabled=True,
        )
    )
    active_job, started = previous_worker.begin_sync_job("source_a")
    assert started is True
    observer.ensure_schema()
    with observer._connect() as conn:
        conn.execute(
            """
            UPDATE sync_job_owners
            SET process_start_id = ?
            WHERE owner_id = ?
            """,
            ("linux-v2|boot-a|pidns-a|100", "previous-worker"),
        )

    monkeypatch.setattr(
        MetadataStore,
        "_is_process_alive",
        staticmethod(lambda process_id: False),
    )
    monkeypatch.setattr(
        MetadataStore,
        "_get_process_start_identity",
        staticmethod(lambda process_id: observer_identity),
    )

    recovered_count = observer.recover_orphaned_running_jobs(
        started_before="9999-01-01T00:00:00+00:00",
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
    )

    assert recovered_count == 0
    assert observer.get_sync_job(active_job.job_id).status == SyncJobStatus.RUNNING


@pytest.mark.parametrize(
    "stored_identity",
    [
        "",
        "linux:boot-a:100",
        "malformed-process-identity",
    ],
)
def test_global_claim_preserves_fresh_linux_owner_with_unknown_stored_scope(
    tmp_path,
    monkeypatch,
    stored_identity,
):
    db_path = tmp_path / "contextwiki.sqlite3"
    previous_worker = MetadataStore(db_path, sync_owner_id="previous-worker")
    requester = MetadataStore(db_path, sync_owner_id="requester")
    observer = MetadataStore(db_path, sync_owner_id="observer")
    for source_id in ("source_a", "source_b"):
        requester.upsert_source(
            SourceModel(
                source_id=source_id,
                source_type=SourceType.GITHUB,
                name=source_id,
                enabled=True,
            )
        )

    source_a_job, started = previous_worker.begin_sync_job("source_a")
    assert started is True
    source_b_job, enqueued = requester.enqueue_sync_job("source_b")
    assert enqueued is True
    with observer._connect() as conn:
        conn.execute(
            """
            UPDATE sync_job_owners
            SET process_start_id = ?
            WHERE owner_id = ?
            """,
            (stored_identity, "previous-worker"),
        )

    monkeypatch.setattr(
        MetadataStore,
        "_is_process_alive",
        staticmethod(lambda process_id: False),
    )
    monkeypatch.setattr(
        MetadataStore,
        "_get_process_start_identity",
        staticmethod(lambda process_id: "linux-v2|boot-a|pidns-observer|300"),
    )

    claimed = observer.claim_next_sync_job(["source_a", "source_b"])

    assert claimed is None
    assert requester.get_sync_job(source_a_job.job_id).status == SyncJobStatus.RUNNING
    assert requester.get_sync_job(source_b_job.job_id).status == SyncJobStatus.QUEUED


@pytest.mark.parametrize(
    "stored_identity",
    [
        "",
        "linux:boot-a:100",
        "malformed-process-identity",
    ],
)
def test_startup_recovery_preserves_fresh_linux_owner_with_unknown_stored_scope(
    tmp_path,
    monkeypatch,
    stored_identity,
):
    db_path = tmp_path / "contextwiki.sqlite3"
    previous_worker = MetadataStore(
        db_path,
        sync_owner_id="previous-worker",
        running_job_timeout_seconds=24 * 60 * 60,
    )
    observer = MetadataStore(
        db_path,
        sync_owner_id="observer",
        running_job_timeout_seconds=24 * 60 * 60,
    )
    previous_worker.upsert_source(
        SourceModel(
            source_id="source_a",
            source_type=SourceType.GITHUB,
            name="source_a",
            enabled=True,
        )
    )
    active_job, started = previous_worker.begin_sync_job("source_a")
    assert started is True
    observer.ensure_schema()
    with observer._connect() as conn:
        conn.execute(
            """
            UPDATE sync_job_owners
            SET process_start_id = ?
            WHERE owner_id = ?
            """,
            (stored_identity, "previous-worker"),
        )

    monkeypatch.setattr(
        MetadataStore,
        "_is_process_alive",
        staticmethod(lambda process_id: False),
    )
    monkeypatch.setattr(
        MetadataStore,
        "_get_process_start_identity",
        staticmethod(lambda process_id: "linux-v2|boot-a|pidns-observer|300"),
    )

    recovered_count = observer.recover_orphaned_running_jobs(
        started_before="9999-01-01T00:00:00+00:00",
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
    )

    assert recovered_count == 0
    assert observer.get_sync_job(active_job.job_id).status == SyncJobStatus.RUNNING


@pytest.mark.parametrize(
    "stored_identity",
    [
        "",
        "legacy-process-identity",
        "darwin:invalid:0",
        "linux-v2|boot-a|pidns-a|100",
    ],
)
def test_dead_owner_is_not_definitive_for_unknown_identity_from_darwin_observer(
    monkeypatch,
    stored_identity,
):
    monkeypatch.setattr(
        "storage.metadata_store.sys.platform",
        "darwin",
    )
    monkeypatch.setattr(
        MetadataStore,
        "_get_process_start_identity",
        staticmethod(lambda process_id: "darwin:300:0"),
    )

    assert (
        MetadataStore._dead_owner_is_definitive_in_current_scope(
            {"process_start_id": stored_identity}
        )
        is False
    )


@pytest.mark.parametrize(
    "stored_identity",
    [
        "",
        "legacy-process-identity",
        "darwin:invalid:0",
        "linux-v2|boot-a|pidns-a|100",
    ],
)
def test_global_claim_preserves_fresh_owner_with_unknown_scope_on_darwin_esrch(
    tmp_path,
    monkeypatch,
    stored_identity,
):
    db_path = tmp_path / "contextwiki.sqlite3"
    previous_worker = MetadataStore(db_path, sync_owner_id="previous-worker")
    requester = MetadataStore(db_path, sync_owner_id="requester")
    observer = MetadataStore(db_path, sync_owner_id="observer")
    for source_id in ("source_a", "source_b"):
        requester.upsert_source(
            SourceModel(
                source_id=source_id,
                source_type=SourceType.GITHUB,
                name=source_id,
                enabled=True,
            )
        )

    source_a_job, started = previous_worker.begin_sync_job("source_a")
    assert started is True
    source_b_job, enqueued = requester.enqueue_sync_job("source_b")
    assert enqueued is True
    with observer._connect() as conn:
        conn.execute(
            """
            UPDATE sync_job_owners
            SET process_start_id = ?
            WHERE owner_id = ?
            """,
            (stored_identity, "previous-worker"),
        )

    monkeypatch.setattr(
        "storage.metadata_store.sys.platform",
        "darwin",
    )
    monkeypatch.setattr(
        MetadataStore,
        "_is_process_alive",
        staticmethod(lambda process_id: False),
    )
    monkeypatch.setattr(
        MetadataStore,
        "_get_process_start_identity",
        staticmethod(lambda process_id: "darwin:300:0"),
    )

    claimed = observer.claim_next_sync_job(["source_a", "source_b"])

    assert claimed is None
    assert requester.get_sync_job(source_a_job.job_id).status == SyncJobStatus.RUNNING
    assert requester.get_sync_job(source_b_job.job_id).status == SyncJobStatus.QUEUED


@pytest.mark.parametrize(
    "stored_identity",
    [
        "",
        "legacy-process-identity",
        "darwin:invalid:0",
        "linux-v2|boot-a|pidns-a|100",
    ],
)
def test_startup_recovery_preserves_fresh_owner_with_unknown_scope_on_darwin_esrch(
    tmp_path,
    monkeypatch,
    stored_identity,
):
    db_path = tmp_path / "contextwiki.sqlite3"
    previous_worker = MetadataStore(
        db_path,
        sync_owner_id="previous-worker",
        running_job_timeout_seconds=24 * 60 * 60,
    )
    observer = MetadataStore(
        db_path,
        sync_owner_id="observer",
        running_job_timeout_seconds=24 * 60 * 60,
    )
    previous_worker.upsert_source(
        SourceModel(
            source_id="source_a",
            source_type=SourceType.GITHUB,
            name="source_a",
            enabled=True,
        )
    )
    active_job, started = previous_worker.begin_sync_job("source_a")
    assert started is True
    observer.ensure_schema()
    with observer._connect() as conn:
        conn.execute(
            """
            UPDATE sync_job_owners
            SET process_start_id = ?
            WHERE owner_id = ?
            """,
            (stored_identity, "previous-worker"),
        )

    monkeypatch.setattr(
        "storage.metadata_store.sys.platform",
        "darwin",
    )
    monkeypatch.setattr(
        MetadataStore,
        "_is_process_alive",
        staticmethod(lambda process_id: False),
    )
    monkeypatch.setattr(
        MetadataStore,
        "_get_process_start_identity",
        staticmethod(lambda process_id: "darwin:300:0"),
    )

    recovered_count = observer.recover_orphaned_running_jobs(
        started_before="9999-01-01T00:00:00+00:00",
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
    )

    assert recovered_count == 0
    assert observer.get_sync_job(active_job.job_id).status == SyncJobStatus.RUNNING


@pytest.mark.parametrize(
    ("stored_identity", "observed_identity"),
    [
        (
            "linux-v2|boot-a|pidns-a|not-numeric",
            "linux-v2|boot-a|pidns-a|300",
        ),
        (
            "linux-v2|boot-a|pidns-a|-1",
            "linux-v2|boot-a|pidns-a|300",
        ),
        (
            "linux-v2|boot-a|pidns-a|0",
            "linux-v2|boot-a|pidns-a|300",
        ),
        (
            "linux-v2|boot-a|pidns-a|\u0661",
            "linux-v2|boot-a|pidns-a|300",
        ),
        (
            "linux-v2|boot-a|pidns-a|001",
            "linux-v2|boot-a|pidns-a|1",
        ),
        (
            "linux-v2|boot-a|pidns-a|100|extra",
            "linux-v2|boot-a|pidns-a|300",
        ),
        (
            "linux-v2||pidns-a|100",
            "linux-v2|boot-a|pidns-a|300",
        ),
        (
            "linux-v2|boot-a|pidns-a|",
            "linux-v2|boot-a|pidns-a|300",
        ),
        (
            "darwin:100:not-numeric",
            "darwin:300:0",
        ),
        (
            "darwin:-1:0",
            "darwin:300:0",
        ),
        (
            "darwin:0:0",
            "darwin:300:0",
        ),
        (
            "darwin:\u0661:0",
            "darwin:300:0",
        ),
        (
            "darwin:100:\u0661",
            "darwin:300:0",
        ),
        (
            "darwin:001:0",
            "darwin:1:0",
        ),
        (
            "darwin:100:001",
            "darwin:100:1",
        ),
        (
            "darwin:100:0:extra",
            "darwin:300:0",
        ),
        (
            "darwin::0",
            "darwin:300:0",
        ),
        (
            "darwin:100:1000000",
            "darwin:300:0",
        ),
    ],
)
def test_global_claim_preserves_fresh_live_owner_with_malformed_recognized_identity(
    tmp_path,
    monkeypatch,
    stored_identity,
    observed_identity,
):
    db_path = tmp_path / "contextwiki.sqlite3"
    previous_worker = MetadataStore(db_path, sync_owner_id="previous-worker")
    requester = MetadataStore(db_path, sync_owner_id="requester")
    observer = MetadataStore(db_path, sync_owner_id="observer")
    for source_id in ("source_a", "source_b"):
        requester.upsert_source(
            SourceModel(
                source_id=source_id,
                source_type=SourceType.GITHUB,
                name=source_id,
                enabled=True,
            )
        )

    source_a_job, started = previous_worker.begin_sync_job("source_a")
    assert started is True
    source_b_job, enqueued = requester.enqueue_sync_job("source_b")
    assert enqueued is True
    with observer._connect() as conn:
        conn.execute(
            """
            UPDATE sync_job_owners
            SET process_start_id = ?
            WHERE owner_id = ?
            """,
            (stored_identity, "previous-worker"),
        )

    monkeypatch.setattr(
        MetadataStore,
        "_is_process_alive",
        staticmethod(lambda process_id: True),
    )
    monkeypatch.setattr(
        MetadataStore,
        "_get_process_start_identity",
        staticmethod(lambda process_id: observed_identity),
    )

    claimed = observer.claim_next_sync_job(["source_a", "source_b"])

    assert claimed is None
    assert requester.get_sync_job(source_a_job.job_id).status == SyncJobStatus.RUNNING
    assert requester.get_sync_job(source_b_job.job_id).status == SyncJobStatus.QUEUED


@pytest.mark.parametrize(
    ("stored_identity", "observed_identity"),
    [
        (
            "linux-v2|boot-a|pidns-a|not-numeric",
            "linux-v2|boot-a|pidns-a|300",
        ),
        (
            "linux-v2|boot-a|pidns-a|-1",
            "linux-v2|boot-a|pidns-a|300",
        ),
        (
            "linux-v2|boot-a|pidns-a|0",
            "linux-v2|boot-a|pidns-a|300",
        ),
        (
            "linux-v2|boot-a|pidns-a|\u0661",
            "linux-v2|boot-a|pidns-a|300",
        ),
        (
            "linux-v2|boot-a|pidns-a|001",
            "linux-v2|boot-a|pidns-a|1",
        ),
        (
            "linux-v2|boot-a|pidns-a|100|extra",
            "linux-v2|boot-a|pidns-a|300",
        ),
        (
            "linux-v2||pidns-a|100",
            "linux-v2|boot-a|pidns-a|300",
        ),
        (
            "linux-v2|boot-a|pidns-a|",
            "linux-v2|boot-a|pidns-a|300",
        ),
        (
            "darwin:100:not-numeric",
            "darwin:300:0",
        ),
        (
            "darwin:-1:0",
            "darwin:300:0",
        ),
        (
            "darwin:0:0",
            "darwin:300:0",
        ),
        (
            "darwin:\u0661:0",
            "darwin:300:0",
        ),
        (
            "darwin:100:\u0661",
            "darwin:300:0",
        ),
        (
            "darwin:001:0",
            "darwin:1:0",
        ),
        (
            "darwin:100:001",
            "darwin:100:1",
        ),
        (
            "darwin:100:0:extra",
            "darwin:300:0",
        ),
        (
            "darwin::0",
            "darwin:300:0",
        ),
        (
            "darwin:100:1000000",
            "darwin:300:0",
        ),
    ],
)
def test_startup_recovery_preserves_fresh_live_owner_with_malformed_recognized_identity(
    tmp_path,
    monkeypatch,
    stored_identity,
    observed_identity,
):
    db_path = tmp_path / "contextwiki.sqlite3"
    previous_worker = MetadataStore(
        db_path,
        sync_owner_id="previous-worker",
        running_job_timeout_seconds=24 * 60 * 60,
    )
    observer = MetadataStore(
        db_path,
        sync_owner_id="observer",
        running_job_timeout_seconds=24 * 60 * 60,
    )
    previous_worker.upsert_source(
        SourceModel(
            source_id="source_a",
            source_type=SourceType.GITHUB,
            name="source_a",
            enabled=True,
        )
    )
    active_job, started = previous_worker.begin_sync_job("source_a")
    assert started is True
    observer.ensure_schema()
    with observer._connect() as conn:
        conn.execute(
            """
            UPDATE sync_job_owners
            SET process_start_id = ?
            WHERE owner_id = ?
            """,
            (stored_identity, "previous-worker"),
        )

    monkeypatch.setattr(
        MetadataStore,
        "_is_process_alive",
        staticmethod(lambda process_id: True),
    )
    monkeypatch.setattr(
        MetadataStore,
        "_get_process_start_identity",
        staticmethod(lambda process_id: observed_identity),
    )

    recovered_count = observer.recover_orphaned_running_jobs(
        started_before="9999-01-01T00:00:00+00:00",
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
    )

    assert recovered_count == 0
    assert observer.get_sync_job(active_job.job_id).status == SyncJobStatus.RUNNING


def test_orphan_recovery_message_is_execution_owner_neutral():
    assert ORPHANED_SYNC_JOB_RECOVERY_MESSAGE == (
        "Previous running sync job was recovered after its execution owner stopped "
        "responding; start sync again."
    )


def test_metadata_store_exposes_public_canonical_document_timestamp():
    """Fetch/skip callers must use the public helper, not the private underscore name."""
    assert hasattr(MetadataStore, "canonical_document_timestamp")
    assert callable(MetadataStore.canonical_document_timestamp)
    assert (
        MetadataStore.canonical_document_timestamp("2026-06-01T00:00:00+00:00")
        == "2026-06-01T00:00:00Z"
    )
    assert MetadataStore.canonical_document_timestamp("") == ""
    assert MetadataStore.canonical_document_timestamp("not-a-timestamp") == ""
    assert MetadataStore.canonical_document_timestamp("2026-06-01T00:00:00Z") == (
        "2026-06-01T00:00:00Z"
    )


def test_get_documents_for_fetch_reuse_returns_empty_for_empty_ids(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")

    assert store.get_documents_for_fetch_reuse([]) == {}
    assert store.get_documents_for_fetch_reuse(()) == {}


def test_get_documents_for_fetch_reuse_batches_skip_fields_for_requested_ids(
    tmp_path, monkeypatch
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    docs = [
        DocumentModel(
            id="doc-a",
            document_id="page-a",
            external_id="page-a",
            source_id="source_notion",
            title="A",
            content="body-a",
            url="https://notion.so/page-a",
            platform="Notion",
            modified_at="2026-06-01T00:00:00Z",
            content_hash="hash-a",
            deleted_at="",
        ),
        DocumentModel(
            id="doc-b",
            document_id="page-b",
            external_id="page-b",
            source_id="source_notion",
            title="B",
            content="body-b",
            url="https://notion.so/page-b",
            platform="Notion",
            modified_at="2026-06-02T00:00:00Z",
            content_hash="hash-b",
            deleted_at="",
        ),
        DocumentModel(
            id="doc-c",
            document_id="page-c",
            external_id="page-c",
            source_id="source_notion",
            title="C",
            content="body-c",
            url="https://notion.so/page-c",
            platform="Notion",
            modified_at="2026-06-03T00:00:00Z",
            content_hash="hash-c",
            deleted_at="2026-06-04T00:00:00Z",
        ),
    ]
    for doc in docs:
        store.upsert_document(doc)

    connect_calls: list[int] = []
    statements: list[str] = []
    original_connect = store._connect

    @contextmanager
    def traced_connect():
        connect_calls.append(1)
        with original_connect() as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(store, "_connect", traced_connect)

    loaded = store.get_documents_for_fetch_reuse(
        ["page-a", "page-b", "page-c", "page-missing"]
    )

    assert set(loaded) == {"page-a", "page-b", "page-c"}
    assert "page-missing" not in loaded
    assert loaded["page-a"].content == "body-a"
    assert loaded["page-a"].modified_at == "2026-06-01T00:00:00Z"
    assert loaded["page-a"].content_hash
    assert loaded["page-a"].deleted_at == ""
    assert loaded["page-b"].content == "body-b"
    assert loaded["page-c"].deleted_at == "2026-06-04T00:00:00Z"
    assert len(connect_calls) == 1
    select_statements = [
        statement
        for statement in statements
        if "SELECT" in statement.upper() and "from documents" in statement.lower()
    ]
    assert len(select_statements) == 1
    assert "IN (" in select_statements[0].upper() or "?," in select_statements[0]


def test_get_documents_for_fetch_reuse_omits_missing_ids(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_document(
        DocumentModel(
            id="doc-only",
            document_id="page-only",
            external_id="page-only",
            source_id="source_notion",
            title="Only",
            content="only-body",
            url="https://notion.so/page-only",
            platform="Notion",
            modified_at="2026-06-01T00:00:00Z",
        )
    )

    loaded = store.get_documents_for_fetch_reuse(["page-only", "page-gone"])

    assert set(loaded) == {"page-only"}
    assert loaded["page-only"].content == "only-body"


def test_update_sync_job_dual_writes_upstream_progress_to_legacy_page_columns(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    job, started = store.begin_sync_job("source_notion")
    assert started is True

    updated = store.update_sync_job(
        job.job_id,
        upstream_total=12,
        upstream_done=5,
        phase="fetching_page_content",
        status_message="Fetching upstream items 5/12 before indexing begins.",
    )

    assert updated.upstream_total == 12
    assert updated.upstream_done == 5
    with store._connect() as conn:
        row = conn.execute(
            """
            SELECT upstream_total, upstream_done,
                   upstream_total_pages, upstream_fetched_pages
            FROM sync_jobs WHERE job_id = ?
            """,
            (job.job_id,),
        ).fetchone()
    assert row["upstream_total"] == 12
    assert row["upstream_done"] == 5
    assert row["upstream_total_pages"] == 12
    assert row["upstream_fetched_pages"] == 5


def test_sync_job_read_prefers_primary_upstream_fields_even_when_zero(
    tmp_path,
):
    """Primary zeros are intentional (e.g. search_completed); do not resurface legacy."""
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

    with store._connect() as conn:
        conn.execute(
            """
            UPDATE sync_jobs SET
                upstream_total = 0,
                upstream_done = 0,
                upstream_total_pages = 9,
                upstream_fetched_pages = 4
            WHERE job_id = ?
            """,
            (job.job_id,),
        )

    loaded = store.get_sync_job(job.job_id)
    assert loaded is not None
    assert loaded.upstream_total == 0
    assert loaded.upstream_done == 0
    dumped = loaded.model_dump()
    assert "upstream_total_pages" not in dumped
    assert "upstream_fetched_pages" not in dumped


def test_ensure_schema_backfills_upstream_progress_from_legacy_page_columns(tmp_path):
    db_path = tmp_path / "contextwiki.sqlite3"
    store = MetadataStore(db_path)
    store.upsert_source(
        SourceModel(
            source_id="source_tistory",
            source_type=SourceType.TISTORY,
            name="Tistory",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    job, started = store.begin_sync_job("source_tistory")
    assert started is True

    with store._connect() as conn:
        conn.execute(
            """
            UPDATE sync_jobs SET
                upstream_total = 0,
                upstream_done = 0,
                upstream_total_pages = 7,
                upstream_fetched_pages = 3
            WHERE job_id = ?
            """,
            (job.job_id,),
        )

    # Migrate backfill runs once per process on first ensure_schema (not when
    # _schema_ready is already true on the same store instance).
    upgraded = MetadataStore(db_path)
    upgraded.ensure_schema()

    with upgraded._connect() as conn:
        row = conn.execute(
            """
            SELECT upstream_total, upstream_done,
                   upstream_total_pages, upstream_fetched_pages
            FROM sync_jobs WHERE job_id = ?
            """,
            (job.job_id,),
        ).fetchone()
    assert row["upstream_total"] == 7
    assert row["upstream_done"] == 3
    assert row["upstream_total_pages"] == 7
    assert row["upstream_fetched_pages"] == 3
    loaded = upgraded.get_sync_job(job.job_id)
    assert loaded is not None
    assert loaded.upstream_total == 7
    assert loaded.upstream_done == 3
