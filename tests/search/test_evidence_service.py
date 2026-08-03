import asyncio
from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError

from core.exceptions import (
    EvidenceRetrievalError,
    EvidenceSearchError,
    InvalidEvidenceRequestError,
)
from core.models import EvidenceChunk, SearchEvidenceInput
from search.evidence_service import EvidenceSearchService


CAREER_SOURCE_TYPES = {
    "resume",
    "previous_resume",
    "project",
    "github_readme",
    "behavioral_story",
    "career_note",
    "skills_inventory",
}
EXPERIENCE_TYPES = {
    "professional",
    "academic",
    "personal_project",
    "prototype",
    "unknown",
}


@dataclass(frozen=True)
class StoredEvidence:
    chunk_id: str
    document_id: str
    exact_quote: str
    retrieval_score: float
    source_id: str = "source_career"
    document_version_id: str = "version-1"
    source_type: str = "resume"
    document_title: str = "Career Evidence"
    section_title: str = "Experience"
    parent_section_title: str = "Work"
    experience_type: str = "professional"
    file_name: str = "resume.md"
    metadata: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.exact_quote

    @property
    def score(self) -> float:
        return self.retrieval_score


class FakeEvidenceStore:
    def __init__(self, records):
        self.records = {record.chunk_id: record for record in records}
        self.batch_hydration_calls = []
        self.legacy_chunk_calls = 0
        self.legacy_document_calls = 0

    def get_active_evidence_snapshots(self, chunk_ids):
        self.batch_hydration_calls.append(list(chunk_ids))
        return {
            chunk_id: (record, record)
            for chunk_id in chunk_ids
            if (record := self.records.get(chunk_id)) is not None
        }

    def get_evidence_chunk(self, chunk_id):
        self.legacy_chunk_calls += 1
        return self.records.get(chunk_id)

    def get_chunk(self, chunk_id):
        return self.records.get(chunk_id)

    def get_document(self, document_id):
        self.legacy_document_calls += 1
        return next(
            (
                record
                for record in self.records.values()
                if record.document_id == document_id
            ),
            None,
        )


class FakeContextSearch:
    def __init__(self, records):
        self.records = list(records)
        self.calls = []

    async def search_context(self, query, *, top_k, **kwargs):
        self.calls.append({"query": query, "top_k": top_k, **kwargs})
        filters = kwargs.get("filters") or {}
        allowed_source_ids = set(filters.get("source_ids") or [])
        records = [
            record
            for record in self.records
            if not allowed_source_ids or record.source_id in allowed_source_ids
        ]
        candidate_filters = kwargs.get("candidate_metadata_filters") or {}
        allowed_evidence_sources = set(
            candidate_filters.get("evidence_source_type") or []
        )
        allowed_experiences = set(candidate_filters.get("experience_type") or [])
        allowed_documents = set(candidate_filters.get("document_id") or [])
        records = [
            record
            for record in records
            if (
                not allowed_evidence_sources
                or record.source_type in allowed_evidence_sources
            )
            and (
                not allowed_experiences or record.experience_type in allowed_experiences
            )
            and (not allowed_documents or record.document_id in allowed_documents)
        ]
        return {
            "query": query,
            "results": [
                {
                    "chunk_id": record.chunk_id,
                    "document_id": record.document_id,
                    "score": record.retrieval_score,
                    # Candidate text is deliberately not authoritative. Exact
                    # evidence must be hydrated from the metadata store.
                    "text": "candidate text must not replace stored quote",
                }
                for record in records[:top_k]
            ],
        }


class FailingContextSearch:
    async def search_context(self, query, *, top_k, **kwargs):
        del query, top_k, kwargs
        raise RuntimeError(
            "token=super-secret-value at /Users/tester/private/career.sqlite3"
        )


def _service(records, *, relevance_threshold=0.2, near_duplicate_threshold=0.9):
    context_search = FakeContextSearch(records)
    service = EvidenceSearchService(
        context_search_service=context_search,
        metadata_store=FakeEvidenceStore(records),
        relevance_threshold=relevance_threshold,
        near_duplicate_threshold=near_duplicate_threshold,
    )
    return service, context_search


