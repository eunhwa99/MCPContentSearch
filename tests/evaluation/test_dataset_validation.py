import json
from pathlib import Path

import pytest

from evaluation.runner import DatasetValidationError, load_dataset


pytestmark = pytest.mark.unit

DATASET_PATH = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "datasets"
    / "retrieval_gold.example.jsonl"
)


def _first_case() -> dict:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8").splitlines()[0])


def _write_cases(path: Path, cases: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(case) + "\n" for case in cases),
        encoding="utf-8",
    )


def test_load_dataset_returns_validated_cases_and_preserves_label_provenance():
    cases = load_dataset(DATASET_PATH)

    assert len(cases) == 13
    assert cases[0].query_id == "q-001"
    assert cases[0].label_source == "deterministic_fixture"
    assert cases[0].graded_relevance["resume-professional-reliability"] == 3


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("query_category", "made_up", "query_category"),
        ("label_source", "humanish", "label_source"),
        ("allowed_source_types", ["job_description"], "source_type"),
        ("allowed_experience_types", ["production"], "experience_type"),
    ],
)
def test_load_dataset_rejects_unsupported_taxonomy_values(
    tmp_path, field, value, message
):
    case = _first_case()
    case[field] = value
    path = tmp_path / "invalid.jsonl"
    _write_cases(path, [case])

    with pytest.raises(DatasetValidationError, match=message):
        load_dataset(path)


def test_load_dataset_rejects_duplicate_query_ids(tmp_path):
    case = _first_case()
    path = tmp_path / "duplicate.jsonl"
    _write_cases(path, [case, case])

    with pytest.raises(DatasetValidationError, match="duplicate query_id"):
        load_dataset(path)


def test_load_dataset_rejects_incoherent_no_answer_case(tmp_path):
    case = _first_case()
    case["query_category"] = "no_answer"
    case["should_return_empty"] = True
    path = tmp_path / "invalid-empty.jsonl"
    _write_cases(path, [case])

    with pytest.raises(DatasetValidationError, match="should_return_empty"):
        load_dataset(path)


def test_load_dataset_never_upgrades_ai_generated_labels(tmp_path):
    case = _first_case()
    case["label_source"] = "ai_generated_unreviewed"
    path = tmp_path / "ai-candidate.jsonl"
    _write_cases(path, [case])

    loaded = load_dataset(path)

    assert loaded[0].label_source == "ai_generated_unreviewed"
