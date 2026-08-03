from __future__ import annotations

from contextlib import closing
import sqlite3

import pytest

from core.models import (
    ChunkModel,
    DocumentModel,
    EvidenceSourceType,
    ExperienceType,
    SourceModel,
    SourceType,
    SyncStatus,
)
from core.utils import ContentHasher
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.integration


def _career_document(**updates) -> DocumentModel:
    values = {
        "id": "career:resume.md",
        "document_id": "career:resume.md",
        "external_id": "resume.md",
        "title": "Synthetic Resume",
        "document_title": "Synthetic Resume",
        "content": "# Experience\nBuilt a reliable queue.",
        "url": "career://resume.md",
        "canonical_url": "career://resume.md",
        "platform": "career",
        "source_id": "source_career",
        "path": "resume.md",
        "file_name": "resume.md",
        "evidence_source_type": EvidenceSourceType.RESUME,
        "experience_type": ExperienceType.PROFESSIONAL,
        "document_version_id": "version-1",
        "version_id": "legacy-version-1",
        "content_hash": ContentHasher.hash_content(
            "# Experience\nBuilt a reliable queue."
        ),
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-02T00:00:00Z",
        "company": "Example Corp",
        "role": "Backend Engineer",
        "project": "Queue migration",
        "start_date": "2024-01",
        "end_date": "2025-01",
    }
    values.update(updates)
    return DocumentModel(**values)


def _career_chunk(document: DocumentModel, **updates) -> ChunkModel:
    text = "# Experience\nBuilt a reliable queue."
    values = {
        "chunk_id": f"{document.document_id}:chunk:experience",
        "document_id": document.document_id,
        "source_id": document.source_id,
        "title": document.title,
        "document_title": document.document_title,
        "text": text,
        "exact_quote": text,
        "url": document.canonical_url,
        "path": document.path,
        "file_name": document.file_name,
        "chunk_index": 0,
        "line_start": 1,
        "line_end": 2,
        "section_title": "Experience",
        "parent_section_title": "",
        "document_version_id": document.document_version_id,
        "version_id": document.version_id,
        "content_hash": ContentHasher.hash_content(text),
        "created_at": document.created_at,
        "updated_at": document.updated_at,
        "evidence_source_type": document.evidence_source_type,
        "experience_type": document.experience_type,
        "company": document.company,
        "role": document.role,
        "project": document.project,
        "start_date": document.start_date,
        "end_date": document.end_date,
    }
    values.update(updates)
    return ChunkModel(**values)


def test_evidence_metadata_round_trips_atomically_through_sqlite(tmp_path):
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    document = _career_document()
    chunk = _career_chunk(document)

    stored = store.upsert_document_and_replace_chunks(document, [chunk])
    loaded_document = store.get_document(document.document_id)
    loaded_chunk = store.get_chunk(chunk.chunk_id)

    assert stored.document_version_id == "version-1"
    assert loaded_document is not None
    assert loaded_document.document_version_id == "version-1"
    assert loaded_document.evidence_source_type == EvidenceSourceType.RESUME
    assert loaded_document.experience_type == ExperienceType.PROFESSIONAL
    assert loaded_document.file_name == "resume.md"
    assert loaded_document.company == "Example Corp"
    assert loaded_document.role == "Backend Engineer"
    assert loaded_document.project == "Queue migration"
    assert loaded_document.start_date == "2024-01"
    assert loaded_document.end_date == "2025-01"

    assert loaded_chunk is not None
    assert loaded_chunk.document_version_id == "version-1"
    assert loaded_chunk.evidence_source_type == EvidenceSourceType.RESUME
    assert loaded_chunk.experience_type == ExperienceType.PROFESSIONAL
    assert loaded_chunk.document_title == "Synthetic Resume"
    assert loaded_chunk.section_title == "Experience"
    assert loaded_chunk.parent_section_title == ""
    assert loaded_chunk.exact_quote == loaded_chunk.text
    assert loaded_chunk.file_name == "resume.md"
    assert loaded_chunk.company == "Example Corp"


