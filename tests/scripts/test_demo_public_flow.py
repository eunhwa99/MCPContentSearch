from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
import sys

from llama_index.core import Settings

from scripts.demo_public_flow import main, parse_args, render_demo_text, run_demo


def test_run_demo_returns_grounded_local_flow():
    result = asyncio.run(
        run_demo(
            question="How does ContextWiki prevent stale citations?",
            query="How does ContextWiki prevent stale citations?",
        )
    )

    assert result["sync"]["status"] == "running"
    assert result["status"]["source"]["source_id"] == "source_obsidian"
    assert result["status"]["latest_job"]["status"] == "succeeded"
    assert result["search"]["results"]
    assert result["answer"]["evidence_status"] == "grounded"
    assert result["answer"]["citations"]


def test_run_demo_returns_insufficient_for_unrelated_question():
    result = asyncio.run(
        run_demo(
            query="How does ContextWiki prevent stale citations?",
            question="What is the deployment region for production?",
        )
    )

    assert result["sync"]["status"] == "running"
    assert result["status"]["latest_job"]["status"] == "succeeded"
    assert result["search"]["results"]
    assert result["answer"]["evidence_status"] == "insufficient"
    assert (
        result["answer"]["answer"]
        == "Insufficient evidence in indexed context to answer this question."
    )
    assert result["answer"]["citations"] == []


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
            "sync": {"status": "running"},
            "status": {
                "source": {"source_id": "source_obsidian"},
                "latest_job": {"status": "succeeded"},
            },
            "search": {"results": [{"chunk_id": "chunk-1"}]},
            "answer": {"evidence_status": "grounded", "citations": [{"chunk_id": "chunk-1"}]},
        },
        query="How does ContextWiki prevent stale citations?",
        question="How does ContextWiki prevent stale citations?",
    )

    assert "ContextWiki Local Demo" in text
    assert "1. Sync retained source" in text
    assert "Retrieval and helper preview use the same input." in text
    assert "3. Search query: How does ContextWiki prevent stale citations?" in text
    assert (
        "4. Helper answer preview question: How does ContextWiki prevent stale citations?"
        in text
    )
    assert "bundled vault through the local Obsidian connector" in text
    assert "Checks: local Obsidian sync, status, search, and citation wiring." in text
    assert (
        "Does not validate: remote Notion/Tistory/GitHub connectors, "
        "user-configured sources, real MCP-client transport, or production "
        "semantic embedding quality."
        in text
    )
    assert "Downstream LLMs usually turn this evidence into the final answer." in text


def test_render_demo_text_warns_when_search_and_answer_inputs_diverge():
    text = render_demo_text(
        {
            "sample_vault": "sample_vault",
            "sync": {"status": "running"},
            "status": {
                "source": {"source_id": "source_obsidian"},
                "latest_job": {"status": "succeeded"},
            },
            "search": {"results": [{"chunk_id": "chunk-1"}]},
            "answer": {"evidence_status": "grounded", "citations": [{"chunk_id": "chunk-1"}]},
        },
        query="stale citations",
        question="How does ContextWiki prevent stale citations?",
    )

    assert "Retrieval and helper preview use different inputs; treat them as separate probes." in text


