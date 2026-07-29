from __future__ import annotations

import inspect
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from core.error_sanitizer import sanitize_error_text
from core.models import (
    DocumentSortBy,
    SearchFilters,
    SearchSortBy,
    SortOrder,
    SourceType,
    SyncStatus,
)
from core.sync_lifecycle import normalize_auth_ref, normalize_sync_job_phase
from indexing.background_tasks import safe_error_message
from search import debug_redaction

if TYPE_CHECKING:
    from fetching.connectors import SourceRegistry
    from indexing.ingestion_service import IngestionService
    from search.answer_service import CitationAnswerService
    from search.context_service import ContextSearchService
    from storage.metadata_store import MetadataStore

logger = logging.getLogger(__name__)
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
MAX_DOCUMENT_PAGE_SIZE = 50
SearchFiltersInput = Annotated[
    SearchFilters | None,
    Field(
        description=(
            "Inclusive UTC filters: source_id, source_ids, published_from, "
            "published_to, modified_from, modified_to, indexed_from, indexed_to."
        )
    ),
]


def _metadata_read_tool(mcp, *, open_world: bool = False):
    """Annotate reads that can persist SQLite schema or owner-heartbeat metadata."""
    try:
        if "annotations" in inspect.signature(mcp.tool).parameters:
            decorator = mcp.tool(
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=open_world,
                )
            )
        else:
            decorator = mcp.tool()
    except (TypeError, ValueError):
        decorator = mcp.tool()

    def register(func):
        registered = decorator(func)
        _hide_fastmcp_tool_validation_inputs(mcp, func.__name__)
        return registered

    return register


