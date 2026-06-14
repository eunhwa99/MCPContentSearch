import asyncio
from pathlib import Path
import subprocess
import sys

from scripts.live_query_smoke import (
    format_smoke_summary,
    redact_live_query_result,
    run_live_query_smoke,
)


def test_live_query_smoke_help_runs_from_repo_root_script_path():
    repo_root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        [sys.executable, "scripts/live_query_smoke.py", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Run a live local retrieval and helper-answer smoke" in result.stdout


def test_format_smoke_summary_includes_rewrite_decision_hits_and_citations():
    summary = format_smoke_summary(
        query="aws startup",
        question="How do I start EC2?",
        source_id="source_github",
        top_k=3,
        search_payload={
            "results": [
                {
                    "source_id": "source_github",
                    "title": "EC2 setup guide",
                    "chunk_id": "chunk-1",
                    "score": 0.91,
                }
            ],
            "debug": {
                "rewrite_enabled": True,
                "rewrite_attempted": True,
                "rewrite_applied": True,
                "rewrite_skipped_reason": "",
                "rewritten_queries": ["aws ec2 setup"],
            },
        },
        answer_payload={
            "evidence_status": "grounded",
            "citations": [
                {
                    "title": "EC2 setup guide",
                    "chunk_id": "chunk-1",
                }
            ],
        },
    )

    assert "search query: aws startup" in summary
    assert "answer question: How do I start EC2?" in summary
    assert "rewrite: enabled=yes attempted=yes applied=yes reason=-" in summary
    assert "rewrites: aws ec2 setup" in summary
    assert "hit 1: source_github | EC2 setup guide | chunk-1 | score=0.910" in summary
    assert "helper answer preview: grounded" in summary
    assert "citation 1: EC2 setup guide | chunk-1" in summary
    assert "inspect helper output: citations, used_chunks, debug, debug_markdown" in summary
    assert "tip: use --json to inspect used_chunks, debug, and debug_markdown safely" in summary


def test_format_smoke_summary_uses_safe_placeholders_for_empty_optional_sections():
    summary = format_smoke_summary(
        query="plain query",
        question="plain question",
        source_id=None,
        top_k=5,
        search_payload={
            "results": [],
            "debug": {
                "rewrite_enabled": False,
                "rewrite_attempted": False,
                "rewrite_applied": False,
                "rewrite_skipped_reason": "disabled",
                "rewritten_queries": [],
            },
        },
        answer_payload={
            "evidence_status": "insufficient",
            "citations": [],
        },
    )

    assert "source filter: -" in summary
    assert "rewrite: enabled=no attempted=no applied=no reason=disabled" in summary
    assert "rewrites: -" in summary
    assert "hits: 0" in summary
    assert "citations: 0" in summary
    assert "helper answer preview: insufficient" in summary


def test_format_smoke_summary_redacts_secret_like_query_text():
    summary = format_smoke_summary(
        query="token super-secret-value docs",
        question="show /Users/eunhwa/private docs",
        source_id=None,
        top_k=5,
        search_payload={"results": [], "debug": {}},
        answer_payload={"evidence_status": "insufficient", "citations": []},
    )

    assert "super-secret-value" not in summary
    assert "/Users/eunhwa/private" not in summary
    assert "[REDACTED]" in summary


def test_live_query_smoke_requests_search_and_answer_debug_payloads(monkeypatch):
    captured: dict[str, object] = {}

    class StubMCP:
        def __init__(self):
            self.tools = {
                "search_context": self.search_context,
                "answer_with_citations": self.answer_with_citations,
            }

        async def search_context(self, query, *, filters=None, top_k=10, include_debug=False):
            captured["query"] = query
            captured["filters"] = filters
            captured["search_top_k"] = top_k
            captured["search_include_debug"] = include_debug
            return {"results": [], "debug": {"rewrite_enabled": True}}

        async def answer_with_citations(
            self,
            question,
            *,
            filters=None,
            top_k=5,
            include_debug=False,
        ):
            captured["question"] = question
            captured["answer_filters"] = filters
            captured["answer_top_k"] = top_k
            captured["answer_include_debug"] = include_debug
            return {
                "evidence_status": "insufficient",
                "citations": [],
                "used_chunks": [],
                "debug": {},
                "debug_markdown": "## Debug",
            }

    monkeypatch.setattr("scripts.live_query_smoke.build_runtime_mcp", lambda rewrite_mode: StubMCP())

    result = asyncio.run(
        run_live_query_smoke(
            query="obsidian citation",
            question="How do citations work?",
            source_id="source_obsidian",
            top_k=4,
            rewrite_mode="auto",
        )
    )

    assert result["search"]["debug"]["rewrite_enabled"] is True
    assert captured["search_include_debug"] is True
    assert captured["filters"] == {"source_id": "source_obsidian"}
    assert captured["search_top_k"] == 4
    assert captured["answer_filters"] == {"source_id": "source_obsidian"}
    assert captured["answer_top_k"] == 4
    assert captured["answer_include_debug"] is True


def test_redact_live_query_result_omits_content_preview_and_path_fields():
    payload = redact_live_query_result(
        {
            "query": "aws startup",
            "question": "How do I start EC2?",
            "source_id": "source_github",
            "top_k": 3,
            "rewrite_mode": "auto",
            "search": {
                "results": [
                    {
                        "chunk_id": "chunk-1",
                        "document_id": "doc-1",
                        "source_id": "source_github",
                        "title": "EC2 setup guide",
                        "score": 0.91,
                        "preview": "compact preview",
                        "path": "docs/ec2.md",
                        "url": "https://example.com/ec2",
                        "text": "full chunk text should not leak",
                    }
                ],
                "debug": {
                    "rewrite_enabled": True,
                    "rewrite_attempted": True,
                    "rewrite_applied": True,
                    "rewrite_skipped_reason": "",
                    "rewritten_queries": ["aws ec2 setup"],
                    "selected_results": [
                        {
                            "chunk_id": "chunk-1",
                            "path": "docs/ec2.md",
                            "url": "https://example.com/ec2",
                        }
                    ],
                },
            },
            "answer": {
                "evidence_status": "grounded",
                "answer": "Use EC2.",
                "citations": [
                    {
                        "title": "EC2 setup guide",
                        "chunk_id": "chunk-1",
                        "path": "docs/ec2.md",
                        "url": "https://example.com/ec2",
                    }
                ],
                "used_chunks": [{"chunk_id": "chunk-1", "text": "used chunk raw text"}],
                "debug": {
                    "selected_chunks": [
                        {
                            "chunk_id": "chunk-1",
                            "path": "docs/ec2.md",
                            "preview": "debug preview should not leak",
                        }
                    ]
                },
                "debug_markdown": "## Debug\n- preview: debug preview should not leak\n- chunk-1",
            },
        }
    )

    assert payload["search"]["results"][0]["chunk_id"] == "chunk-1"
    assert "text" not in payload["search"]["results"][0]
    assert "preview" not in payload["search"]["results"][0]
    assert "path" not in payload["search"]["results"][0]
    assert "url" not in payload["search"]["results"][0]
    assert "title" not in payload["answer"]["citations"][0]
    assert "path" not in payload["answer"]["citations"][0]
    assert "url" not in payload["answer"]["citations"][0]
    assert payload["answer"]["citations"][0]["chunk_id"] == "chunk-1"
    assert payload["answer"]["used_chunks"] == [{"chunk_id": "chunk-1"}]
    assert "path" not in payload["answer"]["debug"]["selected_chunks"][0]
    assert "preview" not in payload["answer"]["debug"]["selected_chunks"][0]
    assert payload["answer"]["debug_markdown"] == "## Debug\n- preview: [REDACTED]\n- chunk-1"
    assert "path" not in payload["search"]["debug"]["selected_results"][0]
    assert "url" not in payload["search"]["debug"]["selected_results"][0]