def test_demo_script_json_mode_runs_successfully():
    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{existing_pythonpath}:{repo_root}" if existing_pythonpath else str(repo_root)
    )
    completed = subprocess.run(
        ["./scripts/demo.sh", "--json"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(completed.stdout)
    assert payload["query"] == "How does ContextWiki prevent stale citations?"
    assert payload["question"] == "How does ContextWiki prevent stale citations?"
    assert payload["same_input"] is True
    assert payload["sync"]["status"] == "running"
    assert payload["sync"]["job_id"] == "<generated>"
    assert payload["status"]["source"]["last_synced_at"] == "<generated>"
    assert payload["status"]["source"]["latest_success_at"] == "<generated>"
    assert payload["status"]["latest_job"]["status"] == "succeeded"
    assert payload["search"]["results"][0]["updated_at"] == "<generated>"
    assert payload["answer"]["evidence_status"] == "grounded"
    assert completed.stdout.lstrip().startswith("{")


def test_demo_script_default_text_mode_shows_local_workflow_scope():
    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{existing_pythonpath}:{repo_root}" if existing_pythonpath else str(repo_root)
    )
    completed = subprocess.run(
        ["./scripts/demo.sh"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert "ContextWiki Local Demo" in completed.stdout
    assert "Local workflow smoke" in completed.stdout
    assert "local Obsidian connector" in completed.stdout
    assert "Retrieval and helper preview use the same input." in completed.stdout
    assert "Does not validate: remote Notion/Tistory/GitHub connectors" in (
        completed.stdout
    )
    assert "user-configured sources" in completed.stdout
    assert "public demo" not in completed.stdout.lower()
    assert "reviewer workflow" not in completed.stdout.lower()
    assert "3. Search query: How does ContextWiki prevent stale citations?" in completed.stdout


def test_demo_script_text_mode_marks_separate_probes_when_inputs_differ():
    repo_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{existing_pythonpath}:{repo_root}" if existing_pythonpath else str(repo_root)
    )
    completed = subprocess.run(
        [
            "./scripts/demo.sh",
            "--query",
            "sqlite active evidence gate",
            "--question",
            "Why does ContextWiki validate citations through SQLite?",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert "Retrieval and helper preview use different inputs" in completed.stdout
    assert "3. Search query: sqlite active evidence gate" in completed.stdout
    assert "4. Helper answer preview question: Why does ContextWiki validate citations through SQLite?" in completed.stdout


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
    assert payload["sync"]["status"] == "running"
    assert payload["status"]["latest_job"]["status"] == "succeeded"
    assert payload["answer"]["evidence_status"] == "grounded"
    assert completed.stdout.lstrip().startswith("{")


def test_parse_args_defaults_to_same_canonical_question(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["demo_public_flow.py"])

    args = parse_args()

    assert args.query == "How does ContextWiki prevent stale citations?"
    assert args.question is None


def test_main_reuses_query_when_demo_question_is_omitted(monkeypatch, capsys):
    captured: dict[str, str] = {}

    async def stub_run_demo(query: str, question: str) -> dict:
        captured["query"] = query
        captured["question"] = question
        return {
            "sample_vault": "sample_vault",
            "sync": {"status": "succeeded"},
            "status": {"source": {"source_id": "source_obsidian"}},
            "search": {"results": []},
            "answer": {"evidence_status": "grounded", "citations": []},
        }

    monkeypatch.setattr(sys, "argv", ["demo_public_flow.py", "--query", "Why does ContextWiki validate citations through SQLite?"])
    monkeypatch.setattr("scripts.demo_public_flow.run_demo", stub_run_demo)

    main()

    assert captured["query"] == "Why does ContextWiki validate citations through SQLite?"
    assert captured["question"] == "Why does ContextWiki validate citations through SQLite?"
    assert "Retrieval and helper preview use the same input." in capsys.readouterr().out


def test_main_json_mode_marks_separate_probes_when_inputs_differ(monkeypatch, capsys):
    async def stub_run_demo(query: str, question: str) -> dict:
        return {
            "sample_vault": "sample_vault",
            "query": query,
            "question": question,
            "same_input": query == question,
            "sync": {"status": "succeeded"},
            "status": {"source": {"source_id": "source_obsidian"}},
            "search": {"results": []},
            "answer": {"evidence_status": "grounded", "citations": []},
        }

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "demo_public_flow.py",
            "--query",
            "stale citations",
            "--question",
            "How does ContextWiki prevent stale citations?",
            "--json",
        ],
    )
    monkeypatch.setattr("scripts.demo_public_flow.run_demo", stub_run_demo)

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["query"] == "stale citations"
    assert payload["question"] == "How does ContextWiki prevent stale citations?"
    assert payload["same_input"] is False


def test_main_text_mode_marks_separate_probes_when_inputs_differ(monkeypatch, capsys):
    async def stub_run_demo(query: str, question: str) -> dict:
        return {
            "sample_vault": "sample_vault",
            "query": query,
            "question": question,
            "same_input": query == question,
            "sync": {"status": "succeeded"},
            "status": {"source": {"source_id": "source_obsidian"}},
            "search": {"results": []},
            "answer": {"evidence_status": "grounded", "citations": []},
        }

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "demo_public_flow.py",
            "--query",
            "stale citations",
            "--question",
            "How does ContextWiki prevent stale citations?",
        ],
    )
    monkeypatch.setattr("scripts.demo_public_flow.run_demo", stub_run_demo)

    main()

    output = capsys.readouterr().out
    assert "Retrieval and helper preview use different inputs" in output
    assert "3. Search query: stale citations" in output
    assert "4. Helper answer preview question: How does ContextWiki prevent stale citations?" in output


def test_demo_help_mentions_question_defaults_to_query():
    repo_root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        [sys.executable, "scripts/demo_public_flow.py", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "safe local ContextWiki workflow smoke" in result.stdout
    assert "checks the local obsidian connector" in result.stdout.lower()
    assert "does not validate remote" in result.stdout
    assert "Notion/Tistory/GitHub connectors" in result.stdout
    assert "user-configured sources" in result.stdout
    assert "Defaults to the same text as --query." in result.stdout


def test_readme_keeps_demo_and_live_smoke_contract_intent():
    repo_root = Path(__file__).resolve().parents[2]
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    normalized_readme = " ".join(readme.split())

    assert "./scripts/demo.sh" in readme
    assert "bundled Obsidian sample vault" in normalized_readme
    assert "needs no credentials" in normalized_readme
    assert "temporary SQLite and Chroma storage" in normalized_readme
    assert "mock embeddings" in normalized_readme
