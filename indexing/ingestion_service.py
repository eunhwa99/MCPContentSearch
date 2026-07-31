import asyncio
import inspect
import logging
from datetime import datetime, timezone
from typing import Any

from core.error_sanitizer import sanitize_error_text
from core.models import DocumentModel, SyncJobStatus
from core.utils import ContentHasher
from fetching.connectors import SourceRegistry
from fetching.notion import _StopRequested
from indexing.chunker import DocumentChunker
from storage.metadata_store import MetadataStore

logger = logging.getLogger(__name__)

CANCELLED_SYNC_ERROR = "Sync request was cancelled before completion."
WORKER_STOPPED_SYNC_ERROR = (
    "Sync worker stopped before completion; restart the worker and start sync again."
)
FETCHING_PAGE_CONTENT_PHASE = "fetching_page_content"
INDEXING_DOCUMENTS_PHASE = "indexing_documents"
FETCH_PROGRESS_STOP_SIGNAL = object()
OBSERVER_CANCELLED_SYNC_ERROR = "Sync request was cancelled by a progress observer before completion."


class _InactiveJobStop(Exception):
    def __init__(self, job):
        super().__init__("Sync job is no longer active")
        self.job = job


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_sensitive_error(message: str) -> str:
    if not message:
        return "Sync failed. See server logs for details."
    return sanitize_error_text(message)


def _stale_cleanup_reason_for_connector(connector, fallback_message: str = "") -> str:
    reason = getattr(connector, "stale_cleanup_disabled_reason", "") or getattr(
        connector,
        "disabled_reason",
        "",
    )
    if reason:
        return _redact_sensitive_error(reason)
    return _redact_sensitive_error(fallback_message) if fallback_message else ""


def _bulk_launch_status_from_results(results: list[dict]) -> str:
    outcomes = {result["launch_outcome"] for result in results}
    if not outcomes or outcomes.issubset({"started", "already_running", "skipped"}):
        return "accepted"
    if outcomes == {"failed"}:
        return "failed"
    return "partial"


def _indexing_status_message(processed_documents: int, total_documents: int) -> str:
    if total_documents <= 0:
        return "Preparing indexing work."
    return f"Indexing documents {processed_documents}/{total_documents}."


