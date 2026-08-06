from __future__ import annotations

import argparse
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from pathlib import Path
import re
import signal
import stat
from datetime import datetime, timezone

from app_runtime import build_ingestion_runtime
from core.error_sanitizer import safe_error_message, sanitize_error_text
from core.models import SyncJobStatus
from environments.config import AppConfig
from environments.runtime_env import get_env_secret
from environments.token import NOTION_API_KEY, TISTORY_BLOG_NAME
from indexing.ingestion_service import WORKER_STOPPED_SYNC_ERROR
from storage.metadata_store import ORPHANED_SYNC_JOB_RECOVERY_MESSAGE

logger = logging.getLogger(__name__)
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
MIN_POLL_INTERVAL_SECONDS = 0.05
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

    def _open(self):
        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        file_descriptor = os.open(self.baseFilename, flags, 0o600)
        try:
            file_stat = os.fstat(file_descriptor)
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_uid != os.getuid()
                or stat.S_IMODE(file_stat.st_mode) != 0o600
            ):
                raise PermissionError("unsafe sync worker log file")
            return open(
                file_descriptor,
                self.mode,
                encoding=self.encoding,
                errors=self.errors,
                closefd=True,
            )
        except BaseException:
            os.close(file_descriptor)
            raise

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

    @staticmethod
    def _extract_exception(record_exc_info: object) -> BaseException | None:
        try:
            if isinstance(record_exc_info, tuple):
                if len(record_exc_info) < 2:
                    return None
                exception = record_exc_info[1]
                return exception if isinstance(exception, BaseException) else None
            if isinstance(record_exc_info, list):
                if len(record_exc_info) < 2:
                    return None
                exception = record_exc_info[1]
                return exception if isinstance(exception, BaseException) else None
            if isinstance(record_exc_info, BaseException):
                return record_exc_info
            if record_exc_info is True:
                _, exception, _ = sys.exc_info()
                return exception
        except Exception:
            return None
        return None

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
            try:
                exception = self._extract_exception(record.exc_info)
                if exception is not None:
                    exception_message = _redact_worker_log_message(
                        str(exception) or type(exception).__name__
                    )
                    message = f"{message} ({type(exception).__name__}: {exception_message})"
            finally:
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


DEFAULT_MAX_CONCURRENT_JOBS = 2
MIN_MAX_CONCURRENT_JOBS = 1
MAX_MAX_CONCURRENT_JOBS = 8
MAX_CONCURRENT_ENV_VAR = "CONTEXTZIP_SYNC_WORKER_MAX_CONCURRENT"


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


