# Index Manager Bool Metadata Guard

## User Request

Investigate and fix the Notion sync failure logged as `Indexing failed: 'bool' object is not subscriptable`, clarifying that the failed sync job is terminal and making the indexing path robust enough that malformed Chroma metadata payloads do not crash source sync.

## Branch Preflight Result

- Starting worktree: `/Users/eunhwa/IdeaProjects/MCPContentSearch`
- Starting state: clean `main`, `git status --short` empty.
- Remote freshness: `git fetch origin main`, `git rev-list --left-right --count HEAD...origin/main` returned `0 0`, `git pull --ff-only origin main` already up to date.
- Task branch: `feature/index-manager-bool-metadata-guard`.
- Safe branch cleanup: skipped; no local branch deletion required.

## Scope and Non-Goals

Scope:

- Guard indexing metadata loading against non-dict Chroma metadata entries and non-dict Chroma `get()` payloads that can produce `'bool' object is not subscriptable`.
- Preserve existing managed/raw vector identity behavior for valid metadata.
- Add focused unit, integration, and deterministic E2E coverage before production code.

Non-goals:

- Do not inspect or mutate local user Chroma or SQLite data.
- Do not run live Notion, Tistory, GitHub, provider, or credential-dependent syncs.
- Do not change MCP response contracts or sync terminal-state semantics.
- Do not change architecture documentation unless the maintained design assumption changes.

## Acceptance Criteria

- `IndexManager` ignores malformed/non-dict metadata entries from Chroma instead of raising.
- A sync using an indexer backed by malformed existing Chroma metadata can still index a valid fetched document and mark the job `succeeded` in temporary test storage.
- Existing source-scoped and managed/raw cleanup behavior remains unchanged.
- Focused unit, integration, and E2E tests pass, then `./scripts/verify_all.sh` passes.
- Functional smoke matrix covers source sync status and indexing/search-adjacent retained surfaces using fake/temp data only.

## Step Breakdown

1. Add RED tests:
   - Unit: `tests/indexing/test_index_manager.py` for malformed Chroma metadata entries.
   - Integration: `tests/indexing/test_ingestion_service.py` or existing indexing integration surface for sync success despite malformed existing vector metadata.
   - Deterministic E2E: retained fake/temp sync flow under `tests/e2e` covering exact source sync terminal success.
2. Run the smallest new/changed test to confirm the expected pre-production failure.
3. Implement the smallest guard in `indexing/manager.py` without changing public contracts.
4. Run focused GREEN unit, integration, and E2E commands.
5. Refactor only if tests stay green.
6. Rerun affected focused tests, then `./scripts/verify_all.sh`.
7. Record matching eval as `n/a` unless retrieval/ranking/answer quality changes.
8. Run functional smoke using retained local fake/temp surfaces.
9. Run exactly three read-only harness reviewers and repeat if actionable findings appear.
10. Stage relevant files, commit, push, and create a `main`-base PR unless blocked.

## Files Likely To Change

- `indexing/manager.py`
- `tests/indexing/test_index_manager.py`
- Possibly `tests/indexing/test_ingestion_service.py`
- Possibly `tests/e2e/test_context_zip_flow.py` or another retained fake/temp E2E file
- This plan document

## Worker Orchestration

Main-agent direct implementation is allowed because the production change is expected to be truly atomic and localized to one indexing metadata loader, with no shared-file overlap or independent implementation slice. Reviewer subagents remain required after verification.

## TDD RED Evidence

- Command: `uv run pytest -q tests/indexing/test_index_manager.py::test_index_manager_treats_non_mapping_chroma_get_payload_as_empty tests/indexing/test_index_manager.py::test_index_manager_ignores_non_mapping_metadata_entries`
- Test layers/names: unit; `test_index_manager_treats_non_mapping_chroma_get_payload_as_empty`, `test_index_manager_ignores_non_mapping_metadata_entries`
- Non-zero exit code: `1`
- Expected failure signature: `TypeError: 'bool' object is not subscriptable` for non-mapping Chroma `get()` payload; `AttributeError: 'bool' object has no attribute 'get'` for non-mapping metadata entries.
- Missing-behavior explanation: `IndexManager._load_existing()` assumes Chroma returns a dict with mapping metadata entries, so malformed bool payloads abort indexing and bubble up as failed sync jobs.
- Predates production edits: yes; only tests and plan were edited before this RED run.

