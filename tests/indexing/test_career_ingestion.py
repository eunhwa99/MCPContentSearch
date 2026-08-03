from __future__ import annotations

import asyncio

import pytest

from indexing import ingestion_service as ingestion_module
from core.exceptions import ParsingError
from core.models import (
    DocumentModel,
    EvidenceSourceType,
    ExperienceType,
    SourceModel,
    SourceType,
    SyncJobStatus,
    SyncStatus,
)
from core.utils import ContentHasher
from fetching.connectors import SourceConnector, SourceRegistry
from indexing.chunker import DocumentChunker
from indexing.ingestion_service import IngestionService
from parsing.career_documents import CareerDocumentParser
from storage.metadata_store import MetadataStore


class CareerSnapshotConnector(SourceConnector):
    supports_stale_cleanup = True
    source = SourceModel(
        source_id="source_career",
        source_type=SourceType.CAREER,
        name="Synthetic career files",
        enabled=True,
        sync_status=SyncStatus.IDLE,
    )

    def __init__(self, documents=None, error: Exception | None = None):
        self.documents = list(documents or [])
        self.error = error

    async def fetch_documents(self):
        if self.error is not None:
            raise self.error
        return list(self.documents)


class DeduplicatingRecordingIndexer:
    """Small deterministic vector fake keyed like managed chunk documents."""

    def __init__(self):
        self.vectors: dict[tuple[str, str], str] = {}
        self.documents: dict[tuple[str, str], DocumentModel] = {}
        self.calls: list[dict[str, list[str]]] = []
        self.metadata_update_calls: list[list[str]] = []
        self.metadata_rollback_calls = 0
        self.deleted_ids: list[str] = []

    async def index_documents(self, documents):
        generated: list[str] = []
        reused: list[str] = []
        for document in documents:
            key = (document.source_id, document.id)
            content_hash = ContentHasher.hash_content(document.content)
            if self.vectors.get(key) == content_hash:
                reused.append(document.id)
                continue
            self.vectors[key] = content_hash
            self.documents[key] = document
            generated.append(document.id)
        self.calls.append({"generated": generated, "reused": reused})
        await asyncio.sleep(0.001)
        return {
            "embeddings_generated": len(generated),
            "embeddings_reused": len(reused),
        }

    async def update_documents_metadata(self, documents):
        previous = {
            (document.source_id, document.id): self.documents.get(
                (document.source_id, document.id)
            )
            for document in documents
        }
        updated_ids = []
        for document in documents:
            key = (document.source_id, document.id)
            if key not in self.vectors:
                continue
            self.documents[key] = document
            updated_ids.append(document.id)
        self.metadata_update_calls.append(updated_ids)
        return {
            "embeddings_generated": 0,
            "embeddings_reused": len(updated_ids),
            "metadata_rollback": previous,
        }

    async def rollback_documents_metadata(self, rollback):
        self.metadata_rollback_calls += 1
        for key, document in rollback.items():
            if document is None:
                self.documents.pop(key, None)
            else:
                self.documents[key] = document

    async def delete_documents_by_ids(self, document_ids, source_id=""):
        for document_id in document_ids:
            self.deleted_ids.append(document_id)
            self.vectors.pop((source_id, document_id), None)
            self.documents.pop((source_id, document_id), None)


class ControlledOutcomeIndexer(DeduplicatingRecordingIndexer):
    def __init__(self, outcome: str):
        super().__init__()
        self.outcome = outcome

    async def index_documents(self, documents):
        if self.outcome == "stop":
            raise ingestion_module._StopRequested
        if self.outcome == "cancel":
            raise asyncio.CancelledError
        if self.outcome == "failure":
            raise RuntimeError("controlled indexing failure")
        return await super().index_documents(documents)


class FailingOnceDeleteIndexer(DeduplicatingRecordingIndexer):
    def __init__(self):
        super().__init__()
        self.delete_attempts = 0

    async def delete_documents_by_ids(self, document_ids, source_id=""):
        self.delete_attempts += 1
        if self.delete_attempts == 1:
            raise RuntimeError("injected vector cleanup failure")
        await super().delete_documents_by_ids(document_ids, source_id=source_id)


class SimulatedMetadataRefreshInterruption(BaseException):
    pass


class HardCrashOnceAfterVectorWriteIndexer(DeduplicatingRecordingIndexer):
    def __init__(self):
        super().__init__()
        self.crash_on_next_write = True

    async def index_documents(self, documents):
        result = await super().index_documents(documents)
        if self.crash_on_next_write:
            self.crash_on_next_write = False
            raise SimulatedMetadataRefreshInterruption(
                "simulated interruption after vector write"
            )
        return result


