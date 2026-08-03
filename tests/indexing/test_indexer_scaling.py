import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from llama_index.core import Document

from core.models import DocumentModel
from core.utils import ContentHasher
from indexing.indexer import ContentIndexer, METADATA_UPDATE_BATCH_SIZE


def _managed_document(index: int, *, content: str) -> DocumentModel:
    chunk_id = f"chunk-{index}"
    return DocumentModel(
        id=chunk_id,
        chunk_id=chunk_id,
        document_id="career:large",
        source_id="source_career",
        title="Evidence",
        content=content,
        url="career://large.md",
        platform="career",
    )


class SnapshotCollection:
    def __init__(self, count: int):
        self.metadatas = [
            {
                "doc_id": f"chunk-{index}",
                "chunk_id": f"chunk-{index}",
                "document_id": "career:large",
                "source_id": "source_career",
                "contextwiki_managed": "true",
                "content_hash": ContentHasher.hash_content("old content"),
            }
            for index in range(count)
        ]
        self.get_calls: list[dict | None] = []
        self.delete_calls: list[dict] = []

    def get(self, *, where=None, include=None):
        assert include == ["metadatas"]
        self.get_calls.append(where)
        if where is None:
            return {"metadatas": list(self.metadatas)}
        filters = where["$and"]
        requested = set(filters[0]["$or"][0]["chunk_id"]["$in"])
        return {
            "metadatas": [
                dict(metadata)
                for metadata in self.metadatas
                if metadata["chunk_id"] in requested
            ]
        }

    def delete(self, *, where):
        self.delete_calls.append(where)


@pytest.mark.integration
def test_filter_documents_batches_snapshot_and_updated_deletes_by_chunk_id():
    document_count = 1_200
    collection = SnapshotCollection(document_count)
    indexer = ContentIndexer(
        config=SimpleNamespace(progress_log_interval=10_000),
        chroma_collection=collection,
        storage_context=None,
    )
    documents = [
        _managed_document(index, content="new content")
        for index in range(document_count)
    ]

    result = asyncio.run(indexer._filter_documents(documents))

    expected_batches = 3
    assert len(result["documents"]) == document_count
    assert result["updated"] == document_count
    assert len(collection.get_calls) == expected_batches
    assert len(collection.delete_calls) == expected_batches
    for where in [*collection.get_calls, *collection.delete_calls]:
        assert where is not None
        filters = where["$and"]
        identity_filters = filters[0]["$or"]
        requested = identity_filters[0]["chunk_id"]["$in"]
        assert 0 < len(requested) <= METADATA_UPDATE_BATCH_SIZE
        assert identity_filters[1]["doc_id"]["$in"] == requested
        assert filters[1] == {"source_id": "source_career"}
        assert filters[2] == {"contextwiki_managed": "true"}


@pytest.mark.integration
def test_filter_documents_snapshot_get_does_not_block_event_loop():
    entered_get = threading.Event()
    peer_progressed = threading.Event()

    class BlockingSnapshotCollection:
        def get(self, *, where=None, include=None):
            del where, include
            entered_get.set()
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                if peer_progressed.is_set():
                    return {"metadatas": []}
                time.sleep(0.01)
            raise AssertionError("event loop did not progress during Chroma snapshot")

        def delete(self, *, where):
            raise AssertionError(f"unexpected delete: {where}")

    indexer = ContentIndexer(
        config=SimpleNamespace(progress_log_interval=10_000),
        chroma_collection=BlockingSnapshotCollection(),
        storage_context=None,
    )

    async def scenario():
        async def peer():
            while not entered_get.is_set():
                await asyncio.sleep(0)
            peer_progressed.set()

        peer_task = asyncio.create_task(peer())
        result = await indexer._filter_documents(
            [_managed_document(1, content="new content")]
        )
        await peer_task
        return result

    result = asyncio.run(scenario())

    assert result["new"] == 1


