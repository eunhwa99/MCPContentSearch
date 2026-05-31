from __future__ import annotations

from typing import Any
import ipaddress
import os
import re
from urllib.parse import urlparse

from fastapi import HTTPException


SAFE_AUTH_REF_RE = re.compile(r"^env:[A-Z_][A-Z0-9_]*$")
PROMPT_TOKEN_SECRET_RE = re.compile(
    r"(gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|"
    r"xox[baprs]-[A-Za-z0-9-]+|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
    r"(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,})",
    re.IGNORECASE,
)
PROMPT_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?P<prefix>(?:access[-_]?token|api[-_]?key|apikey|auth|authorization|"
    r"client[-_]?secret|cookie|credential|jwt|key|pass|password|passwd|"
    r"private[-_]?key|pwd|secret|session|token)\s*[:=]\s*['\"]?)"
    r"(?P<secret>[^'\"\s,;}]+)(?P<suffix>['\"]?)",
    re.IGNORECASE,
)
PROMPT_QUERY_SECRET_RE = re.compile(
    r"(?P<prefix>[?&](?:access[-_]?token|api[-_]?key|apikey|auth|authorization|"
    r"client[-_]?secret|credential|key|password|secret|session|sig|signature|"
    r"token)=)(?P<secret>[^&#\s]+)",
    re.IGNORECASE,
)
PROMPT_PEM_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|SECRET KEY|CERTIFICATE)-----.*?"
    r"-----END [A-Z0-9 ]*(?:PRIVATE KEY|SECRET KEY|CERTIFICATE)-----",
    re.IGNORECASE | re.DOTALL,
)


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def normalize_multiline(value: Any) -> str:
    lines = [line.rstrip() for line in str(value or "").splitlines()]
    return "\n".join(lines).strip()


def normalize_top_k(value: Any, *, default: int) -> int:
    try:
        top_k = int(value)
    except (TypeError, ValueError):
        top_k = default
    return max(1, min(top_k, 20))


def list_sources(metadata_store: Any) -> list[dict[str, Any]]:
    if metadata_store is None:
        return []
    return [
        safe_source_payload(source)
        for source in metadata_store.list_sources()
    ]


def source_sync_status(metadata_store: Any, source_id: str) -> dict[str, Any]:
    source = metadata_store.get_source(source_id)
    latest_job = metadata_store.get_latest_sync_job(source_id)
    return {
        "source_id": source_id,
        "source": safe_source_payload(source) if source else None,
        "latest_job": safe_sync_job_payload(latest_job) if latest_job else None,
    }


