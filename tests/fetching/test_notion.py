import asyncio

import pytest

from environments.config import AppConfig, NotionConfig
from fetching.notion import NotionAPIClient, NotionPageProcessor


pytestmark = pytest.mark.unit


def test_notion_block_fetch_can_surface_strict_full_sync_failures():
    client = NotionAPIClient(NotionConfig(api_key="secret"), AppConfig())

    async def fail_fetch_blocks(http_client, block_id):
        raise RuntimeError("block fetch failed")

    client._fetch_blocks = fail_fetch_blocks

    with pytest.raises(RuntimeError, match="block fetch failed"):
        asyncio.run(client.fetch_block_content(object(), "block-id", strict=True))

    assert asyncio.run(client.fetch_block_content(object(), "block-id")) == ""


def test_notion_page_processor_populates_native_external_id():
    processor = NotionPageProcessor(NotionConfig(api_key="secret"))

    document = processor.build_document(
        {
            "id": "page-123",
            "url": "https://notion.so/page-123",
            "created_time": "2026-05-21T00:00:00Z",
            "last_edited_time": "2026-05-22T00:00:00Z",
            "properties": {
                "title": {
                    "title": [
                        {
                            "plain_text": "Identity",
                        }
                    ]
                }
            },
        },
        "content",
    )

    assert document.id == "notion_page-123"
    assert document.document_id == "page-123"
    assert document.external_id == "page-123"
    assert document.canonical_url == "https://notion.so/page-123"
    assert document.updated_at == "2026-05-22T00:00:00Z"