def _search(service, **overrides):
    request_payload = {
        "query": "Kubernetes reliability evidence",
        "top_k": 5,
    }
    request_payload.update(overrides)
    request = SearchEvidenceInput(**request_payload)
    return asyncio.run(service.search_evidence(request))


def _payload(item: EvidenceChunk) -> dict:
    return item.model_dump(mode="json")


def test_search_evidence_input_uses_fixed_taxonomies_and_normalizes_filters():
    request = SearchEvidenceInput(
        query="  Kubernetes reliability evidence  ",
        source_types=sorted(CAREER_SOURCE_TYPES),
        experience_types=sorted(EXPERIENCE_TYPES),
        document_ids=[" doc-1 ", "doc-1", "doc-2"],
        top_k=50,
    )

    payload = request.model_dump(mode="json")
    assert payload["query"] == "Kubernetes reliability evidence"
    assert set(payload["source_types"]) == CAREER_SOURCE_TYPES
    assert set(payload["experience_types"]) == EXPERIENCE_TYPES
    assert payload["document_ids"] == ["doc-1", "doc-2"]
    assert payload["top_k"] == 50


@pytest.mark.parametrize(
    "overrides",
    [
        {"query": "   "},
        {"query": "valid", "source_types": ["linkedin_profile"]},
        {"query": "valid", "experience_types": ["production"]},
        {"query": "valid", "document_ids": [""]},
        {"query": "valid", "top_k": 0},
        {"query": "valid", "top_k": 51},
        {"query": "valid", "top_k": "3"},
        {"query": "valid", "top_k": 3.0},
        {"query": "valid", "top_k": True},
    ],
)
def test_search_evidence_input_rejects_invalid_requests_without_echoing_values(
    overrides,
):
    with pytest.raises(ValidationError) as exc_info:
        SearchEvidenceInput(**overrides)

    message = str(exc_info.value)
    assert "linkedin_profile" not in message
    assert "production" not in message
    assert "input_value" not in message


@pytest.mark.parametrize(
    "overrides",
    [
        {"query": "q" * 4097},
        {"query": "valid", "document_ids": ["d" * 513]},
        {
            "query": "valid",
            "document_ids": [f"doc-{index}" for index in range(101)],
        },
        {
            "query": "한" * 4096,
            "document_ids": [f"{index:03}-" + "한" * 500 for index in range(100)],
        },
    ],
)
def test_search_evidence_input_bounds_query_ids_and_aggregate_request_bytes(
    overrides,
):
    with pytest.raises(ValidationError) as exc_info:
        SearchEvidenceInput(**overrides)

    message = str(exc_info.value)
    assert "input_value" not in message


def test_evidence_error_types_form_one_search_error_family():
    assert issubclass(InvalidEvidenceRequestError, EvidenceSearchError)
    assert issubclass(EvidenceRetrievalError, EvidenceSearchError)


def test_search_evidence_returns_required_fields_exact_quote_and_score():
    record = StoredEvidence(
        chunk_id="chunk-reliability",
        document_id="doc-resume",
        document_version_id="sha256:version-a",
        source_type="resume",
        document_title="Backend Resume",
        section_title="Platform modernization and reliability",
        parent_section_title="Work experience",
        exact_quote="Reduced deployment failures by 40%.\nKept rollout SLOs above 99.9%.",
        retrieval_score=0.93,
        experience_type="professional",
        file_name="backend-resume.md",
        metadata={"company": "Example Systems", "role": "Backend Engineer"},
    )
    service, _ = _service([record])

    results = _search(service)

    assert len(results) == 1
    payload = _payload(results[0])
    assert payload == {
        "chunk_id": "chunk-reliability",
        "document_id": "doc-resume",
        "document_version_id": "sha256:version-a",
        "source_type": "resume",
        "document_title": "Backend Resume",
        "section_title": "Platform modernization and reliability",
        "parent_section_title": "Work experience",
        "exact_quote": (
            "Reduced deployment failures by 40%.\nKept rollout SLOs above 99.9%."
        ),
        "retrieval_score": 0.93,
        "experience_type": "professional",
        "file_name": "backend-resume.md",
        "metadata": {
            "company": "Example Systems",
            "role": "Backend Engineer",
        },
    }
    assert "candidate text must not replace stored quote" not in payload["exact_quote"]


