import asyncio
import json
from pathlib import Path
import subprocess
import sys
import threading
from dataclasses import dataclass, field

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
import pytest

from api.tools import register_tools
from core.models import (
    ChunkModel,
    DocumentModel,
    SourceModel,
    SourceType,
    SyncStatus,
)
from search.context_service import ContextSearchService
from search.evidence_service import EvidenceSearchService
from search.retrieval_pipeline import BoundedRetrievalExecutor
from storage.metadata_store import MetadataStore


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_public_retrieval_evaluation_rejects_copied_fixture_inputs(tmp_path):
    dataset = tmp_path / "copied-dataset.jsonl"
    corpus = tmp_path / "copied-corpus.jsonl"
    configuration = tmp_path / "copied-configuration.json"
    dataset.write_text(
        (REPO_ROOT / "evaluation/datasets/retrieval_gold.example.jsonl").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    corpus.write_text(
        (REPO_ROOT / "evaluation/datasets/career_corpus.example.jsonl").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    configuration.write_text(
        (REPO_ROOT / "evaluation/configs/deterministic_fixture.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "public-output"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.runner",
            "--dataset",
            str(dataset),
            "--corpus",
            str(corpus),
            "--configuration",
            str(configuration),
            "--output-dir",
            str(output_dir),
            "--git-identifier",
            (
                f"commit={'1' * 40};head_tree={'2' * 40};"
                f"worktree_tree={'3' * 40};state=dirty"
            ),
            "--public-only",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "reviewed public fixture dataset, corpus, and configuration" in (
        completed.stderr
    )
    assert not output_dir.exists()


@dataclass(frozen=True)
class StoredEvidence:
    chunk_id: str
    document_id: str
    document_version_id: str
    source_type: str
    document_title: str
    section_title: str
    parent_section_title: str
    exact_quote: str
    retrieval_score: float
    experience_type: str
    file_name: str
    metadata: dict = field(default_factory=dict)

    @property
    def text(self):
        return self.exact_quote

    @property
    def score(self):
        return self.retrieval_score


class TempEvidenceStore:
    def __init__(self, root, records):
        self.root = root
        self.records = {record.chunk_id: record for record in records}

    def get_evidence_chunk(self, chunk_id):
        return self.records.get(chunk_id)

    def get_chunk(self, chunk_id):
        return self.records.get(chunk_id)

    def get_document(self, document_id):
        return next(
            (
                record
                for record in self.records.values()
                if record.document_id == document_id
            ),
            None,
        )

    def get_active_evidence_snapshots(self, chunk_ids):
        return {
            chunk_id: (record, record)
            for chunk_id in chunk_ids
            if (record := self.records.get(chunk_id)) is not None
        }


class DeterministicContextSearch:
    def __init__(self, records):
        self.records = list(records)

    async def search_context(self, query, *, top_k, **kwargs):
        normalized_query = query.lower()
        if "kubernetes" not in normalized_query and "prototype" not in normalized_query:
            return {"query": query, "results": []}
        candidate_filters = kwargs.get("candidate_metadata_filters") or {}
        source_types = set(candidate_filters.get("evidence_source_type") or [])
        experience_types = set(candidate_filters.get("experience_type") or [])
        document_ids = set(candidate_filters.get("document_id") or [])
        records = [
            record
            for record in self.records
            if (not source_types or record.source_type in source_types)
            and (not experience_types or record.experience_type in experience_types)
            and (not document_ids or record.document_id in document_ids)
        ]
        return {
            "query": query,
            "results": [
                {
                    "chunk_id": record.chunk_id,
                    "document_id": record.document_id,
                    "score": record.retrieval_score,
                    "text": record.exact_quote,
                }
                for record in records[:top_k]
            ],
        }


def _call_json(mcp, arguments):
    blocks = asyncio.run(mcp.call_tool("search_evidence", arguments))
    return json.loads(blocks[0].text)


def test_fastmcp_evidence_timeout_is_typed_sanitized_and_event_loop_safe(tmp_path):
    release = threading.Event()

    def blocking_retriever(query, top_k, source_ids):
        del query, top_k, source_ids
        release.wait(timeout=1)
        return []

    store = TempEvidenceStore(tmp_path, [])
    context_service = ContextSearchService(
        store,
        retriever=blocking_retriever,
        default_source_ids=("source_career",),
        retrieval_timeout_seconds=0.02,
        retrieval_max_concurrency=1,
    )
    service = EvidenceSearchService(
        context_search_service=context_service,
        metadata_store=store,
    )
    mcp = FastMCP("career-evidence-timeout-e2e")
    register_tools(mcp, evidence_search_service=service)
    timer = threading.Timer(0.1, release.set)
    timer.start()
    try:
        with pytest.raises(ToolError) as exc_info:
            asyncio.run(
                mcp.call_tool(
                    "search_evidence",
                    {"query": "private timeout query must not be echoed"},
                )
            )
    finally:
        release.set()
        timer.cancel()
        timer.join(timeout=1)

    message = str(exc_info.value)
    assert "[timeout] Evidence search failed" in message
    assert "private timeout query" not in message

    recovered = _call_json(
        mcp,
        {"query": "retry after bounded worker exit"},
    )
    assert recovered == []


def test_fastmcp_bounds_legacy_context_without_deadline_parameter(tmp_path):
    class LegacyHangingContextSearch:
        def __init__(self):
            self.retrieval_executor = BoundedRetrievalExecutor(
                timeout_seconds=0.02,
                max_concurrency=1,
            )
            self.cancelled = asyncio.Event()

        async def search_context(self, query, *, filters, top_k, candidate_budget):
            del query, filters, top_k, candidate_budget
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()

    context_service = LegacyHangingContextSearch()
    service = EvidenceSearchService(
        context_search_service=context_service,
        metadata_store=TempEvidenceStore(tmp_path, []),
    )
    mcp = FastMCP("career-evidence-legacy-deadline-e2e")
    register_tools(mcp, evidence_search_service=service)

    async def scenario():
        with pytest.raises(ToolError) as exc_info:
            await asyncio.wait_for(
                mcp.call_tool(
                    "search_evidence",
                    {"query": "private legacy timeout query must not be echoed"},
                ),
                timeout=0.3,
            )
        assert context_service.cancelled.is_set()
        return str(exc_info.value)

    message = asyncio.run(scenario())

    assert "[timeout] Evidence search failed" in message
    assert "private legacy timeout query" not in message


def test_fastmcp_authoritative_hydration_timeout_is_typed_and_releases_slot(
    monkeypatch,
    tmp_path,
):
    store = MetadataStore(tmp_path / "hydration-timeout.sqlite3")
    store.upsert_source(
        SourceModel(
            source_id="source_career",
            source_type=SourceType.CAREER,
            name="Career files",
            sync_status=SyncStatus.IDLE,
        )
    )
    text = "Improved bounded retrieval reliability."
    store.upsert_document_and_replace_chunks(
        DocumentModel(
            id="hydration-document",
            document_id="hydration-document",
            source_id="source_career",
            title="Hydration evidence",
            content=text,
            url="career://hydration-document",
            platform="career",
            evidence_source_type="resume",
            experience_type="professional",
            exact_quote=text,
        ),
        [
            ChunkModel(
                chunk_id="hydration-chunk",
                document_id="hydration-document",
                source_id="source_career",
                title="Hydration evidence",
                text=text,
                url="career://hydration-document",
                chunk_index=0,
                content_hash="hydration-hash",
                evidence_source_type="resume",
                experience_type="professional",
                exact_quote=text,
            )
        ],
    )
    original_loader = store.get_active_evidence_snapshots
    release_hydration = threading.Event()
    hydration_finished = threading.Event()
    loader_calls = 0

    def blocking_second_loader(chunk_ids):
        nonlocal loader_calls
        loader_calls += 1
        if loader_calls == 2:
            release_hydration.wait(timeout=1)
            hydration_finished.set()
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
                "chunk_id": "hydration-chunk",
                "document_id": "hydration-document",
                "score": 0.9,
            }
        ]

    context_service = ContextSearchService(
        store,
        retriever=retriever,
        default_source_ids=("source_career",),
        retrieval_timeout_seconds=0.05,
        retrieval_max_concurrency=1,
    )
    service = EvidenceSearchService(
        context_search_service=context_service,
        metadata_store=store,
    )
    mcp = FastMCP("career-evidence-hydration-timeout-e2e")
    register_tools(mcp, evidence_search_service=service)
    timer = threading.Timer(0.2, release_hydration.set)
    timer.start()
    try:
        with pytest.raises(ToolError) as exc_info:
            asyncio.run(
                mcp.call_tool(
                    "search_evidence",
                    {"query": "private hydration query must not be echoed"},
                )
            )
    finally:
        release_hydration.set()
        timer.cancel()
        timer.join(timeout=1)

    assert hydration_finished.wait(timeout=1)
    message = str(exc_info.value)
    assert "[timeout] Evidence search failed" in message
    assert "private hydration query" not in message

    recovered = _call_json(
        mcp,
        {"query": "bounded hydration retry", "top_k": 1},
    )
    assert [item["chunk_id"] for item in recovered] == ["hydration-chunk"]


def test_fastmcp_career_evidence_flow_uses_exact_quotes_filters_and_empty_results(
    tmp_path,
):
    records = [
        StoredEvidence(
            chunk_id="resume-reliability",
            document_id="resume-active",
            document_version_id="version-resume-1",
            source_type="resume",
            document_title="Backend Resume",
            section_title="Platform modernization and reliability",
            parent_section_title="Work experience",
            exact_quote="Improved Kubernetes rollout reliability by 40%.",
            retrieval_score=0.96,
            experience_type="professional",
            file_name="resume.md",
            metadata={"company": "Example Systems", "role": "Backend Engineer"},
        ),
        StoredEvidence(
            chunk_id="project-prototype",
            document_id="project-active",
            document_version_id="version-project-1",
            source_type="project",
            document_title="Scheduler Prototype",
            section_title="Projects",
            parent_section_title="Personal projects",
            exact_quote="Built a Kubernetes scheduler prototype for learning.",
            retrieval_score=0.91,
            experience_type="personal_project",
            file_name="project.md",
            metadata={"project": "Scheduler Prototype"},
        ),
        StoredEvidence(
            chunk_id="project-prototype-duplicate",
            document_id="project-copy",
            document_version_id="version-project-copy-1",
            source_type="project",
            document_title="Scheduler Prototype Copy",
            section_title="Projects",
            parent_section_title="Personal projects",
            exact_quote="Built a Kubernetes scheduler prototype for learning.",
            retrieval_score=0.80,
            experience_type="personal_project",
            file_name="project-copy.md",
        ),
    ]
    store = TempEvidenceStore(tmp_path, records)
    service = EvidenceSearchService(
        context_search_service=DeterministicContextSearch(records),
        metadata_store=store,
        relevance_threshold=0.2,
        near_duplicate_threshold=0.9,
    )
    mcp = FastMCP("career-evidence-e2e")
    register_tools(mcp, evidence_search_service=service)

    professional = _call_json(
        mcp,
        {
            "query": "Kubernetes reliability",
            "source_types": ["resume"],
            "experience_types": ["professional"],
            "document_ids": ["resume-active"],
            "top_k": 5,
        },
    )
    personal = _call_json(
        mcp,
        {
            "query": "Kubernetes prototype",
            "source_types": ["project"],
            "experience_types": ["personal_project"],
            "top_k": 5,
        },
    )
    missing = _call_json(
        mcp,
        {"query": "unrelated evidence that is not indexed", "top_k": 5},
    )

    assert len(professional) == 1
    assert professional[0] == {
        "chunk_id": "resume-reliability",
        "document_id": "resume-active",
        "document_version_id": "version-resume-1",
        "source_type": "resume",
        "document_title": "Backend Resume",
        "section_title": "Platform modernization and reliability",
        "parent_section_title": "Work experience",
        "exact_quote": "Improved Kubernetes rollout reliability by 40%.",
        "retrieval_score": 0.96,
        "experience_type": "professional",
        "file_name": "resume.md",
        "metadata": {"company": "Example Systems", "role": "Backend Engineer"},
    }
    assert [item["chunk_id"] for item in personal] == ["project-prototype"]
    assert personal[0]["experience_type"] == "personal_project"
    assert missing == []


def test_fastmcp_evidence_filters_run_before_three_times_candidate_cap(tmp_path):
    wrong = [
        StoredEvidence(
            chunk_id=f"wrong-{index}",
            document_id=f"wrong-doc-{index}",
            document_version_id="wrong-v1",
            source_type="project",
            document_title="Wrong project",
            section_title="Projects",
            parent_section_title="Personal projects",
            exact_quote=f"Kubernetes target distractor {index}.",
            retrieval_score=0.99 - index * 0.01,
            experience_type="personal_project",
            file_name="wrong.md",
        )
        for index in range(4)
    ]
    target = StoredEvidence(
        chunk_id="target",
        document_id="target-doc",
        document_version_id="target-v1",
        source_type="resume",
        document_title="Target resume",
        section_title="Experience",
        parent_section_title="Work",
        exact_quote="Kubernetes target professional evidence.",
        retrieval_score=0.80,
        experience_type="professional",
        file_name="resume.md",
    )
    records = [*wrong, target]
    store = TempEvidenceStore(tmp_path, records)
    service = EvidenceSearchService(
        context_search_service=DeterministicContextSearch(records),
        metadata_store=store,
    )
    mcp = FastMCP("career-evidence-prefilter-e2e")
    register_tools(mcp, evidence_search_service=service)

    payload = _call_json(
        mcp,
        {
            "query": "Kubernetes target",
            "source_types": ["resume"],
            "experience_types": ["professional"],
            "document_ids": ["target-doc"],
            "top_k": 1,
        },
    )

    assert [item["chunk_id"] for item in payload] == ["target"]
