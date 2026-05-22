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


def test_markdown_chunking_uses_heading_sections():
    document = DocumentModel(
        id="readme",
        source_id="source_github",
        title="README.md",
        content="# Intro\nContextWiki overview.\n## Install\nRun uv sync.\n",
        url="https://github.com/eunhwa99/MCPContentSearch/blob/main/README.md",
        canonical_url="https://github.com/eunhwa99/MCPContentSearch/blob/main/README.md",
        platform="GitHub",
        path="README.md",
        updated_at="2026-05-22T00:00:00Z",
    )

    chunks = DocumentChunker(max_chars=120, overlap_chars=0).chunk_document(document)

    assert [chunk.line_start for chunk in chunks] == [1, 3]
    assert [chunk.line_end for chunk in chunks] == [2, 4]
    assert chunks[0].text == "# Intro\nContextWiki overview."
    assert chunks[1].text == "## Install\nRun uv sync."
    assert all(chunk.url == document.canonical_url for chunk in chunks)


def test_markdown_chunking_uses_setext_heading_sections():
    document = DocumentModel(
        id="readme",
        source_id="source_github",
        title="README.md",
        content="Intro\n=====\nContextWiki overview.\n\nInstall\n-------\nRun uv sync.\n",
        url="https://example.com/README.md",
        platform="GitHub",
        path="README.md",
    )

    chunks = DocumentChunker(max_chars=120, overlap_chars=0).chunk_document(document)

    assert [chunk.line_start for chunk in chunks] == [1, 5]
    assert [chunk.line_end for chunk in chunks] == [3, 7]
    assert chunks[0].text == "Intro\n=====\nContextWiki overview."
    assert chunks[1].text == "Install\n-------\nRun uv sync."


def test_markdown_chunking_keeps_multiline_setext_heading_together():
    document = DocumentModel(
        id="readme",
        source_id="source_github",
        title="README.md",
        content="First heading line\nsecond heading line\n---\nBody text.\n",
        url="https://example.com/README.md",
        platform="GitHub",
        path="README.md",
    )

    chunks = DocumentChunker(max_chars=120, overlap_chars=0).chunk_document(document)

    assert len(chunks) == 1
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 4
    assert chunks[0].text == "First heading line\nsecond heading line\n---\nBody text."


def test_markdown_setext_chunking_stops_at_previous_atx_heading():
    document = DocumentModel(
        id="readme",
        source_id="source_github",
        title="README.md",
        content="# Intro\nSubheading\n---\nBody text.\n",
        url="https://example.com/README.md",
        platform="GitHub",
        path="README.md",
    )

    chunks = DocumentChunker(max_chars=120, overlap_chars=0).chunk_document(document)

    assert len(chunks) == 2
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 1
    assert chunks[0].text == "# Intro"
    assert chunks[1].line_start == 2
    assert chunks[1].line_end == 4
    assert chunks[1].text == "Subheading\n---\nBody text."


def test_markdown_setext_chunking_stops_after_fenced_block():
    document = DocumentModel(
        id="readme",
        source_id="source_github",
        title="README.md",
        content="# Intro\n```\ncode\n```\nSubheading\n---\nBody text.\n",
        url="https://example.com/README.md",
        platform="GitHub",
        path="README.md",
    )

    chunks = DocumentChunker(max_chars=160, overlap_chars=0).chunk_document(document)

    assert len(chunks) == 2
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 4
    assert "code" in chunks[0].text
    assert chunks[1].line_start == 5
    assert chunks[1].line_end == 7
    assert chunks[1].text == "Subheading\n---\nBody text."


def test_markdown_chunking_separates_consecutive_setext_sections():
    document = DocumentModel(
        id="readme",
        source_id="source_github",
        title="README.md",
        content="Title\n=====\nBody line\n\nSubheading\n----\nMore body\n",
        url="https://example.com/README.md",
        platform="GitHub",
        path="README.md",
    )

    chunks = DocumentChunker(max_chars=160, overlap_chars=0).chunk_document(document)

    assert len(chunks) == 2
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 3
    assert chunks[0].text == "Title\n=====\nBody line"
    assert chunks[1].line_start == 5
    assert chunks[1].line_end == 7
    assert chunks[1].text == "Subheading\n----\nMore body"


def test_markdown_chunking_separates_blank_delimited_multiline_setext_sections():
    document = DocumentModel(
        id="readme",
        source_id="source_github",
        title="README.md",
        content=(
            "First heading line\n"
            "second heading line\n"
            "=====\n"
            "\n"
            "Next heading line\n"
            "second next line\n"
            "-----\n"
            "Body text.\n"
        ),
        url="https://example.com/README.md",
        platform="GitHub",
        path="README.md",
    )

    chunks = DocumentChunker(max_chars=200, overlap_chars=0).chunk_document(document)

    assert len(chunks) == 2
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 3
    assert chunks[0].text == "First heading line\nsecond heading line\n====="
    assert chunks[1].line_start == 5
    assert chunks[1].line_end == 8
    assert chunks[1].text == "Next heading line\nsecond next line\n-----\nBody text."


