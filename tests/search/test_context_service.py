import asyncio

import pytest
from llama_index.vector_stores.chroma.base import _to_chroma_filter

from core.models import (
    ChunkModel,
    DocumentModel,
    DocumentSearchResult,
    SourceModel,
    SourceType,
    SyncStatus,
)
from search import ranking
from search.answer_service import CitationAnswerService
from search.context_service import ContextSearchService
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.integration


class FakeNode:
    def __init__(self, chunk_id, score):
        self.metadata = {"chunk_id": chunk_id, "context_zip_managed": "true"}
        self.score = score


class FakeIndexer:
    def get_or_create_index(self):
        return object()


def test_document_search_result_requires_typed_matched_context():
    required_fields = {
        "document_id": "doc-1",
        "chunk_id": "chunk-1",
        "source_id": "source-1",
        "source_type": "notion",
        "title": "Required context",
    }

    with pytest.raises(ValueError, match="matched_context"):
        DocumentSearchResult(**required_fields)
    with pytest.raises(ValueError, match="matched_context"):
        DocumentSearchResult(**required_fields, matched_context=None)

    assert DocumentSearchResult(
        **required_fields,
        matched_context="",
    ).matched_context == ""


def test_vector_search_tries_alias_expanded_query_variants(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-aws",
        "aws-chunk",
        "source_target",
        "AWS guide",
        "Amazon Web Services deployment checklist",
    )
    retrieval_queries = []

    class FakeVectorIndexRetriever:
        def __init__(self, **kwargs):
            self.limit = kwargs.get("similarity_top_k")

        def retrieve(self, query):
            retrieval_queries.append(query)
            if "amazon web services" not in query.lower():
                return []
            node = FakeNode("aws-chunk", 0.9)
            node.metadata["document_id"] = "doc-aws"
            node.metadata["source_id"] = "source_target"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context("AWS에 적은 문서를 찾아줘", top_k=1)
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "aws-chunk"
    assert any("amazon web services" in query.lower() for query in retrieval_queries)


def test_vector_search_does_not_issue_external_rewrite_queries(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-ec2",
        "ec2-chunk",
        "source_target",
        "EC2 setup guide",
        "EC2 setup and instance launch notes.",
    )
    retrieval_queries = []

    class FakeVectorIndexRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            retrieval_queries.append(query)
            if "ec2" not in query.lower():
                return []
            node = FakeNode("ec2-chunk", 0.91)
            node.metadata["document_id"] = "doc-ec2"
            node.metadata["source_id"] = "source_target"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(
        ContextSearchService(
            store,
            indexer=FakeIndexer(),
        ).search_context("aws virtual machine startup", top_k=1, include_debug=True)
    )

    assert result["results"] == []
    assert result["debug"]["retrieval_queries"]
    assert all("ec2" not in query.lower() for query in retrieval_queries)
    assert not any("rewrite" in key for key in result["debug"])


def test_vector_search_uses_metadata_identity_fallback_without_query_rewrite(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/ImageGallery:docs/usage.md",
        "imagegallery-doc-chunk",
        "source_github",
        "Usage guide",
        "Generic component notes without the repo name in body.",
        path="docs/ImageGallery/usage.md",
        url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
    )

    class MetadataOnlyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", MetadataOnlyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(
            store,
            indexer=FakeIndexer(),
        ).search_context("ImageGallery docs", top_k=1, include_debug=True)
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-doc-chunk"
    assert result["debug"]["initial_top_vector_score"] == 0.0
    assert result["debug"]["final_top_score"] == round(result["results"][0].score, 4)
    assert not any("rewrite" in key for key in result["debug"])


def test_search_context_debug_includes_filters_and_result_summary(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-debug",
        "debug-chunk",
        "source_target",
        "ContextZip debug guide",
        "ContextZip debug guide content.",
    )

    result = asyncio.run(
        ContextSearchService(store, retriever=list_search_documents(store)).search_context(
            "context_zip debug",
            filters={"source_ids": ["source_target"]},
            top_k=1,
            include_debug=True,
        )
    )

    assert result["debug"]["filters"] == {"source_ids": ["source_target"]}
    assert result["debug"]["initial_top_vector_score"] == 0.0
    assert result["debug"]["final_top_score"] == round(result["results"][0].score, 4)
    assert result["debug"]["selected_results"][0]["chunk_id"] == "debug-chunk"
    assert result["debug"]["selected_results"][0]["source_id"] == "source_target"
    assert result["debug"]["selected_results"][0]["matched_terms"]


def test_search_context_defaults_to_non_debug_payload(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-debug",
        "debug-chunk",
        "source_target",
        "ContextZip debug guide",
        "ContextZip debug guide content.",
    )

    result = asyncio.run(
        ContextSearchService(store, retriever=list_search_documents(store)).search_context(
            "context_zip debug",
            top_k=1,
        )
    )

    assert result["results"][0].chunk_id == "debug-chunk"
    assert "debug" not in result
    assert "_grounding" not in result


def test_search_context_refills_candidates_before_applying_inclusive_date_filter(
    monkeypatch,
    tmp_path,
):
    from core.models import SearchFilters

    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    for index, published_at in enumerate(
        [
            "2026-06-01T00:00:00Z",
            "2026-06-02T00:00:00Z",
            "2026-07-01T00:00:00Z",
        ]
    ):
        seed_document_chunks(
            store,
            f"doc-{index}",
            f"chunk-{index}",
            "source_target",
            f"ContextZip {index}",
            "ContextZip date filtering evidence.",
            published_at=published_at,
        )
    lookup_counts = {
        "chunks": {},
        "documents": {},
        "sources": {},
    }
    original_get_chunk = store.get_chunk
    original_get_document = store.get_document
    original_get_source = store.get_source

    def counted_get_chunk(chunk_id):
        lookup_counts["chunks"][chunk_id] = (
            lookup_counts["chunks"].get(chunk_id, 0) + 1
        )
        return original_get_chunk(chunk_id)

    def counted_get_document(document_id):
        lookup_counts["documents"][document_id] = (
            lookup_counts["documents"].get(document_id, 0) + 1
        )
        return original_get_document(document_id)

    def counted_get_source(source_id):
        lookup_counts["sources"][source_id] = (
            lookup_counts["sources"].get(source_id, 0) + 1
        )
        return original_get_source(source_id)

    monkeypatch.setattr(store, "get_chunk", counted_get_chunk)
    monkeypatch.setattr(store, "get_document", counted_get_document)
    monkeypatch.setattr(store, "get_source", counted_get_source)
    requested_limits = []

    def retriever(_query, limit, _source_ids):
        requested_limits.append(limit)
        return [
            {"chunk_id": f"chunk-{index}", "score": 1.0 - index / 10}
            for index in range(3)
        ][:limit]

    result = asyncio.run(
        ContextSearchService(store, retriever=retriever).search_context(
            "ContextZip",
            filters=SearchFilters(
                source_ids=["source_target"],
                published_from="2026-07-01T00:00:00Z",
                published_to="2026-07-01T00:00:00Z",
            ),
            top_k=1,
        )
    )

    assert requested_limits == [1, 2, 4]
    assert [item.document_id for item in result["results"]] == ["doc-2"]
    assert lookup_counts["chunks"] == {
        "chunk-0": 1,
        "chunk-1": 1,
        "chunk-2": 1,
    }
    assert lookup_counts["documents"] == {
        "doc-0": 1,
        "doc-1": 1,
        "doc-2": 1,
    }
    assert lookup_counts["sources"] == {"source_target": 1}


def test_search_documents_sorts_matching_documents_by_normalized_date(tmp_path):
    from core.models import SearchFilters

    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    for document_id, published_at in [
        ("older", "2026-07-01T00:00:00Z"),
        ("newer", "2026-07-03T00:00:00Z"),
    ]:
        seed_document_chunks(
            store,
            document_id,
            f"{document_id}-chunk",
            "source_target",
            f"ContextZip {document_id}",
            "ContextZip sorted evidence.",
            published_at=published_at,
        )

    result = asyncio.run(
        ContextSearchService(
            store,
            retriever=list_search_documents(store),
        ).search_documents(
            "ContextZip",
            filters=SearchFilters(source_ids=["source_target"]),
            sort_by="published_at",
            sort_order="desc",
            top_k=2,
        )
    )

    assert [item.document_id for item in result["results"]] == ["newer", "older"]
    assert result["results"][0].published_at == "2026-07-03T00:00:00Z"


@pytest.mark.parametrize(
    ("sort_order", "expected_document_ids"),
    [
        ("asc", ["offset-aware", "date-only", "utc-aware"]),
        ("desc", ["utc-aware", "date-only", "offset-aware"]),
    ],
)
def test_search_documents_normalizes_date_only_and_offset_aware_sort_values(
    tmp_path,
    sort_order,
    expected_document_ids,
):
    from core.models import SearchFilters

    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    for document_id, published_at in [
        ("date-only", "2026-07-02"),
        ("offset-aware", "2026-07-02T08:00:00+09:00"),
        ("utc-aware", "2026-07-02T01:00:00Z"),
    ]:
        seed_document_chunks(
            store,
            document_id,
            f"{document_id}-chunk",
            "source_target",
            f"ContextZip {document_id}",
            "ContextZip mixed timestamp sorting evidence.",
            published_at=published_at,
        )

    result = asyncio.run(
        ContextSearchService(
            store,
            retriever=list_search_documents(store),
        ).search_documents(
            "ContextZip",
            filters=SearchFilters(source_ids=["source_target"]),
            sort_by="published_at",
            sort_order=sort_order,
            top_k=3,
        )
    )

    assert [item.document_id for item in result["results"]] == expected_document_ids


@pytest.mark.parametrize(
    ("sort_order", "documents", "expected_document_ids"),
    [
        (
            "asc",
            [
                ("a-newer", "9999-12-31T23:59:59.999999Z"),
                ("z-older", "9999-12-31T23:59:59.999998Z"),
            ],
            ["z-older", "a-newer"],
        ),
        (
            "desc",
            [
                ("a-older", "9999-12-31T23:59:59.999998Z"),
                ("z-newer", "9999-12-31T23:59:59.999999Z"),
            ],
            ["z-newer", "a-older"],
        ),
    ],
)
def test_search_documents_preserves_microsecond_precision_for_date_sort(
    tmp_path,
    sort_order,
    documents,
    expected_document_ids,
):
    from core.models import SearchFilters

    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    for document_id, published_at in documents:
        seed_document_chunks(
            store,
            document_id,
            f"{document_id}-chunk",
            "source_target",
            f"ContextZip {document_id}",
            "ContextZip precision sorting evidence.",
            published_at=published_at,
        )

    result = asyncio.run(
        ContextSearchService(
            store,
            retriever=list_search_documents(store),
        ).search_documents(
            "ContextZip",
            filters=SearchFilters(source_ids=["source_target"]),
            sort_by="published_at",
            sort_order=sort_order,
            top_k=2,
        )
    )

    assert [item.document_id for item in result["results"]] == expected_document_ids


def test_search_documents_refill_hydrates_each_unique_candidate_once(
    monkeypatch,
    tmp_path,
):
    from core.models import SearchFilters

    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    old_document = DocumentModel(
        id="doc-old-multi",
        source_id="source_target",
        title="Old multi-chunk document",
        content="ContextZip old multi chunk evidence",
        url="https://example.com/doc-old-multi",
        platform="Notion",
        published_at="2026-06-01T00:00:00Z",
    )
    old_chunks = [
        ChunkModel(
            chunk_id=f"chunk-old-{index}",
            document_id="doc-old-multi",
            source_id="source_target",
            title=old_document.title,
            text=f"ContextZip old multi chunk evidence {index}",
            url=old_document.url,
            chunk_index=index,
            content_hash=f"old-{index}",
        )
        for index in range(2)
    ]
    store.upsert_document_and_replace_chunks(old_document, old_chunks)
    seed_document_chunks(
        store,
        "doc-other-old",
        "chunk-other-old",
        "source_target",
        "Other old document",
        "ContextZip other old evidence",
        published_at="2026-06-02T00:00:00Z",
    )
    seed_document_chunks(
        store,
        "doc-match",
        "chunk-match",
        "source_target",
        "Matching document",
        "ContextZip matching date evidence",
        published_at="2026-07-01T00:00:00Z",
    )
    lookup_counts = {
        "chunks": {},
        "documents": {},
        "sources": {},
    }
    original_get_chunk = store.get_chunk
    original_get_document = store.get_document
    original_get_source = store.get_source

    def counted_get_chunk(chunk_id):
        lookup_counts["chunks"][chunk_id] = (
            lookup_counts["chunks"].get(chunk_id, 0) + 1
        )
        return original_get_chunk(chunk_id)

    def counted_get_document(document_id):
        lookup_counts["documents"][document_id] = (
            lookup_counts["documents"].get(document_id, 0) + 1
        )
        return original_get_document(document_id)

    def counted_get_source(source_id):
        lookup_counts["sources"][source_id] = (
            lookup_counts["sources"].get(source_id, 0) + 1
        )
        return original_get_source(source_id)

    monkeypatch.setattr(store, "get_chunk", counted_get_chunk)
    monkeypatch.setattr(store, "get_document", counted_get_document)
    monkeypatch.setattr(store, "get_source", counted_get_source)
    requested_limits = []

    def retriever(_query, limit, _source_ids):
        requested_limits.append(limit)
        return [
            {"chunk_id": "chunk-old-0", "score": 1.0},
            {"chunk_id": "chunk-old-1", "score": 0.9},
            {"chunk_id": "chunk-other-old", "score": 0.8},
            {"chunk_id": "chunk-match", "score": 0.7},
        ][:limit]

    service = ContextSearchService(store, retriever=retriever)
    monkeypatch.setattr(service, "_document_search_candidate_limit", lambda _top_k: 2)
    result = asyncio.run(
        service.search_documents(
            "ContextZip",
            filters=SearchFilters(
                source_ids=["source_target"],
                published_from="2026-07-01T00:00:00Z",
            ),
            top_k=1,
        )
    )

    assert requested_limits == [2, 4]
    assert [item.document_id for item in result["results"]] == ["doc-match"]
    assert lookup_counts["chunks"] == {
        "chunk-old-0": 1,
        "chunk-old-1": 1,
        "chunk-other-old": 1,
        "chunk-match": 1,
    }
    assert lookup_counts["documents"] == {
        "doc-old-multi": 1,
        "doc-other-old": 1,
        "doc-match": 1,
    }
    assert lookup_counts["sources"] == {"source_target": 1}


def test_vector_search_debug_contains_no_query_rewrite_fields(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-plain",
        "plain-chunk",
        "source_target",
        "Plain guide",
        "Plain guide content.",
    )

    class FakeVectorIndexRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)
    result = asyncio.run(
        ContextSearchService(
            store,
            indexer=FakeIndexer(),
        ).search_context("aws virtual machine startup", top_k=1, include_debug=True)
    )

    assert not any("rewrite" in key for key in result["debug"])