def test_evidence_chunk_metadata_defaults_are_not_shared():
    first = EvidenceChunk(
        chunk_id="chunk-1",
        document_id="doc-1",
        source_type="resume",
        exact_quote="First quote",
    )
    second = EvidenceChunk(
        chunk_id="chunk-2",
        document_id="doc-2",
        source_type="project",
        exact_quote="Second quote",
    )

    first.metadata["company"] = "Example"

    assert second.metadata == {}


@pytest.mark.parametrize("retrieval_score", [float("nan"), float("inf"), float("-inf")])
def test_evidence_chunk_rejects_non_finite_retrieval_score(retrieval_score):
    with pytest.raises(ValidationError):
        EvidenceChunk(
            chunk_id="chunk-non-finite",
            document_id="doc-non-finite",
            source_type="resume",
            exact_quote="Stored quote.",
            retrieval_score=retrieval_score,
        )


def test_search_evidence_applies_source_experience_and_document_filters_together():
    records = [
        StoredEvidence(
            chunk_id="resume-professional",
            document_id="doc-resume",
            source_type="resume",
            experience_type="professional",
            exact_quote="Operated Kubernetes services in production.",
            retrieval_score=0.95,
        ),
        StoredEvidence(
            chunk_id="project-personal",
            document_id="doc-project",
            source_type="project",
            experience_type="personal_project",
            exact_quote="Built a Kubernetes prototype for a personal project.",
            retrieval_score=0.94,
        ),
        StoredEvidence(
            chunk_id="resume-academic",
            document_id="doc-academic",
            source_type="resume",
            experience_type="academic",
            exact_quote="Studied Kubernetes scheduling in a course.",
            retrieval_score=0.93,
        ),
    ]
    service, _ = _service(records)

    results = _search(
        service,
        source_types=["resume"],
        experience_types=["professional"],
        document_ids=["doc-resume", "doc-project"],
    )

    assert [item.chunk_id for item in results] == ["resume-professional"]
    assert results[0].experience_type == "professional"
    assert results[0].source_type == "resume"


def test_search_evidence_prefilters_taxonomy_and_document_before_hard_cap():
    wrong = [
        StoredEvidence(
            chunk_id=f"wrong-{index}",
            document_id=f"wrong-doc-{index}",
            source_type="project",
            experience_type="personal_project",
            exact_quote=f"High-ranked wrong evidence {index}.",
            retrieval_score=0.99 - index * 0.01,
        )
        for index in range(4)
    ]
    target = StoredEvidence(
        chunk_id="filtered-target",
        document_id="target-doc",
        source_type="resume",
        experience_type="professional",
        exact_quote="Target evidence survives pre-cap filtering.",
        retrieval_score=0.80,
    )
    service, context_search = _service([*wrong, target])

    results = _search(
        service,
        source_types=["resume"],
        experience_types=["professional"],
        document_ids=["target-doc"],
        top_k=1,
    )

    assert [item.chunk_id for item in results] == ["filtered-target"]
    assert context_search.calls[0]["top_k"] == 3
    assert context_search.calls[0]["candidate_metadata_filters"] == {
        "evidence_source_type": ["resume"],
        "experience_type": ["professional"],
        "document_id": ["target-doc"],
    }


def test_search_evidence_removes_exact_and_near_duplicate_quotes():
    records = [
        StoredEvidence(
            chunk_id="exact-best",
            document_id="doc-1",
            exact_quote="Built Kubernetes readiness probes and SLO dashboards.",
            retrieval_score=0.97,
        ),
        StoredEvidence(
            chunk_id="exact-lower",
            document_id="doc-2",
            exact_quote="Built Kubernetes readiness probes and SLO dashboards.",
            retrieval_score=0.91,
        ),
        StoredEvidence(
            chunk_id="near-lower",
            document_id="doc-3",
            exact_quote=(
                "Built Kubernetes readiness probes and SLO dashboards for services."
            ),
            retrieval_score=0.89,
        ),
        StoredEvidence(
            chunk_id="distinct",
            document_id="doc-4",
            exact_quote="Migrated PostgreSQL workloads with zero data loss.",
            retrieval_score=0.86,
        ),
    ]
    service, _ = _service(records, near_duplicate_threshold=0.75)

    results = _search(service, top_k=4)

    assert [item.chunk_id for item in results] == ["exact-best", "distinct"]


