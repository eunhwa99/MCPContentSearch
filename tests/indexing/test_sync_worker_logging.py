from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import stat

import httpx
import pytest

from environments.config import AppConfig, NotionConfig
from fetching.notion import NotionAPIClient
from indexing.indexer import ContentIndexer
from indexing.ingestion_service import IngestionService
from indexing.sync_worker import _configure_logging, _redact_worker_log_message


class _RecordingCollection:
    def delete(self, **kwargs):
        return None


def _close_test_logging(
    root_logger: logging.Logger,
    previous_handlers: list[logging.Handler],
    previous_level: int,
) -> None:
    for handler in list(root_logger.handlers):
        handler.close()
    root_logger.handlers[:] = previous_handlers
    root_logger.setLevel(previous_level)


def test_worker_logging_recreates_private_runtime_directory_and_log(
    monkeypatch,
    tmp_path: Path,
):
    log_path = tmp_path / "deleted-after-install" / "sync-worker.log"
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_PATH", str(log_path))
    root_logger = logging.getLogger()
    previous_handlers = list(root_logger.handlers)
    previous_level = root_logger.level
    previous_umask = os.umask(0o022)

    try:
        handler = _configure_logging()
        handler.flush()

        assert stat.S_IMODE(log_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
        assert log_path.parent.stat().st_uid == os.getuid()
        assert log_path.stat().st_uid == os.getuid()
    finally:
        os.umask(previous_umask)
        _close_test_logging(root_logger, previous_handlers, previous_level)


@pytest.mark.parametrize(
    "unsafe_kind",
    ("symlink_directory", "symlink_file", "non_directory", "wrong_owner"),
)
def test_worker_logging_rejects_unsafe_runtime_log_paths_without_mutating_targets(
    monkeypatch,
    tmp_path: Path,
    unsafe_kind: str,
):
    target_dir = tmp_path / "target"
    target_dir.mkdir(mode=0o755)
    target_file = target_dir / "target.log"
    target_file.write_text("retained target\n", encoding="utf-8")
    target_file.chmod(0o644)
    expected_dir_mode = stat.S_IMODE(target_dir.stat().st_mode)
    expected_file_mode = stat.S_IMODE(target_file.stat().st_mode)
    expected_content = target_file.read_bytes()

    if unsafe_kind == "symlink_directory":
        log_dir = tmp_path / "linked-dir"
        log_dir.symlink_to(target_dir, target_is_directory=True)
        log_path = log_dir / "sync-worker.log"
    elif unsafe_kind == "symlink_file":
        log_dir = tmp_path / "logs"
        log_dir.mkdir(mode=0o700)
        log_path = log_dir / "sync-worker.log"
        log_path.symlink_to(target_file)
    elif unsafe_kind == "non_directory":
        log_dir = tmp_path / "not-a-directory"
        log_dir.write_text("retained parent\n", encoding="utf-8")
        log_path = log_dir / "sync-worker.log"
    else:
        log_dir = tmp_path / "foreign-owned"
        log_dir.mkdir(mode=0o700)
        log_path = log_dir / "sync-worker.log"
        monkeypatch.setattr(os, "getuid", lambda: target_dir.stat().st_uid + 1)

    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_PATH", str(log_path))
    root_logger = logging.getLogger()
    previous_handlers = list(root_logger.handlers)
    previous_level = root_logger.level

    try:
        with pytest.raises((OSError, ValueError)):
            _configure_logging()
    finally:
        _close_test_logging(root_logger, previous_handlers, previous_level)

    assert target_dir.is_dir()
    assert stat.S_IMODE(target_dir.stat().st_mode) == expected_dir_mode
    assert target_file.read_bytes() == expected_content
    assert stat.S_IMODE(target_file.stat().st_mode) == expected_file_mode


def test_worker_log_redactor_removes_complete_multiword_credentials():
    cases = (
        (
            "Authorization: Bearer bearer-first bearer-trailing-secret",
            ("bearer-first", "bearer-trailing-secret"),
        ),
        (
            "Authorization=Basic Zm9vOmJhcg== basic-trailing-secret",
            ("Zm9vOmJhcg==", "basic-trailing-secret"),
        ),
        (
            "Cookie: session=alpha theme=private preference=hidden",
            ("session=alpha", "theme=private", "preference=hidden"),
        ),
        (
            "api_key=first-segment second-segment third-secret",
            ("first-segment", "second-segment", "third-secret"),
        ),
    )

    for message, raw_secrets in cases:
        redacted = _redact_worker_log_message(message)
        assert "<redacted" in redacted
        assert all(secret not in redacted for secret in raw_secrets)


def test_worker_log_redactor_removes_bare_notion_tokens_and_paths_with_spaces():
    notion_tokens = (
        "ntn_abcdefghijklmnopqrstuvwxyz0123456789",
        "secret_abcdefghijklmnopqrstuvwxyz0123456789",
    )
    sensitive_paths = (
        "/Users/tester/private vault/meeting notes.md",
        r"C:\Users\tester\private vault\meeting notes.md",
    )

    redacted = _redact_worker_log_message(
        "Provider failure "
        f"{notion_tokens[0]} {notion_tokens[1]} "
        f"unix={sensitive_paths[0]}, windows={sensitive_paths[1]}"
    )

    assert "<redacted" in redacted
    assert all(token not in redacted for token in notion_tokens)
    assert all(path not in redacted for path in sensitive_paths)
    assert "meeting notes.md" not in redacted


def test_worker_log_redactor_removes_delimiter_path_suffixes_and_keeps_fields():
    sensitive_paths = (
        "/Users/tester/private,vault;meeting notes.md",
        r"C:\Users\tester\private,vault;meeting notes.md",
    )

    redacted = _redact_worker_log_message(
        "Provider failure "
        f"unix={sensitive_paths[0]}, job_id=job-123; "
        f"windows={sensitive_paths[1]}; source_id=source_notion"
    )

    assert all(path not in redacted for path in sensitive_paths)
    assert "vault;meeting notes.md" not in redacted
    assert "job_id=job-123" in redacted
    assert "source_id=source_notion" in redacted


def test_worker_log_redactor_removes_colon_labeled_paths_and_keeps_fields():
    sensitive_paths = (
        "/Users/tester/private,vault;meeting notes.md",
        r"C:\Users\tester\private,vault;meeting notes.md",
    )

    redacted = _redact_worker_log_message(
        f"path:{sensitive_paths[0]}, job_id=job-123; "
        f"file:{sensitive_paths[1]}; source_id=source_notion"
    )

    assert all(path not in redacted for path in sensitive_paths)
    assert "vault;meeting notes.md" not in redacted
    assert "job_id=job-123" in redacted
    assert "source_id=source_notion" in redacted
    assert "path:<redacted>" in redacted
    assert "file:<redacted>" in redacted


def test_worker_log_redactor_removes_short_explicit_auth_credentials():
    bearer_redacted = _redact_worker_log_message(
        "Bearer abc123 while syncing, source_id=s diagnostic padding"
    )
    basic_redacted = _redact_worker_log_message(
        "Basic Og== because retrying; job_id=j diagnostic padding"
    )

    assert "abc123" not in bearer_redacted
    assert "Bearer <redacted-auth> while syncing," in bearer_redacted
    assert "source_id=s" in bearer_redacted
    assert "Og==" not in basic_redacted
    assert "Basic <redacted-auth> because retrying;" in basic_redacted
    assert "job_id=j" in basic_redacted


def test_worker_log_redactor_removes_folded_authorization_credentials():
    redacted = _redact_worker_log_message(
        "Authorization: Bearer\r\n"
        " folded-worker-bearer-credential\r\n"
        "first clear diagnostic source_id=source_notion job_id=job-123\r"
        "Authorization: Basic\r"
        "\tfolded-worker-basic-credential\r"
        "second clear diagnostic phase=fetching_page_content retry_count=26"
    )

    assert "folded-worker-bearer-credential" not in redacted
    assert "folded-worker-basic-credential" not in redacted
    assert "first clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted
    assert "second clear diagnostic" in redacted
    assert "phase=fetching_page_content" in redacted
    assert "retry_count=26" in redacted


def test_worker_log_redactor_removes_multistage_folded_authorization_credentials():
    redacted = _redact_worker_log_message(
        "Authorization:\r\n"
        " Bearer\r\n"
        " multistage-worker-bearer-credential\r\n"
        "first clear diagnostic source_id=source_notion job_id=job-123\r"
        "Authorization=\r"
        "\tBasic\r"
        "\tmultistage-worker-basic-credential\r"
        "second clear diagnostic phase=fetching_page_content retry_count=28"
    )

    assert "multistage-worker-bearer-credential" not in redacted
    assert "multistage-worker-basic-credential" not in redacted
    assert "first clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted
    assert "second clear diagnostic" in redacted
    assert "phase=fetching_page_content" in redacted
    assert "retry_count=28" in redacted


def test_worker_log_redactor_removes_bare_name_folded_authorization_credentials():
    redacted = _redact_worker_log_message(
        "Authorization\r\n"
        " Bearer\r\n"
        " bare-name-worker-bearer-credential\r\n"
        "first clear diagnostic source_id=source_notion job_id=job-123\r"
        "Authorization\r"
        "\tBasic\r"
        "\tbare-name-worker-basic-credential\r"
        "second clear diagnostic phase=fetching_page_content retry_count=34"
    )

    assert "bare-name-worker-bearer-credential" not in redacted
    assert "bare-name-worker-basic-credential" not in redacted
    assert "first clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted
    assert "second clear diagnostic" in redacted
    assert "phase=fetching_page_content" in redacted
    assert "retry_count=34" in redacted


def test_worker_log_redactor_preserves_lone_cr_clear_diagnostic_after_path():
    sensitive_path = "/Users/tester/private vault/observability notes.md"
    for path_prefix in ("path:", ""):
        redacted = _redact_worker_log_message(
            f"provider failure {path_prefix}{sensitive_path}\r"
            "clear diagnostic source_id=source_notion "
            "job_id=job-123 retry_count=25"
        )

        assert sensitive_path not in redacted
        assert "observability notes.md" not in redacted
        assert "clear diagnostic" in redacted
        assert "source_id=source_notion" in redacted
        assert "job_id=job-123" in redacted
        assert "retry_count=25" in redacted


def test_worker_log_redactor_removes_semicolon_cookie_headers_and_unc_paths():
    raw_values = (
        "session=alpha",
        "theme=private",
        "preference=hidden",
        "sid=bravo",
        "Path=/private",
        r"\\server\private share\meeting notes.md",
        r"\\?\C:\Users\tester\private vault\meeting notes.md",
    )

    redacted = _redact_worker_log_message(
        "Cookie: session=alpha; theme=private; preference=hidden\n"
        "job_id=job-123\n"
        "Set-Cookie: sid=bravo; Path=/private; HttpOnly\n"
        "source_id=source_notion\n"
        rf"failed reading {raw_values[5]}, mirror={raw_values[6]}"
    )

    assert all(value not in redacted for value in raw_values)
    assert "job_id=job-123" in redacted
    assert "source_id=source_notion" in redacted
    assert "<redacted>" in redacted


def test_worker_log_redactor_removes_name_only_cookie_header_folded_lines():
    redacted = _redact_worker_log_message(
        "Cookie\r"
        " source_id=folded-cookie-source-secret; "
        "job_id=folded-cookie-job-secret\r"
        "\tphase=folded-cookie-phase-secret\r"
        "ordinary diagnostic, source_id=source_notion; job_id=job-123; "
        "phase=fetching_page_content"
    )

    for secret in (
        "folded-cookie-source-secret",
        "folded-cookie-job-secret",
        "folded-cookie-phase-secret",
    ):
        assert secret not in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted
    assert "phase=fetching_page_content" in redacted


def test_worker_log_redactor_removes_lone_cr_cookie_value_continuations():
    redacted = _redact_worker_log_message(
        "Cookie: initial-alpha-value\r"
        " folded-alpha-value, folded-delta-value\r"
        "\tfolded-beta-value\r"
        "first clear diagnostic, source_id=source_notion; job_id=job-123\r"
        "Set-Cookie: initial-delta-value\r"
        "\tfolded-gamma-value\r"
        "second clear diagnostic, phase=fetching_page_content"
    )

    for secret in (
        "initial-alpha-value",
        "folded-alpha-value",
        "folded-delta-value",
        "folded-beta-value",
        "initial-delta-value",
        "folded-gamma-value",
    ):
        assert secret not in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted
    assert "phase=fetching_page_content" in redacted


def test_worker_log_redactor_preserves_lone_cr_clear_diagnostic_without_comma():
    redacted = _redact_worker_log_message(
        "Cookie: initial-alpha-value\r"
        "clear diagnostic source_id=source_notion "
        "job_id=job-123 retry_count=24"
    )

    assert "initial-alpha-value" not in redacted
    assert "clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted
    assert "retry_count=24" in redacted


def test_worker_log_redactor_removes_pre_sanitized_directory_path_tails():
    sensitive_tails = (
        "vault/meeting notes",
        r"vault\meeting notes",
        "note",
    )

    redacted = _redact_worker_log_message(
        "Provider failures "
        f"unix=<redacted> {sensitive_tails[0]}, job_id=job-123; "
        f"windows=<redacted> {sensitive_tails[1]}; source_id=source_notion; "
        f"final_component=<redacted> {sensitive_tails[2]}, retry_count=1; "
        "detail=<redacted-path> retry scheduled, attempt=2"
    )

    assert all(tail not in redacted for tail in sensitive_tails)
    assert "job_id=job-123" in redacted
    assert "source_id=source_notion" in redacted
    assert "retry_count=1" in redacted
    assert "detail=<redacted-path> retry scheduled" in redacted
    assert "attempt=2" in redacted


def test_worker_logger_exception_redacts_delimiter_paths_from_actual_stack_context(
    monkeypatch,
    tmp_path: Path,
):
    log_path = tmp_path / "logs" / "sync-worker.log"
    sensitive_paths = (
        "/Users/tester/private,vault;exception notes.md",
        r"C:\Users\tester\private,vault;exception notes.md",
    )
    notion_token = "ntn_abcdefghijklmnopqrstuvwxyz0123456789"
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_PATH", str(log_path))
    root_logger = logging.getLogger()
    previous_handlers = list(root_logger.handlers)
    previous_level = root_logger.level

    try:
        handler = _configure_logging()
        project_logger = logging.getLogger("indexing.sync_worker")

        try:
            raise RuntimeError(
                "provider failure "
                f"unix={sensitive_paths[0]}, job_id=job-123; "
                f"windows={sensitive_paths[1]}; source_id=source_notion "
                f"token={notion_token}"
            )
        except RuntimeError:
            project_logger.exception(
                "Actual delimiter-path worker exception",
                stack_info=True,
            )
        handler.flush()

        combined_log = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(log_path.parent.glob("sync-worker.log*"))
        )
        assert "Actual delimiter-path worker exception" in combined_log
        assert all(path not in combined_log for path in sensitive_paths)
        assert "vault;exception notes.md" not in combined_log
        assert notion_token not in combined_log
        assert "job_id=job-123" in combined_log
        assert "source_id=source_notion" in combined_log
        assert "<redacted" in combined_log
    finally:
        for handler in list(root_logger.handlers):
            handler.close()
        root_logger.handlers[:] = previous_handlers
        root_logger.setLevel(previous_level)


