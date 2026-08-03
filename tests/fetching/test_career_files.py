from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import threading
import time

import pytest

from core.exceptions import CareerManifestParsingError, ParsingError
from core.models import EvidenceSourceType, ExperienceType, SourceType
from environments.config import AppConfig
from fetching import career_files as career_files_module
from fetching import connectors as connector_module
from fetching.career_files import load_career_manifest
from fetching.connectors import CareerSourceConnector, build_source_registry


pytestmark = pytest.mark.integration


def _manifest(tmp_path, documents):
    root = tmp_path / "career"
    root.mkdir()
    path = tmp_path / "career-manifest.json"
    path.write_text(
        json.dumps({"root": "career", "documents": documents}),
        encoding="utf-8",
    )
    return root, path


def test_explicit_manifest_parses_only_listed_files_with_declared_taxonomy(tmp_path):
    root, manifest = _manifest(
        tmp_path,
        [
            {
                "path": "resume.md",
                "source_type": "resume",
                "experience_type": "professional",
                "company": "Example Corp",
            }
        ],
    )
    (root / "resume.md").write_text(
        "# Experience\nBuilt a reliable queue.\n",
        encoding="utf-8",
    )
    (root / "unlisted.txt").write_text("Must not be indexed.", encoding="utf-8")

    documents = load_career_manifest(manifest, max_file_bytes=100_000)

    assert len(documents) == 1
    assert documents[0].file_name == "resume.md"
    assert documents[0].evidence_source_type == EvidenceSourceType.RESUME
    assert documents[0].experience_type == ExperienceType.PROFESSIONAL
    assert documents[0].company == "Example Corp"


@pytest.mark.parametrize("invalid_position", [1, 2, 3])
def test_manifest_parse_failure_carries_bounded_progress_for_failure_position(
    tmp_path,
    invalid_position,
):
    root, manifest = _manifest(
        tmp_path,
        [
            {
                "path": f"entry-{position}.txt",
                "source_type": "career_note",
            }
            for position in range(1, 4)
        ],
    )
    for position in range(1, 4):
        entry = root / f"entry-{position}.txt"
        if position == invalid_position:
            entry.write_bytes(b"\xffprivate-invalid-content")
        else:
            entry.write_text(f"synthetic entry {position}", encoding="utf-8")

    with pytest.raises(CareerManifestParsingError) as exc_info:
        load_career_manifest(manifest, max_file_bytes=100_000)

    error = exc_info.value
    assert isinstance(error, ParsingError)
    assert error.attempted_documents == invalid_position
    assert error.completed_documents == invalid_position - 1
    assert error.parsing_latency_ms > 0
    assert error.attempted_documents <= 3
    assert str(root) not in str(error)
    assert "private-invalid-content" not in str(error)


def test_manifest_rejects_symlinked_intermediate_ancestor(tmp_path):
    real_parent = tmp_path / "real-parent"
    root = real_parent / "career"
    root.mkdir(parents=True)
    (root / "resume.txt").write_text(
        "approved synthetic evidence",
        encoding="utf-8",
    )
    manifest = real_parent / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [{"path": "resume.txt", "source_type": "resume"}],
            }
        ),
        encoding="utf-8",
    )
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ParsingError) as exc_info:
        load_career_manifest(
            linked_parent / manifest.name,
            max_file_bytes=100_000,
        )

    assert "configured manifest" in str(exc_info.value)
    assert str(real_parent) not in str(exc_info.value)


def test_manifest_rejects_group_writable_manifest_file(tmp_path):
    root, manifest = _manifest(
        tmp_path,
        [{"path": "resume.txt", "source_type": "resume"}],
    )
    (root / "resume.txt").write_text(
        "approved synthetic evidence",
        encoding="utf-8",
    )
    manifest.chmod(0o660)

    with pytest.raises(ParsingError, match="existing trusted file"):
        load_career_manifest(manifest, max_file_bytes=100_000)


