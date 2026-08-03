import asyncio
import json

import pytest

from core.models import DocumentModel, EvidenceSourceType, ExperienceType
from core.exceptions import IndexingError
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


class MetadataUpdatingCollection:
    def __init__(self, chunk_ids=("chunk-1",), *, fail_on_update_call=None):
        self.records = {
            f"chroma-{chunk_id}": {
                "_node_content": json.dumps(
                    {
                        "metadata": {
                            "chunk_id": chunk_id,
                            "evidence_source_type": "resume",
                            "experience_type": "professional",
                        },
                        "excluded_embed_metadata_keys": [
                            "evidence_source_type",
                            "experience_type",
                        ],
                    }
                ),
                "chunk_id": chunk_id,
                "doc_id": chunk_id,
                "source_id": "source_career",
                "contextwiki_managed": "true",
                "evidence_source_type": "resume",
                "experience_type": "professional",
            }
            for chunk_id in chunk_ids
        }
        self.get_calls = 0
        self.update_calls = 0
        self.delete_calls = 0
        self.fail_on_update_call = fail_on_update_call

    def get(self, *, where, include):
        self.get_calls += 1
        assert include == ["metadatas"]
        chunk_filter = where["$and"][0]["chunk_id"]
        requested = set(chunk_filter["$in"])
        ids = [
            record_id
            for record_id, metadata in self.records.items()
            if metadata["chunk_id"] in requested
        ]
        return {
            "ids": ids,
            "metadatas": [dict(self.records[record_id]) for record_id in ids],
        }

    def update(self, *, ids, metadatas):
        self.update_calls += 1
        for record_id, metadata in zip(ids, metadatas, strict=True):
            self.records[record_id] = dict(metadata)
        if self.update_calls == self.fail_on_update_call:
            raise RuntimeError("injected partial Chroma update failure")

    def delete(self, *, where):
        self.delete_calls += 1
        chunk_filter = where["$and"][0]["chunk_id"]
        requested = set(chunk_filter["$in"])
        self.records = {
            record_id: metadata
            for record_id, metadata in self.records.items()
            if metadata["chunk_id"] not in requested
        }


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
    manager = IndexManager(collection.metadatas)
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


def test_index_manager_uses_caller_supplied_metadata_snapshot():
    existing_content = "same id, same content"
    manager = IndexManager(
        [
            {
                "doc_id": "shared-chunk",
                "source_id": "source_a",
                "content_hash": ContentHasher.hash_content(existing_content),
            }
        ]
    )
    document = DocumentModel(
        id="shared-chunk",
        source_id="source_a",
        title="Shared",
        content=existing_content,
        url="https://example.com/a",
        platform="GitHub",
    )

    assert manager.is_new(document) is False
    assert manager.is_updated(document) is False


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
    manager = IndexManager(collection.metadatas)
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


def test_index_manager_keys_managed_snapshot_by_chunk_id_not_rewritten_doc_id():
    content = "same managed content"
    manager = IndexManager(
        [
            {
                "doc_id": "llama-ref-doc-id",
                "chunk_id": "stable-chunk-id",
                "source_id": "source_career",
                "contextwiki_managed": "true",
                "content_hash": ContentHasher.hash_content(content),
            }
        ]
    )
    managed_chunk = DocumentModel(
        id="stable-chunk-id",
        chunk_id="stable-chunk-id",
        document_id="career:stable",
        source_id="source_career",
        title="Evidence",
        content=content,
        url="career://evidence.md",
        platform="career",
    )

    assert manager.is_new(managed_chunk) is False
    assert manager.is_updated(managed_chunk) is False


def test_index_manager_keys_raw_snapshot_by_stable_chunk_id():
    content = "same raw content"
    manager = IndexManager(
        [
            {
                "doc_id": "llama-ref-doc-id",
                "chunk_id": "stable-raw-id",
                "contextwiki_managed": "false",
                "content_hash": ContentHasher.hash_content(content),
            }
        ]
    )
    raw_document = DocumentModel(
        id="stable-raw-id",
        title="Legacy",
        content=content,
        url="https://example.com/raw",
        platform="Legacy",
    )

    assert manager.is_new(raw_document) is False
    assert manager.is_updated(raw_document) is False

def test_content_indexer_source_scopes_managed_vector_cleanup():
    collection = FakeCollection([])
    indexer = ContentIndexer(config=None, chroma_collection=collection, storage_context=None)

    asyncio.run(indexer.delete_documents_by_ids(["shared-chunk"], source_id="source_b"))

    assert collection.deleted_where == [
        {
            "$and": [
                {
                    "$or": [
                        {"chunk_id": {"$in": ["shared-chunk"]}},
                        {"doc_id": {"$in": ["shared-chunk"]}},
                    ]
                },
                {"source_id": "source_b"},
                {"contextwiki_managed": "true"},
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
                {
                    "$or": [
                        {"chunk_id": {"$in": ["shared-chunk"]}},
                        {"doc_id": {"$in": ["shared-chunk"]}},
                    ]
                },
                {"contextwiki_managed": {"$ne": "true"}},
            ]
        }
    ]