def test_worker_rotating_logs_remove_complete_multiword_credentials(
    monkeypatch,
    tmp_path: Path,
):
    log_path = tmp_path / "logs" / "sync-worker.log"
    raw_secrets = (
        "bearer-first",
        "bearer-trailing-secret",
        "Zm9vOmJhcg==",
        "basic-trailing-secret",
        "session=alpha",
        "theme=private",
        "preference=hidden",
        "first-segment",
        "second-segment",
        "third-secret",
    )
    messages = (
        "Authorization: Bearer bearer-first bearer-trailing-secret",
        "Authorization=Basic Zm9vOmJhcg== basic-trailing-secret",
        "Cookie: session=alpha theme=private preference=hidden",
        "api_key=first-segment second-segment third-secret",
    )
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_PATH", str(log_path))
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_MAX_BYTES", "1024")
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_BACKUP_COUNT", "3")
    root_logger = logging.getLogger()
    previous_handlers = list(root_logger.handlers)
    previous_level = root_logger.level

    try:
        handler = _configure_logging()
        project_logger = logging.getLogger("indexing.ingestion_service")
        for index in range(30):
            project_logger.info(
                "Source notion completed retained lifecycle batch %s %s",
                index,
                "x" * 60,
            )
        for message in messages:
            project_logger.warning(message)
        handler.flush()

        combined_log = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(log_path.parent.glob("sync-worker.log*"))
        )
        assert "retained lifecycle batch" in combined_log
        assert "<redacted" in combined_log
        assert all(secret not in combined_log for secret in raw_secrets)
    finally:
        for handler in list(root_logger.handlers):
            handler.close()
        root_logger.handlers[:] = previous_handlers
        root_logger.setLevel(previous_level)


