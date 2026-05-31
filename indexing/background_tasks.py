from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable

logger = logging.getLogger(__name__)

_SENSITIVE_KEY_PATTERN = (
    r"access[-_]?key(?:[-_]?id)?|access[-_]?token|api[-_]?key|apikey|auth|"
    r"authorization|aws[-_]?access[-_]?key[-_]?id|"
    r"aws[-_]?secret[-_]?access[-_]?key|client[-_]?secret|code|cookie|"
    r"credential|csrf[-_]?token|csrf|j[-_]?session[-_]?id|jwt[-_]?token|"
    r"jwt|key|pass|password|passwd|private[-_]?key|pwd|secret[-_]?key|"
    r"secret|session[-_]?id|session[-_]?token|session|sig|signature|"
    r"sid|ssh[-_]?private[-_]?key|token|xsrf[-_]?token|xsrf|"
    r"x[-_]?amz[-_]?access[-_]?key[-_]?id|x[-_]?amz[-_]?credential"
)
_PEM_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_QUOTED_ASSIGNMENT_SECRET_PATTERN = re.compile(
    rf"(?P<prefix>['\"]?(?:{_SENSITIVE_KEY_PATTERN})['\"]?\s*[:=]\s*)"
    r"(?P<quote>['\"])(?P<secret>(?:\\.|(?!\2).)*)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_TOKEN_SECRET_PATTERN = re.compile(
    r"(gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|"
    r"xox[baprs]-[A-Za-z0-9-]+|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
    r"(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,})",
    re.IGNORECASE,
)
_MULTIWORD_ASSIGNMENT_SECRET_PATTERN = re.compile(
    rf"(?P<prefix>['\"]?(?:{_SENSITIVE_KEY_PATTERN})['\"]?\s*[:=]\s*['\"]?)"
    rf"(?P<secret>[^'\"\n,;}}]+?)(?P<suffix>['\"]?)"
    rf"(?=(?:\s+['\"]?(?:{_SENSITIVE_KEY_PATTERN})['\"]?\s*[:=])|[\n,;}}]|$)",
    re.IGNORECASE,
)
_ASSIGNMENT_SECRET_PATTERN = re.compile(
    rf"(?P<prefix>['\"]?(?:{_SENSITIVE_KEY_PATTERN})['\"]?\s*[:=]\s*['\"]?)"
    r"(?P<secret>[^'\"\s,;}]+)(?P<suffix>['\"]?)",
    re.IGNORECASE,
)
_QUERY_SECRET_PATTERN = re.compile(
    rf"(?P<prefix>[?&](?:{_SENSITIVE_KEY_PATTERN})=)(?P<secret>[^&#\s]+)",
    re.IGNORECASE,
)
_TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_error_message(error: BaseException, max_length: int = 300) -> str:
    """Return an MCP-safe error summary without obvious credential material."""
    message = str(error) or error.__class__.__name__
    message = _PEM_BLOCK_PATTERN.sub("<redacted>", message)
    message = _QUOTED_ASSIGNMENT_SECRET_PATTERN.sub(
        lambda match: f"{match.group('prefix')}{match.group('quote')}<redacted>{match.group('quote')}",
        message,
    )
    message = _TOKEN_SECRET_PATTERN.sub("<redacted>", message)
    message = _MULTIWORD_ASSIGNMENT_SECRET_PATTERN.sub(
        lambda match: f"{match.group('prefix')}<redacted>{match.group('suffix')}",
        message,
    )
    message = _ASSIGNMENT_SECRET_PATTERN.sub(
        lambda match: f"{match.group('prefix')}<redacted>{match.group('suffix')}",
        message,
    )
    message = _QUERY_SECRET_PATTERN.sub(
        lambda match: f"{match.group('prefix')}<redacted>",
        message,
    )
    if len(message) > max_length:
        message = f"{message[: max_length - 3]}..."
    return message


@dataclass
class BackgroundTaskRecord:
    task_id: str
    label: str
    state: str
    total_docs: int = 0
    processed_docs: int = 0
    error: str = ""
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""

    def model_dump(self) -> dict:
        return {
            "task_id": self.task_id,
            "label": self.label,
            "state": self.state,
            "total_docs": self.total_docs,
            "processed_docs": self.processed_docs,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class BackgroundTaskRegistry:
    """Process-local status for legacy background indexing tasks."""

    def __init__(self, max_tasks: int = 20):
        self.max_tasks = max_tasks
        self._counter = 0
        self._records: list[BackgroundTaskRecord] = []
        self._tasks: set[asyncio.Task] = set()

    def schedule(
        self,
        label: str,
        awaitable: Awaitable[int | None],
        *,
        total_docs: int = 0,
    ) -> asyncio.Task:
        self._counter += 1
        record = BackgroundTaskRecord(
            task_id=f"background-index-{self._counter}",
            label=label,
            state="queued",
            total_docs=total_docs,
            created_at=_now(),
        )
        self._records.append(record)
        self._trim()
        task = asyncio.create_task(self._run(record, awaitable))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def snapshot(self) -> list[dict]:
        return [record.model_dump() for record in self._records]

    async def _run(
        self,
        record: BackgroundTaskRecord,
        awaitable: Awaitable[int | None],
    ) -> None:
        record.state = "running"
        record.started_at = _now()
        try:
            processed = await awaitable
        except asyncio.CancelledError:
            record.state = "cancelled"
            record.error = "Background indexing task was cancelled."
            record.finished_at = _now()
            self._trim()
            raise
        except Exception as exc:
            record.state = "failed"
            record.error = safe_error_message(exc)
            record.finished_at = _now()
            logger.error("Background indexing task failed: %s", record.error)
            self._trim()
            return

        processed_docs = processed if isinstance(processed, int) else record.total_docs
        record.processed_docs = max(0, processed_docs)
        if record.total_docs == 0:
            record.total_docs = record.processed_docs
        record.state = "succeeded"
        record.finished_at = _now()
        self._trim()

    def _trim(self) -> None:
        while len(self._records) > self.max_tasks:
            for index, record in enumerate(self._records):
                if record.state in _TERMINAL_STATES:
                    del self._records[index]
                    break
            else:
                break


_DEFAULT_BACKGROUND_TASK_REGISTRY = BackgroundTaskRegistry()


def get_default_background_task_registry() -> BackgroundTaskRegistry:
    return _DEFAULT_BACKGROUND_TASK_REGISTRY
