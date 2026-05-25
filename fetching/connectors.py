from abc import ABC, abstractmethod
from typing import Iterable

from core.models import DocumentModel, SourceModel, SourceType, SyncStatus
from environments.config import AppConfig
from fetching.github import GitHubRepositoryFetcher
from fetching.notion import fetch_notion_pages
from fetching.tistory import fetch_tistory_posts
from fetching.web_docs import WebsiteDocsFetcher


class SourceConnector(ABC):
    """공통 source connector 인터페이스."""

    source: SourceModel
    supports_stale_cleanup: bool = False

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
    supports_stale_cleanup = True

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
                    "document_id": doc.external_id or doc.document_id or doc.id,
                    "external_id": doc.external_id or doc.document_id or doc.id,
                    "canonical_url": doc.canonical_url or doc.url,
                    "path": doc.path or doc.title,
                    "updated_at": doc.updated_at or doc.date,
                }
            )
            for doc in documents
        ]


class TistorySourceConnector(SourceConnector):
    supports_stale_cleanup = False

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
                    "document_id": doc.external_id or doc.document_id or doc.id,
                    "external_id": doc.external_id or doc.document_id or doc.id,
                    "canonical_url": doc.canonical_url or doc.url,
                    "path": doc.path or doc.url,
                    "updated_at": doc.updated_at or doc.date,
                }
            )
            for doc in documents
        ]


class GitHubSourceConnector(SourceConnector):
    supports_stale_cleanup = True

    def __init__(
        self,
        repositories: tuple[str, ...],
        config: AppConfig,
        *,
        token: str = "",
        http_client=None,
        allow_stale_cleanup: bool = True,
    ):
        self.repositories = tuple(repositories)
        self.config = config
        self.allow_stale_cleanup = allow_stale_cleanup
        self.fetcher = GitHubRepositoryFetcher(
            self.repositories,
            config,
            token=token,
            http_client=http_client,
        )
        self.source = SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=bool(self.repositories),
            auth_ref=f"env:{config.github_token_env_var}",
            sync_status=SyncStatus.IDLE,
        )

    async def fetch_documents(self) -> list[DocumentModel]:
        if not self.source.enabled:
            return []
        try:
            documents = await self.fetcher.fetch_documents()
        except Exception:
            self.supports_stale_cleanup = False
            raise
        else:
            self.supports_stale_cleanup = (
                self.allow_stale_cleanup and self.fetcher.snapshot_complete
            )
            return documents


class WebsiteSourceConnector(SourceConnector):
    supports_stale_cleanup = True

    def __init__(
        self,
        seed_urls: tuple[str, ...],
        config: AppConfig,
        *,
        http_client=None,
    ):
        self.seed_urls = tuple(seed_urls)
        self.config = config
        self.fetcher = WebsiteDocsFetcher(
            self.seed_urls,
            config,
            http_client=http_client,
        )
        self.seed_urls = self.fetcher.seed_urls
        self.source = SourceModel(
            source_id="source_web",
            source_type=SourceType.WEB,
            name="Website Docs",
            enabled=bool(self.seed_urls),
            auth_ref="env:CONTEXTWIKI_WEB_URLS",
            sync_status=SyncStatus.IDLE,
        )

    async def fetch_documents(self) -> list[DocumentModel]:
        if not self.source.enabled:
            return []
        try:
            documents = await self.fetcher.fetch_documents()
        except Exception:
            self.supports_stale_cleanup = False
            raise
        else:
            self.supports_stale_cleanup = self.fetcher.snapshot_complete
            return documents


def build_source_registry(
    *,
    config: AppConfig,
    notion_api_key: str,
    tistory_blog_name: str,
    github_token: str = "",
    github_http_client=None,
    web_http_client=None,
) -> SourceRegistry:
    """Build the production source registry with all configured ContextWiki connectors."""
    return SourceRegistry(
        [
            NotionSourceConnector(notion_api_key, config),
            TistorySourceConnector(tistory_blog_name, config),
            GitHubSourceConnector(
                config.github_repositories,
                config,
                token=github_token,
                http_client=github_http_client,
            ),
            WebsiteSourceConnector(
                config.web_seed_urls,
                config,
                http_client=web_http_client,
            ),
        ]
    )