def test_markdown_chunking_keeps_no_blank_multiline_setext_heading_together():
    document = DocumentModel(
        id="readme",
        source_id="source_github",
        title="README.md",
        content=(
            "First heading line\n"
            "second heading line\n"
            "=====\n"
            "Next heading line\n"
            "second next line\n"
            "-----\n"
            "Body text.\n"
        ),
        url="https://example.com/README.md",
        platform="GitHub",
        path="README.md",
    )

    chunks = DocumentChunker(max_chars=200, overlap_chars=0).chunk_document(document)

    assert len(chunks) == 2
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 3
    assert chunks[0].text == "First heading line\nsecond heading line\n====="
    assert chunks[1].line_start == 4
    assert chunks[1].line_end == 7
    assert chunks[1].text == "Next heading line\nsecond next line\n-----\nBody text."


def test_markdown_chunking_splits_oversized_heading_sections():
    document = DocumentModel(
        id="readme",
        source_id="source_github",
        title="README.md",
        content="# Intro\n" + ("x" * 2500) + "\n## Next\nsmall\n",
        url="https://example.com/README.md",
        platform="GitHub",
        path="README.md",
    )

    chunks = DocumentChunker(max_chars=1000, overlap_chars=0).chunk_document(document)

    assert len(chunks) == 4
    assert all(len(chunk.text) <= 1000 for chunk in chunks)
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 2
    assert chunks[1].line_start == 2
    assert chunks[1].line_end == 2
    assert chunks[-1].text == "## Next\nsmall"
    assert chunks[-1].line_start == 3
    assert chunks[-1].line_end == 4


def test_markdown_chunking_ignores_headings_inside_fenced_code():
    document = DocumentModel(
        id="readme",
        source_id="source_github",
        title="README.md",
        content="# Intro\n```python\n# not a heading\n```\n## Install\nRun uv sync.\n",
        url="https://example.com/README.md",
        platform="GitHub",
        path="README.md",
    )

    chunks = DocumentChunker(max_chars=120, overlap_chars=0).chunk_document(document)

    assert len(chunks) == 2
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 4
    assert "# not a heading" in chunks[0].text
    assert chunks[1].line_start == 5
    assert chunks[1].line_end == 6


def test_markdown_chunking_tracks_fence_marker_type():
    document = DocumentModel(
        id="readme",
        source_id="source_github",
        title="README.md",
        content=(
            "# Intro\n"
            "```md\n"
            "~~~\n"
            "# should stay fenced\n"
            "```\n"
            "## Next\n"
            "body\n"
        ),
        url="https://example.com/README.md",
        platform="GitHub",
        path="README.md",
    )

    chunks = DocumentChunker(max_chars=160, overlap_chars=0).chunk_document(document)

    assert len(chunks) == 2
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 5
    assert "# should stay fenced" in chunks[0].text
    assert chunks[1].line_start == 6
    assert chunks[1].line_end == 7


def test_markdown_chunking_requires_valid_closing_fence():
    document = DocumentModel(
        id="readme",
        source_id="source_github",
        title="README.md",
        content=(
            "# Intro\n"
            "```md\n"
            "```not a close\n"
            "# should stay fenced\n"
            "```\n"
            "    ```\n"
            "## Next\n"
            "body\n"
        ),
        url="https://example.com/README.md",
        platform="GitHub",
        path="README.md",
    )

    chunks = DocumentChunker(max_chars=200, overlap_chars=0).chunk_document(document)

    assert len(chunks) == 2
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 6
    assert "# should stay fenced" in chunks[0].text
    assert chunks[1].line_start == 7
    assert chunks[1].line_end == 8


def test_markdown_chunking_rejects_backtick_fence_openers_with_backtick_info():
    document = DocumentModel(
        id="readme",
        source_id="source_github",
        title="README.md",
        content=(
            "# Intro\n"
            "``` bad ` info\n"
            "## Real Heading\n"
            "body\n"
        ),
        url="https://example.com/README.md",
        platform="GitHub",
        path="README.md",
    )

    chunks = DocumentChunker(max_chars=200, overlap_chars=0).chunk_document(document)

    assert len(chunks) == 2
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 2
    assert chunks[1].line_start == 3
    assert chunks[1].line_end == 4


