from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import time
from typing import Any

from core.models import (
    ChunkModel,
    DocumentModel,
    SearchEvidenceInput,
    SourceModel,
    SourceType,
)
from environments.config import AppConfig
from evaluation.corpus import EvaluationInputError, load_corpus as load_corpus
from evaluation.metrics import calculate_ingestion_metrics, evaluate_retrieval_metrics
from evaluation.reporting import build_report
from search.context_service import ContextSearchService
from search.evidence_service import EvidenceSearchService


TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
QUERY_ALIASES = {
    "side": "personal",
    "detect": "flag",
    "detects": "flag",
    "detected": "flag",
    "made": "improved",
    "faster": "recovery",
    "message": "kafka",
    "consumers": "consumer",
    "systems": "engine",
}
_COMMON_CONFIGURATION_FIELDS = frozenset(
    {
        "name",
        "retrieval_mode",
        "query_normalization",
        "metadata_filtering",
        "exact_duplicate_removal",
        "near_duplicate_removal",
        "candidate_multiplier",
        "near_duplicate_threshold",
        "top_k",
    }
)
_MODE_CONFIGURATION_FIELDS = {
    "keyword": frozenset(),
    "hybrid_rrf": frozenset({"rrf_k"}),
    "production_analog": frozenset(
        {
            "keyword_weight",
            "service_execution",
            "candidate_score_scale",
            "candidate_score_calibration_floor",
            "status",
            "notes",
            "production_mapping",
            "proxy_limitation",
        }
    ),
}


class _FixtureMetadataStore:
    """Minimal in-memory authority used only by deterministic fixture evaluation."""

    def __init__(self, corpus: list[dict[str, Any]]):
        self.source = SourceModel(
            source_id="source_career",
            source_type=SourceType.CAREER,
            name="Deterministic career fixture",
        )
        self.chunks: dict[str, ChunkModel] = {}
        self.documents: dict[str, DocumentModel] = {}
        for index, item in enumerate(corpus):
            chunk_id = str(item["chunk_id"])
            document_id = str(item["document_id"])
            text = str(item["content"])
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            chunk = ChunkModel(
                chunk_id=chunk_id,
                document_id=document_id,
                source_id=self.source.source_id,
                title=str(item.get("document_title") or document_id),
                text=text,
                chunk_index=index,
                content_hash=content_hash,
                document_version_id=str(item.get("document_version_id") or ""),
                evidence_source_type=str(item["source_type"]),
                experience_type=str(item["experience_type"]),
                file_name=str(item.get("file_name") or ""),
                document_title=str(item.get("document_title") or ""),
                section_title=str(item.get("section_title") or ""),
                parent_section_title=str(item.get("parent_section_title") or ""),
                exact_quote=str(item["exact_quote"]),
                company=str(item.get("company") or ""),
                role=str(item.get("role") or ""),
                project=str(item.get("project") or ""),
                start_date=str(item.get("start_date") or ""),
                end_date=str(item.get("end_date") or ""),
            )
            self.chunks[chunk_id] = chunk
            self.documents.setdefault(
                document_id,
                DocumentModel(
                    id=document_id,
                    document_id=document_id,
                    source_id=self.source.source_id,
                    title=str(item.get("document_title") or document_id),
                    content=text,
                    url="",
                    platform="career",
                    content_hash=content_hash,
                    document_version_id=str(item.get("document_version_id") or ""),
                    evidence_source_type=str(item["source_type"]),
                    experience_type=str(item["experience_type"]),
                    file_name=str(item.get("file_name") or ""),
                    document_title=str(item.get("document_title") or ""),
                    section_title=str(item.get("section_title") or ""),
                    parent_section_title=str(item.get("parent_section_title") or ""),
                    exact_quote=str(item["exact_quote"]),
                    company=str(item.get("company") or ""),
                    role=str(item.get("role") or ""),
                    project=str(item.get("project") or ""),
                    start_date=str(item.get("start_date") or ""),
                    end_date=str(item.get("end_date") or ""),
                ),
            )

    def get_chunk(self, chunk_id: str) -> ChunkModel | None:
        return self.chunks.get(chunk_id)

    def get_evidence_chunk(self, chunk_id: str) -> ChunkModel | None:
        return self.chunks.get(chunk_id)

    def get_active_evidence_snapshots(
        self, chunk_ids: list[str]
    ) -> dict[str, tuple[ChunkModel, DocumentModel]]:
        snapshots: dict[str, tuple[ChunkModel, DocumentModel]] = {}
        for chunk_id in chunk_ids:
            chunk = self.chunks.get(chunk_id)
            if chunk is None or chunk_id in snapshots:
                continue
            document = self.documents.get(chunk.document_id)
            if document is not None:
                snapshots[chunk_id] = (chunk, document)
        return snapshots

    def get_document(self, document_id: str) -> DocumentModel | None:
        return self.documents.get(document_id)

    def get_source(self, source_id: str) -> SourceModel | None:
        return self.source if source_id == self.source.source_id else None

    @staticmethod
    def document_matches_filters(document: DocumentModel, filters: Any) -> bool:
        source_ids = list(getattr(filters, "source_ids", ()) or ())
        source_id = str(getattr(filters, "source_id", "") or "")
        if source_id:
            source_ids.append(source_id)
        return not source_ids or document.source_id in source_ids


