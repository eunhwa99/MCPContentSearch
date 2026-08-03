import logging
from collections.abc import Iterable

from core.models import DocumentModel
from core.utils import ContentHasher

logger = logging.getLogger(__name__)


class IndexManager:
    """Classify documents against one caller-bounded Chroma snapshot."""

    def __init__(self, metadatas: Iterable[dict | None]):
        self._existing_docs = {
            self._metadata_key(meta): meta.get("content_hash", "")
            for meta in metadatas
            if meta and (meta.get("doc_id") or meta.get("chunk_id"))
        }
        logger.debug("Loaded %s existing document metadata rows", len(self._existing_docs))

    def is_new(self, doc: DocumentModel) -> bool:
        return self._document_key(doc) not in self._existing_docs

    def is_updated(self, doc: DocumentModel) -> bool:
        document_key = self._document_key(doc)
        if document_key not in self._existing_docs:
            return False

        content_hash = ContentHasher.hash_content(doc.content)
        return self._existing_docs[document_key] != content_hash

    @staticmethod
    def _document_key(doc: DocumentModel) -> str:
        return IndexManager._key(
            doc.id,
            doc.source_id,
            IndexManager._is_contextwiki_managed(doc),
        )

    @staticmethod
    def _metadata_key(metadata: dict) -> str:
        managed = str(metadata.get("contextwiki_managed", "false")).lower() == "true"
        return IndexManager._key(
            metadata.get("chunk_id") or metadata.get("doc_id", ""),
            metadata.get("source_id", ""),
            managed,
        )

    @staticmethod
    def _key(doc_id: str, source_id: str, managed: bool) -> str:
        managed_key = "managed" if managed else "raw"
        return f"{managed_key}:{source_id}:{doc_id}" if source_id else f"{managed_key}:{doc_id}"

    @staticmethod
    def _is_contextwiki_managed(doc: DocumentModel) -> bool:
        return bool(doc.chunk_id and doc.document_id and doc.source_id)