def test_vector_write_intent_survives_until_chunk_commit_resolves_it_atomically(
    tmp_path,
):
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    source = SourceModel(
        source_id="source_career",
        source_type=SourceType.OBSIDIAN,
        name="Career files",
        enabled=True,
        sync_status=SyncStatus.IDLE,
    )
    store.upsert_source(source)
    job, started = store.begin_sync_job(source.source_id)
    assert started is True
    document = _career_document()
    chunk = _career_chunk(document)

    store.record_vector_write_intents(
        [chunk.chunk_id],
        source_id=source.source_id,
        document_id=document.document_id,
        job_id=job.job_id,
    )

    assert store.list_pending_vector_cleanup_ids(source.source_id) == [chunk.chunk_id]

    stored, current_job = store.upsert_document_and_replace_chunks_for_running_job(
        job.job_id,
        document,
        [chunk],
    )

    assert stored is not None
    assert current_job is not None
    assert store.list_pending_vector_cleanup_ids(source.source_id) == []
    with store._connect() as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM vector_write_intents WHERE chunk_id = ?",
                (chunk.chunk_id,),
            ).fetchone()
            is None
        )


def test_vector_metadata_refresh_intent_is_resolved_by_authoritative_chunk_commit(
    tmp_path,
):
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    source = SourceModel(
        source_id="source_career",
        source_type=SourceType.CAREER,
        name="Career files",
        enabled=True,
        sync_status=SyncStatus.IDLE,
    )
    store.upsert_source(source)
    original = _career_document()
    original_chunk = _career_chunk(original)
    store.upsert_document_and_replace_chunks(original, [original_chunk])
    job, started = store.begin_sync_job(source.source_id)
    assert started is True

    store.record_vector_metadata_refresh_intents(
        [original_chunk.chunk_id],
        source_id=source.source_id,
        document_id=original.document_id,
        job_id=job.job_id,
    )

    assert store.list_pending_vector_metadata_refresh_ids(
        source.source_id,
        document_id=original.document_id,
    ) == [original_chunk.chunk_id]

    refreshed = original.model_copy(
        update={
            "evidence_source_type": EvidenceSourceType.CAREER_NOTE,
            "experience_type": ExperienceType.ACADEMIC,
        }
    )
    refreshed_chunk = original_chunk.model_copy(
        update={
            "evidence_source_type": EvidenceSourceType.CAREER_NOTE,
            "experience_type": ExperienceType.ACADEMIC,
        }
    )
    stored, current_job = store.upsert_document_and_replace_chunks_for_running_job(
        job.job_id,
        refreshed,
        [refreshed_chunk],
    )

    assert stored is not None
    assert current_job is not None
    assert store.list_pending_vector_metadata_refresh_ids(
        source.source_id,
        document_id=original.document_id,
    ) == []
    with store._connect() as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM vector_metadata_refresh_intents WHERE chunk_id = ?",
                (original_chunk.chunk_id,),
            ).fetchone()
            is None
        )


def test_active_tombstone_history_is_acknowledged_and_pending_scan_is_indexed(
    tmp_path,
):
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    document = _career_document()
    chunk = _career_chunk(document)
    store.record_pending_vector_cleanup_ids(
        [chunk.chunk_id], source_id=document.source_id
    )

    store.upsert_document_and_replace_chunks(document, [chunk])

    assert store.list_pending_vector_cleanup_ids(document.source_id) == []
    with store._connect() as conn:
        tombstone = conn.execute(
            "SELECT vector_cleanup_at FROM chunk_tombstones WHERE chunk_id = ?",
            (chunk.chunk_id,),
        ).fetchone()
        index_columns = [
            row["name"]
            for row in conn.execute(
                "PRAGMA index_info('idx_chunk_tombstones_pending')"
            ).fetchall()
        ]
    assert tombstone is not None
    assert tombstone["vector_cleanup_at"]
    assert index_columns == ["source_id", "recorded_at", "chunk_id"]


