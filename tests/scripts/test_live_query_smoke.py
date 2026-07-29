import asyncio
import json
from pathlib import Path
import subprocess
import sys

from scripts.live_query_smoke import (
    format_smoke_summary,
    main,
    parse_args,
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


def test_format_smoke_summary_includes_hits_and_citations():
    summary = format_smoke_summary(
        query="aws startup",
        question="aws startup",
        same_input=True,
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
            "debug": {},
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
    assert "answer question: aws startup" in summary
    assert "hit 1: source_github | EC2 setup guide | chunk-1 | score=0.910" in summary
    assert "helper answer preview: grounded" in summary
    assert "citation 1: EC2 setup guide | chunk-1" in summary
    assert "inspect helper output: citations, used_chunks, debug, debug_markdown" in summary
    assert "tip: use --json to inspect used_chunks, debug, and debug_markdown safely" in summary
    assert "canonical" not in summary.lower()
    assert (
        "same-input smoke path: retrieval and helper answer preview use the same input text above."
        in summary
    )


def test_format_smoke_summary_uses_safe_placeholders_for_empty_optional_sections():
    summary = format_smoke_summary(
        query="plain query",
        question="plain question",
        same_input=False,
        source_id=None,
        top_k=5,
        search_payload={
            "results": [],
            "debug": {},
        },
        answer_payload={
            "evidence_status": "insufficient",
            "citations": [],
        },
    )

    assert "source filter: -" in summary
    assert "hits: 0" in summary
    assert "citations: 0" in summary
    assert "helper answer preview: insufficient" in summary


def test_format_smoke_summary_warns_when_search_and_answer_are_separate_probes():
    summary = format_smoke_summary(
        query="aws startup",
        question="How do I start EC2?",
        same_input=False,
        source_id="source_github",
        top_k=3,
        search_payload={
            "results": [],
            "debug": {},
        },
        answer_payload={
            "evidence_status": "insufficient",
            "citations": [],
        },
    )

    assert (
        "separate probes: retrieval summary describes the search query above, while helper answer status and citations describe the answer question."
        in summary
    )


def test_format_smoke_summary_uses_raw_input_equality_not_redacted_text():
    summary = format_smoke_summary(
        query="token alpha-secret",
        question="token beta-secret",
        same_input=False,
        source_id=None,
        top_k=5,
        search_payload={"results": [], "debug": {}},
        answer_payload={"evidence_status": "insufficient", "citations": []},
    )

    assert "same-input smoke path" not in summary
    assert "separate probes:" in summary


def test_format_smoke_summary_redacts_secret_like_query_text():
    summary = format_smoke_summary(
        query="token super-secret-value docs",
        question="show /Users/eunhwa/private docs",
        same_input=False,
        source_id=None,
        top_k=5,
        search_payload={"results": [], "debug": {}},
        answer_payload={"evidence_status": "insufficient", "citations": []},
    )

    assert "super-secret-value" not in summary
    assert "/Users/eunhwa/private" not in summary
    assert "[REDACTED]" in summary


def test_parse_args_leaves_question_empty_until_main_derives_it(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["live_query_smoke.py", "--query", "github sync"])

    args = parse_args()

    assert args.query == "github sync"
    assert args.question is None


def test_main_reuses_query_when_question_is_omitted(monkeypatch, capsys):
    captured: dict[str, object] = {}

    async def stub_run_live_query_smoke(*, query, question, source_id, top_k):
        captured["query"] = query
        captured["question"] = question
        captured["source_id"] = source_id
        captured["top_k"] = top_k
        return {
            "query": query,
            "question": question,
            "source_id": source_id,
            "top_k": top_k,
            "search": {"results": [], "debug": {}},
            "answer": {"evidence_status": "grounded", "citations": []},
        }

    monkeypatch.setattr(sys, "argv", ["live_query_smoke.py", "--query", "github sync"])
    monkeypatch.setattr("scripts.live_query_smoke.run_live_query_smoke", stub_run_live_query_smoke)

    main()

    assert captured["query"] == "github sync"
    assert captured["question"] == "github sync"
    assert "same-input smoke path" in capsys.readouterr().out


def test_main_marks_separate_probes_when_question_differs(monkeypatch, capsys):
    captured: dict[str, object] = {}

    async def stub_run_live_query_smoke(*, query, question, source_id, top_k):
        captured["query"] = query
        captured["question"] = question
        return {
            "query": query,
            "question": question,
            "source_id": source_id,
            "top_k": top_k,
            "search": {"results": [], "debug": {}},
            "answer": {"evidence_status": "grounded", "citations": []},
        }

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "live_query_smoke.py",
            "--query",
            "aws startup",
            "--question",
            "How do I start EC2?",
        ],
    )
    monkeypatch.setattr("scripts.live_query_smoke.run_live_query_smoke", stub_run_live_query_smoke)

    main()

    assert captured["query"] == "aws startup"
    assert captured["question"] == "How do I start EC2?"
    assert "separate probes:" in capsys.readouterr().out