def test_search_context_empty_filter_result_keeps_deterministic_debug(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    result = asyncio.run(
        ContextSearchService(
            store,
            indexer=FakeIndexer(),
            default_source_ids=("source_target",),
        ).search_context(
            "aws virtual machine startup",
            filters={"source_id": "source_other"},
            top_k=1,
            include_debug=True,
        )
    )

    assert result["results"] == []
    assert result["debug"]["retrieval_queries"] == []
    assert result["debug"]["effective_term_groups"] == []
    assert not any("rewrite" in key for key in result["debug"])


def test_vector_search_uses_best_score_for_same_chunk_across_query_variants(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-ec2",
        "ec2-chunk",
        "source_target",
        "EC2 setup guide",
        "EC2 setup and instance launch notes.",
    )

    class FakeVectorIndexRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode(
                "ec2-chunk",
                0.91 if "amazon web services" in query.lower() else 0.22,
            )
            node.metadata["document_id"] = "doc-ec2"
            node.metadata["source_id"] = "source_target"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(
        ContextSearchService(
            store,
            indexer=FakeIndexer(),
        ).search_context("aws virtual machine startup", top_k=1)
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "ec2-chunk"
    assert result["results"][0].vector_score == 0.91


def test_vector_search_keeps_single_high_confidence_exact_match_when_top_k_is_larger(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-ec2",
        "ec2-chunk",
        "source_target",
        "EC2 setup guide",
        "EC2 setup and instance launch notes.",
    )

    class FakeVectorIndexRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            if "ec2" not in query.lower():
                return []
            node = FakeNode("ec2-chunk", 0.98)
            node.metadata["document_id"] = "doc-ec2"
            node.metadata["source_id"] = "source_target"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(
        ContextSearchService(
            store,
            indexer=FakeIndexer(),
        ).search_context("ec2 setup guide", top_k=3, include_debug=True)
    )

    assert result["results"][0].chunk_id == "ec2-chunk"
    assert result["debug"]["initial_top_vector_score"] == 0.98
    assert result["debug"]["final_top_score"] == round(result["results"][0].score, 4)


def test_context_service_exposes_no_query_rewrite_helpers(
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-ec2",
        "ec2-chunk",
        "source_target",
        "EC2 setup guide",
        "EC2 setup and instance launch notes.",
    )

    service = ContextSearchService(store, indexer=FakeIndexer())

    assert not hasattr(service, "_should_try_query_rewrite")
    assert not hasattr(service._pipeline(), "rewrite_queries")


def test_vector_search_keeps_original_deterministic_query_results(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-original",
        "chunk-original",
        "source_target",
        "Original guide",
        "ContextZip original setup guide.",
    )
    seed_document_chunks(
        store,
        "doc-rewritten",
        "chunk-rewritten",
        "source_target",
        "Rewritten guide",
        "ContextZip rewritten setup guide.",
    )

    class RewriteComparisonRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            lowered = query.lower()
            if "rewrite improved" in lowered:
                node = FakeNode("chunk-rewritten", 0.41)
                node.metadata["document_id"] = "doc-rewritten"
                node.metadata["source_id"] = "source_target"
                return [node]
            node = FakeNode("chunk-original", 0.72)
            node.metadata["document_id"] = "doc-original"
            node.metadata["source_id"] = "source_target"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", RewriteComparisonRetriever)

    result = asyncio.run(
        ContextSearchService(
            store,
            indexer=FakeIndexer(),
        ).search_context("context_zip setup", top_k=1, include_debug=True)
    )

    assert result["results"][0].chunk_id == "chunk-original"
    assert result["debug"]["retrieval_queries"] == ["context_zip setup"]
    assert result["debug"]["initial_top_vector_score"] == 0.72
    assert not any("rewrite" in key for key in result["debug"])


def test_vector_search_does_not_probe_unrequested_alternate_queries(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-original",
        "chunk-original",
        "source_target",
        "Original guide",
        "ContextZip original setup guide.",
    )
    seed_document_chunks(
        store,
        "doc-rewritten",
        "chunk-rewritten",
        "source_target",
        "Rewritten guide",
        "ContextZip rewritten setup guide.",
    )

    class RewriteComparisonRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            lowered = query.lower()
            if "rewrite improved" in lowered:
                node = FakeNode("chunk-rewritten", 0.87)
                node.metadata["document_id"] = "doc-rewritten"
                node.metadata["source_id"] = "source_target"
                return [node]
            node = FakeNode("chunk-original", 0.72)
            node.metadata["document_id"] = "doc-original"
            node.metadata["source_id"] = "source_target"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", RewriteComparisonRetriever)

    result = asyncio.run(
        ContextSearchService(
            store,
            indexer=FakeIndexer(),
        ).search_context("context_zip setup", top_k=1, include_debug=True)
    )

    assert result["results"][0].chunk_id == "chunk-original"
    assert result["debug"]["retrieval_queries"] == ["context_zip setup"]


def test_retrieval_pipeline_exposes_no_rewrite_result_comparison(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    service = ContextSearchService(store, indexer=FakeIndexer())

    assert not hasattr(service._pipeline(), "prefer_rewritten_results")


def test_vector_search_uses_metadata_promoted_candidate_without_external_rewrite(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/ec2-notes:docs/guide.md",
        "ec2-metadata-doc",
        "source_github",
        "EC2 guide",
        "EC2 setup and usage guide.",
        path="docs/guide.md",
        url="https://github.com/eunhwa99/ec2-notes/blob/main/docs/guide.md",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(
            store,
            indexer=FakeIndexer(),
        ).search_context("ec2 docs", top_k=3, include_debug=True)
    )

    assert result["results"][0].chunk_id == "ec2-metadata-doc"
    assert result["debug"]["initial_top_vector_score"] == 0.0


def test_vector_search_reports_raw_initial_vector_score_when_top_k_is_larger(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-strong-1",
        "chunk-strong-1",
        "source_target",
        "Strong guide 1",
        "ContextZip setup guide one.",
    )
    seed_document_chunks(
        store,
        "doc-strong-2",
        "chunk-strong-2",
        "source_target",
        "Strong guide 2",
        "ContextZip setup guide two.",
    )

    class StrongVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            first = FakeNode("chunk-strong-1", 0.93)
            first.metadata["document_id"] = "doc-strong-1"
            first.metadata["source_id"] = "source_target"
            second = FakeNode("chunk-strong-2", 0.91)
            second.metadata["document_id"] = "doc-strong-2"
            second.metadata["source_id"] = "source_target"
            return [first, second]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", StrongVectorRetriever)

    result = asyncio.run(
        ContextSearchService(
            store,
            indexer=FakeIndexer(),
        ).search_context("context_zip setup", top_k=3, include_debug=True)
    )

    assert result["debug"]["initial_top_vector_score"] == 0.93


def test_vector_search_uses_raw_initial_vector_score_before_metadata_promotion(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/context_zip:docs/setup.md",
        "context_zip-doc-chunk",
        "source_github",
        "Setup guide",
        "ContextZip setup guide.",
        path="docs/setup.md",
        url="https://github.com/eunhwa99/context_zip/blob/main/docs/setup.md",
    )
    seed_document_chunks(
        store,
        "github:eunhwa99/context_zip:docs/faq.md",
        "context_zip-faq-chunk",
        "source_github",
        "FAQ guide",
        "ContextZip FAQ guide.",
        path="docs/faq.md",
        url="https://github.com/eunhwa99/context_zip/blob/main/docs/faq.md",
    )

    class MixedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("context_zip-doc-chunk", 0.91)
            node.metadata["document_id"] = "github:eunhwa99/context_zip:docs/setup.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", MixedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(
            store,
            indexer=FakeIndexer(),
        ).search_context("context_zip docs", top_k=3, include_debug=True)
    )

    assert result["debug"]["initial_top_vector_score"] == 0.91


def test_vector_search_uses_raw_initial_vector_score_even_when_top_raw_hit_is_filtered_out(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-valid",
        "chunk-valid",
        "source_target",
        "Valid guide",
        "ContextZip setup guide.",
    )
    seed_document_chunks(
        store,
        "doc-filtered",
        "chunk-filtered",
        "source_target",
        "Filtered guide",
        "ContextZip setup guide but unmanaged.",
    )

    class FilteredTopHitRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            filtered = FakeNode("chunk-filtered", 0.96)
            filtered.metadata["document_id"] = "doc-filtered"
            filtered.metadata["source_id"] = "source_target"
            filtered.metadata["context_zip_managed"] = "false"
            valid = FakeNode("chunk-valid", 0.24)
            valid.metadata["document_id"] = "doc-valid"
            valid.metadata["source_id"] = "source_target"
            return [filtered, valid]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FilteredTopHitRetriever)

    result = asyncio.run(
        ContextSearchService(
            store,
            indexer=FakeIndexer(),
        ).search_context("context_zip setup", top_k=1, include_debug=True)
    )

    assert result["results"][0].chunk_id == "chunk-valid"
    assert result["debug"]["initial_top_vector_score"] == 0.96


def test_search_documents_collapses_same_document_chunks_to_highest_ranked_representative(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id="doc-alpha",
            document_id="doc-alpha",
            source_id="source_target",
            title="Alpha guide",
            content="Alpha first chunk\nAlpha second chunk",
            url="https://example.com/doc-alpha",
            platform="Test",
            path="guides/alpha.md",
        ),
        [
            ChunkModel(
                chunk_id="alpha-chunk-1",
                document_id="doc-alpha",
                source_id="source_target",
                title="Alpha guide",
                text="Alpha first chunk",
                url="https://example.com/doc-alpha",
                path="guides/alpha.md",
                chunk_index=0,
                content_hash="alpha-chunk-1",
            ),
            ChunkModel(
                chunk_id="alpha-chunk-2",
                document_id="doc-alpha",
                source_id="source_target",
                title="Alpha guide",
                text="Alpha second chunk",
                url="https://example.com/doc-alpha",
                path="guides/alpha.md",
                chunk_index=1,
                content_hash="alpha-chunk-2",
            ),
        ],
    )
    seed_document_chunks(
        store,
        "doc-beta",
        "beta-chunk-1",
        "source_target",
        "Beta guide",
        "Beta guide content",
        path="guides/beta.md",
        url="https://example.com/doc-beta",
    )
    service = ContextSearchService(store)

    async def fake_retrieve_candidates(query, top_k, source_ids):
        return {
            "candidates": [
                {"chunk_id": "alpha-chunk-2", "score": 0.97, "vector_score": 0.97, "metadata_priority": 1},
                {"chunk_id": "beta-chunk-1", "score": 0.91, "vector_score": 0.91, "metadata_priority": 0},
                {"chunk_id": "alpha-chunk-1", "score": 0.82, "vector_score": 0.82, "metadata_priority": 0},
            ],
            "effective_term_groups": [],
            "original_term_groups": [],
            "retrieval_queries": [query],
        }

    service._retrieve_candidates = fake_retrieve_candidates

    result = asyncio.run(service.search_documents("alpha beta", top_k=10))

    assert [item.document_id for item in result["results"]] == ["doc-alpha", "doc-beta"]
    assert [item.chunk_id for item in result["results"]] == ["alpha-chunk-2", "beta-chunk-1"]
    assert [item.score for item in result["results"]] == [0.97, 0.91]
    assert [item.matched_context for item in result["results"]] == [
        "Alpha second chunk",
        "Beta guide content",
    ]


def test_search_documents_preserves_document_metadata_and_representative_chunk_id(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id="doc-readme",
            document_id="doc-readme",
            source_id="source_github",
            title="README",
            content="Repository overview chunk",
            url="https://github.com/example/repo/blob/main/README.md",
            platform="Test",
            path="README.md",
            version_id="v1",
        ),
        [
            ChunkModel(
                chunk_id="readme-chunk-1",
                document_id="doc-readme",
                source_id="source_github",
                title="README",
                text="Repository overview chunk",
                url="https://github.com/example/repo/blob/main/README.md",
                path="README.md",
                chunk_index=0,
                line_start=10,
                line_end=18,
                version_id="v1",
                content_hash="readme-chunk-1",
                updated_at="2026-06-12T09:00:00+00:00",
            ),
        ],
    )
    service = ContextSearchService(store)

    async def fake_retrieve_candidates(query, top_k, source_ids):
        return {
            "candidates": [
                {"chunk_id": "readme-chunk-1", "score": 0.88, "vector_score": 0.77, "metadata_priority": 2},
            ],
            "effective_term_groups": [],
            "original_term_groups": [],
            "retrieval_queries": [query],
        }

    service._retrieve_candidates = fake_retrieve_candidates

    result = asyncio.run(service.search_documents("repo overview", top_k=5))

    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item.document_id == "doc-readme"
    assert item.chunk_id == "readme-chunk-1"
    assert item.source_id == "source_github"
    assert item.source_type == "github"
    assert item.title == "README"
    assert item.url == "https://github.com/example/repo/blob/main/README.md"
    assert item.path == "README.md"
    assert not hasattr(item, "preview")
    assert item.matched_context == "Repository overview chunk"
    assert item.vector_score == 0.77
    assert item.metadata_priority == 2


def test_search_documents_returns_full_matched_context_without_preview_truncation(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    matched_text = "Representative evidence " + ("continues beyond preview. " * 12)
    seed_document_chunks(
        store,
        "doc-long",
        "long-chunk-1",
        "source_target",
        "Long guide",
        matched_text,
    )
    service = ContextSearchService(store)

    async def fake_retrieve_candidates(query, top_k, source_ids):
        return {
            "candidates": [{"chunk_id": "long-chunk-1", "score": 0.95}],
            "effective_term_groups": [],
            "original_term_groups": [],
            "retrieval_queries": [query],
        }

    service._retrieve_candidates = fake_retrieve_candidates

    result = asyncio.run(service.search_documents("representative evidence", top_k=1))

    item = result["results"][0]
    assert len(matched_text) > 240
    assert item.matched_context == matched_text
    assert not hasattr(item, "preview")


def test_search_documents_expands_candidate_window_until_unique_document_target_is_met(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.GITHUB, "Target")
    seed_document_chunks(
        store,
        "doc-alpha",
        "alpha-chunk-1",
        "source_target",
        "Alpha",
        "Alpha top chunk",
        path="alpha.md",
        url="https://example.com/alpha",
    )
    seed_document_chunks(
        store,
        "doc-alpha",
        "alpha-chunk-2",
        "source_target",
        "Alpha",
        "Alpha second chunk",
        path="alpha.md",
        url="https://example.com/alpha",
    )
    seed_document_chunks(
        store,
        "doc-beta",
        "beta-chunk-1",
        "source_target",
        "Beta",
        "Beta chunk",
        path="beta.md",
        url="https://example.com/beta",
    )
    service = ContextSearchService(store)
    requested_limits = []

    async def fake_retrieve_candidates(query, top_k, source_ids):
        requested_limits.append(top_k)
        duplicate_heavy = [
            {"chunk_id": "alpha-chunk-1", "score": 0.99, "vector_score": 0.99, "metadata_priority": 0},
            {"chunk_id": "alpha-chunk-2", "score": 0.98, "vector_score": 0.98, "metadata_priority": 0},
            {"chunk_id": "beta-chunk-1", "score": 0.70, "vector_score": 0.70, "metadata_priority": 0},
        ]
        candidates = duplicate_heavy[: min(top_k, len(duplicate_heavy))]
        return {
            "candidates": candidates,
            "effective_term_groups": [],
            "original_term_groups": [],
            "retrieval_queries": [query],
        }

    service._retrieve_candidates = fake_retrieve_candidates
    service._document_search_candidate_limit = lambda top_k: 2
    service._max_retrieval_limit = lambda base_limit: 4

    result = asyncio.run(service.search_documents("alpha beta", top_k=2))

    assert requested_limits == [2, 4]
    assert [item.document_id for item in result["results"]] == ["doc-alpha", "doc-beta"]


def test_search_documents_keeps_first_reranked_chunk_as_document_representative(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.GITHUB, "Target")
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id="doc-alpha",
            document_id="doc-alpha",
            source_id="source_target",
            title="Alpha",
            content="Earlier reranked chunk\nLater chunk with same score",
            url="https://example.com/alpha",
            platform="Test",
            path="alpha.md",
        ),
        [
            ChunkModel(
                chunk_id="alpha-chunk-1",
                document_id="doc-alpha",
                source_id="source_target",
                title="Alpha",
                text="Earlier reranked chunk",
                url="https://example.com/alpha",
                path="alpha.md",
                chunk_index=0,
                content_hash="alpha-1",
            ),
            ChunkModel(
                chunk_id="alpha-chunk-2",
                document_id="doc-alpha",
                source_id="source_target",
                title="Alpha",
                text="Later chunk with same score",
                url="https://example.com/alpha",
                path="alpha.md",
                chunk_index=1,
                content_hash="alpha-2",
            ),
        ],
    )
    service = ContextSearchService(store)

    async def fake_retrieve_candidates(query, top_k, source_ids):
        return {
            "candidates": [
                {"chunk_id": "alpha-chunk-1", "score": 0.9, "vector_score": 0.6, "metadata_priority": 0},
                {"chunk_id": "alpha-chunk-2", "score": 0.9, "vector_score": 0.95, "metadata_priority": 1},
            ],
            "effective_term_groups": [],
            "original_term_groups": [],
            "retrieval_queries": [query],
        }

    service._retrieve_candidates = fake_retrieve_candidates

    result = asyncio.run(service.search_documents("alpha", top_k=1))

    assert result["results"][0].chunk_id == "alpha-chunk-1"