def _int_progress_value(value, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _float_progress_value(value, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return default


def _is_replayable_background_failure(job) -> bool:
    if job is None:
        return False
    return (
        getattr(job, "status", None) == SyncJobStatus.FAILED
        and getattr(job, "error_message", "")
        == OBSERVER_CANCELLED_SYNC_ERROR
    )


class IngestionService:
    """Per-source incremental sync orchestration."""

    def __init__(
        self,
        metadata_store: MetadataStore,
        source_registry: SourceRegistry,
        chunker: DocumentChunker,
        indexer,
        register_source_config: bool = True,
        durable_dispatch: bool = False,
    ):
        self.metadata_store = metadata_store
        self.source_registry = source_registry
        self.chunker = chunker
        self.indexer = indexer
        self._background_sync_tasks: dict[str, asyncio.Task] = {}
        self._recent_terminal_background_jobs: dict[str, Any] = {}
        self.register_source_config = register_source_config
        self.durable_dispatch = durable_dispatch
        self.metadata_store.ensure_schema()
        if self.register_source_config:
            for source in self.source_registry.list_sources():
                self.metadata_store.register_source(source)

    def refresh_registered_sources(self):
        for source in self.source_registry.list_sources():
            self.metadata_store.register_source(source)

    async def sync_all(self, source_ids: list[str] | None = None) -> dict:
        if self.durable_dispatch:
            return await self.enqueue_all(source_ids=source_ids)
        sources = self.source_registry.list_sources()
        if self.register_source_config:
            self.refresh_registered_sources()

        if source_ids is None:
            selected_source_ids = [source.source_id for source in sources]
        else:
            selected_source_ids = source_ids
        selected_source_ids = list(dict.fromkeys(selected_source_ids))
        requested_at = _now()

        async def _launch_one(selected_source_id: str) -> dict:
            try:
                job, launch_outcome = await self._start_sync_source_with_outcome(
                    selected_source_id
                )
            except Exception as exc:
                message = _redact_sensitive_error(str(exc))
                logger.error(
                    "Bulk sync launch failed for source %s: %s",
                    selected_source_id,
                    message,
                )
                return {
                    "source_id": selected_source_id,
                    "launch_outcome": "failed",
                    "job": None,
                    "message": message,
                }

            return {
                "source_id": selected_source_id,
                "launch_outcome": launch_outcome,
                "job": job,
                "message": "",
            }

        results = await asyncio.gather(
            *(_launch_one(source_id) for source_id in selected_source_ids)
        )
        summary = {
            "total_sources": len(results),
            "started": sum(
                1 for result in results if result["launch_outcome"] == "started"
            ),
            "already_running": sum(
                1
                for result in results
                if result["launch_outcome"] == "already_running"
            ),
            "skipped": sum(
                1 for result in results if result["launch_outcome"] == "skipped"
            ),
            "failed": sum(
                1 for result in results if result["launch_outcome"] == "failed"
            ),
            "requested_at": requested_at,
        }
        return {
            "status": _bulk_launch_status_from_results(results),
            "summary": summary,
            "results": results,
        }

    async def enqueue_all(self, source_ids: list[str] | None = None) -> dict:
        """Durably enqueue selected sources without owning their execution."""
        sources = self.source_registry.list_sources()
        if self.register_source_config:
            self.refresh_registered_sources()
        selected_source_ids = (
            [source.source_id for source in sources]
            if source_ids is None
            else list(source_ids)
        )
        selected_source_ids = list(dict.fromkeys(selected_source_ids))
        requested_at = _now()
        results = []
        for selected_source_id in selected_source_ids:
            try:
                job, launch_outcome = await self._enqueue_sync_source_with_outcome(
                    selected_source_id
                )
                results.append(
                    {
                        "source_id": selected_source_id,
                        "launch_outcome": launch_outcome,
                        "job": job,
                        "message": "",
                    }
                )
            except Exception as exc:
                message = _redact_sensitive_error(str(exc))
                logger.error(
                    "Bulk sync enqueue failed for source %s: %s",
                    selected_source_id,
                    message,
                )
                results.append(
                    {
                        "source_id": selected_source_id,
                        "launch_outcome": "failed",
                        "job": None,
                        "message": message,
                    }
                )
        summary = {
            "total_sources": len(results),
            "started": sum(
                1 for result in results if result["launch_outcome"] == "started"
            ),
            "already_running": sum(
                1
                for result in results
                if result["launch_outcome"] == "already_running"
            ),
            "skipped": sum(
                1 for result in results if result["launch_outcome"] == "skipped"
            ),
            "failed": sum(
                1 for result in results if result["launch_outcome"] == "failed"
            ),
            "requested_at": requested_at,
        }
        return {
            "status": _bulk_launch_status_from_results(results),
            "summary": summary,
            "results": results,
        }

    async def sync_source(self, source_id: str):
        self._reconcile_finished_background_task(source_id)
        await self._await_finished_background_handoff(source_id)
        recent_terminal_job = self._recent_terminal_background_jobs.pop(source_id, None)
        if recent_terminal_job is not None:
            latest_job = self.metadata_store.get_latest_sync_job(source_id)
            if (
                latest_job is not None
                and latest_job.job_id == recent_terminal_job.job_id
                and latest_job.error_message == OBSERVER_CANCELLED_SYNC_ERROR
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
        if self.durable_dispatch:
            return await self.enqueue_sync_source(source_id)
        job, _ = await self._start_sync_source_with_outcome(source_id)
        return job

    async def _start_sync_source_with_outcome(self, source_id: str):
        self._reconcile_finished_background_task(source_id)
        await self._await_finished_background_handoff(source_id)
        recent_terminal_job = self._recent_terminal_background_jobs.pop(source_id, None)
        if recent_terminal_job is not None:
            latest_job = self.metadata_store.get_latest_sync_job(source_id)
            if (
                latest_job is not None
                and latest_job.job_id == recent_terminal_job.job_id
                and latest_job.error_message == OBSERVER_CANCELLED_SYNC_ERROR
            ):
                return latest_job, "failed"
        connector, job, should_run = self._begin_sync_source(source_id)
        if not should_run:
            if job.status == SyncJobStatus.RUNNING:
                return job, "already_running"
            if not connector.source.enabled:
                return job, "skipped"
            return job, "failed"
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
            failed_job = self.metadata_store.complete_failed_sync(
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
            return failed_job, "failed"
        self._background_sync_tasks[source_id] = task
        setattr(task, "contextwiki_job_id", job.job_id)
        setattr(task, "contextwiki_connector", connector)

        def _finalize(completed_task: asyncio.Task) -> None:
            self._finalize_background_sync_task(
                source_id,
                job.job_id,
                connector,
                completed_task,
            )

        task.add_done_callback(_finalize)
        return job, "started"

    async def enqueue_sync_source(self, source_id: str):
        job, _ = await self._enqueue_sync_source_with_outcome(source_id)
        return job

    async def _enqueue_sync_source_with_outcome(self, source_id: str):
        connector = self.source_registry.get_connector(source_id)
        if self.register_source_config:
            self.refresh_registered_sources()
        else:
            connector.refresh_source_state()
        disabled_message = ""
        disabled_stale_cleanup_reason = ""
        if not connector.source.enabled:
            disabled_message = _redact_sensitive_error(
                getattr(connector, "disabled_reason", "")
                or f"Source {source_id} is disabled"
            )
            disabled_stale_cleanup_reason = _stale_cleanup_reason_for_connector(
                connector,
                disabled_message,
            )
        job, created = self.metadata_store.enqueue_sync_job(
            source_id,
            disabled_error_message=disabled_message,
            disabled_stale_cleanup_reason=disabled_stale_cleanup_reason,
        )
        if not created:
            return job, "already_running"
        if job.status == SyncJobStatus.FAILED:
            return job, "skipped"
        logger.info("Queued durable sync job for source %s", source_id)
        return job, "started"

    async def run_claimed_sync_job(self, job_id: str):
        """Execute exactly one running job claimed by this worker process."""
        job = self.metadata_store.get_owned_running_sync_job(job_id)
        if job is None:
            raise ValueError(f"Sync job is not claimed by this worker: {job_id}")
        connector = self.source_registry.get_connector(job.source_id)
        connector.refresh_source_state()
        if not connector.source.enabled:
            message = _redact_sensitive_error(
                getattr(connector, "disabled_reason", "")
                or f"Source {job.source_id} is disabled"
            )
            return self.metadata_store.complete_failed_sync(
                job_id=job.job_id,
                source_id=job.source_id,
                error_message=message,
                stale_cleanup_disabled_reason=_stale_cleanup_reason_for_connector(
                    connector,
                    message,
                ),
            )
        return await self._run_sync_source_job(
            job.job_id,
            job.source_id,
            connector,
            cancellation_error=WORKER_STOPPED_SYNC_ERROR,
        )

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

    async def _run_sync_source_job(
        self,
        job_id: str,
        source_id: str,
        connector,
        *,
        cancellation_error: str = CANCELLED_SYNC_ERROR,
    ):
        job = None
        observer_stop_requested = False
        previous_progress_callback = getattr(connector, "progress_callback", None)
        progress_callback_attached = hasattr(connector, "progress_callback")
        previous_progress_stop_signal = getattr(connector, "progress_stop_signal", None)
        progress_stop_signal_attached = hasattr(connector, "progress_stop_signal")
        previous_progress_stop_checker = getattr(connector, "progress_stop_checker", None)
        progress_stop_checker_attached = hasattr(connector, "progress_stop_checker")

        async def _composed_progress_callback(event: dict):
            nonlocal observer_stop_requested
            result = await self._handle_source_fetch_progress(job_id, source_id, event)
            nested_result = None
            if previous_progress_callback is not None:
                try:
                    nested_result = previous_progress_callback(event)
                    if inspect.isawaitable(nested_result):
                        nested_result = await nested_result
                except _StopRequested:
                    observer_stop_requested = True
                    return FETCH_PROGRESS_STOP_SIGNAL
                except Exception as exc:
                    logger.debug(
                        "Ignoring nested progress observer failure for source %s: %s",
                        source_id,
                        _redact_sensitive_error(str(exc)),
                    )
            nested_stop_requested = nested_result is FETCH_PROGRESS_STOP_SIGNAL or (
                previous_progress_stop_signal is not None
                and nested_result is previous_progress_stop_signal
            )
            if nested_stop_requested:
                observer_stop_requested = True
            if result is FETCH_PROGRESS_STOP_SIGNAL or nested_stop_requested:
                return FETCH_PROGRESS_STOP_SIGNAL
            return result

        async def _composed_progress_stop_checker():
            nonlocal observer_stop_requested
            nested_stop_requested = False
            if previous_progress_stop_checker is not None:
                try:
                    nested_result = previous_progress_stop_checker()
                    if inspect.isawaitable(nested_result):
                        nested_result = await nested_result
                    nested_stop_requested = bool(nested_result)
                except _StopRequested:
                    observer_stop_requested = True
                    raise
                except Exception as exc:
                    logger.debug(
                        "Ignoring nested progress stop checker failure for source %s: %s",
                        source_id,
                        _redact_sensitive_error(str(exc)),
                    )
            if nested_stop_requested:
                observer_stop_requested = True
                return True
            current_job = self._refresh_running_job_for_progress(job_id)
            if current_job is not None:
                raise _InactiveJobStop(current_job)
            return False

        if progress_callback_attached:
            connector.progress_callback = _composed_progress_callback
        connector.progress_stop_signal = FETCH_PROGRESS_STOP_SIGNAL
        if progress_stop_checker_attached:
            connector.progress_stop_checker = _composed_progress_stop_checker
        try:
            job = self.metadata_store.get_sync_job(job_id)
            if not job:
                raise ValueError(f"Unknown sync job: {job_id}")
            inactive_job = self._refresh_running_job_or_current(job.job_id)
            if inactive_job:
                return inactive_job
            documents = await connector.fetch_documents()
            if observer_stop_requested:
                raise RuntimeError(OBSERVER_CANCELLED_SYNC_ERROR)
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

        except _InactiveJobStop as stop:
            return stop.job
        except _StopRequested:
            error_message = OBSERVER_CANCELLED_SYNC_ERROR
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
        except asyncio.CancelledError:
            error_message = cancellation_error
            logger.warning("Sync cancelled for source %s", source_id)
            if "uncommitted_vector_ids" in locals():
                await self._delete_vectors_best_effort(uncommitted_vector_ids, source_id)
            self.metadata_store.complete_failed_sync(
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
        finally:
            if progress_callback_attached:
                connector.progress_callback = previous_progress_callback
            if progress_stop_signal_attached:
                connector.progress_stop_signal = previous_progress_stop_signal
            else:
                try:
                    delattr(connector, "progress_stop_signal")
                except AttributeError:
                    pass
            if progress_stop_checker_attached:
                connector.progress_stop_checker = previous_progress_stop_checker

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
        try:
            result = task.result()
            if _is_replayable_background_failure(result):
                self._recent_terminal_background_jobs[source_id] = result
        except asyncio.CancelledError:
            logger.warning("Background sync task cancelled for source %s", source_id)
            self.metadata_store.complete_failed_sync(
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
        except Exception as exc:
            logger.error(
                "Background sync task failed for source %s: %s",
                source_id,
                _redact_sensitive_error(str(exc)),
            )
        finally:
            current_task = self._background_sync_tasks.get(source_id)
            if current_task is task:
                self._background_sync_tasks.pop(source_id, None)

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
        phase: str = INDEXING_DOCUMENTS_PHASE,
        upstream_total_pages: int | None = None,
        upstream_fetched_pages: int | None = None,
        last_progress_at: str | None = None,
        status_message: str | None = None,
    ):
        try:
            current_job = self.metadata_store.get_sync_job(job_id)
            if current_job is None:
                raise ValueError(f"Unknown sync job: {job_id}")
            self.metadata_store.update_sync_job(
                job_id,
                total_documents=total_documents,
                processed_documents=processed_documents,
                indexed_chunks=indexed_chunks,
                skipped_documents=skipped_documents,
                phase=phase,
                upstream_total_pages=(
                    current_job.upstream_total_pages
                    if upstream_total_pages is None
                    else upstream_total_pages
                ),
                upstream_fetched_pages=(
                    current_job.upstream_fetched_pages
                    if upstream_fetched_pages is None
                    else upstream_fetched_pages
                ),
                last_progress_at=last_progress_at or _now(),
                status_message=(
                    status_message
                    if status_message is not None
                    else _indexing_status_message(processed_documents, total_documents)
                ),
            )
        except Exception as exc:
            logger.debug(
                "Unable to update sync progress for job %s: %s",
                job_id,
                _redact_sensitive_error(str(exc)),
            )

    def _update_sync_job_hints_best_effort(self, job_id: str, **updates) -> None:
        try:
            self.metadata_store.update_sync_job(job_id, **updates)
        except Exception as exc:
            logger.debug(
                "Unable to update sync job hints for job %s: %s",
                job_id,
                _redact_sensitive_error(str(exc)),
            )

    def _refresh_running_job_for_progress(self, job_id: str):
        try:
            current_job = self.metadata_store.touch_sync_job(job_id)
            if not current_job:
                raise ValueError(f"Unknown sync job: {job_id}")
            if current_job.status != SyncJobStatus.RUNNING:
                return current_job
            if current_job.phase in {"discovering_pages", FETCHING_PAGE_CONTENT_PHASE}:
                self._update_sync_job_hints_best_effort(
                    job_id,
                    last_progress_at=_now(),
                )
            return None
        except Exception as exc:
            logger.debug(
                "Unable to refresh sync progress heartbeat for job %s: %s",
                job_id,
                _redact_sensitive_error(str(exc)),
            )
            return None

    async def _handle_source_fetch_progress(self, job_id: str, source_id: str, event: dict):
        current_job = self._refresh_running_job_for_progress(job_id)
        if current_job:
            raise _InactiveJobStop(current_job)
        running_job = self.metadata_store.get_sync_job(job_id)

        event_name = str(event.get("event") or "").strip()
        total_pages = _int_progress_value(event.get("total_pages"))
        current_page = _int_progress_value(event.get("current_page"))
        elapsed_seconds = _float_progress_value(event.get("elapsed_seconds"))
        progress_timestamp = _now()
        existing_total = _int_progress_value(
            getattr(running_job, "upstream_total_pages", 0) if running_job else 0
        )
        existing_fetched = _int_progress_value(
            getattr(running_job, "upstream_fetched_pages", 0) if running_job else 0
        )

        if event_name == "search_started":
            self._update_sync_job_hints_best_effort(
                job_id,
                phase="discovering_pages",
                last_progress_at=progress_timestamp,
                status_message="Discovering Notion pages before indexing begins.",
            )
            logger.info("Source %s started upstream page discovery", source_id)
            return None

        if event_name == "search_completed":
            self._update_sync_job_hints_best_effort(
                job_id,
                phase=FETCHING_PAGE_CONTENT_PHASE,
                upstream_total_pages=total_pages,
                upstream_fetched_pages=0,
                last_progress_at=progress_timestamp,
                status_message=(
                    f"Fetching Notion page content 0/{total_pages} before indexing begins."
                    if total_pages
                    else "No Notion pages found to index."
                ),
            )
            logger.info(
                "Source %s discovered %s upstream page(s) before indexing",
                source_id,
                total_pages,
            )
            return None

        if event_name == "search_page_batch_completed":
            discovered = _int_progress_value(event.get("pages_discovered"))
            batch_index = _int_progress_value(event.get("batch_index"))
            has_more = bool(event.get("has_more"))
            self._update_sync_job_hints_best_effort(
                job_id,
                phase="discovering_pages",
                upstream_total_pages=max(discovered, existing_total),
                upstream_fetched_pages=existing_fetched,
                last_progress_at=progress_timestamp,
                status_message=(
                    f"Discovering Notion pages: {discovered} found after batch {batch_index}."
                    + (" More results remain." if has_more else "")
                ),
            )
            logger.info(
                "Source %s discovered %s Notion page(s) after search batch %s",
                source_id,
                discovered,
                batch_index or "?",
            )
            return None

        if event_name == "page_fetch_started":
            self._update_sync_job_hints_best_effort(
                job_id,
                phase=FETCHING_PAGE_CONTENT_PHASE,
                upstream_total_pages=max(total_pages, existing_total),
                upstream_fetched_pages=max(max(current_page - 1, 0), existing_fetched),
                last_progress_at=progress_timestamp,
                status_message=(
                    "Fetching Notion page content "
                    f"{max(current_page - 1, 0)}/{total_pages} completed; "
                    f"now fetching page {current_page}."
                    if total_pages
                    else "Fetching Notion page content before indexing begins."
                ),
            )
            logger.info(
                "Source %s fetching upstream page %s/%s",
                source_id,
                current_page or "?",
                total_pages or "?",
            )
            return None

        if event_name == "page_fetch_completed":
            self._update_sync_job_hints_best_effort(
                job_id,
                phase=FETCHING_PAGE_CONTENT_PHASE,
                upstream_total_pages=max(total_pages, existing_total),
                upstream_fetched_pages=max(current_page, existing_fetched),
                last_progress_at=progress_timestamp,
                status_message=(
                    f"Fetching Notion page content {current_page}/{total_pages} before indexing begins."
                    if total_pages
                    else "Fetching Notion page content before indexing begins."
                ),
            )
            logger.info(
                "Source %s fetched upstream page %s/%s in %.2fs",
                source_id,
                current_page or "?",
                total_pages or "?",
                elapsed_seconds,
            )
            return None

        if event_name == "page_fetch_skipped":
            self._update_sync_job_hints_best_effort(
                job_id,
                phase=FETCHING_PAGE_CONTENT_PHASE,
                upstream_total_pages=max(total_pages, existing_total),
                upstream_fetched_pages=max(current_page, existing_fetched),
                last_progress_at=progress_timestamp,
                status_message=(
                    "Reused stored Notion page content "
                    f"{current_page}/{total_pages} before indexing begins."
                    if total_pages
                    else "Reused stored Notion page content before indexing begins."
                ),
            )
            logger.info(
                "Source %s reused stored content for upstream page %s/%s",
                source_id,
                current_page or "?",
                total_pages or "?",
            )
            return None

        logger.debug(
            "Ignoring unknown fetch progress event for source %s: %s",
            source_id,
            event_name or "<empty>",
        )
        return None

    async def _await_finished_background_handoff(self, source_id: str, attempts: int = 5) -> None:
        for _ in range(attempts):
            existing_task = self._background_sync_tasks.get(source_id)
            if existing_task is None:
                return
            if existing_task.done():
                self._reconcile_finished_background_task(source_id)
                if self._background_sync_tasks.get(source_id) is not existing_task:
                    return
            await asyncio.sleep(0)

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
