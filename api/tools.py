from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from core.models import SourceType, SyncJobStatus, SyncStatus
from indexing.background_tasks import safe_error_message
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

    @mcp.tool()
    async def list_sources() -> dict:
        """등록된 ContextWiki source 목록 조회"""
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
        try:
            job = await ingestion_service.start_sync_source(source_id)
            return _safe_sync_job_payload(job)
        except Exception as exc:
            message = safe_error_message(exc)
            logger.error("Sync source error: %s", message)
            return {"status": "error", "message": message}

    @mcp.tool()
    async def sync_all() -> dict:
        """retained source 전체 sync 실행"""
        if ingestion_service is None:
            return {"status": "error", "message": "ingestion service is not configured"}
        try:
            result = await ingestion_service.sync_all()
        except Exception as exc:
            message = safe_error_message(exc)
            logger.error("Sync all error: %s", message)
            return {"status": "error", "message": message}

        sync_results = []
        for item in result.get("results", []):
            source_id = str(item.get("source_id", ""))
            source = metadata_store.get_source(source_id) if metadata_store is not None else None
            sync_results.append(
                {
                    "source_id": source_id,
                    "sync_outcome": item.get("sync_outcome", ""),
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
        return {
            "status": result.get("status", "completed"),
            "summary": result.get("summary", {}),
            "results": sync_results,
        }

    @mcp.tool()
    async def get_sync_status(source_id: str = "") -> dict:
        """source 및 sync job 상태 조회"""
        if metadata_store is None:
            return {"sources": []}
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
                "latest_job": _safe_sync_job_payload(latest_job) if latest_job else None,
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
                    "latest_job": _safe_sync_job_payload(latest_job) if latest_job else None,
                }
            )
        return {"sources": statuses}

    @mcp.tool()
    async def search_context(
        query: str,
        filters: dict = None,
        top_k: int = 10,
        include_debug: bool = False,
    ) -> dict:
        """Citation 가능한 structured context 검색"""
        if context_search_service is None:
            return {"query": _redact_public_query_text(query), "results": []}
        public_filters, has_no_public_source = _public_filters(
            filters,
            metadata_store,
            allowed_source_ids,
        )
        if has_no_public_source:
            rewrite_enabled = bool(
                getattr(context_search_service, "query_rewriter", None)
            )
            return {
                "query": _redact_public_query_text(query),
                "results": [],
                "debug": {
                    "retrieval_queries": [],
                    "rewritten_queries": [],
                    "effective_term_groups": [],
                    "rewrite_enabled": rewrite_enabled,
                    "rewrite_attempted": False,
                    "rewrite_applied": False,
                    "rewrite_skipped_reason": "no_matching_sources",
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
        """문서 단위로 그룹화된 structured context 검색"""
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
        """문서 또는 chunk context 원문 조회"""
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

    @mcp.tool()
    async def answer_with_citations(
        question: str,
        filters: dict = None,
        top_k: int = 5,
        include_debug: bool = False,
    ) -> dict:
        """검색 근거 기반 helper answer preview와 citation/debug 데이터를 반환"""
        public_filters, has_no_public_source = _public_filters(
            filters,
            metadata_store,
            allowed_source_ids,
        )
        if has_no_public_source:
            return _insufficient_answer_for_filtered_sources(question)
        public_filters = _with_default_public_source_filter(
            public_filters,
            allowed_source_ids,
        )
        if answer_service is None:
            return {
                "question": _redact_public_query_text(question),
                "answer": "Citation answer service is not configured.",
                "evidence_status": "insufficient",
                "citations": [],
                "used_chunks": [],
            }
        return await answer_service.answer_with_citations(
            question,
            filters=public_filters,
            top_k=top_k,
            include_debug=include_debug,
        )


def _source_registry_ids(source_registry) -> frozenset[str] | None:
    if source_registry is None:
        return None
    return frozenset(
        str(source.source_id)
        for source in source_registry.list_sources()
        if getattr(source, "source_id", "")
    )


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


def _safe_sync_job_payload(job) -> dict:
    payload = _model_payload(job)
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
    allowed_keys = {
        "document_id",
        "chunk_id",
        "source_id",
        "source_type",
        "title",
        "url",
        "path",
        "score",
        "preview",
    }
    if hasattr(item, "model_dump"):
        return item.model_dump(
            mode="json",
            include=allowed_keys,
        )
    if isinstance(item, dict):
        return {
            key: value
            for key, value in item.items()
            if key in allowed_keys
        }
    return item
