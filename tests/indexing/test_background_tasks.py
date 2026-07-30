import subprocess
import sys
from io import StringIO

import pytest

from core.error_sanitizer import sanitize_error_stream
from indexing.background_tasks import safe_error_message


pytestmark = pytest.mark.unit

MIXED_COOKIE_LINE_ENDINGS = (
    ("Cookie", " ", "\n", "\t"),
    ("Cookie", "\t", "\r\n", " "),
    ("Set-Cookie", " ", "\r\n", "\t"),
    ("Set-Cookie", "\t", "\n", " "),
)

FOLDED_AUTH_CASES = (
    ("Bearer", "\n", " "),
    ("Basic", "\n", "\t"),
    ("Bearer", "\r\n", "\t"),
    ("Basic", "\r\n", " "),
    ("Bearer", "\r", " "),
    ("Basic", "\r", "\t"),
)

STREAM_MAX_LINE_CHARS = 65536


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


def test_short_explicit_auth_credentials_redact_without_losing_fields_or_prose():
    message = (
        "provider rejected Bearer abc123 while syncing, source_id=source_notion; "
        "fallback Basic Og== because retrying; job_id=job-123\n"
        "provider rejected bearer lower123 while syncing; "
        "fallback basic b2c= because retrying\n"
        "bearer authentication failed and basic troubleshooting continues"
    )

    whole_text = safe_error_message(RuntimeError(message), max_length=800)
    stream_target = StringIO()
    sanitize_error_stream(StringIO(message), stream_target)
    stream_text = stream_target.getvalue()

    for redacted in (whole_text, stream_text):
        assert "abc123" not in redacted
        assert "Og==" not in redacted
        assert "lower123" not in redacted
        assert "b2c=" not in redacted
        assert "Bearer <redacted-auth> while syncing," in redacted
        assert "Basic <redacted-auth> because retrying;" in redacted
        assert "bearer <redacted-auth> while syncing;" in redacted
        assert "basic <redacted-auth> because retrying" in redacted
        assert "source_id=source_notion" in redacted
        assert "job_id=job-123" in redacted
        assert "bearer authentication failed" in redacted
        assert "basic troubleshooting continues" in redacted
    assert stream_text == whole_text


@pytest.mark.parametrize(
    ("scheme", "line_ending", "indentation"),
    FOLDED_AUTH_CASES,
)
def test_folded_authorization_credentials_redact_without_losing_next_diagnostic(
    scheme: str,
    line_ending: str,
    indentation: str,
):
    message = (
        f"Authorization: {scheme}{line_ending}"
        f"{indentation}folded-{scheme.lower()}-credential{line_ending}"
        "clear diagnostic source_id=source_notion job_id=job-123 retry_count=26"
    )

    redacted = safe_error_message(RuntimeError(message), max_length=800)

    assert f"folded-{scheme.lower()}-credential" not in redacted
    assert "clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted
    assert "retry_count=26" in redacted


@pytest.mark.parametrize(
    ("scheme", "line_ending", "indentation"),
    FOLDED_AUTH_CASES,
)
def test_multistage_folded_authorization_credentials_redact_without_losing_next_diagnostic(
    scheme: str,
    line_ending: str,
    indentation: str,
):
    credential = f"multistage-{scheme.lower()}-credential"
    message = (
        f"Authorization:{line_ending}"
        f"{indentation}{scheme}{line_ending}"
        f"{indentation}{credential}{line_ending}"
        "clear diagnostic source_id=source_notion job_id=job-123 retry_count=28"
    )

    redacted = safe_error_message(RuntimeError(message), max_length=800)

    assert credential not in redacted
    assert "clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted
    assert "retry_count=28" in redacted


@pytest.mark.parametrize(
    ("scheme", "line_ending", "indentation"),
    FOLDED_AUTH_CASES,
)
def test_bare_name_folded_authorization_credentials_redact_without_losing_next_diagnostic(
    scheme: str,
    line_ending: str,
    indentation: str,
):
    credential = f"bare-name-{scheme.lower()}-credential"
    message = (
        f"Authorization{line_ending}"
        f"{indentation}{scheme}{line_ending}"
        f"{indentation}{credential}{line_ending}"
        "clear diagnostic source_id=source_notion job_id=job-123 retry_count=34"
    )

    redacted = safe_error_message(RuntimeError(message), max_length=800)

    assert credential not in redacted
    assert "clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted
    assert "retry_count=34" in redacted


