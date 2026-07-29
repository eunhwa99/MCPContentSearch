from __future__ import annotations

import argparse
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import signal
from datetime import datetime, timezone

from app_runtime import build_ingestion_runtime
from core.error_sanitizer import safe_error_message, sanitize_error_text
from core.models import SyncJobStatus
from environments.config import AppConfig
from environments.runtime_env import get_env_secret
from environments.token import NOTION_API_KEY, TISTORY_BLOG_NAME
from storage.metadata_store import ORPHANED_SYNC_JOB_RECOVERY_MESSAGE

logger = logging.getLogger(__name__)
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
MIN_POLL_INTERVAL_SECONDS = 0.1
MAX_POLL_INTERVAL_SECONDS = 60.0
DEFAULT_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 3
MIN_LOG_MAX_BYTES = 1024
MAX_LOG_MAX_BYTES = 100 * 1024 * 1024
MAX_LOG_BACKUP_COUNT = 20
MAX_PERSISTED_LOG_MESSAGE_BYTES = 512
MAX_PERSISTED_LOG_CONTEXT_BYTES = 192
MAX_PERSISTED_LOG_RECORD_BYTES = 768
LOG_TRUNCATION_MARKER = " ...<truncated>"
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
PROJECT_LOGGER_PREFIXES = (
    "api",
    "app_runtime",
    "core",
    "environments",
    "fetching",
    "indexing",
    "search",
    "storage",
)
PATH_FIELD_BOUNDARY = r"(?=(?:[,;]\s*|\s+)[A-Za-z_][A-Za-z0-9_.-]*\s*=|[\"'<>\n]|$)"
SANITIZED_PATH_TAIL_RE = re.compile(
    r"(?P<marker><redacted(?:-path)?>)"
    r"(?P<tail>[ \t]+(?:(?!(?:[,;]\s*|\s+)"
    r"[A-Za-z_][A-Za-z0-9_.-]*\s*=|[\"'<>\n]).)+)" + PATH_FIELD_BOUNDARY
)
PATH_TAIL_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,16}\s*$")


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    marker = LOG_TRUNCATION_MARKER.encode("utf-8")
    prefix = encoded[: max_bytes - len(marker)].decode("utf-8", errors="ignore")
    return f"{prefix}{LOG_TRUNCATION_MARKER}"


def _redact_pre_sanitized_path_tail(match: re.Match[str]) -> str:
    marker = match.group("marker")
    if marker == "<redacted>":
        return marker
    tail = match.group("tail")
    if "/" in tail or "\\" in tail or PATH_TAIL_EXTENSION_RE.search(tail):
        return marker
    return match.group(0)


def _redact_worker_log_message(message: str) -> str:
    message = sanitize_error_text(
        message,
        max_length=max(len(message), 1),
    )
    return SANITIZED_PATH_TAIL_RE.sub(_redact_pre_sanitized_path_tail, message)


class BoundedWorkerLogFormatter(logging.Formatter):
    """Keep one formatted record below the smallest supported log-file limit."""

    def format(self, record: logging.LogRecord) -> str:
        return _truncate_utf8(
            super().format(record),
            MAX_PERSISTED_LOG_RECORD_BYTES,
        )


class ByteBoundedRotatingFileHandler(RotatingFileHandler):
    """Measure UTF-8 bytes so rotation never undercounts non-ASCII records."""

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if self.maxBytes <= 0:
            return False
        if self.stream is None:
            self.stream = self._open()
        rendered = f"{self.format(record)}{self.terminator}"
        rendered_size = len(rendered.encode("utf-8"))
        self.stream.seek(0, os.SEEK_END)
        return self.stream.tell() + rendered_size > self.maxBytes


class WorkerLogPrivacyFilter(logging.Filter):
    """Keep project lifecycle logs while suppressing or redacting noisy context."""

    def filter(self, record: logging.LogRecord) -> bool:
        is_project_logger = any(
            record.name == prefix or record.name.startswith(f"{prefix}.")
            for prefix in PROJECT_LOGGER_PREFIXES
        )
        minimum_level = logging.INFO if is_project_logger else logging.WARNING
        if record.levelno < minimum_level:
            return False

        message = record.getMessage()
        if record.exc_info is not None:
            exception = record.exc_info[1]
            exception_message = _redact_worker_log_message(
                str(exception) or type(exception).__name__
            )
            message = f"{message} ({type(exception).__name__}: {exception_message})"
            record.exc_info = None
            record.exc_text = None
        record.msg = _truncate_utf8(
            _redact_worker_log_message(message),
            MAX_PERSISTED_LOG_MESSAGE_BYTES,
        )
        record.args = ()
        if record.stack_info:
            record.stack_info = _truncate_utf8(
                _redact_worker_log_message(record.stack_info),
                MAX_PERSISTED_LOG_CONTEXT_BYTES,
            )
        if record.exc_text:
            record.exc_text = _truncate_utf8(
                _redact_worker_log_message(record.exc_text),
                MAX_PERSISTED_LOG_CONTEXT_BYTES,
            )
        return True


def _poll_interval(value: str | float) -> float:
    try:
        interval = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("poll interval must be a number") from None
    if not MIN_POLL_INTERVAL_SECONDS <= interval <= MAX_POLL_INTERVAL_SECONDS:
        raise argparse.ArgumentTypeError(
            "poll interval must be between "
            f"{MIN_POLL_INTERVAL_SECONDS} and {MAX_POLL_INTERVAL_SECONDS} seconds"
        )
    return interval


