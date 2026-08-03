from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import time
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pydantic import ValidationError

from core.exceptions import CareerManifestParsingError, ParsingError
from core.models import (
    DocumentModel,
    EvidenceSourceType,
    ExperienceType,
    SourceType,
)
from indexing.chunker import DocumentChunker
import parsing.career_documents as career_documents_module
from parsing.career_documents import CareerDocumentParser
from tests.fixtures.career_documents import write_minimal_docx, write_minimal_pdf


pytestmark = pytest.mark.unit


EXPECTED_EVIDENCE_SOURCE_TYPES = {
    "resume",
    "previous_resume",
    "project",
    "github_readme",
    "behavioral_story",
    "career_note",
    "skills_inventory",
}
EXPECTED_EXPERIENCE_TYPES = {
    "professional",
    "academic",
    "personal_project",
    "prototype",
    "unknown",
}


class _NoBulkSplitText(str):
    def splitlines(self, *args, **kwargs):
        del args, kwargs
        raise AssertionError("career title validation must stream lines")


def test_career_manifest_parsing_error_carries_only_valid_progress_metrics():
    error = CareerManifestParsingError(
        "Career manifest document parsing failed",
        attempted_documents=2,
        completed_documents=1,
        parsing_latency_ms=4.5,
    )

    assert isinstance(error, ParsingError)
    assert error.attempted_documents == 2
    assert error.completed_documents == 1
    assert error.parsing_latency_ms == 4.5
    assert vars(error) == {
        "attempted_documents": 2,
        "completed_documents": 1,
        "parsing_latency_ms": 4.5,
    }


@pytest.mark.parametrize(
    ("attempted", "completed", "latency"),
    [
        (-1, 0, 0.0),
        (1, -1, 0.0),
        (1, 2, 0.0),
        (True, 0, 0.0),
        (1, 0, float("inf")),
    ],
)
def test_career_manifest_parsing_error_rejects_invalid_progress_metrics(
    attempted,
    completed,
    latency,
):
    with pytest.raises(ValueError, match="career manifest parsing progress"):
        CareerManifestParsingError(
            "Career manifest document parsing failed",
            attempted_documents=attempted,
            completed_documents=completed,
            parsing_latency_ms=latency,
        )


def _parse(
    root: Path,
    path: Path,
    *,
    source_type: EvidenceSourceType = EvidenceSourceType.RESUME,
    experience_type: ExperienceType | None = ExperienceType.PROFESSIONAL,
    **metadata,
) -> DocumentModel:
    return CareerDocumentParser(root=root).parse_file(
        path,
        source_type=source_type,
        experience_type=experience_type,
        **metadata,
    )


def test_career_taxonomies_are_fixed_and_separate_from_connector_type():
    assert {item.value for item in EvidenceSourceType} == EXPECTED_EVIDENCE_SOURCE_TYPES
    assert {item.value for item in ExperienceType} == EXPECTED_EXPERIENCE_TYPES
    assert SourceType.CAREER.value == "career"
    assert not EXPECTED_EVIDENCE_SOURCE_TYPES.intersection(
        {item.value for item in SourceType}
    )


def test_career_document_metadata_rejects_unknown_taxonomy_values():
    common = {
        "id": "career-doc",
        "title": "Career document",
        "content": "Synthetic evidence only.",
        "url": "career://career-doc",
        "platform": "career",
    }

    with pytest.raises(ValidationError):
        DocumentModel(**common, evidence_source_type="production_resume")
    with pytest.raises(ValidationError):
        DocumentModel(**common, experience_type="production")


def test_markdown_parser_preserves_text_and_normalized_career_metadata(tmp_path):
    path = tmp_path / "resume.md"
    path.write_text(
        "# Synthetic Resume\n\n## Experience\nBuilt a reliable queue.\n",
        encoding="utf-8",
    )

    document = _parse(
        tmp_path,
        path,
        company="Example Corp",
        role="Backend Engineer",
        project="Queue migration",
        start_date="2024-01",
        end_date="2025-01",
    )

    assert document.evidence_source_type == EvidenceSourceType.RESUME
    assert document.experience_type == ExperienceType.PROFESSIONAL
    assert document.file_name == "resume.md"
    assert document.document_title == "Synthetic Resume"
    assert "Built a reliable queue." in document.content
    assert document.company == "Example Corp"
    assert document.role == "Backend Engineer"
    assert document.project == "Queue migration"
    assert document.start_date == "2024-01"
    assert document.end_date == "2025-01"
    assert document.content_hash
    assert document.document_version_id


