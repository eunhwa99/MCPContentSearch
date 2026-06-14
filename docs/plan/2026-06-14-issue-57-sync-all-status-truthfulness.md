## User request

- Work issue #57: fix `sync_all()` disabled-source semantics and make aggregate status/summary truthful.

## Branch preflight result

- Original worktree `/Users/eunhwa/IdeaProjects/MCPContentSearch` was dirty on `feature/readme-rewrite-cleanup`; per harness policy it was preserved untouched.
- Fetched `origin/main` on 2026-06-14 and created isolated worktree `/private/tmp/MCPContentSearch-issue57`.
- Working branch: `feature/issue-57-sync-all-status-truthfulness` from `origin/main` at `76e407e`.
- Subagent/delegation tools were discovered, but the active tool policy allows spawning only when the user explicitly asks. For this task that makes review/worker delegation unavailable without further user approval; target-code edits will proceed only if the user approves bypassing worker orchestration, or if the task is recorded as atomic enough for direct main-agent implementation.

## Scope and non-goals

### Scope

- Make `indexing/ingestion_service.py` report truthful per-source `sync_outcome`, aggregate `summary`, and aggregate top-level `status` for `sync_all()`.
- Preserve direct `sync_source(source_id)` disabled-source behavior unless tests or tool contract require a narrower tweak.
- Update focused tests around sync contracts.
- Update README wording if the `sync_all()` response contract changes materially.

### Non-goals

- No change to retained MCP tool names or parameters.
- No new persisted sync job status enum unless current code makes that unavoidable.
- No live source sync, no local user Chroma/SQLite inspection, no connector behavior changes unrelated to aggregate reporting.

## Acceptance criteria

- Disabled source during `sync_all()` is counted truthfully as `skipped`, not generic `failed`.
- Aggregate `status` is `completed` when outcomes are only `succeeded` and/or `skipped`.
- Aggregate `status` is `partial` when at least one source is `blocked` or `failed` and at least one source is `succeeded` or `skipped`.
- Aggregate `status` is `failed` when there is no success/skip outcome and at least one source is `blocked` or `failed`.
- Explicit empty selection `sync_all([])` is a no-op and must not fan out into a full bulk sync.
- `summary` counts for `succeeded`, `failed`, `blocked`, and `skipped` are accurate.
- Focused tests cover:
  - all enabled sources succeeding
  - disabled source in bulk sync
  - overlapping/running source returning blocked in bulk sync
  - mixed bulk outcomes producing truthful aggregate status
  - no-success bulk outcomes producing aggregate `failed`
  - explicit empty selection producing a zero-source no-op
- README/tool docs are updated if vocabulary or semantics visible to callers changed.

## Step breakdown

1. Add/adjust focused tests in `tests/indexing/test_ingestion_service.py` and, if needed, `tests/api/test_tools_contract.py` to define the new aggregate contract before implementation.
2. Implement the smallest production change in `indexing/ingestion_service.py` and any API formatting/doc touchpoints required by the new contract.
3. Run focused verification first, then broaden to the repo functional E2E gate.
4. If delegation/review remains unavailable under tool policy, stop before mandatory `$subagent-review-loop` and report the blocker with verification evidence.

## Files likely to change

- `indexing/ingestion_service.py`
- `tests/indexing/test_ingestion_service.py`
- `tests/api/test_tools_contract.py`
- `README.md`

## Test and verification plan

- RED/GREEN focused tests:
  - `uv run --locked pytest tests/indexing/test_ingestion_service.py -q`
  - `uv run --locked pytest tests/api/test_tools_contract.py -q`
- Broader focused contract verification:
  - `uv run --locked pytest tests/api/test_tools_contract.py tests/indexing/test_ingestion_service.py -q`
- Syntax/import fallback if uv is broken:
  - `python -m compileall api core environments fetching indexing search storage main.py`
- Required code-change functional gate before review:
  - `./scripts/verify_functional_e2e.sh`

## Functional smoke matrix

