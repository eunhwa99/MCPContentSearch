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
            self._metadata_key(meta): meta.get("content_hash", "")
            for meta in data["metadatas"]
            if meta and meta.get("doc_id")
        }
        logger.info(f"Loaded {len(self._existing_docs)} existing documents")

    def is_new(self, doc: DocumentModel) -> bool:
        return self._document_key(doc) not in self._existing_docs

    def is_updated(self, doc: DocumentModel) -> bool:
        document_key = self._document_key(doc)
        if document_key not in self._existing_docs:
            return False

        content_hash = ContentHasher.hash_content(doc.content)
        return self._existing_docs[document_key] != content_hash

    def delete_document(self, doc: DocumentModel | str, source_id: str = ""):
        doc_id = doc.id if isinstance(doc, DocumentModel) else doc
        resolved_source_id = doc.source_id if isinstance(doc, DocumentModel) else source_id
        managed = self._is_contextwiki_managed(doc) if isinstance(doc, DocumentModel) else False
        if resolved_source_id:
            filters = [{"doc_id": doc_id}, {"source_id": resolved_source_id}]
            filters.append(
                {"contextwiki_managed": "true" if managed else {"$ne": "true"}}
            )
            self.collection.delete(where={"$and": filters})
            logger.info("Deleted outdated document: %s from %s", doc_id, resolved_source_id)
            return
        self.collection.delete(
            where={"$and": [{"doc_id": doc_id}, {"contextwiki_managed": {"$ne": "true"}}]}
        )
        logger.info("Deleted outdated document: %s", doc_id)

    @staticmethod
    def _document_key(doc: DocumentModel) -> str:
        return IndexManager._key(
            doc.id,
            doc.source_id,
            IndexManager._is_contextwiki_managed(doc),
        )

    @staticmethod
    def _metadata_key(metadata: dict) -> str:
        return IndexManager._key(
            metadata.get("doc_id", ""),
            metadata.get("source_id", ""),
            str(metadata.get("contextwiki_managed", "false")).lower() == "true",
        )

    @staticmethod
    def _key(doc_id: str, source_id: str, managed: bool) -> str:
        managed_key = "managed" if managed else "raw"
        return f"{managed_key}:{source_id}:{doc_id}" if source_id else f"{managed_key}:{doc_id}"

    @staticmethod
    def _is_contextwiki_managed(doc: DocumentModel) -> bool:
        return bool(doc.chunk_id and doc.document_id and doc.source_id)
