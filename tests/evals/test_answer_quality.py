from pathlib import Path

import pytest

import pytest

from evals.answer_quality import (
    AnswerQualityCase,
    evaluate_answer_payload,
    evaluate_answer_suite,
    load_cases,
)


pytestmark = pytest.mark.unit


def test_grounded_answer_payload_passes_required_grounding_checks():
    case = AnswerQualityCase(
        case_id="grounded",
        question="What is ContextWiki?",
        expected_answer_terms=("ContextWiki", "MCP", "citations"),
        forbidden_answer_terms=("deployment region",),
        required_citation_chunk_ids=("chunk-1",),
    )
    payload = {
        "question": "What is ContextWiki?",
        "answer": "ContextWiki is an MCP knowledge backend with citations.",
        "evidence_status": "grounded",
        "citations": [{"chunk_id": "chunk-1", "title": "Overview"}],
        "used_chunks": ["chunk-1"],
    }

    result = evaluate_answer_payload(payload, case)

    assert result.passed
    assert result.score == 1.0


def test_missing_required_citation_fails_even_when_answer_text_matches():
    case = AnswerQualityCase(
        case_id="missing-citation",
        question="What is ContextWiki?",
        expected_answer_terms=("ContextWiki",),
        required_citation_chunk_ids=("chunk-required",),
    )
    payload = {
        "answer": "ContextWiki answers from indexed evidence.",
        "evidence_status": "grounded",
        "citations": [{"chunk_id": "chunk-other"}],
        "used_chunks": ["chunk-other"],
    }

    result = evaluate_answer_payload(payload, case)

    assert not result.passed
    assert "required_citations_present" in result.failures


def test_malformed_citation_without_chunk_id_fails_minimum_citation_count():
    case = AnswerQualityCase(
        case_id="malformed-citation",
        question="What is ContextWiki?",
        expected_answer_terms=("ContextWiki",),
        min_citation_count=1,
    )
    payload = {
        "answer": "ContextWiki answers from indexed evidence.",
        "evidence_status": "grounded",
        "citations": [{"title": "Missing chunk id"}],
        "used_chunks": [],
    }

    result = evaluate_answer_payload(payload, case)

    assert not result.passed
    assert "min_citation_count" in result.failures


def test_forbidden_unsupported_claim_fails_answer_quality():
    case = AnswerQualityCase(
        case_id="forbidden-claim",
        question="What is the deployment region?",
        expected_answer_terms=("ContextWiki",),
        forbidden_answer_terms=("us-east-1",),
        required_citation_chunk_ids=("chunk-1",),
    )
    payload = {
        "answer": "ContextWiki runs in us-east-1.",
        "evidence_status": "grounded",
        "citations": [{"chunk_id": "chunk-1"}],
        "used_chunks": ["chunk-1"],
    }

    result = evaluate_answer_payload(payload, case)

    assert not result.passed
    assert "forbidden_terms_absent" in result.failures


def test_insufficient_case_accepts_empty_citations_when_expected():
    case = AnswerQualityCase(
        case_id="insufficient",
        question="What is the deployment region?",
        expected_status="insufficient",
        expected_answer_terms=("Insufficient evidence",),
        min_citation_count=0,
    )
    payload = {
        "answer": "Insufficient evidence in indexed context to answer this question.",
        "evidence_status": "insufficient",
        "citations": [],
        "used_chunks": [],
    }

    result = evaluate_answer_payload(payload, case)

    assert result.passed


def test_secret_like_output_fails_local_eval():
    case = AnswerQualityCase(
        case_id="secret-leak",
        question="What is ContextWiki?",
        expected_answer_terms=("ContextWiki",),
        required_citation_chunk_ids=("chunk-1",),
    )
    payload = {
        "answer": "ContextWiki evidence. api_key=abc123456789",
        "evidence_status": "grounded",
        "citations": [{"chunk_id": "chunk-1"}],
        "used_chunks": ["chunk-1"],
    }

    result = evaluate_answer_payload(payload, case)

    assert not result.passed
    assert "no_secret_like_output" in result.failures


