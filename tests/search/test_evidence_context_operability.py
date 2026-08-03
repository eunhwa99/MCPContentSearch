import asyncio
import threading
import time

import pytest

from core.models import (
    ChunkModel,
    DocumentModel,
    SearchEvidenceInput,
    SourceModel,
    SourceType,
    SyncStatus,
)
from core.exceptions import EvidenceSearchError
from search.context_service import ContextSearchService
from search.evidence_service import EvidenceSearchService
from search.retrieval_pipeline import BoundedRetrievalExecutor
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.integration


class _EmptyEvidenceStore:
    @staticmethod
    def get_active_evidence_snapshots(chunk_ids):
        del chunk_ids
        return {}


class _LegacyHangingContextSearch:
    """Model an older context service without internal deadline support."""

    def __init__(self, *, timeout_seconds: float):
        self.retrieval_executor = BoundedRetrievalExecutor(
            timeout_seconds=timeout_seconds,
            max_concurrency=1,
        )
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def search_context(self, query, *, filters, top_k, candidate_budget):
        del query, filters, top_k, candidate_budget
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()


def test_evidence_search_maps_real_pipeline_deadline_to_sanitized_timeout(tmp_path):
    release = threading.Event()

    def blocking_retriever(query, top_k, source_ids):
        del query, top_k, source_ids
        release.wait(timeout=1)
        return []

    store = MetadataStore(tmp_path / "deadline.sqlite3")
    context_service = ContextSearchService(
        store,
        retriever=blocking_retriever,
        retrieval_timeout_seconds=0.02,
        retrieval_max_concurrency=1,
    )
    evidence_service = EvidenceSearchService(
        context_search_service=context_service,
        metadata_store=store,
    )
    timer = threading.Timer(0.1, release.set)
    timer.start()
    try:
        with pytest.raises(EvidenceSearchError) as exc_info:
            asyncio.run(
                evidence_service.search_evidence(
                    SearchEvidenceInput(
                        query="private deadline query must not be echoed",
                        top_k=1,
                    )
                )
            )
    finally:
        release.set()
        timer.cancel()
        timer.join(timeout=1)

    assert exc_info.value.error_type == "timeout"
    assert str(exc_info.value) == "Evidence retrieval timed out"
    assert "private deadline query" not in str(exc_info.value)


def test_legacy_context_search_is_bounded_by_evidence_absolute_deadline():
    context_service = _LegacyHangingContextSearch(timeout_seconds=0.02)
    evidence_service = EvidenceSearchService(
        context_search_service=context_service,
        metadata_store=_EmptyEvidenceStore(),
    )

    async def scenario():
        with pytest.raises(EvidenceSearchError) as exc_info:
            await asyncio.wait_for(
                evidence_service.search_evidence(
                    SearchEvidenceInput(
                        query="private legacy query must not be echoed",
                        top_k=1,
                    )
                ),
                timeout=0.3,
            )
        assert context_service.started.is_set()
        assert context_service.cancelled.is_set()
        return exc_info.value

    error = asyncio.run(scenario())

    assert error.error_type == "timeout"
    assert str(error) == "Evidence retrieval timed out"
    assert "private legacy query" not in str(error)