| Feature or workflow | Caller surface | Safest data mode | Expected visible result | Command | Result | Evidence | Blocker / substitute |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `sync_all()` all-succeed aggregate | pytest contract/integration | temp SQLite + fake connectors | top-level `status=completed`; `succeeded` count accurate | `uv run --locked pytest tests/api/test_tools_contract.py tests/indexing/test_ingestion_service.py -q` | passed | Focused sync/tool suite green | Focused deterministic substitute for MCP call |
| `sync_all()` disabled source aggregate | pytest contract/integration | temp SQLite + fake disabled connector | disabled source yields `sync_outcome=skipped`; summary `skipped` increments | `uv run --locked pytest tests/api/test_tools_contract.py tests/indexing/test_ingestion_service.py -q` | passed | `test_sync_all_counts_disabled_source_as_skipped` passed | Focused deterministic substitute for MCP call |
| `sync_all()` running-source aggregate | pytest contract/integration | temp SQLite + fake blocking connector | running source yields `blocked`; mixed aggregate becomes `partial` | `uv run --locked pytest tests/api/test_tools_contract.py tests/indexing/test_ingestion_service.py -q` | passed | `test_sync_all_reports_blocked_source_when_job_already_running` passed | Focused deterministic substitute for MCP call |
| `sync_all()` no-success aggregate | pytest contract/integration | temp SQLite + fake failing/blocking connectors | top-level `status=failed` when results are only `failed` or `blocked` | `uv run --locked pytest tests/api/test_tools_contract.py tests/indexing/test_ingestion_service.py -q` | passed | `test_sync_all_reports_failed_when_nothing_completed_successfully`; `test_sync_all_reports_failed_when_all_selected_sources_are_blocked` | Focused deterministic substitute for MCP call |
| `sync_all([])` explicit empty selection | pytest contract/integration | temp SQLite + fake connectors | zero-source no-op with no indexing side effects | `uv run --locked pytest tests/api/test_tools_contract.py tests/indexing/test_ingestion_service.py -q` | passed | `test_sync_all_empty_selection_is_a_no_op` passed | Focused deterministic substitute for MCP call |
| MCP `sync_all` tool payload passthrough | pytest tool contract | fake ingestion service | tool preserves caller-visible `completed`, `partial`, and `failed` aggregate statuses safely | `uv run --locked pytest tests/api/test_tools_contract.py tests/indexing/test_ingestion_service.py -q` | passed | `test_sync_all_passthrough_preserves_completed_and_skipped_outcomes`; `test_sync_all_passthrough_preserves_partial_status`; `test_sync_all_passthrough_preserves_failed_status` | Tool-layer deterministic substitute |
| Retained end-to-end sync/search contract regression | repo functional E2E script | temp/fake retained-source harness | full retained functional suite remains green | `./scripts/verify_functional_e2e.sh` | passed | Retained functional suite green | Required pre-review gate |
| `list_sources` / `get_sync_status` core status surfaces | unchanged neighboring retained features | existing deterministic functional suite | no regression in retained source status surfaces | `./scripts/verify_functional_e2e.sh` | passed | retained functional suite green | Covered by repo functional gate |

## Architecture / ADR constraints

- Keep MCP contract formatting in `api/tools.py`; aggregate behavior belongs in `indexing/ingestion_service.py` per ADR 0001.
- Preserve SQLite/Chroma safety boundaries from ADR 0002; tests must use temp storage only.
- Stay within retained slim MCP sync surface from ADR 0006; no new tools or legacy sync behaviors.

## Risks and rollback notes