def _document(
    document_id: str,
    *,
    content: str,
    file_name: str,
    source_type: EvidenceSourceType,
    experience_type: ExperienceType,
) -> DocumentModel:
    content_hash = ContentHasher.hash_content(content)
    return DocumentModel(
        id=document_id,
        document_id=document_id,
        external_id=document_id,
        title=file_name.rsplit(".", 1)[0],
        document_title=file_name.rsplit(".", 1)[0],
        content=content,
        url=f"career://{file_name}",
        canonical_url=f"career://{file_name}",
        platform="career",
        source_id="source_career",
        path=file_name,
        file_name=file_name,
        evidence_source_type=source_type,
        experience_type=experience_type,
        document_version_id=f"version:{content_hash}",
        content_hash=content_hash,
    )


def _service(tmp_path, connector, indexer):
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=indexer,
    )
    return store, service


@pytest.mark.integration
def test_incremental_ingestion_skips_unchanged_reuses_embeddings_and_isolates_updates(
    tmp_path,
):
    resume = _document(
        "career:resume.md",
        file_name="resume.md",
        source_type=EvidenceSourceType.RESUME,
        experience_type=ExperienceType.PROFESSIONAL,
        content=(
            "# Experience\nBuilt a reliable queue.\n"
            "# Skills\nPython and Kubernetes.\n"
        ),
    )
    inventory = _document(
        "career:skills.txt",
        file_name="skills.txt",
        source_type=EvidenceSourceType.SKILLS_INVENTORY,
        experience_type=ExperienceType.UNKNOWN,
        content="Python, SQLite, and Kubernetes.",
    )
    connector = CareerSnapshotConnector([resume, inventory])
    indexer = DeduplicatingRecordingIndexer()
    store, service = _service(tmp_path, connector, indexer)

    first = asyncio.run(service.sync_source("source_career"))
    first_resume_chunks = store.list_chunks_for_document(resume.document_id)
    inventory_chunk_id = store.list_chunks_for_document(inventory.document_id)[0].chunk_id
    unchanged = asyncio.run(service.sync_source("source_career"))

    updated_resume = resume.model_copy(
        update={
            "content": (
                "# Experience\nBuilt a reliable queue.\n"
                "# Skills\nPython, Kubernetes, and SQLite.\n"
            ),
            "content_hash": ContentHasher.hash_content(
                "# Experience\nBuilt a reliable queue.\n"
                "# Skills\nPython, Kubernetes, and SQLite.\n"
            ),
            "document_version_id": "version:resume-v2",
        }
    )
    connector.documents = [updated_resume, inventory]
    updated = asyncio.run(service.sync_source("source_career"))
    updated_resume_chunks = store.list_chunks_for_document(resume.document_id)

    assert first.status == SyncJobStatus.SUCCEEDED
    assert first.parsed_documents == 2
    assert first.created_chunks == 3
    assert first.updated_chunks == 0
    assert first.skipped_chunks == 0
    assert first.embeddings_generated == 3
    assert first.embeddings_reused == 0
    assert first.parsing_failures == 0
    assert first.indexing_latency_ms > 0

    assert unchanged.status == SyncJobStatus.SUCCEEDED
    assert unchanged.skipped_documents == 2
    assert unchanged.created_chunks == 0
    assert unchanged.updated_chunks == 0
    assert unchanged.skipped_chunks == 3
    assert unchanged.embeddings_generated == 0
    assert unchanged.embeddings_reused == 3

    assert updated.status == SyncJobStatus.SUCCEEDED
    assert updated.processed_documents == 1
    assert updated.updated_documents == 1
    assert updated.skipped_documents == 1
    assert updated.created_chunks == 0
    assert updated.updated_chunks == 2
    assert updated.skipped_chunks == 1
    assert updated.embeddings_generated == 1
    assert updated.embeddings_reused == 2
    assert updated.parsing_failures == 0
    assert updated.indexing_latency_ms > 0

    assert updated_resume_chunks[0].chunk_id == first_resume_chunks[0].chunk_id
    assert updated_resume_chunks[1].chunk_id != first_resume_chunks[1].chunk_id
    assert indexer.deleted_ids == [first_resume_chunks[1].chunk_id]
    generated_ids = [
        document_id
        for call in indexer.calls
        for document_id in call["generated"]
    ]
    assert generated_ids.count(updated_resume_chunks[0].chunk_id) == 1
    assert generated_ids.count(inventory_chunk_id) == 1
    assert generated_ids.count(updated_resume_chunks[1].chunk_id) == 1