def dump_model(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return dict(value)


def sync_status_value(job: Any) -> str:
    status = getattr(job, "status", "")
    return getattr(status, "value", status) or ""


def running_sync_job(metadata_store: Any, source_id: str) -> Any:
    if metadata_store is None:
        return None
    latest_job = metadata_store.get_latest_sync_job(source_id)
    if sync_status_value(latest_job) == "running":
        return latest_job
    return None


def target_sync_already_running_payload(
    source_id: str,
    target_type: str,
    job: Any,
) -> dict[str, Any]:
    return {
        "status": "already_running",
        "source_id": source_id,
        "target_type": target_type,
        "message": "A sync is already running for this source. The requested target was not started.",
        "job": safe_sync_job_payload(job),
    }


def safe_source_payload(source: Any) -> dict[str, Any]:
    payload = dump_model(source)
    if payload.get("last_error"):
        payload["last_error"] = "Source sync failed. See server logs for details."
    auth_ref = payload.get("auth_ref")
    if auth_ref and not SAFE_AUTH_REF_RE.match(str(auth_ref)):
        payload["auth_ref"] = "redacted"
    return payload


def safe_sync_job_payload(job: Any) -> dict[str, Any]:
    payload = dump_model(job)
    if payload.get("error_message"):
        payload["error_message"] = "Sync failed. See server logs for details."
    return payload


def normalize_auto_sync_source_ids(values: Any) -> tuple[str, ...]:
    return tuple(dedupe(normalize_list(values)))


def citation_payload(item: Any) -> dict[str, Any]:
    return {
        "chunk_id": item.chunk_id,
        "title": redact_prompt_text(item.title),
        "url": safe_url_for_display(item.url) if item.url else "",
        "path": redact_prompt_text(item.path),
        "line_start": item.line_start,
        "line_end": item.line_end,
        "version_id": item.version_id,
    }


def codex_answer_payload(
    question: str,
    answer: str,
    evidence_status: str,
    citations: list[dict[str, Any]],
    used_chunks: list[str],
    *,
    codex_status: str,
) -> dict[str, Any]:
    return {
        "question": question,
        "answer": answer,
        "answer_mode": "codex_cli",
        "codex_status": codex_status,
        "evidence_status": evidence_status,
        "citations": citations,
        "used_chunks": used_chunks,
    }


def safe_github_sync_payload(payload: Any) -> dict[str, Any]:
    safe_payload = dump_model(payload)
    if safe_payload.get("target"):
        safe_payload["target"] = safe_github_target_for_display(safe_payload["target"])
    if safe_payload.get("job"):
        safe_payload["job"] = safe_sync_job_payload(safe_payload["job"])
    return safe_payload


def safe_target_sync_payload(source_type: str, payload: Any) -> dict[str, Any]:
    safe_payload = dump_model(payload)
    safe_payload["target_type"] = normalize_source_type(
        safe_payload.get("target_type") or source_type
    )
    safe_payload["source_id"] = safe_payload.get("source_id") or source_id_for_target_type(
        safe_payload["target_type"]
    )
    if safe_payload.get("target"):
        safe_payload["target"] = safe_target_for_display(
            safe_payload["target_type"],
            safe_payload["target"],
        )
    if safe_payload.get("job"):
        safe_payload["job"] = safe_sync_job_payload(safe_payload["job"])
    safe_payload["poll_url"] = f"/api/sources/{safe_payload['source_id']}/sync-status"
    return safe_payload


def safe_target_for_display(source_type: str, value: Any) -> str:
    normalized_type = normalize_source_type(source_type)
    if normalized_type == "github":
        return safe_github_target_for_display(value)
    if normalized_type == "notion":
        try:
            from fetching.notion import parse_notion_object_id

            return f"notion:{parse_notion_object_id(str(value))}"
        except Exception:
            return "redacted"
    if normalized_type == "web":
        return safe_url_for_display(value)
    return "redacted"


def safe_github_target_for_display(value: Any) -> str:
    try:
        from fetching.github import parse_repository_or_owner_target

        owner, repo, ref = parse_repository_or_owner_target(str(value))
    except Exception:
        return "redacted"
    if repo:
        return f"{owner}/{repo}@{ref}"
    return f"github.com/{owner}"


def safe_url_for_display(value: Any) -> str:
    try:
        from fetching.web_docs import _redact_url_credentials

        parsed = urlparse(str(value))
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return "redacted"
        redacted = _redact_url_credentials(str(value))
        if redacted == "<redacted>":
            return "redacted"
        return urlparse(redacted)._replace(query="", fragment="").geturl()
    except Exception:
        return "redacted"


def redact_prompt_text(value: Any) -> str:
    try:
        from wiki.synthesis import OpenAIWikiSynthesizer

        return fallback_redact_prompt_text(
            OpenAIWikiSynthesizer._redact_secret_like(value)
        )
    except Exception:
        return fallback_redact_prompt_text(value)


def fallback_redact_prompt_text(value: Any) -> str:
    text = str(value or "")
    text = PROMPT_PEM_BLOCK_RE.sub("[REDACTED]", text)
    text = PROMPT_TOKEN_SECRET_RE.sub("[REDACTED]", text)
    text = PROMPT_ASSIGNMENT_SECRET_RE.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]{match.group('suffix')}",
        text,
    )
    return PROMPT_QUERY_SECRET_RE.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]",
        text,
    )


def source_id_for_target_type(source_type: str) -> str:
    return {
        "github": "source_github",
        "notion": "source_notion",
        "web": "source_web",
    }.get(normalize_source_type(source_type), "")