def test_authoritative_evidence_hydration_is_off_loop_and_shares_request_deadline(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "authoritative-hydration.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_career",
            source_type=SourceType.CAREER,
            name="Career files",
            sync_status=SyncStatus.IDLE,
        )
    )
    _seed_filtered_evidence_chunk(
        store,
        chunk_id="deadline-chunk",
        document_id="deadline-document",
        evidence_source_type="resume",
        experience_type="professional",
    )
    original_loader = store.get_active_evidence_snapshots
    release_hydration = threading.Event()
    hydration_started = threading.Event()
    loader_calls = 0

    def blocking_second_loader(chunk_ids):
        nonlocal loader_calls
        loader_calls += 1
        if loader_calls == 2:
            hydration_started.set()
            release_hydration.wait(timeout=1)
        return original_loader(chunk_ids)

    monkeypatch.setattr(
        store,
        "get_active_evidence_snapshots",
        blocking_second_loader,
    )

    def retriever(query, top_k, source_ids):
        del query, top_k, source_ids
        return [
            {
                "chunk_id": "deadline-chunk",
                "document_id": "deadline-document",
                "score": 0.9,
            }
        ]

    context_service = ContextSearchService(
        store,
        retriever=retriever,
        retrieval_timeout_seconds=0.05,
        retrieval_max_concurrency=1,
    )
    evidence_service = EvidenceSearchService(
        context_search_service=context_service,
        metadata_store=store,
    )

    async def scenario():
        started_at = time.perf_counter()
        heartbeat = asyncio.create_task(asyncio.sleep(0.02))
        search = asyncio.create_task(
            evidence_service.search_evidence(
                SearchEvidenceInput(
                    query="private hydration query must not be echoed",
                    top_k=1,
                )
            )
        )
        await heartbeat
        heartbeat_latency = time.perf_counter() - started_at
        assert await asyncio.to_thread(hydration_started.wait, 0.5)
        with pytest.raises(EvidenceSearchError) as exc_info:
            await search
        return heartbeat_latency, exc_info.value

    timer = threading.Timer(0.2, release_hydration.set)
    timer.start()
    try:
        heartbeat_latency, error = asyncio.run(scenario())
    finally:
        release_hydration.set()
        timer.cancel()
        timer.join(timeout=1)

    assert heartbeat_latency < 0.1
    assert error.error_type == "timeout"
    assert str(error) == "Evidence retrieval timed out"
    assert "private hydration query" not in str(error)
    assert loader_calls == 2


def test_cancelled_authoritative_hydration_keeps_shared_slot_until_worker_exits(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "cancelled-hydration.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_career",
            source_type=SourceType.CAREER,
            name="Career files",
            sync_status=SyncStatus.IDLE,
        )
    )
    _seed_filtered_evidence_chunk(
        store,
        chunk_id="cancel-chunk",
        document_id="cancel-document",
        evidence_source_type="resume",
        experience_type="professional",
    )
    original_loader = store.get_active_evidence_snapshots
    release_hydration = threading.Event()
    hydration_started = threading.Event()
    loader_calls = 0

    def blocking_second_loader(chunk_ids):
        nonlocal loader_calls
        loader_calls += 1
        if loader_calls == 2:
            hydration_started.set()
            release_hydration.wait(timeout=1)
        return original_loader(chunk_ids)

    monkeypatch.setattr(
        store,
        "get_active_evidence_snapshots",
        blocking_second_loader,
    )
    retriever_calls = 0
    retriever_lock = threading.Lock()

    def retriever(query, top_k, source_ids):
        nonlocal retriever_calls
        del query, top_k, source_ids
        with retriever_lock:
            retriever_calls += 1
        return [
            {
                "chunk_id": "cancel-chunk",
                "document_id": "cancel-document",
                "score": 0.9,
            }
        ]

    context_service = ContextSearchService(
        store,
        retriever=retriever,
        retrieval_timeout_seconds=1,
        retrieval_max_concurrency=1,
    )
    evidence_service = EvidenceSearchService(
        context_search_service=context_service,
        metadata_store=store,
    )
    request = SearchEvidenceInput(query="bounded cancellation", top_k=1)

    async def scenario():
        first = asyncio.create_task(evidence_service.search_evidence(request))
        assert await asyncio.to_thread(hydration_started.wait, 0.5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        second = asyncio.create_task(evidence_service.search_evidence(request))
        await asyncio.sleep(0.05)
        assert retriever_calls == 1

        release_hydration.set()
        results = await asyncio.wait_for(second, timeout=1)
        assert [item.chunk_id for item in results] == ["cancel-chunk"]

    try:
        asyncio.run(scenario())
    finally:
        release_hydration.set()

    assert retriever_calls == 2
    assert loader_calls == 4


class _FakeIndexer:
    class _Collection:
        @staticmethod
        def count():
            return 100_000

    collection = _Collection()

    @staticmethod
    def get_or_create_index():
        return object()


class _FakeNode:
    def __init__(self, chunk_id: str, *, document_id: str, score: float = 0.9):
        self.metadata = {
            "chunk_id": chunk_id,
            "contextwiki_managed": "true",
            "source_id": "source_career",
            "document_id": document_id,
        }
        self.score = score


def _seed_career_chunks(store: MetadataStore, count: int) -> list[str]:
    store.upsert_source(
        SourceModel(
            source_id="source_career",
            source_type=SourceType.CAREER,
            name="Career files",
            sync_status=SyncStatus.IDLE,
        )
    )
    chunk_ids = []
    for index in range(count):
        document_id = f"career-doc-{index}"
        chunk_id = f"career-chunk-{index}"
        text = f"AWS evidence for bounded retrieval item {index}."
        store.upsert_document_and_replace_chunks(
            DocumentModel(
                id=document_id,
                document_id=document_id,
                source_id="source_career",
                title=f"Career evidence {index}",
                content=text,
                url=f"file:///career/{index}",
                platform="career",
            ),
            [
                ChunkModel(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    source_id="source_career",
                    title=f"Career evidence {index}",
                    text=text,
                    url=f"file:///career/{index}",
                    chunk_index=0,
                    content_hash=f"hash-{index}",
                )
            ],
        )
        chunk_ids.append(chunk_id)
    return chunk_ids


def _seed_filtered_evidence_chunk(
    store: MetadataStore,
    *,
    chunk_id: str,
    document_id: str,
    evidence_source_type: str,
    experience_type: str,
) -> None:
    text = f"Stored evidence for {chunk_id}."
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id=document_id,
            document_id=document_id,
            source_id="source_career",
            title=chunk_id,
            content=text,
            url=f"career://{document_id}",
            platform="career",
            evidence_source_type=evidence_source_type,
            experience_type=experience_type,
            exact_quote=text,
        ),
        [
            ChunkModel(
                chunk_id=chunk_id,
                document_id=document_id,
                source_id="source_career",
                title=chunk_id,
                text=text,
                url=f"career://{document_id}",
                chunk_index=0,
                content_hash=f"hash-{chunk_id}",
                evidence_source_type=evidence_source_type,
                experience_type=experience_type,
                exact_quote=text,
            )
        ],
    )