def test_search_evidence_refills_after_deduplication_to_reach_top_k():
    duplicate_quote = "Reduced Kubernetes rollout failures with readiness probes."
    duplicates = [
        StoredEvidence(
            chunk_id=f"duplicate-{index}",
            document_id=f"doc-duplicate-{index}",
            exact_quote=duplicate_quote,
            retrieval_score=0.99 - index * 0.01,
        )
        for index in range(6)
    ]
    unique = [
        StoredEvidence(
            chunk_id="unique-one",
            document_id="doc-unique-one",
            exact_quote="Cut incident response time with trace correlation.",
            retrieval_score=0.80,
        ),
        StoredEvidence(
            chunk_id="unique-two",
            document_id="doc-unique-two",
            exact_quote="Improved queue throughput through bounded batching.",
            retrieval_score=0.79,
        ),
    ]
    service, context_search = _service([*duplicates, *unique])

    results = _search(service, top_k=3)

    assert [item.chunk_id for item in results] == [
        "duplicate-0",
        "unique-one",
        "unique-two",
    ]
    requested_limits = [call["top_k"] for call in context_search.calls]
    assert requested_limits == [9]
    assert context_search.calls[0]["candidate_budget"] == 9


@pytest.mark.parametrize(
    ("top_k", "expected_candidate_budget"),
    [(5, 15), (50, 150)],
)
def test_search_evidence_uses_selected_three_times_hard_candidate_budget(
    top_k,
    expected_candidate_budget,
):
    records = [
        StoredEvidence(
            chunk_id=f"chunk-{index}",
            document_id=f"doc-{index}",
            exact_quote=f"Distinct bounded evidence {index}.",
            retrieval_score=0.99,
        )
        for index in range(200)
    ]
    service, context_search = _service(records)

    results = _search(service, top_k=top_k)

    assert len(results) == top_k
    assert len(context_search.calls) == 1
    call = context_search.calls[0]
    assert isinstance(call["_retrieval_deadline"], float)
    assert {
        key: value for key, value in call.items() if key != "_retrieval_deadline"
    } == {
        "query": "Kubernetes reliability evidence",
        "filters": {"source_ids": ["source_career"]},
        "top_k": expected_candidate_budget,
        "candidate_budget": expected_candidate_budget,
    }


def test_search_evidence_batches_authoritative_hydration_once_without_n_plus_one():
    records = [
        StoredEvidence(
            chunk_id=f"chunk-{index}",
            document_id=f"doc-{index}",
            exact_quote=f"Distinct career evidence {index}.",
            retrieval_score=0.9 - index * 0.01,
        )
        for index in range(8)
    ]
    service, context_search = _service(records)
    store = service.metadata_store

    results = _search(service, top_k=5)

    assert len(results) == 5
    assert len(context_search.calls) == 1
    assert len(store.batch_hydration_calls) == 1
    assert store.batch_hydration_calls[0] == [record.chunk_id for record in records]
    assert store.legacy_chunk_calls == 0
    assert store.legacy_document_calls == 0


def test_search_evidence_restricts_candidates_to_career_source_before_cap():
    unrelated = [
        StoredEvidence(
            chunk_id=f"unrelated-{index}",
            document_id=f"unrelated-doc-{index}",
            source_id="source_notion",
            exact_quote=f"Unrelated candidate {index}.",
            retrieval_score=0.99,
        )
        for index in range(120)
    ]
    career = StoredEvidence(
        chunk_id="career-target",
        document_id="career-document",
        exact_quote="Improved Kubernetes rollout reliability.",
        retrieval_score=0.90,
    )
    service, context_search = _service([*unrelated, career])

    results = _search(service, top_k=1)

    assert [item.chunk_id for item in results] == ["career-target"]
    assert context_search.calls[0]["filters"] == {"source_ids": ["source_career"]}


