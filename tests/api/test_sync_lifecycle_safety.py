import pytest

from api.tools import _safe_auth_ref, _safe_sync_job_payload


pytestmark = pytest.mark.unit


class Dumpable:
    def __init__(self, value):
        self.value = value

    def model_dump(self, mode="json"):
        return dict(self.value)


@pytest.mark.parametrize(
    ("auth_ref", "expected"),
    [
        ("env:NOTION_API_KEY", "env:NOTION_API_KEY"),
        ("env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH", "env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH"),
        ("ntn_abcdefghijklmnopqrstuvwxyz0123456789", "<redacted>"),
        ("secret_abcdefghijklmnopqrstuvwxyz0123456789", "<redacted>"),
        ("env:lowercase_secret", "<redacted>"),
    ],
)
def test_safe_auth_ref_only_exposes_canonical_environment_references(
    auth_ref,
    expected,
):
    assert _safe_auth_ref(auth_ref) == expected


def test_safe_sync_job_payload_drops_noncanonical_phase():
    raw_phase = (
        "fetching /Users/tester/private vault/notes.md "
        "with ntn_abcdefghijklmnopqrstuvwxyz0123456789"
    )
    payload = _safe_sync_job_payload(
        Dumpable(
            {
                "job_id": "job-safe-phase",
                "source_id": "source_notion",
                "status": "running",
                "phase": raw_phase,
                "status_message": "Still running.",
            }
        ),
        include_progress_hints=True,
    )

    assert "phase" not in payload
    assert raw_phase not in str(payload)
    assert payload["job_id"] == "job-safe-phase"
    assert payload["source_id"] == "source_notion"


def test_safe_sync_job_payload_keeps_canonical_running_phase():
    payload = _safe_sync_job_payload(
        Dumpable(
            {
                "job_id": "job-safe-phase",
                "source_id": "source_notion",
                "status": "running",
                "phase": "fetching_page_content",
                "status_message": "Still running.",
            }
        ),
        include_progress_hints=True,
    )

    assert payload["phase"] == "fetching_page_content"