@pytest.mark.integration
def test_taxonomy_only_update_refreshes_vector_metadata_without_new_embeddings(tmp_path):
    original = _document(
        "career:taxonomy.md",
        file_name="taxonomy.md",
        source_type=EvidenceSourceType.RESUME,
        experience_type=ExperienceType.PROFESSIONAL,
        content="# Experience\nBuilt a reliable queue.\n",
    )
    connector = CareerSnapshotConnector([original])
    indexer = DeduplicatingRecordingIndexer()
    store, service = _service(tmp_path, connector, indexer)

    first = asyncio.run(service.sync_source("source_career"))
    chunks = store.list_chunks_for_document(original.document_id)
    connector.documents = [
        original.model_copy(
            update={
                "evidence_source_type": EvidenceSourceType.CAREER_NOTE,
                "experience_type": ExperienceType.ACADEMIC,
            }
        )
    ]

    updated = asyncio.run(service.sync_source("source_career"))

    assert first.embeddings_generated == len(chunks)
    assert updated.processed_documents == 1
    assert updated.updated_documents == 1
    assert updated.skipped_documents == 0
    assert updated.updated_chunks == len(chunks)
    assert updated.skipped_chunks == 0
    assert updated.embeddings_generated == 0
    assert updated.embeddings_reused == len(chunks)
    assert indexer.metadata_update_calls == [[chunk.chunk_id for chunk in chunks]]
    for chunk in store.list_chunks_for_document(original.document_id):
        indexed = indexer.documents[("source_career", chunk.chunk_id)]
        assert indexed.evidence_source_type == EvidenceSourceType.CAREER_NOTE
        assert indexed.experience_type == ExperienceType.ACADEMIC


@pytest.mark.integration
def test_content_stable_career_metadata_update_is_counted_without_reembedding(tmp_path):
    original = _document(
        "career:metadata.md",
        file_name="metadata.md",
        source_type=EvidenceSourceType.CAREER_NOTE,
        experience_type=ExperienceType.PROFESSIONAL,
        content="# Experience\nBuilt a reliable queue.\n",
    ).model_copy(
        update={
            "document_title": "Original title",
            "title": "Original title",
            "company": "Original Co",
            "role": "Engineer",
        }
    )
    connector = CareerSnapshotConnector([original])
    indexer = DeduplicatingRecordingIndexer()
    store, service = _service(tmp_path, connector, indexer)
    first = asyncio.run(service.sync_source("source_career"))
    chunks = store.list_chunks_for_document(original.document_id)
    connector.documents = [
        original.model_copy(
            update={
                "document_title": "Updated title",
                "title": "Updated title",
                "company": "Updated Co",
                "role": "Senior Engineer",
                "project": "Queue migration",
                "start_date": "2024-01",
                "end_date": "2025-06",
            }
        )
    ]

    updated = asyncio.run(service.sync_source("source_career"))
    stored = store.get_document(original.document_id)
    stored_chunks = store.list_chunks_for_document(original.document_id)

    assert first.embeddings_generated == len(chunks)
    assert updated.processed_documents == 1
    assert updated.updated_documents == 1
    assert updated.updated_chunks == len(chunks)
    assert updated.skipped_documents == 0
    assert updated.skipped_chunks == 0
    assert updated.embeddings_generated == 0
    assert updated.embeddings_reused == len(chunks)
    assert indexer.metadata_update_calls == []
    assert stored is not None
    assert stored.document_title == "Updated title"
    assert stored.company == "Updated Co"
    assert stored.role == "Senior Engineer"
    assert stored.project == "Queue migration"
    assert stored.start_date == "2024-01"
    assert stored.end_date == "2025-06"
    assert all(chunk.company == "Updated Co" for chunk in stored_chunks)
    assert all(chunk.role == "Senior Engineer" for chunk in stored_chunks)


