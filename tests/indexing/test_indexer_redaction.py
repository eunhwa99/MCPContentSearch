import asyncio
import traceback
from types import SimpleNamespace

import pytest

from core.exceptions import IndexingError
from core.models import DocumentModel
from indexing.indexer import ContentIndexer


pytestmark = pytest.mark.unit


def test_content_indexer_redacts_failure_status_logs_and_exception(caplog):
    indexer = ContentIndexer(
        config=SimpleNamespace(progress_log_interval=1),
        chroma_collection=None,
        storage_context=None,
    )

    async def fail_filter(documents):
        raise RuntimeError(
            "index failed token=super-secret-value "
            "AKIAIOSFODNN7EXAMPLE "
            "Authorization: Basic dXNlcjpwYXNzd29yZA=="
        )

    indexer._filter_documents = fail_filter
    documents = [
        DocumentModel(
            id="doc-1",
            title="Doc",
            content="content",
            url="https://example.com",
            platform="web",
        )
    ]

    with caplog.at_level("ERROR", logger="indexing.indexer"):
        with pytest.raises(IndexingError) as exc_info:
            asyncio.run(indexer.index_documents(documents))

    assert "super-secret-value" not in indexer.status.message
    assert "AKIAIOSFODNN7EXAMPLE" not in indexer.status.message
    assert "Basic dXNlcjpwYXNzd29yZA==" not in indexer.status.message
    assert "token=<redacted>" in indexer.status.message
    assert "super-secret-value" not in str(exc_info.value)
    assert "AKIAIOSFODNN7EXAMPLE" not in str(exc_info.value)
    assert "Basic dXNlcjpwYXNzd29yZA==" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    formatted_traceback = "".join(
        traceback.format_exception(
            type(exc_info.value),
            exc_info.value,
            exc_info.value.__traceback__,
        )
    )
    assert "super-secret-value" not in formatted_traceback
    assert "AKIAIOSFODNN7EXAMPLE" not in formatted_traceback
    assert "Basic dXNlcjpwYXNzd29yZA==" not in formatted_traceback
    assert "super-secret-value" not in caplog.text
    assert "AKIAIOSFODNN7EXAMPLE" not in caplog.text
    assert "Basic dXNlcjpwYXNzd29yZA==" not in caplog.text
