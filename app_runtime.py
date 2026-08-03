from dataclasses import dataclass
from typing import Any, Callable

from environments.config import AppConfig, setup_chroma
from fetching.connectors import (
    GitHubSourceConnector,
    NotionSourceConnector,
    ObsidianSourceConnector,
    SourceRegistry,
    build_source_registry,
)
from indexing.chunker import DocumentChunker
from indexing.indexer import ContentIndexer
from indexing.ingestion_service import IngestionService
from llama_index.core import Settings, StorageContext
from llama_index.vector_stores.chroma import ChromaVectorStore
from storage.metadata_store import (
    MetadataStore,
    prepare_private_directory,
    prepare_private_sqlite_path,
)


@dataclass(frozen=True)
class IngestionRuntime:
    config: AppConfig
    chroma_collection: Any
    indexer: Any
    metadata_store: MetadataStore
    source_registry: SourceRegistry
    ingestion_service: IngestionService
    retained_source_ids: tuple[str, ...]


def _wire_connector_metadata_stores(
    source_registry: SourceRegistry,
    metadata_store: MetadataStore,
) -> None:
    for source_id, connector_type in (
        ("source_notion", NotionSourceConnector),
        ("source_obsidian", ObsidianSourceConnector),
        ("source_github", GitHubSourceConnector),
    ):
        try:
            connector = source_registry.get_connector(source_id)
        except ValueError:
            continue
        if isinstance(connector, connector_type):
            connector.metadata_store = metadata_store


def _career_source_registered(source_registry: SourceRegistry) -> bool:
    try:
        source_registry.get_connector("source_career")
    except ValueError:
        return False
    return True


def build_ingestion_runtime(
    *,
    config: AppConfig,
    notion_api_key: str,
    tistory_blog_name: str,
    github_token: str = "",
    setup_chroma_fn: Callable[..., Any] = setup_chroma,
    vector_store_cls=ChromaVectorStore,
    storage_context_cls=StorageContext,
    indexer_cls=ContentIndexer,
    metadata_store_cls=MetadataStore,
    source_registry_builder=build_source_registry,
    chunker_cls=DocumentChunker,
    ingestion_service_cls=IngestionService,
) -> IngestionRuntime:
    """Compose the ingestion dependencies shared by MCP and the sync worker."""
    source_registry = source_registry_builder(
        config=config,
        notion_api_key=notion_api_key,
        tistory_blog_name=tistory_blog_name,
        github_token=github_token,
    )
    career_configured = (
        config.career_manifest_path is not None
        or _career_source_registered(source_registry)
    )
    if career_configured:
        prepare_private_directory(config.chroma_db_path)
        prepare_private_sqlite_path(config.metadata_db_path)
    chroma_collection = (
        setup_chroma_fn(config, require_private=True)
        if career_configured
        else setup_chroma_fn(config)
    )
    vector_store = vector_store_cls(chroma_collection=chroma_collection)
    storage_context = storage_context_cls.from_defaults(vector_store=vector_store)
    Settings.cache_dir = config.cache_dir

    indexer = indexer_cls(config, chroma_collection, storage_context)
    metadata_store = (
        metadata_store_cls(config.metadata_db_path, require_private=True)
        if career_configured
        else metadata_store_cls(config.metadata_db_path)
    )
    _wire_connector_metadata_stores(source_registry, metadata_store)
    ingestion_service = ingestion_service_cls(
        metadata_store=metadata_store,
        source_registry=source_registry,
        chunker=chunker_cls(),
        indexer=indexer,
        durable_dispatch=True,
    )
    retained_source_ids = tuple(
        source.source_id for source in source_registry.list_sources()
    )
    return IngestionRuntime(
        config=config,
        chroma_collection=chroma_collection,
        indexer=indexer,
        metadata_store=metadata_store,
        source_registry=source_registry,
        ingestion_service=ingestion_service,
        retained_source_ids=retained_source_ids,
    )