def test_text_parser_defaults_project_experience_to_unknown_without_inference(tmp_path):
    path = tmp_path / "prototype.txt"
    path.write_text(
        "Personal prototype used a queue and Kubernetes.",
        encoding="utf-8",
    )

    document = _parse(
        tmp_path,
        path,
        source_type=EvidenceSourceType.PROJECT,
        experience_type=None,
    )

    assert document.evidence_source_type == EvidenceSourceType.PROJECT
    assert document.experience_type == ExperienceType.UNKNOWN
    assert document.document_title == "prototype"
    assert document.content == "Personal prototype used a queue and Kubernetes."


def test_parser_does_not_label_posix_ctime_as_creation_time(monkeypatch, tmp_path):
    path = tmp_path / "career.txt"
    path.write_text("Synthetic evidence.", encoding="utf-8")
    parser = CareerDocumentParser(root=tmp_path)
    original_read = parser._read_bounded

    def read_without_birthtime(relative_path, safe_name):
        raw, _ = original_read(relative_path, safe_name)
        return raw, SimpleNamespace(st_ctime=10.0, st_mtime=20.0)

    monkeypatch.setattr(parser, "_read_bounded", read_without_birthtime)

    document = parser.parse_file(path, source_type=EvidenceSourceType.CAREER_NOTE)

    assert document.created_at == ""
    assert document.updated_at == parser._timestamp(20.0)