def test_error_stream_keeps_folded_authorization_state_across_mixed_line_chunks():
    message = (
        "Authorization: Bearer\r"
        " folded-bearer-alpha\r\n"
        "\tfolded-bearer-beta\n"
        "first clear diagnostic source_id=source_notion job_id=job-123\n"
        "Authorization: Basic\n"
        "\tfolded-basic-alpha\r"
        " folded-basic-beta\r\n"
        "second clear diagnostic phase=fetching_page_content retry_count=27\n"
    )
    target = StringIO()

    sanitize_error_stream(StringIO(message), target)

    redacted = target.getvalue()
    for credential in (
        "folded-bearer-alpha",
        "folded-bearer-beta",
        "folded-basic-alpha",
        "folded-basic-beta",
    ):
        assert credential not in redacted
    assert "first clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted
    assert "second clear diagnostic" in redacted
    assert "phase=fetching_page_content" in redacted
    assert "retry_count=27" in redacted


def test_error_stream_keeps_multistage_authorization_state_across_mixed_line_chunks():
    message = (
        "Authorization:\r"
        " Bearer\r\n"
        " multistage-stream-bearer-alpha\n"
        "\tmultistage-stream-bearer-beta\r"
        "first clear diagnostic source_id=source_notion job_id=job-123\n"
        "Authorization=\n"
        "\tBasic\r"
        " multistage-stream-basic-alpha\r\n"
        "\tmultistage-stream-basic-beta\n"
        "second clear diagnostic phase=fetching_page_content retry_count=29\n"
        " indented lowercase bearer authentication remains visible\n"
    )
    target = StringIO()

    sanitize_error_stream(StringIO(message), target)

    redacted = target.getvalue()
    for credential in (
        "multistage-stream-bearer-alpha",
        "multistage-stream-bearer-beta",
        "multistage-stream-basic-alpha",
        "multistage-stream-basic-beta",
    ):
        assert credential not in redacted
    assert "first clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted
    assert "second clear diagnostic" in redacted
    assert "phase=fetching_page_content" in redacted
    assert "retry_count=29" in redacted
    assert "indented lowercase bearer authentication remains visible" in redacted


def test_error_stream_keeps_bare_name_authorization_state_across_mixed_chunks():
    class FragmentedReader:
        def __init__(self, chunks: list[str]) -> None:
            self._chunks = iter(chunks)
            self.requested_sizes: list[int] = []

        def readline(self, size: int = -1) -> str:
            self.requested_sizes.append(size)
            return next(self._chunks, "")

    source = FragmentedReader(
        [
            "Authorization\r",
            "\n Bearer\n",
            "\tbare-name-stream-bearer-credential\r",
            "first clear diagnostic source_id=source_notion job_id=job-123\n",
            "Authorization\n",
            "\tBasic\r\n",
            " bare-name-stream-basic-credential\n",
            "second clear diagnostic phase=fetching_page_content retry_count=34\n",
        ]
    )
    target = StringIO()

    sanitize_error_stream(source, target, max_line_chars=64)

    redacted = target.getvalue()
    assert "bare-name-stream-bearer-credential" not in redacted
    assert "bare-name-stream-basic-credential" not in redacted
    assert "first clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted
    assert "second clear diagnostic" in redacted
    assert "phase=fetching_page_content" in redacted
    assert "retry_count=34" in redacted
    assert source.requested_sizes
    assert set(source.requested_sizes) == {65}


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


@pytest.mark.parametrize("path_prefix", ("path:", ""))
def test_path_redaction_preserves_lone_cr_clear_diagnostic_without_comma(
    path_prefix: str,
):
    sensitive_path = "/Users/tester/private vault/observability notes.md"
    message = (
        f"provider failure {path_prefix}{sensitive_path}\r"
        "clear diagnostic source_id=source_notion "
        "job_id=job-123 retry_count=25\n"
    )

    whole_text = safe_error_message(RuntimeError(message), max_length=800)
    stream_target = StringIO()
    sanitize_error_stream(StringIO(message), stream_target)
    stream_text = stream_target.getvalue()

    for redacted in (whole_text, stream_text):
        assert sensitive_path not in redacted
        assert "observability notes.md" not in redacted
        assert "clear diagnostic" in redacted
        assert "source_id=source_notion" in redacted
        assert "job_id=job-123" in redacted
        assert "retry_count=25" in redacted
    assert stream_text == whole_text


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


