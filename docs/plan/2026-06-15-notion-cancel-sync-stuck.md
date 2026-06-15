# Notion Sync Cancellation Leaves Running Job Stuck

## User Request

Investigate why Notion does not sync properly and verify whether the configured
API key is failing. After confirming the key is not the problem, fix the root
cause for the stuck Notion sync. Follow-up: make long source syncs continue
even when the client times out, using the approved `sync_source` background-job
direction with explicit subagent/delegation approval.

## Branch Preflight Result

- Original worktree `/Users/eunhwa/IdeaProjects/MCPContentSearch` is dirty on
  `main` (`.env.example` modified), so it was preserved untouched.
- Fetched `origin/main`.
- Created isolated worktree `/private/tmp/MCPContentSearch-notion-cancel-sync`
  on fresh branch `feature/fix-notion-cancel-sync-stuck` from `origin/main`.
- Initial atomic cancellation-fix work was completed directly before the scope
  expanded.
- User later approved a broader `sync_source` background-job change plus
  explicit subagent/delegation use, so the remainder of this work item now uses
  worker orchestration.

## Scope And Non-goals

Scope:

- Confirm whether Notion auth is actually failing in the live Docker path.
- Fix the sync lifecycle so a cancelled MCP request does not leave a permanent
  `running` job for `source_notion`.
- Add focused regression coverage around cancellation cleanup at the ingestion
  service boundary.
- Keep `IngestionService.sync_source()` as the blocking internal execution path,
  add `IngestionService.start_sync_source()` as the immediate-return background
  launcher, and route the public MCP `sync_source` tool through that launcher.
- Add or update focused contract/E2E coverage for the new `running -> poll for
  completion` behavior.

Non-goals:

- Do not change Notion fetch semantics, block parsing, or credentials policy.
- Do not inspect or mutate user Chroma content beyond the existing live debug
  evidence already gathered.
- Do not change MCP tool shapes unless required for truthful lifecycle cleanup.

## Acceptance Criteria

- Live-debug evidence remains consistent with `NOTION_API_KEY` being loaded and
  accepted by Notion, not rejected for auth.
- If `sync_source()` is cancelled during a long fetch, the sync job is no
  longer left in `running` state forever.
- A subsequent sync attempt can start again after cancellation cleanup.
- Focused regression tests cover the cancellation path without live Notion or
  user data.
- The public MCP `sync_source()` tool returns promptly with a truthful running
  job when it starts a long sync.
- Direct internal callers of `IngestionService.sync_source()` still receive the
  blocking completion path.
- The background worker continues after the request returns and eventually marks
  the job/source succeeded or failed in SQLite metadata.
- A second public `sync_source()` tool call during active background work
  reuses the same running job instead of starting duplicate work.

## Step Breakdown

1. Trace the live failure path from MCP request cancellation through
   `IngestionService.sync_source()` and SQLite job ownership/status handling.
2. Keep the cancellation cleanup fix in place.
3. Keep direct service `sync_source()` blocking, add `start_sync_source()` for
   the request-facing launcher, and let the background worker perform the long
   ingestion lifecycle.
4. Update focused ingestion coverage for the new launcher and adapt MCP
   contract/retained E2E coverage to the public `running -> poll` behavior.
5. Run broader verification and functional smoke entries relevant to source
   sync, then proceed to delegated review.

## Files Likely To Change

- `indexing/ingestion_service.py`
- `api/tools.py`
- `tests/indexing/test_ingestion_service.py`
- `tests/api/test_tools_contract.py`
- `tests/e2e/test_contextwiki_flow.py`
- `tests/e2e/test_phase_b_connectors_flow.py`
- `docs/contextwiki-core-understanding.md`

## Test And Verification Plan

- Focused regression: `uv run pytest tests/indexing/test_ingestion_service.py -q`
- Focused contract regression: `uv run pytest tests/api/test_tools_contract.py -q`
- Syntax safety: `python -m compileall api core environments fetching indexing search storage main.py`
- Functional gate: `./scripts/verify_functional_e2e.sh`
- Retained MCP E2E regression: `uv run pytest tests/e2e/test_contextwiki_flow.py tests/e2e/test_phase_b_connectors_flow.py tests/e2e/test_obsidian_connector_flow.py -q`

## Functional Smoke Matrix