@pytest.mark.integration
def test_filter_documents_keeps_legacy_doc_id_metadata_compatible():
    content = "stable legacy content"

    class LegacyDocumentIdCollection:
        def get(self, *, where, include):
            assert include == ["metadatas"]
            identity_filter = where["$and"][0]
            if "$or" not in identity_filter:
                return {"metadatas": []}
            requested_doc_ids = identity_filter["$or"][1]["doc_id"]["$in"]
            if "legacy-doc" not in requested_doc_ids:
                return {"metadatas": []}
            return {
                "metadatas": [
                    {
                        "doc_id": "legacy-doc",
                        "contextwiki_managed": "false",
                        "content_hash": ContentHasher.hash_content(content),
                    }
                ]
            }

        def delete(self, *, where):
            raise AssertionError(f"unexpected delete: {where}")

    indexer = ContentIndexer(
        config=SimpleNamespace(progress_log_interval=10_000),
        chroma_collection=LegacyDocumentIdCollection(),
        storage_context=None,
    )
    document = DocumentModel(
        id="legacy-doc",
        title="Legacy",
        content=content,
        url="https://example.com/legacy",
        platform="Legacy",
    )

    result = asyncio.run(indexer._filter_documents([document]))

    assert result == {"documents": [], "new": 0, "updated": 0}


@pytest.mark.integration
def test_warmed_large_write_batches_preserve_heartbeat_and_bound_latency(monkeypatch):
    import indexing.indexer as indexer_module

    class WarmedIndex:
        def __init__(self):
            self.insert_calls = 0
            self.docstore = SimpleNamespace(set_document_hash=lambda *_args: None)

        def insert(self, document):
            del document
            self.insert_calls += 1

        def insert_nodes(self, nodes):
            batch_sizes.append(len(nodes))
            time.sleep(0.01)

    warmed_index = WarmedIndex()
    batch_sizes: list[int] = []

    def transform_batch(batch, transformations, show_progress=True):
        del transformations
        assert show_progress is True
        return list(batch)

    monkeypatch.setattr(
        indexer_module,
        "run_transformations",
        transform_batch,
    )
    indexer = ContentIndexer(
        config=SimpleNamespace(progress_log_interval=10_000, batch_size=500),
        chroma_collection=None,
        storage_context=None,
    )
    indexer.index = warmed_index
    documents = [Document(text=f"evidence-{index}") for index in range(1_200)]

    async def scenario():
        heartbeat_times: list[float] = []
        finished = asyncio.Event()

        async def heartbeat():
            while not finished.is_set():
                heartbeat_times.append(time.perf_counter())
                await asyncio.sleep(0.001)

        heartbeat_task = asyncio.create_task(heartbeat())
        started_at = time.perf_counter()
        await indexer._batch_index(documents)
        elapsed = time.perf_counter() - started_at
        finished.set()
        await heartbeat_task
        return elapsed, heartbeat_times

    elapsed, heartbeat_times = asyncio.run(scenario())

    heartbeat_gaps = [
        right - left
        for left, right in zip(heartbeat_times, heartbeat_times[1:])
    ]
    assert batch_sizes == [500, 500, 200]
    assert warmed_index.insert_calls == 0
    assert len(heartbeat_times) >= 3
    assert max(heartbeat_gaps, default=0.0) < 0.05
    assert elapsed < 0.2


@pytest.mark.integration
def test_warmed_prechunked_passages_stay_one_to_one_across_batches(monkeypatch):
    import indexing.indexer as indexer_module

    inserted_nodes = []

    class WarmedIndex:
        docstore = SimpleNamespace(set_document_hash=lambda *_args: None)

        def insert_nodes(self, nodes):
            inserted_nodes.extend(nodes)

    def reject_prechunked_transform(nodes, transformations, show_progress=True):
        del transformations
        assert show_progress is True
        assert all(
            node.metadata.get("contextwiki_managed") != "true" for node in nodes
        ), "already-chunked passages must bypass size transformations"
        return list(nodes)

    monkeypatch.setattr(
        indexer_module,
        "run_transformations",
        reject_prechunked_transform,
    )
    indexer = ContentIndexer(
        config=SimpleNamespace(progress_log_interval=10_000, batch_size=1_000),
        chroma_collection=None,
        storage_context=None,
    )
    indexer.index = WarmedIndex()
    passages = [
        Document(
            id_=f"career-chunk-{index}",
            text=(f"다국어 reliability evidence {index}. " * 200),
            metadata={
                "chunk_id": f"career-chunk-{index}",
                "source_id": "source_career",
                "contextwiki_managed": "true",
            },
        )
        for index in range(501)
    ]

    asyncio.run(indexer._batch_index(passages))

    assert [node.id_ for node in inserted_nodes] == [
        f"career-chunk-{index}" for index in range(501)
    ]