def test_search_documents_uses_same_deterministic_retrieval_contract(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-alpha",
        "alpha-chunk-1",
        "source_target",
        "Alpha",
        "Alpha chunk",
    )

    service = ContextSearchService(store)
    observed_queries = []

    async def fake_retrieve_candidates(query, top_k, source_ids):
        observed_queries.append(query)
        return {
            "candidates": [
                {"chunk_id": "alpha-chunk-1", "score": 0.9, "vector_score": 0.9, "metadata_priority": 0},
            ],
            "effective_term_groups": [],
            "original_term_groups": [],
            "retrieval_queries": [query],
        }

    service._retrieve_candidates = fake_retrieve_candidates

    result = asyncio.run(service.search_documents("alpha", top_k=1))

    assert observed_queries == ["alpha"]
    assert [item.document_id for item in result["results"]] == ["doc-alpha"]


def test_keyword_search_rerank_prefers_query_phrase_match_in_metadata(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "doc-architecture-guide",
        "architecture-guide-chunk",
        "source_github",
        "Project architecture guide",
        "Overview of services and boundaries.",
        path="docs/project-architecture-guide.md",
        url="https://github.com/example/repo/blob/main/docs/project-architecture-guide.md",
    )
    seed_document_chunks(
        store,
        "doc-runtime-notes",
        "runtime-notes-chunk",
        "source_github",
        "Runtime notes",
        "This note mentions project architecture guide ideas in passing.",
        path="notes/runtime.txt",
        url="https://github.com/example/repo/blob/main/notes/runtime.txt",
    )

    result = asyncio.run(
        ContextSearchService(store, retriever=list_search_documents(store)).search_context(
            "project architecture guide",
            top_k=2,
        )
    )

    assert [item.chunk_id for item in result["results"][:2]] == [
        "architecture-guide-chunk",
        "runtime-notes-chunk",
    ]


def test_keyword_search_rerank_prefers_matching_source_type_intent(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "doc-notion-sync",
        "notion-sync-chunk",
        "source_notion",
        "Notion sync notes",
        "Notion sync troubleshooting and checklist.",
        url="https://notion.so/notion-sync",
    )
    seed_document_chunks(
        store,
        "doc-github-sync",
        "github-sync-chunk",
        "source_github",
        "GitHub sync notes",
        "GitHub sync troubleshooting and checklist.",
        path="docs/github-sync.md",
        url="https://github.com/example/repo/blob/main/docs/github-sync.md",
    )

    result = asyncio.run(
        ContextSearchService(store, retriever=list_search_documents(store)).search_context(
            "notion sync notes",
            top_k=2,
        )
    )

    assert [item.chunk_id for item in result["results"][:2]] == [
        "notion-sync-chunk",
        "github-sync-chunk",
    ]


def test_keyword_search_uses_metadata_source_type_not_source_id_naming(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="team-knowledge",
            source_type=SourceType.NOTION,
            name="Team Knowledge",
            sync_status=SyncStatus.IDLE,
        )
    )
    store.upsert_source(
        SourceModel(
            source_id="arbitrary-tistory-source",
            source_type=SourceType.TISTORY,
            name="Arbitrary Tistory",
            sync_status=SyncStatus.IDLE,
        )
    )
    seed_document_chunks(
        store,
        "doc-notion-sync",
        "notion-sync-arbitrary-id",
        "team-knowledge",
        "Notion sync notes",
        "Notion sync troubleshooting and checklist.",
        url="https://www.notion.so/notion-sync",
    )
    seed_document_chunks(
        store,
        "doc-tistory-sync",
        "tistory-sync-arbitrary-id",
        "arbitrary-tistory-source",
        "Generic sync notes",
        "Notion sync troubleshooting and checklist.",
        url="https://devlog.tistory.com/42",
    )

    result = asyncio.run(
        ContextSearchService(store, retriever=list_search_documents(store)).search_context(
            "notion sync notes",
            top_k=2,
        )
    )

    assert [item.chunk_id for item in result["results"][:2]] == [
        "notion-sync-arbitrary-id",
        "tistory-sync-arbitrary-id",
    ]


def test_search_context_debug_exposes_intent_for_collection_query(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_document_chunks(
        store,
        "doc-aws-guide",
        "aws-guide-chunk",
        "source_notion",
        "AWS deployment guide",
        "Amazon Web Services deployment checklist and setup notes.",
    )

    result = asyncio.run(
        ContextSearchService(store, retriever=list_search_documents(store)).search_context(
            "AWS 관련 문서 모아줘",
            top_k=1,
            include_debug=True,
        )
    )

    assert result["debug"]["intent"]["name"] == "list"
    assert "list_hint" in result["debug"]["intent"]["reasons"]


def test_search_context_debug_exposes_strict_lookup_intent_name(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:example/repo:docs/usage.md",
        "usage-doc-chunk",
        "source_github",
        "Usage guide",
        "Usage guide content.",
        path="docs/usage.md",
        url="https://github.com/example/repo/blob/main/docs/usage.md",
    )

    result = asyncio.run(
        ContextSearchService(store, retriever=list_search_documents(store)).search_context(
            "repo docs",
            top_k=1,
            include_debug=True,
        )
    )

    assert result["debug"]["intent"]["name"] == "strict_lookup"


def test_search_context_avoids_false_github_bias_for_plain_lowercase_long_token_document_query(
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:example/repo:docs/usage.md",
        "usage-doc-chunk",
        "source_github",
        "Usage guide",
        "Usage guide content.",
        path="docs/usage.md",
        url="https://github.com/example/repo/blob/main/docs/usage.md",
    )

    result = asyncio.run(
        ContextSearchService(store, retriever=list_search_documents(store)).search_context(
            "runtimeconfiguration docs",
            top_k=3,
        )
    )

    assert result["results"] == []


def test_search_context_prefers_non_github_match_for_plain_lowercase_long_token_document_query(
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "notion-runtimeconfiguration",
        "notion-runtimeconfiguration-doc",
        "source_notion",
        "Runtime configuration docs",
        "Runtimeconfiguration deployment notes.",
    )
    seed_document_chunks(
        store,
        "github:example/repo:docs/runtimeconfiguration.md",
        "github-runtimeconfiguration-doc",
        "source_github",
        "Runtime configuration guide",
        "Runtimeconfiguration usage notes.",
        path="docs/runtimeconfiguration.md",
        url="https://github.com/example/repo/blob/main/docs/runtimeconfiguration.md",
    )

    result = asyncio.run(
        ContextSearchService(store, retriever=list_search_documents(store)).search_context(
            "runtimeconfiguration docs",
            top_k=3,
        )
    )

    assert result["results"][0].chunk_id == "notion-runtimeconfiguration-doc"


def test_search_context_prefers_mixed_language_comparison_documents_without_github_doc_bias(
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_source(store, "source_tistory", SourceType.TISTORY, "Tistory")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "notion:dynamodb-notes",
        "dynamodb-notes-chunk",
        "source_notion",
        "DynamoDB notes",
        "DynamoDB comparison notes with strengths, scaling characteristics, and operational tradeoffs.",
    )
    seed_document_chunks(
        store,
        "tistory:cassandra-notes",
        "cassandra-notes-chunk",
        "source_tistory",
        "Cassandra notes",
        "Cassandra comparison notes with consistency, partitioning, and operational tradeoffs.",
    )
    seed_document_chunks(
        store,
        "github:example/repo:docs/adr/0006-slim-core.md",
        "adr-markdown-chunk",
        "source_github",
        "ADR 0006 slim MCP core scope",
        "# ADR 0006\nSlim MCP core scope for retained retrieval and markdown decision records.",
        path="docs/adr/0006-slim-core.md",
        url="https://github.com/example/repo/blob/main/docs/adr/0006-slim-core.md",
    )

    result = asyncio.run(
        ContextSearchService(store, retriever=list_search_documents(store)).search_context(
            "dynamodb와 cassandra 문서 비교해줘",
            top_k=3,
            include_debug=True,
        )
    )

    assert result["debug"]["intent"]["name"] == "comparison"
    assert {item.chunk_id for item in result["results"][:2]} == {
        "dynamodb-notes-chunk",
        "cassandra-notes-chunk",
    }
    assert all(item.source_id != "source_github" for item in result["results"][:2])


def test_broad_topic_list_query_prefers_document_like_docs_before_code(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:example/repo:docs/aws-overview.md",
        "aws-doc-chunk",
        "source_github",
        "AWS Overview",
        "AWS overview, architecture, and deployment checklist.",
        path="docs/aws-overview.md",
        url="https://github.com/example/repo/blob/main/docs/aws-overview.md",
    )
    seed_document_chunks(
        store,
        "github:example/repo:src/aws_client.py",
        "aws-code-chunk",
        "source_github",
        "aws_client.py",
        "class AwsClient: pass",
        path="src/aws_client.py",
        url="https://github.com/example/repo/blob/main/src/aws_client.py",
    )

    class AwsMixedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            doc_node = FakeNode("aws-doc-chunk", 0.82)
            doc_node.metadata["document_id"] = "github:example/repo:docs/aws-overview.md"
            doc_node.metadata["source_id"] = "source_github"
            code_node = FakeNode("aws-code-chunk", 0.91)
            code_node.metadata["document_id"] = "github:example/repo:src/aws_client.py"
            code_node.metadata["source_id"] = "source_github"
            return [code_node, doc_node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", AwsMixedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "AWS 관련 문서 모아줘",
            top_k=2,
        )
    )

    assert result["results"][0].chunk_id == "aws-doc-chunk"


def test_broad_topic_duplicate_document_penalty_prefers_best_sibling_not_first_seen(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    shared_document_id = "github:example/repo:docs/aws-guide.md"
    seed_document_chunks(
        store,
        shared_document_id,
        "aws-guide-weak",
        "source_github",
        "AWS guide weak",
        "AWS notes",
        path="docs/aws-guide.md",
        url="https://github.com/example/repo/blob/main/docs/aws-guide.md",
    )
    chunk = store.get_chunk("aws-guide-weak")
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=shared_document_id,
            document_id=shared_document_id,
            external_id=shared_document_id,
            source_id="source_github",
            title="AWS guide",
            content="AWS overview and deployment checklist.",
            url="https://github.com/example/repo/blob/main/docs/aws-guide.md",
            canonical_url="https://github.com/example/repo/blob/main/docs/aws-guide.md",
            platform="GitHub",
            path="docs/aws-guide.md",
        ),
        [
                chunk.model_copy(update={"chunk_id": "aws-guide-weak", "text": "AWS notes"}),
                chunk.model_copy(
                    update={
                        "chunk_id": "aws-guide-strong",
                        "text": "AWS architecture deployment checklist and overview notes",
                        "content_hash": "aws-guide-strong",
                    }
                ),
        ],
    )

    class DuplicateDocRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            weak = FakeNode("aws-guide-weak", 0.9)
            weak.metadata["document_id"] = shared_document_id
            weak.metadata["source_id"] = "source_github"
            strong = FakeNode("aws-guide-strong", 0.8)
            strong.metadata["document_id"] = shared_document_id
            strong.metadata["source_id"] = "source_github"
            return [weak, strong]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", DuplicateDocRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "AWS architecture deployment checklist 모아줘",
            top_k=2,
        )
    )

    assert result["results"][0].chunk_id == "aws-guide-strong"


def test_search_context_uses_korean_obsidian_source_intent_for_metadata_fallback(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "team-vault", SourceType.OBSIDIAN, "Team Vault")
    seed_document_chunks(
        store,
        "daily-note",
        "daily-note-chunk",
        "team-vault",
        "Daily Note",
        "Project planning note without a source label.",
        path="notes/daily.md",
        url="obsidian://open?vault=team&file=notes/daily.md",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr(
        "search.context_service.VectorIndexRetriever",
        EmptyVectorRetriever,
    )

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "옵시디언",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "daily-note-chunk"
    assert result["results"][0].source_id == "team-vault"
    assert result["results"][0].source_type == "obsidian"


def test_search_context_uses_obsidian_source_intent_when_vector_results_fill_top_k(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "team-vault", SourceType.OBSIDIAN, "Team Vault")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_document_chunks(
        store,
        "daily-note",
        "daily-note-chunk",
        "team-vault",
        "Daily Note",
        "Project planning note without a source label.",
        path="notes/daily.md",
        url="obsidian://open?vault=team&file=notes/daily.md",
    )
    seed_document_chunks(
        store,
        "notion-note",
        "notion-note-chunk",
        "source_notion",
        "Notion Note",
        "Unrelated workspace note.",
    )

    class NotionVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("notion-note-chunk", 0.99)
            node.metadata["document_id"] = "notion-note"
            node.metadata["source_id"] = "source_notion"
            return [node]

    monkeypatch.setattr(
        "search.context_service.VectorIndexRetriever",
        NotionVectorRetriever,
    )

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "옵시디언",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "daily-note-chunk"
    assert result["results"][0].source_id == "team-vault"
    assert result["results"][0].source_type == "obsidian"


def test_keyword_search_ignores_misleading_source_id_when_source_type_disagrees(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="notion-mirror",
            source_type=SourceType.TISTORY,
            name="Notion Mirror",
            sync_status=SyncStatus.IDLE,
        )
    )
    store.upsert_source(
        SourceModel(
            source_id="team-notes",
            source_type=SourceType.NOTION,
            name="Team Notes",
            sync_status=SyncStatus.IDLE,
        )
    )
    seed_document_chunks(
        store,
        "doc-tistory-mirror",
        "tistory-mirror-chunk",
        "notion-mirror",
        "Mirror sync notes",
        "Notion sync troubleshooting and checklist.",
        url="https://devlog.tistory.com/notion-sync",
    )
    seed_document_chunks(
        store,
        "doc-real-notion",
        "real-notion-chunk",
        "team-notes",
        "Notion sync notes",
        "Notion sync troubleshooting and checklist.",
        url="https://www.notion.so/team-sync",
    )

    result = asyncio.run(
        ContextSearchService(store, retriever=list_search_documents(store)).search_context(
            "notion sync notes",
            top_k=2,
        )
    )

    assert [item.chunk_id for item in result["results"][:2]] == [
        "real-notion-chunk",
        "tistory-mirror-chunk",
    ]