def test_main_json_reuses_query_when_question_is_omitted(monkeypatch, capsys):
    async def stub_run_live_query_smoke(*, query, question, source_id, top_k):
        return {
            "query": query,
            "question": question,
            "source_id": source_id,
            "top_k": top_k,
            "search": {"results": [], "debug": {}},
            "answer": {"evidence_status": "grounded", "citations": []},
        }

    monkeypatch.setattr(
        sys,
        "argv",
        ["live_query_smoke.py", "--query", "github sync", "--json"],
    )
    monkeypatch.setattr("scripts.live_query_smoke.run_live_query_smoke", stub_run_live_query_smoke)

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["query"] == "github sync"
    assert payload["question"] == "github sync"
    assert payload["same_input"] is True


def test_main_json_marks_separate_probes_when_question_differs(monkeypatch, capsys):
    async def stub_run_live_query_smoke(*, query, question, source_id, top_k):
        return {
            "query": query,
            "question": question,
            "source_id": source_id,
            "top_k": top_k,
            "search": {"results": [], "debug": {}},
            "answer": {"evidence_status": "grounded", "citations": []},
        }

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "live_query_smoke.py",
            "--query",
            "token alpha-secret",
            "--question",
            "token beta-secret",
            "--json",
        ],
    )
    monkeypatch.setattr("scripts.live_query_smoke.run_live_query_smoke", stub_run_live_query_smoke)

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["query"] == "token [REDACTED]"
    assert payload["question"] == "token [REDACTED]"
    assert payload["same_input"] is False


def test_live_query_smoke_requests_search_and_answer_debug_payloads(monkeypatch):
    captured: dict[str, object] = {}

    class StubMCP:
        def __init__(self):
            self.tools = {
                "search_context": self.search_context,
            }
            self.answer_service = self

        async def search_context(self, query, *, filters=None, top_k=10, include_debug=False):
            captured["query"] = query
            captured["filters"] = filters
            captured["search_top_k"] = top_k
            captured["search_include_debug"] = include_debug
            return {"results": [], "debug": {"retrieval_queries": [query]}}

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

    monkeypatch.setattr("scripts.live_query_smoke.build_runtime_mcp", lambda: StubMCP())

    result = asyncio.run(
        run_live_query_smoke(
            query="obsidian citation",
            question="How do citations work?",
            source_id="source_obsidian",
            top_k=4,
        )
    )

    assert result["search"]["debug"]["retrieval_queries"] == ["obsidian citation"]
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
                    "retrieval_queries": ["aws startup"],
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
    assert payload["same_input"] is False
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


def test_redact_live_query_result_keeps_same_input_marker_for_redaction_collisions():
    payload = redact_live_query_result(
        {
            "query": "token alpha-secret",
            "question": "token beta-secret",
            "source_id": None,
            "top_k": 5,
            "search": {"results": [], "debug": {}},
            "answer": {"evidence_status": "insufficient", "citations": []},
        }
    )

    assert payload["query"] == "token [REDACTED]"
    assert payload["question"] == "token [REDACTED]"
    assert payload["same_input"] is False