def test_manifest_read_rejects_intermediate_ancestor_swap_after_precheck(
    monkeypatch,
    tmp_path,
):
    approved_parent = tmp_path / "approved-parent"
    approved_root = approved_parent / "career"
    approved_root.mkdir(parents=True)
    (approved_root / "resume.txt").write_text(
        "approved synthetic evidence",
        encoding="utf-8",
    )
    manifest = approved_parent / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [{"path": "resume.txt", "source_type": "resume"}],
            }
        ),
        encoding="utf-8",
    )

    outside_parent = tmp_path / "outside-parent"
    outside_root = outside_parent / "career"
    outside_root.mkdir(parents=True)
    (outside_root / "resume.txt").write_text(
        "attacker-controlled synthetic content",
        encoding="utf-8",
    )
    (outside_parent / manifest.name).write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [{"path": "resume.txt", "source_type": "resume"}],
            }
        ),
        encoding="utf-8",
    )
    moved_parent = tmp_path / "approved-parent-before-swap"
    original_precheck = career_files_module.career_manifest_disabled_reason

    def swap_after_precheck(path):
        reason = original_precheck(path)
        approved_parent.rename(moved_parent)
        approved_parent.symlink_to(outside_parent, target_is_directory=True)
        return reason

    monkeypatch.setattr(
        career_files_module,
        "career_manifest_disabled_reason",
        swap_after_precheck,
    )

    with pytest.raises(ParsingError) as exc_info:
        load_career_manifest(manifest, max_file_bytes=100_000)

    assert manifest.name in str(exc_info.value)
    assert str(outside_parent) not in str(exc_info.value)


def test_manifest_rejects_in_place_mutation_during_descriptor_read(
    monkeypatch,
    tmp_path,
):
    root, manifest = _manifest(
        tmp_path,
        [{"path": "resume.txt", "source_type": "resume"}],
    )
    (root / "resume.txt").write_text(
        "approved synthetic evidence",
        encoding="utf-8",
    )
    original_fdopen = career_files_module.os.fdopen
    mutated = False

    class MutatingReader:
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, exc_type, exc, traceback):
            return self._handle.__exit__(exc_type, exc, traceback)

        def fileno(self):
            return self._handle.fileno()

        def read(self, *args, **kwargs):
            nonlocal mutated
            raw = self._handle.read(*args, **kwargs)
            manifest.write_text(
                json.dumps(
                    {
                        "root": "career",
                        "documents": [
                            {
                                "path": "resume.txt",
                                "source_type": "previous_resume",
                            }
                        ],
                        "mutation_padding": "changed-after-read",
                    }
                ),
                encoding="utf-8",
            )
            mutated = True
            return raw

    def mutating_fdopen(descriptor, *args, **kwargs):
        return MutatingReader(original_fdopen(descriptor, *args, **kwargs))

    monkeypatch.setattr(career_files_module.os, "fdopen", mutating_fdopen)

    with pytest.raises(
        CareerManifestParsingError,
        match=f"Could not read career manifest {manifest.name}",
    ) as exc_info:
        load_career_manifest(manifest, max_file_bytes=100_000)

    assert mutated is True
    assert "changed-after-read" not in str(exc_info.value)


def test_manifest_mutation_while_later_file_loads_fails_final_snapshot(
    monkeypatch,
    tmp_path,
):
    root, manifest = _manifest(
        tmp_path,
        [
            {"path": "first.txt", "source_type": "career_note"},
            {"path": "second.txt", "source_type": "career_note"},
        ],
    )
    (root / "first.txt").write_text("first synthetic evidence", encoding="utf-8")
    (root / "second.txt").write_text("second synthetic evidence", encoding="utf-8")
    original_read_file = career_files_module.CareerDocumentParser.read_file
    mutated = False

    def mutate_manifest_during_second_read(self, requested_path):
        nonlocal mutated
        loaded = original_read_file(self, requested_path)
        if Path(requested_path).name == "second.txt":
            manifest.write_text(
                json.dumps(
                    {
                        "root": "career",
                        "documents": [
                            {"path": "first.txt", "source_type": "career_note"},
                            {"path": "second.txt", "source_type": "career_note"},
                        ],
                        "mutation_padding": "changed-after-initial-read",
                    }
                ),
                encoding="utf-8",
            )
            mutated = True
        return loaded

    monkeypatch.setattr(
        career_files_module.CareerDocumentParser,
        "read_file",
        mutate_manifest_during_second_read,
    )

    with pytest.raises(
        CareerManifestParsingError,
        match=f"Could not read career manifest {manifest.name}",
    ) as exc_info:
        load_career_manifest(manifest, max_file_bytes=100_000)

    assert mutated is True
    assert "changed-after-initial-read" not in str(exc_info.value)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "root": "career\x00private",
            "documents": [{"path": "resume.txt", "source_type": "resume"}],
        },
        {
            "root": "career",
            "documents": [
                {"path": "resume\nprivate.txt", "source_type": "resume"}
            ],
        },
        {
            "root": "career",
            "documents": [{"path": "\tresume.txt", "source_type": "resume"}],
        },
    ],
)
def test_manifest_rejects_control_character_components_with_typed_error(
    tmp_path,
    payload,
):
    root = tmp_path / "career"
    root.mkdir()
    (root / "resume.txt").write_text("synthetic evidence", encoding="utf-8")
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CareerManifestParsingError) as exc_info:
        load_career_manifest(manifest, max_file_bytes=100_000)

    message = str(exc_info.value)
    assert "\x00" not in message
    assert "\n" not in message


def test_manifest_path_nul_is_translated_to_typed_sanitized_error(tmp_path):
    manifest = tmp_path / "career-manifest\x00private.json"

    with pytest.raises(CareerManifestParsingError) as exc_info:
        load_career_manifest(manifest, max_file_bytes=100_000)

    assert "\x00" not in str(exc_info.value)


def test_manifest_rejects_traversal_and_duplicate_document_ids(tmp_path):
    _, unsafe_manifest = _manifest(
        tmp_path,
        [{"path": "../outside.txt", "source_type": "resume"}],
    )
    with pytest.raises(ParsingError, match="unsafe path"):
        load_career_manifest(unsafe_manifest, max_file_bytes=100_000)

    duplicate_root = tmp_path / "duplicate"
    duplicate_root.mkdir()
    (duplicate_root / "a.txt").write_text("a", encoding="utf-8")
    (duplicate_root / "b.txt").write_text("b", encoding="utf-8")
    duplicate_manifest = tmp_path / "duplicate-manifest.json"
    duplicate_manifest.write_text(
        json.dumps(
            {
                "root": "duplicate",
                "documents": [
                    {
                        "path": "a.txt",
                        "source_type": "career_note",
                        "document_id": "same",
                    },
                    {
                        "path": "b.txt",
                        "source_type": "career_note",
                        "document_id": "same",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ParsingError, match="repeats a document id"):
        load_career_manifest(duplicate_manifest, max_file_bytes=100_000)


def test_manifest_rejects_hardlinked_entries_before_document_id_generation(
    monkeypatch,
    tmp_path,
):
    root, manifest = _manifest(
        tmp_path,
        [
            {
                "path": "resume.txt",
                "source_type": "resume",
                "document_id": "resume-primary",
            },
            {
                "path": "resume-hardlink.txt",
                "source_type": "previous_resume",
                "document_id": "resume-duplicate",
            },
        ],
    )
    original = root / "resume.txt"
    original.write_text("approved synthetic evidence", encoding="utf-8")
    os.link(original, root / "resume-hardlink.txt")
    parse_calls: list[object] = []

    def unexpected_parse(*args, **kwargs):
        parse_calls.append((args, kwargs))
        raise AssertionError("duplicate physical file reached document ID generation")

    monkeypatch.setattr(
        career_files_module.CareerDocumentParser,
        "parse_loaded_file",
        unexpected_parse,
    )

    with pytest.raises(ParsingError, match="entry 2 repeats a physical file"):
        load_career_manifest(manifest, max_file_bytes=100_000)

    assert parse_calls == []


def test_manifest_rejects_case_insensitive_path_aliases(tmp_path):
    root, manifest = _manifest(
        tmp_path,
        [
            {
                "path": "Resume.txt",
                "source_type": "resume",
            },
            {
                "path": "resume.txt",
                "source_type": "previous_resume",
            },
        ],
    )
    original = root / "Resume.txt"
    original.write_text("approved synthetic evidence", encoding="utf-8")
    alias = root / "resume.txt"
    if not alias.exists() or not os.path.samefile(original, alias):
        pytest.skip("requires a case-insensitive filesystem")

    with pytest.raises(ParsingError, match="entry 2 repeats a physical file"):
        load_career_manifest(manifest, max_file_bytes=100_000)


def test_manifest_parent_swap_after_read_fails_closed(
    monkeypatch,
    tmp_path,
):
    approved_parent = tmp_path / "approved-parent"
    approved_root = approved_parent / "career"
    approved_root.mkdir(parents=True)
    (approved_root / "resume.txt").write_text(
        "approved synthetic evidence",
        encoding="utf-8",
    )
    manifest = approved_parent / "career-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "root": "career",
                "documents": [{"path": "resume.txt", "source_type": "resume"}],
            }
        ),
        encoding="utf-8",
    )
    moved_parent = tmp_path / "approved-parent-before-swap"
    original_loads = career_files_module.json.loads
    swapped = False

    def swap_parent_after_manifest_read(value):
        nonlocal swapped
        payload = original_loads(value)
        approved_parent.rename(moved_parent)
        replacement_root = approved_parent / "career"
        replacement_root.mkdir(parents=True)
        (replacement_root / "resume.txt").write_text(
            "attacker-controlled synthetic content",
            encoding="utf-8",
        )
        swapped = True
        return payload

    monkeypatch.setattr(
        career_files_module.json, "loads", swap_parent_after_manifest_read
    )

    with pytest.raises(ParsingError) as exc_info:
        load_career_manifest(manifest, max_file_bytes=100_000)

    assert swapped is True
    assert str(tmp_path) not in str(exc_info.value)
    assert "attacker-controlled" not in str(exc_info.value)


def test_manifest_root_swap_before_parser_binding_fails_closed(
    monkeypatch,
    tmp_path,
):
    root, manifest = _manifest(
        tmp_path,
        [{"path": "resume.txt", "source_type": "resume"}],
    )
    (root / "resume.txt").write_text(
        "approved synthetic evidence",
        encoding="utf-8",
    )
    moved_root = tmp_path / "career-before-swap"
    original_init = career_files_module.CareerDocumentParser.__init__
    swapped = False

    def swap_root_before_parser_init(self, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            root.rename(moved_root)
            root.mkdir()
            (root / "resume.txt").write_text(
                "attacker-controlled synthetic content",
                encoding="utf-8",
            )
            swapped = True
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(
        career_files_module.CareerDocumentParser,
        "__init__",
        swap_root_before_parser_init,
    )

    with pytest.raises(ParsingError) as exc_info:
        load_career_manifest(manifest, max_file_bytes=100_000)

    assert swapped is True
    assert "attacker-controlled" not in str(exc_info.value)


def test_duplicate_document_id_reports_actual_entry_index(tmp_path):
    root, manifest = _manifest(
        tmp_path,
        [
            {"path": "a.txt", "source_type": "career_note", "document_id": "same"},
            {"path": "b.txt", "source_type": "career_note", "document_id": "same"},
            {"path": "c.txt", "source_type": "career_note", "document_id": "unique"},
        ],
    )
    for name in ("a.txt", "b.txt", "c.txt"):
        (root / name).write_text(name, encoding="utf-8")

    with pytest.raises(ParsingError, match="entry 2 repeats a document id"):
        load_career_manifest(manifest, max_file_bytes=100_000)


@pytest.mark.parametrize("document_id", ["x" * 513, "safe\nunsafe", "safe\x00unsafe"])
def test_manifest_rejects_unsafe_document_id_before_file_read(
    monkeypatch,
    tmp_path,
    document_id,
):
    root, manifest = _manifest(
        tmp_path,
        [
            {
                "path": "resume.txt",
                "source_type": "resume",
                "document_id": document_id,
            }
        ],
    )
    (root / "resume.txt").write_text("synthetic evidence", encoding="utf-8")
    read_calls: list[object] = []

    def unexpected_read(*args, **kwargs):
        read_calls.append((args, kwargs))
        raise AssertionError("unsafe document_id reached file read")

    monkeypatch.setattr(
        "fetching.career_files.CareerDocumentParser._read_bounded",
        unexpected_read,
    )

    with pytest.raises(ParsingError, match="document_id"):
        load_career_manifest(manifest, max_file_bytes=100_000)

    assert read_calls == []


def test_career_connector_is_additive_and_only_registered_when_configured(tmp_path):
    root, manifest = _manifest(
        tmp_path,
        [{"path": "skills.txt", "source_type": "skills_inventory"}],
    )
    (root / "skills.txt").write_text("Python and SQLite.", encoding="utf-8")
    base_config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=None,
    )
    configured = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "metadata.sqlite3",
        career_manifest_path=manifest,
    )

    base_registry = build_source_registry(
        config=base_config,
        notion_api_key="",
        tistory_blog_name="",
    )
    registry = build_source_registry(
        config=configured,
        notion_api_key="",
        tistory_blog_name="",
    )
    connector = registry.get_connector("source_career")
    documents = asyncio.run(connector.fetch_documents())

    assert "source_career" not in {
        source.source_id for source in base_registry.list_sources()
    }
    assert isinstance(connector, CareerSourceConnector)
    assert connector.source.source_type == SourceType.CAREER
    assert connector.source.enabled is True
    assert connector.supports_stale_cleanup is True
    assert [document.file_name for document in documents] == ["skills.txt"]