def test_parser_uses_birthtime_when_available_and_keeps_it_stable(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "career.txt"
    path.write_text("Synthetic evidence.", encoding="utf-8")
    parser = CareerDocumentParser(root=tmp_path)
    original_read = parser._read_bounded
    birthtime = 10.0

    def read_with_birthtime(relative_path, safe_name):
        raw, _ = original_read(relative_path, safe_name)
        return raw, SimpleNamespace(
            st_birthtime=birthtime,
            st_ctime=999.0,
            st_mtime=20.0,
        )

    monkeypatch.setattr(parser, "_read_bounded", read_with_birthtime)
    first = parser.parse_file(path, source_type=EvidenceSourceType.CAREER_NOTE)
    path.write_text("Updated synthetic evidence.", encoding="utf-8")
    updated = parser.parse_file(path, source_type=EvidenceSourceType.CAREER_NOTE)

    assert first.created_at == parser._timestamp(birthtime)
    assert updated.created_at == first.created_at


def test_pdf_parser_extracts_synthetic_text(tmp_path):
    path = write_minimal_pdf(
        tmp_path / "resume.pdf",
        ["Synthetic Resume", "Experience", "Built a reliable queue."],
    )

    document = _parse(tmp_path, path)

    assert document.file_name == "resume.pdf"
    assert "Synthetic Resume" in document.content
    assert "Experience" in document.content
    assert "Built a reliable queue." in document.content
    assert document.content_hash
    assert document.document_version_id


def test_docx_parser_preserves_heading_hierarchy_for_chunking(tmp_path):
    path = write_minimal_docx(
        tmp_path / "resume.docx",
        [
            ("Title", "Synthetic Resume"),
            ("Heading1", "Experience"),
            ("Heading2", "Example Corp"),
            ("", "Built a reliable queue."),
        ],
    )

    document = _parse(tmp_path, path)
    chunks = DocumentChunker(max_chars=500, overlap_chars=0).chunk_document(document)

    assert "Synthetic Resume" in document.content
    assert "Built a reliable queue." in document.content
    example_chunk = next(chunk for chunk in chunks if "reliable queue" in chunk.text)
    assert example_chunk.section_title == "Example Corp"
    assert example_chunk.parent_section_title == "Experience"
    assert example_chunk.exact_quote == example_chunk.text


def test_markdown_chunking_persists_nested_section_and_exact_quote_metadata(tmp_path):
    path = tmp_path / "career-note.md"
    path.write_text(
        "# Work Experience\nOverview.\n"
        "## Example Corp\nPlatform work.\n"
        "### Reliability\nReduced recovery time by 40%.\n",
        encoding="utf-8",
    )
    document = _parse(
        tmp_path,
        path,
        source_type=EvidenceSourceType.CAREER_NOTE,
    )

    chunks = DocumentChunker(max_chars=500, overlap_chars=0).chunk_document(document)

    assert [chunk.section_title for chunk in chunks] == [
        "Work Experience",
        "Example Corp",
        "Reliability",
    ]
    assert [chunk.parent_section_title for chunk in chunks] == [
        "",
        "Work Experience",
        "Example Corp",
    ]
    assert all(chunk.document_title == document.document_title for chunk in chunks)
    assert all(chunk.file_name == "career-note.md" for chunk in chunks)
    assert all(chunk.exact_quote == chunk.text for chunk in chunks)


def test_document_version_and_chunk_ids_are_deterministic_and_update_locally(tmp_path):
    path = tmp_path / "resume.md"
    original = (
        "# Experience\nBuilt a reliable queue.\n# Skills\nPython and Kubernetes.\n"
    )
    path.write_text(original, encoding="utf-8")
    parser = CareerDocumentParser(root=tmp_path)

    first = parser.parse_file(
        path,
        source_type=EvidenceSourceType.RESUME,
        experience_type=ExperienceType.PROFESSIONAL,
    )
    repeated = parser.parse_file(
        path,
        source_type=EvidenceSourceType.RESUME,
        experience_type=ExperienceType.PROFESSIONAL,
    )
    first_chunks = DocumentChunker(max_chars=500, overlap_chars=0).chunk_document(first)
    repeated_chunks = DocumentChunker(max_chars=500, overlap_chars=0).chunk_document(
        repeated
    )

    assert first.document_id == repeated.document_id
    assert first.document_version_id == repeated.document_version_id
    assert [chunk.chunk_id for chunk in first_chunks] == [
        chunk.chunk_id for chunk in repeated_chunks
    ]

    path.write_text(
        original.replace("Python and Kubernetes.", "Python, Kubernetes, and SQLite."),
        encoding="utf-8",
    )
    updated = parser.parse_file(
        path,
        source_type=EvidenceSourceType.RESUME,
        experience_type=ExperienceType.PROFESSIONAL,
    )
    updated_chunks = DocumentChunker(max_chars=500, overlap_chars=0).chunk_document(
        updated
    )

    assert updated.document_id == first.document_id
    assert updated.document_version_id != first.document_version_id
    assert updated_chunks[0].chunk_id == first_chunks[0].chunk_id
    assert updated_chunks[1].chunk_id != first_chunks[1].chunk_id


@pytest.mark.parametrize("suffix", [".pdf", ".docx"])
def test_corrupt_binary_documents_raise_typed_sanitized_parse_errors(tmp_path, suffix):
    path = tmp_path / f"broken{suffix}"
    path.write_bytes(b"not a valid document")

    with pytest.raises(ParsingError) as exc_info:
        _parse(tmp_path, path)

    assert path.name in str(exc_info.value)
    assert str(tmp_path) not in str(exc_info.value)


def test_invalid_utf8_and_unsupported_extensions_raise_typed_parse_errors(tmp_path):
    invalid_text = tmp_path / "invalid.txt"
    invalid_text.write_bytes(b"\xff\xfe")
    unsupported = tmp_path / "resume.rtf"
    unsupported.write_text("{\\rtf1 unsupported}", encoding="utf-8")
    parser = CareerDocumentParser(root=tmp_path)

    for path in (invalid_text, unsupported):
        with pytest.raises(ParsingError) as exc_info:
            parser.parse_file(
                path,
                source_type=EvidenceSourceType.RESUME,
                experience_type=ExperienceType.PROFESSIONAL,
            )
        assert path.name in str(exc_info.value)
        assert str(tmp_path) not in str(exc_info.value)


def test_docx_parser_rejects_xml_entities_with_typed_sanitized_error(tmp_path):
    path = tmp_path / "entity.docx"
    document_xml = b"""<?xml version="1.0"?>
<!DOCTYPE document [<!ENTITY private "must-not-be-expanded">]>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>&private;</w:t></w:r></w:p></w:body>
</w:document>
"""
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)

    with pytest.raises(ParsingError) as exc_info:
        _parse(tmp_path, path)

    assert path.name in str(exc_info.value)
    assert str(tmp_path) not in str(exc_info.value)


def test_docx_parser_bounds_decompressed_document_xml(tmp_path):
    path = tmp_path / "oversized.docx"
    oversized_text = "A" * 20_000
    document_xml = (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{oversized_text}</w:t></w:r></w:p></w:body>"
        "</w:document>"
    ).encode("utf-8")
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
    assert path.stat().st_size < 1_000

    parser = CareerDocumentParser(root=tmp_path, max_file_bytes=1_000)
    with pytest.raises(ParsingError, match="exceeds byte limit") as exc_info:
        parser.parse_file(
            path,
            source_type=EvidenceSourceType.RESUME,
            experience_type=ExperienceType.PROFESSIONAL,
        )

    assert path.name in str(exc_info.value)
    assert str(tmp_path) not in str(exc_info.value)


