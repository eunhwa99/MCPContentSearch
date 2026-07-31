from abc import ABC, abstractmethod
from typing import Iterable

from core.models import DocumentModel, SourceModel, SourceType, SyncStatus
from environments.config import AppConfig
from fetching.github import GitHubRepositoryFetcher, repository_document_id_prefix
from fetching.notion import fetch_notion_pages
from fetching.obsidian import (
    _OBSIDIAN_DISABLED_REASON,
    _OBSIDIAN_INCOMPLETE_SNAPSHOT_REASON,
    fetch_obsidian_documents,
    obsidian_disabled_reason,
)
from fetching.tistory import fetch_tistory_posts


class SourceConnector(ABC):
    """공통 source connector 인터페이스."""

    source: SourceModel
    supports_stale_cleanup: bool = False
    cleanup_document_id_prefixes: tuple[str, ...] = ()
    disabled_reason: str = ""

    @abstractmethod
    async def fetch_documents(self) -> list[DocumentModel]:
        """Fetch documents for one source."""

    def refresh_source_state(self) -> None:
        """Refresh dynamic source availability before sync/list operations."""


class SourceRegistry:
    """Runtime registry for available source connectors."""

    def __init__(self, connectors: Iterable[SourceConnector]):
        self._connectors = {connector.source.source_id: connector for connector in connectors}

    def get_connector(self, source_id: str) -> SourceConnector:
        if source_id not in self._connectors:
            raise ValueError(f"Unknown source: {source_id}")
        return self._connectors[source_id]

    def list_sources(self) -> list[SourceModel]:
        for connector in self._connectors.values():
            connector.refresh_source_state()
        return [connector.source for connector in self._connectors.values()]


class NotionSourceConnector(SourceConnector):
    supports_stale_cleanup = True

    def __init__(
        self,
        api_key: str,
        config: AppConfig,
        progress_callback=None,
        metadata_store=None,
    ):
        self.api_key = api_key
        self.config = config
        self.progress_callback = progress_callback
        self.progress_stop_signal = None
        self.progress_stop_checker = None
        self.metadata_store = metadata_store
        self.source = SourceModel(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            enabled=bool(api_key),
            auth_ref="env:NOTION_API_KEY",
            sync_status=SyncStatus.IDLE,
        )

    def _load_existing_documents_for_page_ids(
        self, page_ids: list[str] | tuple[str, ...]
    ) -> dict[str, DocumentModel]:
        """Load stored Notion docs for searched page ids only (no full-corpus browse)."""
        if self.metadata_store is None:
            return {}
        ids = [page_id for page_id in page_ids if page_id]
        if not ids:
            return {}
        return self.metadata_store.get_documents_for_fetch_reuse(ids)

    async def fetch_documents(self) -> list[DocumentModel]:
        documents = await fetch_notion_pages(
            self.api_key,
            self.config,
            progress_callback=self.progress_callback,
            progress_stop_signal=getattr(self, "progress_stop_signal", None),
            progress_stop_checker=getattr(self, "progress_stop_checker", None),
            existing_documents_loader=self._load_existing_documents_for_page_ids,
        )
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
    stale_cleanup_disabled_reason = (
        "Stale cleanup is disabled because this source connector does not guarantee complete snapshots."
    )

    def __init__(self, blog_name: str, config: AppConfig):
        self.blog_name = blog_name
        self.config = config
        self.progress_callback = None
        self.progress_stop_signal = None
        self.source = SourceModel(
            source_id="source_tistory",
            source_type=SourceType.TISTORY,
            name="Tistory",
            enabled=bool(blog_name),
            auth_ref="env:TISTORY_BLOG_NAME",
            sync_status=SyncStatus.IDLE,
            stale_cleanup_disabled_reason=self.stale_cleanup_disabled_reason,
        )

    async def fetch_documents(self) -> list[DocumentModel]:
        documents = await fetch_tistory_posts(
            self.blog_name,
            self.config.tistory_max_post_id,
            self.config.connection_limit,
            self.config.request_timeout,
            self.config.tistory_log_interval,
            progress_callback=self.progress_callback,
            progress_stop_signal=getattr(self, "progress_stop_signal", None),
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
        metadata_store=None,
    ):
        self.repositories = tuple(repositories)
        self.config = config
        self.allow_stale_cleanup = allow_stale_cleanup
        self.metadata_store = metadata_store
        self.progress_callback = None
        self.progress_stop_signal = None
        self.progress_stop_checker = None
        self.fetcher = GitHubRepositoryFetcher(
            self.repositories,
            config,
            token=token,
            http_client=http_client,
        )
        self.cleanup_document_id_prefixes = tuple(
            repository_document_id_prefix(spec)
            for spec in self.fetcher.repository_specs
        )
        self.source = SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=bool(self.repositories),
            auth_ref=f"env:{config.github_token_env_var}",
            sync_status=SyncStatus.IDLE,
        )
        self.disabled_reason = (
            "Source source_github is disabled because no GitHub repositories are "
            "configured in CONTEXTWIKI_GITHUB_REPOSITORIES."
            if not self.source.enabled
            else ""
        )
        self.stale_cleanup_disabled_reason = self.disabled_reason
        self.source = self.source.model_copy(
            update={"stale_cleanup_disabled_reason": self.stale_cleanup_disabled_reason}
        )
        # Pin bound method so connector/fetcher identity checks stay stable.
        self._load_existing_documents_for_page_ids = (
            self._load_existing_documents_for_page_ids
        )

    def _load_existing_documents_for_page_ids(
        self, page_ids: list[str] | tuple[str, ...]
    ) -> dict[str, DocumentModel]:
        """Load stored GitHub docs for planned document ids only (no full-corpus browse)."""
        if self.metadata_store is None:
            return {}
        ids = [page_id for page_id in page_ids if page_id]
        if not ids:
            return {}
        return self.metadata_store.get_documents_for_fetch_reuse(ids)

    async def fetch_documents(self) -> list[DocumentModel]:
        if not self.source.enabled:
            self.supports_stale_cleanup = False
            self.cleanup_document_id_prefixes = ()
            return []
        self.supports_stale_cleanup = False
        self.cleanup_document_id_prefixes = ()
        try:
            self.fetcher.progress_callback = self.progress_callback
            self.fetcher.progress_stop_signal = getattr(
                self, "progress_stop_signal", None
            )
            self.fetcher.progress_stop_checker = getattr(
                self, "progress_stop_checker", None
            )
            documents = await self.fetcher.fetch_documents(
                existing_documents_loader=self._load_existing_documents_for_page_ids,
            )
        except Exception:
            self.stale_cleanup_disabled_reason = (
                "Stale cleanup is disabled because the latest GitHub fetch did not complete."
            )
            self.source = self.source.model_copy(
                update={"stale_cleanup_disabled_reason": self.stale_cleanup_disabled_reason}
            )
            raise
        else:
            self.cleanup_document_id_prefixes = tuple(
                repository_document_id_prefix(spec)
                for spec in self.fetcher.repository_specs
            )
            self.supports_stale_cleanup = (
                self.allow_stale_cleanup
                and self.fetcher.snapshot_complete
                and bool(self.cleanup_document_id_prefixes)
            )
            if self.supports_stale_cleanup:
                self.stale_cleanup_disabled_reason = ""
            elif not self.fetcher.snapshot_complete:
                self.stale_cleanup_disabled_reason = (
                    "Stale cleanup is disabled because the latest GitHub snapshot was incomplete."
                )
            elif not self.cleanup_document_id_prefixes:
                self.stale_cleanup_disabled_reason = (
                    "Stale cleanup is disabled because the latest GitHub fetch resolved "
                    "no repository cleanup scope."
                )
            else:
                self.stale_cleanup_disabled_reason = (
                    "Stale cleanup is disabled for this GitHub connector."
                )
            self.source = self.source.model_copy(
                update={"stale_cleanup_disabled_reason": self.stale_cleanup_disabled_reason}
            )
            return documents