def test_markdown_chunking_uses_commonmark_atx_heading_rules():
    document = DocumentModel(
        id="readme",
        source_id="source_github",
        title="README.md",
        content=(
            "# Intro\n"
            "    # indented code\n"
            "###NoSpace\n"
            "####### too many\n"
            "   ## Valid\n"
            "body\n"
        ),
        url="https://example.com/README.md",
        platform="GitHub",
        path="README.md",
    )

    chunks = DocumentChunker(max_chars=200, overlap_chars=0).chunk_document(document)

    assert len(chunks) == 2
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 4
    assert "###NoSpace" in chunks[0].text
    assert "####### too many" in chunks[0].text
    assert chunks[1].line_start == 5
    assert chunks[1].line_end == 6


def test_code_chunking_preserves_line_ranges():
    document = DocumentModel(
        id="tools-py",
        source_id="source_github",
        title="tools.py",
        content="\n".join(
            [
                "def sync_source():",
                "    prepare()",
                "    run()",
                "",
                "class Worker:",
                "    pass",
            ]
        ),
        url="https://github.com/eunhwa99/MCPContentSearch/blob/main/api/tools.py",
        platform="GitHub",
        path="api/tools.py",
        updated_at="2026-05-22T00:00:00Z",
    )

    chunks = DocumentChunker(max_chars=35, overlap_chars=0).chunk_document(document)

    assert len(chunks) >= 2
    assert chunks[0].path == "api/tools.py"
    assert chunks[0].line_start == 1
    assert chunks[0].line_end >= chunks[0].line_start
    assert chunks[1].line_start == chunks[0].line_end + 1
    assert "def sync_source" in chunks[0].text


def test_code_chunking_preserves_blank_line_ranges_between_chunks():
    document = DocumentModel(
        id="tools-py",
        source_id="source_github",
        title="tools.py",
        content="aaa\n\nbbb\n",
        url="https://example.com/tools.py",
        platform="GitHub",
        path="tools.py",
    )

    chunks = DocumentChunker(max_chars=4, overlap_chars=0).chunk_document(document)

    assert [(chunk.line_start, chunk.line_end) for chunk in chunks] == [(1, 2), (3, 3)]
    assert chunks[0].text == "aaa\n"
    assert chunks[1].text == "bbb"


def test_code_chunking_splits_oversized_single_lines():
    document = DocumentModel(
        id="bundle-js",
        source_id="source_github",
        title="bundle.js",
        content="x" * 2500,
        url="https://example.com/bundle.js",
        platform="GitHub",
        path="dist/bundle.js",
    )

    chunks = DocumentChunker(max_chars=1000, overlap_chars=0).chunk_document(document)

    assert len(chunks) == 3
    assert all(len(chunk.text) <= 1000 for chunk in chunks)
    assert [(chunk.line_start, chunk.line_end) for chunk in chunks] == [(1, 1)] * 3


def test_source_aware_chunking_preserves_leading_blank_line_numbers():
    markdown = DocumentModel(
        id="readme",
        source_id="source_github",
        title="README.md",
        content="\n\n# Intro\nContextWiki overview.\n",
        url="https://example.com/README.md",
        platform="GitHub",
        path="README.md",
    )
    code = DocumentModel(
        id="tools",
        source_id="source_github",
        title="tools.py",
        content="\n\ndef sync_source():\n    pass\n",
        url="https://example.com/tools.py",
        platform="GitHub",
        path="tools.py",
    )

    markdown_chunks = DocumentChunker(max_chars=120, overlap_chars=0).chunk_document(markdown)
    code_chunks = DocumentChunker(max_chars=120, overlap_chars=0).chunk_document(code)

    assert markdown_chunks[0].line_start == 3
    assert markdown_chunks[0].line_end == 4
    assert code_chunks[0].line_start == 3
    assert code_chunks[0].line_end == 4


def test_chunk_identity_prefers_external_id_over_document_id():
    document = DocumentModel(
        id="blob-sha",
        document_id="old-doc-id",
        external_id="owner/repo:api/tools.py",
        source_id="source_github",
        title="tools.py",
        content="def sync_source():\n    pass\n",
        url="https://example.com/tools.py",
        platform="GitHub",
        path="api/tools.py",
    )

    chunk = DocumentChunker(max_chars=120, overlap_chars=0).chunk_document(document)[0]

    assert chunk.document_id == "owner/repo:api/tools.py"
    assert chunk.chunk_id.startswith("owner/repo:api/tools.py:chunk:0:")


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


def test_converter_prefers_external_id_for_document_metadata():
    converted = DocumentConverter.to_llama_document(
        DocumentModel(
            id="blob-sha",
            document_id="old-doc-id",
            external_id="owner/repo:api/tools.py",
            chunk_id="chunk-1",
            source_id="source_github",
            title="tools.py",
            content="citation chunk",
            url="https://example.com/tools.py",
            platform="GitHub",
            path="api/tools.py",
        )
    )

    assert converted.metadata["document_id"] == "owner/repo:api/tools.py"
