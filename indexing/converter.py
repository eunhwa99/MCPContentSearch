from llama_index.core import Document
from core.models import DocumentModel
from core.utils import ContentHasher

class DocumentConverter:
    """Convert DocumentModel to LlamaIndex Document"""
    
    @staticmethod
    def to_llama_document(doc: DocumentModel) -> Document:
        """DocumentModel -> LlamaIndex Document"""
        content_hash = ContentHasher.hash_content(doc.content)
        
        return Document(
            text=doc.content,
            metadata={
                "title": doc.title,
                "platform": doc.platform,
                "url": doc.url,
                "date": doc.date,
                "doc_id": doc.id,
                "content_hash": content_hash,
            },
        )