import time

import pytest

from core.models import DocumentModel, EvidenceSourceType
from indexing.chunker import DocumentChunker, _ChunkingCancelled
from indexing.converter import DocumentConverter


pytestmark = pytest.mark.unit


class _CountingText(str):
    """Count input characters revisited by line-number calculations."""

    def __new__(cls, value: str):
        instance = super().__new__(cls, value)
        instance.work_units = 0
        return instance

    def __iter__(self):
        for character in super().__iter__():
            self.work_units += 1
            yield character

    def count(self, sub, start=None, end=None):
        normalized_start = 0 if start is None else start
        normalized_end = len(self) if end is None else end
        self.work_units += max(0, normalized_end - normalized_start)
        if start is None:
            return super().count(sub)
        if end is None:
            return super().count(sub, start)
        return super().count(sub, start, end)


class _NoBulkSplitText(str):
    """Fail if line-oriented chunking materializes every line at once."""

    def splitlines(self, *args, **kwargs):
        del args, kwargs
        raise AssertionError("chunking must stream lines instead of calling splitlines")


class _BuildCountingChunker(DocumentChunker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.model_builds = 0

    def _build_chunk(self, *args, **kwargs):
        self.model_builds += 1
        return super()._build_chunk(*args, **kwargs)


class _ReadTrackingText(str):
    def __new__(cls, value: str):
        instance = super().__new__(cls, value)
        instance.max_read_offset = 0
        return instance

    def __getitem__(self, key):
        if isinstance(key, int):
            self.max_read_offset = max(self.max_read_offset, key + 1)
        elif isinstance(key, slice) and key.stop is not None:
            self.max_read_offset = max(self.max_read_offset, key.stop)
        return super().__getitem__(key)


class _ReadObservingChunker(DocumentChunker):
    def __init__(self, tracked_text, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tracked_text = tracked_text
        self.first_build_read_offset = None

    def _build_chunk(self, *args, **kwargs):
        if self.first_build_read_offset is None:
            self.first_build_read_offset = self.tracked_text.max_read_offset
        return super()._build_chunk(*args, **kwargs)


def _line_calculation_work(*, markdown: bool, size: int) -> int:
    content = _CountingText(("x\n" * ((size + 1) // 2))[:size])
    document = DocumentModel(
        id="scaling",
        source_id="source_fixture",
        title="scaling.md" if markdown else "scaling.txt",
        content="fixture",
        url="",
        platform="Fixture",
        path="scaling.md" if markdown else "scaling.txt",
    )
    chunker = DocumentChunker(max_chars=64, overlap_chars=0)

    if markdown:
        chunker._split_section_text(document, content, 0, 1)
    else:
        chunker._chunk_plain_text(document, content)
    return content.work_units


@pytest.mark.parametrize("markdown", [False, True])
def test_chunk_line_number_calculation_scales_linearly(markdown):
    small_work = _line_calculation_work(markdown=markdown, size=8_192)
    large_work = _line_calculation_work(markdown=markdown, size=16_384)

    assert small_work > 0
    assert 1.8 <= large_work / small_work <= 2.2


def test_line_oriented_chunking_streams_without_bulk_splitlines():
    document = DocumentModel(
        id="streaming",
        source_id="source_fixture",
        title="streaming.md",
        content="fixture",
        url="",
        platform="Fixture",
        path="streaming.md",
    )
    markdown = _NoBulkSplitText("# Intro\nline one\n## Next\nline two\n")
    code = _NoBulkSplitText(
        "def first():\n    return 1\n\ndef second():\n    return 2\n"
    )
    chunker = DocumentChunker(max_chars=32, overlap_chars=0)

    assert chunker._has_markdown_heading(markdown)
    assert [chunk.text for chunk in chunker._chunk_markdown(document, markdown)] == [
        "# Intro\nline one",
        "## Next\nline two",
    ]
    assert [chunk.line_start for chunk in chunker._chunk_code(document, code)] == [1, 4]


def test_oversized_markdown_section_emits_chunks_before_reading_whole_section():
    content = _ReadTrackingText("# Experience\n" + ("bounded body line\n" * 20_000))
    document = DocumentModel(
        id="streaming-section",
        source_id="source_fixture",
        title="streaming-section.md",
        content="fixture",
        url="",
        platform="Fixture",
        path="streaming-section.md",
        evidence_source_type=EvidenceSourceType.CAREER_NOTE,
    )
    chunker = _ReadObservingChunker(
        content,
        max_chars=1_200,
        overlap_chars=120,
    )

    chunks = chunker._chunk_markdown(document, content)

    assert len(chunks) > 100
    assert chunker.first_build_read_offset is not None
    assert chunker.first_build_read_offset < len(content) // 4
    assert all(len(chunk.text) <= 1_200 for chunk in chunks)
    assert chunks[0].section_title == "Experience"


def _oversized_single_line_markdown_seconds(size: int) -> float:
    content = "# Experience\n" + ("x" * size)
    document = DocumentModel(
        id=f"scaling-{size}",
        source_id="source_fixture",
        title="scaling.md",
        content="fixture",
        url="",
        platform="Fixture",
        path="scaling.md",
        evidence_source_type=EvidenceSourceType.CAREER_NOTE,
    )
    chunker = DocumentChunker(max_chars=256, overlap_chars=32)
    started = time.perf_counter()
    chunker._chunk_markdown(document, content)
    return time.perf_counter() - started


def test_oversized_single_line_markdown_scaling_is_near_linear_30k_to_60k():
    small_seconds = min(
        _oversized_single_line_markdown_seconds(30_000) for _ in range(2)
    )
    large_seconds = min(
        _oversized_single_line_markdown_seconds(60_000) for _ in range(2)
    )

    assert small_seconds > 0
    assert large_seconds / small_seconds < 3.2


@pytest.mark.parametrize("markdown_section", [False, True])
def test_chunk_model_build_polls_cooperative_stop_without_finishing_full_output(
    markdown_section,
):
    document = DocumentModel(
        id="cancel-build",
        source_id="source_fixture",
        title="cancel-build.txt",
        content="fixture",
        url="",
        platform="Fixture",
        path="cancel-build.txt",
    )
    chunker = _BuildCountingChunker(max_chars=16, overlap_chars=0)
    content = "evidence line\n" * 1_000

    with pytest.raises(_ChunkingCancelled):
        if markdown_section:
            chunker._split_section_text(
                document,
                content,
                0,
                1,
                stop_checker=lambda: chunker.model_builds >= 4,
            )
        else:
            chunker._chunk_plain_text(
                document,
                content,
                stop_checker=lambda: chunker.model_builds >= 4,
            )

    assert chunker.model_builds == 4


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
        version_id="page-version-42",
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
    assert first[0].version_id == "page-version-42"
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
        content=("# Intro\n```md\n~~~\n# should stay fenced\n```\n## Next\nbody\n"),
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
        content=("# Intro\n``` bad ` info\n## Real Heading\nbody\n"),
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

    markdown_chunks = DocumentChunker(max_chars=120, overlap_chars=0).chunk_document(
        markdown
    )
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


def test_career_chunk_ids_use_content_hash_and_duplicate_ordinal_not_position():
    document = DocumentModel(
        id="career-note",
        document_id="career-note",
        source_id="source_career",
        title="Career note",
        content="# Repeat\nSame evidence.\n# Repeat\nSame evidence.\n",
        url="career://career-note",
        platform="career",
        path="note.md",
        evidence_source_type="career_note",
        experience_type="unknown",
    )
    chunker = DocumentChunker(max_chars=500, overlap_chars=0)

    original = chunker.chunk_document(document)
    prepended = chunker.chunk_document(
        document.model_copy(
            update={
                "content": "# New\nNew evidence.\n" + document.content,
            }
        )
    )

    assert len({chunk.chunk_id for chunk in original}) == 2
    assert [chunk.chunk_id for chunk in prepended[1:]] == [
        chunk.chunk_id for chunk in original
    ]
    assert all(chunk.content_hash in chunk.chunk_id for chunk in original)


def test_non_career_chunk_identity_and_metadata_remain_legacy_compatible():
    document = DocumentModel(
        id="legacy",
        document_id="old-document-id",
        external_id="external-document-id",
        source_id="source_github",
        title="Legacy",
        content="Legacy content.",
        url="https://example.test/legacy",
        platform="GitHub",
        path="legacy.txt",
    )

    chunk = DocumentChunker(max_chars=500, overlap_chars=0).chunk_document(document)[0]

    assert chunk.chunk_id.startswith("external-document-id:chunk:0:")
    assert chunk.exact_quote == ""
    assert chunk.document_title == ""


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
