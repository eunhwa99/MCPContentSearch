from __future__ import annotations

import logging
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any

from web_console.payloads import without_persisted_output_path

logger = logging.getLogger("web_console.app")


def _smoke_temp_root() -> Path:
    root = Path(
        os.getenv("CONTEXTWIKI_WEB_CONSOLE_SMOKE_TMPDIR")
        or os.getenv("CONTEXTWIKI_SMOKE_TMPDIR")
        or os.getenv("RUNNER_TEMP")
        or tempfile.gettempdir()
    ).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


class ScriptSmokeRunner:
    """Run existing smoke helpers and keep their structured result shape."""

    async def run_fake(self, *, topic: str | None = None) -> dict[str, Any]:
        from scripts.smoke_generate_wiki_page import run_fake

        with tempfile.TemporaryDirectory(
            prefix="contextwiki-web-console-fake-", dir=_smoke_temp_root()
        ) as output_dir:
            result = await run_fake(Path(output_dir), topic or "ContextWiki citations")
        return without_persisted_output_path(result)

    async def run_github(
        self,
        *,
        topic: str | None = None,
        github_repository: str = "",
        require_generated: bool = False,
    ) -> dict[str, Any]:
        from scripts.smoke_generate_wiki_page import run_github

        with tempfile.TemporaryDirectory(
            prefix="contextwiki-web-console-github-", dir=_smoke_temp_root()
        ) as output_dir:
            args = SimpleNamespace(
                github_repository=github_repository,
                github_max_files=20,
                github_max_file_bytes=64_000,
                request_timeout=10.0,
                topic=topic or "README",
                output_dir=Path(output_dir),
                require_generated=require_generated,
            )
            result = await run_github(args)
        return without_persisted_output_path(result)


async def run_smoke(mode: str, runner_method, **kwargs) -> dict[str, Any]:
    try:
        return await runner_method(**kwargs)
    except Exception:
        _log_suppressed_error(f"Web console {mode} smoke failed")
        return {
            "mode": mode,
            "status": "failed",
            "error": "Smoke check failed. See server logs for details.",
        }


def _log_suppressed_error(message: str, exc: Exception | None = None) -> None:
    if exc is None:
        logger.error("%s; details suppressed to avoid leaking secrets", message)
        return
    logger.error(
        "%s; details suppressed to avoid leaking secrets; error_type=%s",
        message,
        type(exc).__name__,
    )