def test_filtered_evidence_vector_applies_taxonomy_and_document_before_top_k(
    tmp_path,
):
    store = MetadataStore(tmp_path / "filtered-vector.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_career",
            source_type=SourceType.CAREER,
            name="Career files",
            sync_status=SyncStatus.IDLE,
        )
    )
    wrong_nodes = []
    for index in range(4):
        chunk_id = f"wrong-{index}"
        document_id = f"wrong-doc-{index}"
        _seed_filtered_evidence_chunk(
            store,
            chunk_id=chunk_id,
            document_id=document_id,
            evidence_source_type="project",
            experience_type="personal_project",
        )
        wrong_nodes.append(
            _FakeNode(chunk_id, document_id=document_id, score=0.99 - index * 0.01)
        )
    _seed_filtered_evidence_chunk(
        store,
        chunk_id="target-chunk",
        document_id="target-doc",
        evidence_source_type="resume",
        experience_type="professional",
    )
    target_node = _FakeNode(
        "target-chunk",
        document_id="target-doc",
        score=0.80,
    )
    captured_filters = []
    requested_limits = []

    class _FilterAwareVectorRetriever:
        def __init__(self, **kwargs):
            self.limit = kwargs["similarity_top_k"]
            self.filters = str(kwargs["filters"])
            captured_filters.append(self.filters)
            requested_limits.append(self.limit)

        def retrieve(self, query):
            del query
            required_parts = (
                "evidence_source_type",
                "resume",
                "experience_type",
                "professional",
                "document_id",
                "target-doc",
            )
            if all(part in self.filters for part in required_parts):
                return [target_node]
            return wrong_nodes[: self.limit]

    context_service = ContextSearchService(
        store,
        indexer=_FakeIndexer(),
        vector_retriever_cls=_FilterAwareVectorRetriever,
    )
    evidence_service = EvidenceSearchService(
        context_search_service=context_service,
        metadata_store=store,
    )

    results = asyncio.run(
        evidence_service.search_evidence(
            SearchEvidenceInput(
                query="bounded target evidence",
                source_types=["resume"],
                experience_types=["professional"],
                document_ids=["target-doc"],
                top_k=1,
            )
        )
    )

    assert [item.chunk_id for item in results] == ["target-chunk"]
    assert requested_limits == [3]
    assert captured_filters