def test_removed_web_source_terms_stay_ordinary_content_terms_without_source_type_bonus(tmp_path):
    term_groups = ranking.query_term_groups("web auth guide")
    assert ranking.query_source_type_terms(term_groups) == set()
    assert "web" in {term for group in term_groups for term in group}

    for query in ("website auth guide", "site auth guide", "docs auth guide"):
        assert ranking.query_source_type_terms(ranking.query_term_groups(query)) == set()

    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Team Notes")
    seed_legacy_web_source(store)
    seed_document_chunks(
        store,
        "doc-web-auth-guide",
        "web-auth-guide-chunk",
        "source_notion",
        "Web auth guide",
        "Web auth guide checklist for the team portal.",
    )
    seed_document_chunks(
        store,
        "doc-legacy-web-auth",
        "legacy-web-auth-chunk",
        "source_web",
        "Legacy auth guide",
        "Auth guide checklist.",
    )

    service = ContextSearchService(
        store,
        retriever=list_search_documents(store),
        default_source_ids=["source_notion"],
    )
    legacy_document = DocumentModel(
        id="doc-legacy-web-auth",
        document_id="doc-legacy-web-auth",
        source_id="source_web",
        title="Legacy auth guide",
        content="Auth guide checklist.",
        url="https://example.com/legacy-auth-guide",
        platform="Legacy",
    )

    assert not service.ranker.document_matches_source_type_terms(legacy_document, {"web"})

    result = asyncio.run(service.search_context("web auth guide", top_k=1))

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "web-auth-guide-chunk"


def test_search_context_debug_redacts_paths_and_credential_urls(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-ec2",
        "ec2-chunk",
        "source_target",
        "EC2 setup guide",
        "EC2 setup and instance launch notes.",
    )

    class FakeVectorIndexRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("ec2-chunk", 0.91)
            node.metadata["document_id"] = "doc-ec2"
            node.metadata["source_id"] = "source_target"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "https://user:pass@example.com/path /Users/eunhwa/private/doc.md",
            top_k=1,
            include_debug=True,
        )
    )

    assert result["debug"]["retrieval_queries"]
    assert "/Users/eunhwa" not in str(result["debug"])
    assert "user:pass@" not in str(result["debug"])
    assert "~/private" not in str(result["debug"])
    assert "C:/Users" not in str(result["debug"])


def test_search_context_redacts_public_grounding_but_keeps_internal_groups(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-debug",
        "debug-chunk",
        "source_target",
        "ContextZip debug guide",
        "ContextZip debug guide content.",
    )

    result = asyncio.run(
        ContextSearchService(store, retriever=list_search_documents(store)).search_context(
            "context-wiki-debug guide",
            top_k=1,
            include_debug=True,
        )
    )

    assert ["[REDACTED]"] in result["_grounding"]["effective_term_groups"]
    assert ["[REDACTED]"] in result["debug"]["effective_term_groups"]


def test_search_context_public_payload_omits_internal_grounding(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-debug",
        "debug-chunk",
        "source_target",
        "ContextZip debug guide",
        "ContextZip debug guide content.",
    )

    public_result = asyncio.run(
        ContextSearchService(store, retriever=list_search_documents(store)).search_context(
            "context-wiki-debug guide",
            top_k=1,
        )
    )
    assert "_internal_grounding" not in public_result


def test_keyword_search_treats_custom_github_source_ids_as_github_documents(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "github-team-docs", SourceType.GITHUB, "GitHub Team Docs")
    seed_document_chunks(
        store,
        "doc-readme",
        "readme-chunk",
        "github-team-docs",
        "Neetcode graph docs",
        "Graph study guide and linked docs.",
        path="docs/neetcode-graph.md",
    )
    seed_document_chunks(
        store,
        "doc-code",
        "code-chunk",
        "github-team-docs",
        "Graph implementation",
        "Graph code implementation details.",
        path="src/graph.py",
    )

    result = asyncio.run(
        ContextSearchService(store, retriever=list_search_documents(store)).search_context(
            "neetcode graph docs",
            top_k=2,
        )
    )

    assert [item.chunk_id for item in result["results"]] == ["readme-chunk"]


def test_search_context_debug_redacts_http_paths_not_only_query_strings(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-guide",
        "guide-chunk",
        "source_target",
        "Guide",
        "Guide content.",
    )

    class FakeVectorIndexRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("guide-chunk", 0.91)
            node.metadata["document_id"] = "doc-guide"
            node.metadata["source_id"] = "source_target"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "https://example.com/private/token/opaque-value?signature=secret-value",
            top_k=1,
            include_debug=True,
        )
    )

    debug_text = str(result["debug"])
    assert "opaque-value" not in debug_text
    assert "/private/token" not in debug_text


def test_search_context_debug_redacts_secret_like_tokens(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-ec2",
        "ec2-chunk",
        "source_target",
        "EC2 setup guide",
        "EC2 setup and instance launch notes.",
    )

    class FakeVectorIndexRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("ec2-chunk", 0.91)
            node.metadata["document_id"] = "doc-ec2"
            node.metadata["source_id"] = "source_target"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "token=ghp_example123 sk-proj-abcdefghijklmnopqrstuvwxyz",
            top_k=1,
            include_debug=True,
        )
    )

    debug_text = str(result["debug"])
    assert "ghp_example123" not in debug_text
    assert "sk-proj-" not in debug_text
    assert "[REDACTED]" in debug_text


def test_vector_search_pushes_source_filter_into_retriever(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_target",
            source_type=SourceType.NOTION,
            name="Target",
            sync_status=SyncStatus.IDLE,
        )
    )
    store.upsert_source(
        SourceModel(
            source_id="source_other",
            source_type=SourceType.TISTORY,
            name="Other",
            sync_status=SyncStatus.IDLE,
        )
    )
    seed_document_chunks(store, "doc-target", "target-chunk", "source_target", "Target", "target context")
    seed_document_chunks(store, "doc-other", "other-chunk", "source_other", "Other", "other context")

    class FakeVectorIndexRetriever:
        captured_filters = None

        def __init__(self, **kwargs):
            FakeVectorIndexRetriever.captured_filters = kwargs.get("filters")

        def retrieve(self, query):
            if FakeVectorIndexRetriever.captured_filters is None:
                node = FakeNode("other-chunk", 0.99)
                node.metadata["document_id"] = "doc-other"
                node.metadata["source_id"] = "source_other"
                return [node]
            node = FakeNode("target-chunk", 0.88)
            node.metadata["document_id"] = "doc-target"
            node.metadata["source_id"] = "source_target"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(async_search(ContextSearchService(store, indexer=FakeIndexer())))

    assert FakeVectorIndexRetriever.captured_filters is not None
    assert "context_zip_managed" in str(FakeVectorIndexRetriever.captured_filters)
    assert "source_id" in str(FakeVectorIndexRetriever.captured_filters)
    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "target-chunk"


def test_vector_search_filters_to_context_zip_managed_chunks_by_default(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_target",
            source_type=SourceType.NOTION,
            name="Target",
            sync_status=SyncStatus.IDLE,
        )
    )
    seed_document_chunks(store, "doc-target", "target-chunk", "source_target", "Target", "target context")

    class FakeVectorIndexRetriever:
        captured_filters = None

        def __init__(self, **kwargs):
            FakeVectorIndexRetriever.captured_filters = kwargs.get("filters")

        def retrieve(self, query):
            if "context_zip_managed" not in str(FakeVectorIndexRetriever.captured_filters):
                return [FakeNode("legacy-0", 0.99)]
            node = FakeNode("target-chunk", 0.5)
            node.metadata["document_id"] = "doc-target"
            node.metadata["source_id"] = "source_target"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context("context", top_k=1)
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "target-chunk"
    assert "context_zip_managed" in str(FakeVectorIndexRetriever.captured_filters)


def test_vector_search_accepts_legacy_managed_metadata_for_existing_chunks(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(
        store,
        "doc-target",
        "target-chunk",
        "source_target",
        "Target",
        "target context",
    )

    class FakeVectorIndexRetriever:
        captured_filters = None

        def __init__(self, **kwargs):
            FakeVectorIndexRetriever.captured_filters = kwargs.get("filters")

        def retrieve(self, query):
            filter_text = str(FakeVectorIndexRetriever.captured_filters)
            if "context_zip_managed" not in filter_text:
                return []
            if ("context" + "wiki_managed") not in filter_text:
                return []
            node = FakeNode("target-chunk", 0.92)
            node.metadata.pop("context_zip_managed")
            node.metadata["context" + "wiki_managed"] = "true"
            node.metadata["document_id"] = "doc-target"
            node.metadata["source_id"] = "source_target"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context("context", top_k=1)
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "target-chunk"
    assert "context_zip_managed" in str(FakeVectorIndexRetriever.captured_filters)
    assert ("context" + "wiki_managed") in str(
        FakeVectorIndexRetriever.captured_filters
    )


def test_vector_search_metadata_filters_translate_to_chroma_compatible_filter():
    from search.retrieval_pipeline import metadata_filters

    chroma_filter = _to_chroma_filter(metadata_filters(["source_target"]))

    assert chroma_filter == {
        "$and": [
            {
                "$or": [
                    {"context_zip_managed": {"$eq": "true"}},
                    {("context" + "wiki_managed"): {"$eq": "true"}},
                ]
            },
            {"source_id": {"$eq": "source_target"}},
        ]
    }


def test_vector_search_expands_past_stale_managed_window(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(store, "doc-target", "target-chunk", "source_target", "Target", "target context")
    stale_nodes = [FakeNode(f"stale-{index}", 0.99) for index in range(16)]
    active_node = FakeNode("target-chunk", 0.5)
    active_node.metadata["document_id"] = "doc-target"
    active_node.metadata["source_id"] = "source_target"
    all_nodes = [*stale_nodes, active_node]
    requested_limits = []

    class FakeVectorIndexRetriever:
        def __init__(self, **kwargs):
            requested_limits.append(kwargs.get("similarity_top_k"))
            self.limit = kwargs.get("similarity_top_k")

        def retrieve(self, query):
            return all_nodes[: self.limit]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context("context", top_k=1)
    )

    assert requested_limits[:5] == [2, 4, 8, 16, 32]
    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "target-chunk"


def test_vector_search_rejects_managed_hit_with_mismatched_owner(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(store, "doc-target", "target-chunk", "source_target", "Target", "target context")

    class FakeVectorIndexRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("target-chunk", 0.99)
            node.metadata["document_id"] = "doc-target"
            node.metadata["source_id"] = "source_other"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context("context", top_k=1)
    )

    assert result["results"] == []


def test_vector_search_rejects_managed_hit_missing_owner_metadata(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(store, "doc-target", "target-chunk", "source_target", "Target", "target context")

    class FakeVectorIndexRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return [FakeNode("target-chunk", 0.99)]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context("context", top_k=1)
    )

    assert result["results"] == []


def test_vector_search_rejects_hit_missing_managed_marker(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(store, "doc-target", "target-chunk", "source_target", "Target", "target context")

    class FakeVectorIndexRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("target-chunk", 0.99)
            node.metadata.pop("context_zip_managed")
            node.metadata["document_id"] = "doc-target"
            node.metadata["source_id"] = "source_target"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context("context", top_k=1)
    )

    assert result["results"] == []


def test_vector_search_keeps_looking_after_rejected_duplicate_managed_hit(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(store, "doc-target", "target-chunk", "source_target", "Target", "target context")

    class FakeVectorIndexRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            stale = FakeNode("target-chunk", 0.99)
            stale.metadata["document_id"] = "doc-target"
            stale.metadata["source_id"] = "source_other"
            fresh = FakeNode("target-chunk", 0.5)
            fresh.metadata["document_id"] = "doc-target"
            fresh.metadata["source_id"] = "source_target"
            return [stale, fresh]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context("context", top_k=1)
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "target-chunk"


def test_search_context_accepts_singular_source_id_filter(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_source(store, "source_other", SourceType.TISTORY, "Other")
    seed_document_chunks(
        store,
        "doc-target",
        "target-chunk",
        "source_target",
        "Target",
        "ContextZip citations target",
    )
    seed_document_chunks(
        store,
        "doc-other",
        "other-chunk",
        "source_other",
        "Other",
        "ContextZip citations other",
    )
    documents = [
        store.get_chunk("other-chunk").to_document_model(),
        store.get_chunk("target-chunk").to_document_model(),
    ]

    result = asyncio.run(
        ContextSearchService(store, retriever=documents).search_context(
            "ContextZip citations",
            filters={"source_id": "source_target"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].source_id == "source_target"


def test_search_context_unions_singular_and_plural_source_filters(tmp_path):
    from core.models import SearchFilters

    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_source(store, "source_other", SourceType.TISTORY, "Other")
    for source_id in ("source_target", "source_other"):
        seed_document_chunks(
            store,
            f"doc-{source_id}",
            f"chunk-{source_id}",
            source_id,
            source_id,
            "ContextZip source union evidence",
        )
    documents = [
        store.get_chunk(f"chunk-{source_id}").to_document_model()
        for source_id in ("source_target", "source_other")
    ]

    result = asyncio.run(
        ContextSearchService(store, retriever=documents).search_context(
            "ContextZip source union",
            filters=SearchFilters(
                source_id="source_target",
                source_ids=["source_other"],
            ),
            top_k=2,
        )
    )

    assert {item.source_id for item in result["results"]} == {
        "source_target",
        "source_other",
    }


def test_search_context_returns_chunk_version_id(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "doc-github",
        "github-chunk",
        "source_github",
        "README.md",
        "ContextZip citations include blob versions.",
        version_id="blob-version-123",
    )
    documents = [store.get_chunk("github-chunk").to_document_model()]

    result = asyncio.run(
        ContextSearchService(store, retriever=documents).search_context(
            "ContextZip citations",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].version_id == "blob-version-123"


def test_search_context_matches_github_identity_metadata_when_body_does_not(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/ImageGallery/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-chunk",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/ImageGallery/usage.md",
                chunk_index=0,
                content_hash="imagegallery-chunk",
            )
        ],
    )

    result = asyncio.run(ContextSearchService(store).search_context("ImageGallery", top_k=1))

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-chunk"


def test_no_indexer_plain_topic_uses_bounded_text_lookup(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seeded_chunk = ChunkModel(
        chunk_id="notion-typescript-body",
        document_id="notion-typescript-body",
        source_id="source_notion",
        title="Language notes",
        text="The frontend migration uses TypeScript.",
        chunk_index=0,
        content_hash="notion-typescript-body",
    )
    seed_document_chunks(
        store,
        "notion-typescript-body",
        "notion-typescript-body",
        "source_notion",
        "Language notes",
        "The frontend migration uses TypeScript.",
    )
    matching_calls = []

    def fail_list_chunks(source_ids=None):
        raise AssertionError("no-indexer ordinary query should use bounded text lookup")

    def fake_matching_terms(
        terms,
        source_ids=None,
        limit=200,
        require_document_like=False,
        include_text=False,
        require_all_terms=False,
        metadata_only_terms=None,
    ):
        matching_calls.append(
            {
                "terms": set(terms),
                "source_ids": source_ids,
                "include_text": include_text,
            }
        )
        if set(terms) == {"typescript"} and source_ids is None and include_text:
            return [seeded_chunk]
        return []

    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fake_matching_terms)

    result = asyncio.run(ContextSearchService(store).search_context("typescript", top_k=1))

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-typescript-body"
    assert {
        "terms": {"typescript"},
        "source_ids": None,
        "include_text": True,
    } in matching_calls


def test_no_indexer_github_repo_query_uses_bounded_metadata_lookup(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    chunk = ChunkModel(
        chunk_id="imagegallery-chunk",
        document_id=document_id,
        source_id="source_github",
        title="eunhwa99/ImageGallery docs/usage.md",
        text="Component usage notes and layout details.",
        url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
        path="docs/ImageGallery/usage.md",
        chunk_index=0,
        content_hash="imagegallery-chunk",
    )
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/ImageGallery/usage.md",
        ),
        [chunk],
    )
    matching_calls = []

    def fail_list_chunks(source_ids=None):
        raise AssertionError("no-indexer GitHub repo query should use bounded metadata lookup")

    def fake_matching_terms(
        terms,
        source_ids=None,
        limit=200,
        require_document_like=False,
        include_text=False,
        require_all_terms=False,
        metadata_only_terms=None,
    ):
        matching_calls.append(
            {
                "terms": set(terms),
                "source_ids": source_ids,
                "require_document_like": require_document_like,
                "include_text": include_text,
            }
        )
        if set(terms) == {"imagegallery"} and source_ids == ["source_github"]:
            return [chunk]
        return []

    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fake_matching_terms)

    result = asyncio.run(
        ContextSearchService(store).search_context(
            "ImageGallery",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-chunk"
    assert {
        "terms": {"imagegallery"},
        "source_ids": ["source_github"],
        "require_document_like": True,
        "include_text": False,
    } in matching_calls


def test_no_indexer_stop_word_only_query_skips_metadata_scan(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")

    def fail_list_chunks(source_ids=None):
        raise AssertionError("no-indexer stop-word-only query should not scan chunks")

    def fail_matching_terms(
        terms,
        source_ids=None,
        limit=200,
        require_document_like=False,
        include_text=False,
        require_all_terms=False,
        metadata_only_terms=None,
    ):
        raise AssertionError("no-indexer stop-word-only query has no bounded terms")

    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fail_matching_terms)

    result = asyncio.run(ContextSearchService(store).search_context("search for", top_k=1))

    assert result["results"] == []


def test_search_context_metadata_match_competes_with_full_vector_window(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-github-chunk",
        "source_github",
        "Other repository README",
        "Generic project documentation.",
    )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/ImageGallery/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-chunk",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/ImageGallery/usage.md",
                chunk_index=0,
                content_hash="imagegallery-chunk",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-github-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-chunk"
    assert result["results"][0].score >= 1.0


def test_search_context_ignores_common_request_words_for_metadata_boost(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-github-chunk",
        "source_github",
        "Other repository README",
        "Generic project documentation.",
    )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/ImageGallery/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-chunk",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/ImageGallery/usage.md",
                chunk_index=0,
                content_hash="imagegallery-chunk",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-github-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "show me ImageGallery docs",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-chunk"


@pytest.mark.parametrize(
    "query",
    ["search ImageGallery docs", "search for ImageGallery docs"],
)
def test_search_context_ignores_search_request_words_for_metadata_boost(
    monkeypatch,
    tmp_path,
    query,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-github-chunk",
        "source_github",
        "Other repository README",
        "Generic project documentation.",
    )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/ImageGallery/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-search-docs",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/ImageGallery/usage.md",
                chunk_index=0,
                content_hash="imagegallery-search-docs",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-github-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            query,
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-search-docs"


def test_search_context_ignores_extended_request_words_for_metadata_boost(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-github-chunk",
        "source_github",
        "Other repository README",
        "Generic project documentation.",
    )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/ImageGallery/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-chunk",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/ImageGallery/usage.md",
                chunk_index=0,
                content_hash="imagegallery-chunk",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-github-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "tell me about ImageGallery docs",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-chunk"


def test_search_context_matches_camelcase_repository_name_when_vector_window_is_full(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-github-chunk",
        "source_github",
        "Other repository README",
        "Generic project documentation.",
    )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/ImageGallery/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-chunk",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/ImageGallery/usage.md",
                chunk_index=0,
                content_hash="imagegallery-chunk",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-github-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-chunk"


def test_search_context_ignores_korean_search_filler_for_repository_metadata_boost(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-github-chunk",
        "source_github",
        "Other repository README",
        "Generic project documentation.",
    )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/ImageGallery/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-chunk",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/ImageGallery/usage.md",
                chunk_index=0,
                content_hash="imagegallery-chunk",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-github-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    for query in ("ImageGallery 라고 검색", "ImageGallery 검색해도", "ImageGallery라고 검색해도"):
        result = asyncio.run(
            ContextSearchService(store, indexer=FakeIndexer()).search_context(
                query,
                top_k=1,
            )
        )

        assert len(result["results"]) == 1
        assert result["results"][0].chunk_id == "imagegallery-chunk"


def test_search_context_metadata_identity_match_wins_vector_score_tie(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-github-chunk",
        "source_github",
        "Other repository README",
        "Generic project documentation.",
    )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/ImageGallery/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-chunk",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/ImageGallery/usage.md",
                chunk_index=0,
                content_hash="imagegallery-chunk",
            )
        ],
    )

    class TiedUnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-github-chunk", 1.0)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", TiedUnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-chunk"


def test_search_context_metadata_identity_match_wins_oversized_vector_score(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-github-chunk",
        "source_github",
        "Other repository README",
        "Generic project documentation.",
    )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/ImageGallery/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-chunk",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/ImageGallery/usage.md",
                chunk_index=0,
                content_hash="imagegallery-chunk",
            )
        ],
    )

    class OversizedUnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-github-chunk", 1.5)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", OversizedUnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-chunk"