def test_worker_rotating_logs_redact_formatter_appended_context(
    monkeypatch,
    tmp_path: Path,
):
    log_path = tmp_path / "logs" / "sync-worker.log"
    notion_tokens = (
        "ntn_abcdefghijklmnopqrstuvwxyz0123456789",
        "secret_abcdefghijklmnopqrstuvwxyz0123456789",
    )
    sensitive_paths = (
        "/Users/tester/private vault/stack trace.py",
        r"C:\Users\tester\private vault\exception trace.py",
    )
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_PATH", str(log_path))
    root_logger = logging.getLogger()
    previous_handlers = list(root_logger.handlers)
    previous_level = root_logger.level

    try:
        handler = _configure_logging()
        project_logger = logging.getLogger("indexing.sync_worker")

        stack_record = project_logger.makeRecord(
            project_logger.name,
            logging.WARNING,
            __file__,
            1,
            "Stack diagnostic",
            (),
            None,
        )
        stack_record.stack_info = (
            f'File "{sensitive_paths[0]}", line 1, token {notion_tokens[0]}'
        )
        project_logger.handle(stack_record)

        exception_record = project_logger.makeRecord(
            project_logger.name,
            logging.ERROR,
            __file__,
            1,
            "Exception diagnostic",
            (),
            None,
        )
        exception_record.exc_text = (
            f'RuntimeError at "{sensitive_paths[1]}": {notion_tokens[1]}'
        )
        project_logger.handle(exception_record)
        handler.flush()

        combined_log = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(log_path.parent.glob("sync-worker.log*"))
        )
        assert "Stack diagnostic" in combined_log
        assert "Exception diagnostic" in combined_log
        assert all(token not in combined_log for token in notion_tokens)
        assert all(path not in combined_log for path in sensitive_paths)
        assert "stack trace.py" not in combined_log
        assert "exception trace.py" not in combined_log
        assert "<redacted" in combined_log
    finally:
        for handler in list(root_logger.handlers):
            handler.close()
        root_logger.handlers[:] = previous_handlers
        root_logger.setLevel(previous_level)


