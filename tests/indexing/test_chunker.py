import pytest

from core.models import DocumentModel
from indexing.chunker import DocumentChunker
from indexing.converter import DocumentConverter


pytestmark = pytest.mark.unit


def test_chunker_creates_stable_citation_metadata():
    document = DocumentModel(
        id="tistory_42",
        source_id="source_tistory",
        title="RAG Operations",
        content="alpha beta gamma\n" * 20,
        url="https://example.tistory.com/42",
        platform="Tistory",
        path="/42",
        updated_at="2026-05-20T00:00:00Z",
    )

    chunker = DocumentChunker(max_chars=80, overlap_chars=10)

    first = chunker.chunk_document(document)
    second = chunker.chunk_document(document)

    assert len(first) > 1
    assert first == second
    assert first[0].chunk_id.startswith("tistory_42:chunk:0:")
    assert first[0].document_id == "tistory_42"
    assert first[0].source_id == "source_tistory"
    assert first[0].url == "https://example.tistory.com/42"
    assert first[0].line_start == 1
    assert first[0].line_end >= first[0].line_start


def test_converter_marks_only_contextwiki_chunks_as_managed():
    raw = DocumentConverter.to_llama_document(
        DocumentModel(
            id="raw_doc",
            title="Raw",
            content="raw legacy document",
            url="https://example.com/raw",
            platform="Notion",
        )
    )
    chunk = DocumentConverter.to_llama_document(
        DocumentModel(
            id="chunk_doc",
            chunk_id="chunk_doc",
            document_id="source_doc",
            source_id="source_fake",
            title="Chunk",
            content="citation chunk",
            url="https://example.com/chunk",
            platform="Notion",
        )
    )

    assert raw.metadata["contextwiki_managed"] == "false"
    assert chunk.metadata["contextwiki_managed"] == "true"