def build_filters(request: Any, metadata_store: Any) -> dict[str, Any]:
    filters = dict(request.filters or {})
    source_ids = normalize_list(filters.pop("source_ids", []))
    source_ids.extend(normalize_list(filters.pop("source_id", [])))
    source_types = normalize_list(filters.pop("source_types", []))
    source_types.extend(normalize_list(filters.pop("source_type", [])))
    source_types.extend(normalize_list(request.source_types))
    matched_source_ids = source_ids_for_types(metadata_store, source_types)
    if source_types and not matched_source_ids:
        raise HTTPException(
            status_code=400,
            detail="no configured sources match selected source types",
        )
    source_ids.extend(matched_source_ids)
    source_ids.extend(normalize_list(request.source_ids))
    if source_ids:
        filters["source_ids"] = dedupe(source_ids)
    return filters


def normalize_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        values = value.replace("\n", ",").split(",")
    elif isinstance(value, list | tuple | set):
        values = value
    else:
        values = [value]
    return [normalize_text(item) for item in values if normalize_text(item)]


def source_ids_for_types(metadata_store: Any, source_types: list[str]) -> list[str]:
    requested = {normalize_source_type(value) for value in source_types}
    requested.discard("")
    if not requested or metadata_store is None:
        return []
    source_ids = []
    for source in list_sources(metadata_store):
        if normalize_source_type(source.get("source_type", "")) in requested:
            source_ids.append(source["source_id"])
    return source_ids


def normalize_source_type(value: Any) -> str:
    normalized = normalize_text(value).lower()
    if normalized in {"docs", "pdf"}:
        return "web"
    return normalized


def normalize_target_source_type(value: Any) -> str:
    return normalize_text(value).lower()


def is_loopback_client(host: str | None) -> bool:
    if host in {"testclient", "localhost"}:
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def remote_console_allowed() -> bool:
    return os.getenv("CONTEXTWIKI_WEB_CONSOLE_ALLOW_REMOTE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def safe_answer_failure_payload(question: str, exc: Exception) -> dict[str, Any]:
    if is_openai_authentication_error(exc):
        return {
            "question": question,
            "answer": (
                "Answer failed because the OpenAI API key was rejected. "
                "Restart the local server with the correct .env or OPENAI_API_KEY."
            ),
            "evidence_status": "configuration_error",
            "citations": [],
            "used_chunks": [],
        }
    return {
        "question": question,
        "answer": "Answer failed. See server logs for details.",
        "evidence_status": "error",
        "citations": [],
        "used_chunks": [],
    }


def is_openai_authentication_error(exc: Exception) -> bool:
    class_name = type(exc).__name__.lower()
    module_name = type(exc).__module__.lower()
    message = str(exc).lower()
    status_code = getattr(exc, "status_code", None)
    return (
        status_code == 401
        and ("authentication" in class_name or "api key" in message)
        and ("openai" in module_name or "openai" in message or "api key" in message)
    )


def is_local_host_header(value: str) -> bool:
    host = parse_authority_host(value)
    if not host:
        return False
    return host in {"localhost", "testserver"} or is_loopback_client(host)


def is_local_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return False
    return is_local_host_header(parsed.netloc or parsed.path)


def parse_authority_host(value: str) -> str:
    authority = (value or "").strip()
    if not authority or "@" in authority:
        return ""
    if "://" in authority:
        parsed = urlparse(authority)
        authority = parsed.netloc
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            return ""
        host = authority[1:end].strip().lower()
        remainder = authority[end + 1 :]
        if remainder and not (remainder.startswith(":") and remainder[1:].isdigit()):
            return ""
        return host
    try:
        ipaddress.ip_address(authority)
        return authority.lower()
    except ValueError:
        pass
    if ":" in authority:
        host, port = authority.rsplit(":", 1)
        if not port.isdigit():
            return ""
        authority = host
    if ":" in authority:
        return ""
    return authority.strip().lower()


def without_persisted_output_path(result: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(result)
    if cleaned.pop("output_path", None):
        cleaned["output_retention"] = "temporary file cleaned up"
    return cleaned


def dedupe(values: list[str]) -> list[str]:
    deduped = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped
