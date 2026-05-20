import logging

from core.models import DocumentModel, SyncJobStatus, SyncStatus
from core.utils import ContentHasher
from fetching.connectors import SourceRegistry
from indexing.chunker import DocumentChunker
from storage.metadata_store import MetadataStore

logger = logging.getLogger(__name__)


class IngestionService:
    """Per-source incremental sync orchestration."""

    def __init__(
        self,
        metadata_store: MetadataStore,
        source_registry: SourceRegistry,
        chunker: DocumentChunker,
        indexer,
    ):
        self.metadata_store = metadata_store
        self.source_registry = source_registry
        self.chunker = chunker
        self.indexer = indexer
        self.metadata_store.ensure_schema()
        for source in self.source_registry.list_sources():
            self.metadata_store.register_source(source)

    async def sync_source(self, source_id: str):
        connector = self.source_registry.get_connector(source_id)
        self.metadata_store.register_source(connector.source)
        job = self.metadata_store.create_sync_job(source_id)
        if not connector.source.enabled:
            message = f"Source {source_id} is disabled"
            failed = self.metadata_store.update_sync_job(
                job.job_id,
                status=SyncJobStatus.FAILED,
                error_message=message,
            )
            self.metadata_store.update_source_status(
                source_id,
                SyncStatus.FAILED,
                last_error=message,
            )
            return failed

        self.metadata_store.update_sync_job(job.job_id, status=SyncJobStatus.RUNNING)
        self.metadata_store.update_source_status(source_id, SyncStatus.RUNNING)

        try:
            documents = await connector.fetch_documents()
            processed = 0
            skipped = 0
            indexed_chunks = 0

            for document in documents:
                normalized = self._normalize_document(document, source_id)
                document_id = normalized.document_id or normalized.id
                content_hash = ContentHasher.hash_content(normalized.content)
                if self.metadata_store.get_document_content_hash(document_id) == content_hash:
                    skipped += 1
                    continue

                normalized = normalized.model_copy(update={"content_hash": content_hash})
                chunks = self.chunker.chunk_document(normalized)

                if chunks:
                    await self.indexer.index_documents(
                        [chunk.to_document_model(platform=normalized.platform) for chunk in chunks]
                    )
                old_chunks = self.metadata_store.list_chunks_for_document(document_id)
                new_chunk_ids = {chunk.chunk_id for chunk in chunks}
                stale_chunk_ids = [
                    chunk.chunk_id
                    for chunk in old_chunks
                    if chunk.chunk_id not in new_chunk_ids
                ]
                if stale_chunk_ids and hasattr(self.indexer, "delete_documents_by_ids"):
                    self.indexer.delete_documents_by_ids(stale_chunk_ids)

                self.metadata_store.upsert_document_and_replace_chunks(normalized, chunks)
                processed += 1
                indexed_chunks += len(chunks)

            finished = self.metadata_store.update_sync_job(
                job.job_id,
                status=SyncJobStatus.SUCCEEDED,
                total_documents=len(documents),
                processed_documents=processed,
                indexed_chunks=indexed_chunks,
                skipped_documents=skipped,
            )
            self.metadata_store.update_source_status(
                source_id,
                SyncStatus.SUCCEEDED,
                last_synced_at=finished.finished_at,
                last_error="",
            )
            return finished

        except Exception as exc:
            logger.exception("Sync failed for source %s", source_id)
            failed = self.metadata_store.update_sync_job(
                job.job_id,
                status=SyncJobStatus.FAILED,
                error_message=str(exc),
            )
            self.metadata_store.update_source_status(
                source_id,
                SyncStatus.FAILED,
                last_error=str(exc),
            )
            return failed

    @staticmethod
    def _normalize_document(document: DocumentModel, source_id: str) -> DocumentModel:
        return document.model_copy(
            update={
                "source_id": document.source_id or source_id,
                "document_id": document.document_id or document.id,
                "id": document.document_id or document.id,
                "path": document.path or document.title,
                "updated_at": document.updated_at or document.date,
            }
        )