def test_content_indexer_batches_large_source_scoped_vector_cleanup():
    collection = FakeCollection([])
    indexer = ContentIndexer(config=None, chroma_collection=collection, storage_context=None)
    document_ids = [f"chunk-{index}" for index in range(5_501)]

    asyncio.run(
        indexer.delete_documents_by_ids(
            document_ids,
            source_id="source_career",
        )
    )

    assert len(collection.deleted_where) == 12
    deleted_ids = []
    for where in collection.deleted_where:
        filters = where["$and"]
        identity_filters = filters[0]["$or"]
        batch = identity_filters[0]["chunk_id"]["$in"]
        assert len(batch) <= 500
        assert identity_filters[1]["doc_id"]["$in"] == batch
        assert filters[1] == {"source_id": "source_career"}
        assert filters[2] == {"contextwiki_managed": "true"}
        deleted_ids.extend(batch)
    assert deleted_ids == document_ids


def test_content_indexer_updates_taxonomy_metadata_without_reembedding():
    collection = MetadataUpdatingCollection()
    indexer = ContentIndexer(config=None, chroma_collection=collection, storage_context=None)
    chunk = DocumentModel(
        id="chunk-1",
        chunk_id="chunk-1",
        document_id="career:stable",
        source_id="source_career",
        title="Evidence",
        content="Built a reliable queue.",
        url="career://career:stable",
        platform="career",
        evidence_source_type=EvidenceSourceType.CAREER_NOTE,
        experience_type=ExperienceType.ACADEMIC,
    )

    result = asyncio.run(indexer.update_documents_metadata([chunk]))

    assert result["embeddings_generated"] == 0
    assert result["embeddings_reused"] == 1
    assert result["metadata_rollback"]
    assert collection.get_calls == 1
    assert collection.update_calls == 1
    metadata = collection.records["chroma-chunk-1"]
    assert metadata["evidence_source_type"] == "career_note"
    assert metadata["experience_type"] == "academic"
    serialized = json.loads(metadata["_node_content"])
    assert serialized["metadata"]["evidence_source_type"] == "career_note"
    assert serialized["metadata"]["experience_type"] == "academic"


def test_content_indexer_rolls_back_all_batches_after_partial_metadata_failure(
    monkeypatch,
):
    import indexing.indexer as indexer_module

    monkeypatch.setattr(indexer_module, "METADATA_UPDATE_BATCH_SIZE", 2)
    collection = MetadataUpdatingCollection(
        ("chunk-1", "chunk-2", "chunk-3"),
        fail_on_update_call=2,
    )
    indexer = ContentIndexer(config=None, chroma_collection=collection, storage_context=None)
    chunks = [
        DocumentModel(
            id=f"chunk-{index}",
            chunk_id=f"chunk-{index}",
            document_id="career:stable",
            source_id="source_career",
            title="Evidence",
            content=f"Evidence {index}",
            url="career://career:stable",
            platform="career",
            evidence_source_type=EvidenceSourceType.CAREER_NOTE,
            experience_type=ExperienceType.ACADEMIC,
        )
        for index in range(1, 4)
    ]

    with pytest.raises(IndexingError, match="Vector metadata update failed"):
        asyncio.run(indexer.update_documents_metadata(chunks))

    assert collection.get_calls == 2
    for metadata in collection.records.values():
        assert metadata["evidence_source_type"] == "resume"
        assert metadata["experience_type"] == "professional"


def test_content_indexer_batches_taxonomy_metadata_operations_for_large_sources():
    chunk_ids = tuple(f"chunk-{index}" for index in range(1_200))
    collection = MetadataUpdatingCollection(chunk_ids)
    indexer = ContentIndexer(config=None, chroma_collection=collection, storage_context=None)
    chunks = [
        DocumentModel(
            id=chunk_id,
            chunk_id=chunk_id,
            document_id="career:large",
            source_id="source_career",
            title="Evidence",
            content=f"Evidence {chunk_id}",
            url="career://career:large",
            platform="career",
            evidence_source_type=EvidenceSourceType.CAREER_NOTE,
            experience_type=ExperienceType.ACADEMIC,
        )
        for chunk_id in chunk_ids
    ]

    result = asyncio.run(indexer.update_documents_metadata(chunks))

    assert result["embeddings_generated"] == 0
    assert result["embeddings_reused"] == 1_200
    assert collection.get_calls == 3
    assert collection.update_calls == 3


def test_content_indexer_batches_missing_vector_fallback_and_reports_counters():
    collection = MetadataUpdatingCollection(("chunk-1", "chunk-2"))
    indexer = ContentIndexer(config=None, chroma_collection=collection, storage_context=None)
    generated = []

    async def record_batch(documents):
        generated.extend(documents)

    indexer._batch_index = record_batch
    chunks = [
        DocumentModel(
            id=f"chunk-{index}",
            chunk_id=f"chunk-{index}",
            document_id="career:missing",
            source_id="source_career",
            title="Evidence",
            content=f"Evidence {index}",
            url="career://career:missing",
            platform="career",
            evidence_source_type=EvidenceSourceType.CAREER_NOTE,
            experience_type=ExperienceType.ACADEMIC,
        )
        for index in range(1, 4)
    ]

    result = asyncio.run(indexer.update_documents_metadata(chunks))

    assert result["embeddings_generated"] == 1
    assert result["embeddings_reused"] == 2
    assert len(generated) == 1
    assert generated[0].metadata["chunk_id"] == "chunk-3"
    asyncio.run(indexer.rollback_documents_metadata(result["metadata_rollback"]))
    assert collection.delete_calls == 1
