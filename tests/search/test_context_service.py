import asyncio

import pytest

from core.models import ChunkModel, SourceModel, SourceType, SyncStatus
from search.answer_service import CitationAnswerService
from search.context_service import ContextSearchService
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.integration


class FakeNode:
    def __init__(self, chunk_id, score):
        self.metadata = {"chunk_id": chunk_id}
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
    store.replace_document_chunks(
        "doc-target",
        [
            ChunkModel(
                chunk_id="target-chunk",
                document_id="doc-target",
                source_id="source_target",
                title="Target",
                text="target context",
                chunk_index=0,
                content_hash="target",
            )
        ],
    )
    store.replace_document_chunks(
        "doc-other",
        [
            ChunkModel(
                chunk_id="other-chunk",
                document_id="doc-other",
                source_id="source_other",
                title="Other",
                text="other context",
                chunk_index=0,
                content_hash="other",
            )
        ],
    )

    class FakeVectorIndexRetriever:
        captured_filters = None

        def __init__(self, **kwargs):
            FakeVectorIndexRetriever.captured_filters = kwargs.get("filters")

        def retrieve(self, query):
            if FakeVectorIndexRetriever.captured_filters is None:
                return [FakeNode("other-chunk", 0.99)]
            return [FakeNode("target-chunk", 0.88)]

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
    store.replace_document_chunks(
        "doc-target",
        [
            ChunkModel(
                chunk_id="target-chunk",
                document_id="doc-target",
                source_id="source_target",
                title="Target",
                text="target context",
                chunk_index=0,
                content_hash="target",
            )
        ],
    )

    class FakeVectorIndexRetriever:
        captured_filters = None

        def __init__(self, **kwargs):
            FakeVectorIndexRetriever.captured_filters = kwargs.get("filters")

        def retrieve(self, query):
            if "contextwiki_managed" not in str(FakeVectorIndexRetriever.captured_filters):
                return [FakeNode("legacy-0", 0.99)]
            return [FakeNode("target-chunk", 0.5)]

    monkeypatch.setattr("search.context_service.VectorIndexRetriever", FakeVectorIndexRetriever)

    result = asyncio.run(
        ContextSearchService(store, indexer=FakeIndexer()).search_context("context", top_k=1)
    )

    assert len(result["results"]) == 1
    assert result["results"][0].chunk_id == "target-chunk"
    assert "contextwiki_managed" in str(FakeVectorIndexRetriever.captured_filters)


def test_search_context_accepts_singular_source_id_filter(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_source(store, "source_other", SourceType.TISTORY, "Other")
    store.replace_document_chunks(
        "doc-target",
        [
            ChunkModel(
                chunk_id="target-chunk",
                document_id="doc-target",
                source_id="source_target",
                title="Target",
                text="ContextWiki citations target",
                chunk_index=0,
                content_hash="target",
            )
        ],
    )
    store.replace_document_chunks(
        "doc-other",
        [
            ChunkModel(
                chunk_id="other-chunk",
                document_id="doc-other",
                source_id="source_other",
                title="Other",
                text="ContextWiki citations other",
                chunk_index=0,
                content_hash="other",
            )
        ],
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


def test_answer_with_citations_respects_singular_source_id_filter(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    seed_source(store, "source_target", SourceType.NOTION, "Target")
    seed_source(store, "source_other", SourceType.TISTORY, "Other")
    store.replace_document_chunks(
        "doc-target",
        [
            ChunkModel(
                chunk_id="target-chunk",
                document_id="doc-target",
                source_id="source_target",
                title="Target",
                text="Target source says ContextWiki answers with citations.",
                chunk_index=0,
                content_hash="target",
            )
        ],
    )
    store.replace_document_chunks(
        "doc-other",
        [
            ChunkModel(
                chunk_id="other-chunk",
                document_id="doc-other",
                source_id="source_other",
                title="Other",
                text="Other source also mentions ContextWiki citations.",
                chunk_index=0,
                content_hash="other",
            )
        ],
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