def test_parser_rejects_intermediate_directory_swap_before_file_open(
    monkeypatch,
    tmp_path,
):
    root = tmp_path / "career-root"
    approved_directory = root / "approved"
    approved_directory.mkdir(parents=True)
    requested_path = approved_directory / "resume.txt"
    requested_path.write_text("approved synthetic evidence", encoding="utf-8")

    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    (outside_directory / requested_path.name).write_text(
        "attacker-controlled synthetic content",
        encoding="utf-8",
    )
    moved_directory = root / "approved-before-race"
    parser = CareerDocumentParser(root=root)
    original_open = career_documents_module.os.open
    swapped = False

    def swap_directory() -> None:
        nonlocal swapped
        if swapped:
            return
        approved_directory.rename(moved_directory)
        approved_directory.symlink_to(outside_directory, target_is_directory=True)
        swapped = True

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        path_object = Path(path)
        if not swapped and path_object == root:
            descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
            swap_directory()
            return descriptor
        if not swapped:
            swap_directory()
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(career_documents_module.os, "open", racing_open)

    with pytest.raises(ParsingError) as exc_info:
        parser.parse_file(
            requested_path,
            source_type=EvidenceSourceType.RESUME,
            experience_type=ExperienceType.PROFESSIONAL,
        )

    assert swapped is True
    assert requested_path.name in str(exc_info.value)
    assert str(outside_directory) not in str(exc_info.value)


def test_parser_rejects_in_place_file_mutation_during_descriptor_read(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "resume.txt"
    path.write_text("approved synthetic evidence", encoding="utf-8")
    original_fdopen = career_documents_module.os.fdopen
    mutated = False

    class MutatingReader:
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, exc_type, exc, traceback):
            return self._handle.__exit__(exc_type, exc, traceback)

        def fileno(self):
            return self._handle.fileno()

        def read(self, *args, **kwargs):
            nonlocal mutated
            raw = self._handle.read(*args, **kwargs)
            path.write_text(
                "attacker-controlled synthetic content after descriptor read",
                encoding="utf-8",
            )
            mutated = True
            return raw

    def mutating_fdopen(descriptor, *args, **kwargs):
        return MutatingReader(original_fdopen(descriptor, *args, **kwargs))

    monkeypatch.setattr(career_documents_module.os, "fdopen", mutating_fdopen)

    with pytest.raises(ParsingError, match=f"Could not read {path.name}") as exc_info:
        _parse(tmp_path, path)

    assert mutated is True
    assert "attacker-controlled" not in str(exc_info.value)


def test_parser_revalidates_loaded_file_before_snapshot_finalization(tmp_path):
    path = tmp_path / "resume.txt"
    path.write_text("approved synthetic evidence", encoding="utf-8")
    parser = CareerDocumentParser(root=tmp_path)
    loaded = parser.read_file(path)
    path.write_text(
        "changed synthetic evidence after the initial trusted read",
        encoding="utf-8",
    )

    with pytest.raises(ParsingError, match=f"Could not read {path.name}"):
        parser.revalidate_loaded_file(loaded)


def test_parser_rejects_control_character_path_before_filesystem_call(
    monkeypatch,
    tmp_path,
):
    parser = CareerDocumentParser(root=tmp_path)
    open_calls: list[object] = []

    def unexpected_open(*args, **kwargs):
        open_calls.append((args, kwargs))
        raise AssertionError("unsafe path reached filesystem open")

    monkeypatch.setattr(career_documents_module.os, "open", unexpected_open)

    with pytest.raises(ParsingError) as exc_info:
        parser.parse_file(
            "resume\x00.txt",
            source_type=EvidenceSourceType.RESUME,
            experience_type=ExperienceType.PROFESSIONAL,
        )

    assert open_calls == []
    assert "\x00" not in str(exc_info.value)


def test_parser_rejects_symlinked_intermediate_root_ancestor(tmp_path):
    real_parent = tmp_path / "real-parent"
    root = real_parent / "career-root"
    root.mkdir(parents=True)
    (root / "resume.txt").write_text(
        "approved synthetic evidence",
        encoding="utf-8",
    )
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ParsingError) as exc_info:
        CareerDocumentParser(root=linked_parent / "career-root")

    assert "root" in str(exc_info.value).lower()
    assert str(real_parent) not in str(exc_info.value)