def test_fixture_cases_load_and_suite_summarizes_results():
    cases = load_cases(Path("evals/contextwiki_answer_quality_cases.json"))
    payloads = {
        "github-sync-docs-answer": {
            "answer": "GitHub sync guide explains how ContextWiki answers with citations.",
            "evidence_status": "grounded",
            "citations": [{"chunk_id": "github-sync-doc-chunk"}],
            "used_chunks": ["github-sync-doc-chunk"],
        },
        "neetcode-problems-answer": {
            "answer": "NeetCode problem list captures canonical problem categories.",
            "evidence_status": "grounded",
            "citations": [{"chunk_id": "neetcode-problems-chunk"}],
            "used_chunks": ["neetcode-problems-chunk"],
        },
        "unknown-deployment-region-answer": {
            "answer": "Insufficient evidence in indexed context to answer this question.",
            "evidence_status": "insufficient",
            "citations": [],
            "used_chunks": [],
        },
        "aws-collection-answer": {
            "answer": "## Grounded List\n\n- Indexed evidence matched this collection request for `AWS 관련 문서 모아줘`.",
            "evidence_status": "grounded",
            "citations": [{"chunk_id": "aws-guide-chunk"}],
            "used_chunks": ["aws-guide-chunk"],
        },
        "dynamodb-cassandra-comparison-answer": {
            "answer": "## Grounded Comparison\n\n- DynamoDB notes\n- Cassandra notes",
            "evidence_status": "grounded",
            "citations": [
                {"chunk_id": "dynamodb-notes-chunk"},
                {"chunk_id": "cassandra-notes-chunk"},
            ],
            "used_chunks": ["dynamodb-notes-chunk", "cassandra-notes-chunk"],
        },
        "obsidian-daily-planning-answer": {
            "answer": "Daily planning note captured follow-ups and retrospective bullets.",
            "evidence_status": "grounded",
            "citations": [{"chunk_id": "obsidian-daily-planning-chunk"}],
            "used_chunks": ["obsidian-daily-planning-chunk"],
        },
        "mixed-language-daily-note-answer": {
            "answer": "## Grounded List\n\n- Indexed evidence matched this collection request for `AWS docs 모아줘`.",
            "evidence_status": "grounded",
            "citations": [{"chunk_id": "aws-guide-chunk"}],
            "used_chunks": ["aws-guide-chunk"],
        },
        "adr-markdown-answer": {
            "answer": "ADR 0006 describes the slim MCP core scope in markdown.",
            "evidence_status": "grounded",
            "citations": [{"chunk_id": "adr-markdown-chunk"}],
            "used_chunks": ["adr-markdown-chunk"],
        },
        "graph-search-code-answer": {
            "answer": "The bfs helper in graph_search.py initializes the queue with the start node.",
            "evidence_status": "grounded",
            "citations": [{"chunk_id": "graph-search-code-chunk"}],
            "used_chunks": ["graph-search-code-chunk"],
        },
    }

    summary = evaluate_answer_suite(payloads, cases)

    assert summary["passed"]
    assert summary["total"] == 9
    assert summary["passed_count"] == 9


def test_answer_suite_fails_when_case_list_is_empty():
    summary = evaluate_answer_suite({}, [])

    assert not summary["passed"]
    assert summary["total"] == 0


def test_eval_runner_cli_exits_nonzero_when_summary_fails(monkeypatch, capsys):
    from scripts import run_contextwiki_eval as runner

    monkeypatch.setattr(
        runner,
        "run_contextwiki_eval",
        lambda **kwargs: {"passed": False, "retrieval_suite": {}, "answer_suite": {}},
    )
    monkeypatch.setattr("sys.argv", ["run_contextwiki_eval.py"])

    with pytest.raises(SystemExit) as exc_info:
        runner.main()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert '"passed": false' in captured.out.lower()


def test_mixed_language_case_fails_for_english_only_answer():
    case = AnswerQualityCase(
        case_id="mixed-language-check",
        question="AWS docs 모아줘",
        expected_status="grounded",
        expected_answer_terms=("AWS docs 모아줘",),
        required_citation_chunk_ids=("aws-guide-chunk",),
        min_citation_count=1,
    )
    payload = {
        "answer": "## Grounded List\n\n- Indexed evidence matched this AWS docs request.",
        "evidence_status": "grounded",
        "citations": [{"chunk_id": "aws-guide-chunk"}],
        "used_chunks": ["aws-guide-chunk"],
    }

    result = evaluate_answer_payload(payload, case)

    assert not result.passed
    assert "expected_terms_present" in result.failures


def test_answer_suite_scorable_case_count_uses_citation_labels():
    cases = [
        AnswerQualityCase(
            case_id="with-citation",
            question="citation?",
            required_citation_chunk_ids=("chunk-1",),
            expected_status="grounded",
        ),
        AnswerQualityCase(
            case_id="insufficient",
            question="missing?",
            expected_status="insufficient",
            min_citation_count=0,
        ),
    ]
    payloads = {
        "with-citation": {
            "answer": "ok",
            "evidence_status": "grounded",
            "citations": [{"chunk_id": "chunk-1"}],
            "used_chunks": ["chunk-1"],
        },
        "insufficient": {
            "answer": "",
            "evidence_status": "insufficient",
            "citations": [],
            "used_chunks": [],
        },
    }
    suite = evaluate_answer_suite(payloads, cases)
    metrics = suite["quality_metrics"]
    assert metrics["scorable_case_count"] == 1
    assert metrics["citation_recall"]["denominator"] == 1
    assert metrics["citation_recall"]["value"] == 1.0
    assert metrics["insufficient_status_accuracy"]["denominator"] == 1
