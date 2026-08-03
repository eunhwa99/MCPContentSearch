"""Extractive career-evidence retrieval over the existing context search path."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import math
import re
import time
import uuid
from collections.abc import Mapping
from enum import Enum
from typing import Any

from pydantic import ValidationError

from core.exceptions import (
    EvidenceRetrievalError,
    EvidenceSearchError,
    InvalidEvidenceRequestError,
)
from core.models import EvidenceChunk, SearchEvidenceInput
from search.retrieval_pipeline import (
    BoundedRetrievalExecutor,
    RetrievalDeadlineExceeded,
)

logger = logging.getLogger(__name__)

_MAX_CANDIDATES = 800
_DEFAULT_CANDIDATE_MULTIPLIER = 3
_METADATA_FIELDS = ("company", "role", "project", "start_date", "end_date")
_WORD_PATTERN = re.compile(r"\w+", flags=re.UNICODE)
_DEFAULT_RETRIEVAL_TIMEOUT_SECONDS = 10.0
_DEFAULT_RETRIEVAL_MAX_CONCURRENCY = 10


class EvidenceSearchService:
    """Return stored career quotes proposed by the existing hybrid retriever."""

    def __init__(
        self,
        *,
        context_search_service,
        metadata_store,
        relevance_threshold: float = 0.2,
        near_duplicate_threshold: float = 0.8,
        candidate_multiplier: int = _DEFAULT_CANDIDATE_MULTIPLIER,
    ):
        if not 0.0 <= relevance_threshold <= 1.0:
            raise ValueError("relevance_threshold must be between 0 and 1")
        if not 0.0 <= near_duplicate_threshold <= 1.0:
            raise ValueError("near_duplicate_threshold must be between 0 and 1")
        if (
            isinstance(candidate_multiplier, bool)
            or not isinstance(candidate_multiplier, int)
            or candidate_multiplier < 1
        ):
            raise ValueError("candidate_multiplier must be a positive integer")
        self.context_search_service = context_search_service
        self.metadata_store = metadata_store
        self.relevance_threshold = relevance_threshold
        self.near_duplicate_threshold = near_duplicate_threshold
        self.candidate_multiplier = candidate_multiplier
        shared_executor = getattr(
            context_search_service,
            "retrieval_executor",
            None,
        )
        self._retrieval_executor = (
            shared_executor
            if isinstance(shared_executor, BoundedRetrievalExecutor)
            else None
        ) or BoundedRetrievalExecutor(
            timeout_seconds=_DEFAULT_RETRIEVAL_TIMEOUT_SECONDS,
            max_concurrency=_DEFAULT_RETRIEVAL_MAX_CONCURRENCY,
        )

    async def search_evidence(
        self,
        request: SearchEvidenceInput,
    ) -> list[EvidenceChunk]:
        """Search, hydrate from SQLite, filter, and remove duplicate quotes."""
        request_id = uuid.uuid4().hex
        started_at = time.perf_counter()
        candidate_count = 0
        query_hash = "unavailable"

        try:
            normalized_request = self._validated_request(request)
            query_hash = hashlib.sha256(
                normalized_request.query.encode("utf-8")
            ).hexdigest()[:16]
            results, candidate_count = await self._retrieve(normalized_request)
            self._log_completion(
                request_id=request_id,
                query_hash=query_hash,
                request=normalized_request,
                candidate_count=candidate_count,
                result_count=len(results),
                started_at=started_at,
            )
            return results
        except InvalidEvidenceRequestError:
            self._log_failure(
                request_id=request_id,
                query_hash=query_hash,
                error_type="invalid_request",
                started_at=started_at,
            )
            raise
        except RetrievalDeadlineExceeded:
            self._log_failure(
                request_id=request_id,
                query_hash=query_hash,
                error_type="timeout",
                started_at=started_at,
            )
            raise EvidenceSearchError(
                "Evidence retrieval timed out",
                error_type="timeout",
            ) from None
        except asyncio.CancelledError:
            self._log_failure(
                request_id=request_id,
                query_hash=query_hash,
                error_type="cancelled",
                started_at=started_at,
            )
            raise
        except EvidenceSearchError as exc:
            self._log_failure(
                request_id=request_id,
                query_hash=query_hash,
                error_type=exc.error_type,
                started_at=started_at,
            )
            raise
        except Exception:
            self._log_failure(
                request_id=request_id,
                query_hash=query_hash,
                error_type="internal_error",
                started_at=started_at,
            )
            raise EvidenceRetrievalError("Evidence retrieval failed") from None

    @staticmethod
    def _validated_request(request: SearchEvidenceInput) -> SearchEvidenceInput:
        try:
            if isinstance(request, SearchEvidenceInput):
                return request
            return SearchEvidenceInput.model_validate(request)
        except (TypeError, ValidationError, ValueError):
            raise InvalidEvidenceRequestError("Invalid evidence request") from None

    async def _retrieve(
        self,
        request: SearchEvidenceInput,
    ) -> tuple[list[EvidenceChunk], int]:
        max_candidates = min(
            _MAX_CANDIDATES,
            max(request.top_k * self.candidate_multiplier, request.top_k),
        )
        candidate_metadata_filters: dict[str, list[str]] = {}
        if request.source_types:
            candidate_metadata_filters["evidence_source_type"] = [
                _enum_value(item) for item in request.source_types
            ]
        if request.experience_types:
            candidate_metadata_filters["experience_type"] = [
                _enum_value(item) for item in request.experience_types
            ]
        if request.document_ids:
            candidate_metadata_filters["document_id"] = list(request.document_ids)
        search_kwargs: dict[str, Any] = {
            "filters": {"source_ids": ["source_career"]},
            "top_k": max_candidates,
            "candidate_budget": max_candidates,
        }
        if candidate_metadata_filters:
            search_kwargs["candidate_metadata_filters"] = candidate_metadata_filters
        deadline = self._retrieval_executor.deadline()
        search_context = self.context_search_service.search_context
        if self._supports_retrieval_deadline(search_context):
            search_kwargs["_retrieval_deadline"] = deadline
        response = await self._search_context_until(
            deadline,
            search_context,
            request.query,
            search_kwargs,
        )
        candidates = self._candidate_list(response)
        unique_evidence = await self._retrieval_executor.run_until(
            deadline,
            self._hydrate_filter_and_deduplicate,
            candidates,
            request,
        )
        return unique_evidence[: request.top_k], len(candidates)

    @staticmethod
    def _supports_retrieval_deadline(search_context: Any) -> bool:
        try:
            return any(
                parameter.name == "_retrieval_deadline"
                or parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in inspect.signature(search_context).parameters.values()
            )
        except (TypeError, ValueError):
            return False

    @staticmethod
    async def _search_context_until(
        deadline: float,
        search_context: Any,
        query: str,
        search_kwargs: dict[str, Any],
    ) -> Any:
        loop = asyncio.get_running_loop()
        if loop.time() >= deadline:
            raise RetrievalDeadlineExceeded() from None
        timeout = asyncio.timeout_at(deadline)
        try:
            async with timeout:
                return await search_context(query, **search_kwargs)
        except TimeoutError:
            if timeout.expired():
                raise RetrievalDeadlineExceeded() from None
            raise

    def _hydrate_filter_and_deduplicate(
        self,
        candidates: list[Any],
        request: SearchEvidenceInput,
    ) -> list[EvidenceChunk]:
        return self._deduplicate(self._hydrate_and_filter(candidates, request))

    @staticmethod
    def _candidate_list(response: Any) -> list[Any]:
        if isinstance(response, Mapping):
            results = response.get("results", [])
        else:
            results = getattr(response, "results", [])
        if isinstance(results, list):
            return results
        return list(results or [])

    def _hydrate_and_filter(
        self,
        candidates: list[Any],
        request: SearchEvidenceInput,
    ) -> list[EvidenceChunk]:
        hydrated: list[EvidenceChunk] = []
        requested_sources = {_enum_value(item) for item in request.source_types or []}
        requested_experiences = {
            _enum_value(item) for item in request.experience_types or []
        }
        requested_documents = set(request.document_ids or [])

        candidate_payloads: list[tuple[Mapping[str, Any], float | None, str]] = []
        chunk_ids: list[str] = []
        seen_chunk_ids: set[str] = set()
        for candidate in candidates:
            candidate_payload = _payload(candidate)
            chunk_id = str(candidate_payload.get("chunk_id") or "")
            if not chunk_id:
                continue
            raw_score = candidate_payload.get(
                "retrieval_score",
                candidate_payload.get("score"),
            )
            score = _optional_float(raw_score)
            if raw_score is not None and score is None:
                continue
            if score is not None and score < self.relevance_threshold:
                continue
            candidate_payloads.append((candidate_payload, score, chunk_id))
            if chunk_id not in seen_chunk_ids:
                seen_chunk_ids.add(chunk_id)
                chunk_ids.append(chunk_id)

        loader = getattr(self.metadata_store, "get_active_evidence_snapshots", None)
        if not callable(loader):
            raise RuntimeError("Evidence snapshot hydration is unavailable")
        snapshots = loader(chunk_ids) if chunk_ids else {}

        for candidate_payload, score, chunk_id in candidate_payloads:
            snapshot = snapshots.get(chunk_id)
            if not isinstance(snapshot, (tuple, list)) or len(snapshot) != 2:
                continue
            chunk, document = snapshot
            chunk_payload = _payload(chunk)
            document_id = str(
                chunk_payload.get("document_id")
                or candidate_payload.get("document_id")
                or ""
            )
            if not document_id:
                continue
            document_payload = _payload(document)
            source_type = _first_value(
                chunk_payload,
                document_payload,
                keys=("evidence_source_type", "source_type"),
            )
            experience_type = _first_value(
                chunk_payload,
                document_payload,
                keys=("experience_type",),
            )
            source_value = _enum_value(source_type)
            experience_value = _enum_value(experience_type)

            if not source_value:
                continue
            if requested_sources and source_value not in requested_sources:
                continue
            if requested_experiences and experience_value not in requested_experiences:
                continue
            if requested_documents and document_id not in requested_documents:
                continue

            exact_quote = str(
                _first_value(
                    chunk_payload,
                    document_payload,
                    keys=("exact_quote", "text", "content"),
                )
                or ""
            )
            if not exact_quote:
                continue

            raw_stored_score = _first_value(
                chunk_payload,
                document_payload,
                keys=("retrieval_score", "score"),
            )
            stored_score = _optional_float(raw_stored_score)
            if score is None and raw_stored_score is not None and stored_score is None:
                continue
            hydrated.append(
                EvidenceChunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    document_version_id=_optional_text(
                        _first_value(
                            chunk_payload,
                            document_payload,
                            keys=("document_version_id", "version_id"),
                        )
                    ),
                    source_type=source_type,
                    document_title=_optional_text(
                        _first_value(
                            chunk_payload,
                            document_payload,
                            keys=("document_title", "title"),
                        )
                    ),
                    section_title=_optional_text(
                        _first_value(
                            chunk_payload,
                            document_payload,
                            keys=("section_title",),
                        )
                    ),
                    parent_section_title=_optional_text(
                        _first_value(
                            chunk_payload,
                            document_payload,
                            keys=("parent_section_title",),
                        )
                    ),
                    exact_quote=exact_quote,
                    retrieval_score=score if score is not None else stored_score,
                    experience_type=experience_type,
                    file_name=_optional_text(
                        _first_value(
                            chunk_payload,
                            document_payload,
                            keys=("file_name",),
                        )
                    ),
                    metadata=self._metadata(chunk_payload, document_payload),
                )
            )
        return hydrated

    @staticmethod
    def _metadata(
        chunk_payload: Mapping[str, Any],
        document_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        metadata_value = chunk_payload.get("metadata")
        if not isinstance(metadata_value, Mapping):
            metadata_value = document_payload.get("metadata")
        metadata = dict(metadata_value) if isinstance(metadata_value, Mapping) else {}
        for field_name in _METADATA_FIELDS:
            value = _first_value(
                chunk_payload,
                document_payload,
                keys=(field_name,),
            )
            if value not in (None, ""):
                metadata.setdefault(field_name, value)
        return metadata

    def _deduplicate(self, evidence: list[EvidenceChunk]) -> list[EvidenceChunk]:
        unique: list[EvidenceChunk] = []
        normalized_quotes: set[str] = set()
        quote_tokens: list[set[str]] = []

        for item in evidence:
            normalized = " ".join(item.exact_quote.casefold().split())
            tokens = set(_WORD_PATTERN.findall(normalized))
            if normalized in normalized_quotes:
                continue
            if any(
                _jaccard(tokens, existing) >= self.near_duplicate_threshold
                for existing in quote_tokens
                if tokens and existing
            ):
                continue
            unique.append(item)
            normalized_quotes.add(normalized)
            quote_tokens.append(tokens)
        return unique

    @staticmethod
    def _log_completion(
        *,
        request_id: str,
        query_hash: str,
        request: SearchEvidenceInput,
        candidate_count: int,
        result_count: int,
        started_at: float,
    ) -> None:
        logger.info(
            "Evidence search completed request_id=%s query_hash=%s "
            "source_types=%s experience_types=%s document_id_count=%s "
            "retrieval_mode=context_hybrid candidate_count=%s result_count=%s "
            "latency_ms=%.3f",
            request_id,
            query_hash,
            [_enum_value(item) for item in request.source_types or []],
            [_enum_value(item) for item in request.experience_types or []],
            len(request.document_ids or []),
            candidate_count,
            result_count,
            (time.perf_counter() - started_at) * 1000,
        )

    @staticmethod
    def _log_failure(
        *,
        request_id: str,
        query_hash: str,
        error_type: str,
        started_at: float,
    ) -> None:
        logger.error(
            "Evidence search failed request_id=%s query_hash=%s "
            "retrieval_mode=context_hybrid error_type=%s latency_ms=%.3f",
            request_id,
            query_hash,
            error_type,
            (time.perf_counter() - started_at) * 1000,
        )


def _payload(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="python")
        return payload if isinstance(payload, Mapping) else {}
    attributes = getattr(value, "__dict__", None)
    return attributes if isinstance(attributes, Mapping) else {}


def _first_value(
    primary: Mapping[str, Any],
    secondary: Mapping[str, Any],
    *,
    keys: tuple[str, ...],
) -> Any:
    for payload in (primary, secondary):
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return value
    return None


def _enum_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value) if value is not None else ""


def _optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)