def test_active_evidence_snapshots_batch_join_chunk_and_document_state(tmp_path):
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    first_document = _career_document()
    second_document = _career_document(
        id="career:project.md",
        document_id="career:project.md",
        external_id="project.md",
        title="Synthetic Project",
        document_title="Synthetic Project",
        path="project.md",
        file_name="project.md",
        evidence_source_type=EvidenceSourceType.PROJECT,
        experience_type=ExperienceType.PERSONAL_PROJECT,
    )
    first_chunk = _career_chunk(first_document)
    second_chunk = _career_chunk(
        second_document,
        chunk_id="career:project.md:chunk:project",
    )
    store.upsert_document_and_replace_chunks(first_document, [first_chunk])
    store.upsert_document_and_replace_chunks(second_document, [second_chunk])

    snapshots = store.get_active_evidence_snapshots(
        [first_chunk.chunk_id, second_chunk.chunk_id, first_chunk.chunk_id, "missing"]
    )

    assert list(snapshots) == [first_chunk.chunk_id, second_chunk.chunk_id]
    first_snapshot_chunk, first_snapshot_document = snapshots[first_chunk.chunk_id]
    second_snapshot_chunk, second_snapshot_document = snapshots[second_chunk.chunk_id]
    assert first_snapshot_chunk == first_chunk
    assert first_snapshot_document.document_id == first_document.document_id
    assert first_snapshot_document.evidence_source_type == EvidenceSourceType.RESUME
    assert second_snapshot_chunk == second_chunk
    assert second_snapshot_document.document_id == second_document.document_id
    assert second_snapshot_document.evidence_source_type == EvidenceSourceType.PROJECT

    store.upsert_document(second_document.model_copy(update={"deleted_at": "deleted"}))

    assert store.get_active_evidence_snapshots([second_chunk.chunk_id]) == {}


def test_evidence_metadata_replace_is_document_scoped_and_removes_old_chunks(tmp_path):
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    first = _career_document()
    unrelated = _career_document(
        id="career:skills.txt",
        document_id="career:skills.txt",
        external_id="skills.txt",
        title="Skills",
        document_title="Skills",
        path="skills.txt",
        file_name="skills.txt",
        content="Python and SQLite.",
        content_hash=ContentHasher.hash_content("Python and SQLite."),
        evidence_source_type=EvidenceSourceType.SKILLS_INVENTORY,
        experience_type=ExperienceType.UNKNOWN,
    )
    first_chunk = _career_chunk(first)
    unrelated_chunk = _career_chunk(
        unrelated,
        chunk_id="career:skills.txt:chunk:skills",
        text="Python and SQLite.",
        exact_quote="Python and SQLite.",
        section_title="Skills",
        content_hash=ContentHasher.hash_content("Python and SQLite."),
    )
    store.upsert_document_and_replace_chunks(first, [first_chunk])
    store.upsert_document_and_replace_chunks(unrelated, [unrelated_chunk])

    updated = first.model_copy(
        update={
            "content": "# Experience\nBuilt two reliable queues.",
            "document_version_id": "version-2",
            "content_hash": ContentHasher.hash_content(
                "# Experience\nBuilt two reliable queues."
            ),
        }
    )
    updated_chunk = _career_chunk(
        updated,
        chunk_id="career:resume.md:chunk:experience-v2",
        text="# Experience\nBuilt two reliable queues.",
        exact_quote="# Experience\nBuilt two reliable queues.",
        document_version_id="version-2",
        content_hash=ContentHasher.hash_content(
            "# Experience\nBuilt two reliable queues."
        ),
    )
    store.upsert_document_and_replace_chunks(updated, [updated_chunk])

    assert store.get_chunk(first_chunk.chunk_id) is None
    assert store.has_chunk_record(first_chunk.chunk_id) is True
    assert store.get_chunk(updated_chunk.chunk_id) == updated_chunk
    assert store.get_document(unrelated.document_id) is not None
    assert store.get_chunk(unrelated_chunk.chunk_id) == unrelated_chunk


