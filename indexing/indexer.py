import asyncio
import logging
import traceback
from pathlib import Path
from typing import List, Optional

from llama_index.core import VectorStoreIndex, Document, StorageContext
from llama_index.vector_stores.chroma import ChromaVectorStore

from environments.config import AppConfig
from core.models import DocumentModel, IndexStatusModel, IndexState
from core.exceptions import IndexingError
from indexing.background_tasks import safe_error_message
from indexing.manager import (
    LEGACY_MANAGED_METADATA_KEY,
    IndexManager,
    _managed_not_true_filters,
    _managed_true_filter,
)
from indexing.converter import DocumentConverter

logger = logging.getLogger(__name__)
_TRACEBACK_FRAME_LIMIT = 8


def _safe_traceback_frames(exc: BaseException) -> str:
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "none"
    cwd = Path.cwd().resolve()
    summaries = []
    for frame in frames[-_TRACEBACK_FRAME_LIMIT:]:
        frame_path = Path(frame.filename)
        try:
            display_path = str(frame_path.resolve().relative_to(cwd))
        except (OSError, ValueError):
            display_path = frame_path.name
        summaries.append(f"{display_path}:{frame.lineno}:{frame.name}")
    return " -> ".join(summaries)


def _log_indexing_failure(
    exc: BaseException,
    *,
    stage: str = "",
    operation: str = "",
) -> None:
    context = []
    if stage:
        context.append(f"indexing_stage={stage}")
    if operation:
        context.append(f"indexing_operation={operation}")
    context.append(f"exception_type={type(exc).__name__}")
    context.append(f"trace_frames={_safe_traceback_frames(exc)}")
    logger.error(
        "Indexing error: %s; %s",
        safe_error_message(exc),
        "; ".join(context),
    )


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

            stage = "starting"
            try:
                if not documents:
                    stage = "empty_input"
                    self._complete_indexing("No documents to index")
                    return

                stage = "filter_documents"
                filtered = await self._filter_documents(documents)

                if not filtered["documents"]:
                    stage = "complete_no_changes"
                    self._complete_indexing("No new or updated documents")
                    return

                stage = "batch_index"
                await self._batch_index(filtered["documents"])

                stage = "complete"
                self._complete_indexing(
                    f"Complete: {filtered['new']} new, {filtered['updated']} updated"
                )

            except Exception as e:
                error_message = safe_error_message(e)
                if not getattr(e, "_indexing_diagnostic_logged", False):
                    _log_indexing_failure(e, stage=stage)
                self._update_status(
                    state=IndexState.ERROR,
                    message=f"Error: {error_message}",
                )
                raise IndexingError(f"Indexing failed: {error_message}") from None
    
    async def _filter_documents(self, documents: List[DocumentModel]) -> dict:
        manager = IndexManager(self.collection)
        converter = DocumentConverter()
        
        new_docs = []
        new_count = 0
        update_count = 0
        
        for i, doc in enumerate(documents, 1):
            if manager.is_new(doc):
                new_count += 1
                new_docs.append(converter.to_llama_document(doc))
            elif manager.is_updated(doc):
                update_count += 1
                manager.delete_document(doc)
                new_docs.append(converter.to_llama_document(doc))
            
            if i % self.config.progress_log_interval == 0:
                self._update_progress(i, len(documents))
                await asyncio.sleep(0.01)
        
        return {
            "documents": new_docs,
            "new": new_count,
            "updated": update_count
        }
    
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

    async def _run_chroma_in_thread(
        self,
        func,
        /,
        *args,
        operation: str = "chroma_worker",
        **kwargs,
    ):
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
        except Exception as exc:
            if current is not None and current.cancelling():
                if not task.done():
                    await self._join_thread_task_preserving_cancel(task)
                elif task.exception() is not None:
                    logger.error(
                        "Chroma worker failed during cancel: %s",
                        safe_error_message(task.exception()),
                    )
                raise asyncio.CancelledError from None
            _log_indexing_failure(exc, operation=operation)
            try:
                exc._indexing_diagnostic_logged = True
            except AttributeError:
                pass
            raise

    async def _batch_index(self, documents: List[Document]):
        total = len(documents)
        
        for i in range(0, total, self.config.batch_size):
            batch = documents[i:i + self.config.batch_size]
            
            if self.index is None:
                self.index = await self._run_chroma_in_thread(
                    VectorStoreIndex.from_documents,
                    batch,
                    storage_context=self.storage_context,
                    show_progress=True,
                    operation="vector_store_from_documents",
                )
            else:
                for doc in batch:
                    await self._run_chroma_in_thread(
                        self.index.insert,
                        doc,
                        operation="vector_store_insert",
                    )
            
            processed = min(total, i + self.config.batch_size)
            self._update_progress(processed, total)
            await asyncio.sleep(0.1)
    
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
        """Delete indexed chunks/documents by stored Chroma doc_id metadata."""
        async with self._mutation_lock:
            for document_id in document_ids:
                if source_id:
                    filters = [{"doc_id": document_id}, {"source_id": source_id}]
                    self.collection.delete(
                        where={"$and": [*filters, _managed_true_filter()]}
                    )
                    self.collection.delete(
                        where={
                            "$and": [
                                *filters,
                                _managed_true_filter(LEGACY_MANAGED_METADATA_KEY),
                            ]
                        }
                    )
                    logger.debug(
                        "Deleted managed indexed document: %s from %s",
                        document_id,
                        source_id,
                    )
                    continue
                self.collection.delete(
                    where={
                        "$and": [{"doc_id": document_id}, *_managed_not_true_filters()]
                    }
                )
                logger.debug("Deleted indexed document: %s", document_id)
    
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