- Risk: changing aggregate vocabulary could break callers that expect `status=completed` always. Mitigation: keep per-source result shape stable and document the new status semantics in README.
- Risk: treating disabled as skipped only in bulk sync could create intentional asymmetry with direct `sync_source()`. Mitigation: encode that policy explicitly in tests and docs.
- Rollback point: revert the issue-57 branch/worktree only; no user data mutation is planned.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Preserved dirty original worktree, fetched `origin/main`, created isolated worktree and branch. | `git status --short --branch`; `git fetch origin main`; `git worktree add -b feature/issue-57-sync-all-status-truthfulness /private/tmp/MCPContentSearch-issue57 origin/main` |
| Plan | completed | Wrote issue-57 plan with contract policy, tests, smoke matrix, and delegation blocker note. | `docs/plan/2026-06-14-issue-57-sync-all-status-truthfulness.md` |
| Worker orchestration | completed | Recorded that subagent tools exist but are not authorized by current tool policy without explicit user delegation. This task is treated as atomic because the behavior change is centered on one aggregate sync policy path in `indexing/ingestion_service.py`, with only focused contract tests and small doc wording follow-up. | `tool_search` result; branch preflight note |
| Focused tests (RED) | completed | Added failing sync-all regressions for disabled-source skip, blocked mixed aggregate, failed mixed aggregate, and tool payload passthrough; verified they failed before implementation. | `uv run --locked pytest tests/indexing/test_ingestion_service.py -q -k 'sync_all_counts_disabled_source_as_skipped or sync_all_reports_blocked_source_when_job_already_running or sync_all_reports_partial_when_success_and_failure_are_mixed'` -> 3 failed; `uv run --locked pytest tests/api/test_tools_contract.py -q -k 'sync_all_passthrough_preserves_partial_and_skipped_outcomes'` added before GREEN |
| Implementation | completed | Added bulk outcome/status helpers so disabled sources count as `skipped` only in bulk sync and aggregate status becomes `completed`/`partial`/`failed` truthfully; updated README wording. | `indexing/ingestion_service.py`; `README.md` |
| Focused verification (GREEN) | completed | Reran focused sync contract suite and compile check successfully. | `uv run --locked pytest tests/api/test_tools_contract.py tests/indexing/test_ingestion_service.py -q` -> 67 passed; `python -m compileall api core environments fetching indexing search storage main.py` |
| Functional smoke | completed | Retained functional E2E gate stayed green after the sync contract change. | `./scripts/verify_functional_e2e.sh` -> 347 passed |
| Review pass 1 | completed | First valid five-reviewer pass found actionable gaps: explicit empty `source_ids=[]` still synced everything, tool contract test expected the wrong succeed+skip top-level status, and the new `failed` aggregate branch was not covered by regression tests. | Reviewer ids `019ec638-2d5b-7431-9df0-2d7f34d59e42`, `019ec638-2f5f-7b70-8c86-7351edf59a08`, `019ec638-31b3-7b10-97d2-844aee8a4131`, `019ec638-33e0-7eb0-bfdc-fd5438da6d81`, `019ec638-3694-7691-8027-1036e068a624` |
| Review remediation 1 | completed | Distinguished `None` from explicit empty source selection, fixed the succeed+skip MCP passthrough expectation to `completed`, and added regressions for empty-selection no-op plus no-success aggregate `failed`. | `indexing/ingestion_service.py`; `tests/indexing/test_ingestion_service.py`; `tests/api/test_tools_contract.py` |
| Post-review verification 1 | completed | Reran new regressions, full targeted sync/tool suite, and functional E2E after review fixes. | `uv run --locked pytest tests/indexing/test_ingestion_service.py -q -k 'sync_all_empty_selection_is_a_no_op or sync_all_reports_failed_when_nothing_completed_successfully'` -> 2 passed; `uv run --locked pytest tests/api/test_tools_contract.py -q -k 'sync_all_passthrough_preserves_completed_and_skipped_outcomes'` -> 1 passed; `uv run --locked pytest tests/api/test_tools_contract.py tests/indexing/test_ingestion_service.py -q` -> 69 passed; `./scripts/verify_functional_e2e.sh` -> 349 passed |
| Review pass 2 | completed | Fresh five-reviewer pass found remaining coverage/documentation gaps: stale plan evidence after remediation, acceptance criteria drift around explicit empty selection, missing all-blocked aggregate `failed` regression, and missing MCP tool-level passthrough coverage for `partial`/`failed`. | Reviewer ids `019ec63c-bbf3-7073-b391-f5f207d718ed`, `019ec63c-be24-7251-a395-03f3c7c8d7b6`, `019ec63c-c015-7823-a64a-e7d9b69f24ea`, `019ec63c-c2a6-7a32-8bb1-2b291dd6d624`, `019ec63c-c50a-7aa3-9ef0-c15aa889ef93` |
| Review remediation 2 | completed | Updated the plan to reflect explicit empty-selection behavior and fresh evidence, added all-blocked aggregate `failed` regression coverage, and added MCP tool passthrough tests for `partial` and `failed` statuses. | `docs/plan/2026-06-14-issue-57-sync-all-status-truthfulness.md`; `tests/indexing/test_ingestion_service.py`; `tests/api/test_tools_contract.py` |
| Post-review verification 2 | completed | Reran new regressions, full targeted sync/tool suite, and functional E2E after second review fixes. | `uv run --locked pytest tests/indexing/test_ingestion_service.py -q -k 'sync_all_reports_failed_when_all_selected_sources_are_blocked or sync_all_reports_failed_when_nothing_completed_successfully or sync_all_empty_selection_is_a_no_op'` -> 3 passed; `uv run --locked pytest tests/api/test_tools_contract.py -q -k 'sync_all_passthrough_preserves_completed_and_skipped_outcomes or sync_all_passthrough_preserves_partial_status or sync_all_passthrough_preserves_failed_status'` -> 3 passed; `uv run --locked pytest tests/api/test_tools_contract.py tests/indexing/test_ingestion_service.py -q` -> 72 passed; `./scripts/verify_functional_e2e.sh` -> 352 passed |
| Review pass 3 | completed | Final fresh five-reviewer pass reported no actionable findings. | Reviewer ids `019ec640-ea13-78a1-8332-7bfa5b87bae4`, `019ec640-ec21-7f22-9dfe-cc3c5cc6b04c`, `019ec640-eea3-7293-bc21-0ff9a65a99ad`, `019ec640-f0e9-7ad0-82f4-9e5ecc157378`, `019ec640-f417-7e23-84d9-6fc002cc89ef` |
| Review gate | completed | Final fresh five-reviewer pass is clean; ready for commit, push, and PR delivery. | `72 passed`; `352 passed`; clean reviewer pass |
