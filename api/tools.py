from __future__ import annotations

import inspect
import logging
import math
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP
from pydantic import StrictFloat

from core.models import SourceType, SyncStatus
from indexing.background_tasks import safe_error_message
from indexing.ingestion_service import (
    DEFAULT_SYNC_WAIT_POLL_INTERVAL_SECONDS,
    DEFAULT_SYNC_WAIT_TIMEOUT_SECONDS,
    MAX_SYNC_WAIT_POLL_INTERVAL_SECONDS,
    MAX_SYNC_WAIT_TIMEOUT_SECONDS,
    MIN_SYNC_WAIT_POLL_INTERVAL_SECONDS,
)
from search import debug_redaction

if TYPE_CHECKING:
    from fetching.connectors import SourceRegistry
    from indexing.ingestion_service import IngestionService
    from search.answer_service import CitationAnswerService
    from search.context_service import ContextSearchService
    from storage.metadata_store import MetadataStore

logger = logging.getLogger(__name__)
SAFE_PUBLIC_ENV_REF_RE = re.compile(r"^env:[A-Z_][A-Z0-9_]*$")
REGISTERABLE_SOURCE_ATTRS = (
    "source_id",
    "source_type",
    "name",
    "enabled",
    "auth_ref",
    "sync_status",
    "last_synced_at",
    "last_error",
    "created_at",
    "updated_at",
)


def register_tools(
    mcp: FastMCP,
    ingestion_service: IngestionService | None = None,
    context_search_service: ContextSearchService | None = None,
    answer_service: CitationAnswerService | None = None,
    metadata_store: MetadataStore | None = None,
    source_registry: SourceRegistry | None = None,
):
    """Register the retained slim ContextWiki MCP tool surface."""
    allowed_source_ids = _source_registry_ids(source_registry)

    def _sync_all_error_payload(message: str) -> dict:
        return {
            "status": "failed",
            "message": message,
            "summary": _public_bulk_sync_summary(
                {"requested_at": datetime.now(timezone.utc).isoformat()},
                [],
            ),
            "results": [],
        }

    def _wait_for_sync_all_error_payload(message: str) -> dict:
        return {
            "status": "error",
            "message": message,
            "summary": _public_bulk_wait_summary({}, []),
            "results": [],
        }

    @mcp.tool()
    async def list_sources() -> dict:
        """등록된 ContextWiki source 목록 조회"""
        try:
            _refresh_registered_sources(metadata_store, source_registry)
            if metadata_store is not None:
                sources = metadata_store.list_sources()
            elif source_registry is not None:
                sources = source_registry.list_sources()
            else:
                sources = []
            sources = [
                source
                for source in sources
                if _source_id_is_public(
                    getattr(source, "source_id", ""),
                    metadata_store,
                    allowed_source_ids,
                )
            ]
            return {
                "sources": [
                    _safe_source_payload(
                        source,
                        metadata_store=metadata_store,
                        source_registry=source_registry,
                    )
                    for source in sources
                ]
            }
        except Exception as exc:
            message = safe_error_message(exc)
            logger.error("List sources error: %s", message)
            return {"status": "error", "message": message, "sources": []}

    @mcp.tool()
    async def sync_source(source_id: str) -> dict:
        """특정 source sync를 시작하거나 기존 running job을 재사용한다."""
        if ingestion_service is None:
            return {"status": "error", "message": "ingestion service is not configured"}
        if not hasattr(ingestion_service, "start_sync_source"):
            return {
                "status": "error",
                "message": "ingestion service does not support background sync launch",
            }
        if not _source_id_is_public(
            source_id,
            metadata_store,
            allowed_source_ids,
        ):
            message = safe_error_message(ValueError(f"Unknown source: {source_id}"))
            return {"status": "error", "message": message}
        try:
            job = await ingestion_service.start_sync_source(source_id)
            return _safe_sync_job_payload(job)
        except Exception as exc:
            message = safe_error_message(exc)
            logger.error("Sync source error: %s", message)
            return {"status": "error", "message": message}

    @mcp.tool()
    async def sync_all() -> dict:
        """Start or reuse all source syncs in the background and return launch outcomes immediately.

        Poll get_sync_status(source_id) for each source before treating its sync as complete.
        """
        if ingestion_service is None:
            return _sync_all_error_payload("ingestion service is not configured")
        try:
            _refresh_registered_sources(metadata_store, source_registry)
            public_source_ids = _ordered_public_source_ids(
                metadata_store,
                source_registry,
                allowed_source_ids,
            )
            public_bulk_sync_requires_filtering = _public_bulk_sync_requires_filtering(
                source_registry,
                public_source_ids,
            )
            sync_all_callable = ingestion_service.sync_all
            if public_bulk_sync_requires_filtering:
                sync_all_signature = inspect.signature(sync_all_callable)
                if "source_ids" not in sync_all_signature.parameters:
                    return _sync_all_error_payload(
                        "ingestion service does not support public bulk sync filtering"
                    )
                result = await sync_all_callable(source_ids=public_source_ids)
            else:
                result = await sync_all_callable()
            sync_results = []
            for item in result.get("results", []):
                source_id = str(item.get("source_id", ""))
                if not _source_id_is_public(
                    source_id,
                    metadata_store,
                    allowed_source_ids,
                ):
                    continue
                source = metadata_store.get_source(source_id) if metadata_store is not None else None
                sync_results.append(
                    {
                        "source_id": source_id,
                        "launch_outcome": item.get("launch_outcome", ""),
                        "message": _redact_public_error_text(item.get("message", "")),
                        "source": (
                            _safe_source_payload(
                                source,
                                metadata_store=metadata_store,
                                source_registry=source_registry,
                            )
                            if source
                            else None
                        ),
                        "job": _safe_sync_job_payload(item.get("job")) if item.get("job") else None,
                    }
                )
            summary = _public_bulk_sync_summary(result.get("summary", {}), sync_results)
            return {
                "status": _public_bulk_sync_status(
                    sync_results,
                    result.get("status", "accepted"),
                    upstream_result_count=len(result.get("results", [])),
                ),
                "summary": summary,
                "results": sync_results,
            }
        except Exception as exc:
            message = safe_error_message(exc)
            logger.error("Sync all error: %s", message)
            return _sync_all_error_payload(message)

    @mcp.tool()
    async def wait_for_sync_all(
        timeout_seconds: StrictFloat = DEFAULT_SYNC_WAIT_TIMEOUT_SECONDS,
        poll_interval_seconds: StrictFloat = DEFAULT_SYNC_WAIT_POLL_INTERVAL_SECONDS,
    ) -> dict:
        """Start or reuse all source syncs and wait for their exact jobs to finish."""
        try:
            timeout_seconds, poll_interval_seconds = _validate_public_sync_wait_options(
                timeout_seconds,
                poll_interval_seconds,
            )
        except ValueError as exc:
            return _wait_for_sync_all_error_payload(str(exc))
        if ingestion_service is None:
            return _wait_for_sync_all_error_payload(
                "ingestion service is not configured"
            )
        wait_callable = getattr(ingestion_service, "wait_for_sync_all", None)
        if not callable(wait_callable):
            return _wait_for_sync_all_error_payload(
                "ingestion service does not support completion waiting"
            )
        try:
            _refresh_registered_sources(metadata_store, source_registry)
            public_source_ids = _ordered_public_source_ids(
                metadata_store,
                source_registry,
                allowed_source_ids,
            )
            public_bulk_sync_requires_filtering = _public_bulk_sync_requires_filtering(
                source_registry,
                public_source_ids,
            )
            wait_kwargs = {
                "timeout_seconds": timeout_seconds,
                "poll_interval_seconds": poll_interval_seconds,
            }
            if public_bulk_sync_requires_filtering:
                wait_signature = inspect.signature(wait_callable)
                if "source_ids" not in wait_signature.parameters:
                    return _wait_for_sync_all_error_payload(
                        "ingestion service does not support public bulk sync filtering"
                    )
                wait_kwargs["source_ids"] = public_source_ids
            result = await wait_callable(**wait_kwargs)
            sync_results = []
            for item in result.get("results", []):
                source_id = str(item.get("source_id", ""))
                if not _source_id_is_public(
                    source_id,
                    metadata_store,
                    allowed_source_ids,
                ):
                    continue
                source = metadata_store.get_source(source_id) if metadata_store is not None else None
                sync_results.append(
                    {
                        "source_id": source_id,
                        "launch_outcome": item.get("launch_outcome", ""),
                        "completion_outcome": item.get("completion_outcome", ""),
                        "message": _redact_public_error_text(item.get("message", "")),
                        "source": (
                            _safe_source_payload(
                                source,
                                metadata_store=metadata_store,
                                source_registry=source_registry,
                            )
                            if source
                            else None
                        ),
                        "job": _safe_sync_job_payload(item.get("job")) if item.get("job") else None,
                    }
                )
            summary = _public_bulk_wait_summary(result.get("summary", {}), sync_results)
            return {
                "status": _public_bulk_wait_status(
                    sync_results,
                    result.get("status", "completed"),
                    upstream_result_count=len(result.get("results", [])),
                ),
                "summary": summary,
                "results": sync_results,
            }
        except Exception as exc:
            message = safe_error_message(exc)
            logger.error("Wait for sync all error: %s", message)
            return _wait_for_sync_all_error_payload(message)

    @mcp.tool()
    async def get_sync_status(source_id: str = "") -> dict:
        """source 및 sync job 상태 조회"""
        if metadata_store is None:
            return {"sources": []}
        try:
            _refresh_registered_sources(metadata_store, source_registry)

            if source_id:
                source = metadata_store.get_source(source_id)
                if not source or not _source_id_is_public(
                    source_id,
                    metadata_store,
                    allowed_source_ids,
                ):
                    return {
                        "source": None,
                        "latest_job": None,
                    }
                latest_job = metadata_store.get_latest_sync_job(source_id)
                source = metadata_store.get_source(source_id) or source
                return {
                    "source": _safe_source_payload(
                        source,
                        metadata_store=metadata_store,
                        source_registry=source_registry,
                    )
                    if source
                    else None,
                    "latest_job": (
                        _safe_sync_job_payload(latest_job, include_progress_hints=True)
                        if latest_job
                        else None
                    ),
                }

            statuses = []
            for source in metadata_store.list_sources():
                if not _source_id_is_public(
                    source.source_id,
                    metadata_store,
                    allowed_source_ids,
                ):
                    continue
                latest_job = metadata_store.get_latest_sync_job(source.source_id)
                source = metadata_store.get_source(source.source_id) or source
                statuses.append(
                    {
                        "source": _safe_source_payload(
                            source,
                            metadata_store=metadata_store,
                            source_registry=source_registry,
                        ),
                        "latest_job": (
                            _safe_sync_job_payload(latest_job, include_progress_hints=True)
                            if latest_job
                            else None
                        ),
                    }
                )
            return {"sources": statuses}
        except Exception as exc:
            message = safe_error_message(exc)
            logger.error("Get sync status error: %s", message)
            if source_id:
                return {
                    "status": "error",
                    "message": message,
                    "source": None,
                    "latest_job": None,
                }
            return {"status": "error", "message": message, "sources": []}

    @mcp.tool()
    async def search_context(
        query: str,
        filters: dict = None,
        top_k: int = 10,
        include_debug: bool = False,
    ) -> dict:
        """Find focused, citation-ready chunk evidence for answering a user's query."""
        if context_search_service is None:
            return {"query": _redact_public_query_text(query), "results": []}
        public_filters, has_no_public_source = _public_filters(
            filters,
            metadata_store,
            allowed_source_ids,
        )
        if has_no_public_source:
            return {
                "query": _redact_public_query_text(query),
                "results": [],
                "debug": {
                    "retrieval_queries": [],
                    "effective_term_groups": [],
                },
            }
        public_filters = _with_default_public_source_filter(
            public_filters,
            allowed_source_ids,
        )
        result = await context_search_service.search_context(
            query,
            filters=public_filters,
            top_k=top_k,
            include_debug=include_debug,
        )
        results = [
            payload
            for payload in (
                _search_context_result_payload(item)
                for item in result["results"]
            )
            if _payload_source_is_public(payload, metadata_store, allowed_source_ids)
        ]
        payload = {
            "query": _redact_public_query_text(result["query"]),
            "results": results,
            "debug": result.get("debug", {}),
        }
        if include_debug and "debug" in result:
            payload["debug"] = result["debug"]
        return payload

    @mcp.tool()
    async def search_documents(query: str, filters: dict = None, top_k: int = 10) -> dict:
        """Find one row per relevant document with its representative matched_context."""
        if context_search_service is None:
            return {"query": _redact_public_query_text(query), "results": []}
        public_filters, has_no_public_source = _public_filters(
            filters,
            metadata_store,
            allowed_source_ids,
        )
        if has_no_public_source:
            return {"query": _redact_public_query_text(query), "results": []}
        public_filters = _with_default_public_source_filter(
            public_filters,
            allowed_source_ids,
        )
        result = await context_search_service.search_documents(
            query,
            filters=public_filters,
            top_k=top_k,
        )
        results = [
            payload
            for payload in (
                _search_documents_result_payload(item)
                for item in result["results"]
            )
            if _payload_source_is_public(payload, metadata_store, allowed_source_ids)
        ]
        return {
            "query": _redact_public_query_text(result["query"]),
            "results": results,
        }

    @mcp.tool()
    async def fetch_context(document_id: str = "", chunk_id: str = "") -> dict:
        """Optionally drill into exact stored document or chunk content after its ID is known."""
        if metadata_store is None:
            return {"status": "error", "message": "metadata store is not configured"}
        if not document_id and not chunk_id:
            return {"status": "error", "message": "document_id or chunk_id is required"}

        if chunk_id:
            chunk = metadata_store.get_chunk(chunk_id)
            if chunk and not _model_source_is_public(chunk, metadata_store, allowed_source_ids):
                chunk = None
            return {
                "chunk": chunk.model_dump(mode="json") if chunk else None,
            }

        document = metadata_store.get_document(document_id)
        if (
            not document
            or not _model_source_is_public(document, metadata_store, allowed_source_ids)
            or getattr(document, "deleted_at", "")
        ):
            return {
                "document": None,
                "chunks": [],
            }
        chunks = metadata_store.list_chunks_for_document(document_id)
        chunks = [
            chunk
            for chunk in chunks
            if _model_source_is_public(chunk, metadata_store, allowed_source_ids)
        ]
        return {
            "document": document.model_dump(mode="json") if document else None,
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
        }

