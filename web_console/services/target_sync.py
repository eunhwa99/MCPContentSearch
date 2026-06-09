from __future__ import annotations

from typing import Any

from web_console.payloads import (
    normalize_target_source_type,
    running_sync_job,
    safe_github_target_for_display,
    safe_sync_job_payload,
    safe_target_sync_payload,
    safe_url_for_display,
    sync_status_value,
    target_sync_already_running_payload,
)


class GitHubTargetSyncService:
    """Run explicit GitHub target syncs without changing process environment."""

    def __init__(
        self,
        *,
        config: Any,
        metadata_store: Any,
        indexer: Any,
        github_token: str = "",
    ):
        self.config = config
        self.metadata_store = metadata_store
        self.indexer = indexer
        self.github_token = github_token

    async def sync_target(self, target: str) -> dict[str, Any]:
        running_job = running_sync_job(self.metadata_store, "source_github")
        if running_job:
            return target_sync_already_running_payload(
                "source_github",
                "github",
                running_job,
            )

        from fetching.connectors import GitHubSourceConnector, SourceRegistry
        from fetching.github import GitHubRepositoryDiscovery
        from indexing.chunker import DocumentChunker
        from indexing.ingestion_service import IngestionService

        discovery = GitHubRepositoryDiscovery(
            self.config,
            token=self.github_token,
        )
        repositories = await discovery.discover_repository_specs(target)
        if not repositories:
            return {
                "status": "skipped",
                "source_id": "source_github",
                "target": target,
                "repository_count": 0,
                "repositories": [],
                "message": "No GitHub repositories were discovered for this target.",
            }

        connector = GitHubSourceConnector(
            tuple(repositories),
            self.config,
            token=self.github_token,
            allow_stale_cleanup=False,
        )
        service = IngestionService(
            metadata_store=self.metadata_store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(),
            indexer=self.indexer,
            register_source_config=False,
        )
        job = await service.sync_source("source_github")
        if sync_status_value(job) == "running":
            return target_sync_already_running_payload(
                "source_github",
                "github",
                job,
            )
        return {
            "status": sync_status_value(job),
            "source_id": "source_github",
            "target": safe_github_target_for_display(target),
            "repository_count": len(repositories),
            "repositories": repositories,
            "stale_cleanup": "disabled",
            "job": safe_sync_job_payload(job),
        }


class NotionTargetSyncService:
    """Run explicit Notion page/database target syncs with configured credentials."""

    def __init__(
        self,
        *,
        config: Any,
        metadata_store: Any,
        indexer: Any,
        notion_api_key: str = "",
    ):
        self.config = config
        self.metadata_store = metadata_store
        self.indexer = indexer
        self.notion_api_key = notion_api_key

    async def sync_target(self, target: str) -> dict[str, Any]:
        from fetching.connectors import NotionSourceConnector, SourceRegistry
        from fetching.notion import parse_notion_object_id
        from indexing.chunker import DocumentChunker
        from indexing.ingestion_service import IngestionService

        object_id = parse_notion_object_id(target)
        if not str(self.notion_api_key or "").strip():
            raise RuntimeError("NOTION_API_KEY is required for Notion target sync")
        connector = _NotionTargetConnector(
            NotionSourceConnector(self.notion_api_key, self.config).source,
            self.notion_api_key,
            self.config,
            target,
        )
        service = IngestionService(
            metadata_store=self.metadata_store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(),
            indexer=self.indexer,
            register_source_config=False,
        )
        job = await service.sync_source("source_notion")
        if sync_status_value(job) == "running":
            return target_sync_already_running_payload(
                "source_notion",
                "notion",
                job,
            )
        return {
            "status": sync_status_value(job),
            "source_id": "source_notion",
            "target_type": "notion",
            "target": f"notion:{object_id}",
            "document_count": job.total_documents,
            "stale_cleanup": "disabled",
            "job": safe_sync_job_payload(job),
        }


class WebTargetSyncService:
    """Run explicit website target syncs without changing configured web sources."""

    def __init__(
        self,
        *,
        config: Any,
        metadata_store: Any,
        indexer: Any,
    ):
        self.config = config
        self.metadata_store = metadata_store
        self.indexer = indexer

    async def sync_target(self, target: str) -> dict[str, Any]:
        from fetching.connectors import SourceRegistry, WebsiteSourceConnector
        from indexing.chunker import DocumentChunker
        from indexing.ingestion_service import IngestionService

        connector = WebsiteSourceConnector(
            (target,),
            self.config,
            allow_stale_cleanup=False,
        )
        service = IngestionService(
            metadata_store=self.metadata_store,
            source_registry=SourceRegistry([connector]),
            chunker=DocumentChunker(),
            indexer=self.indexer,
            register_source_config=False,
        )
        job = await service.sync_source("source_web")
        if sync_status_value(job) == "running":
            return target_sync_already_running_payload(
                "source_web",
                "web",
                job,
            )
        return {
            "status": sync_status_value(job),
            "source_id": "source_web",
            "target_type": "web",
            "target": safe_url_for_display(target),
            "stale_cleanup": "disabled",
            "job": safe_sync_job_payload(job),
        }


class TargetSyncService:
    """Route one-off Web Console target syncs by source type."""

    def __init__(
        self,
        *,
        github_sync_service: Any,
        notion_sync_service: Any,
        web_sync_service: Any,
    ):
        self.github_sync_service = github_sync_service
        self.notion_sync_service = notion_sync_service
        self.web_sync_service = web_sync_service

    async def sync_target(self, source_type: str, target: str) -> dict[str, Any]:
        normalized_type = normalize_target_source_type(source_type)
        if normalized_type == "github":
            return safe_target_sync_payload(
                "github",
                await self.github_sync_service.sync_target(target),
            )
        if normalized_type == "notion":
            return safe_target_sync_payload(
                "notion",
                await self.notion_sync_service.sync_target(target),
            )
        if normalized_type == "web":
            return safe_target_sync_payload(
                "web",
                await self.web_sync_service.sync_target(target),
            )
        raise ValueError("Unsupported target source type")


class _NotionTargetConnector:
    supports_stale_cleanup = False
    cleanup_document_id_prefixes: tuple[str, ...] = ()

    def __init__(self, source: Any, api_key: str, config: Any, target: str):
        self.source = source
        self.api_key = api_key
        self.config = config
        self.target = target

    async def fetch_documents(self):
        from fetching.notion import fetch_notion_target

        return await fetch_notion_target(self.api_key, self.config, self.target)