## TDD GREEN Evidence

- Focused unit/integration/E2E command: `uv run pytest -q tests/indexing/test_index_manager.py::test_index_manager_treats_non_mapping_chroma_get_payload_as_empty tests/indexing/test_index_manager.py::test_index_manager_ignores_non_mapping_metadata_entries tests/indexing/test_ingestion_service.py::test_ingestion_sync_succeeds_when_existing_chroma_metadata_payload_is_bool tests/e2e/test_context_zip_flow.py::test_context_zip_fake_e2e_sync_survives_bool_existing_chroma_metadata`
- Result: `4 passed in 1.12s`
- Broader affected indexing command: `uv run pytest -q tests/indexing/test_index_manager.py tests/indexing/test_ingestion_service.py`
- Result: `87 passed in 2.04s`
- Affected E2E rerun: `uv run pytest -q tests/e2e/test_context_zip_flow.py::test_context_zip_fake_e2e_sync_survives_bool_existing_chroma_metadata`
- Result: `1 passed in 1.16s`
- Syntax check: `python -m compileall api core environments fetching indexing search storage main.py`
- Result: passed.

## Full-Suite Evidence

Command: `./scripts/verify_all.sh`

Result: passed.

- Static verification layer: passed.
- Public MCP contract layer: `40 passed in 1.25s`.
- Broad non-live regression layer: `1375 passed in 104.65s`.
- Coverage: required `70.0%`, actual `87.91%`.
- Deterministic quality eval layer: passed; retrieval `14/14`, document sort `2/2`, answer `9/9`, all average score `1.0`.
- Deterministic functional E2E layer: `59 passed in 3.77s`.

## Matching Eval Gate

`n/a` for scope requirement. This fix does not change retrieval quality, ranking, grounding, citation selection, or answer quality; it hardens indexing metadata parsing. The full-suite deterministic quality eval layer still passed as part of `./scripts/verify_all.sh`.

## Improvement Performance Delta

`n/a`. This is a correctness/reliability bug fix with no measurable performance-improvement claim.

## Functional Smoke Matrix

| Feature or workflow | Caller surface | Data mode | Expected result | Command | Result | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Malformed Chroma metadata guard | Focused unit test | Fake collection | Non-dict metadata ignored; valid metadata still used | `uv run pytest -q tests/indexing/test_index_manager.py::test_index_manager_treats_non_mapping_chroma_get_payload_as_empty tests/indexing/test_index_manager.py::test_index_manager_ignores_non_mapping_metadata_entries` | passed | `4 passed in 1.12s` as part of focused command |
| Source sync indexing path | Focused integration/E2E test | Temporary SQLite + fake connector/indexer/collection | Job succeeds instead of failing from bool metadata | `uv run pytest -q tests/indexing/test_ingestion_service.py::test_ingestion_sync_succeeds_when_existing_chroma_metadata_payload_is_bool tests/e2e/test_context_zip_flow.py::test_context_zip_fake_e2e_sync_survives_bool_existing_chroma_metadata` | passed | `4 passed in 1.12s` as part of focused command; E2E rerun `1 passed in 1.16s` |
| Retained functional E2E suite | Repo script | Temporary/fake fixtures | Existing MCP/source-sync/search contracts still pass | `./scripts/verify_all.sh` | passed | Deterministic functional E2E layer `59 passed in 3.77s` |
| Live Notion configured sync | MCP live source | User data/live credentials | Blocked unless explicitly approved | Not run | blocked/gated | Live source/user-data approval not requested; fake/temp substitute above |

## Architecture Constraints

