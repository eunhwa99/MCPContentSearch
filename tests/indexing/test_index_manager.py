import pytest

from core.models import DocumentModel
from core.utils import ContentHasher
from indexing.indexer import ContentIndexer
from indexing.manager import IndexManager


pytestmark = pytest.mark.unit


class FakeCollection:
    def __init__(self, metadatas):
        self.metadatas = metadatas
        self.deleted_where = []

    def get(self, include=None):
        return {"metadatas": self.metadatas}

    def delete(self, where):
        self.deleted_where.append(where)


def test_index_manager_keys_existing_documents_by_source_id():
    existing_content = "same id, same content"
    collection = FakeCollection(
        [
            {
                "doc_id": "shared-chunk",
                "source_id": "source_a",
                "content_hash": ContentHasher.hash_content(existing_content),
            }
        ]
    )
    manager = IndexManager(collection)
    same_source = DocumentModel(
        id="shared-chunk",
        source_id="source_a",
        title="Shared",
        content=existing_content,
        url="https://example.com/a",
        platform="GitHub",
    )
    other_source = same_source.model_copy(update={"source_id": "source_b"})

    assert manager.is_new(same_source) is False
    assert manager.is_updated(same_source) is False
    assert manager.is_new(other_source) is True
    assert manager.is_updated(other_source) is False


def test_index_manager_deletes_outdated_document_with_source_filter():
    collection = FakeCollection([])
    manager = IndexManager(collection)
    document = DocumentModel(
        id="shared-chunk",
        source_id="source_b",
        title="Shared",
        content="updated",
        url="https://example.com/b",
        platform="GitHub",
    )

    manager.delete_document(document)

    assert collection.deleted_where == [
        {
            "$and": [
                {"doc_id": "shared-chunk"},
                {"source_id": "source_b"},
                {"contextwiki_managed": {"$ne": "true"}},
            ]
        }
    ]


def test_index_manager_no_source_raw_delete_does_not_match_managed_vectors():
    collection = FakeCollection([])
    manager = IndexManager(collection)

    manager.delete_document("shared-chunk")

    assert collection.deleted_where == [
        {
            "$and": [
                {"doc_id": "shared-chunk"},
                {"contextwiki_managed": {"$ne": "true"}},
            ]
        }
    ]


def test_index_manager_separates_managed_chunks_from_legacy_vectors():
    existing_content = "same id, same content"
    collection = FakeCollection(
        [
            {
                "doc_id": "shared-chunk",
                "source_id": "source_a",
                "contextwiki_managed": "false",
                "content_hash": ContentHasher.hash_content(existing_content),
            }
        ]
    )
    manager = IndexManager(collection)
    managed_chunk = DocumentModel(
        id="shared-chunk",
        chunk_id="shared-chunk",
        document_id="doc-1",
        source_id="source_a",
        title="Shared",
        content=existing_content,
        url="https://example.com/a",
        platform="GitHub",
    )

    assert manager.is_new(managed_chunk) is True
    assert manager.is_updated(managed_chunk) is False

    manager.delete_document(managed_chunk)

    assert collection.deleted_where == [
        {
            "$and": [
                {"doc_id": "shared-chunk"},
                {"source_id": "source_a"},
                {"contextwiki_managed": "true"},
            ]
        }
    ]


def test_content_indexer_source_scopes_managed_vector_cleanup():
    collection = FakeCollection([])
    indexer = ContentIndexer(config=None, chroma_collection=collection, storage_context=None)

    indexer.delete_documents_by_ids(["shared-chunk"], source_id="source_b")

    assert collection.deleted_where == [
        {
            "$and": [
                {"doc_id": "shared-chunk"},
                {"source_id": "source_b"},
                {"contextwiki_managed": "true"},
            ]
        }
    ]


def test_content_indexer_raw_cleanup_does_not_match_managed_vectors_without_source():
    collection = FakeCollection([])
    indexer = ContentIndexer(config=None, chroma_collection=collection, storage_context=None)

    indexer.delete_documents_by_ids(["shared-chunk"])

    assert collection.deleted_where == [
        {
            "$and": [
                {"doc_id": "shared-chunk"},
                {"contextwiki_managed": {"$ne": "true"}},
            ]
        }
    ]