@pytest.mark.integration
@pytest.mark.parametrize("commit_outcome", ["failure", "inactive"])
def test_taxonomy_vector_metadata_rolls_back_when_sqlite_does_not_commit(
    monkeypatch,
    tmp_path,
    commit_outcome,
):
    original = _document(
        "career:rollback.md",
        file_name="rollback.md",
        source_type=EvidenceSourceType.RESUME,
        experience_type=ExperienceType.PROFESSIONAL,
        content="# Experience\nBuilt a reliable queue.\n",
    )
    connector = CareerSnapshotConnector([original])
    indexer = DeduplicatingRecordingIndexer()
    store, service = _service(tmp_path, connector, indexer)
    first = asyncio.run(service.sync_source("source_career"))
    chunks = store.list_chunks_for_document(original.document_id)
    connector.documents = [
        original.model_copy(
            update={
                "evidence_source_type": EvidenceSourceType.CAREER_NOTE,
                "experience_type": ExperienceType.ACADEMIC,
            }
        )
    ]

    def fail_commit(job_id, *args, **kwargs):
        del args, kwargs
        if commit_outcome == "failure":
            raise RuntimeError("injected SQLite commit failure")
        return store.complete_failed_sync(
            job_id=job_id,
            source_id="source_career",
            error_message="injected inactive job",
        )

    monkeypatch.setattr(service, "_commit_chunks_or_current", fail_commit)
    failed = asyncio.run(service.sync_source("source_career"))

    assert first.status == SyncJobStatus.SUCCEEDED
    assert failed.status == SyncJobStatus.FAILED
    assert indexer.metadata_rollback_calls == 1
    stored = store.get_document(original.document_id)
    assert stored is not None
    assert stored.evidence_source_type == EvidenceSourceType.RESUME
    assert stored.experience_type == ExperienceType.PROFESSIONAL
    for chunk in chunks:
        indexed = indexer.documents[("source_career", chunk.chunk_id)]
        assert indexed.evidence_source_type == EvidenceSourceType.RESUME
        assert indexed.experience_type == ExperienceType.PROFESSIONAL


@pytest.mark.integration
def test_interrupted_taxonomy_refresh_repairs_vector_metadata_on_unchanged_retry(
    monkeypatch,
    tmp_path,
):
    original = _document(
        "career:interrupted-taxonomy.md",
        file_name="interrupted-taxonomy.md",
        source_type=EvidenceSourceType.RESUME,
        experience_type=ExperienceType.PROFESSIONAL,
        content="# Experience\nBuilt a reliable queue.\n",
    )
    connector = CareerSnapshotConnector([original])
    indexer = DeduplicatingRecordingIndexer()
    store, service = _service(tmp_path, connector, indexer)
    first = asyncio.run(service.sync_source("source_career"))
    chunks = store.list_chunks_for_document(original.document_id)
    changed = original.model_copy(
        update={
            "evidence_source_type": EvidenceSourceType.CAREER_NOTE,
            "experience_type": ExperienceType.ACADEMIC,
        }
    )
    connector.documents = [changed]
    original_commit = service._commit_chunks_or_current

    def interrupt_after_vector_refresh(*args, **kwargs):
        del args, kwargs
        raise SimulatedMetadataRefreshInterruption(
            "simulated interruption before SQLite commit"
        )

    monkeypatch.setattr(
        service,
        "_commit_chunks_or_current",
        interrupt_after_vector_refresh,
    )
    with pytest.raises(SimulatedMetadataRefreshInterruption):
        asyncio.run(service.sync_source("source_career"))

    interrupted = store.get_latest_sync_job("source_career")
    assert interrupted is not None
    assert store.list_pending_vector_metadata_refresh_ids(
        "source_career",
        document_id=original.document_id,
    ) == [chunk.chunk_id for chunk in chunks]
    for chunk in chunks:
        indexed = indexer.documents[("source_career", chunk.chunk_id)]
        assert indexed.evidence_source_type == EvidenceSourceType.CAREER_NOTE
        assert indexed.experience_type == ExperienceType.ACADEMIC
    stored = store.get_document(original.document_id)
    assert stored is not None
    assert stored.evidence_source_type == EvidenceSourceType.RESUME
    assert stored.experience_type == ExperienceType.PROFESSIONAL

    store.complete_failed_sync(
        job_id=interrupted.job_id,
        source_id="source_career",
        error_message="worker interrupted",
    )
    monkeypatch.setattr(service, "_commit_chunks_or_current", original_commit)
    connector.documents = [original]

    recovered = asyncio.run(service.sync_source("source_career"))

    assert first.status == SyncJobStatus.SUCCEEDED
    assert recovered.status == SyncJobStatus.SUCCEEDED
    assert recovered.embeddings_generated == 0
    assert store.list_pending_vector_metadata_refresh_ids(
        "source_career",
        document_id=original.document_id,
    ) == []
    assert indexer.metadata_update_calls == [
        [chunk.chunk_id for chunk in chunks],
        [chunk.chunk_id for chunk in chunks],
    ]
    for chunk in chunks:
        indexed = indexer.documents[("source_career", chunk.chunk_id)]
        assert indexed.evidence_source_type == EvidenceSourceType.RESUME
        assert indexed.experience_type == ExperienceType.PROFESSIONAL


