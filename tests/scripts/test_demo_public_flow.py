from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
import sys

from llama_index.core import Settings

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


def test_run_demo_uses_temp_cache_dir_and_restores_it(monkeypatch):
    observed: dict[str, str] = {}
    import scripts.demo_public_flow as demo_public_flow

    missing = object()
    original_builder = demo_public_flow.build_demo_components

    def wrapped_builder(sample_vault, temp_root):
        observed["cache_dir"] = Settings.cache_dir
        observed["temp_root"] = str(temp_root)
        return original_builder(sample_vault, temp_root)

    previous_cache_dir = getattr(Settings, "cache_dir", missing)
    monkeypatch.setattr(demo_public_flow, "build_demo_components", wrapped_builder)

    asyncio.run(
        run_demo(
            query="stale citations",
            question="How does ContextWiki prevent stale citations?",
        )
    )

    assert observed["cache_dir"] == str(Path(observed["temp_root"]) / "llama_cache")
    if previous_cache_dir is missing:
        assert not hasattr(Settings, "cache_dir")
    else:
        assert Settings.cache_dir == previous_cache_dir


def test_run_demo_does_not_require_preinitialized_embed_model():
    missing = object()
    had_embed_model_attr = hasattr(Settings, "_embed_model")
    previous_embed_model = getattr(Settings, "_embed_model", missing)
    try:
        Settings._embed_model = None

        result = asyncio.run(
            run_demo(
                query="stale citations",
                question="How does ContextWiki prevent stale citations?",
            )
        )

        assert result["answer"]["evidence_status"] == "grounded"
        assert Settings._embed_model is None
    finally:
        if had_embed_model_attr:
            Settings._embed_model = previous_embed_model
        else:
            try:
                delattr(Settings, "_embed_model")
            except AttributeError:
                pass


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
    assert (
        "4. Helper answer preview question: How does ContextWiki prevent stale citations?"
        in text
    )
    assert "Downstream LLMs usually turn this evidence into the final answer." in text


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
    assert completed.stdout.lstrip().startswith("{")


def test_demo_public_flow_script_runs_from_repo_root_without_pythonpath():
    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [sys.executable, "scripts/demo_public_flow.py", "--json"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(completed.stdout)
    assert payload["sync"]["status"] == "succeeded"
    assert payload["answer"]["evidence_status"] == "grounded"
    assert completed.stdout.lstrip().startswith("{")