def test_parser_rejects_group_writable_root_component(tmp_path):
    root = tmp_path / "career-root"
    root.mkdir()
    root.chmod(0o770)

    with pytest.raises(ParsingError, match="trusted absolute directory"):
        CareerDocumentParser(root=root)


def test_parser_rejects_intermediate_root_ancestor_swap_after_initialization(
    tmp_path,
):
    approved_parent = tmp_path / "approved-parent"
    approved_root = approved_parent / "career-root"
    approved_root.mkdir(parents=True)
    requested_path = approved_root / "resume.txt"
    requested_path.write_text(
        "approved synthetic evidence",
        encoding="utf-8",
    )
    parser = CareerDocumentParser(root=approved_root)

    moved_parent = tmp_path / "approved-parent-before-swap"
    approved_parent.rename(moved_parent)
    replacement_root = approved_parent / "career-root"
    replacement_root.mkdir(parents=True)
    (replacement_root / requested_path.name).write_text(
        "attacker-controlled synthetic content",
        encoding="utf-8",
    )

    with pytest.raises(ParsingError) as exc_info:
        parser.parse_file(
            requested_path,
            source_type=EvidenceSourceType.RESUME,
            experience_type=ExperienceType.PROFESSIONAL,
        )

    assert requested_path.name in str(exc_info.value)
    assert str(replacement_root) not in str(exc_info.value)


def test_parser_rejects_root_replacement_against_bound_descriptor(tmp_path):
    approved_root = tmp_path / "career-root"
    approved_root.mkdir()
    approved_descriptor = career_documents_module._open_trusted_absolute_directory(
        approved_root,
        "career document root",
    )
    moved_root = tmp_path / "career-root-before-swap"
    approved_root.rename(moved_root)
    approved_root.mkdir()
    try:
        with pytest.raises(ParsingError, match="trusted absolute directory"):
            CareerDocumentParser(
                root=approved_root,
                root_descriptor=approved_descriptor,
            )
    finally:
        career_documents_module.os.close(approved_descriptor)