@pytest.mark.integration
def test_typed_parsing_failure_is_counted_and_persisted_without_indexing(tmp_path):
    connector = CareerSnapshotConnector(
        error=ParsingError("Could not parse broken.pdf")
    )
    indexer = DeduplicatingRecordingIndexer()
    store, service = _service(tmp_path, connector, indexer)

    failed = asyncio.run(service.sync_source("source_career"))
    persisted = store.get_sync_job(failed.job_id)

    assert failed.status == SyncJobStatus.FAILED
    assert failed.parsing_failures == 1
    assert failed.indexing_latency_ms == 0.0
    assert persisted is not None
    assert persisted.parsing_failures == 1
    assert persisted.indexing_latency_ms == 0.0
    assert "broken.pdf" in failed.error_message
    assert indexer.calls == []


@pytest.mark.integration
def test_complete_replacement_snapshot_tombstones_old_document_and_deletes_vectors(
    tmp_path,
):
    old = _document(
        "career:resume-v1.pdf",
        file_name="resume-v1.pdf",
        source_type=EvidenceSourceType.PREVIOUS_RESUME,
        experience_type=ExperienceType.PROFESSIONAL,
        content="Previous synthetic resume.",
    )
    replacement = _document(
        "career:resume.pdf",
        file_name="resume.pdf",
        source_type=EvidenceSourceType.RESUME,
        experience_type=ExperienceType.PROFESSIONAL,
        content="Current synthetic resume.",
    )
    connector = CareerSnapshotConnector([old])
    indexer = DeduplicatingRecordingIndexer()
    store, service = _service(tmp_path, connector, indexer)

    first = asyncio.run(service.sync_source("source_career"))
    old_chunk_id = store.list_chunks_for_document(old.document_id)[0].chunk_id
    connector.documents = [replacement]
    second = asyncio.run(service.sync_source("source_career"))

    assert first.status == SyncJobStatus.SUCCEEDED
    assert second.status == SyncJobStatus.SUCCEEDED
    assert store.get_document(old.document_id).deleted_at
    assert store.list_chunks_for_document(old.document_id) == []
    assert store.has_chunk_record(old_chunk_id) is True
    assert old_chunk_id in indexer.deleted_ids
    active_replacement = store.get_document(replacement.document_id)
    assert active_replacement is not None
    assert active_replacement.deleted_at == ""
    assert len(store.list_chunks_for_document(replacement.document_id)) == 1


@pytest.mark.integration
def test_failed_vector_cleanup_retries_from_tombstone_on_unchanged_sync(tmp_path):
    stale = _document(
        "career:stale.md",
        file_name="stale.md",
        source_type=EvidenceSourceType.CAREER_NOTE,
        experience_type=ExperienceType.PROFESSIONAL,
        content="Stale evidence that must leave the vector store.",
    )
    retained = _document(
        "career:retained.md",
        file_name="retained.md",
        source_type=EvidenceSourceType.CAREER_NOTE,
        experience_type=ExperienceType.PROFESSIONAL,
        content="Retained evidence remains searchable.",
    )
    connector = CareerSnapshotConnector([stale, retained])
    indexer = FailingOnceDeleteIndexer()
    store, service = _service(tmp_path, connector, indexer)

    first = asyncio.run(service.sync_source("source_career"))
    stale_chunk_id = store.list_chunks_for_document(stale.document_id)[0].chunk_id
    connector.documents = [retained]
    failed_cleanup = asyncio.run(service.sync_source("source_career"))

    assert first.status == SyncJobStatus.SUCCEEDED
    assert failed_cleanup.status == SyncJobStatus.SUCCEEDED
    assert ("source_career", stale_chunk_id) in indexer.documents
    assert store.list_pending_vector_cleanup_ids("source_career") == [stale_chunk_id]

    retried = asyncio.run(service.sync_source("source_career"))

    assert retried.status == SyncJobStatus.SUCCEEDED
    assert indexer.delete_attempts == 2
    assert ("source_career", stale_chunk_id) not in indexer.documents
    assert store.list_pending_vector_cleanup_ids("source_career") == []


