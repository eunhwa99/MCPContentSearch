from evals.contextwiki_eval import run_contextwiki_eval
from evals.document_sort_quality import (
    DocumentSortQualityCase,
    evaluate_document_sort_payload,
    load_cases,
)


EXPECTED_DESC_DOCUMENT_ORDER = [
    "notion:document-sort-new",
    "notion:document-sort-tie-a",
    "notion:document-sort-tie-b",
    "notion:document-sort-null",
]
EXPECTED_ASC_DOCUMENT_ORDER = [
    "notion:document-sort-tie-a",
    "notion:document-sort-tie-b",
    "notion:document-sort-new",
    "notion:document-sort-null",
]


def test_document_sort_evaluator_uses_document_ids_without_chunk_conflation():
    case = DocumentSortQualityCase(
        case_id="sort",
        query="document ordering sentinel",
        sort_by="published_at",
        sort_order="desc",
        expected_document_ids=tuple(EXPECTED_DESC_DOCUMENT_ORDER),
        top_k=4,
    )
    payload = {
        "results": [
            {"document_id": document_id, "chunk_id": f"unrelated-{index}"}
            for index, document_id in enumerate(EXPECTED_DESC_DOCUMENT_ORDER)
        ]
    }

    result = evaluate_document_sort_payload(payload, case)

    assert result.passed
    assert result.details["document_ids"] == EXPECTED_DESC_DOCUMENT_ORDER
    assert "chunk_ids" not in result.details


def test_document_sort_evaluator_rejects_wrong_direction_with_null_first():
    case = DocumentSortQualityCase(
        case_id="sort-negative",
        query="document ordering sentinel",
        sort_by="published_at",
        sort_order="desc",
        expected_document_ids=tuple(EXPECTED_DESC_DOCUMENT_ORDER),
        top_k=4,
    )
    payload = {
        "results": [
            {"document_id": "notion:document-sort-null"},
            {"document_id": "notion:document-sort-tie-a"},
            {"document_id": "notion:document-sort-tie-b"},
            {"document_id": "notion:document-sort-new"},
        ]
    }

    result = evaluate_document_sort_payload(payload, case)

    assert not result.passed
    assert "document_order" in result.failures


def test_document_sort_fixture_case_covers_timestamp_tie_and_null_last():
    cases = load_cases("evals/document_sort_quality_cases.json")

    assert len(cases) == 2
    assert list(cases[0].expected_document_ids) == EXPECTED_DESC_DOCUMENT_ORDER
    assert list(cases[1].expected_document_ids) == EXPECTED_ASC_DOCUMENT_ORDER


def test_contextwiki_eval_runner_reports_document_sort_suite():
    summary = run_contextwiki_eval()

    assert summary["passed"]
    suite = summary["document_sort_suite"]
    assert suite["passed"]
    assert suite["total"] == 2
    assert suite["passed_count"] == 2
    assert suite["average_score"] == 1.0
    results = {result["case_id"]: result for result in suite["results"]}
    assert results["published-desc-tie-and-null-last"]["details"][
        "document_ids"
    ] == EXPECTED_DESC_DOCUMENT_ORDER
    assert results["published-asc-tie-and-null-last"]["details"][
        "document_ids"
    ] == EXPECTED_ASC_DOCUMENT_ORDER


def test_rag_report_includes_document_sort_suite_failures(tmp_path):
    from evals.contextwiki_eval import _write_artifacts

    summary = {
        "passed": False,
        "retrieval_suite": {
            "passed": True,
            "results": [],
            "quality_metrics": {},
            "group_breakdown": {},
        },
        "answer_suite": {
            "passed": True,
            "results": [],
            "quality_metrics": {},
            "group_breakdown": {},
        },
        "document_sort_suite": {
            "passed": False,
            "results": [
                {
                    "case_id": "published-desc-only-failing",
                    "passed": False,
                    "failures": ["document_order"],
                }
            ],
        },
        "runtime_metrics": {
            "retrieval_suite": {"latency_ms": {"average": 1.0, "p95": 1.0}},
        },
    }

    _write_artifacts(tmp_path, summary)
    report = (tmp_path / "rag_report.md").read_text(encoding="utf-8")
    assert "published-desc-only-failing" in report
    assert "document_order" in report
    assert "- None" not in report.split("## Failures", 1)[1].split("##", 1)[0]
