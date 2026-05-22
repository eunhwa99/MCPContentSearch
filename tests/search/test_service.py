import asyncio

import pytest

from core.models import ChunkModel, DocumentModel, SourceModel, SourceType, SyncStatus
from environments.config import AppConfig
from search.service import SearchService
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.unit


class FakeNode:
    def __init__(self, metadata, text="deleted managed chunk", score=0.9):
        self.metadata = metadata
        self.text = text
        self.score = score


class FakeMetadataStore:
    def __init__(self, chunks=None, documents=None, chunk_records=None):
        self.chunks = chunks or {}
        self.documents = documents or {}
        self.chunk_records = set(chunk_records or self.chunks)

    def get_chunk(self, chunk_id):
        return self.chunks.get(chunk_id)

    def get_document(self, document_id):
        return self.documents.get(document_id)

    def get_document_by_url(self, url):
        for document in self.documents.values():
            if document.canonical_url == url or document.url == url:
                return document
        return None

    def has_chunk_record(self, chunk_id):
        return chunk_id in self.chunk_records


class FakeIndexer:
    def get_or_create_index(self):
        return object()


def test_legacy_search_hides_tombstoned_managed_chunks():
    service = SearchService(
        AppConfig(preview_length=80),
        indexer=None,
        metadata_store=FakeMetadataStore(),
    )
    node = FakeNode(
        {
            "contextwiki_managed": "true",
            "chunk_id": "deleted-chunk",
            "title": "Deleted",
            "url": "https://example.com/deleted",
            "platform": "GitHub",
        }
    )

    markdown = service._format_results("deleted", [node], 10)

    assert markdown == "No results found for 'deleted'"


def test_legacy_search_hides_managed_chunks_without_metadata_store():
    service = SearchService(AppConfig(preview_length=80), indexer=None, metadata_store=None)
    node = FakeNode(
        {
            "contextwiki_managed": "true",
            "chunk_id": "managed-chunk",
            "title": "Managed",
            "url": "https://example.com/managed",
            "platform": "GitHub",
        }
    )

    markdown = service._format_results("managed", [node], 10)

    assert markdown == "No results found for 'managed'"


def test_legacy_search_hydrates_active_managed_chunks_from_sqlite():
    chunk = ChunkModel(
        chunk_id="active-chunk",
        document_id="doc-1",
        source_id="source_fake",
        title="Fresh Title",
        text="Fresh chunk text from SQLite",
        url="https://example.com/fresh",
        path="fresh.md",
        chunk_index=0,
        line_start=10,
        line_end=12,
        content_hash="hash",
        updated_at="2026-05-22T05:00:00Z",
    )
    service = SearchService(
        AppConfig(preview_length=80),
        indexer=None,
        metadata_store=FakeMetadataStore({"active-chunk": chunk}),
    )
    node = FakeNode(
        {
            "contextwiki_managed": "true",
            "chunk_id": "active-chunk",
            "document_id": "doc-1",
            "source_id": "source_fake",
            "title": "Stale Title",
            "url": "https://example.com/stale",
            "platform": "StalePlatform",
            "date": "1999-01-01",
        },
        text="stale vector text",
    )

    markdown = service._format_results("fresh", [node], 10)

    assert "Fresh Title" in markdown
    assert "https://example.com/fresh" in markdown
    assert "Fresh chunk text from SQLite" in markdown
    assert "Stale Title" not in markdown
    assert "https://example.com/stale" not in markdown
    assert "source_fake" in markdown
    assert "2026-05-22T05:00:00Z" in markdown
    assert "StalePlatform" not in markdown
    assert "1999-01-01" not in markdown


def test_legacy_search_rejects_managed_vector_with_mismatched_owner():
    chunk = ChunkModel(
        chunk_id="active-chunk",
        document_id="doc-1",
        source_id="source_fake",
        title="Fresh Title",
        text="Fresh chunk text from SQLite",
        url="https://example.com/fresh",
        path="fresh.md",
        chunk_index=0,
        content_hash="hash",
    )
    service = SearchService(
        AppConfig(preview_length=80),
        indexer=None,
        metadata_store=FakeMetadataStore({"active-chunk": chunk}),
    )
    node = FakeNode(
        {
            "contextwiki_managed": "true",
            "chunk_id": "active-chunk",
            "document_id": "doc-1",
            "source_id": "source_other",
        },
        text="stale vector text",
    )

    markdown = service._format_results("fresh", [node], 10)

    assert markdown == "No results found for 'fresh'"


def test_legacy_search_rejects_managed_vector_missing_owner_metadata():
    chunk = ChunkModel(
        chunk_id="active-chunk",
        document_id="doc-1",
        source_id="source_fake",
        title="Fresh Title",
        text="Fresh chunk text from SQLite",
        url="https://example.com/fresh",
        path="fresh.md",
        chunk_index=0,
        content_hash="hash",
    )
    service = SearchService(
        AppConfig(preview_length=80),
        indexer=None,
        metadata_store=FakeMetadataStore({"active-chunk": chunk}),
    )
    node = FakeNode(
        {
            "contextwiki_managed": "true",
            "chunk_id": "active-chunk",
        },
        text="stale vector text",
    )

    markdown = service._format_results("fresh", [node], 10)

    assert markdown == "No results found for 'fresh'"


