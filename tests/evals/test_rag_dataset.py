from __future__ import annotations

import pytest

from evals.rag_dataset import load_cases, load_documents, load_manifest

pytestmark = pytest.mark.unit


def test_rag_v1_manifest_declares_version_and_split_policy():
    manifest = load_manifest()

    assert manifest["dataset_version"] == "rag_v1"
    assert set(manifest["splits"]) == {"train", "dev", "test"}
    assert "test labels must not be used for retrieval tuning" in manifest["policy"].lower()


def test_rag_v1_documents_cover_required_synthetic_types():
    documents = load_documents()
    doc_types = {str(item.get("doc_type")) for item in documents}
    languages = {str(item.get("language")) for item in documents}

    assert "readme" in doc_types
    assert "adr" in doc_types
    assert "runbook" in doc_types
    assert "hard-negative" in doc_types
    assert "inactive" in doc_types or "stale" in doc_types
    assert any(item.get("active") is False for item in documents)
    assert "mixed" in languages or "ko-en" in languages


def test_rag_v1_cases_have_required_labels_and_disjoint_splits():
    train = load_cases("train")
    dev = load_cases("dev")
    test = load_cases("test")

    assert train and dev and test

    required_fields = {
        "case_id",
        "split",
        "group",
        "query",
        "relevant_document_ids",
        "relevant_chunk_ids",
        "forbidden_chunk_ids",
        "forbidden_inactive_chunk_ids",
        "hard_negative_chunk_ids",
        "expected_source_id",
        "no_answer",
        "top_k",
    }
    for case in [*train, *dev, *test]:
        assert required_fields.issubset(case.keys())
        assert case["split"] in {"train", "dev", "test"}
        assert isinstance(case["no_answer"], bool)
        assert int(case["top_k"]) >= 1
        if case["no_answer"]:
            assert case["relevant_chunk_ids"] == []
            assert case["relevant_document_ids"] == []
        if case["group"] == "hard-negative":
            assert case["hard_negative_chunk_ids"]
        if case["group"] in {"stale-block", "inactive"}:
            assert case["forbidden_inactive_chunk_ids"]

    train_ids = {case["case_id"] for case in train}
    dev_ids = {case["case_id"] for case in dev}
    test_ids = {case["case_id"] for case in test}
    assert train_ids.isdisjoint(dev_ids)
    assert train_ids.isdisjoint(test_ids)
    assert dev_ids.isdisjoint(test_ids)


def test_rag_v1_test_split_covers_stale_hardneg_and_no_answer():
    test = load_cases("test")
    groups = {case["group"] for case in test}
    assert any(case["no_answer"] for case in test)
    assert "stale-block" in groups or "inactive" in groups
    assert "hard-negative" in groups
    for case in test:
        if case["group"] == "no-answer":
            assert case["query"].startswith("ZZZ_NOANSWER_TEST_")
            assert case["no_answer"] is True
        if case["group"] == "stale-block":
            assert case["forbidden_inactive_chunk_ids"]
            assert case["no_answer"] is False

    train_no = {c["query"] for c in load_cases("train") if c["group"] == "no-answer"}
    dev_no = {c["query"] for c in load_cases("dev") if c["group"] == "no-answer"}
    test_no = {c["query"] for c in load_cases("test") if c["group"] == "no-answer"}
    assert train_no.isdisjoint(dev_no)
    assert train_no.isdisjoint(test_no)
    assert dev_no.isdisjoint(test_no)