def test_worker_logger_exception_redacts_raw_context_before_truncating(
    monkeypatch,
    tmp_path: Path,
):
    log_path = tmp_path / "logs" / "sync-worker.log"
    notion_tokens = (
        "ntn_abcdefghijklmnopqrstuvwxyz0123456789",
        "secret_abcdefghijklmnopqrstuvwxyz0123456789",
    )
    sensitive_paths = (
        "/Users/tester/private vault/exception notes.md",
        r"C:\Users\tester\private vault\windows exception notes.md",
    )
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_PATH", str(log_path))
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_MAX_BYTES", "1024")
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_BACKUP_COUNT", "3")
    root_logger = logging.getLogger()
    previous_handlers = list(root_logger.handlers)
    previous_level = root_logger.level

    try:
        handler = _configure_logging()
        project_logger = logging.getLogger("indexing.sync_worker")

        try:
            raise RuntimeError(
                "provider failure "
                f"unix={sensitive_paths[0]}, "
                f"windows={sensitive_paths[1]}, "
                f"tokens={notion_tokens[0]} {notion_tokens[1]}"
            )
        except RuntimeError:
            project_logger.exception("Actual worker exception")

        try:
            raise RuntimeError(f"{'x' * 285} {notion_tokens[0]}")
        except RuntimeError:
            project_logger.exception("Exception with a token at the truncation edge")
        handler.flush()

        combined_log = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(log_path.parent.glob("sync-worker.log*"))
        )
        assert "Actual worker exception" in combined_log
        assert "Exception with a token at the truncation edge" in combined_log
        assert all(token not in combined_log for token in notion_tokens)
        assert "ntn_" not in combined_log
        assert "secret_" not in combined_log
        assert "abcdefghijklmnopqrstuvwxyz0123456789" not in combined_log
        assert all(path not in combined_log for path in sensitive_paths)
        assert "exception notes.md" not in combined_log
        assert "windows exception notes.md" not in combined_log
        assert "<redacted" in combined_log
    finally:
        for handler in list(root_logger.handlers):
            handler.close()
        root_logger.handlers[:] = previous_handlers
        root_logger.setLevel(previous_level)


