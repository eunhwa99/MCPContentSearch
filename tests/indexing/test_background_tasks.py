from io import StringIO

import pytest

from core.error_sanitizer import sanitize_error_stream
from indexing.background_tasks import safe_error_message


pytestmark = pytest.mark.unit


def test_safe_error_message_redacts_broad_secret_tokens():
    message = (
        "OpenAI sk-proj-abcdefghijklmnopqrstuvwxyz123456 "
        "GitHub github_pat_abcdefghijklmnopqrstuvwxyz123456 "
        "AWS AKIAIOSFODNN7EXAMPLE "
        "Slack xoxb-1234567890-secret "
        "JWT eyJheader.payload123456.signature123456 "
        "Authorization: Basic dXNlcjpwYXNzd29yZA== "
        "api_key: plain secret with spaces\n"
        "next line"
    )

    redacted = safe_error_message(RuntimeError(message))

    assert "sk-proj-" not in redacted
    assert "github_pat_" not in redacted
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted
    assert "xoxb-" not in redacted
    assert "eyJheader" not in redacted
    assert "Basic dXNlcjpwYXNzd29yZA==" not in redacted
    assert "plain secret with spaces" not in redacted
    assert "api_key: <redacted>" in redacted


def test_safe_error_message_redacts_notion_tokens_and_complete_paths_without_losing_fields():
    notion_tokens = (
        "ntn_abcdefghijklmnopqrstuvwxyz0123456789",
        "secret_abcdefghijklmnopqrstuvwxyz0123456789",
    )
    sensitive_paths = (
        "/Users/tester/private,vault;meeting notes.md",
        r"C:\Users\tester\private,vault;meeting notes.md",
    )
    message = (
        f"provider failure at {sensitive_paths[0]}, job_id=job-123; "
        f"fallback={sensitive_paths[1]}; source_id=source_notion "
        f"tokens={notion_tokens[0]} {notion_tokens[1]}"
    )

    redacted = safe_error_message(RuntimeError(message), max_length=600)

    assert all(token not in redacted for token in notion_tokens)
    assert all(path not in redacted for path in sensitive_paths)
    assert "vault;meeting notes.md" not in redacted
    assert "notes.md" not in redacted
    assert "job_id=job-123" in redacted
    assert "source_id=source_notion" in redacted
    assert "<redacted" in redacted


def test_safe_error_message_redacts_colon_labeled_paths_without_losing_fields():
    sensitive_paths = (
        "/Users/tester/private vault/meeting notes.md",
        r"C:\Users\tester\private vault\meeting notes.md",
    )
    message = (
        f"path:{sensitive_paths[0]}, job_id=job-123; "
        f"file:{sensitive_paths[1]}; source_id=source_notion"
    )

    redacted = safe_error_message(RuntimeError(message), max_length=600)

    assert all(path not in redacted for path in sensitive_paths)
    assert "meeting notes.md" not in redacted
    assert "job_id=job-123" in redacted
    assert "source_id=source_notion" in redacted
    assert "path:<redacted>" in redacted
    assert "file:<redacted>" in redacted


def test_safe_error_message_redacts_semicolon_cookie_headers_and_unc_paths():
    raw_values = (
        "session=alpha",
        "theme=private",
        "preference=hidden",
        "sid=bravo",
        "Path=/private",
        r"\\server\private share\meeting notes.md",
        r"\\?\C:\Users\tester\private vault\meeting notes.md",
        r"\\?\UNC\server\private share\archive notes.md",
    )
    message = (
        "Cookie: session=alpha; theme=private; preference=hidden\n"
        "job_id=job-123\n"
        "Set-Cookie: sid=bravo; Path=/private; HttpOnly\n"
        "source_id=source_notion\n"
        rf"failed reading {raw_values[5]}, mirror={raw_values[6]}, "
        rf"archive={raw_values[7]}"
    )

    redacted = safe_error_message(RuntimeError(message), max_length=800)

    assert all(value not in redacted for value in raw_values)
    assert "job_id=job-123" in redacted
    assert "source_id=source_notion" in redacted
    assert redacted.count("<redacted>") >= 5


