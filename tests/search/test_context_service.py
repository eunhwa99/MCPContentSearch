import asyncio

import pytest

from core.models import ChunkModel, DocumentModel, SourceModel, SourceType, SyncStatus
from search.answer_service import CitationAnswerService
from search.context_service import ContextSearchService
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.integration


class FakeNode:
    def __init__(self, chunk_id, score):
        self.metadata = {"chunk_id": chunk_id, "contextwiki_managed": "true"}
        self.score = score


class FakeIndexer:
    def get_or_create_index(self):
        return object()


def test_vector_search_pushes_source_filter_into_retriever(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    assert "contextwiki_managed" in str(FakeVectorIndexRetriever.captured_filters)
    assert "source_id" in str(FakeVectorIndexRetriever.captured_filters)
    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "target-chunk"


def test_vector_search_filters_to_contextwiki_managed_chunks_by_default(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
            if "contextwiki_managed" not in str(FakeVectorIndexRetriever.captured_filters):
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
    assert "contextwiki_managed" in str(FakeVectorIndexRetriever.captured_filters)


def test_vector_search_expands_past_stale_managed_window(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_document_chunks(store, "doc-target", "target-chunk", "source_target", "Target", "target context")

    class FakeVectorIndexRetriever:
        def __init__(self, **kwargs):
            pass

        def retrieve(self, query):
            node = FakeNode("target-chunk", 0.99)
            node.metadata.pop("contextwiki_managed")
            node.metadata["document_id"] = "doc-target"
            node.metadata["source_id"] = "source_target"
            return [node]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context("context", top_k=1)
    )

    assert result["results"] == []


def test_vector_search_keeps_looking_after_rejected_duplicate_managed_hit(monkeypatch, tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
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
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_source(store, "source_other", SourceType.TISTORY, "Other")
    seed_document_chunks(
        store,
        "doc-target",
        "target-chunk",
        "source_target",
        "Target",
        "ContextWiki citations target",
    )
    seed_document_chunks(
        store,
        "doc-other",
        "other-chunk",
        "source_other",
        "Other",
        "ContextWiki citations other",
    )
    documents = [
        store.get_chunk("other-chunk").to_document_model(),
        store.get_chunk("target-chunk").to_document_model(),
    ]

    result = asyncio.run(
        ContextSearchService(store, retriever=documents).search_context(
            "ContextWiki citations",
            filters={"source_id": "source_target"},
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].source_id == "source_target"


def test_search_context_returns_chunk_version_id(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    seed_source(store, "source_github", SourceType.GITHUB, "GitHub")
    seed_document_chunks(
        store,
        "doc-github",
        "github-chunk",
        "source_github",
        "README.md",
        "ContextWiki citations include blob versions.",
        version_id="blob-version-123",
    )
    documents = [store.get_chunk("github-chunk").to_document_model()]

    result = asyncio.run(
        ContextSearchService(store, retriever=documents).search_context(
            "ContextWiki citations",
            top_k=1,
        )
    )

    assert len(result["results"]) == 1
    assert result["results"][0].version_id == "blob-version-123"


def test_answer_with_citations_respects_singular_source_id_filter(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_source(store, "source_other", SourceType.TISTORY, "Other")
    seed_document_chunks(
        store,
        "doc-target",
        "target-chunk",
        "source_target",
        "Target",
        "Target source says ContextWiki answers with citations.",
    )
    seed_document_chunks(
        store,
        "doc-other",
        "other-chunk",
        "source_other",
        "Other",
        "Other source also mentions ContextWiki citations.",
    )
    documents = [
        store.get_chunk("other-chunk").to_document_model(),
        store.get_chunk("target-chunk").to_document_model(),
    ]
    context_search = ContextSearchService(store, retriever=documents)
    answer_service = CitationAnswerService(context_search, min_score=0.1, min_results=1)

    answer = asyncio.run(
        answer_service.answer_with_citations(
            "How does ContextWiki answer?",
            filters={"source_id": "source_target"},
            top_k=1,
        )
    )

    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == ["target-chunk"]
    assert answer["citations"][0]["chunk_id"] == "target-chunk"


def test_search_context_ignores_tombstoned_document_chunks(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    seed_source(store, "source_target", SourceType.GITHUB, "Target")
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id="doc-target",
            document_id="doc-target",
            source_id="source_target",
            title="Target",
            content="ContextWiki stale deleted content",
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
                text="ContextWiki stale deleted content",
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
                text="ContextWiki stale deleted content",
                chunk_index=0,
                content_hash="target",
            ).to_document_model()],
        ).search_context("ContextWiki", top_k=1)
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


def seed_document_chunks(
    store,
    document_id,
    chunk_id,
    source_id,
    title,
    text,
    *,
    version_id="",
):
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            source_id=source_id,
            title=title,
            content=text,
            url=f"https://example.com/{document_id}",
            platform="Test",
            path=title,
            version_id=version_id,
        ),
        [
            ChunkModel(
                chunk_id=chunk_id,
                document_id=document_id,
                source_id=source_id,
                title=title,
                text=text,
                chunk_index=0,
                content_hash=chunk_id,
                version_id=version_id,
            )
        ],
    )