| Feature | Surface | Data Mode | Expected Result | Status |
| --- | --- | --- | --- | --- |
| Configured Notion sync cancellation cleanup | direct `IngestionService.sync_source` / metadata job state | fake/temp | Cancelled blocking sync ends in truthful non-running state and does not block later syncs | completed |
| Configured source retry after cancellation | direct `IngestionService.sync_source` / metadata job state | fake/temp | A new blocking sync can start after the cancelled job is reconciled | completed |
| Background configured sync launch | MCP `sync_source` / metadata job state | fake/temp | MCP `sync_source` returns a running job quickly while background work keeps going | completed |
| Background sync completion polling | MCP `get_sync_status` / retained E2E caller | fake/temp | Polling eventually observes `succeeded` or `failed` after background completion | completed |
| Live Docker Notion auth evidence | Docker logs / current metadata DB | live read-only | Notion requests continue to return `200 OK`, confirming auth is not the failure | completed |

## Architecture And ADR Constraints

- ADR 0002: SQLite metadata is the authoritative sync/job status store; cleanup
  must keep source/job state truthful without storing raw secrets.
- ADR 0004: Connector-specific behavior stays inside existing fetching/indexing
  boundaries; do not add connector-specific MCP tools for this fix.
- Architecture doc: keep API/tool contracts thin and put lifecycle logic in the
  service/storage layers.

## Risks And Rollback Notes

- `asyncio` cancellation can bypass generic `except Exception` handling, so the
  fix must explicitly handle cancellation without swallowing unrelated errors.
- Background task failures must be persisted, not dropped silently.
- Request-return semantics change from "usually completed job" toward "running
  job launcher", so tests and documentation must be updated carefully.
- Cleanup must avoid falsely marking a still-active competing job as failed.
- Rollback is limited to reverting the cancellation-specific lifecycle changes
  and their focused test coverage.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Preserved dirty `main`, fetched `origin/main`, and created isolated feature worktree. | `git status --short --branch`; `git fetch origin main`; `git worktree add -b feature/fix-notion-cancel-sync-stuck /private/tmp/MCPContentSearch-notion-cancel-sync origin/main` |
