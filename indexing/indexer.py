import asyncio
import json
import logging
from typing import Any, List, Optional, Sequence

from llama_index.core import Document, Settings, StorageContext, VectorStoreIndex
from llama_index.core.ingestion import run_transformations
from llama_index.core.schema import BaseNode, TransformComponent
from llama_index.vector_stores.chroma import ChromaVectorStore

from environments.config import AppConfig
from core.models import DocumentModel, IndexStatusModel, IndexState
from core.exceptions import IndexingError
from indexing.background_tasks import safe_error_message
from indexing.manager import IndexManager
from indexing.converter import DocumentConverter

logger = logging.getLogger(__name__)
METADATA_UPDATE_BATCH_SIZE = 500
VECTOR_WRITE_BATCH_SIZE = 500


class _PreChunkedPassageTransform(TransformComponent):
    """Keep an already-sized logical passage as exactly one vector node."""

    def __call__(
        self,
        nodes: Sequence[BaseNode],
        **kwargs: Any,
    ) -> Sequence[BaseNode]:
        del kwargs
        return nodes


PRECHUNKED_PASSAGE_TRANSFORMATIONS = [_PreChunkedPassageTransform()]

class ContentIndexer:
    def __init__(
        self,
        config: AppConfig,
        chroma_collection,
        storage_context: StorageContext
    ):
        self.config = config
        self.collection = chroma_collection
        self.storage_context = storage_context
        self.index: Optional[VectorStoreIndex] = None
        self.status = IndexStatusModel()
        self._mutation_lock = asyncio.Lock()
    
    async def index_documents(self, documents: List[DocumentModel]):
        """Index the provided documents."""
        async with self._mutation_lock:
            self._update_status(
                state=IndexState.RUNNING,
                message="Starting indexing...",
                total_docs=len(documents)
            )

            try:
                if not documents:
                    self._complete_indexing("No documents to index")
                    return {"embeddings_generated": 0, "embeddings_reused": 0}

                filtered = await self._filter_documents(documents)

                if not filtered["documents"]:
                    self._complete_indexing("No new or updated documents")
                    return {
                        "embeddings_generated": 0,
                        "embeddings_reused": len(documents),
                    }

                await self._batch_index(filtered["documents"])

                self._complete_indexing(
                    f"Complete: {filtered['new']} new, {filtered['updated']} updated"
                )
                generated = len(filtered["documents"])
                return {
                    "embeddings_generated": generated,
                    "embeddings_reused": len(documents) - generated,
                }

            except Exception as e:
                error_message = safe_error_message(e)
                logger.error("Indexing error: %s", error_message)
                self._update_status(
                    state=IndexState.ERROR,
                    message=f"Error: {error_message}",
                )
                raise IndexingError(f"Indexing failed: {error_message}") from None

    async def update_documents_metadata(self, documents: List[DocumentModel]):
        """Refresh managed vector metadata while preserving existing embeddings."""
        if not documents:
            return {
                "embeddings_generated": 0,
                "embeddings_reused": 0,
                "metadata_rollback": {
                    "metadata_batches": [],
                    "inserted_chunks": [],
                },
            }

        async with self._mutation_lock:
            converter = DocumentConverter()
            converted_by_key: dict[tuple[str, str], Document] = {}
            source_chunk_ids: dict[str, list[str]] = {}
            for document in documents:
                key = (document.source_id, document.id)
                converted_by_key[key] = converter.to_llama_document(document)
                source_chunk_ids.setdefault(document.source_id, []).append(document.id)

            prepared_updates: list[tuple[list[str], list[dict], list[dict]]] = []
            found_keys: set[tuple[str, str]] = set()
            rollback: dict[str, list] = {
                "metadata_batches": [],
                "inserted_chunks": [],
            }
            try:
                for source_id, chunk_ids in source_chunk_ids.items():
                    for chunk_id_batch in self._metadata_batches(chunk_ids):
                        existing = await self._run_chroma_in_thread(
                            self.collection.get,
                            where={
                                "$and": [
                                    {"chunk_id": {"$in": chunk_id_batch}},
                                    {"source_id": source_id},
                                    {"contextwiki_managed": "true"},
                                ]
                            },
                            include=["metadatas"],
                        )
                        ids = list(existing.get("ids") or [])
                        metadatas = [
                            dict(metadata or {})
                            for metadata in (existing.get("metadatas") or [])
                        ]
                        if len(ids) != len(metadatas):
                            raise ValueError("Chroma metadata snapshot is incomplete")
                        refreshed = []
                        for metadata in metadatas:
                            chunk_id = str(metadata.get("chunk_id") or "")
                            key = (source_id, chunk_id)
                            converted = converted_by_key.get(key)
                            if converted is None:
                                raise ValueError(
                                    "Chroma metadata snapshot contains an unexpected chunk"
                                )
                            found_keys.add(key)
                            refreshed.append(
                                self._merge_stored_metadata(
                                    metadata,
                                    converted.metadata,
                                )
                            )
                        if ids:
                            prepared_updates.append((ids, refreshed, metadatas))

                missing_keys = [
                    key for key in converted_by_key if key not in found_keys
                ]
                missing_documents = [converted_by_key[key] for key in missing_keys]
                rollback["metadata_batches"] = [
                    (ids, original)
                    for ids, _refreshed, original in prepared_updates
                ]
                rollback["inserted_chunks"] = missing_keys

                for ids, refreshed, _original in prepared_updates:
                    await self._run_chroma_in_thread(
                        self.collection.update,
                        ids=ids,
                        metadatas=refreshed,
                    )

                if missing_documents:
                    await self._batch_index(missing_documents)
                return {
                    "embeddings_generated": len(missing_documents),
                    "embeddings_reused": len(found_keys),
                    "metadata_rollback": rollback,
                }
            except asyncio.CancelledError:
                await self._restore_metadata_rollback_locked(rollback)
                raise
            except Exception as exc:
                try:
                    await self._restore_metadata_rollback_locked(rollback)
                except Exception as rollback_exc:
                    logger.error(
                        "Vector metadata rollback error: %s",
                        safe_error_message(rollback_exc),
                    )
                error_message = safe_error_message(exc)
                logger.error("Vector metadata update error: %s", error_message)
                raise IndexingError(
                    f"Vector metadata update failed: {error_message}"
                ) from None

    async def rollback_documents_metadata(self, rollback: object) -> None:
        """Compensate a metadata refresh when its authoritative DB commit fails."""
        if not isinstance(rollback, dict):
            raise ValueError("Vector metadata rollback token is invalid")
        async with self._mutation_lock:
            await self._restore_metadata_rollback_locked(rollback)

    async def _restore_metadata_rollback_locked(self, rollback: dict) -> None:
        for ids, metadatas in rollback.get("metadata_batches", []):
            if not ids:
                continue
            await self._run_chroma_in_thread(
                self.collection.update,
                ids=list(ids),
                metadatas=[dict(metadata) for metadata in metadatas],
            )
        inserted_by_source: dict[str, list[str]] = {}
        for source_id, chunk_id in rollback.get("inserted_chunks", []):
            inserted_by_source.setdefault(str(source_id), []).append(str(chunk_id))
        for source_id, chunk_ids in inserted_by_source.items():
            for chunk_id_batch in self._metadata_batches(chunk_ids):
                await self._run_chroma_in_thread(
                    self.collection.delete,
                    where={
                        "$and": [
                            {"chunk_id": {"$in": chunk_id_batch}},
                            {"source_id": source_id},
                            {"contextwiki_managed": "true"},
                        ]
                    },
                )

    @staticmethod
    def _metadata_batches(values: list[str]) -> list[list[str]]:
        return [
            values[offset : offset + METADATA_UPDATE_BATCH_SIZE]
            for offset in range(0, len(values), METADATA_UPDATE_BATCH_SIZE)
        ]

    @staticmethod
    def _merge_stored_metadata(existing: dict, refreshed: dict) -> dict:
        merged = {**existing, **refreshed}
        serialized_node = existing.get("_node_content")
        if not isinstance(serialized_node, str):
            return merged
        try:
            node_payload = json.loads(serialized_node)
        except (TypeError, ValueError):
            return merged
        node_metadata = node_payload.get("metadata")
        if not isinstance(node_metadata, dict):
            return merged
        node_payload["metadata"] = {**node_metadata, **refreshed}
        excluded_keys = node_payload.get("excluded_embed_metadata_keys")
        if isinstance(excluded_keys, list):
            node_payload["excluded_embed_metadata_keys"] = list(
                dict.fromkeys([*excluded_keys, *refreshed])
            )
        merged["_node_content"] = json.dumps(node_payload, ensure_ascii=False)
        return merged
    
    async def _filter_documents(self, documents: List[DocumentModel]) -> dict:
        converter = DocumentConverter()
        new_docs: list[Document] = []
        new_count = 0
        update_count = 0

        for offset in range(0, len(documents), METADATA_UPDATE_BATCH_SIZE):
            document_batch = documents[offset : offset + METADATA_UPDATE_BATCH_SIZE]
            grouped_documents: dict[
                tuple[str, str, bool], list[DocumentModel]
            ] = {}
            for document in document_batch:
                managed = IndexManager._is_contextwiki_managed(document)
                metadata_field = "chunk_id"
                key = (metadata_field, document.source_id, managed)
                grouped_documents.setdefault(key, []).append(document)

            managers: dict[tuple[str, str, bool], IndexManager] = {}
            for group_key, group_documents in grouped_documents.items():
                where = self._document_metadata_where(group_key, group_documents)
                snapshot = await self._run_chroma_in_thread(
                    self.collection.get,
                    where=where,
                    include=["metadatas"],
                )
                managers[group_key] = IndexManager(snapshot.get("metadatas") or [])

            updated_by_group: dict[
                tuple[str, str, bool], list[DocumentModel]
            ] = {}
            for batch_index, document in enumerate(document_batch, offset + 1):
                managed = IndexManager._is_contextwiki_managed(document)
                metadata_field = "chunk_id"
                group_key = (metadata_field, document.source_id, managed)
                manager = managers[group_key]
                if manager.is_new(document):
                    new_count += 1
                    new_docs.append(converter.to_llama_document(document))
                elif manager.is_updated(document):
                    update_count += 1
                    updated_by_group.setdefault(group_key, []).append(document)
                    new_docs.append(converter.to_llama_document(document))

                if batch_index % self.config.progress_log_interval == 0:
                    self._update_progress(batch_index, len(documents))
                    await asyncio.sleep(0.01)

            for group_key, updated_documents in updated_by_group.items():
                await self._run_chroma_in_thread(
                    self.collection.delete,
                    where=self._document_metadata_where(
                        group_key,
                        updated_documents,
                    ),
                )

        return {
            "documents": new_docs,
            "new": new_count,
            "updated": update_count,
        }

    @staticmethod
    def _document_metadata_where(
        group_key: tuple[str, str, bool],
        documents: list[DocumentModel],
    ) -> dict:
        metadata_field, source_id, managed = group_key
        document_ids = list(dict.fromkeys(document.id for document in documents))
        legacy_field = "doc_id" if metadata_field == "chunk_id" else "chunk_id"
        filters: list[dict] = [
            {
                "$or": [
                    {metadata_field: {"$in": document_ids}},
                    {legacy_field: {"$in": document_ids}},
                ]
            }
        ]
        if source_id:
            filters.append({"source_id": source_id})
        filters.append(
            {"contextwiki_managed": "true" if managed else {"$ne": "true"}}
        )
        return {"$and": filters}
    
    async def _join_thread_task_preserving_cancel(self, task: asyncio.Task) -> None:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception as exc:
                logger.error(
                    "Chroma worker failed during cancel join: %s",
                    safe_error_message(exc),
                )
                continue

    async def _run_chroma_in_thread(self, func, /, *args, **kwargs):
        """Run blocking Chroma work off-loop without releasing the mutation lock.

        ``asyncio.shield`` alone still raises ``CancelledError`` immediately,
        which would exit ``async with self._mutation_lock`` while the executor
        thread continues. Join the thread task before re-raising cancel.
        """
        task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
        current = asyncio.current_task()
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await self._join_thread_task_preserving_cancel(task)
            raise
        except Exception:
            if current is not None and current.cancelling():
                if not task.done():
                    await self._join_thread_task_preserving_cancel(task)
                elif task.exception() is not None:
                    logger.error(
                        "Chroma worker failed during cancel: %s",
                        safe_error_message(task.exception()),
                    )
                raise asyncio.CancelledError from None
            raise

    async def _batch_index(self, documents: List[Document]):
        total = len(documents)

        batch_size = min(self.config.batch_size, VECTOR_WRITE_BATCH_SIZE)
        for i in range(0, total, batch_size):
            batch = documents[i:i + batch_size]

            if self.index is None:
                prechunked = [
                    document for document in batch if self._is_prechunked(document)
                ]
                transformable = [
                    document for document in batch if not self._is_prechunked(document)
                ]
                initial_batch = prechunked or transformable
                kwargs = {}
                if prechunked:
                    kwargs["transformations"] = PRECHUNKED_PASSAGE_TRANSFORMATIONS
                self.index = await self._run_chroma_in_thread(
                    VectorStoreIndex.from_documents,
                    initial_batch,
                    storage_context=self.storage_context,
                    show_progress=True,
                    **kwargs,
                )
                if prechunked and transformable:
                    await self._run_chroma_in_thread(
                        self._insert_document_batch,
                        transformable,
                    )
            else:
                await self._run_chroma_in_thread(
                    self._insert_document_batch,
                    batch,
                )

            processed = min(total, i + batch_size)
            self._update_progress(processed, total)

    def _insert_document_batch(self, documents: List[Document]) -> None:
        if self.index is None:
            raise RuntimeError("Cannot insert a batch before creating the index")
        prechunked = [
            document for document in documents if self._is_prechunked(document)
        ]
        transformable = [
            document for document in documents if not self._is_prechunked(document)
        ]
        nodes: list[BaseNode] = list(prechunked)
        if transformable:
            nodes.extend(
                run_transformations(
                    transformable,
                    Settings.transformations,
                    show_progress=True,
                )
            )
        self.index.insert_nodes(nodes)
        for document in documents:
            self.index.docstore.set_document_hash(document.id_, document.hash)

    @staticmethod
    def _is_prechunked(document: Document) -> bool:
        metadata = getattr(document, "metadata", {})
        return (
            metadata.get("contextwiki_managed") == "true"
            and bool(metadata.get("chunk_id"))
        )
    
    def get_or_create_index(self) -> VectorStoreIndex:
        if self.index is None:
            self.index = VectorStoreIndex.from_vector_store(
                vector_store=ChromaVectorStore(
                    chroma_collection=self.collection
                ),
                storage_context=self.storage_context
            )
        return self.index

    async def delete_documents_by_ids(self, document_ids: List[str], source_id: str = ""):
        """Delete indexed chunks/documents with bounded off-loop Chroma calls."""
        unique_ids = list(dict.fromkeys(document_id for document_id in document_ids if document_id))
        if not unique_ids:
            return
        async with self._mutation_lock:
            for document_id_batch in self._metadata_batches(unique_ids):
                if source_id:
                    await self._run_chroma_in_thread(
                        self.collection.delete,
                        where={
                            "$and": [
                                {
                                    "$or": [
                                        {"chunk_id": {"$in": document_id_batch}},
                                        {"doc_id": {"$in": document_id_batch}},
                                    ]
                                },
                                {"source_id": source_id},
                                {"contextwiki_managed": "true"},
                            ]
                        },
                    )
                    logger.debug(
                        "Deleted %s managed indexed document(s) from %s",
                        len(document_id_batch),
                        source_id,
                    )
                    continue
                await self._run_chroma_in_thread(
                    self.collection.delete,
                    where={
                        "$and": [
                            {
                                "$or": [
                                    {"chunk_id": {"$in": document_id_batch}},
                                    {"doc_id": {"$in": document_id_batch}},
                                ]
                            },
                            {"contextwiki_managed": {"$ne": "true"}},
                        ]
                    },
                )
                logger.debug(
                    "Deleted %s unmanaged indexed document(s)",
                    len(document_id_batch),
                )
    
    def _update_status(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self.status, key, value)
    
    def _update_progress(self, processed: int, total: int):
        self.status.processed_docs = processed
        self.status.progress = round(processed / total, 2)
    
    def _complete_indexing(self, message: str):
        self._update_status(
            state=IndexState.DONE,
            message=message,
            progress=1.0
        )
        logger.info(message)
