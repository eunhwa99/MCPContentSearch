from __future__ import annotations

from typing import Any
import re


SAFE_AUTH_REF_RE = re.compile(r"^env:[A-Z_][A-Z0-9_]*$")
PUBLIC_CONFIG_ERROR_MESSAGES = {
    (
        "Source source_github is disabled because no GitHub repositories are "
        "configured in CONTEXTWIKI_GITHUB_REPOSITORIES."
    ),
    (
        "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
        "is not set or is not an existing directory."
    ),
    (
        "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
        "must be an absolute path."
    ),
    (
        "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
        "must not be a symlink."
    ),
    "Obsidian vault snapshot was incomplete because one or more notes could not be read.",
    "NOTION_API_KEY is required for Notion target sync",
    "Previous running sync job was recovered after server restart; start sync again.",
}


def dump_model(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return dict(value)


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def safe_public_config_error(value: Any, *, fallback: str) -> str:
    message = normalize_text(value)
    if message in PUBLIC_CONFIG_ERROR_MESSAGES:
        return message
    return fallback


def safe_source_payload(source: Any) -> dict[str, Any]:
    payload = dump_model(source)
    if payload.get("last_error"):
        payload["last_error"] = safe_public_config_error(
            payload["last_error"],
            fallback="Source sync failed. See server logs for details.",
        )
    auth_ref = payload.get("auth_ref")
    if auth_ref and not SAFE_AUTH_REF_RE.match(str(auth_ref)):
        payload["auth_ref"] = "redacted"
    return payload


def safe_sync_job_payload(job: Any) -> dict[str, Any]:
    payload = dump_model(job)
    if payload.get("error_message"):
        payload["error_message"] = safe_public_config_error(
            payload["error_message"],
            fallback="Sync failed. See server logs for details.",
        )
    return payload
