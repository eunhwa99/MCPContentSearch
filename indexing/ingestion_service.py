import asyncio
import logging
from datetime import datetime, timezone

from core.models import DocumentModel, SyncJobStatus
from core.utils import ContentHasher
from fetching.connectors import SourceRegistry
from indexing.background_tasks import safe_error_message
from indexing.chunker import DocumentChunker
from storage.metadata_store import MetadataStore

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_sensitive_error(message: str) -> str:
    if not message:
        return "Sync failed. See server logs for details."
    return safe_error_message(RuntimeError(message))


def _stale_cleanup_reason_for_connector(connector, fallback_message: str = "") -> str:
    reason = getattr(connector, "stale_cleanup_disabled_reason", "") or getattr(
        connector,
        "disabled_reason",
        "",
    )
    if reason:
        return _redact_sensitive_error(reason)
    return _redact_sensitive_error(fallback_message) if fallback_message else ""


def _bulk_sync_outcome_for_job(job, *, source_enabled: bool) -> str:
    if job.status == SyncJobStatus.SUCCEEDED:
        return "succeeded"
    if job.status == SyncJobStatus.RUNNING:
        return "blocked"
    if not source_enabled and job.status == SyncJobStatus.FAILED:
        return "skipped"
    return "failed"


def _bulk_sync_status_from_results(results: list[dict]) -> str:
    outcomes = {result["sync_outcome"] for result in results}
    if outcomes.issubset({"succeeded", "skipped"}):
        return "completed"
    if outcomes.intersection({"succeeded", "skipped"}):
        return "partial"
    return "failed"


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

    def refresh_registered_sources(self):
        for source in self.source_registry.list_sources():
            self.metadata_store.register_source(source)

    async def sync_all(self, source_ids: list[str] | None = None) -> dict:
        sources = self.source_registry.list_sources()
        if self.register_source_config:
            self.refresh_registered_sources()

        if source_ids is None:
            selected_source_ids = [source.source_id for source in sources]
        else:
            selected_source_ids = source_ids
        selected_source_ids = list(dict.fromkeys(selected_source_ids))
        started_at = _now()

        async def _sync_one(selected_source_id: str) -> dict:
            try:
                connector = self.source_registry.get_connector(selected_source_id)
                job = await self.sync_source(selected_source_id)
            except Exception as exc:
                message = _redact_sensitive_error(str(exc))
                logger.error("Bulk sync failed for source %s: %s", selected_source_id, message)
                return {
                    "source_id": selected_source_id,
                    "sync_outcome": "failed",
                    "job": None,
                    "message": message,
                }

            outcome = _bulk_sync_outcome_for_job(job, source_enabled=connector.source.enabled)
            return {
                "source_id": selected_source_id,
                "sync_outcome": outcome,
                "job": job,
                "message": "",
            }

        results = await asyncio.gather(*(_sync_one(source_id) for source_id in selected_source_ids))
        finished_at = _now()
        summary = {
            "total_sources": len(results),
            "succeeded": sum(1 for result in results if result["sync_outcome"] == "succeeded"),
            "failed": sum(1 for result in results if result["sync_outcome"] == "failed"),
            "blocked": sum(1 for result in results if result["sync_outcome"] == "blocked"),
            "skipped": sum(1 for result in results if result["sync_outcome"] == "skipped"),
            "started_at": started_at,
            "finished_at": finished_at,
        }
        return {
            "status": _bulk_sync_status_from_results(results),
            "summary": summary,
            "results": results,
        }

    async def sync_source(self, source_id: str):
        connector = self.source_registry.get_connector(source_id)
        if self.register_source_config:
            self.refresh_registered_sources()
        else:
            connector.refresh_source_state()
        job, started = self.metadata_store.begin_sync_job(source_id)
        if not started:
            logger.info("Sync already running for source %s", source_id)
            return job
        if not connector.source.enabled:
            message = _redact_sensitive_error(
                getattr(connector, "disabled_reason", "") or f"Source {source_id} is disabled"
            )
            return self.metadata_store.complete_failed_sync(
                job_id=job.job_id,
                source_id=source_id,
                error_message=message,
                stale_cleanup_disabled_reason=_stale_cleanup_reason_for_connector(
                    connector,
                    message,
                ),
            )

        try:
            inactive_job = self._refresh_running_job_or_current(job.job_id)
            if inactive_job:
                return inactive_job
            documents = await connector.fetch_documents()
            inactive_job = self._refresh_running_job_or_current(job.job_id)
            if inactive_job:
                return inactive_job
            cleanup_missing_documents = getattr(connector, "supports_stale_cleanup", False)
            processed = 0
            skipped = 0
            indexed_chunks = 0
            total_documents = len(documents)
            self._record_sync_progress(
                job.job_id,
                total_documents=total_documents,
                processed_documents=processed,
                indexed_chunks=indexed_chunks,
                skipped_documents=skipped,
            )
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
                content_hash = normalized.content_hash or ContentHasher.hash_content(
                    normalized.content
                )
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
                        self._record_sync_progress(
                            job.job_id,
                            total_documents=total_documents,
                            processed_documents=processed,
                            indexed_chunks=indexed_chunks,
                            skipped_documents=skipped,
                        )
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
                        await self._delete_vectors_best_effort(uncommitted_vector_ids, source_id)
                        uncommitted_vector_ids = []
                        return inactive_job
                    uncommitted_vector_ids = []
                    await self._delete_vectors_best_effort(stale_chunk_ids, source_id)
                    processed += 1
                    indexed_chunks += len(chunks)
                    self._record_sync_progress(
                        job.job_id,
                        total_documents=total_documents,
                        processed_documents=processed,
                        indexed_chunks=indexed_chunks,
                        skipped_documents=skipped,
                    )
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
                    await self._delete_vectors_best_effort(uncommitted_vector_ids, source_id)
                    uncommitted_vector_ids = []
                    return inactive_job
                uncommitted_vector_ids = []
                await self._delete_vectors_best_effort(stale_chunk_ids, source_id)
                processed += 1
                indexed_chunks += len(chunks)
                self._record_sync_progress(
                    job.job_id,
                    total_documents=total_documents,
                    processed_documents=processed,
                    indexed_chunks=indexed_chunks,
                    skipped_documents=skipped,
                )

            finished, deleted_chunk_ids = self.metadata_store.complete_successful_sync(
                job_id=job.job_id,
                source_id=source_id,
                total_documents=total_documents,
                processed_documents=processed,
                indexed_chunks=indexed_chunks,
                skipped_documents=skipped,
                last_seen_at=last_seen_at,
                last_seen_sync_id=last_seen_sync_id,
                cleanup_missing_documents=cleanup_missing_documents,
                cleanup_document_id_prefixes=getattr(
                    connector,
                    "cleanup_document_id_prefixes",
                    (),
                ),
                deleted_at=_now(),
                stale_cleanup_disabled_reason=_stale_cleanup_reason_for_connector(connector),
            )
            await self._delete_vectors_best_effort(deleted_chunk_ids, source_id)
            return finished

        except Exception as exc:
            error_message = _redact_sensitive_error(str(exc))
            logger.error("Sync failed for source %s: %s", source_id, error_message)
            if "uncommitted_vector_ids" in locals():
                await self._delete_vectors_best_effort(uncommitted_vector_ids, source_id)
            return self.metadata_store.complete_failed_sync(
                job_id=job.job_id,
                source_id=source_id,
                error_message=error_message,
                stale_cleanup_disabled_reason=(
                    _stale_cleanup_reason_for_connector(connector, error_message)
                    if not getattr(connector, "supports_stale_cleanup", False)
                    or not connector.source.enabled
                    else ""
                ),
            )

    def _refresh_running_job_or_current(self, job_id: str):
        current_job = self.metadata_store.touch_sync_job(job_id)
        if not current_job:
            raise ValueError(f"Unknown sync job: {job_id}")
        if current_job.status != SyncJobStatus.RUNNING:
            return current_job
        return None

    def _record_sync_progress(
        self,
        job_id: str,
        *,
        total_documents: int,
        processed_documents: int,
        indexed_chunks: int,
        skipped_documents: int,
    ):
        try:
            self.metadata_store.update_sync_job(
                job_id,
                total_documents=total_documents,
                processed_documents=processed_documents,
                indexed_chunks=indexed_chunks,
                skipped_documents=skipped_documents,
            )
        except Exception as exc:
            logger.debug(
                "Unable to update sync progress for job %s: %s",
                job_id,
                _redact_sensitive_error(str(exc)),
            )

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

    async def _delete_vectors_best_effort(self, chunk_ids: list[str], source_id: str):
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
            delete_result = self.indexer.delete_documents_by_ids(
                deletable_chunk_ids,
                source_id=source_id,
            )
            if asyncio.iscoroutine(delete_result):
                await delete_result
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
