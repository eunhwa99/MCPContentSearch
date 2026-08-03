from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import threading
import time

import pytest
from llama_index.core import Settings, StorageContext
from llama_index.core.embeddings import MockEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore
from mcp.server.fastmcp import FastMCP

from api.tools import register_tools
from core.exceptions import ParsingError
from core.models import (
    DocumentModel,
    EvidenceSourceType,
    ExperienceType,
    SyncJobStatus,
)
from core.utils import ContentHasher
from environments.config import AppConfig, setup_chroma
from fetching import connectors as connector_module
from fetching.connectors import CareerSourceConnector, SourceRegistry
from fetching import career_files as career_files_module
from indexing.chunker import DocumentChunker
from indexing.indexer import ContentIndexer
from indexing.ingestion_service import IngestionService
from parsing import career_documents as career_documents_module
from search.context_service import ContextSearchService
from search.evidence_service import EvidenceSearchService
from storage.metadata_store import MetadataStore
from tests.fixtures.career_documents import write_minimal_docx, write_minimal_pdf


pytestmark = pytest.mark.e2e


class DeterministicEmbeddingIndex:
    """Temp vector-provider substitute retaining real indexed chunk documents."""

    def __init__(self):
        self.documents = {}
        self.hashes = {}
        self.index_calls = 0
        self.metadata_update_calls = 0

    async def index_documents(self, documents):
        self.index_calls += 1
        generated = 0
        reused = 0
        for document in documents:
            content_hash = ContentHasher.hash_content(document.content)
            if self.hashes.get(document.id) == content_hash:
                reused += 1
                continue
            self.hashes[document.id] = content_hash
            self.documents[document.id] = document
            generated += 1
        return {
            "embeddings_generated": generated,
            "embeddings_reused": reused,
        }

    async def update_documents_metadata(self, documents):
        self.metadata_update_calls += 1
        reused = 0
        for document in documents:
            if document.id not in self.hashes:
                continue
            self.documents[document.id] = document
            reused += 1
        return {
            "embeddings_generated": 0,
            "embeddings_reused": reused,
        }

    async def delete_documents_by_ids(self, document_ids, source_id=""):
        del source_id
        for document_id in document_ids:
            self.documents.pop(document_id, None)
            self.hashes.pop(document_id, None)


class FailingOnceDeleteEmbeddingIndex(DeterministicEmbeddingIndex):
    def __init__(self):
        super().__init__()
        self.delete_attempts = 0

    async def delete_documents_by_ids(self, document_ids, source_id=""):
        self.delete_attempts += 1
        if self.delete_attempts == 1:
            raise RuntimeError("injected vector cleanup failure")
        await super().delete_documents_by_ids(document_ids, source_id=source_id)


class SimulatedWorkerTermination(BaseException):
    pass


class HardCrashAfterWriteEmbeddingIndex(DeterministicEmbeddingIndex):
    async def index_documents(self, documents):
        await super().index_documents(documents)
        raise SimulatedWorkerTermination("simulated abrupt worker termination")


class ParsingFailingEmbeddingIndex(DeterministicEmbeddingIndex):
    async def index_documents(self, documents):
        del documents
        await asyncio.sleep(0.001)
        raise ParsingError("synthetic parser failure")


class ProgressWriteFailingMetadataStore(MetadataStore):
    def update_sync_job(self, job_id: str, **updates):
        del job_id, updates
        raise RuntimeError("injected best-effort progress failure")


class HeartbeatRecordingChunker(DocumentChunker):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started = threading.Event()
        self.finished = threading.Event()

    def chunk_document(self, document, **kwargs):
        self.started.set()
        try:
            return super().chunk_document(document, **kwargs)
        finally:
            self.finished.set()


class DelayedCareerSourceConnector(CareerSourceConnector):
    def __init__(self, config, *, delay_seconds: float):
        super().__init__(config)
        self.delay_seconds = delay_seconds

    async def fetch_documents(self):
        await asyncio.sleep(self.delay_seconds)
        return await super().fetch_documents()


class MetadataRecordingCollection:
    def __init__(self, collection):
        self.collection = collection
        self.update_calls: list[dict] = []

    def __getattr__(self, name):
        return getattr(self.collection, name)

    def update(self, *args, **kwargs):
        self.update_calls.append(dict(kwargs))
        return self.collection.update(*args, **kwargs)