def test_search_context_metadata_priority_is_preserved_for_existing_high_score_vector_hit(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-github-chunk",
        "source_github",
        "Other repository README",
        "Generic project documentation.",
    )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/ImageGallery/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-chunk",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/ImageGallery/usage.md",
                chunk_index=0,
                content_hash="imagegallery-chunk",
            )
        ],
    )

    class MixedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            unrelated = FakeNode("other-github-chunk", 1.5)
            unrelated.metadata["document_id"] = "github:eunhwa99/other:README.md"
            unrelated.metadata["source_id"] = "source_github"
            exact = FakeNode("imagegallery-chunk", 1.2)
            exact.metadata["document_id"] = document_id
            exact.metadata["source_id"] = "source_github"
            return [unrelated, exact]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", MixedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-chunk"


def test_search_context_treats_readme_as_document_for_neetcode_korean_query_with_vector_competition(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-github-chunk",
        "source_github",
        "Other repository README",
        "Generic project documentation.",
    )
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:README.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="README",
            content="Dynamic programming notes.",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            platform="GitHub",
            path="README.md",
        ),
        [
            ChunkModel(
                chunk_id="neetcode-readme-chunk",
                document_id=document_id,
                source_id="source_github",
                title="README",
                text="Dynamic programming notes.",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
                path="README.md",
                chunk_index=0,
                content_hash="neetcode-readme-chunk",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-github-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "니트코드 문서 찾아와",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "neetcode-readme-chunk"


def test_search_context_rejects_neetcode_docs_query_for_code_only_metadata_match(
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:Graph.java"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="Graph.java",
            content="class GraphSolution { void dfs() {} }",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/Graph.java",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/Graph.java",
            platform="GitHub",
            path="Graph.java",
        ),
        [
            ChunkModel(
                chunk_id="code-only",
                document_id=document_id,
                source_id="source_github",
                title="Graph.java",
                text="class GraphSolution { void dfs() {} }",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/Graph.java",
                path="Graph.java",
                chunk_index=0,
                content_hash="code-only",
            )
        ],
    )

    result = asyncio.run(
        ContextSearchService(store).search_context(
            "니트코드 문서 찾아와",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert result["results"] == []


def test_search_context_rejects_neetcode_docs_query_for_code_only_vector_hit(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:Graph.java"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="Graph.java",
            content="class GraphSolution { void dfs() {} }",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/Graph.java",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/Graph.java",
            platform="GitHub",
            path="Graph.java",
        ),
        [
            ChunkModel(
                chunk_id="code-only-vector",
                document_id=document_id,
                source_id="source_github",
                title="Graph.java",
                text="class GraphSolution { void dfs() {} }",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/Graph.java",
                path="Graph.java",
                chunk_index=0,
                content_hash="code-only-vector",
            )
        ],
    )

    class CodeOnlyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("code-only-vector", 0.99)
            node.metadata["document_id"] = document_id
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", CodeOnlyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "니트코드 문서 찾아와",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert result["results"] == []


def test_search_context_matches_neetcode_doc_with_topic_only_in_readme_body(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-github-chunk",
        "source_github",
        "Other README",
        "Unrelated docs.",
    )
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:README.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/neetcode-submissions-8ogaz8xl README",
            content="Graph traversal notes live in this README.",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            platform="GitHub",
            path="README.md",
        ),
        [
            ChunkModel(
                chunk_id="neetcode-readme-body-graph",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/neetcode-submissions-8ogaz8xl README",
                text="Graph traversal notes live in this README.",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
                path="README.md",
                chunk_index=0,
                content_hash="neetcode-readme-body-graph",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-github-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    def fail_list_chunks(source_ids=None):
        raise AssertionError("strong-anchor docs fallback should use bounded lookup")

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "니트코드 그래프 문서 찾아와",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "neetcode-readme-body-graph"


def test_search_context_prefers_neetcode_over_leetcode_for_korean_anchor(
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    for document_id, chunk_id, title, url in (
        (
            "github:eunhwa99/leetcode-solutions:README.md",
            "leetcode-readme",
            "eunhwa99/leetcode-solutions README",
            "https://github.com/eunhwa99/leetcode-solutions/blob/main/README.md",
        ),
        (
            "github:eunhwa99/neetcode-submissions-8ogaz8xl:README.md",
            "neetcode-readme",
            "eunhwa99/neetcode-submissions-8ogaz8xl README",
            "https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
        ),
    ):
        store.upsert_document_and_replace_chunks(
            DocumentModel(
                id=document_id,
                document_id=document_id,
                external_id=document_id,
                source_id="source_github",
                title=title,
                content="Study notes.",
                url=url,
                canonical_url=url,
                platform="GitHub",
                path="README.md",
            ),
            [
                ChunkModel(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    source_id="source_github",
                    title=title,
                    text="Study notes.",
                    url=url,
                    path="README.md",
                    chunk_index=0,
                    content_hash=chunk_id,
                )
            ],
        )

    result = asyncio.run(
        ContextSearchService(store).search_context(
            "니트코드 문서 찾아와",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "neetcode-readme"


def test_search_context_prefers_neetcode_for_no_space_korean_anchor_and_document_intent(
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    for document_id, chunk_id, title, url in (
        (
            "github:eunhwa99/leetcode-solutions:README.md",
            "leetcode-readme",
            "eunhwa99/leetcode-solutions README",
            "https://github.com/eunhwa99/leetcode-solutions/blob/main/README.md",
        ),
        (
            "github:eunhwa99/neetcode-submissions-8ogaz8xl:README.md",
            "neetcode-readme",
            "eunhwa99/neetcode-submissions-8ogaz8xl README",
            "https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
        ),
    ):
        store.upsert_document_and_replace_chunks(
            DocumentModel(
                id=document_id,
                document_id=document_id,
                external_id=document_id,
                source_id="source_github",
                title=title,
                content="Study notes.",
                url=url,
                canonical_url=url,
                platform="GitHub",
                path="README.md",
            ),
            [
                ChunkModel(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    source_id="source_github",
                    title=title,
                    text="Study notes.",
                    url=url,
                    path="README.md",
                    chunk_index=0,
                    content_hash=chunk_id,
                )
            ],
        )

    result = asyncio.run(
        ContextSearchService(store).search_context(
            "니트코드문서찾아와",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "neetcode-readme"


def test_search_context_ignores_broad_algorithm_term_for_neetcode_graph_metadata_boost(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-github-chunk",
        "source_github",
        "Other repository README",
        "Generic project documentation.",
    )
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:README.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="NeetCode Clone Graph README",
            content="Graph traversal notes.",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            platform="GitHub",
            path="README.md",
        ),
        [
            ChunkModel(
                chunk_id="neetcode-graph-readme",
                document_id=document_id,
                source_id="source_github",
                title="NeetCode Clone Graph README",
                text="Graph traversal notes.",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
                path="README.md",
                chunk_index=0,
                content_hash="neetcode-graph-readme",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-github-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "니트코드 알고리즘에서 그래프 관련 코드 알려줘",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "neetcode-graph-readme"


def test_search_context_keeps_algorithm_term_for_general_document_queries(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-github-chunk",
        "source_github",
        "Other README",
        "General project notes.",
    )
    seed_document_chunks(
        store,
        "github:eunhwa99/algorithms:README.md",
        "algorithm-readme",
        "source_github",
        "Algorithm README",
        "Algorithm study notes.",
    )

    result = asyncio.run(
        ContextSearchService(store).search_context(
            "algorithm docs",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "algorithm-readme"


def test_search_context_keeps_korean_algorithm_term_for_general_document_queries(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-github-chunk",
        "source_github",
        "Other README",
        "General project notes.",
    )
    seed_document_chunks(
        store,
        "github:eunhwa99/algorithms:README.md",
        "algorithm-readme",
        "source_github",
        "Algorithm README",
        "Algorithm study notes.",
    )

    result = asyncio.run(
        ContextSearchService(store).search_context(
            "알고리즘 문서 찾아와",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "algorithm-readme"


def test_search_context_metadata_match_boosts_low_score_vector_hit(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/ImageGallery/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-chunk",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/ImageGallery/usage.md",
                chunk_index=0,
                content_hash="imagegallery-chunk",
            )
        ],
    )

    class LowScoreMetadataMatchRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("imagegallery-chunk", 0.12)
            node.metadata["document_id"] = document_id
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr(
        "search.context_service.VectorIndexRetriever",
        LowScoreMetadataMatchRetriever,
    )

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-chunk"
    assert result["results"][0].score >= 1.0


def test_search_context_metadata_boost_survives_stale_duplicate_vector_hit(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/ImageGallery/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-chunk",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/ImageGallery/usage.md",
                chunk_index=0,
                content_hash="imagegallery-chunk",
            )
        ],
    )

    class DuplicateVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            stale = FakeNode("imagegallery-chunk", 0.99)
            stale.metadata["document_id"] = "github:eunhwa99/old:docs/usage.md"
            stale.metadata["source_id"] = "source_github"
            valid = FakeNode("imagegallery-chunk", 0.12)
            valid.metadata["document_id"] = document_id
            valid.metadata["source_id"] = "source_github"
            return [stale, valid]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", DuplicateVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-chunk"
    assert result["results"][0].score >= 1.0


def test_search_context_metadata_match_survives_stale_only_vector_hit(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/ImageGallery/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-chunk",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/ImageGallery/usage.md",
                chunk_index=0,
                content_hash="imagegallery-chunk",
            )
        ],
    )

    class StaleOnlyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            stale = FakeNode("imagegallery-chunk", 0.99)
            stale.metadata["document_id"] = "github:eunhwa99/old:docs/usage.md"
            stale.metadata["source_id"] = "source_github"
            return [stale]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", StaleOnlyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-chunk"
    assert result["results"][0].score >= 1.0


def test_vector_search_stops_expanding_after_enough_active_candidates(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/one:README.md",
        "one-chunk",
        "source_github",
        "One",
        "One project context.",
    )
    seed_document_chunks(
        store,
        "github:eunhwa99/two:README.md",
        "two-chunk",
        "source_github",
        "Two",
        "Two project context.",
    )
    requested_limits = []

    class ActiveVectorRetriever:
        def __init__(self, **kwargs):
            self.limit = kwargs["similarity_top_k"]
            requested_limits.append(self.limit)

        def retrieve(self, query):
            nodes = []
            for chunk_id, document_id in (
                ("one-chunk", "github:eunhwa99/one:README.md"),
                ("two-chunk", "github:eunhwa99/two:README.md"),
            ):
                node = FakeNode(chunk_id, 0.8)
                node.metadata["document_id"] = document_id
                node.metadata["source_id"] = "source_github"
                nodes.append(node)
            return nodes[: self.limit]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", ActiveVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "project",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert requested_limits == [2]


def test_vector_search_skips_metadata_scan_when_vector_results_are_enough(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/one:README.md",
        "one-chunk",
        "source_github",
        "One",
        "Plain context.",
    )

    class ActiveVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("one-chunk", 0.8)
            node.metadata["document_id"] = "github:eunhwa99/one:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    def fail_list_chunks(source_ids=None):
        raise AssertionError("metadata fallback should not scan chunks")

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", ActiveVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "plain context",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "one-chunk"


def test_vector_search_skips_metadata_scan_for_filtered_ordinary_query_when_vector_results_are_enough(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/one:README.md",
        "one-chunk",
        "source_github",
        "One",
        "Plain context.",
    )

    class ActiveVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("one-chunk", 0.8)
            node.metadata["document_id"] = "github:eunhwa99/one:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    def fail_list_chunks(source_ids=None):
        raise AssertionError("metadata fallback should not scan filtered chunks")

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", ActiveVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "plain context",
            filters={"source_id": "source_github"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "one-chunk"


def test_vector_search_skips_metadata_lookup_for_ordinary_long_token_when_vector_results_are_enough(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/one:README.md",
        "one-chunk",
        "source_github",
        "One",
        "Plain context.",
    )

    class ActiveVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("one-chunk", 0.8)
            node.metadata["document_id"] = "github:eunhwa99/one:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    def fail_list_chunks(source_ids=None):
        raise AssertionError("metadata fallback should not scan chunks")

    def fake_matching_terms(terms, source_ids=None, limit=200, require_document_like=False, include_text=False, require_all_terms=False):
        raise AssertionError("metadata fallback should not run for ordinary vector search")

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", ActiveVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fake_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "database",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "one-chunk"


def test_vector_search_skips_metadata_lookup_for_plain_long_word_when_vector_results_are_enough(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_document_chunks(
        store,
        "github:eunhwa99/one:README.md",
        "one-chunk",
        "source_github",
        "One",
        "Performance overview.",
    )
    seed_document_chunks(
        store,
        "notion-performance-notes",
        "notion-performance-metadata",
        "source_notion",
        "Performance",
        "Metadata-only performance notes.",
    )

    class ActiveVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("one-chunk", 0.8)
            node.metadata["document_id"] = "github:eunhwa99/one:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    def fail_list_chunks(source_ids=None):
        raise AssertionError("plain long-word query should not scan metadata")

    def fail_matching_terms(terms, source_ids=None, limit=200, require_document_like=False, include_text=False, require_all_terms=False):
        raise AssertionError("plain long-word query should not use metadata lookup")

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", ActiveVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fail_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "performance",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "one-chunk"


def test_vector_search_skips_metadata_lookup_for_unlisted_plain_long_word_when_vector_results_are_enough(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_document_chunks(
        store,
        "notion-infrastructure-vector",
        "notion-infrastructure-vector",
        "source_notion",
        "Infra",
        "Infrastructure overview.",
    )
    seed_document_chunks(
        store,
        "github:eunhwa99/infrastructure-notes:README.md",
        "github-infrastructure-metadata",
        "source_github",
        "Infrastructure notes",
        "Metadata-only infrastructure notes.",
    )

    class ActiveVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("notion-infrastructure-vector", 0.8)
            node.metadata["document_id"] = "notion-infrastructure-vector"
            node.metadata["source_id"] = "source_notion"
            return [node]

    def fail_list_chunks(source_ids=None):
        raise AssertionError("plain long-word query should not scan metadata")

    def fail_matching_terms(terms, source_ids=None, limit=200, require_document_like=False, include_text=False, require_all_terms=False):
        raise AssertionError("plain long-word query should not use metadata lookup")

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", ActiveVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fail_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "infrastructure",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-infrastructure-vector"


def test_vector_search_skips_metadata_lookup_for_plain_language_name_when_vector_results_are_enough(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_document_chunks(
        store,
        "notion-javascript-vector",
        "notion-javascript-vector",
        "source_notion",
        "Language notes",
        "JavaScript overview.",
    )
    seed_document_chunks(
        store,
        "github:eunhwa99/javascript-notes:README.md",
        "github-javascript-metadata",
        "source_github",
        "JavaScript notes",
        "Metadata-only JavaScript notes.",
    )

    class ActiveVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("notion-javascript-vector", 0.8)
            node.metadata["document_id"] = "notion-javascript-vector"
            node.metadata["source_id"] = "source_notion"
            return [node]

    def fail_list_chunks(source_ids=None):
        raise AssertionError("plain language query should not scan metadata")

    def fail_matching_terms(terms, source_ids=None, limit=200, require_document_like=False, include_text=False, require_all_terms=False):
        raise AssertionError("plain language query should not use metadata lookup")

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", ActiveVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fail_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "javascript",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-javascript-vector"


def test_vector_search_skips_metadata_lookup_for_generic_document_query_when_vector_results_are_enough(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_document_chunks(
        store,
        "notion-configuration-vector",
        "notion-configuration-vector",
        "source_notion",
        "Configuration",
        "Configuration docs overview.",
    )
    seed_document_chunks(
        store,
        "github:eunhwa99/configuration-docs:README.md",
        "github-configuration-metadata",
        "source_github",
        "Configuration docs",
        "Metadata-only configuration docs.",
    )

    class ActiveVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("notion-configuration-vector", 0.8)
            node.metadata["document_id"] = "notion-configuration-vector"
            node.metadata["source_id"] = "source_notion"
            return [node]

    def fail_list_chunks(source_ids=None):
        raise AssertionError("generic document query should not scan metadata")

    def fail_matching_terms(terms, source_ids=None, limit=200, require_document_like=False, include_text=False, require_all_terms=False):
        raise AssertionError("generic document query should not use metadata lookup")

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", ActiveVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fail_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "configuration docs",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-configuration-vector"


def test_search_context_matches_lowercase_repository_name_when_vector_window_is_full(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-github-chunk",
        "source_github",
        "Other repository README",
        "Generic project documentation.",
    )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/ImageGallery/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-lowercase-chunk",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/ImageGallery/usage.md",
                chunk_index=0,
                content_hash="imagegallery-lowercase-chunk",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-github-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "imagegallery",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-lowercase-chunk"


def test_search_context_matches_other_lowercase_repository_name_when_vector_window_is_full(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_document_chunks(
        store,
        "notion-other-vector",
        "notion-other-vector",
        "source_notion",
        "Other notes",
        "Unrelated vector result.",
    )
    document_id = "github:eunhwa99/anothergallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/anothergallery docs/usage.md",
            content="Gallery usage notes.",
            url="https://github.com/eunhwa99/anothergallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/anothergallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="anothergallery-docs",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/anothergallery docs/usage.md",
                text="Gallery usage notes.",
                url="https://github.com/eunhwa99/anothergallery/blob/main/docs/usage.md",
                path="docs/usage.md",
                chunk_index=0,
                content_hash="anothergallery-docs",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("notion-other-vector", 0.99)
            node.metadata["document_id"] = "notion-other-vector"
            node.metadata["source_id"] = "source_notion"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "anothergallery",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "anothergallery-docs"


def test_lowercase_repository_probe_uses_github_metadata_when_vector_results_are_empty(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "notion-anothergallery",
        "notion-anothergallery",
        "source_notion",
        "anothergallery planning notes",
        "Non-GitHub anothergallery notes.",
    )
    seed_document_chunks(
        store,
        "github:eunhwa99/anothergallery:README.md",
        "github-anothergallery-readme",
        "source_github",
        "eunhwa99/anothergallery README.md",
        "GitHub anothergallery docs.",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "anothergallery",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "github-anothergallery-readme"


def test_explicit_lowercase_repository_lookup_prefers_docs_before_code(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-github-chunk",
        "source_github",
        "Other repository README",
        "Generic project documentation.",
    )
    for index in range(501):
        seed_document_chunks(
            store,
            f"github:eunhwa99/ImageGallery:aaa{index:03}.java",
            f"imagegallery-lowercase-code-{index}",
            "source_github",
            f"aaa{index:03}.java",
            "class Component {}",
        )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes and layout details.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/ImageGallery/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-lowercase-docs-first",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes and layout details.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/ImageGallery/usage.md",
                chunk_index=0,
                content_hash="imagegallery-lowercase-docs-first",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-github-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "imagegallery repository docs",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-lowercase-docs-first"


def test_vector_search_uses_bounded_metadata_lookup_for_repo_docs_query(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/one:README.md",
        "one-chunk",
        "source_github",
        "One",
        "Plain context.",
    )

    class ActiveVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("one-chunk", 0.8)
            node.metadata["document_id"] = "github:eunhwa99/one:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    matching_calls = []

    def fail_list_chunks(source_ids=None):
        raise AssertionError("metadata fallback should use bounded lookup")

    def fake_matching_terms(terms, source_ids=None, limit=200, require_document_like=False, include_text=False, require_all_terms=False):
        matching_calls.append(
            {
                "terms": set(terms),
                "source_ids": source_ids,
                "limit": limit,
                "require_document_like": require_document_like,
            }
        )
        return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", ActiveVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fake_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery docs",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "one-chunk"
    assert matching_calls == [
        {
            "terms": {"imagegallery"},
            "source_ids": ["source_github"],
            "limit": 500,
            "require_document_like": True,
        }
    ]


def test_vector_search_uses_selective_terms_for_github_prefixed_repo_docs_query(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/one:README.md",
        "one-chunk",
        "source_github",
        "One",
        "Plain context.",
    )

    class ActiveVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("one-chunk", 0.8)
            node.metadata["document_id"] = "github:eunhwa99/one:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    matching_calls = []

    def fail_list_chunks(source_ids=None):
        raise AssertionError("metadata fallback should use bounded lookup")

    def fake_matching_terms(terms, source_ids=None, limit=200, require_document_like=False, include_text=False, require_all_terms=False):
        matching_calls.append(
            {
                "terms": set(terms),
                "source_ids": source_ids,
                "limit": limit,
                "require_document_like": require_document_like,
            }
        )
        return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", ActiveVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fake_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "github ImageGallery docs",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "one-chunk"
    assert matching_calls == [
        {
            "terms": {"imagegallery"},
            "source_ids": ["source_github"],
            "limit": 500,
            "require_document_like": True,
        }
    ]


def test_vector_search_uses_bounded_metadata_lookup_for_short_underscore_repo_docs_query(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/one:README.md",
        "one-chunk",
        "source_github",
        "One",
        "Plain context.",
    )

    class ActiveVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("one-chunk", 0.8)
            node.metadata["document_id"] = "github:eunhwa99/one:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    matching_calls = []

    def fail_list_chunks(source_ids=None):
        raise AssertionError("metadata fallback should use bounded lookup")

    def fake_matching_terms(terms, source_ids=None, limit=200, require_document_like=False, include_text=False, require_all_terms=False):
        matching_calls.append(
            {
                "terms": set(terms),
                "source_ids": source_ids,
                "limit": limit,
                "require_document_like": require_document_like,
            }
        )
        return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", ActiveVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fake_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "foo_bar docs",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "one-chunk"
    assert matching_calls == [
        {
            "terms": {"foo_bar"},
            "source_ids": ["source_github"],
            "limit": 500,
            "require_document_like": True,
        }
    ]


def test_vector_search_ignores_repo_request_words_for_korean_repo_query(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/one:README.md",
        "one-chunk",
        "source_github",
        "One",
        "Plain context.",
    )

    class ActiveVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("one-chunk", 0.8)
            node.metadata["document_id"] = "github:eunhwa99/one:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    matching_calls = []

    def fail_list_chunks(source_ids=None):
        raise AssertionError("metadata fallback should use bounded lookup")

    def fake_matching_terms(terms, source_ids=None, limit=200, require_document_like=False, include_text=False, require_all_terms=False):
        matching_calls.append(
            {
                "terms": set(terms),
                "source_ids": source_ids,
                "limit": limit,
                "require_document_like": require_document_like,
            }
        )
        return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", ActiveVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fake_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery라는 리포지토리 검색",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "one-chunk"
    assert matching_calls == [
        {
            "terms": {"imagegallery"},
            "source_ids": ["source_github"],
            "limit": 500,
            "require_document_like": True,
        },
        {
            "terms": {"imagegallery"},
            "source_ids": ["source_github"],
            "limit": 500,
            "require_document_like": False,
        }
    ]


def test_github_document_lookup_filters_code_rows_before_limit(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-chunk",
        "source_github",
        "Other README",
        "Unrelated docs.",
    )
    for index in range(501):
        seed_document_chunks(
            store,
            f"github:eunhwa99/ImageGallery:aaa{index:03}.java",
            f"imagegallery-code-{index}",
            "source_github",
            f"aaa{index:03}.java",
            "class Component {}",
        )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-docs-after-code",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/usage.md",
                chunk_index=0,
                content_hash="imagegallery-docs-after-code",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery docs",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-docs-after-code"


def test_anchored_topic_document_lookup_requires_topic_before_limit(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-chunk",
        "source_github",
        "Other README",
        "Unrelated docs.",
    )
    for index in range(501):
        seed_document_chunks(
            store,
            f"github:eunhwa99/neetcode-submissions-8ogaz8xl:aaa{index:03}/README.md",
            f"neetcode-generic-readme-{index}",
            "source_github",
            f"aaa{index:03} README",
            "NeetCode generic notes.",
        )
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:graph/README.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="graph README",
            content="NeetCode graph notes.",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/graph/README.md",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/graph/README.md",
            platform="GitHub",
            path="graph/README.md",
        ),
        [
            ChunkModel(
                chunk_id="neetcode-graph-readme",
                document_id=document_id,
                source_id="source_github",
                title="graph README",
                text="NeetCode graph notes.",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/graph/README.md",
                path="graph/README.md",
                chunk_index=0,
                content_hash="neetcode-graph-readme",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "니트코드 그래프 문서 찾아와",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "neetcode-graph-readme"


@pytest.mark.parametrize(
    ("query", "repo_name", "expected_chunk_id"),
    [
        ("imagegallery docs", "ImageGallery", "imagegallery-docs-lowercase"),
        ("image-gallery docs", "image-gallery", "image-gallery-docs"),
        ("foo-bar docs", "foo-bar", "foo-bar-docs"),
    ],
)
def test_unfiltered_repository_docs_lookup_filters_github_code_rows_before_limit(
    monkeypatch,
    tmp_path,
    query,
    repo_name,
    expected_chunk_id,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-chunk",
        "source_github",
        "Other README",
        "Unrelated docs.",
    )
    for index in range(501):
        seed_document_chunks(
            store,
            f"github:eunhwa99/{repo_name}:aaa{index:03}.java",
            f"{expected_chunk_id}-code-{index}",
            "source_github",
            f"aaa{index:03}.java",
            "class Component {}",
        )
    document_id = f"github:eunhwa99/{repo_name}:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title=f"eunhwa99/{repo_name} docs/usage.md",
            content="Component usage notes.",
            url=f"https://github.com/eunhwa99/{repo_name}/blob/main/docs/usage.md",
            canonical_url=f"https://github.com/eunhwa99/{repo_name}/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/usage.md",
        ),
        [
            ChunkModel(
                chunk_id=expected_chunk_id,
                document_id=document_id,
                source_id="source_github",
                title=f"eunhwa99/{repo_name} docs/usage.md",
                text="Component usage notes.",
                url=f"https://github.com/eunhwa99/{repo_name}/blob/main/docs/usage.md",
                path="docs/usage.md",
                chunk_index=0,
                content_hash=expected_chunk_id,
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            query,
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == expected_chunk_id


def test_github_document_lookup_filters_code_rows_for_mixed_source_filters(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-chunk",
        "source_github",
        "Other README",
        "Unrelated docs.",
    )
    seed_document_chunks(
        store,
        "notion-imagegallery-notes",
        "notion-imagegallery-notes",
        "source_notion",
        "ImageGallery planning notes",
        "Planning notes.",
    )
    for index in range(501):
        seed_document_chunks(
            store,
            f"github:eunhwa99/ImageGallery:aaa{index:03}.java",
            f"imagegallery-code-mixed-{index}",
            "source_github",
            f"aaa{index:03}.java",
            "class Component {}",
        )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-docs-mixed-source",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/usage.md",
                chunk_index=0,
                content_hash="imagegallery-docs-mixed-source",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery docs",
            filters={"source_ids": ["source_github", "source_notion"]},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-docs-mixed-source"


def test_repository_name_lookup_prefers_docs_before_code_without_document_intent(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-chunk",
        "source_github",
        "Other README",
        "Unrelated docs.",
    )
    for index in range(501):
        seed_document_chunks(
            store,
            f"github:eunhwa99/ImageGallery:aaa{index:03}.java",
            f"imagegallery-code-repo-only-{index}",
            "source_github",
            f"aaa{index:03}.java",
            "class Component {}",
        )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-docs-repo-only",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/usage.md",
                chunk_index=0,
                content_hash="imagegallery-docs-repo-only",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-docs-repo-only"


def test_github_document_lookup_filters_docs_named_code_rows_before_limit(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-chunk",
        "source_github",
        "Other README",
        "Unrelated docs.",
    )
    for index in range(501):
        seed_document_chunks(
            store,
            f"github:eunhwa99/ImageGallery:DocsComponent{index:03}.java",
            f"imagegallery-docs-component-{index}",
            "source_github",
            f"DocsComponent{index:03}.java",
            "class DocsComponent {}",
        )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-docs-after-docs-code",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/usage.md",
                chunk_index=0,
                content_hash="imagegallery-docs-after-docs-code",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery docs",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-docs-after-docs-code"


def test_github_document_lookup_filters_adocs_code_rows_before_limit(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-chunk",
        "source_github",
        "Other README",
        "Unrelated docs.",
    )
    for index in range(501):
        seed_document_chunks(
            store,
            f"github:eunhwa99/ImageGallery:adocs/Component{index:03}.java",
            f"imagegallery-adocs-component-{index}",
            "source_github",
            f"adocs/Component{index:03}.java",
            "class Component {}",
        )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-docs-after-adocs-code",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/usage.md",
                chunk_index=0,
                content_hash="imagegallery-docs-after-adocs-code",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery docs",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-docs-after-adocs-code"


@pytest.mark.parametrize("code_name", ["ReadmeComponent", "DocumentationComponent"])
def test_github_document_lookup_filters_readme_documentation_named_code_rows_before_limit(
    monkeypatch,
    tmp_path,
    code_name,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-chunk",
        "source_github",
        "Other README",
        "Unrelated docs.",
    )
    for index in range(501):
        seed_document_chunks(
            store,
            f"github:eunhwa99/ImageGallery:{code_name}{index:03}.java",
            f"imagegallery-{code_name.lower()}-{index}",
            "source_github",
            f"{code_name}{index:03}.java",
            f"class {code_name} {{}}",
        )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/usage.md",
        ),
        [
            ChunkModel(
                chunk_id=f"imagegallery-docs-after-{code_name.lower()}-code",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/usage.md",
                chunk_index=0,
                content_hash=f"imagegallery-docs-after-{code_name.lower()}-code",
            )
        ],
    )

    class UnrelatedVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("other-chunk", 0.99)
            node.metadata["document_id"] = "github:eunhwa99/other:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", UnrelatedVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery docs",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == f"imagegallery-docs-after-{code_name.lower()}-code"


def test_metadata_lookup_treats_underscore_terms_as_literal_text(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/fooXbar:README.md",
        "fooxbar-chunk",
        "source_github",
        "fooXbar README",
        "Wrong repository.",
    )
    seed_document_chunks(
        store,
        "github:eunhwa99/foo_bar:README.md",
        "foobar-underscore-chunk",
        "source_github",
        "foo_bar README",
        "Target repository.",
    )

    chunks = store.list_chunks_matching_metadata_terms(
        {"foo_bar"},
        ["source_github"],
        limit=1,
        require_document_like=True,
    )

    assert [chunk.chunk_id for chunk in chunks] == ["foobar-underscore-chunk"]


def test_repository_lookup_uses_metadata_fields_before_chunk_text(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    for index in range(501):
        seed_document_chunks(
            store,
            f"github:eunhwa99/aaa{index:03}:README.md",
            f"other-readme-{index}",
            "source_github",
            f"aaa{index:03} README",
            "This README compares ImageGallery alternatives.",
        )
    document_id = "github:eunhwa99/ImageGallery:docs/usage.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="eunhwa99/ImageGallery docs/usage.md",
            content="Component usage notes.",
            url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            canonical_url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
            platform="GitHub",
            path="docs/usage.md",
        ),
        [
            ChunkModel(
                chunk_id="imagegallery-docs-metadata-only",
                document_id=document_id,
                source_id="source_github",
                title="eunhwa99/ImageGallery docs/usage.md",
                text="Component usage notes.",
                url="https://github.com/eunhwa99/ImageGallery/blob/main/docs/usage.md",
                path="docs/usage.md",
                chunk_index=0,
                content_hash="imagegallery-docs-metadata-only",
            )
        ],
    )

    service = ContextSearchService(store)
    result = asyncio.run(service.search_context("ImageGallery docs", top_k=1))

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "imagegallery-docs-metadata-only"


def test_strong_github_anchor_must_match_metadata_before_body_text(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "other-neetcode-body-only",
        "source_github",
        "Other README",
        "NeetCode graph notes appear in this unrelated README body.",
    )
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:README.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="README",
            content="Graph traversal notes.",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            platform="GitHub",
            path="README.md",
        ),
        [
            ChunkModel(
                chunk_id="neetcode-readme-graph",
                document_id=document_id,
                source_id="source_github",
                title="README",
                text="Graph traversal notes.",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
                path="README.md",
                chunk_index=0,
                content_hash="neetcode-readme-graph",
            )
        ],
    )

    result = asyncio.run(
        ContextSearchService(store).search_context(
            "니트코드 그래프 문서 찾아와",
            top_k=5,
        )
    )

    chunk_ids = [item.chunk_id for item in result["results"]]
    assert chunk_ids == ["neetcode-readme-graph"]


def test_strong_github_anchor_lookup_survives_saturated_body_only_false_positives(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    for index in range(501):
        seed_document_chunks(
            store,
            f"github:eunhwa99/aaa{index:03}:README.md",
            f"other-readme-body-only-{index}",
            "source_github",
            f"aaa{index:03} README",
            "NeetCode graph notes appear in this unrelated README body.",
        )
    document_id = "github:eunhwa99/neetcode-submissions-8ogaz8xl:README.md"
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            source_id="source_github",
            title="README",
            content="Graph traversal notes.",
            url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            canonical_url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
            platform="GitHub",
            path="README.md",
        ),
        [
            ChunkModel(
                chunk_id="neetcode-readme-after-body-only-window",
                document_id=document_id,
                source_id="source_github",
                title="README",
                text="Graph traversal notes.",
                url="https://github.com/eunhwa99/neetcode-submissions-8ogaz8xl/blob/main/README.md",
                path="README.md",
                chunk_index=0,
                content_hash="neetcode-readme-after-body-only-window",
            )
        ],
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "니트코드 그래프 문서 찾아와",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "neetcode-readme-after-body-only-window"


def test_vector_search_uses_bounded_metadata_lookup_for_neetcode_docs_query(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/one:README.md",
        "one-chunk",
        "source_github",
        "One",
        "Plain context.",
    )

    class ActiveVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("one-chunk", 0.8)
            node.metadata["document_id"] = "github:eunhwa99/one:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    matching_calls = []

    def fail_list_chunks(source_ids=None):
        raise AssertionError("metadata fallback should use bounded lookup")

    def fake_matching_terms(terms, source_ids=None, limit=200, require_document_like=False, include_text=False, require_all_terms=False):
        matching_calls.append(
            {
                "terms": set(terms),
                "source_ids": source_ids,
                "limit": limit,
                "require_document_like": require_document_like,
            }
        )
        return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", ActiveVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fake_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "니트코드 문서 찾아와",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "one-chunk"
    assert matching_calls == [
        {
            "terms": {"neetcode"},
            "source_ids": ["source_github"],
            "limit": 500,
            "require_document_like": True,
        }
    ]


def test_document_intent_metadata_fallback_keeps_non_github_sources_when_unanchored(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_document_chunks(
        store,
        "notion-project-docs",
        "notion-docs-chunk",
        "source_notion",
        "Project docs",
        "Planning notes.",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "project docs",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-docs-chunk"


def test_single_document_term_metadata_fallback_keeps_non_github_sources(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_document_chunks(
        store,
        "notion-project-document",
        "notion-document-chunk",
        "source_notion",
        "Project document",
        "Planning notes.",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "document",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-document-chunk"


def test_ordinary_long_token_metadata_fallback_keeps_non_github_sources_when_vector_empty(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_document_chunks(
        store,
        "notion-architecture-notes",
        "notion-architecture-chunk",
        "source_notion",
        "Architecture notes",
        "Architecture planning notes.",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "architecture",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-architecture-chunk"


def test_plain_long_token_metadata_fallback_keeps_non_github_sources_when_vector_empty(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_document_chunks(
        store,
        "notion-performance-notes",
        "notion-performance-chunk",
        "source_notion",
        "Performance notes",
        "Performance planning notes.",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "performance",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-performance-chunk"


def test_plain_long_token_metadata_fallback_matches_chunk_body_when_vector_empty(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_document_chunks(
        store,
        "notion-runbook",
        "notion-troubleshooting-chunk",
        "source_notion",
        "Runbook",
        "Troubleshooting steps live in the body only.",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "troubleshooting",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-troubleshooting-chunk"


def test_search_context_preserves_raw_vector_score_when_metadata_fallback_promotes_chunk(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "doc-image-gallery",
        "image-gallery-chunk",
        "source_github",
        "ImageGallery guide",
        "ImageGallery component docs and usage notes.",
        path="docs/image-gallery.md",
    )
    seeded_chunk = store.get_chunk("image-gallery-chunk")
    assert seeded_chunk is not None

    class LowVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("image-gallery-chunk", 0.2)
            node.metadata["document_id"] = "doc-image-gallery"
            node.metadata["source_id"] = "source_github"
            return [node]

    def fake_matching_terms(
        terms,
        source_ids=None,
        limit=200,
        require_document_like=False,
        include_text=False,
        require_all_terms=False,
        metadata_only_terms=None,
    ):
        return [seeded_chunk]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", LowVectorRetriever)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fake_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "ImageGallery docs",
            top_k=1,
        )
    )

    assert result["results"][0].chunk_id == "image-gallery-chunk"
    assert result["results"][0].vector_score == pytest.approx(0.2)
    assert result["results"][0].score > result["results"][0].vector_score


def test_korean_only_non_github_document_query_uses_bounded_original_terms(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seeded_chunk = ChunkModel(
        chunk_id="notion-korean-algorithm-doc",
        document_id="notion-korean-algorithm-doc",
        source_id="source_notion",
        title="알고리즘 문서",
        text="알고리즘 정리 내용입니다.",
        chunk_index=0,
        content_hash="notion-korean-algorithm-doc",
    )
    seed_document_chunks(
        store,
        "notion-korean-algorithm-doc",
        "notion-korean-algorithm-doc",
        "source_notion",
        "알고리즘 문서",
        "알고리즘 정리 내용입니다.",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    matching_calls = []

    def fail_list_chunks(source_ids=None):
        raise AssertionError("Korean ordinary fallback should use bounded lookup")

    def fake_matching_terms(
        terms,
        source_ids=None,
        limit=200,
        require_document_like=False,
        include_text=False,
        require_all_terms=False,
    ):
        matching_calls.append(
            {
                "terms": set(terms),
                "source_ids": source_ids,
                "include_text": include_text,
            }
        )
        assert "알고리즘" in set(terms)
        assert {"algorithm", "algorithms"}.issubset(set(terms))
        assert include_text is True
        return [seeded_chunk]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fake_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "알고리즘 문서",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-korean-algorithm-doc"
    assert matching_calls


@pytest.mark.parametrize(
    ("query", "title", "text", "expected_chunk_id"),
    [
        ("monitoring", "Monitoring guide", "Operational telemetry notes.", "notion-monitoring"),
        ("authorization", "Access guide", "Authorization policy notes.", "notion-authorization"),
    ],
)
def test_ordinary_technical_terms_keep_non_github_fallback_when_vector_empty(
    monkeypatch,
    tmp_path,
    query,
    title,
    text,
    expected_chunk_id,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        f"notion-{query}",
        expected_chunk_id,
        "source_notion",
        title,
        text,
    )
    seed_document_chunks(
        store,
        "github:eunhwa99/unrelated:README.md",
        "github-unrelated-readme",
        "source_github",
        "Unrelated README",
        "GitHub unrelated notes.",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            query,
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == expected_chunk_id


@pytest.mark.parametrize(
    ("query", "source_id", "title", "text", "expected_chunk_id"),
    [
        (
            "typescript",
            "source_notion",
            "TypeScript notes",
            "TypeScript planning notes.",
            "notion-typescript",
        ),
        (
            "postgresql",
            "source_tistory",
            "PostgreSQL tuning",
            "PostgreSQL index notes.",
            "tistory-postgresql",
        ),
        (
            "typescript docs",
            "source_notion",
            "TypeScript docs",
            "TypeScript API reference.",
            "notion-typescript-docs",
        ),
    ],
)
def test_plain_lowercase_topic_terms_recover_non_github_metadata_when_vector_empty(
    monkeypatch,
    tmp_path,
    query,
    source_id,
    title,
    text,
    expected_chunk_id,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_source(store, "source_tistory", SourceType.TISTORY, "Tistory")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        expected_chunk_id,
        expected_chunk_id,
        source_id,
        title,
        text,
    )
    seed_document_chunks(
        store,
        "github:eunhwa99/unrelated:README.md",
        "github-unrelated-readme",
        "source_github",
        "Unrelated README",
        "GitHub unrelated notes.",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            query,
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == expected_chunk_id


def test_plain_lowercase_topic_merges_non_github_body_match_with_github_metadata(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/typescript-notes:README.md",
        "github-typescript-readme",
        "source_github",
        "TypeScript README",
        "Repository overview.",
    )
    seed_document_chunks(
        store,
        "github:eunhwa99/typescript-notes:src/example.ts",
        "github-typescript-code",
        "source_github",
        "src/example.ts",
        "export const example = true;",
    )
    seed_document_chunks(
        store,
        "notion-language-notes",
        "notion-typescript-body",
        "source_notion",
        "Language notes",
        "The team uses TypeScript for frontend architecture.",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "typescript",
            top_k=2,
        )
    )

    chunk_ids = [result.chunk_id for result in result["results"]]
    assert "notion-typescript-body" in chunk_ids
    if "github-typescript-code" in chunk_ids:
        assert chunk_ids.index("notion-typescript-body") < chunk_ids.index("github-typescript-code")


def test_source_filtered_non_github_lookup_matches_body_only_lowercase_term(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_document_chunks(
        store,
        "notion-language-body",
        "notion-language-filtered-body",
        "source_notion",
        "Language notes",
        "TypeScript appears only in the body text.",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "typescript",
            filters={"source_id": "source_notion"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-language-filtered-body"


def test_plain_lowercase_topic_keeps_sufficient_lexical_vector_hit_over_github_metadata(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "notion-typescript-vector",
        "notion-typescript-vector",
        "source_notion",
        "TypeScript notes",
        "TypeScript planning notes.",
    )
    seed_document_chunks(
        store,
        "github:eunhwa99/typescript:README.md",
        "github-typescript-readme",
        "source_github",
        "TypeScript README",
        "Repository overview.",
    )

    class ActiveVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("notion-typescript-vector", 0.8)
            node.metadata["document_id"] = "notion-typescript-vector"
            node.metadata["source_id"] = "source_notion"
            return [node]

    def fail_list_chunks(source_ids=None):
        raise AssertionError("sufficient vector results should not trigger broad metadata fallback")

    def fake_matching_terms(
        terms,
        source_ids=None,
        limit=200,
        require_document_like=False,
        include_text=False,
        require_all_terms=False,
    ):
        raise AssertionError("sufficient lexical vector results should not use GitHub metadata")

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", ActiveVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fake_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "typescript",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-typescript-vector"


def test_plain_lowercase_topic_recovers_non_github_body_when_vector_window_is_irrelevant(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "notion-unrelated-vector",
        "notion-unrelated-vector",
        "source_notion",
        "Roadmap notes",
        "Planning notes without the requested language term.",
    )
    seed_document_chunks(
        store,
        "notion-typescript-body",
        "notion-typescript-body",
        "source_notion",
        "Language notes",
        "The frontend architecture uses TypeScript in key modules.",
    )

    class IrrelevantVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("notion-unrelated-vector", 0.9)
            node.metadata["document_id"] = "notion-unrelated-vector"
            node.metadata["source_id"] = "source_notion"
            return [node]

    def fail_list_chunks(source_ids=None):
        raise AssertionError("lowercase full-window recovery should use bounded lookup")

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", IrrelevantVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "typescript",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-typescript-body"


@pytest.mark.parametrize("query", ["api/v1", "api/v1 docs"])
def test_api_path_queries_recover_non_github_body_when_vector_empty(
    monkeypatch,
    tmp_path,
    query,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seeded_chunk = ChunkModel(
        chunk_id="notion-api-v1-body",
        document_id="notion-api-v1-body",
        source_id="source_notion",
        title="API notes",
        text="The api/v1 endpoint behavior is documented here.",
        chunk_index=0,
        content_hash="notion-api-v1-body",
    )
    seed_document_chunks(
        store,
        "notion-api-v1-body",
        "notion-api-v1-body",
        "source_notion",
        "API notes",
        "The api/v1 endpoint behavior is documented here.",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    def fail_list_chunks(source_ids=None):
        raise AssertionError("api path fallback should use bounded text lookup")

    def fake_matching_terms(
        terms,
        source_ids=None,
        limit=200,
        require_document_like=False,
        include_text=False,
        require_all_terms=False,
    ):
        assert set(terms) == {"api/v1"}
        assert source_ids is None
        assert include_text is True
        return [seeded_chunk]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fake_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            query,
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-api-v1-body"


@pytest.mark.parametrize("query", ["docs", "문서 찾아와"])
def test_document_intent_only_queries_do_not_use_unbounded_chunk_scan(
    monkeypatch,
    tmp_path,
    query,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    matching_calls = []

    def fail_list_chunks(source_ids=None):
        raise AssertionError("document-intent fallback should not call list_chunks")

    def fake_matching_terms(
        terms,
        source_ids=None,
        limit=200,
        require_document_like=False,
        include_text=False,
        require_all_terms=False,
    ):
        matching_calls.append(
            {
                "terms": set(terms),
                "source_ids": source_ids,
                "require_document_like": require_document_like,
            }
        )
        return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fake_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            query,
            top_k=1,
        )
    )

    assert result["results"] == []
    assert matching_calls
    assert all(call["require_document_like"] is True for call in matching_calls)


def test_mixed_github_and_notion_filter_keeps_non_github_body_text_lookup(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    notion_chunk = ChunkModel(
        chunk_id="notion-typescript-mixed-body",
        document_id="notion-typescript-mixed-body",
        source_id="source_notion",
        title="Language notes",
        text="TypeScript appears only in the Notion body.",
        chunk_index=0,
        content_hash="notion-typescript-mixed-body",
    )
    seed_document_chunks(
        store,
        "notion-typescript-mixed-body",
        "notion-typescript-mixed-body",
        "source_notion",
        "Language notes",
        "TypeScript appears only in the Notion body.",
    )
    seed_document_chunks(
        store,
        "github:eunhwa99/other:README.md",
        "github-other-readme",
        "source_github",
        "Other README",
        "Repository overview.",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    matching_calls = []

    def fake_matching_terms(
        terms,
        source_ids=None,
        limit=200,
        require_document_like=False,
        include_text=False,
        require_all_terms=False,
    ):
        matching_calls.append(
            {
                "terms": set(terms),
                "source_ids": source_ids,
                "include_text": include_text,
            }
        )
        if source_ids == ["source_notion"] and include_text:
            return [notion_chunk]
        return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fake_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "typescript",
            filters={"source_ids": ["source_github", "source_notion"]},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-typescript-mixed-body"
    assert {
        "terms": {"typescript"},
        "source_ids": ["source_github"],
        "include_text": False,
    } in matching_calls
    assert {
        "terms": {"typescript"},
        "source_ids": ["source_notion"],
        "include_text": True,
    } in matching_calls


def test_unlisted_technical_term_skips_metadata_lookup_when_vector_results_are_enough(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "notion-scalability-vector",
        "notion-scalability-vector",
        "source_notion",
        "Scalability notes",
        "Scalability planning notes.",
    )
    seed_document_chunks(
        store,
        "github:eunhwa99/scalability:README.md",
        "github-scalability-metadata",
        "source_github",
        "Scalability README",
        "Metadata-only scalability notes.",
    )

    class ActiveVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("notion-scalability-vector", 0.8)
            node.metadata["document_id"] = "notion-scalability-vector"
            node.metadata["source_id"] = "source_notion"
            return [node]

    def fail_list_chunks(source_ids=None):
        raise AssertionError("ordinary technical query should not scan metadata")

    def fail_matching_terms(
        terms,
        source_ids=None,
        limit=200,
        require_document_like=False,
        include_text=False,
        require_all_terms=False,
    ):
        raise AssertionError("ordinary technical query should not use metadata lookup")

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", ActiveVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fail_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "scalability",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-scalability-vector"


def test_generic_long_word_docs_query_keeps_non_github_sources_when_vector_empty(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_document_chunks(
        store,
        "notion-configuration-docs",
        "notion-configuration-docs",
        "source_notion",
        "Configuration docs",
        "Configuration reference.",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "configuration docs",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-configuration-docs"


def test_hyphenated_topic_docs_query_matches_body_when_vector_empty(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seed_document_chunks(
        store,
        "notion-security-guide",
        "notion-zero-trust-body",
        "source_notion",
        "Security guide",
        "Zero-trust rollout notes live in the body only.",
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "zero-trust docs",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "notion-zero-trust-body"


@pytest.mark.parametrize(
    ("query", "title", "text", "expected_terms", "expected_chunk_id"),
    [
        (
            "project structure",
            "Project structure",
            "Repository layout notes.",
            {"project", "structure"},
            "notion-project-structure",
        ),
        (
            "plain context",
            "Plain notes",
            "Plain context lives in the body.",
            {"plain", "context"},
            "notion-plain-context",
        ),
        (
            "configuration docs",
            "Configuration docs",
            "Configuration reference.",
            {"configuration"},
            "notion-configuration-docs-bounded",
        ),
    ],
)
def test_vector_empty_ordinary_queries_use_bounded_metadata_lookup(
    monkeypatch,
    tmp_path,
    query,
    title,
    text,
    expected_terms,
    expected_chunk_id,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_notion", SourceType.NOTION, "Notion")
    seeded_chunk = ChunkModel(
        chunk_id=expected_chunk_id,
        document_id=expected_chunk_id,
        source_id="source_notion",
        title=title,
        text=text,
        chunk_index=0,
        content_hash=expected_chunk_id,
    )
    seed_document_chunks(
        store,
        expected_chunk_id,
        expected_chunk_id,
        "source_notion",
        title,
        text,
    )

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    matching_calls = []

    def fail_list_chunks(source_ids=None):
        raise AssertionError("ordinary vector-empty fallback should use bounded lookup")

    def fake_matching_terms(
        terms,
        source_ids=None,
        limit=200,
        require_document_like=False,
        include_text=False,
        require_all_terms=False,
    ):
        matching_calls.append(
            {
                "terms": set(terms),
                "source_ids": source_ids,
                "include_text": include_text,
                "require_all_terms": require_all_terms,
            }
        )
        assert set(terms) == expected_terms
        assert include_text is True
        return [seeded_chunk]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fake_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            query,
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == expected_chunk_id
    assert matching_calls


def test_hyphenated_ordinary_query_skips_metadata_scan_when_vector_results_are_enough(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "github:eunhwa99/one:README.md",
        "one-chunk",
        "source_github",
        "One",
        "Read-only retrieval notes.",
    )

    class ActiveVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("one-chunk", 0.8)
            node.metadata["document_id"] = "github:eunhwa99/one:README.md"
            node.metadata["source_id"] = "source_github"
            return [node]

    def fail_list_chunks(source_ids=None):
        raise AssertionError("ordinary hyphenated query should not scan metadata")

    def fail_matching_terms(terms, source_ids=None, limit=200, require_document_like=False, include_text=False, require_all_terms=False):
        raise AssertionError("ordinary hyphenated query should not use metadata lookup")

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", ActiveVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fail_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            "read-only retrieval",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "one-chunk"


@pytest.mark.parametrize("query", ["찾아와", "search for", "please show me"])
def test_stop_word_only_queries_skip_metadata_fallback_scan(
    monkeypatch,
    tmp_path,
    query,
):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")

    class EmptyVectorRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            return []

    def fail_list_chunks(source_ids=None):
        raise AssertionError("stop-word-only query should not scan metadata")

    def fail_matching_terms(
        terms,
        source_ids=None,
        limit=200,
        require_document_like=False,
        include_text=False,
        require_all_terms=False,
    ):
        raise AssertionError("stop-word-only query has no bounded metadata terms")

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", EmptyVectorRetriever)
    monkeypatch.setattr(store, "list_chunks", fail_list_chunks)
    monkeypatch.setattr(store, "list_chunks_matching_metadata_terms", fail_matching_terms)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context(
            query,
            top_k=1,
        )
    )

    assert result["results"] == []


def test_answer_with_citations_respects_singular_source_id_filter(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_source(store, "source_other", SourceType.TISTORY, "Other")
    seed_document_chunks(
        store,
        "doc-target",
        "target-chunk",
        "source_target",
        "Target",
        "Target source says ContextZip answers with citations.",
    )
    seed_document_chunks(
        store,
        "doc-other",
        "other-chunk",
        "source_other",
        "Other",
        "Other source also mentions ContextZip citations.",
    )
    documents = [
        store.get_chunk("other-chunk").to_document_model(),
        store.get_chunk("target-chunk").to_document_model(),
    ]
    context_search = ContextSearchService(store, retriever=documents)
    answer_service = CitationAnswerService(context_search, min_score=0.1, min_results=1)

    answer = asyncio.run(
        answer_service.answer_with_citations(
            "How does ContextZip answer?",
            filters={"source_id": "source_target"},
            top_k=1,
        )
    )

    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == ["target-chunk"]
    assert answer["citations"][0]["chunk_id"] == "target-chunk"


def test_search_context_default_source_ids_filter_legacy_removed_sources(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_legacy_web_source(store)
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "legacy-doc",
        "legacy-chunk",
        "source_web",
        "Legacy Web",
        "Legacy web content should not be public.",
    )
    seed_document_chunks(
        store,
        "github-doc",
        "github-chunk",
        "source_github",
        "GitHub Doc",
        "Retained GitHub content should remain searchable.",
    )
    service = ContextSearchService(
        store,
        retriever=list_search_documents(store),
        default_source_ids=["source_github", "source_notion", "source_tistory"],
    )

    legacy_result = asyncio.run(service.search_context("legacy web", top_k=5))
    retained_result = asyncio.run(service.search_context("retained github", top_k=5))
    explicit_legacy_result = asyncio.run(
        service.search_context(
            "legacy web",
            filters={"source_id": "source_web"},
            top_k=5,
        )
    )

    assert legacy_result["results"] == []
    assert [item.chunk_id for item in retained_result["results"]] == ["github-chunk"]
    assert explicit_legacy_result["results"] == []


def test_search_context_ignores_tombstoned_document_chunks(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    seed_source(store, "source_target", SourceType.GITHUB, "Target")
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id="doc-target",
            document_id="doc-target",
            source_id="source_target",
            title="Target",
            content="ContextZip stale deleted content",
            url="https://example.com/doc-target",
            platform="GitHub",
            path="doc-target.md",
        ),
        [
            ChunkModel(
                chunk_id="target-chunk",
                document_id="doc-target",
                source_id="source_target",
                title="Target",
                text="ContextZip stale deleted content",
                chunk_index=0,
                content_hash="target",
            )
        ],
    )
    job, started = store.begin_sync_job("source_target")
    assert started is True
    store.complete_successful_sync(
        job_id=job.job_id,
        source_id="source_target",
        total_documents=0,
        processed_documents=0,
        indexed_chunks=0,
        skipped_documents=0,
        last_seen_at="2026-05-22T00:00:00Z",
        cleanup_missing_documents=True,
        deleted_at="2026-05-22T00:01:00Z",
    )

    result = asyncio.run(
        ContextSearchService(
            store,
            retriever=[ChunkModel(
                chunk_id="target-chunk",
                document_id="doc-target",
                source_id="source_target",
                title="Target",
                text="ContextZip stale deleted content",
                chunk_index=0,
                content_hash="target",
            ).to_document_model()],
        ).search_context("ContextZip", top_k=1)
    )

    assert result["results"] == []


async def async_search(service):
    return await service.search_context(
        "context",
        filters={"source_ids": ["source_target"]},
        top_k=1,
    )


def seed_source(store, source_id, source_type, name):
    store.upsert_source(
        SourceModel(
            source_id=source_id,
            source_type=source_type,
            name=name,
            sync_status=SyncStatus.IDLE,
        )
    )


def seed_legacy_web_source(store):
    store.ensure_schema()
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO sources (
                source_id, source_type, name, enabled, auth_ref, sync_status,
                last_synced_at, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "source_web",
                "web",
                "Legacy Web",
                1,
                "",
                SyncStatus.SUCCEEDED.value,
                "2026-06-10T00:00:00+00:00",
                "",
                "2026-06-10T00:00:00+00:00",
                "2026-06-10T00:00:00+00:00",
            ),
        )


def seed_document_chunks(
    store,
    document_id,
    chunk_id,
    source_id,
    title,
    text,
    *,
    version_id="",
    path="",
    url="",
    published_at="",
):
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            source_id=source_id,
            title=title,
            content=text,
            url=url or f"https://example.com/{document_id}",
            platform="Test",
            path=path or title,
            version_id=version_id,
            published_at=published_at,
            modified_at=published_at,
            indexed_at=published_at,
            date_provenance="test" if published_at else "",
        ),
        [
            ChunkModel(
                chunk_id=chunk_id,
                document_id=document_id,
                source_id=source_id,
                title=title,
                text=text,
                url=url or f"https://example.com/{document_id}",
                path=path or title,
                chunk_index=0,
                content_hash=chunk_id,
                version_id=version_id,
            )
        ],
    )


def list_search_documents(store):
    return [
        chunk.to_document_model(platform="Test")
        for chunk in store.list_chunks()
    ]