def _source_registry_ids(source_registry) -> frozenset[str] | None:
    if source_registry is None:
        return None
    return frozenset(
        str(source.source_id)
        for source in source_registry.list_sources()
        if getattr(source, "source_id", "")
    )


def _ordered_public_source_ids(
    metadata_store,
    source_registry,
    allowed_source_ids: frozenset[str] | None,
) -> list[str] | None:
    if allowed_source_ids is None or source_registry is None:
        return None
    return [
        str(source.source_id)
        for source in source_registry.list_sources()
        if _source_id_is_public(source.source_id, metadata_store, allowed_source_ids)
    ]


def _public_bulk_sync_requires_filtering(source_registry, ordered_public_source_ids: list[str] | None) -> bool:
    if source_registry is None or ordered_public_source_ids is None:
        return False
    return len(ordered_public_source_ids) != len(source_registry.list_sources())


def _refresh_registered_sources(metadata_store, source_registry) -> None:
    if metadata_store is None or source_registry is None:
        return
    register_source = getattr(metadata_store, "register_source", None)
    if not callable(register_source):
        return
    for source in source_registry.list_sources():
        if all(hasattr(source, attr) for attr in REGISTERABLE_SOURCE_ATTRS):
            register_source(source)


def _model_payload(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    if isinstance(model, dict):
        return dict(model)
    return {}


def _redact_public_error_text(value):
    if not value:
        return value
    return safe_error_message(ValueError(str(value)))


def _redact_public_query_text(value: str):
    return debug_redaction.redact_debug_query_text(value)


def _safe_auth_ref(value):
    if not value:
        return value
    auth_ref = str(value)
    if SAFE_PUBLIC_ENV_REF_RE.match(auth_ref):
        return auth_ref
    return "<redacted>"


def _safe_source_payload(source, *, metadata_store=None, source_registry=None) -> dict:
    payload = _model_payload(source)
    if "auth_ref" in payload:
        payload["auth_ref"] = _safe_auth_ref(payload["auth_ref"])
    if "last_error" in payload:
        payload["last_error"] = _redact_public_error_text(payload["last_error"])
    payload.update(_safe_source_status_surface(source, metadata_store, source_registry))
    return payload


def _safe_sync_job_payload(job, *, include_progress_hints: bool = False) -> dict:
    payload = _model_payload(job)
    if include_progress_hints and payload.get("status") != "running":
        include_progress_hints = False
    if not include_progress_hints:
        for key in (
            "phase",
            "upstream_total_pages",
            "upstream_fetched_pages",
            "last_progress_at",
            "status_message",
        ):
            payload.pop(key, None)
    elif "status_message" in payload:
        payload["status_message"] = _redact_public_error_text(payload["status_message"])
    if "error_message" in payload:
        payload["error_message"] = _redact_public_error_text(payload["error_message"])
    return payload


def _safe_source_status_surface(source, metadata_store, source_registry) -> dict:
    source_id = getattr(source, "source_id", "")
    snapshot = {}
    if metadata_store is not None:
        get_snapshot = getattr(metadata_store, "get_source_status_snapshot", None)
        if callable(get_snapshot):
            snapshot = dict(get_snapshot(source_id))
    latest_success_at = snapshot.get("latest_success_at", "") or getattr(source, "last_synced_at", "")
    latest_failure_reason = _redact_public_error_text(
        snapshot.get("latest_failure_reason", "") or getattr(source, "last_error", "")
    )
    persisted_stale_cleanup_disabled_reason = _redact_public_error_text(
        getattr(source, "stale_cleanup_disabled_reason", "")
    )
    return {
        "latest_success_at": latest_success_at,
        "latest_failure_at": snapshot.get("latest_failure_at", ""),
        "document_count": snapshot.get("document_count", 0),
        "chunk_count": snapshot.get("chunk_count", 0),
        "latest_failure_reason": latest_failure_reason,
        "stale_cleanup_disabled_reason": _stale_cleanup_disabled_reason(
            source,
            source_registry=source_registry,
            latest_failure_reason=latest_failure_reason,
            persisted_reason=persisted_stale_cleanup_disabled_reason,
        ),
    }


def _public_bulk_sync_status(
    sync_results: list[dict],
    fallback_status: str,
    *,
    upstream_result_count: int = 0,
) -> str:
    outcomes = {item.get("launch_outcome", "") for item in sync_results}
    if not outcomes:
        if upstream_result_count > 0:
            return "accepted"
        return fallback_status or "accepted"
    if outcomes.issubset({"started", "already_running", "skipped"}):
        return "accepted"
    if outcomes == {"failed"}:
        return "failed"
    return "partial"


def _public_bulk_sync_summary(upstream_summary: dict, sync_results: list[dict]) -> dict:
    summary = {
        "total_sources": len(sync_results),
        "started": sum(1 for item in sync_results if item.get("launch_outcome") == "started"),
        "already_running": sum(
            1 for item in sync_results if item.get("launch_outcome") == "already_running"
        ),
        "skipped": sum(1 for item in sync_results if item.get("launch_outcome") == "skipped"),
        "failed": sum(1 for item in sync_results if item.get("launch_outcome") == "failed"),
    }
    if "requested_at" in upstream_summary:
        summary["requested_at"] = upstream_summary["requested_at"]
    return summary


def _validate_public_sync_wait_options(
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> tuple[float, float]:
    if isinstance(timeout_seconds, bool) or isinstance(poll_interval_seconds, bool):
        raise ValueError(
            "timeout_seconds and poll_interval_seconds must be numeric"
        )
    try:
        normalized_timeout = float(timeout_seconds)
        normalized_poll_interval = float(poll_interval_seconds)
    except (TypeError, ValueError):
        raise ValueError(
            "timeout_seconds and poll_interval_seconds must be numeric"
        ) from None
    if not math.isfinite(normalized_timeout) or not (
        0 < normalized_timeout <= MAX_SYNC_WAIT_TIMEOUT_SECONDS
    ):
        raise ValueError(
            f"timeout_seconds must be greater than 0 and at most "
            f"{MAX_SYNC_WAIT_TIMEOUT_SECONDS:g}"
        )
    if not math.isfinite(normalized_poll_interval) or not (
        MIN_SYNC_WAIT_POLL_INTERVAL_SECONDS
        <= normalized_poll_interval
        <= MAX_SYNC_WAIT_POLL_INTERVAL_SECONDS
    ):
        raise ValueError(
            f"poll_interval_seconds must be between "
            f"{MIN_SYNC_WAIT_POLL_INTERVAL_SECONDS:g} and "
            f"{MAX_SYNC_WAIT_POLL_INTERVAL_SECONDS:g}"
        )
    return normalized_timeout, normalized_poll_interval


def _public_bulk_wait_status(
    sync_results: list[dict],
    fallback_status: str,
    *,
    upstream_result_count: int = 0,
) -> str:
    outcomes = {item.get("completion_outcome", "") for item in sync_results}
    if not outcomes:
        if upstream_result_count > 0:
            return "completed"
        return fallback_status or "completed"
    if outcomes.issubset({"succeeded", "skipped"}):
        return "completed"
    if "failed" in outcomes and outcomes.issubset({"failed", "skipped"}):
        return "failed"
    if "timed_out" in outcomes and outcomes.issubset({"timed_out", "skipped"}):
        return "timed_out"
    return "partial"


def _public_bulk_wait_summary(upstream_summary: dict, sync_results: list[dict]) -> dict:
    summary = {
        "total_sources": len(sync_results),
        "succeeded": sum(
            1 for item in sync_results if item.get("completion_outcome") == "succeeded"
        ),
        "failed": sum(
            1 for item in sync_results if item.get("completion_outcome") == "failed"
        ),
        "skipped": sum(
            1 for item in sync_results if item.get("completion_outcome") == "skipped"
        ),
        "timed_out": sum(
            1 for item in sync_results if item.get("completion_outcome") == "timed_out"
        ),
    }
    for key in ("requested_at", "completed_at"):
        if key in upstream_summary:
            summary[key] = upstream_summary[key]
    return summary


def _stale_cleanup_disabled_reason(
    source,
    *,
    source_registry,
    latest_failure_reason: str,
    persisted_reason: str,
) -> str:
    if persisted_reason:
        return persisted_reason
    disabled_reason = ""
    connector_supports_cleanup = None
    if source_registry is not None:
        try:
            connector = source_registry.get_connector(getattr(source, "source_id", ""))
        except Exception:
            connector = None
        if connector is not None:
            connector_supports_cleanup = getattr(connector, "supports_stale_cleanup", False)
            disabled_reason = _redact_public_error_text(
                getattr(connector, "stale_cleanup_disabled_reason", "")
                or getattr(connector, "disabled_reason", "")
            )
    if not getattr(source, "enabled", True):
        return disabled_reason or latest_failure_reason
    if latest_failure_reason and "incomplete" in latest_failure_reason.lower():
        return latest_failure_reason
    if disabled_reason:
        return disabled_reason
    source_type = getattr(source, "source_type", "")
    if source_type == SourceType.TISTORY:
        return "Stale cleanup is disabled because this source connector does not guarantee complete snapshots."
    if connector_supports_cleanup is True:
        return ""
    if getattr(source, "sync_status", None) == SyncStatus.FAILED:
        return latest_failure_reason
    return "Stale cleanup is disabled for this source."


def _model_source_is_public(model, metadata_store, allowed_source_ids: frozenset[str] | None) -> bool:
    source_id = getattr(model, "source_id", "")
    return _source_id_is_public(source_id, metadata_store, allowed_source_ids)


def _payload_source_is_public(
    payload,
    metadata_store,
    allowed_source_ids: frozenset[str] | None,
) -> bool:
    if not isinstance(payload, dict):
        return allowed_source_ids is None
    return _source_id_is_public(payload.get("source_id", ""), metadata_store, allowed_source_ids)


def _public_filters(
    filters: dict | None,
    metadata_store,
    allowed_source_ids: frozenset[str] | None,
) -> tuple[dict | None, bool]:
    if not filters:
        return filters, False
    source_ids = _filter_source_ids(filters or {})
    if source_ids is None:
        return filters, False
    public_source_ids = [
        source_id
        for source_id in source_ids
        if _source_id_is_public(source_id, metadata_store, allowed_source_ids)
    ]
    if not public_source_ids:
        return None, True
    sanitized = dict(filters)
    sanitized.pop("source_id", None)
    sanitized["source_ids"] = public_source_ids
    return sanitized, False


def _with_default_public_source_filter(
    filters: dict | None,
    allowed_source_ids: frozenset[str] | None,
) -> dict | None:
    if allowed_source_ids is None or _filter_source_ids(filters or {}):
        return filters
    public_filters = dict(filters or {})
    public_filters["source_ids"] = sorted(allowed_source_ids)
    return public_filters


def _filter_source_ids(filters: dict) -> list[str] | None:
    normalized = []
    for key in ("source_ids", "source_id"):
        value = filters.get(key)
        if not value:
            continue
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, list | tuple | set):
            values = list(value)
        else:
            values = [value]
        for source_id in values:
            if source_id and source_id not in normalized:
                normalized.append(str(source_id))
    return normalized or None


def _source_id_is_public(
    source_id: str,
    metadata_store,
    allowed_source_ids: frozenset[str] | None,
) -> bool:
    if not source_id:
        return allowed_source_ids is None
    normalized_source_id = str(source_id)
    if allowed_source_ids is not None:
        return normalized_source_id in allowed_source_ids
    if metadata_store is None:
        return True
    get_source = getattr(metadata_store, "get_source", None)
    if not callable(get_source):
        return True
    return get_source(normalized_source_id) is not None


def _insufficient_answer_for_filtered_sources(question: str) -> dict:
    return {
        "question": _redact_public_query_text(question),
        "answer": "No retained source matched the requested filters.",
        "evidence_status": "insufficient",
        "citations": [],
        "used_chunks": [],
    }


def _search_context_result_payload(item):
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json", exclude={"vector_score", "metadata_priority"})
    if isinstance(item, dict):
        return {
            key: value
            for key, value in item.items()
            if key not in {"vector_score", "metadata_priority"}
        }
    return item


def _search_documents_result_payload(item):
    serialization_error = "search_documents result must serialize to a mapping"
    allowed_keys = {
        "document_id",
        "chunk_id",
        "source_id",
        "source_type",
        "title",
        "url",
        "path",
        "score",
        "matched_context",
    }
    try:
        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            raw_payload = model_dump(
                mode="json",
                include=allowed_keys,
            )
        elif isinstance(item, Mapping):
            raw_payload = item
        else:
            raise TypeError(serialization_error)
        if not isinstance(raw_payload, Mapping):
            raise TypeError(serialization_error)
        payload = {
            key: value
            for key, value in raw_payload.items()
            if key in allowed_keys
        }
    except Exception:
        raise TypeError(serialization_error) from None
    if "matched_context" not in payload:
        raise ValueError(
            "search_documents result is missing required field 'matched_context'"
        )
    if not isinstance(payload["matched_context"], str):
        raise TypeError(
            "search_documents result field 'matched_context' must be a string"
        )
    return payload
