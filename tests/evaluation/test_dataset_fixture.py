import json
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = REPO_ROOT / "evaluation" / "datasets" / "retrieval_gold.example.jsonl"
CORPUS_PATH = REPO_ROOT / "evaluation" / "datasets" / "career_corpus.example.jsonl"

EXPECTED_QUERY_CATEGORIES = {
    "exact_keyword",
    "semantic_paraphrase",
    "technology",
    "scale_or_metric",
    "professional_only",
    "personal_project_only",
    "section_specific",
    "ambiguous",
    "no_answer",
}
EXPECTED_SOURCE_TYPES = {
    "resume",
    "previous_resume",
    "project",
    "github_readme",
    "behavioral_story",
    "career_note",
    "skills_inventory",
}
EXPECTED_EXPERIENCE_TYPES = {
    "professional",
    "academic",
    "personal_project",
    "prototype",
    "unknown",
}


def _raw_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in DATASET_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _raw_corpus() -> list[dict]:
    return [
        json.loads(line)
        for line in CORPUS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_public_fixture_covers_all_required_query_and_career_taxonomies():
    cases = _raw_cases()

    assert {case["query_category"] for case in cases} == EXPECTED_QUERY_CATEGORIES
    assert {
        source_type
        for case in cases
        for source_type in case["allowed_source_types"]
    } == EXPECTED_SOURCE_TYPES
    assert {
        experience_type
        for case in cases
        for experience_type in case["allowed_experience_types"]
    } == EXPECTED_EXPERIENCE_TYPES
    assert {case["label_source"] for case in cases} == {"deterministic_fixture"}


def test_public_fixture_has_coherent_positive_and_no_answer_labels():
    cases = _raw_cases()

    assert len(cases) >= 9
    assert len({case["query_id"] for case in cases}) == len(cases)
    for case in cases:
        if case["should_return_empty"]:
            assert case["query_category"] == "no_answer"
            assert case["expected_chunk_ids"] == []
            assert case["expected_document_ids"] == []
            assert case["graded_relevance"] == {}
        else:
            assert case["expected_chunk_ids"]
            assert case["expected_document_ids"]
            assert set(case["expected_chunk_ids"]).issubset(
                case["graded_relevance"]
            )


def test_no_answer_case_uses_filters_that_match_unrelated_indexed_chunks():
    no_answer = next(case for case in _raw_cases() if case["should_return_empty"])

    matching_but_unrelated = [
        chunk
        for chunk in _raw_corpus()
        if chunk["source_type"] in no_answer["allowed_source_types"]
        and chunk["experience_type"] in no_answer["allowed_experience_types"]
    ]

    assert matching_but_unrelated
    assert all(
        "cobol" not in chunk["content"].casefold()
        and "mainframe" not in chunk["content"].casefold()
        for chunk in matching_but_unrelated
    )


def test_public_fixture_contains_only_sanitized_synthetic_identifiers():
    raw_text = DATASET_PATH.read_text(encoding="utf-8")

    assert "/Users/" not in raw_text
    assert "@" not in raw_text
    assert "eunhwa" not in raw_text.lower()
    assert "api_key" not in raw_text.lower()
    assert "access_token" not in raw_text.lower()


def test_private_dataset_and_report_paths_are_explicitly_git_ignored():
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "evaluation/datasets/retrieval_gold.local.jsonl" in gitignore
    assert "evaluation/reports/private/" in gitignore