- Chroma remains a vector candidate store; SQLite remains authoritative for sync lifecycle and active/citable documents.
- Failed or partial syncs must not tombstone absent documents.
- Chroma metadata parsing must not expose secrets or inspect real local vector data.
- Public MCP sync status contracts remain unchanged.

## Risks and Rollback Notes

- Risk: swallowing too much malformed metadata could cause duplicate vector writes. Mitigation: ignore only entries that are not dict-like metadata; preserve valid metadata matching.
- Risk: Chroma `get()` shape variations. Mitigation: normalize defensively and log debug-level skips without failing sync.
- Rollback: revert the localized guard and associated tests.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Clean `main`, fetched `origin/main`, created feature branch. | `git status --short`; `git rev-list --left-right --count HEAD...origin/main` -> `0 0`; `git switch -c feature/index-manager-bool-metadata-guard` |
| Plan | completed | Non-exempt indexing behavior fix plan created. | `docs/plan/2026-08-06-index-manager-bool-metadata-guard.md` |
| Improvement delta declare | completed | Non-improvement reliability bug fix; no performance claim. | `n/a` |
| TDD RED | completed | Added unit coverage and confirmed existing bool metadata path fails before production edits. | `uv run pytest -q tests/indexing/test_index_manager.py::test_index_manager_treats_non_mapping_chroma_get_payload_as_empty tests/indexing/test_index_manager.py::test_index_manager_ignores_non_mapping_metadata_entries` -> exit `1`; expected `TypeError: 'bool' object is not subscriptable` |
| Focused unit GREEN | completed | Malformed metadata unit regression passes. | Focused command -> `4 passed in 1.12s`; affected indexing files -> `87 passed in 2.04s` |
| Focused integration GREEN | completed | Ingestion service succeeds with bool Chroma metadata payload. | Focused command -> `4 passed in 1.12s`; affected indexing files -> `87 passed in 2.04s` |
| Focused E2E GREEN | completed | MCP fake sync/status path succeeds with bool Chroma metadata payload. | `uv run pytest -q tests/e2e/test_context_zip_flow.py::test_context_zip_fake_e2e_sync_survives_bool_existing_chroma_metadata` -> `1 passed in 1.16s` |
| Full suite GREEN | completed | Full verification wrapper passed. | `./scripts/verify_all.sh`: public MCP `40 passed`; broad regression `1375 passed`; deterministic functional E2E `59 passed` |
| Matching eval | completed | Scope requirement `n/a`; full-suite quality eval still passed. | `./scripts/verify_all.sh`: retrieval `14/14`, document sort `2/2`, answer `9/9` |
| Improvement after/delta | completed | Non-improvement correctness/reliability bug fix. | `n/a`; no performance-improvement claim |
| Functional smoke | completed | Fake/temp source sync and retained E2E surfaces passed; live Notion blocked/gated. | Focused fake MCP E2E passed; full deterministic functional E2E `59 passed in 3.77s` |
| Harness review | completed | Three fresh read-only reviewers reported no actionable findings. | Reviewer 1 correctness pass; Reviewer 2 security/data safety pass; Reviewer 3 reliability pass |
| Delivery | pending | Commit, push, PR after clean review unless blocked. | Pending |

## Three-Reviewer Evidence

| Reviewer | Result | Notes |
| --- | --- | --- |
| Reviewer 1 - bugs/correctness/contracts/tests | pass | No actionable findings. Guard treats non-mapping Chroma payloads as empty, skips non-mapping metadata, preserves valid metadata matching, and test coverage directly exercises the reported bool failure plus MCP-visible sync/status success. |
| Reviewer 2 - security/privacy/data safety | pass | No actionable findings. No live-source access, credentials, destructive cleanup, local user-data mutation, or secret exposure; debug logs only type names. |
| Reviewer 3 - performance/reliability/operability | pass | No actionable findings. Guard keeps O(n) metadata scan behavior, adds no async/concurrency/lifecycle cleanup risk, and `n/a` improvement delta is coherent for a reliability-only bug fix. |

Newest three-reviewer pass status: all three fresh read-only reviewers reported no actionable findings.