@pytest.mark.parametrize(
    ("header_name", "line_ending"),
    (
        ("Cookie", "\n"),
        ("Set-Cookie", "\n"),
        ("Cookie", "\r\n"),
        ("Set-Cookie", "\r\n"),
        ("Cookie", "\r"),
        ("Set-Cookie", "\r"),
    ),
)
def test_safe_error_message_redacts_name_only_cookie_header_folded_lines(
    header_name: str,
    line_ending: str,
):
    message = (
        f"{header_name}{line_ending}"
        f" source_id=folded-cookie-source-secret; "
        f"job_id=folded-cookie-job-secret{line_ending}"
        f"\tphase=folded-cookie-phase-secret{line_ending}"
        f"ordinary diagnostic, source_id=source_notion; "
        f"job_id=job-123; phase=fetching_page_content"
    )

    redacted = safe_error_message(RuntimeError(message), max_length=800)

    for secret in (
        "folded-cookie-source-secret",
        "folded-cookie-job-secret",
        "folded-cookie-phase-secret",
    ):
        assert secret not in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted
    assert "phase=fetching_page_content" in redacted


@pytest.mark.parametrize("header_name", ("Cookie", "Set-Cookie"))
def test_cookie_value_header_redacts_lone_cr_folded_lines_and_matches_stream(
    header_name: str,
):
    message = (
        f"{header_name}: opaque-initial-value\r"
        " folded-alpha-value, folded-delta-value\r"
        "\tfolded-beta-value folded-gamma-value\r"
        "clear diagnostic, source_id=source_notion; "
        "job_id=job-123; phase=fetching_page_content"
    )

    whole_text = safe_error_message(RuntimeError(message), max_length=800)
    stream_target = StringIO()
    sanitize_error_stream(StringIO(message), stream_target)
    stream_text = stream_target.getvalue()

    for redacted in (whole_text, stream_text):
        for secret in (
            "opaque-initial-value",
            "folded-alpha-value",
            "folded-delta-value",
            "folded-beta-value",
            "folded-gamma-value",
        ):
            assert secret not in redacted
        assert "source_id=source_notion" in redacted
        assert "job_id=job-123" in redacted
        assert "phase=fetching_page_content" in redacted
    assert stream_text == whole_text


@pytest.mark.parametrize("header_name", ("Cookie", "Set-Cookie"))
def test_cookie_redaction_preserves_lone_cr_clear_diagnostic_without_comma(
    header_name: str,
):
    message = (
        f"{header_name}: opaque-initial-value\r"
        "clear diagnostic source_id=source_notion "
        "job_id=job-123 retry_count=24\n"
    )

    whole_text = safe_error_message(RuntimeError(message), max_length=800)
    stream_target = StringIO()
    sanitize_error_stream(StringIO(message), stream_target)
    stream_text = stream_target.getvalue()

    for redacted in (whole_text, stream_text):
        assert "opaque-initial-value" not in redacted
        assert "clear diagnostic" in redacted
        assert "source_id=source_notion" in redacted
        assert "job_id=job-123" in redacted
        assert "retry_count=24" in redacted
    assert stream_text == whole_text


def test_multiword_secret_remains_fail_closed_before_lone_cr_boundary():
    message = (
        "api_key: opaque alpha value\r"
        "clear diagnostic source_id=source_notion job_id=job-123"
    )

    redacted = safe_error_message(RuntimeError(message), max_length=800)

    assert "opaque alpha value" not in redacted
    assert "alpha value" not in redacted
    assert "clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted


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