class SyncWorker:
    """Run queued source syncs serially in a process independent from FastMCP."""

    def __init__(
        self,
        ingestion_service,
        metadata_store,
        *,
        source_ids: tuple[str, ...] | list[str] | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ):
        self.ingestion_service = ingestion_service
        self.metadata_store = metadata_store
        self.source_ids = tuple(source_ids) if source_ids is not None else None
        self.poll_interval_seconds = _poll_interval(poll_interval_seconds)

    async def run_once(self):
        """Claim and finish at most one queued job."""
        job = self.metadata_store.claim_next_sync_job(self.source_ids)
        if job is None:
            return None
        return await self._execute_claimed_job(job)

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Poll until stopped, cancelling and failing an in-flight job gracefully."""
        stop_event = stop_event or asyncio.Event()
        while not stop_event.is_set():
            job = self.metadata_store.claim_next_sync_job(self.source_ids)
            if job is None:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self.poll_interval_seconds,
                    )
                except TimeoutError:
                    continue
                break

            job_task = asyncio.create_task(
                self._execute_claimed_job(job),
                name=f"durable-sync:{job.source_id}:{job.job_id}",
            )
            stop_task = asyncio.create_task(
                stop_event.wait(),
                name="durable-sync-worker-stop",
            )
            done, _ = await asyncio.wait(
                {job_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done and stop_event.is_set() and not job_task.done():
                job_task.cancel()
            if not stop_task.done():
                stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
            try:
                await job_task
            except asyncio.CancelledError:
                if not stop_event.is_set():
                    raise
            if stop_event.is_set():
                break

    async def _execute_claimed_job(self, job):
        logger.info(
            "Running durable sync job %s for source %s",
            job.job_id,
            job.source_id,
        )
        try:
            result = await self.ingestion_service.run_claimed_sync_job(job.job_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Durable sync job %s failed before ingestion completed: %s",
                job.job_id,
                safe_error_message(exc),
            )
            current = self.metadata_store.get_sync_job(job.job_id)
            if current is not None and current.status == SyncJobStatus.RUNNING:
                return self.metadata_store.complete_failed_sync(
                    job_id=job.job_id,
                    source_id=job.source_id,
                    error_message=(
                        "Sync worker could not execute the claimed job; "
                        "see worker logs and start sync again."
                    ),
                )
            if current is not None:
                return current
            return None
        if result is None:
            return None
        logger.info(
            "Durable sync job %s for source %s finished with status %s",
            result.job_id,
            result.source_id,
            result.status.value,
        )
        return result


def create_worker(*, poll_interval_seconds: float) -> SyncWorker:
    process_started_at = datetime.now(timezone.utc).isoformat()
    config = AppConfig()
    runtime = build_ingestion_runtime(
        config=config,
        notion_api_key=NOTION_API_KEY,
        tistory_blog_name=TISTORY_BLOG_NAME,
        github_token=get_env_secret(config.github_token_env_var),
    )
    recovered_count = runtime.metadata_store.recover_orphaned_running_jobs(
        started_before=process_started_at,
        error_message=ORPHANED_SYNC_JOB_RECOVERY_MESSAGE,
        source_ids=runtime.retained_source_ids,
    )
    if recovered_count:
        logger.info("Recovered %s orphaned running sync job(s)", recovered_count)
    return SyncWorker(
        runtime.ingestion_service,
        runtime.metadata_store,
        source_ids=runtime.retained_source_ids,
        poll_interval_seconds=poll_interval_seconds,
    )


async def _run_worker(worker: SyncWorker) -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop_event.set))
    await worker.run(stop_event)


def _default_poll_interval() -> float:
    return _poll_interval(
        os.getenv(
            "CONTEXTWIKI_SYNC_WORKER_POLL_SECONDS",
            str(DEFAULT_POLL_INTERVAL_SECONDS),
        )
    )


def _bounded_log_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _configure_logging() -> logging.Handler:
    log_path_value = os.getenv("CONTEXTWIKI_SYNC_WORKER_LOG_PATH", "").strip()
    if not log_path_value:
        handler: logging.Handler = logging.StreamHandler()
    else:
        log_path = Path(log_path_value).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = ByteBoundedRotatingFileHandler(
            log_path,
            maxBytes=_bounded_log_int(
                "CONTEXTWIKI_SYNC_WORKER_LOG_MAX_BYTES",
                DEFAULT_LOG_MAX_BYTES,
                MIN_LOG_MAX_BYTES,
                MAX_LOG_MAX_BYTES,
            ),
            backupCount=_bounded_log_int(
                "CONTEXTWIKI_SYNC_WORKER_LOG_BACKUP_COUNT",
                DEFAULT_LOG_BACKUP_COUNT,
                1,
                MAX_LOG_BACKUP_COUNT,
            ),
            encoding="utf-8",
        )
    handler.addFilter(WorkerLogPrivacyFilter())
    handler.setFormatter(BoundedWorkerLogFormatter(LOG_FORMAT))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    return handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the durable ContextWiki sync worker."
    )
    parser.add_argument(
        "--poll-interval",
        type=_poll_interval,
        default=_default_poll_interval(),
        help=(
            "Seconds to wait between empty queue polls "
            f"({MIN_POLL_INTERVAL_SECONDS}-{MAX_POLL_INTERVAL_SECONDS})."
        ),
    )
    args = parser.parse_args(argv)
    _configure_logging()
    worker = create_worker(poll_interval_seconds=args.poll_interval)
    logger.info("Starting durable sync worker")
    asyncio.run(_run_worker(worker))
    logger.info("Durable sync worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
