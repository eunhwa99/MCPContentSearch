from __future__ import annotations

import re


CANONICAL_AUTH_REF_PATTERN = re.compile(r"^env:[A-Z_][A-Z0-9_]*$")
SYNC_JOB_PHASES = frozenset(
    {
        "",
        "starting",
        "discovering_pages",
        "fetching_page_content",
        "indexing_documents",
        "completed",
        "failed",
    }
)


def normalize_auth_ref(value: object) -> str:
    auth_ref = str(value) if value else ""
    if CANONICAL_AUTH_REF_PATTERN.fullmatch(auth_ref):
        return auth_ref
    return ""


def normalize_sync_job_phase(value: object) -> str:
    phase = str(value) if value else ""
    if phase in SYNC_JOB_PHASES:
        return phase
    return ""
