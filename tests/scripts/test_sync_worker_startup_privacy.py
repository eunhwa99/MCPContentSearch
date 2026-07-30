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
                "    'Cookie: session=alpha; theme=private; preference=hidden\\n'",
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


def test_launch_agent_runner_redacts_folded_authorization_credentials(
    tmp_path: Path,
):
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "\n".join(
            (
                f"#!{sys.executable}",
                "import sys",
                "sys.stderr.write('Authorization: Bearer\\r\\n')",
                "sys.stderr.write(' folded-startup-bearer-credential\\r\\n')",
                "sys.stderr.write(",
                "    'first clear diagnostic source_id=source_notion '",
                "    'job_id=job-123\\r'",
                ")",
                "sys.stderr.write('Authorization: Basic\\r')",
                "sys.stderr.write('\\tfolded-startup-basic-credential\\r')",
                "sys.stderr.write(",
                "    'second clear diagnostic phase=starting '",
                "    'retry_count=26\\n'",
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
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "4096",
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
    assert diagnostic_log.stat().st_size <= 4096
    assert "folded-startup-bearer-credential" not in diagnostic
    assert "folded-startup-basic-credential" not in diagnostic
    assert "first clear diagnostic" in diagnostic
    assert "source_id=source_notion" in diagnostic
    assert "job_id=job-123" in diagnostic
    assert "second clear diagnostic" in diagnostic
    assert "phase=starting" in diagnostic
    assert "retry_count=26" in diagnostic


def test_launch_agent_runner_redacts_multistage_folded_authorization_credentials(
    tmp_path: Path,
):
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "\n".join(
            (
                f"#!{sys.executable}",
                "import sys",
                "sys.stderr.write('Authorization:\\r\\n')",
                "sys.stderr.write(' Bearer\\r\\n')",
                "sys.stderr.write(' multistage-startup-bearer-credential\\r\\n')",
                "sys.stderr.write(",
                "    'first clear diagnostic source_id=source_notion '",
                "    'job_id=job-123\\r'",
                ")",
                "sys.stderr.write('Authorization=\\r')",
                "sys.stderr.write('\\tBasic\\r')",
                "sys.stderr.write('\\tmultistage-startup-basic-credential\\r')",
                "sys.stderr.write(",
                "    'second clear diagnostic phase=starting '",
                "    'retry_count=28\\n'",
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
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "4096",
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
    assert diagnostic_log.stat().st_size <= 4096
    assert "multistage-startup-bearer-credential" not in diagnostic
    assert "multistage-startup-basic-credential" not in diagnostic
    assert "first clear diagnostic" in diagnostic
    assert "source_id=source_notion" in diagnostic
    assert "job_id=job-123" in diagnostic
    assert "second clear diagnostic" in diagnostic
    assert "phase=starting" in diagnostic
    assert "retry_count=28" in diagnostic


def test_launch_agent_runner_redacts_bare_name_folded_authorization_credentials(
    tmp_path: Path,
):
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "\n".join(
            (
                f"#!{sys.executable}",
                "import sys",
                "sys.stderr.write('Authorization\\r\\n')",
                "sys.stderr.write(' Bearer\\r\\n')",
                "sys.stderr.write(' bare-name-startup-bearer-credential\\r\\n')",
                "sys.stderr.write(",
                "    'first clear diagnostic source_id=source_notion '",
                "    'job_id=job-123\\r'",
                ")",
                "sys.stderr.write('Authorization\\r')",
                "sys.stderr.write('\\tBasic\\r')",
                "sys.stderr.write('\\tbare-name-startup-basic-credential\\r')",
                "sys.stderr.write(",
                "    'second clear diagnostic phase=starting '",
                "    'retry_count=34\\n'",
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
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "4096",
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
    assert diagnostic_log.stat().st_size <= 4096
    assert "bare-name-startup-bearer-credential" not in diagnostic
    assert "bare-name-startup-basic-credential" not in diagnostic
    assert "first clear diagnostic" in diagnostic
    assert "source_id=source_notion" in diagnostic
    assert "job_id=job-123" in diagnostic
    assert "second clear diagnostic" in diagnostic
    assert "phase=starting" in diagnostic
    assert "retry_count=34" in diagnostic


def test_launch_agent_runner_redacts_oversized_cookie_header_and_folded_continuation(
    tmp_path: Path,
):
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "\n".join(
            (
                f"#!{sys.executable}",
                "import sys",
                "sys.stderr.write(",
                "    'Cookie: source_id=cookie-source-secret; padding='",
                "    + ('x' * 70000)",
                "    + '\\n'",
                ")",
                "sys.stderr.write(",
                "    '\\tjob_id=folded-cookie-secret; '",
                "    'phase=folded-phase-secret\\n'",
                ")",
                "sys.stderr.write(",
                "    'ordinary diagnostic, source_id=source_notion; '",
                "    'job_id=job-123\\n'",
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
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "4096",
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
    assert diagnostic_log.stat().st_size <= 4096
    assert "cookie-source-secret" not in diagnostic
    assert "folded-cookie-secret" not in diagnostic
    assert "folded-phase-secret" not in diagnostic
    assert "<redacted oversized diagnostic>" in diagnostic
    assert "source_id=source_notion" in diagnostic
    assert "job_id=job-123" in diagnostic


def test_launch_agent_runner_redacts_cookie_separator_split_across_oversized_chunks(
    tmp_path: Path,
):
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "\n".join(
            (
                f"#!{sys.executable}",
                "import sys",
                "sys.stderr.write(",
                "    'Set-Cookie'",
                "    + (' ' * 65540)",
                "    + ': source_id=cookie-source-secret\\n'",
                ")",
                "sys.stderr.write(",
                "    '\\tjob_id=folded-cookie-secret; '",
                "    'phase=folded-phase-secret\\n'",
                ")",
                "sys.stderr.write(",
                "    'ordinary diagnostic, source_id=source_notion; '",
                "    'job_id=job-123\\n'",
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
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "4096",
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
    assert diagnostic_log.stat().st_size <= 4096
    assert "cookie-source-secret" not in diagnostic
    assert "folded-cookie-secret" not in diagnostic
    assert "folded-phase-secret" not in diagnostic
    assert "<redacted oversized diagnostic>" in diagnostic
    assert "source_id=source_notion" in diagnostic
    assert "job_id=job-123" in diagnostic


def test_launch_agent_runner_redacts_folded_credentials_when_indent_and_body_split_at_production_boundary(
    tmp_path: Path,
):
    folded_secrets = (
        "startup-split-authorization-credential-987654",
        "startup-split-cookie-credential-987654",
    )
    messages = []
    for header_prefix, folded_secret in (
        ("Authorization: Bearer initial-credential-", folded_secrets[0]),
        ("Cookie: session=initial-cookie; padding=", folded_secrets[1]),
    ):
        padding = "x" * (65535 - len(header_prefix))
        messages.append(
            f"{header_prefix}{padding}\r {folded_secret}\r"
            "clear diagnostic source_id=source_notion job_id=job-123\n"
        )

    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "\n".join(
            (
                f"#!{sys.executable}",
                "import sys",
                f"sys.stderr.write({''.join(messages)!r})",
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
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "4096",
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
    assert diagnostic_log.stat().st_size <= 4096
    assert all(secret not in diagnostic for secret in folded_secrets)
    assert diagnostic.count("<redacted oversized diagnostic>") == 2
    assert diagnostic.count("clear diagnostic") == 2
    assert diagnostic.count("source_id=source_notion") == 2
    assert diagnostic.count("job_id=job-123") == 2


def test_launch_agent_runner_redacts_folded_credentials_when_crlf_is_split_at_production_boundary(
    tmp_path: Path,
):
    folded_secrets = (
        "startup-boundary-crlf-authorization-credential-987654",
        "startup-boundary-crlf-cookie-credential-987654",
    )
    messages = []
    for header_prefix, folded_secret in (
        ("Authorization: Bearer initial-credential-", folded_secrets[0]),
        ("Cookie: session=initial-cookie; padding=", folded_secrets[1]),
    ):
        padding = "x" * (65536 - len(header_prefix))
        messages.append(
            f"{header_prefix}{padding}\r\n {folded_secret}\r\n"
            "clear diagnostic source_id=source_notion job_id=job-123\n"
        )

    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "\n".join(
            (
                f"#!{sys.executable}",
                "import sys",
                f"sys.stderr.write({''.join(messages)!r})",
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
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "4096",
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
    assert diagnostic_log.stat().st_size <= 4096
    assert all(secret not in diagnostic for secret in folded_secrets)
    assert diagnostic.count("<redacted oversized diagnostic>") == 2
    assert diagnostic.count("clear diagnostic") == 2
    assert diagnostic.count("source_id=source_notion") == 2
    assert diagnostic.count("job_id=job-123") == 2


def test_launch_agent_runner_redacts_name_only_cookie_header_and_folded_value(
    tmp_path: Path,
):
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "\n".join(
            (
                f"#!{sys.executable}",
                "import sys",
                "sys.stderr.write('Set-Cookie\\n')",
                "sys.stderr.write(",
                "    '\\t: source_id=cookie-source-secret; '",
                "    'job_id=cookie-job-secret\\n'",
                ")",
                "sys.stderr.write(",
                "    'ordinary diagnostic, source_id=source_notion; '",
                "    'job_id=job-123\\n'",
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
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "4096",
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
    assert diagnostic_log.stat().st_size <= 4096
    assert "cookie-source-secret" not in diagnostic
    assert "cookie-job-secret" not in diagnostic
    assert "source_id=source_notion" in diagnostic
    assert "job_id=job-123" in diagnostic
    assert "ordinary diagnostic" in diagnostic


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
    diagnostic_log.parent.mkdir(mode=0o700)
    diagnostic_log.write_text("x" * 1020, encoding="utf-8")
    diagnostic_log.chmod(0o600)
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