def test_manifest_rejects_document_count_before_parsing_any_file(
    monkeypatch,
    tmp_path,
):
    root, manifest = _manifest(
        tmp_path,
        [
            {"path": "first.txt", "source_type": "career_note"},
            {"path": "second.txt", "source_type": "career_note"},
        ],
    )
    (root / "first.txt").write_text("first", encoding="utf-8")
    (root / "second.txt").write_text("second", encoding="utf-8")
    parse_calls: list[object] = []

    def unexpected_parse(*args, **kwargs):
        parse_calls.append((args, kwargs))
        raise AssertionError("file parsing must not start before count validation")

    monkeypatch.setattr(
        "fetching.career_files.CareerDocumentParser.parse_file",
        unexpected_parse,
    )

    with pytest.raises(ParsingError, match="file count limit"):
        load_career_manifest(
            manifest,
            max_file_bytes=100,
            max_files=1,
            max_total_raw_bytes=1_000,
            max_total_extracted_text_bytes=1_000,
        )

    assert parse_calls == []


def test_manifest_bounds_aggregate_raw_bytes_across_individually_valid_files(
    tmp_path,
):
    root, manifest = _manifest(
        tmp_path,
        [
            {"path": "first.txt", "source_type": "career_note"},
            {"path": "second.txt", "source_type": "career_note"},
        ],
    )
    (root / "first.txt").write_text("123456", encoding="utf-8")
    (root / "second.txt").write_text("abcdef", encoding="utf-8")

    with pytest.raises(ParsingError, match="aggregate raw byte limit"):
        load_career_manifest(
            manifest,
            max_file_bytes=100,
            max_files=10,
            max_total_raw_bytes=10,
            max_total_extracted_text_bytes=1_000,
        )


def test_manifest_reads_each_file_once_for_raw_accounting_and_parsing(
    monkeypatch,
    tmp_path,
):
    root, manifest = _manifest(
        tmp_path,
        [{"path": "resume.txt", "source_type": "resume"}],
    )
    (root / "resume.txt").write_text("synthetic evidence", encoding="utf-8")
    read_count = 0
    original_read = career_files_module.CareerDocumentParser._read_bounded

    def counting_read(self, *args, **kwargs):
        nonlocal read_count
        read_count += 1
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(
        "fetching.career_files.CareerDocumentParser._read_bounded",
        counting_read,
    )

    documents = load_career_manifest(manifest, max_file_bytes=100_000)

    assert len(documents) == 1
    assert read_count == 1


