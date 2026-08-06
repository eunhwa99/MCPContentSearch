import asyncio

import pytest

from core.models import DocumentModel
from core.utils import ContentHasher
from indexing.indexer import ContentIndexer
from indexing.manager import IndexManager


pytestmark = pytest.mark.unit
LEGACY_MANAGED_KEY = "context" + "wiki_managed"


class FakeCollection:
    def __init__(self, metadatas):
        self.metadatas = metadatas
        self.deleted_where = []

    def get(self, include=None):
        return {"metadatas": self.metadatas}

    def delete(self, where):
        self.deleted_where.append(where)


class FakeMalformedCollection(FakeCollection):
    def __init__(self, payload):
        super().__init__([])
        self.payload = payload

    def get(self, include=None):
        return self.payload


def test_index_manager_treats_non_mapping_chroma_get_payload_as_empty():
    collection = FakeMalformedCollection(True)

    manager = IndexManager(collection)

    document = DocumentModel(
        id="new-chunk",
        source_id="source_a",
        title="New",
        content="new content",
        url="https://example.com/new",
        platform="Notion",
    )
    assert manager.is_new(document) is True
    assert manager.is_updated(document) is False


def test_index_manager_ignores_non_mapping_metadata_entries():
    existing_content = "same id, same content"
    collection = FakeMalformedCollection(
        {
            "metadatas": [
                True,
                None,
                {
                    "doc_id": "kept",
                    "source_id": "source_a",
                    "content_hash": ContentHasher.hash_content(existing_content),
                },
            ]
        }
    )

    manager = IndexManager(collection)

    kept = DocumentModel(
        id="kept",
        source_id="source_a",
        title="Kept",
        content=existing_content,
        url="https://example.com/kept",
        platform="Notion",
    )
    assert manager.is_new(kept) is False
    assert manager.is_updated(kept) is False


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
                {"context_zip_managed": {"$ne": "true"}},
                {LEGACY_MANAGED_KEY: {"$ne": "true"}},
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
                {"context_zip_managed": {"$ne": "true"}},
                {LEGACY_MANAGED_KEY: {"$ne": "true"}},
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
                "context_zip_managed": "false",
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
                {"context_zip_managed": "true"},
            ]
        },
        {
            "$and": [
                {"doc_id": "shared-chunk"},
                {"source_id": "source_a"},
                {LEGACY_MANAGED_KEY: "true"},
            ]
        }
    ]


def test_index_manager_recognizes_legacy_managed_metadata_key_for_cleanup():
    existing_content = "same id, same content"
    collection = FakeCollection(
        [
            {
                "doc_id": "shared-chunk",
                "source_id": "source_a",
                LEGACY_MANAGED_KEY: "true",
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

    assert manager.is_new(managed_chunk) is False
    assert manager.is_updated(managed_chunk) is False

    manager.delete_document(managed_chunk)

    assert collection.deleted_where[-1] == {
        "$and": [
            {"doc_id": "shared-chunk"},
            {"source_id": "source_a"},
            {LEGACY_MANAGED_KEY: "true"},
        ]
    }


def test_content_indexer_source_scopes_managed_vector_cleanup():
    collection = FakeCollection([])
    indexer = ContentIndexer(config=None, chroma_collection=collection, storage_context=None)

    asyncio.run(indexer.delete_documents_by_ids(["shared-chunk"], source_id="source_b"))

    assert collection.deleted_where == [
        {
            "$and": [
                {"doc_id": "shared-chunk"},
                {"source_id": "source_b"},
                {"context_zip_managed": "true"},
            ]
        },
        {
            "$and": [
                {"doc_id": "shared-chunk"},
                {"source_id": "source_b"},
                {LEGACY_MANAGED_KEY: "true"},
            ]
        }
    ]


def test_content_indexer_raw_cleanup_does_not_match_managed_vectors_without_source():
    collection = FakeCollection([])
    indexer = ContentIndexer(config=None, chroma_collection=collection, storage_context=None)

    asyncio.run(indexer.delete_documents_by_ids(["shared-chunk"]))

    assert collection.deleted_where == [
        {
            "$and": [
                {"doc_id": "shared-chunk"},
                {"context_zip_managed": {"$ne": "true"}},
                {LEGACY_MANAGED_KEY: {"$ne": "true"}},
            ]
        }
    ]