def test_sync_job_ingestion_metrics_round_trip_with_zero_defaults(tmp_path):
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.register_source(
        SourceModel(
            source_id="source_career",
            source_type=SourceType.CAREER,
            name="Career files",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    created = store.create_sync_job("source_career")

    assert created.parsed_documents == 0
    assert created.updated_documents == 0
    assert created.created_chunks == 0
    assert created.updated_chunks == 0
    assert created.skipped_chunks == 0
    assert created.embeddings_generated == 0
    assert created.embeddings_reused == 0
    assert created.parsing_failures == 0
    assert created.indexing_latency_ms == 0.0

    store.update_sync_job(
        created.job_id,
        parsed_documents=2,
        updated_documents=1,
        created_chunks=3,
        updated_chunks=1,
        skipped_chunks=4,
        embeddings_generated=4,
        embeddings_reused=4,
        parsing_failures=1,
        indexing_latency_ms=12.5,
    )
    loaded = store.get_sync_job(created.job_id)

    assert loaded is not None
    assert loaded.parsed_documents == 2
    assert loaded.updated_documents == 1
    assert loaded.created_chunks == 3
    assert loaded.updated_chunks == 1
    assert loaded.skipped_chunks == 4
    assert loaded.embeddings_generated == 4
    assert loaded.embeddings_reused == 4
    assert loaded.parsing_failures == 1
    assert loaded.indexing_latency_ms == 12.5


def test_legacy_document_and_chunk_schema_migrates_additively(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(db_path)) as conn:
        with conn:
            conn.executescript(
                """
            CREATE TABLE documents (
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
                version_id TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO documents (
                document_id, source_id, external_id, title, content, url,
                canonical_url, platform, date, path, updated_at, published_at,
                modified_at, indexed_at, date_provenance, last_seen_at,
                last_seen_sync_id, deleted_at, version_id, content_hash
            ) VALUES (
                'legacy-doc', 'source_notion', 'legacy-doc', 'Legacy',
                'Legacy content.', 'https://example.test/legacy',
                'https://example.test/legacy', 'Notion', '', 'Legacy', '',
                '', '', '', '', '', '', '', 'legacy-version', 'legacy-hash'
            );
            INSERT INTO chunks (
                chunk_id, document_id, source_id, title, text, url, path,
                chunk_index, line_start, line_end, version_id, content_hash,
                updated_at
            ) VALUES (
                'legacy-chunk', 'legacy-doc', 'source_notion', 'Legacy',
                'Legacy content.', 'https://example.test/legacy', 'Legacy',
                0, 1, 1, 'legacy-version', 'legacy-hash', ''
            );
                """
            )

    store = MetadataStore(db_path)
    document = store.get_document("legacy-doc")
    chunk = store.get_chunk("legacy-chunk")

    assert document is not None
    assert document.document_version_id == ""
    assert document.evidence_source_type is None
    assert document.experience_type == ExperienceType.UNKNOWN
    assert document.file_name == ""
    assert document.company == ""

    assert chunk is not None
    assert chunk.document_version_id == ""
    assert chunk.evidence_source_type is None
    assert chunk.experience_type == ExperienceType.UNKNOWN
    assert chunk.section_title == ""
    assert chunk.parent_section_title == ""
    assert chunk.exact_quote == ""


def test_schema_recheck_does_not_backfill_unrelated_current_rows(tmp_path):
    db_path = tmp_path / "current.sqlite3"
    store = MetadataStore(db_path)
    document = DocumentModel(
        id="notion-current",
        document_id="notion-current",
        source_id="source_notion",
        title="Current row",
        content="Current content.",
        url="https://example.test/current",
        platform="Notion",
        version_id="legacy-compatible-version",
        content_hash="current-hash",
    )
    chunk = ChunkModel(
        chunk_id="notion-current:chunk:0",
        document_id=document.document_id,
        source_id=document.source_id,
        title=document.title,
        text=document.content,
        chunk_index=0,
        version_id="legacy-compatible-version",
        content_hash="current-hash",
    )
    store.upsert_document_and_replace_chunks(document, [chunk])

    reopened = MetadataStore(db_path)
    reopened.ensure_schema()
    loaded = reopened.get_chunk(chunk.chunk_id)

    assert loaded is not None
    assert loaded.document_version_id == ""
    assert loaded.exact_quote == ""


def test_non_career_writes_do_not_copy_evidence_only_metadata(tmp_path):
    store = MetadataStore(tmp_path / "non-career.sqlite3")
    document = DocumentModel(
        id="github-document",
        document_id="github-document",
        source_id="source_github",
        title="GitHub document",
        content="Public synthetic content.",
        url="https://example.test/repository",
        platform="GitHub",
        content_hash="document-hash",
        exact_quote="must not persist",
        document_title="must not persist",
        company="must not persist",
    )
    chunk = ChunkModel(
        chunk_id="github-document:chunk:0:hash",
        document_id=document.document_id,
        source_id=document.source_id,
        title=document.title,
        text=document.content,
        chunk_index=0,
        content_hash="chunk-hash",
        exact_quote="must not persist",
        document_title="must not persist",
        company="must not persist",
    )

    store.upsert_document_and_replace_chunks(document, [chunk])
    loaded_document = store.get_document(document.document_id)
    loaded_chunk = store.get_chunk(chunk.chunk_id)

    assert loaded_document is not None
    assert loaded_document.exact_quote == ""
    assert loaded_document.document_title == ""
    assert loaded_document.company == ""
    assert loaded_chunk is not None
    assert loaded_chunk.exact_quote == ""
    assert loaded_chunk.document_title == ""
    assert loaded_chunk.company == ""