@pytest.mark.parametrize("header_name", ("Cookie", "Set-Cookie"))
def test_error_stream_keeps_cookie_mode_when_oversized_header_separator_crosses_chunk(
    header_name: str,
):
    source = StringIO(
        header_name
        + (" " * 70)
        + ": source_id=cookie-source-secret\n"
        "\tjob_id=folded-cookie-secret; phase=folded-phase-secret\n"
        "ordinary diagnostic, source_id=source_notion; job_id=job-123\n"
    )
    target = StringIO()

    sanitize_error_stream(source, target, max_line_chars=64)

    redacted = target.getvalue()
    assert "cookie-source-secret" not in redacted
    assert "folded-cookie-secret" not in redacted
    assert "folded-phase-secret" not in redacted
    assert "<redacted oversized diagnostic>" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted


@pytest.mark.parametrize(
    ("header_name", "line_ending"),
    (
        ("Cookie", "\n"),
        ("Set-Cookie", "\n"),
        ("Cookie", "\r\n"),
        ("Set-Cookie", "\r\n"),
    ),
)
def test_error_stream_fails_closed_when_cookie_name_precedes_folded_delimiter_and_value(
    header_name: str,
    line_ending: str,
):
    source = StringIO(
        f"{header_name}{line_ending}"
        f"\t: source_id=cookie-source-secret; "
        f"job_id=cookie-job-secret{line_ending}"
        f"ordinary diagnostic, source_id=source_notion; "
        f"job_id=job-123{line_ending}"
    )
    target = StringIO()

    sanitize_error_stream(source, target)

    redacted = target.getvalue()
    assert "cookie-source-secret" not in redacted
    assert "cookie-job-secret" not in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted
    assert "ordinary diagnostic" in redacted


@pytest.mark.parametrize(
    ("header_name", "first_indent", "first_ending", "second_indent"),
    MIXED_COOKIE_LINE_ENDINGS,
)
def test_error_stream_keeps_cookie_state_across_mixed_endings_in_one_read(
    header_name: str,
    first_indent: str,
    first_ending: str,
    second_indent: str,
):
    message = (
        f"{header_name}\r"
        f"{first_indent}folded-alpha-value, folded-delta-value{first_ending}"
        f"{second_indent}folded-beta-value folded-gamma-value\n"
        "clear diagnostic, source_id=source_notion; job_id=job-123\n"
    )
    target = StringIO()

    sanitize_error_stream(StringIO(message), target)

    redacted = target.getvalue()
    for value in (
        "folded-alpha-value",
        "folded-delta-value",
        "folded-beta-value",
        "folded-gamma-value",
    ):
        assert value not in redacted
    assert "clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted


