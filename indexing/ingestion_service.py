import asyncio
import inspect
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from core.error_sanitizer import sanitize_error_text
from core.exceptions import CareerManifestParsingError, ParsingError
from core.models import DocumentModel, SyncJobStatus
from core.utils import ContentHasher
from fetching.connectors import SourceRegistry
from fetching.notion import _StopRequested
from indexing.chunker import DocumentChunker, _ChunkingCancelled
from storage.metadata_store import MetadataStore

logger = logging.getLogger(__name__)

CANCELLED_SYNC_ERROR = "Sync request was cancelled before completion."
WORKER_STOPPED_SYNC_ERROR = (
    "Sync worker stopped before completion; restart the worker and start sync again."
)
FETCHING_PAGE_CONTENT_PHASE = "fetching_page_content"
INDEXING_DOCUMENTS_PHASE = "indexing_documents"
FETCH_PROGRESS_STOP_SIGNAL = object()
OBSERVER_CANCELLED_SYNC_ERROR = (
    "Sync request was cancelled by a progress observer before completion."
)
# Coalesce frequent per-item writes. Hint counters stay sparse; liveness is
# denser so orphan detection and public last_progress_at stay fresh.
_PAGE_FETCH_HINT_PERSIST_INTERVAL = 25
_PAGE_FETCH_LIVENESS_PERSIST_INTERVAL = 5
_DURABLE_STOP_POLL_INTERVAL_SECONDS = 0.5
VECTOR_CLEANUP_PAGE_SIZE = 5_000
VECTOR_CLEANUP_MAX_PAGES_PER_SYNC = 4


class _InactiveJobStop(Exception):
    def __init__(self, job):
        super().__init__("Sync job is no longer active")
        self.job = job


def _should_persist_page_fetch_cadence(
    current_page: int, total_pages: int, interval: int
) -> bool:
    if current_page <= 0:
        return True
    if total_pages > 0 and current_page >= total_pages:
        return True
    return current_page % interval == 0


def _should_persist_page_fetch_hints(current_page: int, total_pages: int) -> bool:
    return _should_persist_page_fetch_cadence(
        current_page,
        total_pages,
        _PAGE_FETCH_HINT_PERSIST_INTERVAL,
    )


