from abc import ABC, abstractmethod
from typing import Iterable

from core.models import DocumentModel, SourceModel, SourceType, SyncStatus
from environments.config import AppConfig
from fetching.notion import fetch_notion_pages
from fetching.tistory import fetch_tistory_posts


class SourceConnector(ABC):
    """공통 source connector 인터페이스."""

    source: SourceModel

    @abstractmethod
    async def fetch_documents(self) -> list[DocumentModel]:
        """Fetch documents for one source."""


class SourceRegistry:
    """Runtime registry for available source connectors."""

    def __init__(self, connectors: Iterable[SourceConnector]):
        self._connectors = {connector.source.source_id: connector for connector in connectors}

    def get_connector(self, source_id: str) -> SourceConnector:
        if source_id not in self._connectors:
            raise ValueError(f"Unknown source: {source_id}")
        return self._connectors[source_id]

    def list_sources(self) -> list[SourceModel]:
        return [connector.source for connector in self._connectors.values()]


class NotionSourceConnector(SourceConnector):
    def __init__(self, api_key: str, config: AppConfig):
        self.api_key = api_key
        self.config = config
        self.source = SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=bool(api_key),
            auth_ref="env:NOTION_API_KEY",
            sync_status=SyncStatus.IDLE,
        )

    async def fetch_documents(self) -> list[DocumentModel]:
        documents = await fetch_notion_pages(self.api_key, self.config)
        return [
            doc.model_copy(
                update={
                    "source_id": self.source.source_id,
                    "document_id": doc.document_id or doc.id,
                    "path": doc.path or doc.title,
                    "updated_at": doc.updated_at or doc.date,
                }
            )
            for doc in documents
        ]


class TistorySourceConnector(SourceConnector):
    def __init__(self, blog_name: str, config: AppConfig):
        self.blog_name = blog_name
        self.config = config
        self.source = SourceModel(
            source_id="source_tistory",
            source_type=SourceType.TISTORY,
            name="Tistory",
            enabled=bool(blog_name),
            auth_ref="env:TISTORY_BLOG_NAME",
            sync_status=SyncStatus.IDLE,
        )

    async def fetch_documents(self) -> list[DocumentModel]:
        documents = await fetch_tistory_posts(
            self.blog_name,
            self.config.tistory_max_post_id,
            self.config.connection_limit,
            self.config.request_timeout,
            self.config.tistory_log_interval,
        )
        return [
            doc.model_copy(
                update={
                    "source_id": self.source.source_id,
                    "document_id": doc.document_id or doc.id,
                    "path": doc.path or doc.url,
                    "updated_at": doc.updated_at or doc.date,
                }
            )
            for doc in documents
        ]