def test_safe_error_message_redacts_coalesced_and_folded_cookie_pairs():
    raw_cookie_values = (
        "session=alpha",
        "theme=private",
        "preference=hidden",
        "sid=bravo",
        "unknown_attribute=top-secret",
        "folded_cookie=delta",
    )
    message = (
        "Cookie: session=alpha, theme=private, preference=hidden\n"
        "job_id=job-123\n"
        "Set-Cookie: sid=bravo, unknown_attribute=top-secret,\n"
        "\tfolded_cookie=delta\n"
        "source_id=source_notion; retry_count=2"
    )

    redacted = safe_error_message(RuntimeError(message), max_length=800)

    assert all(value not in redacted for value in raw_cookie_values)
    assert "job_id=job-123" in redacted
    assert "source_id=source_notion" in redacted
    assert "retry_count=2" in redacted
    assert "<redacted>" in redacted


def test_error_stream_redacts_folded_cookie_continuations_across_lines():
    source = StringIO(
        "Set-Cookie: sid=bravo, unknown_attribute=top-secret,\n"
        "\tfolded_cookie=delta\n"
        "ordinary diagnostic, source_id=source_notion; retry_count=2; attempt=3\n"
    )
    target = StringIO()

    sanitize_error_stream(source, target)

    redacted = target.getvalue()
    assert "sid=bravo" not in redacted
    assert "unknown_attribute=top-secret" not in redacted
    assert "folded_cookie=delta" not in redacted
    assert "source_id=source_notion" in redacted
    assert "retry_count=2" in redacted
    assert (
        "ordinary diagnostic, source_id=source_notion; retry_count=2; attempt=3"
        in redacted
    )


def test_safe_error_message_fails_closed_for_cookie_names_that_match_diagnostic_fields():
    message = (
        "Cookie: source_id=cookie-source-secret, job_id=cookie-job-secret; "
        "phase=cookie-phase-secret\n"
        "ordinary diagnostic, source_id=source_notion; job_id=job-123; "
        "phase=fetching_page_content\n"
    )

    redacted = safe_error_message(RuntimeError(message), max_length=800)

    for secret in (
        "cookie-source-secret",
        "cookie-job-secret",
        "cookie-phase-secret",
    ):
        assert secret not in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted
    assert "phase=fetching_page_content" in redacted


def test_error_stream_keeps_cookie_mode_after_an_oversized_header():
    source = StringIO(
        "Cookie: source_id=cookie-source-secret; padding="
        + ("x" * 70000)
        + "\n"
        "\tjob_id=folded-cookie-secret; phase=folded-phase-secret\n"
        "ordinary diagnostic, source_id=source_notion; job_id=job-123\n"
    )
    target = StringIO()

    sanitize_error_stream(source, target)

    redacted = target.getvalue()
    assert "cookie-source-secret" not in redacted
    assert "folded-cookie-secret" not in redacted
    assert "folded-phase-secret" not in redacted
    assert "<redacted oversized diagnostic>" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted


def test_error_stream_finishes_after_an_oversized_unterminated_line():
    class FailingRepeatedEofStream(StringIO):
        eof_reads = 0

        def readline(self, size: int = -1) -> str:
            value = super().readline(size)
            if not value:
                self.eof_reads += 1
                if self.eof_reads > 1:
                    raise AssertionError("sanitize_error_stream repeatedly read EOF")
            return value

    source = FailingRepeatedEofStream("Cookie: session=secret;" + ("x" * 70000))
    target = StringIO()

    sanitize_error_stream(source, target)

    assert target.getvalue() == "<redacted oversized diagnostic>"


def test_safe_error_message_keeps_the_existing_max_length_contract():
    redacted = safe_error_message(RuntimeError("x" * 100), max_length=40)

    assert redacted == f"{'x' * 37}..."
    assert len(redacted) == 40
