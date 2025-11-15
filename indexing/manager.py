import logging
from typing import Dict
from core.models import DocumentModel
from core.utils import ContentHasher

logger = logging.getLogger(__name__)

class IndexManager:
    def __init__(self, chroma_collection):
        self.collection = chroma_collection
        self._existing_docs: Dict[str, str] = {}
        self._load_existing()
    
    def _load_existing(self):
        data = self.collection.get(include=["metadatas"])
        self._existing_docs = {
            meta.get("doc_id"): meta.get("content_hash", "")
            for meta in data["metadatas"]
        }
        logger.info(f"Loaded {len(self._existing_docs)} existing documents")
    
    def is_new(self, doc: DocumentModel) -> bool:
        return doc.id not in self._existing_docs
    
    def is_updated(self, doc: DocumentModel) -> bool:
        if doc.id not in self._existing_docs:
            return False
        
        content_hash = ContentHasher.hash_content(doc.content)
        return self._existing_docs[doc.id] != content_hash
    
    def delete_document(self, doc_id: str):
        self.collection.delete(where={"doc_id": doc_id})
        logger.info(f"Deleted outdated document: {doc_id}")