def test_legacy_search_rejects_managed_vector_missing_managed_marker():
    chunk = ChunkModel(
        chunk_id="active-chunk",
        document_id="doc-1",
        source_id="source_fake",
        title="Fresh Title",
        text="Fresh chunk text from SQLite",
        url="https://example.com/fresh",
        path="fresh.md",
        chunk_index=0,
        content_hash="hash",
    )
    service = SearchService(
        AppConfig(preview_length=80),
        indexer=None,
        metadata_store=FakeMetadataStore({"active-chunk": chunk}),
    )
    node = FakeNode(
        {
            "chunk_id": "active-chunk",
            "document_id": "doc-1",
            "source_id": "source_fake",
        },
        text="stale vector text",
    )

    markdown = service._format_results("fresh", [node], 10)

    assert markdown == "No results found for 'fresh'"


def test_legacy_search_rejects_markerless_vector_with_doc_id_chunk_id():
    chunk = ChunkModel(
        chunk_id="active-chunk",
        document_id="doc-1",
        source_id="source_fake",
        title="Fresh Title",
        text="Fresh chunk text from SQLite",
        url="https://example.com/fresh",
        path="fresh.md",
        chunk_index=0,
        content_hash="hash",
    )
    service = SearchService(
        AppConfig(preview_length=80),
        indexer=None,
        metadata_store=FakeMetadataStore({"active-chunk": chunk}),
    )
    node = FakeNode(
        {
            "doc_id": "active-chunk",
            "title": "Stale markerless raw",
            "url": "https://example.com/stale",
            "platform": "GitHub",
        },
        text="stale vector text",
    )

    markdown = service._format_results("fresh", [node], 10)

    assert markdown == "No results found for 'fresh'"


def test_legacy_search_rejects_markerless_vector_with_tombstoned_chunk_id():
    service = SearchService(
        AppConfig(preview_length=80),
        indexer=None,
        metadata_store=FakeMetadataStore(chunk_records={"deleted-chunk"}),
    )
    node = FakeNode(
        {
            "doc_id": "deleted-chunk",
            "title": "Deleted markerless raw",
            "url": "https://example.com/deleted",
            "platform": "GitHub",
        },
        text="deleted vector text",
    )

    markdown = service._format_results("deleted", [node], 10)

    assert markdown == "No results found for 'deleted'"


def test_legacy_search_rejects_real_tombstoned_chunk_id_after_cleanup(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_github",
            source_type=SourceType.GITHUB,
            name="GitHub",
            enabled=True,
            sync_status=SyncStatus.IDLE,
        )
    )
    job, started = store.begin_sync_job("source_github")
    assert started is True
    stale = DocumentModel(
        id="stale",
        source_id="source_github",
        title="stale.py",
        content="print('stale')",
        url="https://example.com/stale.py",
        platform="GitHub",
        path="stale.py",
        last_seen_at="2026-05-22T00:00:00Z",
    )
    store.upsert_document_and_replace_chunks(
        stale,
        [
            ChunkModel(
                chunk_id="stale:chunk:0:bbb",
                document_id="stale",
                source_id="source_github",
                title="stale.py",
                text="print('stale')",
                url="https://example.com/stale.py",
                path="stale.py",
                chunk_index=0,
                content_hash="bbb",
            )
        ],
    )
    store.complete_successful_sync(
        job_id=job.job_id,
        source_id="source_github",
        total_documents=0,
        processed_documents=0,
        indexed_chunks=0,
        skipped_documents=0,
        last_seen_at="2026-05-22T00:01:00Z",
        cleanup_missing_documents=True,
        deleted_at="2026-05-22T00:02:00Z",
    )
    service = SearchService(
        AppConfig(preview_length=80),
        indexer=None,
        metadata_store=store,
    )
    node = FakeNode(
        {
            "doc_id": "stale:chunk:0:bbb",
            "title": "Deleted markerless raw",
            "url": "https://example.com/stale.py",
            "platform": "GitHub",
        },
        text="deleted vector text",
    )

    assert store.get_chunk("stale:chunk:0:bbb") is None
    assert store.has_chunk_record("stale:chunk:0:bbb") is True
    markdown = service._format_results("deleted", [node], 10)

    assert markdown == "No results found for 'deleted'"


def test_legacy_search_rejects_raw_vector_for_tombstoned_contextwiki_document():
    tombstoned = DocumentModel(
        id="doc-1",
        document_id="doc-1",
        source_id="source_fake",
        title="Deleted",
        content="deleted content",
        url="https://example.com/deleted",
        platform="GitHub",
        deleted_at="2026-05-22T00:00:00Z",
    )
    service = SearchService(
        AppConfig(preview_length=80),
        indexer=None,
        metadata_store=FakeMetadataStore(documents={"doc-1": tombstoned}),
    )
    node = FakeNode(
        {
            "doc_id": "doc-1",
            "document_id": "doc-1",
            "source_id": "source_fake",
            "title": "Stale raw deleted",
        },
        text="stale deleted raw vector",
    )

    markdown = service._format_results("deleted", [node], 10)

    assert markdown == "No results found for 'deleted'"


