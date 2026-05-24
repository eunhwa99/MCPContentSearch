import pytest

from scripts.smoke_generate_wiki_page import _redact
from scripts.smoke_generate_wiki_page import _suppress_sync_error_logs


pytestmark = pytest.mark.unit


def test_smoke_redact_uses_broad_secret_patterns():
    message = (
        "failed with access_token=plain-token "
        "and api_key: plain secret with spaces\n"
        "Authorization: Bearer abcdefghijklmnop "
        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
    )

    redacted = _redact(message)

    assert "plain-token" not in redacted
    assert "plain secret with spaces" not in redacted
    assert "Bearer abcdefghijklmnop" not in redacted
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted
    assert "[REDACTED]" in redacted


def test_suppress_sync_error_logs_restores_logger_levels():
    import logging

    logger = logging.getLogger("indexing.ingestion_service")
    api_logger = logging.getLogger("api.tools")
    original_level = logger.level
    original_api_level = api_logger.level

    with _suppress_sync_error_logs():
        assert logger.level > logging.CRITICAL
        assert api_logger.level > logging.CRITICAL

    assert logger.level == original_level
    assert api_logger.level == original_api_level