def _max_concurrent_jobs(value: str | int) -> int:
    """Parse and bound sync worker concurrency; fail closed on invalid values."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(
            "max_concurrent_jobs must be an integer between "
            f"{MIN_MAX_CONCURRENT_JOBS} and {MAX_MAX_CONCURRENT_JOBS}"
        )
    if isinstance(value, str):
        raw = value.strip()
        if not raw or raw.lower() in {"true", "false"} or not raw.lstrip("-").isdigit():
            raise ValueError(
                "max_concurrent_jobs must be an integer between "
                f"{MIN_MAX_CONCURRENT_JOBS} and {MAX_MAX_CONCURRENT_JOBS}"
            )
        try:
            parsed = int(raw)
        except ValueError as exc:
            raise ValueError(
                "max_concurrent_jobs must be an integer between "
                f"{MIN_MAX_CONCURRENT_JOBS} and {MAX_MAX_CONCURRENT_JOBS}"
            ) from exc
    else:
        parsed = value
    if not MIN_MAX_CONCURRENT_JOBS <= parsed <= MAX_MAX_CONCURRENT_JOBS:
        raise ValueError(
            "max_concurrent_jobs must be an integer between "
            f"{MIN_MAX_CONCURRENT_JOBS} and {MAX_MAX_CONCURRENT_JOBS}"
        )
    return parsed


def _default_max_concurrent_jobs() -> int:
    raw = os.getenv(MAX_CONCURRENT_ENV_VAR)
    if raw is None:
        return DEFAULT_MAX_CONCURRENT_JOBS
    return _max_concurrent_jobs(raw)


class SyncWorker:
    """Run queued source syncs with bounded concurrency independent from FastMCP."""

    def __init__(
        self,
        ingestion_service,
        metadata_store,
        *,
        source_ids: tuple[str, ...] | list[str] | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        max_concurrent_jobs: int = DEFAULT_MAX_CONCURRENT_JOBS,
    ):
        self.ingestion_service = ingestion_service
        self.metadata_store = metadata_store
        self.source_ids = tuple(source_ids) if source_ids is not None else None
        self.poll_interval_seconds = _poll_interval(poll_interval_seconds)
        self.max_concurrent_jobs = _max_concurrent_jobs(max_concurrent_jobs)
        self.metadata_store.max_concurrent_sync_jobs = self.max_concurrent_jobs

    async def run_once(self):
        """Claim and finish at most one queued job."""
        job = self.metadata_store.claim_next_sync_job(self.source_ids)
        if job is None:
            return None
        return await self._execute_claimed_job(job)

    async def _cancel_in_flight(self, in_flight: set[asyncio.Task]) -> None:
        for task in in_flight:
            if not task.done():
                task.cancel()
        if not in_flight:
            return
        results = await asyncio.gather(*in_flight, return_exceptions=True)
        in_flight.clear()
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                continue
            if isinstance(result, BaseException):
                raise result

    async def _await_cancel_in_flight(self, in_flight: set[asyncio.Task]) -> None:
        """Cancel in-flight jobs and join even if this awaiter is cancelled."""
        drain = asyncio.create_task(self._cancel_in_flight(in_flight))
        current = asyncio.current_task()
        try:
            await asyncio.shield(drain)
        except asyncio.CancelledError:
            while not drain.done():
                try:
                    await asyncio.shield(drain)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    continue
            raise
        except Exception:
            if current is not None and current.cancelling():
                while not drain.done():
                    try:
                        await asyncio.shield(drain)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        continue
                raise asyncio.CancelledError from None
            raise

    async def run(self, stop_event: asyncio.Event | None = None) -> None:
        """Poll until stopped, running up to N claimed jobs concurrently."""
        stop_event = stop_event or asyncio.Event()
        in_flight: set[asyncio.Task] = set()
        try:
            while not stop_event.is_set() or in_flight:
                while (
                    not stop_event.is_set()
                    and len(in_flight) < self.max_concurrent_jobs
                ):
                    job = self.metadata_store.claim_next_sync_job(self.source_ids)
                    if job is None:
                        break
                    in_flight.add(
                        asyncio.create_task(
                            self._execute_claimed_job(job),
                            name=f"durable-sync:{job.source_id}:{job.job_id}",
                        )
                    )

                if stop_event.is_set():
                    await self._await_cancel_in_flight(in_flight)
                    break

                if not in_flight:
                    try:
                        await asyncio.wait_for(
                            stop_event.wait(),
                            timeout=self.poll_interval_seconds,
                        )
                    except TimeoutError:
                        continue
                    break

                stop_task = asyncio.create_task(
                    stop_event.wait(),
                    name="durable-sync-worker-stop",
                )
                try:
                    done, _ = await asyncio.wait(
                        in_flight | {stop_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    if not stop_task.done():
                        stop_task.cancel()
                        await asyncio.gather(stop_task, return_exceptions=True)

                for task in in_flight & done:
                    in_flight.discard(task)
                    try:
                        await task
                    except asyncio.CancelledError:
                        if not stop_event.is_set():
                            await self._await_cancel_in_flight(in_flight)
                            raise

                if stop_event.is_set():
                    await self._await_cancel_in_flight(in_flight)
                    break
        finally:
            await self._await_cancel_in_flight(in_flight)

    async def _execute_claimed_job(self, job):
        logger.info(
            "Running durable sync job %s for source %s",
            job.job_id,
            job.source_id,
        )
        try:
            result = await self.ingestion_service.run_claimed_sync_job(job.job_id)
        except asyncio.CancelledError:
            try:
                current = self.metadata_store.get_sync_job(job.job_id)
                if current is not None and current.status == SyncJobStatus.RUNNING:
                    self.metadata_store.complete_failed_sync(
                        job_id=job.job_id,
                        source_id=job.source_id,
                        error_message=WORKER_STOPPED_SYNC_ERROR,
                    )
            except Exception as finalize_exc:
                logger.error(
                    "Failed to finalize cancelled sync job %s: %s",
                    job.job_id,
                    safe_error_message(finalize_exc),
                )
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
    max_concurrent_jobs = _default_max_concurrent_jobs()
    runtime = build_ingestion_runtime(
        config=config,
        notion_api_key=NOTION_API_KEY,
        tistory_blog_name=TISTORY_BLOG_NAME,
        github_token=get_env_secret(config.github_token_env_var),
    )
    runtime.metadata_store.max_concurrent_sync_jobs = max_concurrent_jobs
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
        max_concurrent_jobs=max_concurrent_jobs,
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
            "CONTEXTZIP_SYNC_WORKER_POLL_SECONDS",
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


def _prepare_private_log_path(log_path: Path) -> None:
    if not log_path.is_absolute() or Path(os.path.normpath(log_path)) != log_path:
        raise ValueError("sync worker log path must be canonical and absolute")

    current = Path(log_path.anchor)
    for component in log_path.parent.parts[1:]:
        current /= component
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            current_stat = current.lstat()
        if stat.S_ISLNK(current_stat.st_mode) or not stat.S_ISDIR(
            current_stat.st_mode
        ):
            raise ValueError("unsafe sync worker log directory")

    directory_stat = log_path.parent.lstat()
    if (
        directory_stat.st_uid != os.getuid()
        or stat.S_IMODE(directory_stat.st_mode) != 0o700
    ):
        raise PermissionError("unsafe sync worker log directory")

    try:
        file_stat = log_path.lstat()
    except FileNotFoundError:
        return
    if (
        stat.S_ISLNK(file_stat.st_mode)
        or not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_uid != os.getuid()
        or stat.S_IMODE(file_stat.st_mode) != 0o600
    ):
        raise PermissionError("unsafe sync worker log file")


def _configure_logging() -> logging.Handler:
    log_path_value = os.getenv("CONTEXTZIP_SYNC_WORKER_LOG_PATH", "").strip()
    if not log_path_value:
        handler: logging.Handler = logging.StreamHandler()
    else:
        log_path = Path(log_path_value).expanduser()
        previous_umask = os.umask(0o077)
        try:
            _prepare_private_log_path(log_path)
            handler = ByteBoundedRotatingFileHandler(
                log_path,
                maxBytes=_bounded_log_int(
                    "CONTEXTZIP_SYNC_WORKER_LOG_MAX_BYTES",
                    DEFAULT_LOG_MAX_BYTES,
                    MIN_LOG_MAX_BYTES,
                    MAX_LOG_MAX_BYTES,
                ),
                backupCount=_bounded_log_int(
                    "CONTEXTZIP_SYNC_WORKER_LOG_BACKUP_COUNT",
                    DEFAULT_LOG_BACKUP_COUNT,
                    1,
                    MAX_LOG_BACKUP_COUNT,
                ),
                encoding="utf-8",
            )
        finally:
            os.umask(previous_umask)
    handler.addFilter(WorkerLogPrivacyFilter())
    handler.setFormatter(BoundedWorkerLogFormatter(LOG_FORMAT))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    return handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the durable ContextZip sync worker."
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
