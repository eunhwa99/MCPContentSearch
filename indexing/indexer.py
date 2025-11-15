import asyncio
import logging
from typing import List, Optional

from llama_index.core import VectorStoreIndex, Document, StorageContext
from llama_index.vector_stores.chroma import ChromaVectorStore

from environments.config import AppConfig
from core.models import DocumentModel, IndexStatusModel, IndexState
from core.exceptions import IndexingError
from indexing.manager import IndexManager
from indexing.converter import DocumentConverter

logger = logging.getLogger(__name__)

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
    
    async def index_documents(self, documents: List[DocumentModel]):
        """Index the provided documents."""
        self._update_status(
            state=IndexState.RUNNING,
            message="Starting indexing...",
            total_docs=len(documents)
        )
        
        try:
            if not documents:
                self._complete_indexing("No documents to index")
                return
            
            filtered = await self._filter_documents(documents)
            
            if not filtered["documents"]:
                self._complete_indexing("No new or updated documents")
                return
            
            await self._batch_index(filtered["documents"])
            
            self._complete_indexing(
                f"Complete: {filtered['new']} new, {filtered['updated']} updated"
            )
        
        except Exception as e:
            logger.error(f"Indexing error: {e}")
            self._update_status(
                state=IndexState.ERROR,
                message=f"Error: {str(e)}"
            )
            raise IndexingError(f"Indexing failed: {e}")
    
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
                manager.delete_document(doc.id)
                new_docs.append(converter.to_llama_document(doc))
            
            if i % self.config.progress_log_interval == 0:
                self._update_progress(i, len(documents))
                await asyncio.sleep(0.01)
        
        return {
            "documents": new_docs,
            "new": new_count,
            "updated": update_count
        }
    
    async def _batch_index(self, documents: List[Document]):
        total = len(documents)
        
        for i in range(0, total, self.config.batch_size):
            batch = documents[i:i + self.config.batch_size]
            
            if self.index is None:
                self.index = VectorStoreIndex.from_documents(
                    batch,
                    storage_context=self.storage_context,
                    show_progress=True
                )
            else:
                for doc in batch:
                    self.index.insert(doc)
            
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