def test_worker_rotating_logs_bound_each_oversized_record(
    monkeypatch,
    tmp_path: Path,
):
    log_path = tmp_path / "logs" / "sync-worker.log"
    notion_tokens = (
        "ntn_abcdefghijklmnopqrstuvwxyz0123456789",
        "secret_abcdefghijklmnopqrstuvwxyz0123456789",
    )
    sensitive_paths = (
        "/Users/tester/private vault/oversized stack.py",
        r"C:\Users\tester\private vault\oversized exception.py",
    )
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_PATH", str(log_path))
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_MAX_BYTES", "1024")
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_BACKUP_COUNT", "3")
    root_logger = logging.getLogger()
    previous_handlers = list(root_logger.handlers)
    previous_level = root_logger.level

    try:
        handler = _configure_logging()
        project_logger = logging.getLogger("indexing.sync_worker")
        project_logger.warning(
            "Oversized message %s token %s",
            "민감로그" * 1500,
            notion_tokens[0],
        )

        stack_record = project_logger.makeRecord(
            project_logger.name,
            logging.WARNING,
            __file__,
            1,
            "Oversized stack",
            (),
            None,
        )
        stack_record.stack_info = (
            f"{'스택' * 2500} path={sensitive_paths[0]} token={notion_tokens[0]}"
        )
        project_logger.handle(stack_record)

        exception_record = project_logger.makeRecord(
            project_logger.name,
            logging.ERROR,
            __file__,
            1,
            "Oversized exception text",
            (),
            None,
        )
        exception_record.exc_text = (
            f"{'예외' * 2500} path={sensitive_paths[1]} token={notion_tokens[1]}"
        )
        project_logger.handle(exception_record)
        handler.flush()

        rotated_files = sorted(log_path.parent.glob("sync-worker.log*"))
        combined_log = "\n".join(
            path.read_text(encoding="utf-8") for path in rotated_files
        )
        assert rotated_files
        assert all(path.stat().st_size <= 1024 for path in rotated_files)
        assert "Oversized message" in combined_log
        assert "Oversized stack" in combined_log
        assert "Oversized exception text" in combined_log
        assert all(token not in combined_log for token in notion_tokens)
        assert "ntn_" not in combined_log
        assert "secret_" not in combined_log
        assert "abcdefghijklmnopqrstuvwxyz0123456789" not in combined_log
        assert all(path not in combined_log for path in sensitive_paths)
        assert "oversized stack.py" not in combined_log
        assert "oversized exception.py" not in combined_log
    finally:
        for handler in list(root_logger.handlers):
            handler.close()
        root_logger.handlers[:] = previous_handlers
        root_logger.setLevel(previous_level)