def _real_career_chroma_components(tmp_path, manifest, *, collection_wrapper=None):
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    store = MetadataStore(config.metadata_db_path)
    collection = setup_chroma(config)
    indexer_collection = (
        collection_wrapper(collection) if collection_wrapper else collection
    )
    storage_context = StorageContext.from_defaults(
        vector_store=ChromaVectorStore(chroma_collection=collection)
    )
    indexer = ContentIndexer(config, indexer_collection, storage_context)
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([CareerSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=indexer,
    )
    return store, collection, indexer_collection, ingestion


def _write_reorder_manifest(tmp_path):
    career_root = tmp_path / "career"
    career_root.mkdir()
    evidence_path = career_root / "reorder.md"
    evidence_path.write_text(
        "# First\nFirst stable evidence.\n# Second\nSecond stable evidence.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [
                    {
                        "path": "reorder.md",
                        "source_type": "career_note",
                        "experience_type": "professional",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return evidence_path, manifest


def _managed_metadata_by_chunk(collection):
    snapshot = collection.get(
        where={
            "$and": [
                {"source_id": "source_career"},
                {"contextwiki_managed": "true"},
            ]
        },
        include=["metadatas"],
    )
    return {
        metadata["chunk_id"]: metadata for metadata in (snapshot.get("metadatas") or [])
    }


def test_manifest_ingestion_keeps_event_loop_responsive_during_large_file_chunking(
    tmp_path,
):
    career_root = tmp_path / "career"
    career_root.mkdir()
    (career_root / "large.txt").write_text("career evidence\n" * 15_000)
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [
                    {
                        "path": "large.txt",
                        "source_type": "career_note",
                        "experience_type": "professional",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    chunker = HeartbeatRecordingChunker(max_chars=64, overlap_chars=0)
    ingestion = IngestionService(
        metadata_store=MetadataStore(config.metadata_db_path),
        source_registry=SourceRegistry([CareerSourceConnector(config)]),
        chunker=chunker,
        indexer=DeterministicEmbeddingIndex(),
    )

    async def run_with_heartbeat():
        ticks_during_chunking = 0

        async def heartbeat():
            nonlocal ticks_during_chunking
            while not chunker.finished.is_set():
                if chunker.started.is_set():
                    ticks_during_chunking += 1
                await asyncio.sleep(0)

        result, _ = await asyncio.gather(
            ingestion.sync_source("source_career"),
            heartbeat(),
        )
        return result, ticks_during_chunking

    result, ticks = asyncio.run(run_with_heartbeat())

    assert result.status == SyncJobStatus.SUCCEEDED
    assert ticks > 0, "career chunking blocked the worker event loop"


def test_manifest_ingestion_cancellation_stops_real_chunk_model_build(tmp_path):
    class CancellationDuringBuildChunker(DocumentChunker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.first_model_built = threading.Event()
            self.finished = threading.Event()
            self.stop_observed = threading.Event()
            self.model_builds = 0

        def chunk_document(self, document, **kwargs):
            stop_checker = kwargs["stop_checker"]

            def observed_stop_checker():
                stopped = stop_checker()
                if stopped:
                    self.stop_observed.set()
                return stopped

            try:
                return super().chunk_document(
                    document,
                    stop_checker=observed_stop_checker,
                )
            finally:
                self.finished.set()

        def _build_chunk(self, *args, **kwargs):
            self.model_builds += 1
            self.first_model_built.set()
            time.sleep(0.0005)
            return super()._build_chunk(*args, **kwargs)

    career_root = tmp_path / "career"
    career_root.mkdir()
    (career_root / "large.txt").write_text(
        "career evidence\n" * 2_000,
        encoding="utf-8",
    )
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [
                    {
                        "path": "large.txt",
                        "source_type": "career_note",
                        "experience_type": "professional",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    chunker = CancellationDuringBuildChunker(max_chars=16, overlap_chars=0)
    indexer = DeterministicEmbeddingIndex()
    ingestion = IngestionService(
        metadata_store=MetadataStore(config.metadata_db_path),
        source_registry=SourceRegistry([CareerSourceConnector(config)]),
        chunker=chunker,
        indexer=indexer,
    )

    async def cancel_during_model_build():
        task = asyncio.create_task(ingestion.sync_source("source_career"))
        while not chunker.first_model_built.is_set():
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_during_model_build())

    assert chunker.finished.is_set()
    assert chunker.stop_observed.is_set()
    assert chunker.model_builds < 10
    assert indexer.index_calls == 0


def test_manifest_docx_parsing_cancellation_interrupts_xml_iteration(
    monkeypatch,
    tmp_path,
):
    career_root = tmp_path / "career"
    career_root.mkdir()
    write_minimal_docx(
        career_root / "resume.docx",
        [("", f"Synthetic paragraph {index}") for index in range(2_000)],
    )
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [
                    {
                        "path": "resume.docx",
                        "source_type": "resume",
                        "experience_type": "professional",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    iteration_started = threading.Event()
    original_iterparse = career_documents_module.ElementTree.iterparse

    def slow_iterparse(*args, **kwargs):
        for item in original_iterparse(*args, **kwargs):
            iteration_started.set()
            time.sleep(0.001)
            yield item

    monkeypatch.setattr(
        career_documents_module.ElementTree,
        "iterparse",
        slow_iterparse,
    )
    connector = CareerSourceConnector(
        AppConfig(
            chroma_db_path=tmp_path / "chroma",
            metadata_db_path=tmp_path / "metadata.sqlite3",
            career_manifest_path=manifest,
        )
    )

    async def cancel_during_xml_iteration():
        task = asyncio.create_task(connector.fetch_documents())
        assert await asyncio.to_thread(iteration_started.wait, 1)
        started = time.perf_counter()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return time.perf_counter() - started

    cancellation_seconds = asyncio.run(cancel_during_xml_iteration())

    assert cancellation_seconds < 0.5


def test_real_chroma_reorder_refreshes_retained_chunk_metadata_without_reembedding(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(Settings, "embed_model", MockEmbedding(embed_dim=8))
    evidence_path, manifest = _write_reorder_manifest(tmp_path)
    store, collection, _recording, ingestion = _real_career_chroma_components(
        tmp_path,
        manifest,
    )

    first = asyncio.run(ingestion.sync_source("source_career"))
    document_id = store.list_documents()["documents"][0].document_id
    first_chunks = store.list_chunks_for_document(document_id)
    first_ids = {chunk.text: chunk.chunk_id for chunk in first_chunks}
    evidence_path.write_text(
        "# Second\nSecond stable evidence.\n# First\nFirst stable evidence.\n",
        encoding="utf-8",
    )

    updated = asyncio.run(ingestion.sync_source("source_career"))
    stored_chunks = store.list_chunks_for_document(document_id)
    vector_metadata = _managed_metadata_by_chunk(collection)

    assert first.status == SyncJobStatus.SUCCEEDED
    assert updated.status == SyncJobStatus.SUCCEEDED
    assert {chunk.text: chunk.chunk_id for chunk in stored_chunks} == first_ids
    assert updated.updated_chunks == 2
    assert updated.skipped_chunks == 0
    assert updated.embeddings_generated == 0
    assert updated.embeddings_reused == 2
    for chunk in stored_chunks:
        metadata = vector_metadata[chunk.chunk_id]
        node_metadata = json.loads(metadata["_node_content"])["metadata"]
        for field in (
            "chunk_index",
            "line_start",
            "line_end",
            "document_version_id",
        ):
            assert metadata[field] == getattr(chunk, field)
            assert node_metadata[field] == getattr(chunk, field)


def test_real_chroma_reorder_rolls_back_metadata_when_sqlite_commit_fails(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(Settings, "embed_model", MockEmbedding(embed_dim=8))
    evidence_path, manifest = _write_reorder_manifest(tmp_path)
    store, collection, recording, ingestion = _real_career_chroma_components(
        tmp_path,
        manifest,
        collection_wrapper=MetadataRecordingCollection,
    )

    first = asyncio.run(ingestion.sync_source("source_career"))
    document_id = store.list_documents()["documents"][0].document_id
    original_chunks = store.list_chunks_for_document(document_id)
    original_metadata = _managed_metadata_by_chunk(collection)
    evidence_path.write_text(
        "# Second\nSecond stable evidence.\n# First\nFirst stable evidence.\n",
        encoding="utf-8",
    )

    def fail_commit(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("injected SQLite commit failure")

    monkeypatch.setattr(ingestion, "_commit_chunks_or_current", fail_commit)
    failed = asyncio.run(ingestion.sync_source("source_career"))
    rolled_back_metadata = _managed_metadata_by_chunk(collection)

    assert first.status == SyncJobStatus.SUCCEEDED
    assert failed.status == SyncJobStatus.FAILED
    assert len(recording.update_calls) >= 2
    assert store.list_chunks_for_document(document_id) == original_chunks
    for chunk_id, before in original_metadata.items():
        after = rolled_back_metadata[chunk_id]
        for field in (
            "chunk_index",
            "line_start",
            "line_end",
            "document_version_id",
        ):
            assert after[field] == before[field]
        assert (
            json.loads(after["_node_content"])["metadata"]
            == json.loads(before["_node_content"])["metadata"]
        )


def test_real_chroma_incremental_indexing_uses_only_filtered_snapshots(tmp_path):
    class RecordingCollection:
        def __init__(self, collection):
            self.collection = collection
            self.get_filters = []
            self.delete_filters = []

        def get(self, *args, **kwargs):
            self.get_filters.append(kwargs.get("where"))
            return self.collection.get(*args, **kwargs)

        def delete(self, *args, **kwargs):
            self.delete_filters.append(kwargs.get("where"))
            return self.collection.delete(*args, **kwargs)

    previous_embed_model = Settings.embed_model
    Settings.embed_model = MockEmbedding(embed_dim=8)
    try:
        config = AppConfig(
            chroma_db_path=tmp_path / "chroma",
            metadata_db_path=tmp_path / "metadata.sqlite3",
            collection_name="career_indexer_scaling_e2e",
        )
        collection = setup_chroma(config)
        recording_collection = RecordingCollection(collection)
        storage_context = StorageContext.from_defaults(
            vector_store=ChromaVectorStore(chroma_collection=collection)
        )
        indexer = ContentIndexer(config, recording_collection, storage_context)
        chunk = DocumentModel(
            id="chunk-1",
            chunk_id="chunk-1",
            document_id="career:stable",
            source_id="source_career",
            title="Evidence",
            content="Built reliable queues.",
            url="career://evidence.md",
            platform="career",
        )

        first = asyncio.run(indexer.index_documents([chunk]))
        second = asyncio.run(
            indexer.index_documents(
                [chunk.model_copy(update={"content": "Built reliable queue retries."})]
            )
        )
        raw_document = DocumentModel(
            id="legacy-raw-doc",
            title="Legacy evidence",
            content="Stable raw evidence.",
            url="https://example.com/raw",
            platform="Legacy",
        )
        raw_first = asyncio.run(indexer.index_documents([raw_document]))
        raw_unchanged = asyncio.run(indexer.index_documents([raw_document]))
        raw_updated = asyncio.run(
            indexer.index_documents(
                [raw_document.model_copy(update={"content": "Updated raw evidence."})]
            )
        )

        assert first["embeddings_generated"] == 1
        assert second["embeddings_generated"] == 1
        assert raw_first["embeddings_generated"] == 1
        assert raw_unchanged == {
            "embeddings_generated": 0,
            "embeddings_reused": 1,
        }
        assert raw_updated["embeddings_generated"] == 1
        assert recording_collection.get_filters
        assert all(where is not None for where in recording_collection.get_filters)
        assert recording_collection.delete_filters
        for where in [
            *recording_collection.get_filters,
            *recording_collection.delete_filters,
        ]:
            filters = where["$and"]
            identity_filters = filters[0]["$or"]
            assert len(identity_filters[0]["chunk_id"]["$in"]) == 1
            assert (
                identity_filters[1]["doc_id"]["$in"]
                == identity_filters[0]["chunk_id"]["$in"]
            )
        assert any(
            where["$and"][-1] == {"contextwiki_managed": "true"}
            for where in recording_collection.get_filters
        )
        assert any(
            where["$and"][-1] == {"contextwiki_managed": {"$ne": "true"}}
            for where in recording_collection.get_filters
        )
    finally:
        Settings.embed_model = previous_embed_model


def test_real_chroma_warmed_index_bulk_writes_1200_chunks_in_bounded_batches(
    monkeypatch,
    tmp_path,
):
    import indexing.indexer as indexer_module

    previous_embed_model = Settings.embed_model
    Settings.embed_model = MockEmbedding(embed_dim=8)
    original_from_documents = indexer_module.VectorStoreIndex.from_documents
    original_insert_nodes = indexer_module.VectorStoreIndex.insert_nodes
    cold_batch_sizes: list[int] = []
    warm_batch_sizes: list[int] = []

    def recording_from_documents(batch, *args, **kwargs):
        cold_batch_sizes.append(len(batch))
        return original_from_documents(batch, *args, **kwargs)

    def recording_insert_nodes(index, nodes, *args, **kwargs):
        warm_batch_sizes.append(len(nodes))
        return original_insert_nodes(index, nodes, *args, **kwargs)

    monkeypatch.setattr(
        indexer_module.VectorStoreIndex,
        "from_documents",
        staticmethod(recording_from_documents),
    )
    monkeypatch.setattr(
        indexer_module.VectorStoreIndex,
        "insert_nodes",
        recording_insert_nodes,
    )
    try:
        config = AppConfig(
            chroma_db_path=tmp_path / "chroma",
            metadata_db_path=tmp_path / "metadata.sqlite3",
            collection_name="career_warmed_bulk_write_e2e",
            batch_size=1_000,
        )
        collection = setup_chroma(config)
        storage_context = StorageContext.from_defaults(
            vector_store=ChromaVectorStore(chroma_collection=collection)
        )
        indexer = ContentIndexer(config, collection, storage_context)
        seed = DocumentModel(
            id="seed-chunk",
            chunk_id="seed-chunk",
            document_id="career:seed",
            source_id="source_career",
            title="Seed",
            content="Seed evidence.",
            url="career://seed.md",
            platform="career",
            evidence_source_type=EvidenceSourceType.CAREER_NOTE,
            experience_type=ExperienceType.PROFESSIONAL,
        )
        chunks = [
            DocumentModel(
                id=f"bulk-chunk-{index}",
                chunk_id=f"bulk-chunk-{index}",
                document_id="career:bulk",
                source_id="source_career",
                title="Bulk evidence",
                content=f"Synthetic bulk evidence {index}.",
                url="career://bulk.md",
                platform="career",
                evidence_source_type=EvidenceSourceType.CAREER_NOTE,
                experience_type=ExperienceType.PROFESSIONAL,
            )
            for index in range(1_200)
        ]

        asyncio.run(indexer.index_documents([seed]))
        result = asyncio.run(indexer.index_documents(chunks))
        unchanged = asyncio.run(indexer.index_documents(chunks))

        assert result == {
            "embeddings_generated": 1_200,
            "embeddings_reused": 0,
        }
        assert unchanged == {
            "embeddings_generated": 0,
            "embeddings_reused": 1_200,
        }
        assert cold_batch_sizes == [1]
        assert warm_batch_sizes == [500, 500, 200]
        assert collection.count() == 1_201
        snapshot = collection.get(
            where={
                "$and": [
                    {"source_id": "source_career"},
                    {"contextwiki_managed": "true"},
                ]
            },
            include=["metadatas"],
        )
        metadata_by_chunk = {
            metadata["chunk_id"]: metadata
            for metadata in (snapshot.get("metadatas") or [])
        }
        assert set(metadata_by_chunk) == {
            "seed-chunk",
            *(f"bulk-chunk-{index}" for index in range(1_200)),
        }
        assert (
            metadata_by_chunk["bulk-chunk-777"]["evidence_source_type"] == "career_note"
        )
        assert metadata_by_chunk["bulk-chunk-777"]["experience_type"] == "professional"
        assert metadata_by_chunk["bulk-chunk-777"]["content_hash"] == (
            ContentHasher.hash_content("Synthetic bulk evidence 777.")
        )
    finally:
        Settings.embed_model = previous_embed_model


def test_real_chroma_large_prechunked_passages_have_one_stable_vector_each(
    tmp_path,
):
    previous_embed_model = Settings.embed_model
    Settings.embed_model = MockEmbedding(embed_dim=8)
    try:
        config = AppConfig(
            chroma_db_path=tmp_path / "chroma",
            metadata_db_path=tmp_path / "metadata.sqlite3",
            collection_name="career_prechunked_identity_e2e",
            batch_size=500,
        )
        collection = setup_chroma(config)
        storage_context = StorageContext.from_defaults(
            vector_store=ChromaVectorStore(chroma_collection=collection)
        )
        indexer = ContentIndexer(config, collection, storage_context)
        cold = DocumentModel(
            id="career-large-cold",
            chunk_id="career-large-cold",
            document_id="career:large-cold",
            source_id="source_career",
            title="Large multilingual evidence",
            content=("Kubernetes reliability 개선 성과. " * 2_000),
            url="career://large-cold.md",
            platform="career",
            evidence_source_type=EvidenceSourceType.CAREER_NOTE,
            experience_type=ExperienceType.PROFESSIONAL,
        )
        warm = DocumentModel(
            id="career-large-warm",
            chunk_id="career-large-warm",
            document_id="career:large-warm",
            source_id="source_career",
            title="Large second evidence",
            content=("Async queue latency 개선 evidence. " * 2_000),
            url="career://large-warm.md",
            platform="career",
            evidence_source_type=EvidenceSourceType.CAREER_NOTE,
            experience_type=ExperienceType.PROFESSIONAL,
        )

        cold_result = asyncio.run(indexer.index_documents([cold]))
        warm_result = asyncio.run(indexer.index_documents([warm]))
        unchanged_result = asyncio.run(indexer.index_documents([cold, warm]))

        assert cold_result == {"embeddings_generated": 1, "embeddings_reused": 0}
        assert warm_result == {"embeddings_generated": 1, "embeddings_reused": 0}
        assert unchanged_result == {
            "embeddings_generated": 0,
            "embeddings_reused": 2,
        }
        assert collection.count() == 2
        snapshot = collection.get(include=["metadatas"])
        assert set(snapshot["ids"]) == {"career-large-cold", "career-large-warm"}
        assert {metadata["chunk_id"] for metadata in snapshot["metadatas"]} == {
            "career-large-cold",
            "career-large-warm",
        }
    finally:
        Settings.embed_model = previous_embed_model


def _call_search_evidence(mcp: FastMCP, arguments: dict) -> list[dict]:
    blocks = asyncio.run(mcp.call_tool("search_evidence", arguments))
    return json.loads(blocks[0].text)


def test_manifest_all_formats_to_fastmcp_search_evidence_uses_sqlite_authority(
    tmp_path,
):
    career_root = tmp_path / "career"
    career_root.mkdir()
    (career_root / "resume.md").write_text(
        "# Work experience\n"
        "## Example Systems\n"
        "### Platform modernization and reliability\n"
        "Improved Kubernetes rollout reliability by 40%.\n",
        encoding="utf-8",
    )
    write_minimal_pdf(
        career_root / "previous-resume.pdf",
        [
            "Previous Resume Evidence",
            "Improved PDF reliability safeguards.",
        ],
    )
    write_minimal_docx(
        career_root / "project.docx",
        [
            ("Title", "Scheduler Prototype"),
            ("Heading1", "Projects"),
            ("Heading2", "Scheduler"),
            ("", "Built a deterministic scheduler prototype."),
        ],
    )
    (career_root / "skills.txt").write_text(
        "Python and SQLite skills inventory.",
        encoding="utf-8",
    )
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [
                    {
                        "path": "resume.md",
                        "source_type": "resume",
                        "experience_type": "professional",
                        "company": "Example Systems",
                        "role": "Backend Engineer",
                    },
                    {
                        "path": "previous-resume.pdf",
                        "source_type": "previous_resume",
                        "experience_type": "professional",
                    },
                    {
                        "path": "project.docx",
                        "source_type": "project",
                        "experience_type": "personal_project",
                        "project": "Scheduler Prototype",
                    },
                    {
                        "path": "skills.txt",
                        "source_type": "skills_inventory",
                        "experience_type": "unknown",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    connector = CareerSourceConnector(config)
    store = MetadataStore(config.metadata_db_path)
    indexer = DeterministicEmbeddingIndex()
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=indexer,
    )

    first = asyncio.run(ingestion.sync_source("source_career"))
    unchanged = asyncio.run(ingestion.sync_source("source_career"))

    assert first.status == SyncJobStatus.SUCCEEDED
    assert first.parsed_documents == 4
    assert unchanged.status == SyncJobStatus.SUCCEEDED
    assert unchanged.skipped_documents == 4
    assert unchanged.embeddings_generated == 0
    assert indexer.index_calls == 4

    documents = store.list_documents()["documents"]
    assert len(documents) == 4
    stored_documents = {
        document.file_name: document
        for item in documents
        if (document := store.get_document(item.document_id)) is not None
    }
    stored_chunks = {
        file_name: store.list_chunks_for_document(document.document_id)
        for file_name, document in stored_documents.items()
    }
    total_chunks = sum(len(chunks) for chunks in stored_chunks.values())
    assert first.embeddings_generated == total_chunks
    assert unchanged.embeddings_reused == total_chunks

    markdown_target = next(
        chunk
        for chunk in stored_chunks["resume.md"]
        if chunk.section_title == "Platform modernization and reliability"
    )
    pdf_target = stored_chunks["previous-resume.pdf"][0]
    docx_target = next(
        chunk
        for chunk in stored_chunks["project.docx"]
        if chunk.section_title == "Scheduler"
    )
    text_target = stored_chunks["skills.txt"][0]
    assert all(
        chunk.exact_quote == chunk.text
        for chunk in (markdown_target, pdf_target, docx_target, text_target)
    )

    context_search = ContextSearchService(
        metadata_store=store,
        config=config,
        retriever=list(indexer.documents.values()),
        default_source_ids=("source_career",),
    )
    evidence_search = EvidenceSearchService(
        context_search_service=context_search,
        metadata_store=store,
    )
    mcp = FastMCP("integrated-career-evidence-e2e")
    register_tools(mcp, evidence_search_service=evidence_search)

    cases = [
        (
            "Kubernetes rollout reliability",
            "resume",
            "professional",
            markdown_target,
            "Example Systems",
        ),
        (
            "PDF reliability safeguards",
            "previous_resume",
            "professional",
            pdf_target,
            None,
        ),
        (
            "deterministic scheduler prototype",
            "project",
            "personal_project",
            docx_target,
            "Projects",
        ),
        (
            "Python SQLite skills inventory",
            "skills_inventory",
            "unknown",
            text_target,
            None,
        ),
    ]
    for query, source_type, experience_type, target, expected_parent in cases:
        document = stored_documents[target.file_name]
        response = _call_search_evidence(
            mcp,
            {
                "query": query,
                "source_types": [source_type],
                "experience_types": [experience_type],
                "document_ids": [document.document_id],
                "top_k": 1,
            },
        )

        assert len(response) == 1
        assert response[0]["chunk_id"] == target.chunk_id
        assert response[0]["document_id"] == document.document_id
        assert response[0]["document_version_id"] == document.document_version_id
        assert response[0]["source_type"] == source_type
        assert response[0]["experience_type"] == experience_type
        assert response[0]["section_title"] == (target.section_title or None)
        assert response[0]["parent_section_title"] == expected_parent
        assert response[0]["exact_quote"] == target.text
        assert response[0]["file_name"] == target.file_name


def test_symlinked_manifest_ancestor_fails_closed_before_indexing(tmp_path):
    real_parent = tmp_path / "real-parent"
    career_root = real_parent / "career"
    career_root.mkdir(parents=True)
    (career_root / "resume.md").write_text(
        "# Evidence\nApproved synthetic career evidence.\n",
        encoding="utf-8",
    )
    manifest = real_parent / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [
                    {
                        "path": "resume.md",
                        "source_type": "resume",
                        "experience_type": "professional",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=linked_parent / manifest.name,
    )
    connector = CareerSourceConnector(config)
    store = MetadataStore(config.metadata_db_path)
    indexer = DeterministicEmbeddingIndex()
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=indexer,
    )

    failed = asyncio.run(ingestion.sync_source("source_career"))

    assert failed.status == SyncJobStatus.FAILED
    assert indexer.index_calls == 0
    assert store.list_documents()["documents"] == []
    assert connector.supports_stale_cleanup is False


def test_duplicate_physical_manifest_file_fails_snapshot_before_indexing(tmp_path):
    career_root = tmp_path / "career"
    career_root.mkdir()
    original = career_root / "resume.md"
    original.write_text(
        "# Evidence\nApproved synthetic career evidence.\n",
        encoding="utf-8",
    )
    os.link(original, career_root / "resume-hardlink.md")
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [
                    {
                        "path": "resume.md",
                        "source_type": "resume",
                        "document_id": "resume-primary",
                    },
                    {
                        "path": "resume-hardlink.md",
                        "source_type": "previous_resume",
                        "document_id": "resume-duplicate",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    connector = CareerSourceConnector(config)
    store = MetadataStore(config.metadata_db_path)
    indexer = DeterministicEmbeddingIndex()
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=indexer,
    )

    failed = asyncio.run(ingestion.sync_source("source_career"))

    assert failed.status == SyncJobStatus.FAILED
    assert indexer.index_calls == 0
    assert store.list_documents()["documents"] == []
    assert connector.supports_stale_cleanup is False


def test_manifest_parent_replacement_after_read_fails_snapshot_before_indexing(
    monkeypatch,
    tmp_path,
):
    approved_parent = tmp_path / "approved-parent"
    career_root = approved_parent / "career"
    career_root.mkdir(parents=True)
    (career_root / "resume.md").write_text(
        "# Evidence\nApproved synthetic career evidence.\n",
        encoding="utf-8",
    )
    manifest = approved_parent / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [{"path": "resume.md", "source_type": "resume"}],
            }
        ),
        encoding="utf-8",
    )
    moved_parent = tmp_path / "approved-parent-before-swap"
    original_loads = career_files_module.json.loads
    swapped = False

    def swap_parent_after_manifest_read(value):
        nonlocal swapped
        payload = original_loads(value)
        approved_parent.rename(moved_parent)
        replacement_root = approved_parent / "career"
        replacement_root.mkdir(parents=True)
        (replacement_root / "resume.md").write_text(
            "# Evidence\nAttacker-controlled synthetic content.\n",
            encoding="utf-8",
        )
        swapped = True
        return payload

    monkeypatch.setattr(
        career_files_module.json, "loads", swap_parent_after_manifest_read
    )
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    connector = CareerSourceConnector(config)
    store = MetadataStore(config.metadata_db_path)
    indexer = DeterministicEmbeddingIndex()
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=indexer,
    )

    failed = asyncio.run(ingestion.sync_source("source_career"))

    assert swapped is True
    assert failed.status == SyncJobStatus.FAILED
    assert indexer.index_calls == 0
    assert store.list_documents()["documents"] == []
    assert connector.supports_stale_cleanup is False


def test_in_place_document_mutation_during_read_fails_snapshot_before_indexing(
    monkeypatch,
    tmp_path,
):
    career_root = tmp_path / "career"
    career_root.mkdir()
    document_path = career_root / "resume.md"
    document_path.write_text(
        "# Evidence\nApproved synthetic career evidence.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [{"path": "resume.md", "source_type": "resume"}],
            }
        ),
        encoding="utf-8",
    )
    document_stat = document_path.stat()
    document_identity = (document_stat.st_dev, document_stat.st_ino)
    original_fdopen = career_files_module.os.fdopen
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
            document_path.write_text(
                "# Evidence\nAttacker-controlled synthetic content after read.\n",
                encoding="utf-8",
            )
            mutated = True
            return raw

    def mutating_fdopen(descriptor, *args, **kwargs):
        handle = original_fdopen(descriptor, *args, **kwargs)
        descriptor_stat = os.fstat(handle.fileno())
        if (descriptor_stat.st_dev, descriptor_stat.st_ino) == document_identity:
            return MutatingReader(handle)
        return handle

    monkeypatch.setattr(career_files_module.os, "fdopen", mutating_fdopen)
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    connector = CareerSourceConnector(config)
    store = MetadataStore(config.metadata_db_path)
    indexer = DeterministicEmbeddingIndex()
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=indexer,
    )

    failed = asyncio.run(ingestion.sync_source("source_career"))

    assert mutated is True
    assert failed.status == SyncJobStatus.FAILED
    assert indexer.index_calls == 0
    assert store.list_documents()["documents"] == []
    assert connector.supports_stale_cleanup is False


def test_earlier_document_mutation_while_later_file_loads_fails_before_indexing(
    monkeypatch,
    tmp_path,
):
    career_root = tmp_path / "career"
    career_root.mkdir()
    first_path = career_root / "first.md"
    first_path.write_text(
        "# Evidence\nFirst approved synthetic career evidence.\n",
        encoding="utf-8",
    )
    (career_root / "second.md").write_text(
        "# Evidence\nSecond approved synthetic career evidence.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [
                    {"path": "first.md", "source_type": "career_note"},
                    {"path": "second.md", "source_type": "career_note"},
                ],
            }
        ),
        encoding="utf-8",
    )
    original_read_file = career_files_module.CareerDocumentParser.read_file
    mutated = False

    def mutate_first_during_second_read(self, requested_path):
        nonlocal mutated
        loaded = original_read_file(self, requested_path)
        if Path(requested_path).name == "second.md":
            first_path.write_text(
                "# Evidence\nChanged synthetic content after first file read.\n",
                encoding="utf-8",
            )
            mutated = True
        return loaded

    monkeypatch.setattr(
        career_files_module.CareerDocumentParser,
        "read_file",
        mutate_first_during_second_read,
    )
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    connector = CareerSourceConnector(config)
    store = MetadataStore(config.metadata_db_path)
    indexer = DeterministicEmbeddingIndex()
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=indexer,
    )

    failed = asyncio.run(ingestion.sync_source("source_career"))

    assert mutated is True
    assert failed.status == SyncJobStatus.FAILED
    assert indexer.index_calls == 0
    assert store.list_documents()["documents"] == []
    assert connector.supports_stale_cleanup is False


def test_taxonomy_only_manifest_update_is_retrievable_under_new_vector_filter(
    monkeypatch,
    tmp_path,
):
    career_root = tmp_path / "career"
    career_root.mkdir()
    (career_root / "evidence.md").write_text(
        "# Experience\nBuilt a reliable queue.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "career-manifest.json"

    def write_manifest(source_type: str, experience_type: str) -> None:
        manifest.write_text(
            json.dumps(
                {
                    "root": "career",
                    "documents": [
                        {
                            "path": "evidence.md",
                            "source_type": source_type,
                            "experience_type": experience_type,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    write_manifest("resume", "professional")
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    monkeypatch.setattr(Settings, "embed_model", MockEmbedding(embed_dim=8))
    store = MetadataStore(config.metadata_db_path)
    collection = setup_chroma(config)
    storage_context = StorageContext.from_defaults(
        vector_store=ChromaVectorStore(chroma_collection=collection)
    )
    indexer = ContentIndexer(config, collection, storage_context)
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([CareerSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=indexer,
    )

    first = asyncio.run(ingestion.sync_source("source_career"))
    first_document = store.list_documents()["documents"][0]
    first_chunk_ids = {
        chunk.chunk_id
        for chunk in store.list_chunks_for_document(first_document.document_id)
    }
    write_manifest("career_note", "academic")
    updated = asyncio.run(ingestion.sync_source("source_career"))
    updated_documents = store.list_documents()["documents"]
    updated_document = updated_documents[0]
    updated_chunk_ids = {
        chunk.chunk_id
        for chunk in store.list_chunks_for_document(updated_document.document_id)
    }
    write_manifest("resume", "professional")

    def fail_commit(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("injected SQLite commit failure")

    monkeypatch.setattr(ingestion, "_commit_chunks_or_current", fail_commit)
    failed = asyncio.run(ingestion.sync_source("source_career"))

    context_search = ContextSearchService(
        metadata_store=store,
        config=config,
        indexer=indexer,
        default_source_ids=("source_career",),
    )
    evidence_search = EvidenceSearchService(
        context_search_service=context_search,
        metadata_store=store,
    )
    mcp = FastMCP("taxonomy-update-e2e")
    register_tools(mcp, evidence_search_service=evidence_search)
    new_taxonomy = _call_search_evidence(
        mcp,
        {
            "query": "reliable queue",
            "source_types": ["career_note"],
            "experience_types": ["academic"],
            "top_k": 1,
        },
    )
    old_taxonomy = _call_search_evidence(
        mcp,
        {
            "query": "reliable queue",
            "source_types": ["resume"],
            "experience_types": ["professional"],
            "top_k": 1,
        },
    )

    assert first.embeddings_generated > 0
    assert updated.embeddings_generated == 0
    assert updated.embeddings_reused == first.embeddings_generated
    assert failed.status == SyncJobStatus.FAILED
    assert len(updated_documents) == 1
    assert updated_document.document_id == first_document.document_id
    assert updated_chunk_ids == first_chunk_ids
    assert len(new_taxonomy) == 1
    assert new_taxonomy[0]["source_type"] == "career_note"
    assert new_taxonomy[0]["experience_type"] == "academic"
    assert old_taxonomy == []


def test_interrupted_taxonomy_refresh_repairs_real_chroma_on_unchanged_retry(
    monkeypatch,
    tmp_path,
):
    career_root = tmp_path / "career"
    career_root.mkdir()
    (career_root / "evidence.md").write_text(
        "# Experience\nBuilt a reliable queue.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "career-manifest.json"

    def write_manifest(source_type: str, experience_type: str) -> None:
        manifest.write_text(
            json.dumps(
                {
                    "root": "career",
                    "documents": [
                        {
                            "path": "evidence.md",
                            "source_type": source_type,
                            "experience_type": experience_type,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    write_manifest("resume", "professional")
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    monkeypatch.setattr(Settings, "embed_model", MockEmbedding(embed_dim=8))
    store = MetadataStore(config.metadata_db_path)
    collection = setup_chroma(config)
    storage_context = StorageContext.from_defaults(
        vector_store=ChromaVectorStore(chroma_collection=collection)
    )
    indexer = ContentIndexer(config, collection, storage_context)
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([CareerSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=indexer,
    )

    first = asyncio.run(ingestion.sync_source("source_career"))
    write_manifest("career_note", "academic")
    original_commit = ingestion._commit_chunks_or_current

    def interrupt_after_vector_refresh(*args, **kwargs):
        del args, kwargs
        raise SimulatedWorkerTermination("simulated interruption before SQLite commit")

    monkeypatch.setattr(
        ingestion,
        "_commit_chunks_or_current",
        interrupt_after_vector_refresh,
    )
    with pytest.raises(SimulatedWorkerTermination):
        asyncio.run(ingestion.sync_source("source_career"))

    interrupted = store.get_latest_sync_job("source_career")
    assert interrupted is not None
    interrupted_document = store.list_documents()["documents"][0]
    interrupted_chunk_ids = [
        chunk.chunk_id
        for chunk in store.list_chunks_for_document(interrupted_document.document_id)
    ]
    assert (
        store.list_pending_vector_metadata_refresh_ids(
            "source_career",
            document_id=interrupted_document.document_id,
        )
        == interrupted_chunk_ids
    )
    store.complete_failed_sync(
        job_id=interrupted.job_id,
        source_id="source_career",
        error_message="worker interrupted",
    )
    monkeypatch.setattr(ingestion, "_commit_chunks_or_current", original_commit)
    write_manifest("resume", "professional")

    recovered = asyncio.run(ingestion.sync_source("source_career"))

    context_search = ContextSearchService(
        metadata_store=store,
        config=config,
        indexer=indexer,
        default_source_ids=("source_career",),
    )
    evidence_search = EvidenceSearchService(
        context_search_service=context_search,
        metadata_store=store,
    )
    mcp = FastMCP("taxonomy-interruption-e2e")
    register_tools(mcp, evidence_search_service=evidence_search)
    restored_taxonomy = _call_search_evidence(
        mcp,
        {
            "query": "reliable queue",
            "source_types": ["resume"],
            "experience_types": ["professional"],
            "top_k": 1,
        },
    )
    interrupted_taxonomy = _call_search_evidence(
        mcp,
        {
            "query": "reliable queue",
            "source_types": ["career_note"],
            "experience_types": ["academic"],
            "top_k": 1,
        },
    )

    assert first.status == SyncJobStatus.SUCCEEDED
    assert recovered.status == SyncJobStatus.SUCCEEDED
    assert recovered.embeddings_generated == 0
    assert (
        store.list_pending_vector_metadata_refresh_ids(
            "source_career",
            document_id=interrupted_document.document_id,
        )
        == []
    )
    assert len(restored_taxonomy) == 1
    assert restored_taxonomy[0]["source_type"] == "resume"
    assert restored_taxonomy[0]["experience_type"] == "professional"
    assert interrupted_taxonomy == []


def test_reactivated_same_hash_real_chroma_refreshes_taxonomy_filters(
    monkeypatch,
    tmp_path,
):
    career_root = tmp_path / "career"
    career_root.mkdir()
    (career_root / "evidence.md").write_text(
        "# Experience\nBuilt a reliable queue.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "career-manifest.json"

    def write_manifest(
        source_type: str | None,
        experience_type: str | None,
    ) -> None:
        documents = []
        if source_type is not None and experience_type is not None:
            documents.append(
                {
                    "path": "evidence.md",
                    "source_type": source_type,
                    "experience_type": experience_type,
                }
            )
        manifest.write_text(
            json.dumps({"root": "career", "documents": documents}),
            encoding="utf-8",
        )

    write_manifest("resume", "professional")
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    monkeypatch.setattr(Settings, "embed_model", MockEmbedding(embed_dim=8))
    store = MetadataStore(config.metadata_db_path)
    collection = setup_chroma(config)
    storage_context = StorageContext.from_defaults(
        vector_store=ChromaVectorStore(chroma_collection=collection)
    )
    indexer = ContentIndexer(config, collection, storage_context)
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([CareerSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=indexer,
    )

    first = asyncio.run(ingestion.sync_source("source_career"))
    first_document = store.list_documents()["documents"][0]
    first_chunk_ids = [
        chunk.chunk_id
        for chunk in store.list_chunks_for_document(first_document.document_id)
    ]
    original_delete = indexer.delete_documents_by_ids
    delete_attempts = 0

    async def fail_cleanup_once(document_ids, source_id=""):
        nonlocal delete_attempts
        delete_attempts += 1
        if delete_attempts == 1:
            raise RuntimeError("injected vector cleanup failure")
        return await original_delete(document_ids, source_id=source_id)

    monkeypatch.setattr(indexer, "delete_documents_by_ids", fail_cleanup_once)
    write_manifest(None, None)
    deleted = asyncio.run(ingestion.sync_source("source_career"))
    write_manifest("career_note", "academic")

    reactivated = asyncio.run(ingestion.sync_source("source_career"))

    context_search = ContextSearchService(
        metadata_store=store,
        config=config,
        indexer=indexer,
        default_source_ids=("source_career",),
    )
    evidence_search = EvidenceSearchService(
        context_search_service=context_search,
        metadata_store=store,
    )
    mcp = FastMCP("taxonomy-reactivation-e2e")
    register_tools(mcp, evidence_search_service=evidence_search)
    reactivated_taxonomy = _call_search_evidence(
        mcp,
        {
            "query": "reliable queue",
            "source_types": ["career_note"],
            "experience_types": ["academic"],
            "top_k": 1,
        },
    )
    stale_taxonomy = _call_search_evidence(
        mcp,
        {
            "query": "reliable queue",
            "source_types": ["resume"],
            "experience_types": ["professional"],
            "top_k": 1,
        },
    )

    reactivated_chunks = store.list_chunks_for_document(first_document.document_id)
    assert first.status == SyncJobStatus.SUCCEEDED
    assert deleted.status == SyncJobStatus.SUCCEEDED
    assert reactivated.status == SyncJobStatus.SUCCEEDED
    assert reactivated.embeddings_generated == 0
    assert reactivated.embeddings_reused == len(first_chunk_ids)
    assert [chunk.chunk_id for chunk in reactivated_chunks] == first_chunk_ids
    assert len(reactivated_taxonomy) == 1
    assert reactivated_taxonomy[0]["source_type"] == "career_note"
    assert reactivated_taxonomy[0]["experience_type"] == "academic"
    assert stale_taxonomy == []


def test_indexing_latency_excludes_controlled_connector_fetch_delay(tmp_path):
    career_root = tmp_path / "career"
    career_root.mkdir()
    (career_root / "skills.txt").write_text(
        "Python and SQLite skills inventory.",
        encoding="utf-8",
    )
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [
                    {
                        "path": "skills.txt",
                        "source_type": "skills_inventory",
                        "experience_type": "unknown",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    fetch_delay_seconds = 0.25
    ingestion = IngestionService(
        metadata_store=MetadataStore(config.metadata_db_path),
        source_registry=SourceRegistry(
            [
                DelayedCareerSourceConnector(
                    config,
                    delay_seconds=fetch_delay_seconds,
                )
            ]
        ),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=DeterministicEmbeddingIndex(),
    )

    started_at = time.perf_counter()
    job = asyncio.run(ingestion.sync_source("source_career"))
    elapsed_ms = (time.perf_counter() - started_at) * 1000

    assert job.status == SyncJobStatus.SUCCEEDED
    assert elapsed_ms >= fetch_delay_seconds * 1000
    assert 0 < job.indexing_latency_ms < fetch_delay_seconds * 500


def test_career_parser_polling_throttles_durable_touch_off_event_loop(
    monkeypatch,
    tmp_path,
):
    career_root = tmp_path / "career"
    career_root.mkdir()
    (career_root / "resume.md").write_text(
        "# Synthetic Resume\n\n## Experience\nBuilt bounded ingestion.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [
                    {
                        "path": "resume.md",
                        "source_type": "resume",
                        "experience_type": "professional",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    parser_active = threading.Event()
    original_load = connector_module.load_career_manifest

    def delayed_load(*args, cancel_check=None, **kwargs):
        parser_active.set()
        try:
            deadline = time.monotonic() + 0.65
            while time.monotonic() < deadline:
                if cancel_check is not None and cancel_check():
                    break
                time.sleep(0.005)
            return original_load(*args, cancel_check=cancel_check, **kwargs)
        finally:
            parser_active.clear()

    monkeypatch.setattr(connector_module, "load_career_manifest", delayed_load)

    class SlowTouchMetadataStore(MetadataStore):
        def __init__(self, db_path):
            super().__init__(db_path)
            self.active_touch_threads: list[int] = []

        def touch_sync_job(self, job_id: str):
            if parser_active.is_set():
                self.active_touch_threads.append(threading.get_ident())
                time.sleep(0.075)
            return super().touch_sync_job(job_id)

    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    store = SlowTouchMetadataStore(config.metadata_db_path)
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([CareerSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=DeterministicEmbeddingIndex(),
    )

    async def run_sync():
        event_loop_thread = threading.get_ident()
        heartbeat_ticks = 0
        running = True

        async def heartbeat():
            nonlocal heartbeat_ticks
            while running:
                heartbeat_ticks += 1
                await asyncio.sleep(0.01)

        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            job = await ingestion.sync_source("source_career")
        finally:
            running = False
            await heartbeat_task
        return job, event_loop_thread, heartbeat_ticks

    job, event_loop_thread, heartbeat_ticks = asyncio.run(run_sync())

    assert job.status == SyncJobStatus.SUCCEEDED
    assert 1 <= len(store.active_touch_threads) <= 2
    assert all(
        thread_id != event_loop_thread for thread_id in store.active_touch_threads
    )
    assert heartbeat_ticks >= 50


def test_terminal_career_metrics_survive_every_progress_write_failure(tmp_path):
    career_root = tmp_path / "career"
    career_root.mkdir()
    (career_root / "metrics.md").write_text(
        "# Evidence\nBuilt durable ingestion metrics.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [
                    {
                        "path": "metrics.md",
                        "source_type": "career_note",
                        "experience_type": "professional",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    ingestion = IngestionService(
        metadata_store=ProgressWriteFailingMetadataStore(config.metadata_db_path),
        source_registry=SourceRegistry([CareerSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=DeterministicEmbeddingIndex(),
    )

    completed = asyncio.run(ingestion.sync_source("source_career"))

    assert completed.status == SyncJobStatus.SUCCEEDED
    assert completed.parsed_documents == 1
    assert completed.created_chunks == 1
    assert completed.embeddings_generated == 1
    assert completed.indexing_latency_ms > 0


def test_failed_terminal_career_metrics_survive_every_progress_write_failure(tmp_path):
    career_root = tmp_path / "career"
    career_root.mkdir()
    (career_root / "metrics.md").write_text(
        "# Evidence\nBuilt durable failure metrics.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [
                    {
                        "path": "metrics.md",
                        "source_type": "career_note",
                        "experience_type": "professional",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    store = ProgressWriteFailingMetadataStore(config.metadata_db_path)
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([CareerSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=ParsingFailingEmbeddingIndex(),
    )

    failed = asyncio.run(ingestion.sync_source("source_career"))
    persisted = store.get_sync_job(failed.job_id)

    assert failed.status == SyncJobStatus.FAILED
    assert failed.total_documents == 1
    assert failed.parsed_documents == 1
    assert failed.parsing_failures == 1
    assert failed.indexing_latency_ms > 0
    assert persisted is not None
    assert persisted.parsing_failures == 1
    assert persisted.indexing_latency_ms == failed.indexing_latency_ms


def test_partial_manifest_parse_failure_is_atomic_and_retry_is_idempotent(tmp_path):
    career_root = tmp_path / "career"
    career_root.mkdir()
    (career_root / "first.txt").write_text("First evidence.", encoding="utf-8")
    invalid_file = career_root / "private-invalid.txt"
    invalid_file.write_bytes(b"\xffprivate-content")
    (career_root / "third.txt").write_text("Third evidence.", encoding="utf-8")
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [
                    {"path": "first.txt", "source_type": "career_note"},
                    {"path": "private-invalid.txt", "source_type": "career_note"},
                    {"path": "third.txt", "source_type": "career_note"},
                ],
            }
        ),
        encoding="utf-8",
    )
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    store = ProgressWriteFailingMetadataStore(config.metadata_db_path)
    indexer = DeterministicEmbeddingIndex()
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([CareerSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=indexer,
    )

    first_failure = asyncio.run(ingestion.sync_source("source_career"))
    second_failure = asyncio.run(ingestion.sync_source("source_career"))

    for failed in (first_failure, second_failure):
        assert failed.status == SyncJobStatus.FAILED
        assert failed.total_documents == 2
        assert failed.parsed_documents == 1
        assert failed.parsing_failures == 1
        assert failed.indexing_latency_ms == 0.0
        assert failed.processed_documents == 0
        assert "private-invalid.txt" not in failed.error_message
        assert "private-content" not in failed.error_message
    assert indexer.index_calls == 0
    assert indexer.documents == {}

    invalid_file.write_text("Second evidence.", encoding="utf-8")
    completed = asyncio.run(ingestion.sync_source("source_career"))

    assert completed.status == SyncJobStatus.SUCCEEDED
    assert completed.total_documents == 3
    assert completed.parsed_documents == 3
    assert completed.parsing_failures == 0
    assert len(indexer.documents) == 3
    assert store.get_sync_job(first_failure.job_id) == first_failure
    assert store.get_sync_job(second_failure.job_id) == second_failure


def test_abrupt_vector_write_interruption_leaves_cleanup_intent_for_next_sync(tmp_path):
    career_root = tmp_path / "career"
    career_root.mkdir()
    (career_root / "interrupted.md").write_text(
        "# Evidence\nThis vector write is interrupted.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "career-manifest.json"

    def write_manifest(documents):
        manifest.write_text(
            json.dumps({"root": "career", "documents": documents}),
            encoding="utf-8",
        )

    write_manifest(
        [
            {
                "path": "interrupted.md",
                "source_type": "career_note",
                "experience_type": "professional",
            }
        ]
    )
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    store = MetadataStore(config.metadata_db_path)
    indexer = HardCrashAfterWriteEmbeddingIndex()
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([CareerSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=indexer,
    )

    with pytest.raises(SimulatedWorkerTermination):
        asyncio.run(ingestion.sync_source("source_career"))

    interrupted = store.get_latest_sync_job("source_career")
    assert interrupted is not None
    orphan_chunk_id = next(iter(indexer.documents))
    assert store.list_pending_vector_cleanup_ids("source_career") == [orphan_chunk_id]
    store.complete_failed_sync(
        job_id=interrupted.job_id,
        source_id="source_career",
        error_message="worker interrupted",
    )
    write_manifest([])

    recovered = asyncio.run(ingestion.sync_source("source_career"))

    assert recovered.status == SyncJobStatus.SUCCEEDED
    assert orphan_chunk_id not in indexer.documents
    assert store.list_pending_vector_cleanup_ids("source_career") == []


def test_failed_cleanup_retries_on_unchanged_sync_before_evidence_retrieval(tmp_path):
    career_root = tmp_path / "career"
    career_root.mkdir()
    (career_root / "stale.md").write_text(
        "# Evidence\nStale queue evidence.\n",
        encoding="utf-8",
    )
    (career_root / "retained.md").write_text(
        "# Evidence\nRetained queue evidence.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "career-manifest.json"

    def write_manifest(paths):
        manifest.write_text(
            json.dumps(
                {
                    "root": "career",
                    "documents": [
                        {
                            "path": path,
                            "source_type": "career_note",
                            "experience_type": "professional",
                        }
                        for path in paths
                    ],
                }
            ),
            encoding="utf-8",
        )

    write_manifest(["stale.md", "retained.md"])
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    store = MetadataStore(config.metadata_db_path)
    indexer = FailingOnceDeleteEmbeddingIndex()
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([CareerSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=indexer,
    )

    asyncio.run(ingestion.sync_source("source_career"))
    stale_chunk_id = next(
        chunk.chunk_id
        for chunk in store.list_chunks(["source_career"])
        if chunk.file_name == "stale.md"
    )
    write_manifest(["retained.md"])
    asyncio.run(ingestion.sync_source("source_career"))
    retried = asyncio.run(ingestion.sync_source("source_career"))

    context_search = ContextSearchService(
        metadata_store=store,
        config=config,
        retriever=list(indexer.documents.values()),
        default_source_ids=("source_career",),
    )
    evidence_search = EvidenceSearchService(
        context_search_service=context_search,
        metadata_store=store,
    )
    mcp = FastMCP("cleanup-retry-e2e")
    register_tools(mcp, evidence_search_service=evidence_search)
    response = _call_search_evidence(
        mcp,
        {
            "query": "retained queue evidence",
            "source_types": ["career_note"],
            "experience_types": ["professional"],
            "top_k": 1,
        },
    )

    assert retried.status == SyncJobStatus.SUCCEEDED
    assert indexer.delete_attempts == 2
    assert stale_chunk_id not in indexer.documents
    assert len(response) == 1
    assert response[0]["file_name"] == "retained.md"


def test_successful_sync_drains_more_than_pending_page_before_retrieval(
    monkeypatch,
    tmp_path,
):
    career_root = tmp_path / "career"
    career_root.mkdir()
    (career_root / "retained.md").write_text(
        "# Evidence\nRetained large-cleanup evidence.\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [
                    {
                        "path": "retained.md",
                        "source_type": "career_note",
                        "experience_type": "professional",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )
    store = MetadataStore(config.metadata_db_path)
    indexer = DeterministicEmbeddingIndex()
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([CareerSourceConnector(config)]),
        chunker=DocumentChunker(max_chars=500, overlap_chars=0),
        indexer=indexer,
    )
    asyncio.run(ingestion.sync_source("source_career"))
    stale_ids = [f"stale-chunk-{index}" for index in range(5_501)]
    for chunk_id in stale_ids:
        indexer.documents[chunk_id] = object()
        indexer.hashes[chunk_id] = "stale"
    complete_successful_sync = store.complete_successful_sync

    def complete_with_large_cleanup(**kwargs):
        job, _deleted_ids = complete_successful_sync(**kwargs)
        return job, stale_ids

    monkeypatch.setattr(store, "complete_successful_sync", complete_with_large_cleanup)
    completed = asyncio.run(ingestion.sync_source("source_career"))

    assert completed.status == SyncJobStatus.SUCCEEDED
    assert all(chunk_id not in indexer.documents for chunk_id in stale_ids)
    context_search = ContextSearchService(
        metadata_store=store,
        config=config,
        retriever=list(indexer.documents.values()),
        default_source_ids=("source_career",),
    )
    evidence_search = EvidenceSearchService(
        context_search_service=context_search,
        metadata_store=store,
    )
    mcp = FastMCP("large-cleanup-e2e")
    register_tools(mcp, evidence_search_service=evidence_search)
    response = _call_search_evidence(
        mcp,
        {
            "query": "retained large cleanup evidence",
            "source_types": ["career_note"],
            "experience_types": ["professional"],
            "top_k": 1,
        },
    )

    assert len(response) == 1
    assert response[0]["file_name"] == "retained.md"