def test_manifest_cancellation_stops_before_next_file(
    monkeypatch,
    tmp_path,
):
    root, manifest = _manifest(
        tmp_path,
        [
            {"path": "first.txt", "source_type": "career_note"},
            {"path": "second.txt", "source_type": "career_note"},
        ],
    )
    (root / "first.txt").write_text("first", encoding="utf-8")
    (root / "second.txt").write_text("second", encoding="utf-8")
    cancel_requested = False
    read_count = 0
    original_read = career_files_module.CareerDocumentParser._read_bounded

    def cancelling_read(self, *args, **kwargs):
        nonlocal cancel_requested, read_count
        read_count += 1
        result = original_read(self, *args, **kwargs)
        cancel_requested = True
        return result

    monkeypatch.setattr(
        "fetching.career_files.CareerDocumentParser._read_bounded",
        cancelling_read,
    )
    cancel_error = getattr(
        career_files_module,
        "CareerParsingCancelled",
        RuntimeError,
    )

    with pytest.raises(cancel_error):
        load_career_manifest(
            manifest,
            max_file_bytes=100_000,
            cancel_check=lambda: cancel_requested,
        )

    assert read_count == 1


def test_manifest_rejects_unbounded_per_file_limit_before_reading(tmp_path):
    root, manifest = _manifest(
        tmp_path,
        [{"path": "resume.txt", "source_type": "resume"}],
    )
    (root / "resume.txt").write_text("synthetic evidence", encoding="utf-8")

    with pytest.raises(ValueError, match="max_file_bytes.*maximum"):
        load_career_manifest(manifest, max_file_bytes=2**63 - 1)


def test_manifest_bounds_aggregate_extracted_text_bytes_across_documents(
    tmp_path,
):
    root, manifest = _manifest(
        tmp_path,
        [
            {"path": "first.txt", "source_type": "career_note"},
            {"path": "second.txt", "source_type": "career_note"},
        ],
    )
    (root / "first.txt").write_text("career", encoding="utf-8")
    (root / "second.txt").write_text("evidence", encoding="utf-8")

    with pytest.raises(ParsingError, match="aggregate extracted text byte limit"):
        load_career_manifest(
            manifest,
            max_file_bytes=100,
            max_files=10,
            max_total_raw_bytes=1_000,
            max_total_extracted_text_bytes=10,
        )


@pytest.mark.parametrize(
    "field",
    [
        "document_title",
        "company",
        "role",
        "project",
        "start_date",
        "end_date",
    ],
)
def test_manifest_rejects_oversized_metadata_before_parsing(
    monkeypatch,
    tmp_path,
    field,
):
    root, manifest = _manifest(
        tmp_path,
        [
            {
                "path": "resume.txt",
                "source_type": "resume",
                field: "x" * 20_000,
            }
        ],
    )
    (root / "resume.txt").write_text("synthetic evidence", encoding="utf-8")
    parse_calls: list[object] = []

    def unexpected_parse(*args, **kwargs):
        parse_calls.append((args, kwargs))
        raise AssertionError("oversized metadata reached parser")

    monkeypatch.setattr(
        "fetching.career_files.CareerDocumentParser.parse_file",
        unexpected_parse,
    )

    with pytest.raises(ParsingError, match=rf"{field}.*limit"):
        load_career_manifest(manifest, max_file_bytes=100_000)

    assert parse_calls == []


