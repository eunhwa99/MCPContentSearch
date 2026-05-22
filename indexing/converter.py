from llama_index.core import Document
from core.models import DocumentModel
from core.utils import ContentHasher

class DocumentConverter:
    """Convert DocumentModel to LlamaIndex Document"""
    
    @staticmethod
    def to_llama_document(doc: DocumentModel) -> Document:
        """DocumentModel -> LlamaIndex Document"""
        content_hash = ContentHasher.hash_content(doc.content)
        is_contextwiki_chunk = bool(doc.chunk_id and doc.document_id and doc.source_id)
        document_id = doc.external_id or doc.document_id or doc.id
        
        return Document(
            text=doc.content,
            metadata={
                "title": doc.title,
                "platform": doc.platform,
                "url": doc.url,
                "canonical_url": doc.canonical_url or doc.url,
                "date": doc.date,
                "doc_id": doc.id,
                "chunk_id": doc.chunk_id or doc.id,
                "document_id": document_id,
                "external_id": doc.external_id,
                "source_id": doc.source_id,
                "path": doc.path,
                "chunk_index": doc.chunk_index if doc.chunk_index is not None else -1,
                "line_start": doc.line_start if doc.line_start is not None else -1,
                "line_end": doc.line_end if doc.line_end is not None else -1,
                "updated_at": doc.updated_at,
                "last_seen_at": doc.last_seen_at,
                "last_seen_sync_id": doc.last_seen_sync_id,
                "version_id": doc.version_id,
                "contextwiki_managed": "true" if is_contextwiki_chunk else "false",
                "content_hash": content_hash,
            },
        )
