from pathlib import Path

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
    cases = load_cases(Path("evals/answer_quality_cases.json"))
    payloads = {
        "contextwiki-grounded-answer": {
            "answer": "ContextWiki is an MCP backend that answers with citations.",
            "evidence_status": "grounded",
            "citations": [{"chunk_id": "chunk-contextwiki-overview"}],
            "used_chunks": ["chunk-contextwiki-overview"],
        },
        "unknown-deployment-region": {
            "answer": "Insufficient evidence in indexed context to answer this question.",
            "evidence_status": "insufficient",
            "citations": [],
            "used_chunks": [],
        },
    }

    summary = evaluate_answer_suite(payloads, cases)

    assert summary["passed"]
    assert summary["total"] == 2
    assert summary["passed_count"] == 2