@pytest.mark.parametrize(
    ("header_name", "first_indent", "first_ending", "second_indent"),
    MIXED_COOKIE_LINE_ENDINGS,
)
def test_error_sanitizer_cli_keeps_cookie_state_across_mixed_endings_in_one_read(
    header_name: str,
    first_indent: str,
    first_ending: str,
    second_indent: str,
):
    message = (
        f"{header_name}\r"
        f"{first_indent}folded-alpha-value, folded-delta-value{first_ending}"
        f"{second_indent}folded-beta-value folded-gamma-value\n"
        "clear diagnostic, source_id=source_notion; job_id=job-123\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "core.error_sanitizer", "--stream"],
        input=message,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    for value in (
        "folded-alpha-value",
        "folded-delta-value",
        "folded-beta-value",
        "folded-gamma-value",
    ):
        assert value not in result.stdout
    assert "clear diagnostic" in result.stdout
    assert "source_id=source_notion" in result.stdout
    assert "job_id=job-123" in result.stdout


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


@pytest.mark.parametrize(
    ("line_prefix", "clear_ending"),
    (
        ("Cookie: opaque=", "\n"),
        ("path:/Users/tester/private-vault/", "\r\n"),
    ),
)
def test_error_stream_retains_clear_diagnostic_after_oversized_lone_cr_line(
    line_prefix: str,
    clear_ending: str,
):
    source = StringIO(
        line_prefix
        + ("x" * 80)
        + "\r"
        + "clear diagnostic source_id=source_notion job_id=job-123"
        + clear_ending
    )
    target = StringIO()

    sanitize_error_stream(source, target, max_line_chars=64)

    redacted = target.getvalue()
    assert "<redacted oversized diagnostic>" in redacted
    assert "clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted


@pytest.mark.parametrize(
    ("line_prefix", "clear_ending"),
    (
        ("Cookie: opaque=", "\n"),
        ("path:/Users/tester/private-vault/", "\r\n"),
    ),
)
def test_error_sanitizer_cli_retains_clear_diagnostic_after_oversized_lone_cr_line(
    line_prefix: str,
    clear_ending: str,
):
    message = (
        line_prefix
        + ("x" * 65540)
        + "\r"
        + "clear diagnostic source_id=source_notion job_id=job-123"
        + clear_ending
    )

    result = subprocess.run(
        [sys.executable, "-m", "core.error_sanitizer", "--stream"],
        input=message,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert "<redacted oversized diagnostic>" in result.stdout
    assert "clear diagnostic" in result.stdout
    assert "source_id=source_notion" in result.stdout
    assert "job_id=job-123" in result.stdout


def test_error_stream_keeps_folded_cookie_fail_closed_after_oversized_lone_cr():
    source = StringIO(
        "Cookie: opaque="
        + ("x" * 80)
        + "\r"
        + "\tfolded-alpha-value\n"
        + "clear diagnostic source_id=source_notion\n"
    )
    target = StringIO()

    sanitize_error_stream(source, target, max_line_chars=64)

    redacted = target.getvalue()
    assert "folded-alpha-value" not in redacted
    assert "clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted


@pytest.mark.parametrize(
    ("header_prefix", "folded_secret"),
    (
        (
            "Authorization: Bearer initial-credential-",
            "split-authorization-credential-987654",
        ),
        (
            "Cookie: session=initial-cookie; padding=",
            "split-cookie-credential-987654",
        ),
    ),
)
@pytest.mark.parametrize("line_ending", ("\r", "\n", "\r\n"))
def test_error_stream_redacts_folded_credential_when_indent_and_body_split_at_production_boundary(
    header_prefix: str,
    folded_secret: str,
    line_ending: str,
):
    padding = "x" * (65535 - len(header_prefix))
    message = (
        f"{header_prefix}{padding}{line_ending} {folded_secret}{line_ending}"
        "clear diagnostic source_id=source_notion job_id=job-123\n"
    )
    target = StringIO()

    sanitize_error_stream(StringIO(message), target)

    redacted = target.getvalue()
    assert folded_secret not in redacted
    if line_ending == "\r":
        assert "<redacted oversized diagnostic>" in redacted
    assert "clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted


@pytest.mark.parametrize(
    ("header_prefix", "folded_secret"),
    (
        (
            "Authorization: Bearer initial-credential-",
            "cli-split-authorization-credential-987654",
        ),
        (
            "Cookie: session=initial-cookie; padding=",
            "cli-split-cookie-credential-987654",
        ),
    ),
)
@pytest.mark.parametrize("line_ending", ("\r", "\n", "\r\n"))
def test_error_sanitizer_cli_redacts_folded_credential_when_indent_and_body_split_at_production_boundary(
    header_prefix: str,
    folded_secret: str,
    line_ending: str,
):
    padding = "x" * (65535 - len(header_prefix))
    message = (
        f"{header_prefix}{padding}{line_ending} {folded_secret}{line_ending}"
        "clear diagnostic source_id=source_notion job_id=job-123\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "core.error_sanitizer", "--stream"],
        input=message,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert folded_secret not in result.stdout
    if line_ending == "\r":
        assert "<redacted oversized diagnostic>" in result.stdout
    assert "clear diagnostic" in result.stdout
    assert "source_id=source_notion" in result.stdout
    assert "job_id=job-123" in result.stdout


@pytest.mark.parametrize(
    ("header_prefix", "folded_secret"),
    (
        (
            "Authorization: Bearer initial-credential-",
            "boundary-crlf-authorization-credential-987654",
        ),
        (
            "Cookie: session=initial-cookie; padding=",
            "boundary-crlf-cookie-credential-987654",
        ),
    ),
)
def test_error_stream_coalesces_boundary_split_crlf_without_resetting_sensitive_state(
    header_prefix: str,
    folded_secret: str,
):
    padding = "x" * (STREAM_MAX_LINE_CHARS - len(header_prefix))
    message = (
        f"{header_prefix}{padding}\r\n"
        f" {folded_secret}\r\n"
        "clear diagnostic source_id=source_notion job_id=job-123\n"
    )
    target = StringIO()

    sanitize_error_stream(StringIO(message), target)

    redacted = target.getvalue()
    assert folded_secret not in redacted
    assert "<redacted oversized diagnostic>\r\n" in redacted
    assert "clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted
    assert "job_id=job-123" in redacted


@pytest.mark.parametrize(
    ("header_prefix", "folded_secret"),
    (
        (
            "Authorization: Bearer initial-credential-",
            "cli-boundary-crlf-authorization-credential-987654",
        ),
        (
            "Cookie: session=initial-cookie; padding=",
            "cli-boundary-crlf-cookie-credential-987654",
        ),
    ),
)
def test_error_sanitizer_cli_coalesces_boundary_split_crlf_without_resetting_sensitive_state(
    header_prefix: str,
    folded_secret: str,
):
    padding = "x" * (STREAM_MAX_LINE_CHARS - len(header_prefix))
    message = (
        f"{header_prefix}{padding}\r\n"
        f" {folded_secret}\r\n"
        "clear diagnostic source_id=source_notion job_id=job-123\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "core.error_sanitizer", "--stream"],
        input=message,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert folded_secret not in result.stdout
    assert "<redacted oversized diagnostic>\n" in result.stdout
    assert "clear diagnostic" in result.stdout
    assert "source_id=source_notion" in result.stdout
    assert "job_id=job-123" in result.stdout


@pytest.mark.parametrize(
    ("header_prefix", "folded_secret"),
    (
        (
            "Authorization: Bearer initial-",
            "fragmented-auth-credential",
        ),
        (
            "Cookie: session=initial; padding=",
            "fragmented-cookie-credential",
        ),
    ),
)
def test_error_stream_coalesces_fragmented_crlf_with_bounded_state(
    header_prefix: str,
    folded_secret: str,
):
    class TrackingFragmentedReader:
        requested_sizes: list[int]

        def __init__(self, chunks: list[str]) -> None:
            self.chunks = iter(chunks)
            self.requested_sizes = []

        def readline(self, size: int = -1) -> str:
            self.requested_sizes.append(size)
            return next(self.chunks, "")

    max_line_chars = 64
    padding = "x" * (max_line_chars - len(header_prefix))
    source = TrackingFragmentedReader(
        [
            f"{header_prefix}{padding}\r",
            f"\n {folded_secret}\r",
            "\nclear diagnostic source_id=source_notion\n",
        ]
    )
    target = StringIO()

    sanitize_error_stream(
        source,
        target,
        max_line_chars=max_line_chars,
    )

    redacted = target.getvalue()
    assert folded_secret not in redacted
    assert "<redacted oversized diagnostic>\r\n" in redacted
    assert "clear diagnostic" in redacted
    assert "source_id=source_notion" in redacted
    assert source.requested_sizes
    assert set(source.requested_sizes) == {max_line_chars + 1}
    assert len(redacted) < 192


def test_error_stream_keeps_fragment_tracking_bounded_until_logical_terminator():
    class TrackingStream:
        requested_sizes: list[int]

        def __init__(self, chunks: list[str]) -> None:
            self.chunks = iter(chunks)
            self.requested_sizes = []

        def readline(self, size: int = -1) -> str:
            self.requested_sizes.append(size)
            return next(self.chunks, "")

    max_line_chars = 32
    header_prefix = "Authorization: Bearer "
    padding = "x" * (max_line_chars - 1 - len(header_prefix))
    folded_secret = "bounded-credential-987654"
    second_folded_secret = "second-bounded-credential-987654"
    source = TrackingStream(
        [
            f"{header_prefix}{padding}\r ",
            f"{folded_secret}\r ",
            f"{second_folded_secret}\r",
            "clear diagnostic source_id=source_notion\n",
        ]
    )
    target = StringIO()

    sanitize_error_stream(source, target, max_line_chars=max_line_chars)

    redacted = target.getvalue()
    assert folded_secret not in redacted
    assert second_folded_secret not in redacted
    assert "clear diagnostic" in redacted
    assert source.requested_sizes
    assert set(source.requested_sizes) == {max_line_chars + 1}
    assert len(redacted) < 256


def test_safe_error_message_keeps_the_existing_max_length_contract():
    redacted = safe_error_message(RuntimeError("x" * 100), max_length=40)

    assert redacted == f"{'x' * 37}..."
    assert len(redacted) == 40