def _hide_fastmcp_tool_validation_inputs(mcp, tool_name: str) -> None:
    """Prevent generated outer argument models from echoing rejected nested inputs."""
    tool_manager = getattr(mcp, "_tool_manager", None)
    get_tool = getattr(tool_manager, "get_tool", None)
    if not callable(get_tool):
        return
    tool = get_tool(tool_name)
    fn_metadata = getattr(tool, "fn_metadata", None)
    arg_model = getattr(fn_metadata, "arg_model", None)
    model_config = getattr(arg_model, "model_config", None)
    model_rebuild = getattr(arg_model, "model_rebuild", None)
    if not isinstance(model_config, dict) or not callable(model_rebuild):
        return
    model_config["hide_input_in_errors"] = True
    model_rebuild(force=True)


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
        """특정 source sync를 durable queue에 넣거나 기존 active job을 재사용한다."""
        if ingestion_service is None:
            return {"status": "error", "message": "ingestion service is not configured"}
        if not hasattr(ingestion_service, "enqueue_sync_source"):
            return {
                "status": "error",
                "message": "ingestion service does not support durable sync enqueue",
            }
        if not _source_id_is_public(
            source_id,
            metadata_store,
            allowed_source_ids,
        ):
            message = safe_error_message(ValueError(f"Unknown source: {source_id}"))
            return {"status": "error", "message": message}
        try:
            job = await ingestion_service.enqueue_sync_source(source_id)
            return _safe_sync_job_payload(job)
        except Exception as exc:
            message = safe_error_message(exc)
            logger.error("Sync source error: %s", message)
            return {"status": "error", "message": message}

    @mcp.tool()
    async def sync_all() -> dict:
        """Enqueue or reuse all source syncs and return launch outcomes immediately.

        Keep only started/already_running source_id and job_id targets.
        Do not poll skipped or failed launches; report them immediately.
        Poll each exact job with separate short
        get_sync_status(source_id=..., job_id=...) requests using a 2-second initial interval:
        wait 2, 4, 8, then 10 seconds maximum between attempts as capped backoff.
        Stop at a 5-minute deadline or after
        three consecutive status errors or missing exact jobs.
        Report still-running job IDs without cancelling them.
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
            if not hasattr(ingestion_service, "enqueue_all"):
                return _sync_all_error_payload(
                    "ingestion service does not support durable bulk sync enqueue"
                )
            sync_all_callable = ingestion_service.enqueue_all
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

    @_metadata_read_tool(mcp)
    async def get_sync_status(source_id: str = "", job_id: str = "") -> dict:
        """Check one source, all sources when source_id is empty, or one exact job in a short request.

        Pass source_id and job_id together to poll an exact sync_all job. Use paced, bounded,
        separate requests; a client deadline stops observation, not the sync.
        """
        if metadata_store is None:
            if job_id:
                return {"source": None, "job": None}
            return {"sources": []}
        try:
            _refresh_registered_sources(metadata_store, source_registry)

            if job_id:
                requested_source_id = str(source_id or "")
                if not requested_source_id or not _source_id_is_public(
                    requested_source_id,
                    metadata_store,
                    allowed_source_ids,
                ):
                    return {"source": None, "job": None}
                source = metadata_store.get_source(requested_source_id)
                if source is None:
                    return {"source": None, "job": None}
                exact_job = metadata_store.get_sync_job(job_id)
                if exact_job is None:
                    return {"source": None, "job": None}
                job_source_id = str(
                    _model_payload(exact_job).get("source_id")
                    or getattr(exact_job, "source_id", "")
                )
                exact_source_id = requested_source_id
                if requested_source_id != job_source_id:
                    return {"source": None, "job": None}

                metadata_store.get_latest_sync_job(exact_source_id)
                exact_job = metadata_store.get_sync_job(job_id)
                if exact_job is None:
                    return {"source": None, "job": None}
                source = metadata_store.get_source(exact_source_id)
                if source is None:
                    return {"source": None, "job": None}
                return {
                    "source": _safe_source_payload(
                        source,
                        metadata_store=metadata_store,
                        source_registry=source_registry,
                    ),
                    "job": (
                        _safe_sync_job_payload(exact_job, include_progress_hints=True)
                        if exact_job
                        else None
                    ),
                }

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
            if job_id:
                return {
                    "status": "error",
                    "message": message,
                    "source": None,
                    "job": None,
                }
            if source_id:
                return {
                    "status": "error",
                    "message": message,
                    "source": None,
                    "latest_job": None,
                }
            return {"status": "error", "message": message, "sources": []}

    @_metadata_read_tool(mcp, open_world=True)
    async def search_context(
        query: str,
        filters: SearchFiltersInput = None,
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

    @_metadata_read_tool(mcp, open_world=True)
    async def search_documents(
        query: str,
        filters: SearchFiltersInput = None,
        sort_by: SearchSortBy = SearchSortBy.RELEVANCE,
        sort_order: SortOrder = SortOrder.DESC,
        top_k: int = 10,
    ) -> dict:
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
        search_documents_callable = context_search_service.search_documents
        search_parameters = inspect.signature(search_documents_callable).parameters
        if {"sort_by", "sort_order"} <= set(search_parameters):
            result = await search_documents_callable(
                query,
                filters=public_filters,
                sort_by=sort_by,
                sort_order=sort_order,
                top_k=top_k,
            )
        else:
            result = await search_documents_callable(
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

    @_metadata_read_tool(mcp)
    async def list_documents(
        filters: SearchFiltersInput = None,
        sort_by: DocumentSortBy = DocumentSortBy.INDEXED_AT,
        sort_order: SortOrder = SortOrder.DESC,
        page_size: Annotated[int, Field(ge=1, le=MAX_DOCUMENT_PAGE_SIZE)] = 20,
        cursor: str | None = None,
    ) -> dict:
        """Browse active documents without a semantic query, ordered by normalized timestamps."""
        if metadata_store is None:
            return {
                "status": "error",
                "message": "metadata store is not configured",
                "documents": [],
                "next_cursor": None,
            }
        try:
            public_filters, has_no_public_source = _public_filters(
                filters,
                metadata_store,
                allowed_source_ids,
            )
            if has_no_public_source or (
                allowed_source_ids is not None and not allowed_source_ids
            ):
                # Empty registry must not browse unrestricted rows: store treats
                # source_ids=[] as "no source filter", which would leave a stale
                # next_cursor after the public post-filter drops every document.
                return {"documents": [], "next_cursor": None}
            public_filters = _with_default_public_source_filter(
                public_filters,
                allowed_source_ids,
            )
            result = metadata_store.list_documents(
                filters=public_filters,
                sort_by=sort_by,
                sort_order=sort_order,
                page_size=page_size,
                cursor=cursor,
            )
            documents = [
                payload
                for payload in (
                    _document_list_result_payload(document)
                    for document in result.get("documents", [])
                )
                if _payload_source_is_public(
                    payload,
                    metadata_store,
                    allowed_source_ids,
                )
            ]
            return {
                "documents": documents,
                "next_cursor": result.get("next_cursor"),
            }
        except ValueError as exc:
            message = safe_error_message(exc)
            if message == "Invalid document cursor":
                raise ValueError(message) from None
            logger.error("List documents error: %s", message)
        except Exception as exc:
            message = safe_error_message(exc)
            logger.error("List documents error: %s", message)
        return {
            "status": "error",
            "message": message,
            "documents": [],
            "next_cursor": None,
        }

    @_metadata_read_tool(mcp)
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
            or document.deleted_at
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
    return sanitize_error_text(value)


def _redact_public_query_text(value: str):
    return debug_redaction.redact_debug_query_text(value)


def _safe_auth_ref(value):
    if not value:
        return value
    auth_ref = str(value)
    normalized = normalize_auth_ref(auth_ref)
    if normalized:
        return normalized
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
    else:
        normalized_phase = normalize_sync_job_phase(payload.get("phase"))
        if normalized_phase:
            payload["phase"] = normalized_phase
        else:
            payload.pop("phase", None)
        if "status_message" in payload:
            payload["status_message"] = _redact_public_error_text(
                payload["status_message"]
            )
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
    filters: SearchFilters | Mapping | None,
    metadata_store,
    allowed_source_ids: frozenset[str] | None,
) -> tuple[dict | None, bool]:
    if not filters:
        return None, False
    filter_payload = _filter_payload(filters)
    source_ids = _filter_source_ids(filter_payload)
    if source_ids is None:
        return filter_payload, False
    public_source_ids = [
        source_id
        for source_id in source_ids
        if _source_id_is_public(source_id, metadata_store, allowed_source_ids)
    ]
    if not public_source_ids:
        return None, True
    sanitized = dict(filter_payload)
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


def _filter_payload(filters: SearchFilters | Mapping) -> dict:
    if isinstance(filters, Mapping):
        return dict(filters)
    model_dump = getattr(filters, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json", exclude_none=True)
    raise TypeError("filters must serialize to a mapping")


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
        "published_at",
        "modified_at",
        "indexed_at",
        "date_provenance",
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


def _document_list_result_payload(item) -> dict:
    """Serialize only browse-safe metadata; stored full content and local paths stay private."""
    allowed_keys = {
        "document_id",
        "source_id",
        "title",
        "url",
        "canonical_url",
        "platform",
        "published_at",
        "modified_at",
        "indexed_at",
        "date_provenance",
    }
    raw_payload = _model_payload(item)
    return {
        key: value
        for key, value in raw_payload.items()
        if key in allowed_keys
    }
