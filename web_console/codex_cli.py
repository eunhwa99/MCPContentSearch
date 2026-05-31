from __future__ import annotations

import asyncio
from contextlib import suppress
import os
from pathlib import Path
import shutil
import signal
import tempfile
from typing import Any

from web_console.payloads import normalize_multiline, redact_prompt_text


CODEX_DISABLED_FEATURES = (
    "apps",
    "auth_elicitation",
    "shell_tool",
    "shell_snapshot",
    "unified_exec",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "in_app_browser",
    "image_generation",
    "memories",
    "plugins",
    "plugin_hooks",
    "multi_agent",
    "tool_call_mcp_elicitation",
    "workspace_dependencies",
)


class CodexCliExecutionError(RuntimeError):
    def __init__(self, safe_message: str):
        super().__init__("codex cli failed")
        self.safe_message = safe_message


async def run_codex_cli(
    prompt: str,
    *,
    timeout_seconds: float,
    codex_binary: str,
) -> str:
    binary = shutil.which(codex_binary)
    if not binary:
        raise FileNotFoundError(codex_binary)

    output_path = ""
    sandbox_profile_path = ""
    work_dir = ""
    process = None
    try:
        work_dir = tempfile.mkdtemp(
            prefix="contextwiki-codex-work-",
            dir="/private/tmp",
        )
        with tempfile.NamedTemporaryFile(
            prefix="contextwiki-codex-answer-",
            suffix=".txt",
            dir="/private/tmp",
            delete=False,
        ) as output_file:
            output_path = output_file.name

        command_args = codex_exec_args(binary, work_dir, output_path)
        sandbox_requested = use_codex_sandbox_exec()
        sandbox_exec = shutil.which("sandbox-exec") if sandbox_requested else None
        if sandbox_requested and not sandbox_exec:
            raise CodexCliExecutionError(
                "Codex CLI macOS sandbox was requested but sandbox-exec is not available. "
                "Disable CONTEXTWIKI_CODEX_USE_SANDBOX_EXEC or use ContextWiki Answer mode."
            )
        if sandbox_exec:
            sandbox_profile_path = write_codex_sandbox_profile(
                binary=binary,
                work_dir=work_dir,
                output_path=output_path,
            )
            command_args = [
                sandbox_exec,
                "-f",
                sandbox_profile_path,
                *command_args,
            ]

        process = await asyncio.create_subprocess_exec(
            *command_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=codex_subprocess_env(),
            cwd=work_dir,
            start_new_session=True,
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(prompt.encode("utf-8")),
            timeout=timeout_seconds,
        )
        if process.returncode != 0:
            raise CodexCliExecutionError(safe_codex_failure_message(stderr))
        if output_path:
            try:
                output = Path(output_path).read_text(encoding="utf-8")
            except FileNotFoundError:
                output = ""
        else:
            output = ""
        return output.strip() or stdout.decode("utf-8", errors="replace").strip()
    except TimeoutError:
        await stop_codex_process(process)
        raise
    except asyncio.CancelledError:
        await stop_codex_process(process)
        raise
    finally:
        if output_path:
            try:
                os.unlink(output_path)
            except FileNotFoundError:
                pass
        if sandbox_profile_path:
            try:
                os.unlink(sandbox_profile_path)
            except FileNotFoundError:
                pass
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


async def stop_codex_process(process: Any) -> None:
    if process and process.returncode is None:
        terminate_process_group(process.pid)
        with suppress(Exception):
            await asyncio.wait_for(process.wait(), timeout=2)
        if process.returncode is None:
            kill_process_group(process.pid)
            with suppress(Exception):
                await process.wait()


def use_codex_sandbox_exec() -> bool:
    return str(os.environ.get("CONTEXTWIKI_CODEX_USE_SANDBOX_EXEC", "")).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def codex_exec_args(binary: str, work_dir: str, output_path: str) -> list[str]:
    return [
        binary,
        "exec",
        *codex_disabled_feature_args(),
        "--ephemeral",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--ignore-rules",
        "--sandbox",
        "read-only",
        "--cd",
        work_dir,
        "--output-last-message",
        output_path,
        "--color",
        "never",
        "-",
    ]


def write_codex_sandbox_profile(*, binary: str, work_dir: str, output_path: str) -> str:
    with tempfile.NamedTemporaryFile(
        prefix="contextwiki-codex-sandbox-",
        suffix=".sb",
        dir="/private/tmp",
        mode="w",
        encoding="utf-8",
        delete=False,
    ) as profile_file:
        profile_file.write(codex_sandbox_profile(binary, work_dir, output_path))
        return profile_file.name


def codex_sandbox_profile(binary: str, work_dir: str, output_path: str) -> str:
    codex_env = codex_subprocess_env()
    codex_home = codex_env.get("CODEX_HOME")
    home = codex_env.get("HOME") or str(Path.home())

    read_paths = [
        "/bin",
        "/System",
        "/usr",
        binary,
        work_dir,
        output_path,
    ]
    if Path("/Library").exists():
        read_paths.append("/Library")
    if Path("/opt/homebrew").exists():
        read_paths.append("/opt/homebrew")
    if codex_home:
        read_paths.append(codex_home)
    elif home:
        read_paths.append(str(Path(home) / ".codex"))

    write_paths = [work_dir, output_path]

    for env_key in ("TMPDIR", "TEMP", "TMP", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
        env_path = codex_env.get(env_key)
        if env_path:
            read_paths.append(env_path)

    return "\n".join(
        [
            "(version 1)",
            "(deny default)",
            "(allow process*)",
            "(allow sysctl-read)",
            "(allow mach-lookup)",
            "(allow network-outbound)",
            f"(allow file-read* {sandbox_path_filters(read_paths)})",
            f"(allow file-write* {sandbox_path_filters(write_paths)})",
            "",
        ]
    )


def sandbox_path_filters(paths: list[str]) -> str:
    filters = []
    for path in dict.fromkeys(paths):
        if not path:
            continue
        normalized = str(Path(path))
        predicate = "subpath" if Path(normalized).is_dir() else "literal"
        filters.append(f"({predicate} {sandbox_quote(normalized)})")
    return " ".join(filters)


def sandbox_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def codex_subprocess_env() -> dict[str, str]:
    allowed_keys = {
        "CODEX_HOME",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "PATH",
        "SHELL",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "USER",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
    }
    return {
        key: value
        for key, value in os.environ.items()
        if key in allowed_keys and value
    }


def codex_disabled_feature_args() -> list[str]:
    args = []
    for feature in CODEX_DISABLED_FEATURES:
        args.extend(["--disable", feature])
    return args


def bounded_prompt_field(value: Any, *, limit: int) -> str:
    text = redact_prompt_text(value)
    return text[: max(1, limit)]


def codex_prompt_char_budget(max_chunks: int, max_chunk_chars: int) -> int:
    return 2_500 + max_chunks * (max_chunk_chars + 1_200)


def terminate_process_group(pid: int) -> None:
    with suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGTERM)


def kill_process_group(pid: int) -> None:
    with suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


def safe_codex_failure_message(stderr: bytes | str) -> str:
    raw_text = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr
    text = normalize_multiline(raw_text)
    lowered = text.lower()
    if (
        "failed to initialize in-process app-server client" in lowered
        or "attempt to write a readonly database" in lowered
    ):
        return (
            "Codex CLI could not initialize from this server process. "
            "If the Web Console is running inside the Codex desktop sandbox, "
            "start it from a normal terminal or use ContextWiki Answer mode."
        )
    return "Codex CLI answer failed. See server logs for details."