@pytest.mark.integration
def test_reactivated_same_hash_chunk_refreshes_reused_vector_taxonomy(tmp_path):
    original = _document(
        "career:reactivated.md",
        file_name="reactivated.md",
        source_type=EvidenceSourceType.RESUME,
        experience_type=ExperienceType.PROFESSIONAL,
        content="# Experience\nBuilt a reliable queue.\n",
    )
    connector = CareerSnapshotConnector([original])
    indexer = FailingOnceDeleteIndexer()
    store, service = _service(tmp_path, connector, indexer)

    first = asyncio.run(service.sync_source("source_career"))
    first_chunks = store.list_chunks_for_document(original.document_id)
    connector.documents = []
    deleted = asyncio.run(service.sync_source("source_career"))
    changed = original.model_copy(
        update={
            "evidence_source_type": EvidenceSourceType.CAREER_NOTE,
            "experience_type": ExperienceType.ACADEMIC,
        }
    )
    connector.documents = [changed]

    reactivated = asyncio.run(service.sync_source("source_career"))
    reactivated_chunks = store.list_chunks_for_document(original.document_id)

    assert first.status == SyncJobStatus.SUCCEEDED
    assert deleted.status == SyncJobStatus.SUCCEEDED
    assert reactivated.status == SyncJobStatus.SUCCEEDED
    assert [chunk.chunk_id for chunk in reactivated_chunks] == [
        chunk.chunk_id for chunk in first_chunks
    ]
    assert indexer.calls[-1] == {
        "generated": [],
        "reused": [chunk.chunk_id for chunk in first_chunks],
    }
    assert indexer.metadata_update_calls[-1] == [
        chunk.chunk_id for chunk in first_chunks
    ]
    for chunk in reactivated_chunks:
        indexed = indexer.documents[("source_career", chunk.chunk_id)]
        assert indexed.evidence_source_type == EvidenceSourceType.CAREER_NOTE
        assert indexed.experience_type == ExperienceType.ACADEMIC


@pytest.mark.integration
def test_interrupted_orphan_vector_retry_refreshes_changed_taxonomy(tmp_path):
    original = _document(
        "career:orphan-retry.md",
        file_name="orphan-retry.md",
        source_type=EvidenceSourceType.RESUME,
        experience_type=ExperienceType.PROFESSIONAL,
        content="# Experience\nBuilt a reliable queue.\n",
    )
    connector = CareerSnapshotConnector([original])
    indexer = HardCrashOnceAfterVectorWriteIndexer()
    store, service = _service(tmp_path, connector, indexer)

    with pytest.raises(SimulatedMetadataRefreshInterruption):
        asyncio.run(service.sync_source("source_career"))

    interrupted = store.get_latest_sync_job("source_career")
    assert interrupted is not None
    orphan_chunk_id = next(iter(indexer.documents))[1]
    assert store.list_pending_vector_cleanup_ids("source_career") == [
        orphan_chunk_id
    ]
    store.complete_failed_sync(
        job_id=interrupted.job_id,
        source_id="source_career",
        error_message="worker interrupted",
    )
    changed = original.model_copy(
        update={
            "evidence_source_type": EvidenceSourceType.CAREER_NOTE,
            "experience_type": ExperienceType.ACADEMIC,
        }
    )
    connector.documents = [changed]

    recovered = asyncio.run(service.sync_source("source_career"))

    assert recovered.status == SyncJobStatus.SUCCEEDED
    assert indexer.calls[-1] == {"generated": [], "reused": [orphan_chunk_id]}
    assert indexer.metadata_update_calls[-1] == [orphan_chunk_id]
    indexed = indexer.documents[("source_career", orphan_chunk_id)]
    assert indexed.evidence_source_type == EvidenceSourceType.CAREER_NOTE
    assert indexed.experience_type == ExperienceType.ACADEMIC
    assert store.list_pending_vector_cleanup_ids("source_career") == []