| Root-cause investigation | completed | Verified live Docker Notion requests return `200 OK`; stuck state comes from cancelled sync request leaving job `2b55e869-...` in `running` with unchanged heartbeat while fetch was still in progress. | Docker logs show `Found 265 Notion pages`, `Progress: 10/265`, then `Request cancelled`; SQLite `sync_jobs` row stays `running` with `heartbeat_at=2026-06-15T06:06:42.327570+00:00` |
| Planning | completed | Created this plan before target edits. | `docs/plan/2026-06-15-notion-cancel-sync-stuck.md` |
| Implementation | completed | Production ownership moved to the implementation worker: direct `IngestionService.sync_source()` stayed blocking, public MCP `sync_source` now routes through `start_sync_source()`, and this follow-up stayed scoped to owned tests/docs. | `indexing/ingestion_service.py`; `api/tools.py`; owned test/docs files only edited here |
| Boundary revision | completed | Adjusted the test/doc plan to the reduced-blast-radius boundary: direct service tests stay mostly unchanged, MCP-level tests poll `get_sync_status`. | User integration update on 2026-06-15; owned file scope confirmed |
| Focused verification | completed | Focused ingestion, API contract, public contract, and retained MCP E2E coverage passed with the revised boundary. | `uv run pytest tests/indexing/test_ingestion_service.py -q -k "start_sync_source_returns_running_job_and_completes_in_background or cancelled_source_sync_marks_job_failed_and_allows_retry"` -> 2 passed; `uv run pytest tests/api/test_tools_contract.py -q -k "sync_source_"` -> 4 passed; `uv run pytest tests/contracts/test_public_mcp_contracts.py -q -k "sync_source_contract or get_sync_status_contract"` -> 2 passed; `uv run pytest tests/e2e/test_contextwiki_flow.py -q -k "contextwiki_fake_e2e_sync_search_fetch_and_answer or contextwiki_temp_chroma_e2e_sync_search_fetch_and_answer or phase1_alias_expansion or phase2_query_rewrite or phase3_repository_lookup"` -> 5 passed; `uv run pytest tests/e2e/test_phase_b_connectors_flow.py -q -k "retained_source_smoke or retained_github_sync_through_mcp_tools"` -> 1 passed |
| Functional smoke | completed | Repo functional smoke passed after the retained E2E suite was updated for immediate-return `sync_source` plus `get_sync_status` polling. | `./scripts/verify_functional_e2e.sh` -> 25 passed in 4.90s |
| Scope expansion | completed | User approved the broader background-job direction for `sync_source` plus explicit delegation. | Approved `1+4`, then confirmed to proceed |
| Worker orchestration | completed | The follow-up initially stayed atomic within the owned test/doc scope after the boundary revision, and later review-driven fixes extended back into the owned production boundary without spawning a new split. | Ownership stayed inside the planned sync-source boundary across code, tests, docs, and plan updates |
| Verification refresh | completed | Re-ran syntax, focused regressions, retained MCP E2E coverage, full functional smoke, and diff hygiene in the isolated worktree before review. | `python -m compileall api core environments fetching indexing search storage main.py` passed; `uv run pytest tests/indexing/test_ingestion_service.py tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py -q` -> 86 passed in 2.97s; `uv run pytest tests/e2e/test_contextwiki_flow.py tests/e2e/test_phase_b_connectors_flow.py tests/e2e/test_obsidian_connector_flow.py -q` -> 25 passed in 5.07s; `./scripts/verify_functional_e2e.sh` -> 25 passed in 4.90s; `git diff --check` passed |
| Review pass 1 | completed | Five fresh reviewers found one real race window around background-task cancellation plus public contract doc and MCP re-entry test gaps. | Findings referenced `indexing/ingestion_service.py`, `README.md`, `.agents/docs/architecture.md`, `api/tools.py`, and `tests/contracts/test_public_mcp_contracts.py` |
| Review fixes | completed | Hardened background-task cancellation reconciliation, added focused launcher reuse/cancellation regressions, expanded MCP contract coverage for same-job reuse plus polling, and updated public contract docs. | `indexing/ingestion_service.py`; `tests/indexing/test_ingestion_service.py`; `tests/contracts/test_public_mcp_contracts.py`; `README.md`; `.agents/docs/architecture.md`; `api/tools.py` |
| Review-fix verification | completed | Re-ran syntax, focused regression suites, retained MCP E2E suites, functional smoke, and diff hygiene after the review-driven fixes. | `python -m compileall api core environments fetching indexing search storage main.py` passed; `uv run pytest tests/indexing/test_ingestion_service.py tests/contracts/test_public_mcp_contracts.py tests/api/test_tools_contract.py -q` -> 89 passed in 3.31s; `uv run pytest tests/e2e/test_contextwiki_flow.py tests/e2e/test_phase_b_connectors_flow.py tests/e2e/test_obsidian_connector_flow.py -q` -> 25 passed in 6.01s; `./scripts/verify_functional_e2e.sh` -> 25 passed in 5.52s; `git diff --check` passed |
| Review pass 2 | completed | Five fresh reviewers found one more contract-hardening gap in the MCP tool fallback path and one bounded-wait gap in retained E2E polling. | Findings referenced `api/tools.py`, `tests/e2e/test_contextwiki_flow.py`, and a small plan audit-trail wording mismatch |
| Review pass 2 fixes | completed | Removed the public MCP fallback to blocking `sync_source`, switched the retained FastMCP polling tests to bounded helper usage, and aligned the plan audit-trail wording. | `api/tools.py`; `tests/e2e/test_contextwiki_flow.py`; `docs/plan/2026-06-15-notion-cancel-sync-stuck.md` |
| Review pass 2 verification | completed | Re-ran the affected API/contract/retained E2E suites plus the repo functional smoke after the second-pass fixes. | `uv run pytest tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py tests/e2e/test_contextwiki_flow.py -q` -> 50 passed in 5.61s; `./scripts/verify_functional_e2e.sh` -> 25 passed in 5.55s; `git diff --check` passed |
| Review pass 3 | completed | Five fresh reviewers found one remaining early-failure window before the background worker entered `_run_sync_source_job()`'s guarded path. | Finding referenced `indexing/ingestion_service.py` initial `get_sync_job()` lookup before the main `try` block |
| Review pass 3 fixes | completed | Moved the initial metadata lookup under exception handling and added a regression that forces the first background `get_sync_job()` read to miss so the job is marked failed instead of staying `running`. | `indexing/ingestion_service.py`; `tests/indexing/test_ingestion_service.py` |
| Review pass 3 verification | completed | Re-ran syntax, focused ingestion/API/contract/retained E2E coverage, and repo functional smoke after the early-failure fix. | `python -m compileall api core environments fetching indexing search storage main.py` passed; `uv run pytest tests/indexing/test_ingestion_service.py tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py tests/e2e/test_contextwiki_flow.py -q` -> 96 passed in 10.44s; `./scripts/verify_functional_e2e.sh` -> 25 passed in 7.72s; `git diff --check` passed |
| Review pass 4 | completed | Five fresh reviewers found one remaining architecture-decision gap: the public MCP `sync_source` contract shift needed an ADR. | Finding referenced `.agents/docs/adr/README.md` policy and the new public `sync_source` polling contract |
| Review pass 4 fixes | completed | Added ADR 0007 to record the internal blocking vs public background-launch sync boundary, polling expectations, and cancellation ownership. | `.agents/docs/adr/0007-sync-source-background-launch-contract.md`; `.agents/docs/adr/README.md` |
| Review pass 4 verification | completed | Re-checked diff hygiene after the ADR/documentation-only follow-up. | `git diff --check` passed |
| Review pass 5 | completed | Five fresh reviewers found one remaining contract mismatch: a direct internal `sync_source()` call did not wait for a locally launched background sync on the same source. | Finding referenced `indexing/ingestion_service.py`, ADR 0007, and `docs/contextwiki-core-understanding.md` |
| Review pass 5 fixes | completed | Updated the blocking direct path to await the local in-flight background task for the same source and added a regression that launches background sync then joins it through direct `sync_source()`. | `indexing/ingestion_service.py`; `tests/indexing/test_ingestion_service.py` |
| Review pass 5 verification | completed | Re-ran focused ingestion/API/contract/retained E2E coverage and repo functional smoke after restoring direct blocking semantics. | `uv run pytest tests/indexing/test_ingestion_service.py tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py tests/e2e/test_contextwiki_flow.py -q` -> 97 passed in 10.07s; `./scripts/verify_functional_e2e.sh` -> 25 passed in 8.03s; `git diff --check` passed |
| Review pass 6 | completed | A fresh reviewer found one more aggregate-contract regression: `sync_all()` was joining a local background source sync instead of reporting it as `blocked`. | Finding referenced `indexing/ingestion_service.py`, `README.md`, and `docs/contextwiki-core-understanding.md` blocked semantics |
| Review pass 6 fixes | completed | Split the direct-join behavior from the bulk path so `sync_all()` keeps non-joining blocked semantics while direct `sync_source()` still joins a local background sync, and added a regression for `start_sync_source()` followed by `sync_all()`. | `indexing/ingestion_service.py`; `tests/indexing/test_ingestion_service.py` |
| Review pass 6 verification | completed | Re-ran focused ingestion/API/contract/retained E2E coverage and repo functional smoke after restoring `sync_all()` blocked behavior. | `uv run pytest tests/indexing/test_ingestion_service.py tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py tests/e2e/test_contextwiki_flow.py -q` -> 98 passed in 10.06s; `./scripts/verify_functional_e2e.sh` -> 25 passed in 8.17s; `git diff --check` passed |
| Review pass 7 | completed | A fresh reviewer found one remaining test-quality gap: the public-contract fake could recreate a new running job while still appearing to "reuse" because it hid `status`/`job_id` attributes and reused the same hard-coded id. | Finding referenced `tests/contracts/test_public_mcp_contracts.py` fake metadata/job behavior |
| Review pass 7 fixes | completed | Exposed fake job attributes in `Dumpable` storage and made the fake ingestion service issue distinct job ids so contract reuse tests fail if reuse breaks. | `tests/contracts/test_public_mcp_contracts.py` |
| Review pass 7 verification | completed | Re-ran the public MCP contract suite and diff hygiene after tightening the fake reuse semantics. | `uv run pytest tests/contracts/test_public_mcp_contracts.py -q` -> 10 passed in 0.47s; `git diff --check` passed |
| Review pass 8 | completed | A fresh reviewer found two final follow-ups outside the main sync engine: the public demo script still assumed blocking MCP sync, and an untracked design spec artifact was still present in the branch. | Findings referenced `scripts/demo_public_flow.py`, `tests/scripts/test_demo_public_flow.py`, and `docs/superpowers/specs/2026-06-15-background-sync-source-design.md` |
| Review pass 8 fixes | completed | Updated the public demo harness to poll `get_sync_status()` to a terminal job before retrieval/answer steps and removed the stray untracked design artifact from `docs/superpowers/specs/`. | `scripts/demo_public_flow.py`; deleted `docs/superpowers/specs/2026-06-15-background-sync-source-design.md` |
| Review pass 8 verification | completed | Re-ran the public demo-script suite and diff hygiene after the demo/artifact follow-up. | `uv run pytest tests/scripts/test_demo_public_flow.py -q` -> 16 passed in 25.28s; `git diff --check` passed |
| Review pass 9 | completed | Fresh reviewers found two last truthfulness gaps: joined-background pre-start cancellation still had a one-tick direct-caller race, and the public demo output was overwriting the launcher payload with the terminal job. | Findings referenced `indexing/ingestion_service.py`, `tests/indexing/test_ingestion_service.py`, `scripts/demo_public_flow.py`, and `tests/scripts/test_demo_public_flow.py` |
| Review pass 9 fixes | completed | Added reconcile-and-yield handling for joined-background pre-start cancellation, extended the regression coverage, and kept the demo's `sync` payload as the initial launcher response while leaving terminal completion in `status.latest_job`. | `indexing/ingestion_service.py`; `tests/indexing/test_ingestion_service.py`; `scripts/demo_public_flow.py`; `tests/scripts/test_demo_public_flow.py` |
| Review pass 9 verification | completed | Re-ran focused ingestion/API/contract/retained E2E coverage, the public demo-script suite, and the repo functional smoke after the final truthfulness fixes. | `uv run pytest tests/indexing/test_ingestion_service.py tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py tests/e2e/test_contextwiki_flow.py -q` -> 101 passed in 13.85s; `uv run pytest tests/scripts/test_demo_public_flow.py -q` -> 16 passed in 27.72s; `./scripts/verify_functional_e2e.sh` -> 25 passed in 10.47s; `git diff --check` passed |
| Review pass 10 | completed | A fresh reviewer flagged one remaining wording mismatch: the ADR/context note overstated blocking behavior for direct callers that observe a foreign running SQLite job with no joinable local task. | Finding referenced ADR 0007 and `docs/contextwiki-core-understanding.md` direct-caller wording |
| Review pass 10 fixes | completed | Narrowed the direct-caller wording so blocking semantics apply when the caller starts the work itself or joins a same-process local background task, while foreign running jobs are described as current-state returns. | `.agents/docs/adr/0007-sync-source-background-launch-contract.md`; `docs/contextwiki-core-understanding.md` |
| Review pass 10 verification | completed | Re-checked diff hygiene after the wording-only follow-up. | `git diff --check` passed |
| Review pass 11 | completed | A fresh reviewer found one last direct-caller race: after a local background task is cancelled and reconciled, `sync_source()` still needed to surface that terminal result once instead of immediately opening a fresh job. | Findings referenced `indexing/ingestion_service.py` pre-start cancel path and the matching regression in `tests/indexing/test_ingestion_service.py` |
| Review pass 11 fixes | completed | Stored the most recent reconciled terminal background job per source so direct `sync_source()` can return that one-shot terminal result, while `start_sync_source()` still clears it and opens a fresh retry. | `indexing/ingestion_service.py` |
| Review pass 11 verification | completed | Re-ran focused ingestion/API/contract/retained E2E coverage, the public demo-script suite, and the repo functional smoke after the direct-caller terminal-result fix. | `uv run pytest tests/indexing/test_ingestion_service.py tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py tests/e2e/test_contextwiki_flow.py -q` -> 101 passed in 17.14s; `uv run pytest tests/scripts/test_demo_public_flow.py -q` -> 16 passed in 33.83s; `./scripts/verify_functional_e2e.sh` -> 25 passed in 11.50s; `git diff --check` passed |
| Review pass 12 | completed | A fresh reviewer found one remaining cache-scope regression: the one-shot terminal background cache also captured successful background completions and could suppress the next fresh direct sync. | Finding referenced `indexing/ingestion_service.py` `_recent_terminal_background_jobs` scope and the missing post-success rerun regression |
| Review pass 12 fixes | completed | Restricted the one-shot terminal cache to cancelled-to-failed background reconciliations only and added a regression that a successful background completion is followed by a fresh direct sync with a new job id. | `indexing/ingestion_service.py`; `tests/indexing/test_ingestion_service.py` |
| Review pass 12 verification | completed | Re-ran focused ingestion/API/contract/retained E2E coverage, the public demo-script suite, and the repo functional smoke after narrowing the terminal cache. | `uv run pytest tests/indexing/test_ingestion_service.py tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py tests/e2e/test_contextwiki_flow.py -q` -> 102 passed in 15.00s; `uv run pytest tests/scripts/test_demo_public_flow.py -q` -> 16 passed in 33.69s; `./scripts/verify_functional_e2e.sh` -> 25 passed in 9.66s; `git diff --check` passed |
| Review pass 13 | completed | A fresh reviewer found two remaining race windows: callback-first cancelled background handoff could still skip the one-shot failed result, and the direct entrypoint only consumed that cache when a done task was still locally visible. | Findings referenced `indexing/ingestion_service.py` one-shot cache consumption order and missing callback-first regression coverage |
| Review pass 13 fixes | completed | Simplified direct `sync_source()` to reconcile then consume the one-shot cancelled-job cache on every entry, removed redundant cache writes from the reconcile path, and added a regression for the callback-first cancelled-background handoff. | `indexing/ingestion_service.py`; `tests/indexing/test_ingestion_service.py` |
| Review pass 13 verification | completed | Re-ran focused ingestion coverage, the broader ingestion/API/contract/retained E2E suite, the public demo-script suite, the repo functional smoke, and diff hygiene after the callback-first cancellation fix. | `uv run pytest tests/indexing/test_ingestion_service.py -q` -> 53 passed in 11.84s; `uv run pytest tests/indexing/test_ingestion_service.py tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py tests/e2e/test_contextwiki_flow.py -q` -> 103 passed in 23.06s; `uv run pytest tests/scripts/test_demo_public_flow.py -q` -> 16 passed in 56.73s; `./scripts/verify_functional_e2e.sh` -> 25 passed in 16.24s; `git diff --check` passed |
| Review pass 14 | completed | A fresh reviewer found one remaining authority gap: a cached cancelled local background job could still override a newer authoritative SQLite job started by another owner before the next direct `sync_source()` call. | Finding referenced `indexing/ingestion_service.py` cache freshness against the latest persisted job plus README launcher wording |
| Review pass 14 fixes | completed | Gated the one-shot cancelled-job cache on the latest persisted SQLite job still matching the cached cancelled job, added a regression for a newer foreign running job, and tightened README wording so the public MCP launcher no longer promises generic terminal-job returns. | `indexing/ingestion_service.py`; `tests/indexing/test_ingestion_service.py`; `README.md` |
| Review pass 14 verification | completed | Re-ran focused ingestion coverage, the broader ingestion/API/contract/retained E2E suite, the public demo-script suite, the repo functional smoke, and diff hygiene after the cache-authority fix. | `uv run pytest tests/indexing/test_ingestion_service.py -q` -> 54 passed in 3.26s; `uv run pytest tests/indexing/test_ingestion_service.py tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py tests/e2e/test_contextwiki_flow.py -q` -> 104 passed in 10.66s; `uv run pytest tests/scripts/test_demo_public_flow.py -q` -> 16 passed in 21.74s; `./scripts/verify_functional_e2e.sh` -> 25 passed in 7.01s; `git diff --check` passed |
| Review pass 15 | completed | A fresh reviewer found two remaining contract-maintenance gaps: the ADR/context note still described cancelled-local handoff too broadly, and the retained public contract coverage still did not pin the no-blocking-fallback branch when `start_sync_source()` is unavailable. | Findings referenced ADR 0007, `docs/contextwiki-core-understanding.md`, `tests/api/test_tools_contract.py`, and `tests/contracts/test_public_mcp_contracts.py` |
| Review pass 15 fixes | completed | Narrowed the ADR/context wording so cancelled-local handoff only applies while that cancelled job remains the latest authoritative SQLite job, and added explicit API plus retained public contract coverage for the no-background-launcher error path. | `.agents/docs/adr/0007-sync-source-background-launch-contract.md`; `docs/contextwiki-core-understanding.md`; `tests/api/test_tools_contract.py`; `tests/contracts/test_public_mcp_contracts.py` |
| Review pass 15 verification | completed | Re-ran focused API/contract coverage, the broader ingestion/API/contract/retained E2E suite, the public demo-script suite, the repo functional smoke, and diff hygiene after the contract-maintenance follow-up. | `uv run pytest tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py -q` -> 46 passed in 0.81s; `uv run pytest tests/indexing/test_ingestion_service.py tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py tests/e2e/test_contextwiki_flow.py -q` -> 106 passed in 9.75s; `uv run pytest tests/scripts/test_demo_public_flow.py -q` -> 16 passed in 22.78s; `./scripts/verify_functional_e2e.sh` -> 25 passed in 7.17s; `git diff --check` passed |
