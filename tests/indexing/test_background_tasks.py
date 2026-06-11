import pytest

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