def test_worker_logging_rotates_project_output_with_bounded_backups(
    monkeypatch,
    tmp_path: Path,
):
    log_path = tmp_path / "logs" / "sync-worker.log"
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_PATH", str(log_path))
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_MAX_BYTES", "1024")
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_BACKUP_COUNT", "2")
    root_logger = logging.getLogger()
    previous_handlers = list(root_logger.handlers)
    previous_level = root_logger.level

    try:
        handler = _configure_logging()
        project_logger = logging.getLogger("indexing.ingestion_service")
        for index in range(100):
            project_logger.info("bounded lifecycle message %s %s", index, "x" * 80)
        assert handler is not None
        handler.flush()

        rotated_files = sorted(log_path.parent.glob("sync-worker.log*"))
        assert 1 < len(rotated_files) <= 3
        assert all(path.stat().st_size <= 1024 for path in rotated_files)
        assert any(
            "bounded lifecycle message" in path.read_text(encoding="utf-8")
            for path in rotated_files
        )
    finally:
        for handler in list(root_logger.handlers):
            handler.close()
        root_logger.handlers[:] = previous_handlers
        root_logger.setLevel(previous_level)


def test_worker_logs_suppress_dependency_info_and_redact_sensitive_context(
    monkeypatch,
    tmp_path: Path,
):
    log_path = tmp_path / "logs" / "sync-worker.log"
    sensitive_url = "https://api.example.test/private/page?token=secret-query"
    sensitive_token = "secret-worker-token"
    sensitive_path = str(tmp_path / "private obsidian vault" / "journal.md")
    sensitive_page_id = "01234567-89ab-cdef-0123-456789abcdef"
    sensitive_block_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_PATH", str(log_path))
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_MAX_BYTES", "1024")
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_BACKUP_COUNT", "3")
    root_logger = logging.getLogger()
    previous_handlers = list(root_logger.handlers)
    previous_level = root_logger.level

    try:
        handler = _configure_logging()
        assert handler is not None
        project_logger = logging.getLogger("indexing.ingestion_service")
        for index in range(30):
            project_logger.info(
                "Source notion completed lifecycle batch %s %s",
                index,
                "x" * 60,
            )

        transport = httpx.MockTransport(lambda request: httpx.Response(200))
        with httpx.Client(transport=transport) as client:
            client.get(sensitive_url)

        notion_client = NotionAPIClient(
            NotionConfig(api_key="test-key"),
            AppConfig(),
        )
        asyncio.run(
            notion_client.fetch_block_content(
                object(),
                sensitive_block_id,
                depth=notion_client.app_config.notion_max_depth + 1,
            )
        )

        service = object.__new__(IngestionService)
        service.metadata_store = type(
            "_ProgressStore",
            (),
            {"get_sync_job": lambda self, job_id: None},
        )()
        service._refresh_running_job_for_progress = lambda job_id: None
        service._update_sync_job_hints_best_effort = lambda *args, **kwargs: None
        asyncio.run(
            service._handle_source_fetch_progress(
                "job-id",
                "source_notion",
                {
                    "event": "page_fetch_completed",
                    "current_page": 1,
                    "total_pages": 1,
                    "page_id": sensitive_page_id,
                    "elapsed_seconds": 0.25,
                },
            )
        )
        logging.getLogger("fetching.notion").warning(
            "Provider warning token=%s path=%s",
            sensitive_token,
            sensitive_path,
        )
        handler.flush()

        combined_log = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(log_path.parent.glob("sync-worker.log*"))
        )
        assert "completed lifecycle batch" in combined_log
        assert sensitive_url not in combined_log
        assert "api.example.test" not in combined_log
        assert sensitive_token not in combined_log
        assert sensitive_path not in combined_log
        assert "journal.md" not in combined_log
        assert sensitive_page_id not in combined_log
        assert sensitive_block_id not in combined_log
        assert "HTTP Request:" not in combined_log
        assert "<redacted" in combined_log
        assert all(
            path.stat().st_size <= 1024
            for path in log_path.parent.glob("sync-worker.log*")
        )
    finally:
        for handler in list(root_logger.handlers):
            handler.close()
        root_logger.handlers[:] = previous_handlers
        root_logger.setLevel(previous_level)


def test_worker_info_log_does_not_persist_path_bearing_document_ids(
    monkeypatch,
    tmp_path: Path,
):
    log_path = tmp_path / "logs" / "sync-worker.log"
    sensitive_document_id = str(
        tmp_path / "private obsidian vault" / "personal journal.md"
    )
    monkeypatch.setenv("CONTEXTWIKI_SYNC_WORKER_LOG_PATH", str(log_path))
    root_logger = logging.getLogger()
    previous_handlers = list(root_logger.handlers)
    previous_level = root_logger.level

    try:
        handler = _configure_logging()
        assert handler is not None
        indexer = object.__new__(ContentIndexer)
        indexer.collection = _RecordingCollection()
        indexer._mutation_lock = asyncio.Lock()
        asyncio.run(
            indexer.delete_documents_by_ids(
                [sensitive_document_id],
                source_id="obsidian",
            )
        )
        handler.flush()

        combined_log = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(log_path.parent.glob("sync-worker.log*"))
        )
        assert sensitive_document_id not in combined_log
        assert "personal journal.md" not in combined_log
    finally:
        for handler in list(root_logger.handlers):
            handler.close()
        root_logger.handlers[:] = previous_handlers
        root_logger.setLevel(previous_level)