def test_evidence_vector_budget_caps_total_provider_hits_and_executes_each_variant_once(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "bounded-vector.sqlite3")
    _seed_career_chunks(store, 300)
    provider_calls = []
    requested_limits = []
    returned_hits = []
    metadata_lookup_limits = []
    metadata_rows_returned = []
    stale_corpus = [
        _FakeNode(
            f"stale-{index}",
            document_id=f"stale-doc-{index}",
        )
        for index in range(1_000)
    ]

    class _BoundedVectorRetriever:
        def __init__(self, **kwargs):
            self.limit = kwargs["similarity_top_k"]
            requested_limits.append(self.limit)

        def retrieve(self, query):
            provider_calls.append(query)
            nodes = stale_corpus[: self.limit]
            returned_hits.append(len(nodes))
            return nodes

    original_list_matching = store.list_chunks_matching_metadata_terms

    def counted_list_matching(terms, source_ids=None, **kwargs):
        metadata_lookup_limits.append(kwargs["limit"])
        rows = original_list_matching(terms, source_ids, **kwargs)
        metadata_rows_returned.append(len(rows))
        return rows

    monkeypatch.setattr(
        store,
        "list_chunks_matching_metadata_terms",
        counted_list_matching,
    )

    service = ContextSearchService(
        store,
        indexer=_FakeIndexer(),
        vector_retriever_cls=_BoundedVectorRetriever,
    )

    result = asyncio.run(
        service.search_context(
            "AWS evidence",
            filters={"source_ids": ["source_career"]},
            top_k=15,
            candidate_budget=15,
        )
    )

    assert result["results"] == []
    assert len(provider_calls) == 2
    assert len(set(provider_calls)) == 2
    assert sum(requested_limits) == 15
    assert sum(returned_hits) == 15
    assert metadata_lookup_limits == []
    assert metadata_rows_returned == []
    assert sum(returned_hits) + sum(metadata_rows_returned) == 15


@pytest.mark.parametrize("candidate_budget", [15, 150])
def test_evidence_context_temp_sqlite_hydration_stays_one_batch_without_n_plus_one(
    monkeypatch,
    tmp_path,
    candidate_budget,
):
    store = MetadataStore(tmp_path / f"hydration-{candidate_budget}.sqlite3")
    chunk_ids = _seed_career_chunks(store, candidate_budget)
    candidates = [
        {"chunk_id": chunk_id, "score": 0.99 - index / 10_000}
        for index, chunk_id in enumerate(chunk_ids)
    ]
    provider_calls = []

    def retriever(query, top_k, source_ids):
        provider_calls.append((query, top_k, source_ids))
        return candidates[:top_k]

    original_batch_loader = store.get_active_evidence_snapshots
    original_get_source = store.get_source
    batch_calls = []
    source_calls = []

    def counted_batch_loader(requested_chunk_ids):
        batch_calls.append(list(requested_chunk_ids))
        return original_batch_loader(requested_chunk_ids)

    def fail_get_chunk(chunk_id):
        raise AssertionError(f"unexpected per-chunk hydration: {chunk_id}")

    def fail_get_document(document_id):
        raise AssertionError(f"unexpected per-document hydration: {document_id}")

    def counted_get_source(source_id):
        source_calls.append(source_id)
        return original_get_source(source_id)

    monkeypatch.setattr(store, "get_active_evidence_snapshots", counted_batch_loader)
    monkeypatch.setattr(store, "get_chunk", fail_get_chunk)
    monkeypatch.setattr(store, "get_document", fail_get_document)
    monkeypatch.setattr(store, "get_source", counted_get_source)

    result = asyncio.run(
        ContextSearchService(store, retriever=retriever).search_context(
            "AWS evidence",
            filters={"source_ids": ["source_career"]},
            top_k=candidate_budget,
            candidate_budget=candidate_budget,
        )
    )

    assert len(result["results"]) == candidate_budget
    assert provider_calls == [("AWS evidence", candidate_budget, ["source_career"])]
    assert len(batch_calls) == 1
    assert batch_calls[0] == chunk_ids
    assert source_calls == ["source_career"]