def test_pdf_parser_rejects_page_count_before_unbounded_extraction(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "many-pages.pdf"
    path.write_bytes(b"synthetic PDF reader input")
    extracted_pages: list[str] = []

    class FakePage:
        def __init__(self, text: str):
            self.text = text

        def extract_text(self) -> str:
            extracted_pages.append(self.text)
            return self.text

    class FakeReader:
        def __init__(self, _stream, *, strict):
            assert strict is True
            self.pages = [FakePage("one"), FakePage("two"), FakePage("three")]

    monkeypatch.setattr(career_documents_module, "PdfReader", FakeReader)
    parser = CareerDocumentParser(
        root=tmp_path,
        max_pdf_pages=2,
        max_pdf_extracted_chars=100,
        max_pdf_extracted_bytes=100,
    )

    with pytest.raises(ParsingError, match="page limit") as exc_info:
        parser.parse_file(
            path,
            source_type=EvidenceSourceType.RESUME,
            experience_type=ExperienceType.PROFESSIONAL,
        )

    assert len(extracted_pages) <= 2
    assert path.name in str(exc_info.value)
    assert str(tmp_path) not in str(exc_info.value)


def test_pdf_parser_bounds_aggregate_extracted_characters_and_work(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "character-expansion.pdf"
    path.write_bytes(b"synthetic PDF reader input")
    extracted_pages: list[str] = []

    class FakePage:
        def __init__(self, text: str):
            self.text = text

        def extract_text(self) -> str:
            extracted_pages.append(self.text)
            return self.text

    class FakeReader:
        def __init__(self, _stream, *, strict):
            assert strict is True
            self.pages = [
                FakePage("123456"),
                FakePage("abcdef"),
                FakePage("must not be extracted"),
            ]

    monkeypatch.setattr(career_documents_module, "PdfReader", FakeReader)
    parser = CareerDocumentParser(
        root=tmp_path,
        max_pdf_pages=10,
        max_pdf_extracted_chars=10,
        max_pdf_extracted_bytes=100,
    )

    with pytest.raises(ParsingError, match="character limit") as exc_info:
        parser.parse_file(
            path,
            source_type=EvidenceSourceType.RESUME,
            experience_type=ExperienceType.PROFESSIONAL,
        )

    assert extracted_pages == ["123456", "abcdef"]
    assert path.name in str(exc_info.value)
    assert str(tmp_path) not in str(exc_info.value)


def test_pdf_parser_bounds_aggregate_extracted_utf8_bytes_and_work(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "utf8-expansion.pdf"
    path.write_bytes(b"synthetic PDF reader input")
    extracted_pages: list[str] = []

    class FakePage:
        def __init__(self, text: str):
            self.text = text

        def extract_text(self) -> str:
            extracted_pages.append(self.text)
            return self.text

    class FakeReader:
        def __init__(self, _stream, *, strict):
            assert strict is True
            self.pages = [
                FakePage("경력"),
                FakePage("증명"),
                FakePage("must not be extracted"),
            ]

    monkeypatch.setattr(career_documents_module, "PdfReader", FakeReader)
    parser = CareerDocumentParser(
        root=tmp_path,
        max_pdf_pages=10,
        max_pdf_extracted_chars=100,
        max_pdf_extracted_bytes=10,
    )

    with pytest.raises(ParsingError, match="UTF-8 byte limit") as exc_info:
        parser.parse_file(
            path,
            source_type=EvidenceSourceType.RESUME,
            experience_type=ExperienceType.PROFESSIONAL,
        )

    assert extracted_pages == ["경력", "증명"]
    assert path.name in str(exc_info.value)
    assert str(tmp_path) not in str(exc_info.value)


@pytest.mark.parametrize(
    "field",
    [
        "document_title",
        "company",
        "role",
        "project",
        "start_date",
        "end_date",
    ],
)
def test_parser_rejects_oversized_metadata_before_document_creation(tmp_path, field):
    path = tmp_path / "resume.txt"
    path.write_text("synthetic evidence", encoding="utf-8")

    with pytest.raises(ParsingError, match=rf"{field}.*limit"):
        _parse(tmp_path, path, **{field: "x" * 20_000})


def test_parser_rejects_oversized_extracted_document_title(tmp_path):
    path = tmp_path / "oversized-title.md"
    path.write_text(f"# {'T' * 20_000}\n\nSynthetic evidence.\n", encoding="utf-8")

    with pytest.raises(ParsingError, match="document title.*limit"):
        _parse(tmp_path, path)


def test_markdown_title_and_section_validation_stream_without_bulk_splitlines():
    content = _NoBulkSplitText(
        "# Synthetic Resume\n\n## Experience\nBuilt bounded ingestion.\n"
    )

    assert CareerDocumentParser._markdown_title(content) == "Synthetic Resume"
    CareerDocumentParser._validate_section_titles(content)


@pytest.mark.parametrize("operation", ["title", "sections"])
def test_markdown_title_scans_poll_cooperative_cancellation(operation):
    content = "bounded line\n" * 10_000
    checks = 0

    def cancel_check():
        nonlocal checks
        checks += 1
        return checks >= 5

    with pytest.raises(career_documents_module.CareerParsingCancelled):
        if operation == "title":
            CareerDocumentParser._markdown_title(
                content,
                cancel_check=cancel_check,
            )
        else:
            CareerDocumentParser._validate_section_titles(
                content,
                cancel_check=cancel_check,
            )

    assert checks == 5


def test_section_validation_rejects_oversized_multiline_setext_title():
    content = ("S" * 300) + "\n" + ("T" * 300) + "\n=====\nbody\n"

    with pytest.raises(ParsingError, match="section title.*limit"):
        CareerDocumentParser._validate_section_titles(content)


def test_parser_rejects_oversized_section_title_before_chunking(tmp_path):
    path = tmp_path / "oversized-section.md"
    path.write_text(
        f"# Normal title\n\n## {'S' * 20_000}\nSynthetic evidence.\n",
        encoding="utf-8",
    )

    with pytest.raises(ParsingError, match="section title.*limit"):
        _parse(tmp_path, path)


def test_text_parser_rejects_oversized_section_title_before_chunking(tmp_path):
    path = tmp_path / "oversized-section.txt"
    path.write_text(
        f"# {'S' * 20_000}\nSynthetic evidence.\n",
        encoding="utf-8",
    )

    with pytest.raises(ParsingError, match="section title.*limit"):
        _parse(tmp_path, path)


def test_docx_xml_and_paragraph_extraction_poll_cooperative_cancellation(tmp_path):
    path = write_minimal_docx(
        tmp_path / "cancelled.docx",
        [("", f"Synthetic paragraph {index}") for index in range(1_000)],
    )
    checks = 0

    def cancel_check():
        nonlocal checks
        checks += 1
        return checks >= 5

    parser = CareerDocumentParser(root=tmp_path, cancel_check=cancel_check)

    with pytest.raises(career_documents_module.CareerParsingCancelled):
        parser._parse_docx(path.read_bytes(), path.name)

    assert checks == 5


def test_pdf_extraction_timeout_terminates_worker_without_orphan(tmp_path):
    path = write_minimal_pdf(
        tmp_path / "timeout.pdf",
        ["Synthetic Resume", "Built a reliable queue."],
    )
    baseline_pids = {child.pid for child in multiprocessing.active_children()}
    parser = CareerDocumentParser(
        root=tmp_path,
        pdf_extraction_timeout_seconds=1e-9,
    )

    started = time.monotonic()
    with pytest.raises(ParsingError, match="PDF extraction timed out") as exc_info:
        parser.parse_file(
            path,
            source_type=EvidenceSourceType.RESUME,
            experience_type=ExperienceType.PROFESSIONAL,
        )
    elapsed = time.monotonic() - started

    deadline = time.monotonic() + 2
    new_children = {
        child.pid
        for child in multiprocessing.active_children()
        if child.pid not in baseline_pids
    }
    while new_children and time.monotonic() < deadline:
        time.sleep(0.01)
        new_children = {
            child.pid
            for child in multiprocessing.active_children()
            if child.pid not in baseline_pids
        }

    assert new_children == set()
    assert elapsed < 2
    assert path.name in str(exc_info.value)
    assert str(tmp_path) not in str(exc_info.value)


def test_pdf_worker_uses_parent_rss_policy_on_darwin():
    calls = []

    class FakeResource:
        RLIMIT_CPU = 1
        RLIMIT_AS = 2

        @staticmethod
        def setrlimit(resource_name, limits):
            calls.append((resource_name, limits))

    child_memory_limit_applied = (
        career_documents_module._apply_pdf_worker_resource_limits(
            FakeResource,
            platform="darwin",
            cpu_seconds=3,
        )
    )

    assert child_memory_limit_applied is False
    assert (FakeResource.RLIMIT_CPU, (3, 4)) in calls
    assert all(resource_name != FakeResource.RLIMIT_AS for resource_name, _ in calls)


def test_pdf_worker_applies_address_space_limit_on_linux():
    calls = []

    class FakeResource:
        RLIMIT_CPU = 1
        RLIMIT_AS = 2

        @staticmethod
        def setrlimit(resource_name, limits):
            calls.append((resource_name, limits))

    child_memory_limit_applied = (
        career_documents_module._apply_pdf_worker_resource_limits(
            FakeResource,
            platform="linux",
            cpu_seconds=3,
        )
    )

    assert child_memory_limit_applied is True
    assert (
        FakeResource.RLIMIT_AS,
        (
            career_documents_module._PDF_WORKER_MEMORY_BYTES,
            career_documents_module._PDF_WORKER_MEMORY_BYTES,
        ),
    ) in calls


def test_pdf_worker_resource_policy_fails_closed_without_memory_limit():
    class FakeResource:
        RLIMIT_CPU = 1

        @staticmethod
        def setrlimit(_resource_name, _limits):
            return None

    with pytest.raises(RuntimeError, match="memory limit"):
        career_documents_module._apply_pdf_worker_resource_limits(
            FakeResource,
            platform="linux",
            cpu_seconds=3,
        )


def test_pdf_parent_rss_monitor_ignores_path_shadowed_ps(
    monkeypatch,
    tmp_path,
):
    fake_ps = tmp_path / "ps"
    fake_ps.write_text(
        '#!/bin/sh\n: > "$0.ran"\nprintf "1\\n"\n',
        encoding="utf-8",
    )
    fake_ps.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(career_documents_module.sys, "platform", "darwin")

    rss_bytes = career_documents_module._read_process_rss_bytes(os.getpid())

    assert rss_bytes is not None
    assert rss_bytes > 0
    assert not fake_ps.with_suffix(".ran").exists()


def test_pdf_parent_rss_monitor_fails_closed_when_system_ps_is_not_executable(
    monkeypatch,
):
    monkeypatch.setattr(
        career_documents_module.os, "access", lambda _path, _mode: False
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("untrusted process launcher must not run")

    monkeypatch.setattr(career_documents_module.subprocess, "run", fail_if_called)

    assert career_documents_module._read_process_rss_bytes(os.getpid()) is None


def test_pdf_parent_monitor_fails_closed_when_rss_is_unavailable(
    monkeypatch,
    tmp_path,
):
    class FakeConnection:
        @staticmethod
        def close():
            return None

    class FakeReceiver(FakeConnection):
        @staticmethod
        def poll(_timeout):
            return False

    class FakeProcess:
        pid = 123

        def __init__(self):
            self.alive = False
            self.terminated = False

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

        def kill(self):
            self.alive = False

        @staticmethod
        def join(_timeout=None):
            return None

        @staticmethod
        def close():
            return None

    process = FakeProcess()

    class FakeContext:
        @staticmethod
        def Pipe(*, duplex):
            assert duplex is False
            return FakeReceiver(), FakeConnection()

        @staticmethod
        def Process(*args, **kwargs):
            return process

    monkeypatch.setattr(
        career_documents_module.multiprocessing,
        "get_context",
        lambda _kind: FakeContext(),
    )
    monkeypatch.setattr(career_documents_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        career_documents_module,
        "_read_process_rss_bytes",
        lambda _pid: None,
    )
    parser = CareerDocumentParser(root=tmp_path)

    with pytest.raises(ParsingError, match="memory monitoring unavailable"):
        parser._parse_pdf(b"synthetic", "unmonitored.pdf")

    assert process.terminated is True


def test_pdf_parent_throttles_rss_sampling_without_slower_cancel_polling(
    monkeypatch,
    tmp_path,
):
    class FakeConnection:
        @staticmethod
        def close():
            return None

    class FakeReceiver(FakeConnection):
        def __init__(self):
            self.poll_calls = 0

        def poll(self, _timeout):
            self.poll_calls += 1
            return self.poll_calls >= 12

        @staticmethod
        def recv():
            return ("ok", "synthetic content", "Synthetic title")

    class FakeProcess:
        pid = 123

        def __init__(self):
            self.alive = False

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.alive = False

        def kill(self):
            self.alive = False

        def join(self, _timeout=None):
            self.alive = False

        @staticmethod
        def close():
            return None

    receiver = FakeReceiver()
    process = FakeProcess()

    class FakeContext:
        @staticmethod
        def Pipe(*, duplex):
            assert duplex is False
            return receiver, FakeConnection()

        @staticmethod
        def Process(*args, **kwargs):
            del args, kwargs
            return process

    rss_calls: list[int] = []
    cancel_checks = 0

    def read_rss(process_id):
        rss_calls.append(process_id)
        return 1

    def cancel_check():
        nonlocal cancel_checks
        cancel_checks += 1
        return False

    monkeypatch.setattr(
        career_documents_module.multiprocessing,
        "get_context",
        lambda _kind: FakeContext(),
    )
    monkeypatch.setattr(career_documents_module.sys, "platform", "darwin")
    monkeypatch.setattr(career_documents_module, "_read_process_rss_bytes", read_rss)
    parser = CareerDocumentParser(root=tmp_path, cancel_check=cancel_check)

    assert parser._parse_pdf(b"synthetic", "sampled.pdf") == (
        "synthetic content",
        "Synthetic title",
    )
    assert receiver.poll_calls == 12
    assert cancel_checks >= 12
    assert rss_calls == [123]


def test_pdf_polling_cancellation_terminates_worker():
    class FakeConnection:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class FakeReceiver(FakeConnection):
        @staticmethod
        def poll(_timeout):
            return False

    class FakeProcess:
        def __init__(self):
            self.started = False
            self.alive = False
            self.terminated = False
            self.closed = False

        def start(self):
            self.started = True
            self.alive = True

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

        def kill(self):
            self.alive = False

        def join(self, _timeout=None):
            return None

        def close(self):
            self.closed = True

    receiver = FakeReceiver()
    sender = FakeConnection()
    process = FakeProcess()

    class FakeContext:
        @staticmethod
        def Pipe(*, duplex):
            assert duplex is False
            return receiver, sender

        @staticmethod
        def Process(*args, **kwargs):
            return process

    original_get_context = career_documents_module.multiprocessing.get_context
    career_documents_module.multiprocessing.get_context = lambda _kind: FakeContext()
    try:
        parser = CareerDocumentParser(
            root=Path.cwd(),
            cancel_check=lambda: True,
        )
        cancel_error = getattr(
            career_documents_module,
            "CareerParsingCancelled",
            RuntimeError,
        )
        with pytest.raises(cancel_error):
            parser._parse_pdf(b"synthetic", "cancelled.pdf")
    finally:
        career_documents_module.multiprocessing.get_context = original_get_context

    assert process.started is True
    assert process.terminated is True
    assert process.closed is True