@pytest.mark.e2e
def test_markdown_file_parse_to_incremental_index_preserves_evidence_contract(tmp_path):
    career_root = tmp_path / "career"
    career_root.mkdir()
    path = career_root / "resume.md"
    path.write_text(
        "# Experience\n## Example Corp\nBuilt a reliable queue.\n",
        encoding="utf-8",
    )
    document = CareerDocumentParser(root=career_root).parse_file(
        path,
        source_type=EvidenceSourceType.RESUME,
        experience_type=ExperienceType.PROFESSIONAL,
        company="Example Corp",
        role="Backend Engineer",
    )
    connector = CareerSnapshotConnector([document])
    indexer = DeduplicatingRecordingIndexer()
    store, service = _service(tmp_path, connector, indexer)

    first = asyncio.run(service.sync_source("source_career"))
    second = asyncio.run(service.sync_source("source_career"))
    chunks = store.list_chunks_for_document(document.document_id)
    evidence = next(chunk for chunk in chunks if "reliable queue" in chunk.text)

    assert first.status == SyncJobStatus.SUCCEEDED
    assert second.status == SyncJobStatus.SUCCEEDED
    assert second.skipped_documents == 1
    assert len(store.list_documents()["documents"]) == 1
    assert evidence.document_version_id == document.document_version_id
    assert evidence.evidence_source_type == EvidenceSourceType.RESUME
    assert evidence.experience_type == ExperienceType.PROFESSIONAL
    assert evidence.document_title == document.document_title
    assert evidence.section_title == "Example Corp"
    assert evidence.parent_section_title == "Experience"
    assert evidence.exact_quote == evidence.text
    assert evidence.company == "Example Corp"
    assert evidence.role == "Backend Engineer"


@pytest.mark.integration
def test_preceding_career_section_preserves_existing_ids_and_reuses_embeddings(
    tmp_path,
):
    original = _document(
        "career:stable.md",
        file_name="stable.md",
        source_type=EvidenceSourceType.CAREER_NOTE,
        experience_type=ExperienceType.PROFESSIONAL,
        content=(
            "# Experience\nBuilt a reliable queue.\n"
            "# Skills\nPython and SQLite.\n"
        ),
    )
    connector = CareerSnapshotConnector([original])
    indexer = DeduplicatingRecordingIndexer()
    store, service = _service(tmp_path, connector, indexer)

    first = asyncio.run(service.sync_source("source_career"))
    first_chunks = store.list_chunks_for_document(original.document_id)
    first_ids_by_text = {chunk.text: chunk.chunk_id for chunk in first_chunks}
    updated_content = "# Summary\nNew evidence.\n" + original.content
    connector.documents = [
        original.model_copy(
            update={
                "content": updated_content,
                "content_hash": ContentHasher.hash_content(updated_content),
                "document_version_id": "version:stable-v2",
            }
        )
    ]

    updated = asyncio.run(service.sync_source("source_career"))
    updated_chunks = store.list_chunks_for_document(original.document_id)
    updated_ids_by_text = {chunk.text: chunk.chunk_id for chunk in updated_chunks}

    assert first.status == SyncJobStatus.SUCCEEDED
    assert updated.status == SyncJobStatus.SUCCEEDED
    assert len(updated_chunks) == len(first_chunks) + 1
    for text, chunk_id in first_ids_by_text.items():
        assert updated_ids_by_text[text] == chunk_id
    assert updated.embeddings_generated == 1
    assert updated.embeddings_reused == len(first_chunks)
    assert indexer.deleted_ids == []


@pytest.mark.integration
def test_chunk_lifecycle_counters_use_stable_id_sets_without_double_counting(
    tmp_path,
):
    original = _document(
        "career:lifecycle.md",
        file_name="lifecycle.md",
        source_type=EvidenceSourceType.CAREER_NOTE,
        experience_type=ExperienceType.PROFESSIONAL,
        content=(
            "# Repeat\nSame evidence.\n"
            "# Other\nOther evidence.\n"
            "# Repeat\nSame evidence.\n"
        ),
    )
    connector = CareerSnapshotConnector([original])
    indexer = DeduplicatingRecordingIndexer()
    _, service = _service(tmp_path, connector, indexer)

    first = asyncio.run(service.sync_source("source_career"))

    reordered_content = (
        "# Other\nOther evidence.\n"
        "# Repeat\nSame evidence.\n"
        "# Repeat\nSame evidence.\n"
    )
    connector.documents = [
        original.model_copy(
            update={
                "content": reordered_content,
                "content_hash": ContentHasher.hash_content(reordered_content),
                "document_version_id": "version:reordered",
            }
        )
    ]
    reordered = asyncio.run(service.sync_source("source_career"))

    prepended_content = "# New\nNew evidence.\n" + reordered_content
    connector.documents = [
        original.model_copy(
            update={
                "content": prepended_content,
                "content_hash": ContentHasher.hash_content(prepended_content),
                "document_version_id": "version:prepended",
            }
        )
    ]
    prepended = asyncio.run(service.sync_source("source_career"))

    replaced_content = prepended_content.replace(
        "# Other\nOther evidence.\n",
        "# Replacement\nReplacement evidence.\n",
    )
    connector.documents = [
        original.model_copy(
            update={
                "content": replaced_content,
                "content_hash": ContentHasher.hash_content(replaced_content),
                "document_version_id": "version:replaced",
            }
        )
    ]
    replaced = asyncio.run(service.sync_source("source_career"))

    assert first.created_chunks == 3
    assert reordered.created_chunks == 0
    assert reordered.updated_chunks == 3
    assert reordered.skipped_chunks == 0
    assert reordered.embeddings_generated == 0
    assert reordered.embeddings_reused == 3
    assert prepended.created_chunks == 1
    assert prepended.updated_chunks == 3
    assert prepended.skipped_chunks == 0
    assert prepended.embeddings_generated == 1
    assert prepended.embeddings_reused == 3
    assert replaced.created_chunks == 0
    assert replaced.updated_chunks == 4
    assert replaced.skipped_chunks == 0
    assert replaced.embeddings_generated == 1
    assert replaced.embeddings_reused == 3
    for job, expected_chunks in (
        (reordered, 3),
        (prepended, 4),
        (replaced, 4),
    ):
        assert job.created_chunks + job.updated_chunks + job.skipped_chunks == expected_chunks