def test_search_evidence_forwards_absolute_deadline_to_kwargs_context():
    record = StoredEvidence(
        chunk_id="deadline-candidate",
        document_id="deadline-document",
        exact_quote="Bounded retrieval keeps one total request deadline.",
        retrieval_score=0.91,
    )
    service, context_search = _service([record])

    results = _search(service, top_k=1)

    assert [item.chunk_id for item in results] == ["deadline-candidate"]
    assert isinstance(context_search.calls[0]["_retrieval_deadline"], float)


def test_default_near_duplicate_threshold_matches_selected_measured_config():
    records = [
        StoredEvidence(
            chunk_id="best",
            document_id="doc-best",
            exact_quote="Built queue recovery checks for reliable services.",
            retrieval_score=0.95,
        ),
        StoredEvidence(
            chunk_id="near-copy",
            document_id="doc-near-copy",
            exact_quote="Built queue recovery checks for reliable backend services.",
            retrieval_score=0.90,
        ),
    ]
    context_search = FakeContextSearch(records)
    service = EvidenceSearchService(
        context_search_service=context_search,
        metadata_store=FakeEvidenceStore(records),
    )

    results = _search(service, top_k=2)

    assert service.near_duplicate_threshold == 0.8
    assert [item.chunk_id for item in results] == ["best"]


def test_search_evidence_returns_empty_for_irrelevant_candidates():
    record = StoredEvidence(
        chunk_id="irrelevant",
        document_id="doc-irrelevant",
        exact_quote="A cooking recipe with no career evidence.",
        retrieval_score=0.04,
    )
    service, _ = _service([record], relevance_threshold=0.2)

    assert _search(service) == []


def test_search_evidence_skips_non_finite_candidate_scores_before_thresholding():
    records = [
        StoredEvidence(
            chunk_id="nan-score",
            document_id="doc-nan",
            exact_quote="NaN must not become JSON evidence.",
            retrieval_score=float("nan"),
        ),
        StoredEvidence(
            chunk_id="positive-infinity-score",
            document_id="doc-positive-infinity",
            exact_quote="Positive infinity must not become JSON evidence.",
            retrieval_score=float("inf"),
        ),
        StoredEvidence(
            chunk_id="negative-infinity-score",
            document_id="doc-negative-infinity",
            exact_quote="Negative infinity must not become JSON evidence.",
            retrieval_score=float("-inf"),
        ),
        StoredEvidence(
            chunk_id="finite-score",
            document_id="doc-finite",
            exact_quote="Finite scores remain valid evidence.",
            retrieval_score=0.91,
        ),
    ]
    service, _ = _service(records)

    results = _search(service)

    assert [item.chunk_id for item in results] == ["finite-score"]


def test_search_evidence_wraps_internal_failures_in_sanitized_typed_error():
    service = EvidenceSearchService(
        context_search_service=FailingContextSearch(),
        metadata_store=FakeEvidenceStore([]),
    )

    with pytest.raises(EvidenceRetrievalError) as exc_info:
        _search(service)

    message = str(exc_info.value)
    assert exc_info.value.error_type == "internal_error"
    assert "Evidence retrieval failed" in message
    assert "super-secret-value" not in message
    assert "/Users/tester/private" not in message


def test_search_evidence_logs_counts_and_hash_without_private_request_content(caplog):
    record = StoredEvidence(
        chunk_id="private-chunk-id",
        document_id="private-document-id",
        exact_quote="Private resume evidence must not appear in logs.",
        retrieval_score=0.95,
    )
    service, _ = _service([record])

    with caplog.at_level("INFO", logger="search.evidence_service"):
        _search(
            service,
            query="private career query must not appear in logs",
            document_ids=["private-document-id"],
        )

    rendered = caplog.text
    assert "request_id=" in rendered
    assert "query_hash=" in rendered
    assert "document_id_count=1" in rendered
    assert "private career query" not in rendered
    assert "private-document-id" not in rendered
    assert record.exact_quote not in rendered