def test_connector_fails_whole_snapshot_and_disables_cleanup_on_aggregate_limit(
    tmp_path,
):
    root, manifest = _manifest(
        tmp_path,
        [
            {"path": "first.txt", "source_type": "career_note"},
            {"path": "second.txt", "source_type": "career_note"},
        ],
    )
    (root / "first.txt").write_text("123456", encoding="utf-8")
    (root / "second.txt").write_text("abcdef", encoding="utf-8")
    connector = CareerSourceConnector(
        AppConfig(
            chroma_db_path=tmp_path / "chroma",
            metadata_db_path=tmp_path / "metadata.sqlite3",
            career_manifest_path=manifest,
            career_max_file_bytes=100,
            career_max_files=10,
            career_max_total_raw_bytes=10,
            career_max_total_extracted_text_bytes=1_000,
        )
    )

    with pytest.raises(ParsingError, match="aggregate raw byte limit"):
        asyncio.run(connector.fetch_documents())

    assert connector.supports_stale_cleanup is False
    assert "did not parse completely" in connector.source.stale_cleanup_disabled_reason


def test_career_connector_cancellation_joins_parser_thread_before_return(
    monkeypatch,
    tmp_path,
):
    root, manifest = _manifest(
        tmp_path,
        [{"path": "resume.txt", "source_type": "resume"}],
    )
    (root / "resume.txt").write_text("synthetic evidence", encoding="utf-8")
    started = threading.Event()
    finished = threading.Event()

    def slow_load(*args, cancel_check=None, **kwargs):
        started.set()
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            if cancel_check is not None and cancel_check():
                break
            time.sleep(0.005)
        finished.set()
        return []

    monkeypatch.setattr(connector_module, "load_career_manifest", slow_load)
    connector = CareerSourceConnector(
        AppConfig(
            chroma_db_path=tmp_path / "chroma",
            metadata_db_path=tmp_path / "metadata.sqlite3",
            career_manifest_path=manifest,
        )
    )

    async def run_and_cancel():
        task = asyncio.create_task(connector.fetch_documents())
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(run_and_cancel())


def test_career_connector_stop_checker_joins_parser_thread_before_return(
    monkeypatch,
    tmp_path,
):
    root, manifest = _manifest(
        tmp_path,
        [{"path": "resume.txt", "source_type": "resume"}],
    )
    (root / "resume.txt").write_text("synthetic evidence", encoding="utf-8")
    finished = threading.Event()

    def slow_load(*args, cancel_check=None, **kwargs):
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            if cancel_check is not None and cancel_check():
                break
            time.sleep(0.005)
        finished.set()
        return []

    monkeypatch.setattr(connector_module, "load_career_manifest", slow_load)
    connector = CareerSourceConnector(
        AppConfig(
            chroma_db_path=tmp_path / "chroma",
            metadata_db_path=tmp_path / "metadata.sqlite3",
            career_manifest_path=manifest,
        )
    )
    connector.progress_stop_checker = lambda: True

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(connector.fetch_documents())

    assert finished.is_set()


def test_career_connector_keeps_fast_in_memory_stop_polling(
    monkeypatch,
    tmp_path,
):
    root, manifest = _manifest(
        tmp_path,
        [{"path": "resume.txt", "source_type": "resume"}],
    )
    (root / "resume.txt").write_text("synthetic evidence", encoding="utf-8")

    def slow_load(*args, cancel_check=None, **kwargs):
        del args, kwargs
        deadline = time.monotonic() + 0.24
        while time.monotonic() < deadline:
            if cancel_check is not None and cancel_check():
                break
            time.sleep(0.005)
        return []

    monkeypatch.setattr(connector_module, "load_career_manifest", slow_load)
    connector = CareerSourceConnector(
        AppConfig(
            chroma_db_path=tmp_path / "chroma",
            metadata_db_path=tmp_path / "metadata.sqlite3",
            career_manifest_path=manifest,
        )
    )
    poll_times: list[float] = []

    async def in_memory_stop_checker():
        poll_times.append(time.monotonic())
        return False

    connector.progress_stop_checker = in_memory_stop_checker

    assert asyncio.run(connector.fetch_documents()) == []
    assert len(poll_times) >= 4
    assert max(right - left for left, right in zip(poll_times, poll_times[1:])) < 0.09