def _should_persist_page_fetch_liveness(current_page: int, total_pages: int) -> bool:
    return _should_persist_page_fetch_cadence(
        current_page,
        total_pages,
        _PAGE_FETCH_LIVENESS_PERSIST_INTERVAL,
    )


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
        and getattr(job, "error_message", "") == OBSERVER_CANCELLED_SYNC_ERROR
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

    async def _chunk_document_off_loop(
        self,
        document: DocumentModel,
    ):
        stop_requested = threading.Event()
        chunk_task = asyncio.create_task(
            asyncio.to_thread(
                self.chunker.chunk_document,
                document,
                stop_checker=stop_requested.is_set,
            )
        )
        try:
            return await asyncio.shield(chunk_task)
        except asyncio.CancelledError:
            stop_requested.set()
            try:
                await chunk_task
            except _ChunkingCancelled:
                pass
            raise

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
                1 for result in results if result["launch_outcome"] == "already_running"
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
                1 for result in results if result["launch_outcome"] == "already_running"
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
        await self._await_finished_background_handoff(
            source_id,
            completion_grace_seconds=0.5,
        )
        recent_terminal_job = self._recent_terminal_background_jobs.pop(source_id, None)
        if recent_terminal_job is not None:
            latest_job = self.metadata_store.get_latest_sync_job(source_id)
            if (
                latest_job is not None
                and latest_job.job_id == recent_terminal_job.job_id
                and latest_job.error_message == OBSERVER_CANCELLED_SYNC_ERROR
            ):
                return latest_job
        return await self._sync_source_internal(
            source_id, join_existing_background=True
        )

    async def _sync_source_internal(
        self, source_id: str, *, join_existing_background: bool
    ):
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
                            latest_job = self.metadata_store.get_latest_sync_job(
                                source_id
                            )
                            if (
                                latest_job is not None
                                and latest_job.status != SyncJobStatus.RUNNING
                            ):
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
                getattr(connector, "disabled_reason", "")
                or f"Source {source_id} is disabled"
            )
            return (
                connector,
                self.metadata_store.complete_failed_sync(
                    job_id=job.job_id,
                    source_id=source_id,
                    error_message=message,
                    stale_cleanup_disabled_reason=_stale_cleanup_reason_for_connector(
                        connector,
                        message,
                    ),
                ),
                False,
            )
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
        indexing_started_at: float | None = None
        total_documents = 0
        processed = 0
        indexed_chunks = 0
        skipped = 0
        parsed_documents = 0
        updated_documents = 0
        created_chunks = 0
        updated_chunks = 0
        skipped_chunks = 0
        embeddings_generated = 0
        embeddings_reused = 0
        observer_stop_requested = False
        last_durable_stop_poll_at = float("-inf")
        previous_progress_callback = getattr(connector, "progress_callback", None)
        progress_callback_attached = hasattr(connector, "progress_callback")
        previous_progress_stop_signal = getattr(connector, "progress_stop_signal", None)
        progress_stop_signal_attached = hasattr(connector, "progress_stop_signal")
        previous_progress_stop_checker = getattr(
            connector, "progress_stop_checker", None
        )
        progress_stop_checker_attached = hasattr(connector, "progress_stop_checker")

        def _complete_failed_job(
            error_message: str,
            *,
            parsing_failures: int = 0,
        ):
            indexing_latency_ms = (
                (time.perf_counter() - indexing_started_at) * 1000
                if indexing_started_at is not None
                else 0.0
            )
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
                total_documents=total_documents,
                processed_documents=processed,
                indexed_chunks=indexed_chunks,
                skipped_documents=skipped,
                parsed_documents=parsed_documents,
                updated_documents=updated_documents,
                created_chunks=created_chunks,
                updated_chunks=updated_chunks,
                skipped_chunks=skipped_chunks,
                embeddings_generated=embeddings_generated,
                embeddings_reused=embeddings_reused,
                parsing_failures=parsing_failures,
                indexing_latency_ms=indexing_latency_ms,
            )

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
            nonlocal observer_stop_requested, last_durable_stop_poll_at
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
            if observer_stop_requested:
                return True
            now = time.monotonic()
            if now - last_durable_stop_poll_at < _DURABLE_STOP_POLL_INTERVAL_SECONDS:
                return False
            last_durable_stop_poll_at = now
            current_job = await asyncio.to_thread(
                self._refresh_running_job_for_progress,
                job_id,
            )
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
            indexing_started_at = time.perf_counter()
            cleanup_missing_documents = getattr(
                connector, "supports_stale_cleanup", False
            )
            total_documents = len(documents)
            parsed_documents = total_documents
            self._record_sync_progress(
                job.job_id,
                total_documents=total_documents,
                processed_documents=processed,
                indexed_chunks=indexed_chunks,
                skipped_documents=skipped,
                parsed_documents=parsed_documents,
                updated_documents=updated_documents,
                created_chunks=created_chunks,
                updated_chunks=updated_chunks,
                skipped_chunks=skipped_chunks,
                embeddings_generated=embeddings_generated,
                embeddings_reused=embeddings_reused,
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
                normalized = normalized.model_copy(
                    update={"content_hash": content_hash}
                )
                chunks = await self._chunk_document_off_loop(normalized)
                old_chunks = self.metadata_store.list_chunks_for_document(document_id)
                existing_content_hash = self.metadata_store.get_document_content_hash(
                    document_id
                )
                existing_document = self.metadata_store.get_document(document_id)
                old_chunk_ids = {chunk.chunk_id for chunk in old_chunks}
                new_chunk_ids = {chunk.chunk_id for chunk in chunks}
                reused_chunk_ids = old_chunk_ids & new_chunk_ids
                old_chunks_by_id = {chunk.chunk_id: chunk for chunk in old_chunks}
                retained_metadata_changed_chunks = [
                    chunk
                    for chunk in chunks
                    if chunk.chunk_id in reused_chunk_ids
                    and self._vector_chunk_metadata_changed(
                        old_chunks_by_id[chunk.chunk_id],
                        chunk,
                    )
                ]
                retained_metadata_changed_ids = {
                    chunk.chunk_id for chunk in retained_metadata_changed_chunks
                }
                generated_chunk_ids = new_chunk_ids - old_chunk_ids
                removed_chunk_ids = old_chunk_ids - new_chunk_ids
                lifecycle_updated_chunks = min(
                    len(removed_chunk_ids),
                    len(generated_chunk_ids),
                )
                lifecycle_created_chunks = (
                    len(generated_chunk_ids) - lifecycle_updated_chunks
                )
                stale_chunk_ids = [
                    chunk.chunk_id
                    for chunk in old_chunks
                    if chunk.chunk_id not in new_chunk_ids
                ]
                inactive_job = self._validate_document_before_index(
                    job.job_id, normalized
                )
                if inactive_job:
                    return inactive_job
                if (
                    existing_document is not None
                    and existing_document.evidence_source_type is not None
                ):
                    await self._reconcile_pending_vector_metadata_refresh(
                        source_id=source_id,
                        document_id=document_id,
                        authoritative_chunks=old_chunks,
                        platform=existing_document.platform,
                    )
                index_result = None

                if existing_content_hash == content_hash:
                    if old_chunk_ids == new_chunk_ids:
                        career_metadata_changed = self._career_metadata_changed(
                            existing_document,
                            normalized,
                        )
                        if retained_metadata_changed_chunks or career_metadata_changed:
                            (
                                inactive_job,
                                index_result,
                            ) = await self._refresh_vector_metadata_and_commit(
                                job.job_id,
                                normalized,
                                chunks,
                                retained_metadata_changed_chunks,
                            )
                            if inactive_job:
                                return inactive_job
                            if index_result is None:
                                index_result = {
                                    "embeddings_generated": 0,
                                    "embeddings_reused": len(reused_chunk_ids),
                                }
                            processed += 1
                            indexed_chunks += len(chunks)
                            updated_documents += 1
                            updated_chunks += len(reused_chunk_ids)
                            embeddings_generated += self._index_metric(
                                index_result,
                                "embeddings_generated",
                                0,
                            )
                            embeddings_reused += self._index_metric(
                                index_result,
                                "embeddings_reused",
                                len(reused_chunk_ids),
                            )
                            self._record_sync_progress(
                                job.job_id,
                                total_documents=total_documents,
                                processed_documents=processed,
                                indexed_chunks=indexed_chunks,
                                skipped_documents=skipped,
                                parsed_documents=parsed_documents,
                                updated_documents=updated_documents,
                                created_chunks=created_chunks,
                                updated_chunks=updated_chunks,
                                skipped_chunks=skipped_chunks,
                                embeddings_generated=embeddings_generated,
                                embeddings_reused=embeddings_reused,
                            )
                            continue
                        inactive_job = self._commit_chunks_or_current(
                            job.job_id,
                            normalized,
                            chunks,
                        )
                        if inactive_job:
                            return inactive_job
                        skipped += 1
                        skipped_chunks += len(reused_chunk_ids)
                        embeddings_reused += len(reused_chunk_ids)
                        self._record_sync_progress(
                            job.job_id,
                            total_documents=total_documents,
                            processed_documents=processed,
                            indexed_chunks=indexed_chunks,
                            skipped_documents=skipped,
                            parsed_documents=parsed_documents,
                            updated_documents=updated_documents,
                            created_chunks=created_chunks,
                            updated_chunks=updated_chunks,
                            skipped_chunks=skipped_chunks,
                            embeddings_generated=embeddings_generated,
                            embeddings_reused=embeddings_reused,
                        )
                        continue

                    if chunks:
                        uncommitted_vector_ids = [
                            chunk.chunk_id
                            for chunk in chunks
                            if chunk.chunk_id not in old_chunk_ids
                        ]
                        await self._record_vector_write_intents(
                            uncommitted_vector_ids,
                            source_id=source_id,
                            document_id=document_id,
                            job_id=job.job_id,
                        )
                        index_result = await self.indexer.index_documents(
                            [
                                chunk.to_document_model(platform=normalized.platform)
                                for chunk in chunks
                            ]
                        )
                    vector_metadata_refresh_chunks = (
                        self._vector_metadata_refresh_chunks_after_index(
                            chunks,
                            retained_metadata_changed_chunks,
                            generated_chunk_ids,
                            index_result,
                        )
                    )
                    (
                        inactive_job,
                        _metadata_update_result,
                    ) = await self._refresh_vector_metadata_and_commit(
                        job.job_id,
                        normalized,
                        chunks,
                        vector_metadata_refresh_chunks,
                    )
                    if inactive_job:
                        await self._delete_vectors_best_effort(
                            uncommitted_vector_ids, source_id
                        )
                        uncommitted_vector_ids = []
                        return inactive_job
                    uncommitted_vector_ids = []
                    await self._delete_vectors_best_effort(stale_chunk_ids, source_id)
                    processed += 1
                    indexed_chunks += len(chunks)
                    updated_documents += 1
                    created_chunks += lifecycle_created_chunks
                    updated_chunks += lifecycle_updated_chunks + len(
                        retained_metadata_changed_ids
                    )
                    skipped_chunks += len(
                        reused_chunk_ids - retained_metadata_changed_ids
                    )
                    embeddings_generated += self._index_metric(
                        index_result,
                        "embeddings_generated",
                        len(generated_chunk_ids),
                    )
                    embeddings_reused += self._index_metric(
                        index_result,
                        "embeddings_reused",
                        len(reused_chunk_ids),
                    )
                    self._record_sync_progress(
                        job.job_id,
                        total_documents=total_documents,
                        processed_documents=processed,
                        indexed_chunks=indexed_chunks,
                        skipped_documents=skipped,
                        parsed_documents=parsed_documents,
                        updated_documents=updated_documents,
                        created_chunks=created_chunks,
                        updated_chunks=updated_chunks,
                        skipped_chunks=skipped_chunks,
                        embeddings_generated=embeddings_generated,
                        embeddings_reused=embeddings_reused,
                    )
                    continue

                if chunks:
                    uncommitted_vector_ids = [
                        chunk.chunk_id
                        for chunk in chunks
                        if chunk.chunk_id not in old_chunk_ids
                    ]
                    await self._record_vector_write_intents(
                        uncommitted_vector_ids,
                        source_id=source_id,
                        document_id=document_id,
                        job_id=job.job_id,
                    )
                    index_result = await self.indexer.index_documents(
                        [
                            chunk.to_document_model(platform=normalized.platform)
                            for chunk in chunks
                        ]
                    )
                vector_metadata_refresh_chunks = (
                    self._vector_metadata_refresh_chunks_after_index(
                        chunks,
                        retained_metadata_changed_chunks,
                        generated_chunk_ids,
                        index_result,
                    )
                )
                (
                    inactive_job,
                    _metadata_update_result,
                ) = await self._refresh_vector_metadata_and_commit(
                    job.job_id,
                    normalized,
                    chunks,
                    vector_metadata_refresh_chunks,
                )
                if inactive_job:
                    await self._delete_vectors_best_effort(
                        uncommitted_vector_ids, source_id
                    )
                    uncommitted_vector_ids = []
                    return inactive_job
                uncommitted_vector_ids = []
                await self._delete_vectors_best_effort(stale_chunk_ids, source_id)
                processed += 1
                indexed_chunks += len(chunks)
                if old_chunks:
                    updated_documents += 1
                    created_chunks += lifecycle_created_chunks
                    updated_chunks += lifecycle_updated_chunks + len(
                        retained_metadata_changed_ids
                    )
                else:
                    created_chunks += len(chunks)
                skipped_chunks += len(reused_chunk_ids - retained_metadata_changed_ids)
                embeddings_generated += self._index_metric(
                    index_result,
                    "embeddings_generated",
                    len(generated_chunk_ids),
                )
                embeddings_reused += self._index_metric(
                    index_result,
                    "embeddings_reused",
                    len(reused_chunk_ids),
                )
                self._record_sync_progress(
                    job.job_id,
                    total_documents=total_documents,
                    processed_documents=processed,
                    indexed_chunks=indexed_chunks,
                    skipped_documents=skipped,
                    parsed_documents=parsed_documents,
                    updated_documents=updated_documents,
                    created_chunks=created_chunks,
                    updated_chunks=updated_chunks,
                    skipped_chunks=skipped_chunks,
                    embeddings_generated=embeddings_generated,
                    embeddings_reused=embeddings_reused,
                )

            indexing_latency_ms = (
                (time.perf_counter() - indexing_started_at) * 1000
                if indexing_started_at is not None
                else 0.0
            )
            self._record_sync_progress(
                job.job_id,
                total_documents=total_documents,
                processed_documents=processed,
                indexed_chunks=indexed_chunks,
                skipped_documents=skipped,
                parsed_documents=parsed_documents,
                updated_documents=updated_documents,
                created_chunks=created_chunks,
                updated_chunks=updated_chunks,
                skipped_chunks=skipped_chunks,
                embeddings_generated=embeddings_generated,
                embeddings_reused=embeddings_reused,
                indexing_latency_ms=indexing_latency_ms,
            )
            finished, deleted_chunk_ids = self.metadata_store.complete_successful_sync(
                job_id=job.job_id,
                source_id=source_id,
                total_documents=total_documents,
                processed_documents=processed,
                indexed_chunks=indexed_chunks,
                skipped_documents=skipped,
                parsed_documents=parsed_documents,
                updated_documents=updated_documents,
                created_chunks=created_chunks,
                updated_chunks=updated_chunks,
                skipped_chunks=skipped_chunks,
                embeddings_generated=embeddings_generated,
                embeddings_reused=embeddings_reused,
                parsing_failures=0,
                indexing_latency_ms=indexing_latency_ms,
                last_seen_at=last_seen_at,
                last_seen_sync_id=last_seen_sync_id,
                cleanup_missing_documents=cleanup_missing_documents,
                cleanup_document_id_prefixes=getattr(
                    connector,
                    "cleanup_document_id_prefixes",
                    (),
                ),
                deleted_at=_now(),
                stale_cleanup_disabled_reason=_stale_cleanup_reason_for_connector(
                    connector
                ),
            )
            await self._drain_vector_cleanup_backlog(deleted_chunk_ids, source_id)
            return finished

        except _InactiveJobStop as stop:
            return stop.job
        except _StopRequested:
            error_message = OBSERVER_CANCELLED_SYNC_ERROR
            if "uncommitted_vector_ids" in locals():
                await self._delete_vectors_best_effort(
                    uncommitted_vector_ids, source_id
                )
            return _complete_failed_job(error_message)
        except asyncio.CancelledError:
            error_message = cancellation_error
            logger.warning("Sync cancelled for source %s", source_id)
            if "uncommitted_vector_ids" in locals():
                await self._delete_vectors_best_effort(
                    uncommitted_vector_ids, source_id
                )
            _complete_failed_job(error_message)
            raise
        except CareerManifestParsingError as exc:
            total_documents = exc.attempted_documents
            parsed_documents = exc.completed_documents
            error_message = "Career manifest snapshot did not parse completely."
            logger.error("Career manifest parsing failed for source %s", source_id)
            return _complete_failed_job(
                error_message,
                parsing_failures=1,
            )
        except ParsingError as exc:
            error_message = _redact_sensitive_error(str(exc))
            logger.error(
                "Career parsing failed for source %s: %s", source_id, error_message
            )
            return _complete_failed_job(error_message, parsing_failures=1)
        except Exception as exc:
            error_message = _redact_sensitive_error(str(exc))
            logger.error("Sync failed for source %s: %s", source_id, error_message)
            if "uncommitted_vector_ids" in locals():
                await self._delete_vectors_best_effort(
                    uncommitted_vector_ids, source_id
                )
            return _complete_failed_job(error_message)
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
        parsed_documents: int | None = None,
        updated_documents: int | None = None,
        created_chunks: int | None = None,
        updated_chunks: int | None = None,
        skipped_chunks: int | None = None,
        embeddings_generated: int | None = None,
        embeddings_reused: int | None = None,
        parsing_failures: int | None = None,
        indexing_latency_ms: float | None = None,
        phase: str = INDEXING_DOCUMENTS_PHASE,
        upstream_total: int | None = None,
        upstream_done: int | None = None,
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
                parsed_documents=(
                    current_job.parsed_documents
                    if parsed_documents is None
                    else parsed_documents
                ),
                updated_documents=(
                    current_job.updated_documents
                    if updated_documents is None
                    else updated_documents
                ),
                created_chunks=(
                    current_job.created_chunks
                    if created_chunks is None
                    else created_chunks
                ),
                updated_chunks=(
                    current_job.updated_chunks
                    if updated_chunks is None
                    else updated_chunks
                ),
                skipped_chunks=(
                    current_job.skipped_chunks
                    if skipped_chunks is None
                    else skipped_chunks
                ),
                embeddings_generated=(
                    current_job.embeddings_generated
                    if embeddings_generated is None
                    else embeddings_generated
                ),
                embeddings_reused=(
                    current_job.embeddings_reused
                    if embeddings_reused is None
                    else embeddings_reused
                ),
                parsing_failures=(
                    current_job.parsing_failures
                    if parsing_failures is None
                    else parsing_failures
                ),
                indexing_latency_ms=(
                    current_job.indexing_latency_ms
                    if indexing_latency_ms is None
                    else indexing_latency_ms
                ),
                phase=phase,
                upstream_total=(
                    current_job.upstream_total
                    if upstream_total is None
                    else upstream_total
                ),
                upstream_done=(
                    current_job.upstream_done
                    if upstream_done is None
                    else upstream_done
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

    @staticmethod
    def _index_metric(result: object, key: str, default: int) -> int:
        if not isinstance(result, dict):
            return default
        value = result.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return default
        return value

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
            # Heartbeat touch for orphan detection. Callers coalesce page_fetch_*
            # cadence so this is not invoked on every item event.
            current_job = self.metadata_store.touch_sync_job(job_id)
            if not current_job:
                raise ValueError(f"Unknown sync job: {job_id}")
            if current_job.status != SyncJobStatus.RUNNING:
                return current_job
            return None
        except Exception as exc:
            logger.debug(
                "Unable to refresh sync progress heartbeat for job %s: %s",
                job_id,
                _redact_sensitive_error(str(exc)),
            )
            return None

    def _require_running_job_for_progress(self, job_id: str, *, touch: bool):
        if touch:
            inactive = self._refresh_running_job_for_progress(job_id)
            if inactive:
                return inactive
            return None
        current_job = self.metadata_store.get_sync_job(job_id)
        if not current_job:
            raise ValueError(f"Unknown sync job: {job_id}")
        if current_job.status != SyncJobStatus.RUNNING:
            return current_job
        return None

    async def _handle_source_fetch_progress(
        self, job_id: str, source_id: str, event: dict
    ):
        event_name = str(event.get("event") or "").strip()
        total_pages = _int_progress_value(event.get("total_pages"))
        current_page = _int_progress_value(event.get("current_page"))
        elapsed_seconds = _float_progress_value(event.get("elapsed_seconds"))
        progress_timestamp = _now()
        is_page_fetch = event_name in {
            "page_fetch_started",
            "page_fetch_completed",
            "page_fetch_skipped",
        }

        if is_page_fetch:
            persist_liveness = _should_persist_page_fetch_liveness(
                current_page, total_pages
            )
            persist_hints = _should_persist_page_fetch_hints(current_page, total_pages)
            inactive = self._require_running_job_for_progress(
                job_id,
                touch=persist_liveness,
            )
            if inactive:
                raise _InactiveJobStop(inactive)
            if not persist_liveness and not persist_hints:
                if event_name == "page_fetch_started":
                    logger.info(
                        "Source %s fetching upstream item %s/%s",
                        source_id,
                        current_page or "?",
                        total_pages or "?",
                    )
                elif event_name == "page_fetch_completed":
                    logger.info(
                        "Source %s fetched upstream item %s/%s in %.2fs",
                        source_id,
                        current_page or "?",
                        total_pages or "?",
                        elapsed_seconds,
                    )
                else:
                    logger.info(
                        "Source %s reused stored content for upstream item %s/%s",
                        source_id,
                        current_page or "?",
                        total_pages or "?",
                    )
                return None

            running_job = self.metadata_store.get_sync_job(job_id)
            existing_total = _int_progress_value(
                getattr(running_job, "upstream_total", 0) if running_job else 0
            )
            existing_done = _int_progress_value(
                getattr(running_job, "upstream_done", 0) if running_job else 0
            )
            updates: dict[str, Any] = {
                "phase": FETCHING_PAGE_CONTENT_PHASE,
                "last_progress_at": progress_timestamp,
            }
            if persist_hints:
                updates["upstream_total"] = max(total_pages, existing_total)
                if event_name == "page_fetch_started":
                    updates["upstream_done"] = max(
                        max(current_page - 1, 0), existing_done
                    )
                    updates["status_message"] = (
                        "Fetching upstream items "
                        f"{max(current_page - 1, 0)}/{total_pages} completed; "
                        f"now fetching item {current_page}."
                        if total_pages
                        else "Fetching upstream items before indexing begins."
                    )
                elif event_name == "page_fetch_skipped":
                    updates["upstream_done"] = max(current_page, existing_done)
                    updates["status_message"] = (
                        "Reused stored upstream item content "
                        f"{current_page}/{total_pages} before indexing begins."
                        if total_pages
                        else "Reused stored upstream item content before indexing begins."
                    )
                else:
                    updates["upstream_done"] = max(current_page, existing_done)
                    updates["status_message"] = (
                        f"Fetching upstream items {current_page}/{total_pages} before indexing begins."
                        if total_pages
                        else "Fetching upstream items before indexing begins."
                    )
            self._update_sync_job_hints_best_effort(job_id, **updates)
            if event_name == "page_fetch_started":
                logger.info(
                    "Source %s fetching upstream item %s/%s",
                    source_id,
                    current_page or "?",
                    total_pages or "?",
                )
            elif event_name == "page_fetch_completed":
                logger.info(
                    "Source %s fetched upstream item %s/%s in %.2fs",
                    source_id,
                    current_page or "?",
                    total_pages or "?",
                    elapsed_seconds,
                )
            else:
                logger.info(
                    "Source %s reused stored content for upstream item %s/%s",
                    source_id,
                    current_page or "?",
                    total_pages or "?",
                )
            return None

        current_job = self._refresh_running_job_for_progress(job_id)
        if current_job:
            raise _InactiveJobStop(current_job)
        running_job = self.metadata_store.get_sync_job(job_id)
        existing_total = _int_progress_value(
            getattr(running_job, "upstream_total", 0) if running_job else 0
        )
        existing_done = _int_progress_value(
            getattr(running_job, "upstream_done", 0) if running_job else 0
        )

        if event_name == "search_started":
            self._update_sync_job_hints_best_effort(
                job_id,
                phase="discovering_pages",
                last_progress_at=progress_timestamp,
                status_message="Discovering upstream items before indexing begins.",
            )
            logger.info("Source %s started upstream item discovery", source_id)
            return None

        if event_name == "search_completed":
            self._update_sync_job_hints_best_effort(
                job_id,
                phase=FETCHING_PAGE_CONTENT_PHASE,
                upstream_total=total_pages,
                upstream_done=0,
                last_progress_at=progress_timestamp,
                status_message=(
                    f"Fetching upstream items 0/{total_pages} before indexing begins."
                    if total_pages
                    else "No upstream items found to index."
                ),
            )
            logger.info(
                "Source %s discovered %s upstream item(s) before indexing",
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
                upstream_total=max(discovered, existing_total),
                upstream_done=existing_done,
                last_progress_at=progress_timestamp,
                status_message=(
                    f"Discovering upstream items: {discovered} found after batch {batch_index}."
                    + (" More results remain." if has_more else "")
                ),
            )
            logger.info(
                "Source %s discovered %s upstream item(s) after search batch %s",
                source_id,
                discovered,
                batch_index or "?",
            )
            return None

        logger.debug(
            "Ignoring unknown fetch progress event for source %s: %s",
            source_id,
            event_name or "<empty>",
        )
        return None

    async def _await_finished_background_handoff(
        self,
        source_id: str,
        attempts: int = 5,
        completion_grace_seconds: float = 0.0,
    ) -> None:
        if completion_grace_seconds > 0:
            existing_task = self._background_sync_tasks.get(source_id)
            if existing_task is not None and not existing_task.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(existing_task),
                        timeout=completion_grace_seconds,
                    )
                except TimeoutError:
                    return
                except asyncio.CancelledError:
                    if not existing_task.cancelled():
                        raise
                self._reconcile_finished_background_task(source_id)
                return
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
        current_job = self.metadata_store.validate_running_job_document(
            job_id, document
        )
        if not current_job:
            raise ValueError(f"Unknown sync job: {job_id}")
        if current_job.status != SyncJobStatus.RUNNING:
            return current_job
        return None

    def _commit_chunks_or_current(self, job_id: str, document: DocumentModel, chunks):
        _, current_job = (
            self.metadata_store.upsert_document_and_replace_chunks_for_running_job(
                job_id,
                document,
                chunks,
            )
        )
        if not current_job:
            raise ValueError(f"Unknown sync job: {job_id}")
        if current_job.status != SyncJobStatus.RUNNING:
            return current_job
        return None

    async def _update_vector_metadata(self, chunks, *, platform: str):
        update_metadata = getattr(self.indexer, "update_documents_metadata", None)
        if not callable(update_metadata):
            raise RuntimeError("Configured indexer cannot refresh vector metadata")
        result = update_metadata(
            [chunk.to_document_model(platform=platform) for chunk in chunks]
        )
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def _refresh_vector_metadata_and_commit(
        self,
        job_id: str,
        document: DocumentModel,
        chunks,
        retained_metadata_changed_chunks,
    ):
        metadata_update_result = None
        refresh_chunk_ids = [
            chunk.chunk_id for chunk in retained_metadata_changed_chunks
        ]
        await self._record_vector_metadata_refresh_intents(
            refresh_chunk_ids,
            source_id=document.source_id,
            document_id=document.document_id or document.id,
            job_id=job_id,
        )
        try:
            if retained_metadata_changed_chunks:
                metadata_update_result = await self._update_vector_metadata(
                    retained_metadata_changed_chunks,
                    platform=document.platform,
                )
            inactive_job = self._commit_chunks_or_current(job_id, document, chunks)
        except asyncio.CancelledError:
            if metadata_update_result is not None:
                await self._rollback_vector_metadata(metadata_update_result)
                await self._mark_vector_metadata_refresh_complete(
                    refresh_chunk_ids,
                    source_id=document.source_id,
                )
            raise
        except Exception:
            if metadata_update_result is not None:
                await self._rollback_vector_metadata(metadata_update_result)
                await self._mark_vector_metadata_refresh_complete(
                    refresh_chunk_ids,
                    source_id=document.source_id,
                )
            raise
        if inactive_job and metadata_update_result is not None:
            await self._rollback_vector_metadata(metadata_update_result)
            await self._mark_vector_metadata_refresh_complete(
                refresh_chunk_ids,
                source_id=document.source_id,
            )
        return inactive_job, metadata_update_result

    async def _reconcile_pending_vector_metadata_refresh(
        self,
        *,
        source_id: str,
        document_id: str,
        authoritative_chunks,
        platform: str,
    ) -> None:
        authoritative_by_id = {chunk.chunk_id: chunk for chunk in authoritative_chunks}
        while True:
            pending_ids = await self._run_blocking_operation(
                self.metadata_store.list_pending_vector_metadata_refresh_ids,
                source_id,
                document_id=document_id,
            )
            if not pending_ids:
                return
            chunks_to_restore = [
                authoritative_by_id[chunk_id]
                for chunk_id in pending_ids
                if chunk_id in authoritative_by_id
            ]
            if not chunks_to_restore:
                return
            await self._update_vector_metadata(chunks_to_restore, platform=platform)
            await self._mark_vector_metadata_refresh_complete(
                [chunk.chunk_id for chunk in chunks_to_restore],
                source_id=source_id,
            )

    async def _rollback_vector_metadata(self, index_result: object) -> None:
        if not isinstance(index_result, dict):
            raise RuntimeError("Vector metadata update did not return rollback state")
        rollback_state = index_result.get("metadata_rollback")
        rollback_metadata = getattr(self.indexer, "rollback_documents_metadata", None)
        if rollback_state is None or not callable(rollback_metadata):
            raise RuntimeError("Configured indexer cannot roll back vector metadata")
        result = rollback_metadata(rollback_state)
        if asyncio.iscoroutine(result):
            await result

    @staticmethod
    def _vector_chunk_metadata_changed(existing, current) -> bool:
        if existing.evidence_source_type is None:
            return False
        fields = (
            "chunk_index",
            "line_start",
            "line_end",
            "version_id",
            "document_version_id",
            "created_at",
            "updated_at",
            "evidence_source_type",
            "experience_type",
        )
        return any(
            getattr(existing, field) != getattr(current, field) for field in fields
        )

    @classmethod
    def _vector_metadata_refresh_chunks_after_index(
        cls,
        chunks,
        retained_metadata_changed_chunks,
        generated_chunk_ids: set[str],
        index_result: object,
    ):
        refresh_ids = {
            chunk.chunk_id for chunk in retained_metadata_changed_chunks
        }
        reused_vector_count = cls._index_metric(
            index_result,
            "embeddings_reused",
            len(generated_chunk_ids),
        )
        if generated_chunk_ids and reused_vector_count > 0:
            refresh_ids.update(
                chunk.chunk_id
                for chunk in chunks
                if chunk.evidence_source_type is not None
                and chunk.chunk_id in generated_chunk_ids
            )
        return [chunk for chunk in chunks if chunk.chunk_id in refresh_ids]

    @staticmethod
    def _career_metadata_changed(
        existing: DocumentModel | None,
        current: DocumentModel,
    ) -> bool:
        if (
            existing is None
            or existing.evidence_source_type is None
            or current.evidence_source_type is None
        ):
            return False
        fields = (
            "title",
            "document_title",
            "file_name",
            "company",
            "role",
            "project",
            "start_date",
            "end_date",
        )
        return any(
            getattr(existing, field) != getattr(current, field) for field in fields
        )

    async def _record_vector_write_intents(
        self,
        chunk_ids: list[str],
        *,
        source_id: str,
        document_id: str,
        job_id: str,
    ) -> None:
        if not chunk_ids:
            return
        await self._run_blocking_operation(
            self.metadata_store.record_vector_write_intents,
            chunk_ids,
            source_id=source_id,
            document_id=document_id,
            job_id=job_id,
        )

    async def _record_vector_metadata_refresh_intents(
        self,
        chunk_ids: list[str],
        *,
        source_id: str,
        document_id: str,
        job_id: str,
    ) -> None:
        if not chunk_ids:
            return
        await self._run_blocking_operation(
            self.metadata_store.record_vector_metadata_refresh_intents,
            chunk_ids,
            source_id=source_id,
            document_id=document_id,
            job_id=job_id,
        )

    async def _mark_vector_metadata_refresh_complete(
        self,
        chunk_ids: list[str],
        *,
        source_id: str,
    ) -> None:
        if not chunk_ids:
            return
        await self._run_blocking_operation(
            self.metadata_store.mark_vector_metadata_refresh_complete,
            chunk_ids,
            source_id=source_id,
        )

    @staticmethod
    async def _run_blocking_operation(operation, *args, **kwargs):
        """Keep a started SQLite/vector operation alive through caller cancellation."""
        task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except Exception:
                pass
            raise

    async def _drain_vector_cleanup_backlog(
        self,
        newly_deleted_chunk_ids: list[str],
        source_id: str,
    ) -> None:
        if newly_deleted_chunk_ids:
            cleaned = await self._delete_vectors_best_effort(
                newly_deleted_chunk_ids,
                source_id,
            )
            if not cleaned:
                return

        seen_pages: set[tuple[str, ...]] = set()
        for _ in range(VECTOR_CLEANUP_MAX_PAGES_PER_SYNC):
            pending = await self._run_blocking_operation(
                self.metadata_store.list_pending_vector_cleanup_ids,
                source_id,
                limit=VECTOR_CLEANUP_PAGE_SIZE,
            )
            if not pending:
                return
            page_key = tuple(pending)
            if page_key in seen_pages:
                logger.error("Vector cleanup made no progress for source %s", source_id)
                return
            seen_pages.add(page_key)
            cleaned = await self._delete_vectors_best_effort(pending, source_id)
            if not cleaned:
                return
        logger.info(
            "Vector cleanup retry budget exhausted for source %s; remaining work is deferred",
            source_id,
        )

    async def _delete_vectors_best_effort(
        self,
        chunk_ids: list[str],
        source_id: str,
    ) -> bool:
        if not chunk_ids or not hasattr(self.indexer, "delete_documents_by_ids"):
            return not chunk_ids
        try:
            unique_chunk_ids = list(
                dict.fromkeys(chunk_id for chunk_id in chunk_ids if chunk_id)
            )
            active_chunk_ids = await self._run_blocking_operation(
                self.metadata_store.get_active_chunk_ids,
                unique_chunk_ids,
                source_id=source_id,
            )
            deletable_chunk_ids = [
                chunk_id
                for chunk_id in unique_chunk_ids
                if chunk_id not in active_chunk_ids
            ]
            if not deletable_chunk_ids:
                return True
            await self._run_blocking_operation(
                self.metadata_store.record_pending_vector_cleanup_ids,
                deletable_chunk_ids,
                source_id=source_id,
            )
            delete_operation = self.indexer.delete_documents_by_ids
            if inspect.iscoroutinefunction(delete_operation):
                await delete_operation(deletable_chunk_ids, source_id=source_id)
            else:
                delete_result = await self._run_blocking_operation(
                    delete_operation,
                    deletable_chunk_ids,
                    source_id=source_id,
                )
                if inspect.isawaitable(delete_result):
                    await delete_result
            await self._run_blocking_operation(
                self.metadata_store.mark_vector_cleanup_complete,
                deletable_chunk_ids,
                source_id=source_id,
            )
            return True
        except Exception as exc:
            logger.error(
                "Vector cleanup failed for source %s: %s",
                source_id,
                _redact_sensitive_error(str(exc)),
            )
            return False

    @staticmethod
    def _normalize_document(
        document: DocumentModel,
        source_id: str,
        last_seen_at: str,
        last_seen_sync_id: str = "",
    ) -> DocumentModel:
        document_id = (
            document.document_id
            if document.evidence_source_type and document.document_id
            else document.external_id or document.document_id or document.id
        )
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