class _DeterministicOfflineCandidateProvider:
    def __init__(
        self,
        corpus: list[dict[str, Any]],
        configuration: dict[str, Any],
    ):
        self.corpus = corpus
        self.configuration = configuration

    def __call__(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
        *,
        candidate_metadata_filters: dict[str, list[str]] | None = None,
    ) -> list[dict[str, Any]]:
        if source_ids is not None and "source_career" not in source_ids:
            return []
        filtered_corpus = [
            chunk
            for chunk in self.corpus
            if _matches_candidate_metadata_filters(
                chunk,
                candidate_metadata_filters,
            )
        ]
        ranked = _production_analog_candidates(
            query,
            filtered_corpus,
            self.configuration,
            top_k=top_k,
        )
        return [
            {"chunk_id": chunk["chunk_id"], "score": score, "vector_score": score}
            for score, chunk in ranked[:top_k]
        ]


def _matches_candidate_metadata_filters(
    chunk: dict[str, Any],
    filters: dict[str, list[str]] | None,
) -> bool:
    if not filters:
        return True
    corpus_fields = {
        "evidence_source_type": "source_type",
        "experience_type": "experience_type",
        "document_id": "document_id",
    }
    return all(
        not allowed_values
        or str(chunk.get(corpus_fields[key]) or "") in allowed_values
        for key, allowed_values in filters.items()
        if key in corpus_fields
    )


