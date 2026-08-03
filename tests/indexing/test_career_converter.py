from core.models import DocumentModel
from indexing.converter import DocumentConverter
from llama_index.core.schema import MetadataMode


def test_converter_omits_evidence_only_metadata_for_non_career_document():
    document = DocumentModel(
        id="legacy-chunk",
        chunk_id="legacy-chunk",
        document_id="legacy-document",
        source_id="source_github",
        title="Legacy",
        content="Legacy content.",
        exact_quote="must not be copied",
        document_title="must not be copied",
        company="must not be copied",
        url="https://example.test/legacy",
        platform="GitHub",
    )

    converted = DocumentConverter.to_llama_document(document)

    for field_name in (
        "document_version_id",
        "evidence_source_type",
        "experience_type",
        "file_name",
        "document_title",
        "section_title",
        "parent_section_title",
        "exact_quote",
        "created_at",
        "company",
        "role",
        "project",
        "start_date",
        "end_date",
    ):
        assert field_name not in converted.metadata


def test_career_converter_keeps_minimal_vector_metadata_and_embeds_only_text():
    document = DocumentModel(
        id="career-chunk",
        chunk_id="career-chunk",
        document_id="career-document",
        source_id="source_career",
        title="Private resume title",
        content="Built a reliable queue.",
        exact_quote="Built a reliable queue.",
        document_title="Private resume title",
        section_title="Private section",
        parent_section_title="Private parent",
        file_name="private-resume.pdf",
        company="Private Company",
        role="Private Role",
        project="Private Project",
        start_date="2024-01",
        end_date="2025-01",
        url="career://career-document",
        platform="career",
        chunk_index=3,
        line_start=17,
        line_end=21,
        version_id="source-version-2",
        document_version_id="document-version-2",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-02T00:00:00Z",
        evidence_source_type="resume",
        experience_type="professional",
    )

    converted = DocumentConverter.to_llama_document(document)

    assert converted.id_ == document.chunk_id
    assert set(converted.metadata) == {
        "doc_id",
        "chunk_id",
        "document_id",
        "source_id",
        "evidence_source_type",
        "experience_type",
        "contextwiki_managed",
        "content_hash",
        "chunk_index",
        "line_start",
        "line_end",
        "version_id",
        "document_version_id",
        "created_at",
        "updated_at",
    }
    assert converted.metadata["chunk_index"] == 3
    assert converted.metadata["line_start"] == 17
    assert converted.metadata["line_end"] == 21
    assert converted.metadata["version_id"] == "source-version-2"
    assert converted.metadata["document_version_id"] == "document-version-2"
    assert set(converted.excluded_embed_metadata_keys) == set(converted.metadata)
    embed_content = converted.get_content(metadata_mode=MetadataMode.EMBED)
    assert embed_content == document.content
    assert embed_content.count(document.content) == 1
