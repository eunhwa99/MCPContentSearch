from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

from scripts.demo_public_flow import render_demo_text, run_demo


def test_run_demo_returns_grounded_public_flow():
    result = asyncio.run(
        run_demo(
            query="stale citations",
            question="How does ContextWiki prevent stale citations?",
        )
    )

    assert result["sync"]["status"] == "succeeded"
    assert result["status"]["source"]["source_id"] == "source_obsidian"
    assert result["search"]["results"]
    assert result["answer"]["evidence_status"] == "grounded"
    assert result["answer"]["citations"]


def test_render_demo_text_includes_sync_search_and_answer_sections():
    text = render_demo_text(
        {
            "sample_vault": "sample_vault",
            "sync": {"status": "succeeded"},
            "status": {"source": {"source_id": "source_obsidian"}},
            "search": {"results": [{"chunk_id": "chunk-1"}]},
            "answer": {"evidence_status": "grounded", "citations": [{"chunk_id": "chunk-1"}]},
        },
        query="stale citations",
        question="How does ContextWiki prevent stale citations?",
    )

    assert "1. Sync retained source" in text
    assert "3. Search query: stale citations" in text
    assert "4. Grounded question: How does ContextWiki prevent stale citations?" in text


def test_demo_script_json_mode_runs_successfully():
    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{existing_pythonpath}:{repo_root}" if existing_pythonpath else str(repo_root)
    )
    env["CONTEXTWIKI_SEARCH_LLM_ENABLED"] = "true"
    completed = subprocess.run(
        ["./scripts/demo.sh", "--json"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(completed.stdout)
    assert payload["sync"]["status"] == "succeeded"
    assert payload["sync"]["job_id"] == "<generated>"
    assert payload["status"]["source"]["last_synced_at"] == "<generated>"
    assert payload["search"]["results"][0]["updated_at"] == "<generated>"
    assert payload["answer"]["evidence_status"] == "grounded"