def test_legacy_search_rejects_notion_legacy_raw_id_for_native_document():
    tombstoned = DocumentModel(
        id="notion_page-123",
        document_id="page-123",
        external_id="page-123",
        source_id="source_notion",
        title="Deleted Notion",
        content="deleted content",
        url="https://notion.so/page-123",
        canonical_url="https://notion.so/page-123",
        platform="Notion",
        deleted_at="2026-05-22T00:00:00Z",
    )
    service = SearchService(
        AppConfig(preview_length=80),
        indexer=None,
        metadata_store=FakeMetadataStore(documents={"page-123": tombstoned}),
    )
    node = FakeNode(
        {
            "doc_id": "notion_page-123",
            "url": "https://notion.so/page-123",
            "title": "Legacy raw Notion",
            "platform": "Notion",
        },
        text="stale Notion vector",
    )

    markdown = service._format_results("notion", [node], 10)

    assert markdown == "No results found for 'notion'"


def test_legacy_search_rejects_tistory_legacy_raw_id_for_native_document():
    tombstoned = DocumentModel(
        id="tistory_7",
        document_id="devlog:7",
        external_id="devlog:7",
        source_id="source_tistory",
        title="Deleted Tistory",
        content="deleted content",
        url="https://devlog.tistory.com/7",
        canonical_url="https://devlog.tistory.com/7",
        platform="Tistory",
        deleted_at="2026-05-22T00:00:00Z",
    )
    service = SearchService(
        AppConfig(preview_length=80),
        indexer=None,
        metadata_store=FakeMetadataStore(documents={"devlog:7": tombstoned}),
    )
    node = FakeNode(
        {
            "doc_id": "tistory_7",
            "url": "https://devlog.tistory.com/7",
            "title": "Legacy raw Tistory",
            "platform": "Tistory",
        },
        text="stale Tistory vector",
    )

    markdown = service._format_results("tistory", [node], 10)

    assert markdown == "No results found for 'tistory'"


def test_legacy_search_rejects_raw_vector_for_known_canonical_url(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    store.upsert_document(
        DocumentModel(
            id="notion_page-123",
            document_id="page-123",
            external_id="page-123",
            source_id="source_notion",
            title="Deleted Notion",
            content="deleted content",
            url="https://notion.so/page-123",
            canonical_url="https://notion.so/page-123",
            platform="Notion",
            deleted_at="2026-05-22T00:00:00Z",
        )
    )
    service = SearchService(
        AppConfig(preview_length=80),
        indexer=None,
        metadata_store=store,
    )
    node = FakeNode(
        {
            "doc_id": "legacy-raw-id",
            "canonical_url": "https://notion.so/different-canonical",
            "url": "https://notion.so/page-123",
            "title": "Legacy raw URL hit",
            "platform": "Notion",
        },
        text="stale vector text",
    )

    markdown = service._format_results("notion", [node], 10)

    assert markdown == "No results found for 'notion'"


def test_legacy_search_expands_past_stale_managed_window(monkeypatch):
    active = ChunkModel(
        chunk_id="active-chunk",
        document_id="doc-1",
        source_id="source_fake",
        title="Active",
        text="Active chunk text",
        url="https://example.com/active",
        path="active.md",
        chunk_index=0,
        content_hash="hash",
    )
    stale = FakeNode(
        {
            "contextwiki_managed": "true",
            "chunk_id": "stale-chunk",
            "title": "Stale",
            "url": "https://example.com/stale",
        },
        score=0.99,
    )
    active_node = FakeNode(
        {
            "contextwiki_managed": "true",
            "chunk_id": "active-chunk",
            "document_id": "doc-1",
            "source_id": "source_fake",
            "title": "Stale Active Title",
            "url": "https://example.com/stale-active",
        },
        score=0.8,
    )
    all_nodes = [stale for _ in range(8)] + [active_node]
    requested_limits = []

    class FakeRetriever:
        def __init__(self, index, similarity_top_k, vector_store_query_mode):
            requested_limits.append(similarity_top_k)
            self.limit = similarity_top_k

        def retrieve(self, query):
            return all_nodes[: self.limit]

    monkeypatch.setattr("search.service.VectorIndexRetriever", FakeRetriever)
    service = SearchService(
        AppConfig(search_multiplier=1, preview_length=80),
        indexer=FakeIndexer(),
        metadata_store=FakeMetadataStore({"active-chunk": active}),
    )

    markdown = asyncio.run(service.search("active", 1))

    assert requested_limits[:5] == [1, 2, 4, 8, 16]
    assert "Active" in markdown
    assert "Stale" not in markdown
