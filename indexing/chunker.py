from core.models import ChunkModel, DocumentModel
from core.utils import ContentHasher


class DocumentChunker:
    """Deterministic character chunker with best-effort line metadata."""

    def __init__(self, max_chars: int = 1200, overlap_chars: int = 120):
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        if overlap_chars < 0:
            raise ValueError("overlap_chars must be non-negative")
        if overlap_chars >= max_chars:
            raise ValueError("overlap_chars must be smaller than max_chars")
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars

    def chunk_document(self, document: DocumentModel) -> list[ChunkModel]:
        content = document.content.strip()
        if not content:
            return []

        chunks = []
        document_id = document.document_id or document.id
        start = 0
        chunk_index = 0
        while start < len(content):
            end = min(len(content), start + self.max_chars)
            text = content[start:end].strip()
            if text:
                content_hash = ContentHasher.hash_content(text)
                line_start = content.count("\n", 0, start) + 1
                line_end = line_start + text.count("\n")
                chunks.append(
                    ChunkModel(
                        chunk_id=f"{document_id}:chunk:{chunk_index}:{content_hash[:12]}",
                        document_id=document_id,
                        source_id=document.source_id,
                        title=document.title,
                        text=text,
                        url=document.url,
                        path=document.path or document.title,
                        chunk_index=chunk_index,
                        line_start=line_start,
                        line_end=line_end,
                        content_hash=content_hash,
                        updated_at=document.updated_at or document.date,
                    )
                )
                chunk_index += 1

            if end >= len(content):
                break
            start = end - self.overlap_chars

        return chunks
