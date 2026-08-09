#!/usr/bin/env python3
"""Watch a sync job's upstream progress from the local metadata DB.

Usage examples:
  python scripts/watch_sync_progress.py --source-id source_github
  python scripts/watch_sync_progress.py --source-id source_github --job-id job-123
  python scripts/watch_sync_progress.py --source-id source_github --db-path /path/to/context_zip_metadata.sqlite3 --poll 2.0
  python scripts/watch_sync_progress.py --source-id source_github --job-id job-123 --live
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime
import sys
from pathlib import Path


def _ensure_repo_root_on_sys_path() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


_ensure_repo_root_on_sys_path()

from environments.config import AppConfig
from storage.metadata_store import MetadataStore


def _format_bar(done: int, total: int, width: int = 24) -> str:
    if total <= 0:
        return "[" + " " * width + "]"
    clamped_total = max(total, 1)
    ratio = max(0.0, min(done / clamped_total, 1.0))
    filled = int(ratio * width)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _latest_text_status(total: int, done: int, phase: str, message: str) -> str:
    if total <= 0:
        return (
            f"phase={phase or '-'} "
            f"state: waiting/discovering upstream (total unknown yet)"
        )
    remaining = max(total - done, 0)
    pct = (done / total * 100.0) if total else 0.0
    return (
        f"phase={phase or '-'} "
        f"upstream={done}/{total} "
        f"({pct:5.1f}%) "
        f"remaining={remaining} "
        f"msg={message or '-'}"
    )


def _status_line(
    job,
    bar_width: int = 24,
    *,
    include_elapsed: bool = False,
    started_at: float | None = None,
) -> str:
    upstream_total = int(getattr(job, "upstream_total", 0) or 0)
    upstream_done = int(getattr(job, "upstream_done", 0) or 0)
    phase = str(getattr(job, "phase", "") or "")
    message = str(getattr(job, "status_message", "") or "")
    status = str(getattr(job, "status", "") or "")
    bar = _format_bar(upstream_done, max(upstream_total, 1), width=bar_width)
    details = _latest_text_status(upstream_total, upstream_done, phase, message)
    elapsed = ""
    if include_elapsed and started_at is not None:
        elapsed_seconds = time.time() - started_at
        elapsed = f" | elapsed={elapsed_seconds:0.1f}s"
    return f"{bar} {details} | status={status}{elapsed}"


def _completion_summary(job, started_at: float) -> str:
    upstream_total = int(getattr(job, "upstream_total", 0) or 0)
    upstream_done = int(getattr(job, "upstream_done", 0) or 0)
    status = str(getattr(job, "status", "") or "")
    elapsed_seconds = time.time() - started_at
    if upstream_total <= 0:
        percent = 0.0
    else:
        percent = min(100.0, (upstream_done / upstream_total) * 100.0)
    return (
        f"status={status} upstream_done={upstream_done} "
        f"upstream_total={upstream_total} ({percent:.1f}%) "
        f"elapsed={elapsed_seconds:0.1f}s"
    )


def watch(
    *,
    source_id: str,
    job_id: str | None,
    db_path: Path,
    poll_seconds: float,
    timeout_seconds: float,
    bar_width: int,
    live_mode: bool,
) -> int:
    store = MetadataStore(db_path)
    deadline = time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
    started_at = time.time()
    last_printed = ""

    while True:
        job = store.get_sync_job(job_id) if job_id else store.get_latest_sync_job(source_id)
        if job is None:
            print(f"No matching job found for source_id={source_id} job_id={job_id}")
            return 2

        status = str(getattr(job, "status", "") or "")
        line = _status_line(
            job,
            bar_width=bar_width,
            include_elapsed=True,
            started_at=started_at,
        )

        if live_mode:
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"{timestamp} {line}")
        elif line != last_printed:
            print(f"\r{line}", end="", flush=True)
            last_printed = line

        if status not in {"running", "queued", "starting"}:
            if live_mode:
                print(_completion_summary(job, started_at=started_at))
            else:
                print(f"\n{_completion_summary(job, started_at=started_at)}")
            return 0

        if deadline is not None and time.monotonic() > deadline:
            print("\nTimed out while waiting for sync job to finish")
            print(_completion_summary(job, started_at=started_at))
            return 1

        time.sleep(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--job-id", default="")
    parser.add_argument("--db-path", default="")
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=0.0)
    parser.add_argument("--bar-width", type=int, default=24)
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Always emit one status line per poll (real-time streaming), "
            "instead of only printing on changed output."
        ),
    )
    args = parser.parse_args()

    if args.poll <= 0:
        print("--poll must be > 0")
        return 2

    if args.db_path:
        db_path = Path(args.db_path)
    else:
        db_path = AppConfig().metadata_db_path

    if not db_path.exists():
        print(f"metadata db not found: {db_path}")
        return 2

    return watch(
        source_id=args.source_id,
        job_id=args.job_id or None,
        db_path=db_path,
        poll_seconds=args.poll,
        timeout_seconds=args.timeout,
        bar_width=max(8, args.bar_width),
        live_mode=args.live,
    )


if __name__ == "__main__":
    raise SystemExit(main())