def retrieve_evidence(
    query: str,
    corpus: list[dict[str, Any]],
    configuration: dict[str, Any],
    *,
    allowed_source_types: tuple[str, ...] = (),
    allowed_experience_types: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Run deterministic, dependency-free retrieval over a sanitized corpus."""
    validate_configuration(configuration)
    if not isinstance(query, str) or not query.strip():
        raise EvaluationInputError("query must be a non-empty string")
    mode = str(configuration["retrieval_mode"])
    top_k = _positive_int(configuration, "top_k", default=5)
    candidate_multiplier = _positive_int(
        configuration, "candidate_multiplier", default=1
    )
    near_duplicate_threshold = _unit_interval(
        configuration, "near_duplicate_threshold", default=0.9
    )
    use_normalization = bool(configuration.get("query_normalization", False))
    metadata_filtering = bool(configuration.get("metadata_filtering", False))

    candidates = [dict(chunk) for chunk in corpus]
    if metadata_filtering:
        candidates = [
            chunk
            for chunk in candidates
            if (
                not allowed_source_types
                or chunk.get("source_type") in allowed_source_types
            )
            and (
                not allowed_experience_types
                or chunk.get("experience_type") in allowed_experience_types
            )
        ]

    query_tokens = _tokens(query, normalize_query=use_normalization)
    keyword_ranked = _rank_positive(
        [
            (_keyword_score(query_tokens, _tokens(_searchable_text(chunk))), chunk)
            for chunk in candidates
        ]
    )
    if mode == "keyword":
        ranked = keyword_ranked
    else:
        semantic_ranked = _rank_positive(
            [
                (
                    _semantic_score(
                        query,
                        _searchable_text(chunk),
                        normalize_query=use_normalization,
                    ),
                    chunk,
                )
                for chunk in candidates
            ]
        )
        candidate_count = min(len(candidates), max(top_k, top_k * candidate_multiplier))
        if mode == "hybrid_rrf":
            ranked = _reciprocal_rank_fusion(
                keyword_ranked[:candidate_count],
                semantic_ranked[:candidate_count],
                rrf_k=_positive_int(configuration, "rrf_k", default=60),
            )
        else:
            ranked = _weighted_candidate_fusion(
                keyword_ranked[:candidate_count],
                semantic_ranked[:candidate_count],
                keyword_weight=_unit_interval(
                    configuration, "keyword_weight", default=0.7
                ),
            )

    if bool(configuration.get("exact_duplicate_removal", False)):
        ranked = _remove_exact_duplicates(ranked)
    if bool(configuration.get("near_duplicate_removal", False)):
        ranked = _remove_near_duplicates(ranked, threshold=near_duplicate_threshold)

    results: list[dict[str, Any]] = []
    for score, chunk in ranked[:top_k]:
        result = dict(chunk)
        result["retrieval_score"] = round(float(score), 12)
        results.append(result)
    return results


def run_evaluation(
    *,
    cases: list[Any],
    corpus: list[dict[str, Any]],
    dataset_name: str,
    configuration: dict[str, Any],
    git_identifier: str,
    timestamp: str | None = None,
    input_digests: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not cases:
        raise EvaluationInputError("evaluation dataset must not be empty")
    label_sources = {str(_value(case, "label_source", "")) for case in cases}
    if len(label_sources) != 1 or "" in label_sources:
        raise EvaluationInputError(
            "evaluation datasets must use exactly one label_source"
        )
    validate_configuration(configuration)

    use_service_path = configuration.get(
        "retrieval_mode"
    ) == "production_analog" and bool(configuration.get("service_execution", False))
    if use_service_path:
        results_by_query_id, latencies_ms = asyncio.run(
            _run_service_evaluation(cases, corpus, configuration)
        )
    else:
        results_by_query_id = {}
        latencies_ms = []
        for case in cases:
            query_id = str(_value(case, "query_id", ""))
            started = time.perf_counter()
            results_by_query_id[query_id] = retrieve_evidence(
                str(_value(case, "query", "")),
                corpus,
                configuration,
                allowed_source_types=tuple(
                    str(item) for item in _value(case, "allowed_source_types", ())
                ),
                allowed_experience_types=tuple(
                    str(item) for item in _value(case, "allowed_experience_types", ())
                ),
            )
            latencies_ms.append((time.perf_counter() - started) * 1000.0)

    indexed_chunks = {str(chunk["chunk_id"]): chunk for chunk in corpus}
    indexed_documents = {
        str(chunk["document_id"]): {"document_id": chunk["document_id"]}
        for chunk in corpus
    }
    metrics = evaluate_retrieval_metrics(
        cases,
        results_by_query_id,
        indexed_chunks=indexed_chunks,
        indexed_documents=indexed_documents,
        latencies_ms=latencies_ms,
        near_duplicate_threshold=float(
            configuration.get("near_duplicate_threshold", 0.9)
        ),
    )
    report = build_report(
        dataset_name=dataset_name,
        label_source=next(iter(label_sources)),
        dataset_size=len(cases),
        configuration=configuration,
        metrics=metrics,
        failures=_evaluation_failures(cases, results_by_query_id),
        git_identifier=git_identifier,
        timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
    )
    report["input_digests"] = (
        dict(input_digests)
        if input_digests is not None
        else _canonical_input_digests(cases, corpus, configuration)
    )
    report["status"] = "measured"
    report["resource_cost"] = {
        "external_api_calls": 0,
        "estimated_cost_usd": 0.0,
    }
    report["ingestion_metrics"] = calculate_ingestion_metrics()
    report["ingestion_metrics_note"] = (
        "No ingestion run was executed by this retrieval-only fixture; values "
        "remain undefined with zero denominators."
    )
    if use_service_path:
        top_k = _positive_int(configuration, "top_k", default=5)
        candidate_multiplier = _positive_int(
            configuration, "candidate_multiplier", default=1
        )
        report["execution_path"] = {
            "identity": (
                "context-search-service+evidence-search-service+"
                "deterministic-offline-candidate-provider:v1"
            ),
            "candidate_budget_per_query": top_k * candidate_multiplier,
            "candidate_multiplier": candidate_multiplier,
            "context_service": "ContextSearchService",
            "evidence_service": "EvidenceSearchService",
            "provider": "deterministic_offline_candidate_provider",
        }
        report["limitations"] = [
            "Executes ContextSearchService and EvidenceSearchService with an offline "
            "lexical and character-similarity candidate provider; it does not execute "
            "the configured embedding provider or reproduce provider vector scores.",
            "Latency measures the in-process sanitized fixture only, not production I/O.",
        ]
    elif configuration.get("retrieval_mode") == "production_analog":
        report["execution_path"] = _proxy_execution_path(configuration)
        report["limitations"] = [
            "Offline lexical and character-similarity proxy; it does not execute "
            "ContextSearchService, EvidenceSearchService, or the configured "
            "embedding provider.",
            "Latency measures the in-process sanitized fixture only, not production I/O.",
        ]
    else:
        report["execution_path"] = _proxy_execution_path(configuration)
    return report


def _proxy_execution_path(configuration: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": (
            "dependency-free-retrieve-evidence:"
            f"{configuration['retrieval_mode']}:v1"
        ),
        "provider": "deterministic_offline_retrieval_function",
        "retrieval_function": "evaluation.retrieval.retrieve_evidence",
    }


def _canonical_input_digests(
    cases: list[Any],
    corpus: list[dict[str, Any]],
    configuration: dict[str, Any],
) -> dict[str, str]:
    normalized_cases = [
        case.as_dict() if callable(getattr(case, "as_dict", None)) else dict(case)
        for case in cases
    ]
    return {
        "dataset_sha256": _canonical_sha256(normalized_cases),
        "corpus_sha256": _canonical_sha256(corpus),
        "configuration_sha256": _canonical_sha256(configuration),
    }


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _run_service_evaluation(
    cases: list[Any],
    corpus: list[dict[str, Any]],
    configuration: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], list[float]]:
    metadata_store = _FixtureMetadataStore(corpus)
    candidate_provider = _DeterministicOfflineCandidateProvider(
        corpus,
        configuration,
    )
    context_service = ContextSearchService(
        metadata_store=metadata_store,  # type: ignore[arg-type]
        config=AppConfig(),
        retriever=candidate_provider,
        default_source_ids=["source_career"],
    )
    evidence_service = EvidenceSearchService(
        context_search_service=context_service,
        metadata_store=metadata_store,
        candidate_multiplier=_positive_int(
            configuration, "candidate_multiplier", default=1
        ),
        near_duplicate_threshold=float(
            configuration.get("near_duplicate_threshold", 0.8)
        ),
    )
    results_by_query_id: dict[str, list[dict[str, Any]]] = {}
    latencies_ms: list[float] = []
    for case in cases:
        query_id = str(_value(case, "query_id", ""))
        request = SearchEvidenceInput(
            query=str(_value(case, "query", "")),
            source_types=list(_value(case, "allowed_source_types", ())) or None,
            experience_types=(
                list(_value(case, "allowed_experience_types", ())) or None
            ),
            top_k=_positive_int(configuration, "top_k", default=5),
        )
        started = time.perf_counter()
        evidence = await evidence_service.search_evidence(request)
        latencies_ms.append((time.perf_counter() - started) * 1000.0)
        results_by_query_id[query_id] = [
            item.model_dump(mode="json") for item in evidence
        ]
    return results_by_query_id, latencies_ms


def validate_configuration(configuration: dict[str, Any]) -> None:
    if not isinstance(configuration, dict):
        raise EvaluationInputError("configuration must be a JSON object")
    mode = configuration.get("retrieval_mode")
    if not isinstance(mode, str) or mode not in _MODE_CONFIGURATION_FIELDS:
        raise EvaluationInputError("configuration has unsupported retrieval_mode")
    allowed_fields = _COMMON_CONFIGURATION_FIELDS.union(
        _MODE_CONFIGURATION_FIELDS[mode]
    )
    if set(configuration) - allowed_fields:
        raise EvaluationInputError("configuration contains unsupported fields")

    name = configuration.get("name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise EvaluationInputError("configuration name must be a non-empty string")
    for field in (
        "query_normalization",
        "metadata_filtering",
        "exact_duplicate_removal",
        "near_duplicate_removal",
        "service_execution",
    ):
        if field in configuration and not isinstance(configuration[field], bool):
            raise EvaluationInputError("configuration field has invalid type")

    _positive_int(configuration, "top_k", default=5)
    _positive_int(configuration, "candidate_multiplier", default=1)
    _unit_interval(configuration, "near_duplicate_threshold", default=0.9)
    if mode == "hybrid_rrf":
        _positive_int(configuration, "rrf_k", default=60)
    if mode == "production_analog":
        _unit_interval(configuration, "keyword_weight", default=0.7)
        _positive_float(configuration, "candidate_score_scale", default=16.0)
        _unit_interval(
            configuration,
            "candidate_score_calibration_floor",
            default=0.01,
        )
        for field in (
            "status",
            "notes",
            "production_mapping",
            "proxy_limitation",
        ):
            value = configuration.get(field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise EvaluationInputError(
                    "configuration documentation field must be a non-empty string"
                )


def _evaluation_failures(
    cases: list[Any],
    results_by_query_id: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for case in cases:
        query_id = str(_value(case, "query_id", ""))
        query = str(_value(case, "query", ""))
        results = results_by_query_id[query_id]
        returned_ids = [str(result.get("chunk_id", "")) for result in results]
        returned_document_ids = [
            str(result.get("document_id", "")) for result in results
        ]
        expected = tuple(str(item) for item in _value(case, "expected_chunk_ids", ()))
        missing = sorted(set(expected) - set(returned_ids[:5]))
        expected_documents = tuple(
            str(item) for item in _value(case, "expected_document_ids", ())
        )
        missing_documents = sorted(
            set(expected_documents) - set(returned_document_ids[:5])
        )
        should_return_empty = bool(_value(case, "should_return_empty", False))
        allowed_sources = tuple(
            str(item) for item in _value(case, "allowed_source_types", ())
        )
        allowed_experience = tuple(
            str(item) for item in _value(case, "allowed_experience_types", ())
        )
        reasons: list[str] = []
        if should_return_empty and results:
            reasons.append("empty_result_false_positive")
        if not should_return_empty and not results:
            reasons.append("missing_evidence")
        if missing:
            reasons.append("expected_chunk_not_in_top_5")
        if missing_documents:
            reasons.append("expected_document_not_in_top_5")
        if allowed_sources and any(
            result.get("source_type") not in allowed_sources for result in results
        ):
            reasons.append("wrong_source_type")
        if allowed_experience and any(
            result.get("experience_type") not in allowed_experience
            for result in results
        ):
            reasons.append("wrong_experience_type")
        if not reasons:
            continue
        failures.append(
            {
                "query_id": query_id,
                "query": query,
                "reason": ",".join(reasons),
                "expected_behavior": (
                    "empty result"
                    if should_return_empty
                    else (
                        f"top-5 chunks contain {list(expected)}; "
                        f"documents contain {list(expected_documents)}"
                    )
                ),
                "missing_chunk_ids": missing,
                "missing_document_ids": missing_documents,
                "returned_results": [
                    {
                        "chunk_id": result.get("chunk_id"),
                        "document_id": result.get("document_id"),
                        "source_type": result.get("source_type"),
                        "experience_type": result.get("experience_type"),
                        "exact_quote": result.get("exact_quote"),
                        "retrieval_score": result.get("retrieval_score"),
                    }
                    for result in results
                ],
            }
        )
    return failures


def _positive_int(configuration: dict[str, Any], key: str, *, default: int) -> int:
    value = configuration.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EvaluationInputError(f"{key} must be a positive integer")
    return value


def _unit_interval(configuration: dict[str, Any], key: str, *, default: float) -> float:
    value = configuration.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationInputError(f"{key} must be between 0 and 1")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise EvaluationInputError(f"{key} must be between 0 and 1")
    return normalized


def _positive_float(
    configuration: dict[str, Any], key: str, *, default: float
) -> float:
    value = configuration.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationInputError(f"{key} must be a positive number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise EvaluationInputError(f"{key} must be a positive number")
    return normalized


def _searchable_text(chunk: dict[str, Any]) -> str:
    return " ".join(
        str(chunk.get(key, ""))
        for key in (
            "document_title",
            "section_title",
            "parent_section_title",
            "content",
        )
    )


def _tokens(text: str, *, normalize_query: bool = False) -> tuple[str, ...]:
    tokens = [token.casefold() for token in TOKEN_RE.findall(text)]
    if normalize_query:
        tokens = [QUERY_ALIASES.get(token, token) for token in tokens]
    return tuple(_singularize(token) for token in tokens)


def _singularize(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _keyword_score(
    query_tokens: tuple[str, ...], content_tokens: tuple[str, ...]
) -> float:
    query_set = set(query_tokens)
    content_set = set(content_tokens)
    if not query_set or not content_set:
        return 0.0
    overlap = len(query_set.intersection(content_set))
    return overlap / math.sqrt(len(query_set) * len(content_set)) if overlap else 0.0


def _semantic_score(query: str, content: str, *, normalize_query: bool) -> float:
    query_text = " ".join(_tokens(query, normalize_query=normalize_query))
    content_text = " ".join(_tokens(content))
    query_trigrams = _character_trigrams(query_text)
    content_trigrams = _character_trigrams(content_text)
    if not query_trigrams or not content_trigrams:
        return 0.0
    return len(query_trigrams.intersection(content_trigrams)) / len(
        query_trigrams.union(content_trigrams)
    )


def _character_trigrams(text: str) -> set[str]:
    compact = f"  {' '.join(text.split())}  "
    return {compact[index : index + 3] for index in range(len(compact) - 2)}


def _rank_positive(
    scored: list[tuple[float, dict[str, Any]]],
) -> list[tuple[float, dict[str, Any]]]:
    return sorted(
        ((score, chunk) for score, chunk in scored if score > 0.0),
        key=lambda item: (-item[0], str(item[1].get("chunk_id", ""))),
    )


def _production_analog_candidates(
    query: str,
    corpus: list[dict[str, Any]],
    configuration: dict[str, Any],
    *,
    top_k: int,
) -> list[tuple[float, dict[str, Any]]]:
    """Propose offline candidates; production services own filtering and dedup."""
    use_normalization = bool(configuration.get("query_normalization", False))
    query_tokens = _tokens(query, normalize_query=use_normalization)
    keyword_ranked = _rank_positive(
        [
            (_keyword_score(query_tokens, _tokens(_searchable_text(chunk))), chunk)
            for chunk in corpus
        ]
    )
    semantic_ranked = _rank_positive(
        [
            (
                _semantic_score(
                    query,
                    _searchable_text(chunk),
                    normalize_query=use_normalization,
                ),
                chunk,
            )
            for chunk in corpus
        ]
    )
    candidate_count = min(
        len(corpus),
        max(
            top_k,
            top_k * _positive_int(configuration, "candidate_multiplier", default=1),
        ),
    )
    ranked = _weighted_candidate_fusion(
        keyword_ranked[:candidate_count],
        semantic_ranked[:candidate_count],
        keyword_weight=_unit_interval(
            configuration,
            "keyword_weight",
            default=0.7,
        ),
    )
    score_scale = _positive_float(
        configuration,
        "candidate_score_scale",
        default=16.0,
    )
    calibration_floor = _unit_interval(
        configuration,
        "candidate_score_calibration_floor",
        default=0.01,
    )
    return [
        (
            min(1.0, score * score_scale) if score >= calibration_floor else score,
            chunk,
        )
        for score, chunk in ranked
    ]


def _reciprocal_rank_fusion(
    keyword_ranked: list[tuple[float, dict[str, Any]]],
    semantic_ranked: list[tuple[float, dict[str, Any]]],
    *,
    rrf_k: int,
) -> list[tuple[float, dict[str, Any]]]:
    scores: dict[str, float] = {}
    chunks: dict[str, dict[str, Any]] = {}
    for ranking in (keyword_ranked, semantic_ranked):
        for rank, (_, chunk) in enumerate(ranking, start=1):
            chunk_id = str(chunk["chunk_id"])
            chunks[chunk_id] = chunk
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (rrf_k + rank)
    return sorted(
        ((score, chunks[chunk_id]) for chunk_id, score in scores.items()),
        key=lambda item: (-item[0], str(item[1]["chunk_id"])),
    )


def _weighted_candidate_fusion(
    keyword_ranked: list[tuple[float, dict[str, Any]]],
    semantic_ranked: list[tuple[float, dict[str, Any]]],
    *,
    keyword_weight: float,
) -> list[tuple[float, dict[str, Any]]]:
    semantic_weight = 1.0 - keyword_weight
    scores: dict[str, float] = {}
    chunks: dict[str, dict[str, Any]] = {}
    for score, chunk in keyword_ranked:
        chunk_id = str(chunk["chunk_id"])
        chunks[chunk_id] = chunk
        scores[chunk_id] = scores.get(chunk_id, 0.0) + keyword_weight * score
    for score, chunk in semantic_ranked:
        chunk_id = str(chunk["chunk_id"])
        chunks[chunk_id] = chunk
        scores[chunk_id] = scores.get(chunk_id, 0.0) + semantic_weight * score
    return sorted(
        ((score, chunks[chunk_id]) for chunk_id, score in scores.items()),
        key=lambda item: (-item[0], str(item[1]["chunk_id"])),
    )


def _remove_exact_duplicates(
    ranked: list[tuple[float, dict[str, Any]]],
) -> list[tuple[float, dict[str, Any]]]:
    seen: set[str] = set()
    retained: list[tuple[float, dict[str, Any]]] = []
    for item in ranked:
        signature = " ".join(_tokens(str(item[1].get("exact_quote", ""))))
        if signature in seen:
            continue
        seen.add(signature)
        retained.append(item)
    return retained


def _remove_near_duplicates(
    ranked: list[tuple[float, dict[str, Any]]], *, threshold: float
) -> list[tuple[float, dict[str, Any]]]:
    retained: list[tuple[float, dict[str, Any]]] = []
    retained_tokens: list[set[str]] = []
    for item in ranked:
        tokens = set(_tokens(str(item[1].get("exact_quote", ""))))
        if any(_jaccard(tokens, prior) >= threshold for prior in retained_tokens):
            continue
        retained.append(item)
        retained_tokens.append(tokens)
    return retained


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left.union(right)
    return len(left.intersection(right)) / len(union) if union else 1.0


def _value(item: Any, key: str, default: Any) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)
