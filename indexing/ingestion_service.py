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

CANCELLED_SYNC_ERROR = "Sync request was cancelled before completion."


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
        self._background_sync_tasks: dict[str, asyncio.Task] = {}
        self._recent_terminal_background_jobs: dict[str, object] = {}
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
                job = await self._sync_source_internal(
                    selected_source_id,
                    join_existing_background=False,
                )
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
        self._reconcile_finished_background_task(source_id)
        recent_terminal_job = self._recent_terminal_background_jobs.pop(source_id, None)
        if recent_terminal_job is not None:
            latest_job = self.metadata_store.get_latest_sync_job(source_id)
            if (
                latest_job is not None
                and latest_job.job_id == recent_terminal_job.job_id
                and latest_job.status == SyncJobStatus.FAILED
                and latest_job.error_message == CANCELLED_SYNC_ERROR
            ):
                return latest_job
        return await self._sync_source_internal(source_id, join_existing_background=True)

    async def _sync_source_internal(self, source_id: str, *, join_existing_background: bool):
        connector, job, should_run = self._begin_sync_source(source_id)
        if not should_run:
            existing_task = self._background_sync_tasks.get(source_id)
            if (
                join_existing_background
                and job.status == SyncJobStatus.RUNNING
                and existing_task is not None
            ):
                try:
                    return await asyncio.shield(existing_task)
                except asyncio.CancelledError:
                    if existing_task.cancelled() or existing_task.done():
                        for _ in range(5):
                            self._reconcile_finished_background_task(source_id)
                            latest_job = self.metadata_store.get_latest_sync_job(source_id)
                            if latest_job is not None and latest_job.status != SyncJobStatus.RUNNING:
                                return latest_job
                            await asyncio.sleep(0)
                    raise
            return job
        return await self._run_sync_source_job(job.job_id, source_id, connector)

    async def start_sync_source(self, source_id: str):
        self._reconcile_finished_background_task(source_id)
        self._recent_terminal_background_jobs.pop(source_id, None)
        connector, job, should_run = self._begin_sync_source(source_id)
        if not should_run:
            return job
        try:
            task = asyncio.create_task(
                self._run_sync_source_job(job.job_id, source_id, connector),
                name=f"sync-source:{source_id}:{job.job_id}",
            )
        except Exception as exc:
            error_message = _redact_sensitive_error(str(exc))
            logger.error(
                "Unable to start background sync for source %s: %s",
                source_id,
                error_message,
            )
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
        self._background_sync_tasks[source_id] = task
        setattr(task, "contextwiki_job_id", job.job_id)
        setattr(task, "contextwiki_connector", connector)
        task.add_done_callback(
            lambda completed_task, sid=source_id, jid=job.job_id, sync_connector=connector: self._finalize_background_sync_task(
                sid,
                jid,
                sync_connector,
                completed_task,
            )
        )
        return job

    def _begin_sync_source(self, source_id: str):
        connector = self.source_registry.get_connector(source_id)
        if self.register_source_config:
            self.refresh_registered_sources()
        else:
            connector.refresh_source_state()
        job, started = self.metadata_store.begin_sync_job(source_id)
        if not started:
            logger.info("Sync already running for source %s", source_id)
            return connector, job, False
        if not connector.source.enabled:
            message = _redact_sensitive_error(
                getattr(connector, "disabled_reason", "") or f"Source {source_id} is disabled"
            )
            return connector, self.metadata_store.complete_failed_sync(
                job_id=job.job_id,
                source_id=source_id,
                error_message=message,
                stale_cleanup_disabled_reason=_stale_cleanup_reason_for_connector(
                    connector,
                    message,
                ),
            ), False
        return connector, job, True

    async def _run_sync_source_job(self, job_id: str, source_id: str, connector):
        job = None
        try:
            job = self.metadata_store.get_sync_job(job_id)
            if not job:
                raise ValueError(f"Unknown sync job: {job_id}")
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

        except asyncio.CancelledError:
            error_message = CANCELLED_SYNC_ERROR
            logger.warning("Sync cancelled for source %s", source_id)
            if "uncommitted_vector_ids" in locals():
                await self._delete_vectors_best_effort(uncommitted_vector_ids, source_id)
            self.metadata_store.complete_failed_sync(
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
            raise
        except Exception as exc:
            error_message = _redact_sensitive_error(str(exc))
            logger.error("Sync failed for source %s: %s", source_id, error_message)
            if "uncommitted_vector_ids" in locals():
                await self._delete_vectors_best_effort(uncommitted_vector_ids, source_id)
            return self.metadata_store.complete_failed_sync(
                job_id=job.job_id if job is not None else job_id,
                source_id=source_id,
                error_message=error_message,
                stale_cleanup_disabled_reason=(
                    _stale_cleanup_reason_for_connector(connector, error_message)
                    if not getattr(connector, "supports_stale_cleanup", False)
                    or not connector.source.enabled
                    else ""
                ),
            )

    def _reconcile_finished_background_task(self, source_id: str) -> None:
        existing_task = self._background_sync_tasks.get(source_id)
        if existing_task is None or not existing_task.done():
            return
        job_id = getattr(existing_task, "contextwiki_job_id", "")
        connector = getattr(existing_task, "contextwiki_connector", None)
        if not job_id or connector is None:
            self._background_sync_tasks.pop(source_id, None)
            return
        self._finalize_background_sync_task(source_id, job_id, connector, existing_task)

    def _finalize_background_sync_task(
        self,
        source_id: str,
        job_id: str,
        connector,
        task: asyncio.Task,
    ) -> None:
        current_task = self._background_sync_tasks.get(source_id)
        if current_task is task:
            self._background_sync_tasks.pop(source_id, None)
        try:
            task.result()
        except asyncio.CancelledError:
            logger.warning("Background sync task cancelled for source %s", source_id)
            failed_job = self.metadata_store.complete_failed_sync(
                job_id=job_id,
                source_id=source_id,
                error_message=CANCELLED_SYNC_ERROR,
                stale_cleanup_disabled_reason=(
                    _stale_cleanup_reason_for_connector(connector, CANCELLED_SYNC_ERROR)
                    if not getattr(connector, "supports_stale_cleanup", False)
                    or not connector.source.enabled
                    else ""
                ),
            )
            self._recent_terminal_background_jobs[source_id] = failed_job
        except Exception as exc:
            logger.error(
                "Background sync task failed for source %s: %s",
                source_id,
                _redact_sensitive_error(str(exc)),
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