class ObsidianSourceConnector(SourceConnector):
    supports_stale_cleanup = True

    def __init__(self, config: AppConfig, metadata_store=None):
        self.vault_path = config.obsidian_vault_path
        self.max_files = config.obsidian_max_files
        self.max_file_bytes = config.obsidian_max_file_bytes
        self.metadata_store = metadata_store
        self.progress_callback = None
        self.progress_stop_signal = None
        self.source = SourceModel(
            source_id="source_obsidian",
            source_type=SourceType.OBSIDIAN,
            name="Obsidian",
            enabled=False,
            auth_ref="env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH",
            sync_status=SyncStatus.IDLE,
            stale_cleanup_disabled_reason=_OBSIDIAN_DISABLED_REASON,
        )
        self.disabled_reason = _OBSIDIAN_DISABLED_REASON
        self.refresh_source_state()

    def refresh_source_state(self) -> None:
        self.disabled_reason = obsidian_disabled_reason(self.vault_path)
        enabled = self.disabled_reason == ""
        self.supports_stale_cleanup = enabled
        self.source = self.source.model_copy(
            update={
                "enabled": enabled,
                "last_error": "" if enabled else self.disabled_reason,
                "stale_cleanup_disabled_reason": "" if enabled else self.disabled_reason,
            }
        )

    def _load_existing_documents_for_page_ids(
        self, page_ids: list[str] | tuple[str, ...]
    ) -> dict[str, DocumentModel]:
        """Load stored Obsidian docs for listed note ids only (no full-corpus browse)."""
        if self.metadata_store is None:
            return {}
        ids = [page_id for page_id in page_ids if page_id]
        if not ids:
            return {}
        return self.metadata_store.get_documents_for_fetch_reuse(ids)

    async def fetch_documents(self) -> list[DocumentModel]:
        self.refresh_source_state()
        if not self.source.enabled:
            self.supports_stale_cleanup = False
            raise FileNotFoundError(self.disabled_reason)
        try:
            snapshot = await fetch_obsidian_documents(
                self.vault_path,
                max_files=self.max_files,
                max_file_bytes=self.max_file_bytes,
                progress_callback=self.progress_callback,
                progress_stop_signal=getattr(self, "progress_stop_signal", None),
                existing_documents_loader=self._load_existing_documents_for_page_ids,
            )
        except Exception:
            self.supports_stale_cleanup = False
            raise
        if not snapshot.snapshot_complete:
            self.supports_stale_cleanup = False
            self.source = self.source.model_copy(
                update={"stale_cleanup_disabled_reason": _OBSIDIAN_INCOMPLETE_SNAPSHOT_REASON}
            )
            raise RuntimeError(_OBSIDIAN_INCOMPLETE_SNAPSHOT_REASON)
        self.supports_stale_cleanup = snapshot.snapshot_complete
        self.source = self.source.model_copy(update={"stale_cleanup_disabled_reason": ""})
        return snapshot.documents


def build_source_registry(
    *,
    config: AppConfig,
    notion_api_key: str,
    tistory_blog_name: str,
    github_token: str = "",
    github_http_client=None,
) -> SourceRegistry:
    """Build the production source registry with retained ContextWiki connectors."""
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
            ObsidianSourceConnector(config),
        ]
    )
