from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_SCRIPT = REPO_ROOT / "scripts" / "run_sync_worker_launch_agent.sh"


def _minimal_runner_path(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "minimal-bin"
    fake_bin.mkdir()
    for command in (
        "cat",
        "chmod",
        "dd",
        "dirname",
        "mkdir",
        "mkfifo",
        "mktemp",
        "mv",
        "rm",
        "tail",
        "touch",
        "wc",
    ):
        resolved = shutil.which(command)
        assert resolved is not None
        (fake_bin / command).symlink_to(resolved)
    return fake_bin


def test_launch_agent_runner_sanitizes_startup_stderr_before_persisting(
    tmp_path: Path,
):
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "\n".join(
            (
                f"#!{sys.executable}",
                "import sys",
                "sys.stderr.write(",
                "    'worker bootstrap failed, job_id=job-123\\n'",
                "    'Authorization: Bearer startup-first startup-secret, '",
                "    'source_id=source_notion\\n'",
                "    'Cookie: session=alpha; theme=private; preference=hidden, '",
                "    'retry_count=1\\n'",
                "    'token=raw-startup-token-value, '",
                r"    'path=\\\\server\\private share\\meeting notes.md\\n'",
                ")",
                "raise SystemExit(23)",
                "",
            )
        ),
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    diagnostic_log = tmp_path / "logs" / "startup.log"
    env = {
        **os.environ,
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_PATH": str(diagnostic_log),
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "1024",
        "CONTEXTWIKI_SYNC_WORKER_SANITIZER_PYTHON_PATH": sys.executable,
    }

    result = subprocess.run(
        [str(RUN_SCRIPT), str(fake_uv), str(REPO_ROOT)],
        check=False,
        env=env,
        timeout=10,
    )

    diagnostic = diagnostic_log.read_text(encoding="utf-8")
    assert result.returncode == 23
    assert diagnostic_log.stat().st_size <= 1024
    assert "worker bootstrap failed" in diagnostic
    assert "job_id=job-123" in diagnostic
    assert "source_id=source_notion" in diagnostic
    assert "retry_count=1" in diagnostic
    assert "<redacted>" in diagnostic
    for raw_value in (
        "startup-first",
        "startup-secret",
        "session=alpha",
        "theme=private",
        "preference=hidden",
        "raw-startup-token-value",
        r"\\server\private share\meeting notes.md",
        "meeting notes.md",
    ):
        assert raw_value not in diagnostic


def test_launch_agent_runner_uses_uv_managed_sanitizer_without_path_python(
    tmp_path: Path,
):
    fake_bin = _minimal_runner_path(tmp_path)
    worker_started = tmp_path / "worker-started"
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "\n".join(
            (
                "#!/bin/sh",
                'case "$*" in',
                "  *core.error_sanitizer*)",
                (
                    f"    exec {str(sys.executable)!r} -u "
                    "-m core.error_sanitizer --stream"
                ),
                "    ;;",
                "esac",
                f"touch {str(worker_started)!r}",
                "printf 'worker started safely\\n' >&2",
                "exit 0",
                "",
            )
        ),
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    diagnostic_log = tmp_path / "logs" / "startup.log"
    env = {
        **os.environ,
        "PATH": str(fake_bin),
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_PATH": str(diagnostic_log),
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "1024",
    }
    env.pop("CONTEXTWIKI_SYNC_WORKER_SANITIZER_PYTHON_PATH", None)

    result = subprocess.run(
        ["/bin/bash", str(RUN_SCRIPT), str(fake_uv), str(REPO_ROOT)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert worker_started.exists()
    assert "worker started safely" in diagnostic_log.read_text(encoding="utf-8")


def test_launch_agent_runner_reports_safe_bounded_error_when_sanitizer_fails(
    tmp_path: Path,
):
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        "printf 'Authorization: Bearer must-not-persist secret-tail\\n' >&2\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    failing_sanitizer = tmp_path / "failing-sanitizer"
    failing_sanitizer.write_text(
        f"#!{sys.executable}\n"
        "raise SystemExit(73)\n",
        encoding="utf-8",
    )
    failing_sanitizer.chmod(0o755)
    diagnostic_log = tmp_path / "logs" / "startup.log"
    diagnostic_log.parent.mkdir()
    diagnostic_log.write_text("x" * 1020, encoding="utf-8")
    env = {
        **os.environ,
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_PATH": str(diagnostic_log),
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "1024",
        "CONTEXTWIKI_SYNC_WORKER_SANITIZER_PYTHON_PATH": str(failing_sanitizer),
    }

    result = subprocess.run(
        [str(RUN_SCRIPT), str(fake_uv), str(REPO_ROOT)],
        check=False,
        env=env,
        timeout=10,
    )

    diagnostic = diagnostic_log.read_text(encoding="utf-8")
    assert result.returncode != 0
    assert diagnostic_log.stat().st_size <= 1024
    assert diagnostic.endswith("error: startup diagnostic sanitizer failed\n")
    assert "must-not-persist" not in diagnostic
    assert "secret-tail" not in diagnostic