@pytest.mark.integration
def test_reordered_retained_chunks_refresh_vector_metadata_and_update_counters(
    tmp_path,
):
    original = _document(
        "career:reordered-metadata.md",
        file_name="reordered-metadata.md",
        source_type=EvidenceSourceType.CAREER_NOTE,
        experience_type=ExperienceType.PROFESSIONAL,
        content=(
            "# First\nFirst stable evidence.\n"
            "# Second\nSecond stable evidence.\n"
        ),
    )
    connector = CareerSnapshotConnector([original])
    indexer = DeduplicatingRecordingIndexer()
    store, service = _service(tmp_path, connector, indexer)

    first = asyncio.run(service.sync_source("source_career"))
    first_chunks = store.list_chunks_for_document(original.document_id)
    first_ids = {chunk.text: chunk.chunk_id for chunk in first_chunks}
    reordered_content = (
        "# Second\nSecond stable evidence.\n"
        "# First\nFirst stable evidence.\n"
    )
    connector.documents = [
        original.model_copy(
            update={
                "content": reordered_content,
                "content_hash": ContentHasher.hash_content(reordered_content),
                "document_version_id": "version:reordered-metadata-v2",
            }
        )
    ]

    updated = asyncio.run(service.sync_source("source_career"))
    stored_chunks = store.list_chunks_for_document(original.document_id)

    assert first.status == SyncJobStatus.SUCCEEDED
    assert updated.status == SyncJobStatus.SUCCEEDED
    assert {chunk.text: chunk.chunk_id for chunk in stored_chunks} == first_ids
    assert updated.updated_documents == 1
    assert updated.created_chunks == 0
    assert updated.updated_chunks == 2
    assert updated.skipped_chunks == 0
    assert updated.embeddings_generated == 0
    assert updated.embeddings_reused == 2
    assert indexer.metadata_update_calls == [
        [chunk.chunk_id for chunk in stored_chunks]
    ]
    for chunk in stored_chunks:
        indexed = indexer.documents[("source_career", chunk.chunk_id)]
        assert indexed.chunk_index == chunk.chunk_index
        assert indexed.line_start == chunk.line_start
        assert indexed.line_end == chunk.line_end
        assert indexed.document_version_id == chunk.document_version_id


@pytest.mark.parametrize("outcome", ["stop", "cancel", "failure"])
@pytest.mark.integration
def test_failed_or_cancelled_post_fetch_indexing_persists_elapsed_latency(
    monkeypatch,
    tmp_path,
    outcome,
):
    document = _document(
        f"career:{outcome}.txt",
        file_name=f"{outcome}.txt",
        source_type=EvidenceSourceType.CAREER_NOTE,
        experience_type=ExperienceType.UNKNOWN,
        content="Synthetic evidence.",
    )
    connector = CareerSnapshotConnector([document])
    indexer = ControlledOutcomeIndexer(outcome)
    store, service = _service(tmp_path, connector, indexer)
    clock = iter((100.0, 100.25))
    monkeypatch.setattr(ingestion_module.time, "perf_counter", lambda: next(clock))

    if outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(service.sync_source("source_career"))
        result = store.get_latest_sync_job("source_career")
    else:
        result = asyncio.run(service.sync_source("source_career"))

    assert result is not None
    assert result.status == SyncJobStatus.FAILED
    assert result.indexing_latency_ms == pytest.approx(250.0)
