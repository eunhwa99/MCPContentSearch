import logging
import re
from datetime import datetime, timezone

from core.models import DocumentModel, SyncJobStatus
from core.utils import ContentHasher
from fetching.connectors import SourceRegistry
from indexing.chunker import DocumentChunker
from storage.metadata_store import MetadataStore

logger = logging.getLogger(__name__)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(access[-_]?key(?:[-_]?id)?|access[-_]?token|api[-_]?key|"
    r"apikey|auth|authorization|aws[-_]?access[-_]?key[-_]?id|"
    r"client[-_]?secret|code|cookie|credential|csrf[-_]?token|csrf|"
    r"j[-_]?session[-_]?id|jwt[-_]?token|jwt|key|pass|password|"
    r"php[-_]?sess[-_]?id|secret|session[-_]?id|session[-_]?token|"
    r"session|sessionid|sig|signature|sid|token|x[-_]?amz[-_]?access[-_]?key[-_]?id|"
    r"x[-_]?amz[-_]?credential|x[-_]?amz[-_]?signature|xsrf[-_]?token|xsrf)"
    r"\s*[:=]\s*([^&,\s]+)"
)
CREDENTIAL_LIKE_RE = re.compile(
    r"(?:"
    r"gh[pousr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AIza[A-Za-z0-9_-]{30,}|"
    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|"
    r"(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"
    r")",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_sensitive_error(message: str) -> str:
    redacted = SENSITIVE_ASSIGNMENT_RE.sub(r"\1=<redacted>", message or "")
    redacted = CREDENTIAL_LIKE_RE.sub("<redacted>", redacted)
    return redacted or "Sync failed. See server logs for details."


class IngestionService:
    """Per-source incremental sync orchestration."""

    def __init__(
        self,
        metadata_store: MetadataStore,
        source_registry: SourceRegistry,
        chunker: DocumentChunker,
        indexer,
        register_source_config: bool = True,
    ):
        self.metadata_store = metadata_store
        self.source_registry = source_registry
        self.chunker = chunker
        self.indexer = indexer
        self.register_source_config = register_source_config
        self.metadata_store.ensure_schema()
        if self.register_source_config:
            for source in self.source_registry.list_sources():
                self.metadata_store.register_source(source)

    async def sync_source(self, source_id: str):
        connector = self.source_registry.get_connector(source_id)
        if self.register_source_config:
            self.metadata_store.register_source(connector.source)
        job, started = self.metadata_store.begin_sync_job(source_id)
        if not started:
            logger.info("Sync already running for source %s", source_id)
            return job
        if not connector.source.enabled:
            message = f"Source {source_id} is disabled"
            return self.metadata_store.complete_failed_sync(
                job_id=job.job_id,
                source_id=source_id,
                error_message=message,
            )

        try:
            inactive_job = self._refresh_running_job_or_current(job.job_id)
            if inactive_job:
                return inactive_job
            documents = await connector.fetch_documents()
            inactive_job = self._refresh_running_job_or_current(job.job_id)
            if inactive_job:
                return inactive_job
            processed = 0
            skipped = 0
            indexed_chunks = 0
            last_seen_at = _now()
            last_seen_sync_id = job.job_id
            uncommitted_vector_ids: list[str] = []

            for document in documents:
                inactive_job = self._refresh_running_job_or_current(job.job_id)
                if inactive_job:
                    return inactive_job
                normalized = self._normalize_document(
                    document,
                    source_id,
                    last_seen_at,
                    last_seen_sync_id,
                )
                document_id = normalized.document_id or normalized.id
                content_hash = ContentHasher.hash_content(normalized.content)
                normalized = normalized.model_copy(update={"content_hash": content_hash})
                chunks = self.chunker.chunk_document(normalized)
                old_chunks = self.metadata_store.list_chunks_for_document(document_id)
                old_chunk_ids = {chunk.chunk_id for chunk in old_chunks}
                new_chunk_ids = {chunk.chunk_id for chunk in chunks}
                stale_chunk_ids = [
                    chunk.chunk_id
                    for chunk in old_chunks
                    if chunk.chunk_id not in new_chunk_ids
                ]
                inactive_job = self._validate_document_before_index(job.job_id, normalized)
                if inactive_job:
                    return inactive_job

                if self.metadata_store.get_document_content_hash(document_id) == content_hash:
                    if old_chunk_ids == new_chunk_ids:
                        inactive_job = self._commit_chunks_or_current(
                            job.job_id,
                            normalized,
                            chunks,
                        )
                        if inactive_job:
                            return inactive_job
                        skipped += 1
                        continue

                    if chunks:
                        uncommitted_vector_ids = [
                            chunk.chunk_id
                            for chunk in chunks
                            if chunk.chunk_id not in old_chunk_ids
                        ]
                        await self.indexer.index_documents(
                            [
                                chunk.to_document_model(platform=normalized.platform)
                                for chunk in chunks
                            ]
                        )
                    inactive_job = self._commit_chunks_or_current(
                        job.job_id,
                        normalized,
                        chunks,
                    )
                    if inactive_job:
                        self._delete_vectors_best_effort(uncommitted_vector_ids, source_id)
                        uncommitted_vector_ids = []
                        return inactive_job
                    uncommitted_vector_ids = []
                    self._delete_vectors_best_effort(stale_chunk_ids, source_id)
                    processed += 1
                    indexed_chunks += len(chunks)
                    continue

                if chunks:
                    uncommitted_vector_ids = [
                        chunk.chunk_id
                        for chunk in chunks
                        if chunk.chunk_id not in old_chunk_ids
                    ]
                    await self.indexer.index_documents(
                        [chunk.to_document_model(platform=normalized.platform) for chunk in chunks]
                    )

                inactive_job = self._commit_chunks_or_current(job.job_id, normalized, chunks)
                if inactive_job:
                    self._delete_vectors_best_effort(uncommitted_vector_ids, source_id)
                    uncommitted_vector_ids = []
                    return inactive_job
                uncommitted_vector_ids = []
                self._delete_vectors_best_effort(stale_chunk_ids, source_id)
                processed += 1
                indexed_chunks += len(chunks)

            finished, deleted_chunk_ids = self.metadata_store.complete_successful_sync(
                job_id=job.job_id,
                source_id=source_id,
                total_documents=len(documents),
                processed_documents=processed,
                indexed_chunks=indexed_chunks,
                skipped_documents=skipped,
                last_seen_at=last_seen_at,
                last_seen_sync_id=last_seen_sync_id,
                cleanup_missing_documents=getattr(connector, "supports_stale_cleanup", False),
                cleanup_document_id_prefixes=getattr(
                    connector,
                    "cleanup_document_id_prefixes",
                    (),
                ),
                deleted_at=_now(),
            )
            self._delete_vectors_best_effort(deleted_chunk_ids, source_id)
            return finished

        except Exception as exc:
            error_message = _redact_sensitive_error(str(exc))
            logger.error("Sync failed for source %s: %s", source_id, error_message)
            if "uncommitted_vector_ids" in locals():
                self._delete_vectors_best_effort(uncommitted_vector_ids, source_id)
            return self.metadata_store.complete_failed_sync(
                job_id=job.job_id,
                source_id=source_id,
                error_message=error_message,
            )

    def _refresh_running_job_or_current(self, job_id: str):
        current_job = self.metadata_store.touch_sync_job(job_id)
        if not current_job:
            raise ValueError(f"Unknown sync job: {job_id}")
        if current_job.status != SyncJobStatus.RUNNING:
            return current_job
        return None

    def _validate_document_before_index(self, job_id: str, document: DocumentModel):
        current_job = self.metadata_store.validate_running_job_document(job_id, document)
        if not current_job:
            raise ValueError(f"Unknown sync job: {job_id}")
        if current_job.status != SyncJobStatus.RUNNING:
            return current_job
        return None

    def _commit_chunks_or_current(self, job_id: str, document: DocumentModel, chunks):
        _, current_job = self.metadata_store.upsert_document_and_replace_chunks_for_running_job(
            job_id,
            document,
            chunks,
        )
        if not current_job:
            raise ValueError(f"Unknown sync job: {job_id}")
        if current_job.status != SyncJobStatus.RUNNING:
            return current_job
        return None

    def _delete_vectors_best_effort(self, chunk_ids: list[str], source_id: str):
        if not chunk_ids or not hasattr(self.indexer, "delete_documents_by_ids"):
            return
        deletable_chunk_ids = [
            chunk_id
            for chunk_id in chunk_ids
            if not self.metadata_store.get_chunk(chunk_id)
        ]
        if not deletable_chunk_ids:
            return
        try:
            self.indexer.delete_documents_by_ids(deletable_chunk_ids, source_id=source_id)
        except Exception as exc:
            logger.error(
                "Vector cleanup failed for source %s: %s",
                source_id,
                _redact_sensitive_error(str(exc)),
            )

    @staticmethod
    def _normalize_document(
        document: DocumentModel,
        source_id: str,
        last_seen_at: str,
        last_seen_sync_id: str = "",
    ) -> DocumentModel:
        document_id = document.external_id or document.document_id or document.id
        return document.model_copy(
            update={
                "source_id": source_id,
                "document_id": document_id,
                "id": document_id,
                "canonical_url": document.canonical_url or document.url,
                "path": document.path or document.title,
                "updated_at": document.updated_at or document.date,
                "last_seen_at": last_seen_at,
                "last_seen_sync_id": last_seen_sync_id,
                "deleted_at": "",
            }
        )
