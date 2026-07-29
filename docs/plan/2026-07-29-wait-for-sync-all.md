# Wait For Sync All

## User Request

Add a ContextWiki MCP tool that starts all retained source syncs and waits until
every launched or already-running job reaches a terminal state, so Claude can
return one final per-source result without requiring the user to poll manually.

## Branch Preflight Result

- Started from a clean `/Users/eunhwa/IdeaProjects/MCPContentSearch` worktree on
  `main`.
- Fetched and fast-forward checked `origin/main`; local `main` remained current
  at `38cddedf7de4ee85d7b9e2d5e145df44b7b399c8`.
- Removed only local non-worktree branches already merged into `main`; preserved
  linked-worktree branches and all unmerged/local-only work.
- Created fresh branch `feature/wait-for-sync-all`; it is `0 0` relative to
  `origin/main`.

## Scope and Non-Goals

- Add a public `wait_for_sync_all` MCP tool that launches or reuses all retained
  configured-source jobs and waits for the exact returned job IDs.
- Return truthful per-source terminal, skipped, failed, or timeout outcomes and
  a concise aggregate summary.
- Keep existing `sync_all` launch-only behavior and response contract unchanged.
- Bound caller-controlled timeout and polling inputs so the tool cannot wait
  forever or busy-loop.
- Update retained MCP contract tests, functional E2E coverage, README guidance,
  and the maintained architecture document.
- Do not change connector fetching, indexing, Chroma, SQLite schema, tombstone
  behavior, source identity, or existing sync job ownership.
- Do not call live Notion, Tistory, GitHub, Obsidian, or embedding providers.
- Do not inspect or mutate user Chroma or SQLite data; tests use fakes and
  temporary metadata stores.

## Acceptance Criteria

- `wait_for_sync_all(...)` is registered as a FastMCP tool and is discoverable
  beside the retained source-sync tools.
- One call starts/reuses every public retained source and waits for the exact
  jobs returned by that launch operation.
- The normal response contains a final `succeeded` or `failed` job payload for
  each started/already-running source, without requiring client-side polling.
- Mixed success/failure, disabled/skipped source, launch failure, timeout, and
  invalid timeout/poll arguments produce truthful bounded responses without
  leaking sensitive errors.
- Timeout does not cancel background sync jobs; timed-out jobs remain visible as
  running so a later status call can observe completion.
- Existing `sync_all`, `sync_source`, `get_sync_status`, and retrieval contracts
  remain compatible.
- Focused public MCP contract tests and retained functional E2E tests exercise
  the new tool through `FastMCP.call_tool(...)`.
- README and `.agents/docs/architecture.md` explain when to use launch-only
  `sync_all` versus completion-waiting `wait_for_sync_all`.

## Step Breakdown

1. `completion-service-contract`: add bounded completion waiting within
   `IngestionService`, keyed by exact launch job IDs and preserving background
   task ownership/cancellation behavior.
2. `mcp-public-contract`: register and safely serialize the new MCP tool,
   including aggregate completion status, per-source outcomes, redacted errors,
   and bounded parameter validation.
3. `focused-and-e2e-tests`: add public contract and retained functional E2E
   coverage for success, failure/partial completion, timeout, and existing-tool
   compatibility using fake connectors and temporary SQLite.
4. `docs-and-architecture`: document the new tool and update the maintained bulk
   sync flow without presenting launch acceptance as completion.
5. `verification-review-delivery`: run focused checks, functional smoke, full
   verification, fresh five-reviewer gates, then commit, push, and open a
   `main`-base PR.

## Worker Ownership

| Persona | Owned boundary | Acceptance and verification |
| --- | --- | --- |
| Async sync implementation worker | `indexing/ingestion_service.py`, `api/tools.py` | Implement the smallest safe waiting contract; preserve `sync_all`; no tests/docs edits; run compile or focused imports. |
| MCP test worker | `tests/contracts/test_public_mcp_contracts.py`, `tests/e2e/test_contextwiki_flow.py` | Cover the public caller surface and exact-job/timeout semantics with fake/temp data; do not edit production/docs files; run focused pytest. |
| Docs worker | `README.md`, `.agents/docs/architecture.md` | Document tool choice, bounded wait, timeout/non-cancellation, and final result semantics; no production/test edits; run `git diff --check` on owned files. |

All workers share this task branch, must preserve concurrent changes, and must
not commit, push, open PRs, inspect secrets, inspect or mutate user data, or
change files outside their assigned ownership.

## Files Likely To Change

- `indexing/ingestion_service.py`
- `api/tools.py`
- `tests/contracts/test_public_mcp_contracts.py`
- `tests/e2e/test_contextwiki_flow.py`
- `README.md`
- `.agents/docs/architecture.md`
- `docs/plan/2026-07-29-wait-for-sync-all.md`

## Test and Verification Plan

- `python -m compileall api core environments fetching indexing search storage main.py`
- `uv run pytest -q tests/contracts/test_public_mcp_contracts.py`
- `uv run pytest -q tests/e2e/test_contextwiki_flow.py`
- A local FastMCP registration/call smoke using fake connectors and a temporary
  SQLite database, covered by the retained E2E test.
- `./scripts/verify_functional_e2e.sh`
- `./scripts/verify_all.sh`
- `git diff --check`

This feature changes source-sync orchestration but not retrieval/answer quality,
so the retained retrieval evaluation suite does not require new eval coverage.

## Functional Smoke Matrix

| Feature or workflow | Caller surface | Safe data mode | Expected result | Planned command/evidence | Result |
| --- | --- | --- | --- | --- | --- |
| `wait_for_sync_all` success | FastMCP tool call | Fake connectors + temp SQLite | One call returns all exact jobs as terminal successes | Focused retained E2E test | passed |
| `wait_for_sync_all` mixed/failed | FastMCP tool call | Fake success/failure connectors + temp SQLite | Truthful partial/failed aggregate and redacted per-source error | Focused contract/E2E test | passed |
| `wait_for_sync_all` timeout | FastMCP tool call | Gated fake connector + temp SQLite | Timed-out running job returned and background work not cancelled | Focused contract/E2E test | passed |
| `sync_all` launch-only compatibility | FastMCP tool call | Fake connectors + temp SQLite | Returns while jobs are running with existing acceptance vocabulary | Existing and focused E2E tests | passed |
| `sync_source` | FastMCP tool call | Fake connector + temp SQLite | Launch/reuse behavior remains valid | Functional E2E gate | passed |
| `get_sync_status` | FastMCP tool call | Fake connector + temp SQLite | Running and terminal status remains observable | Functional E2E gate | passed |
| `list_sources` | FastMCP tool call | Fake registry + temp SQLite | Registered source health remains visible | Functional E2E gate | passed |
| `search_context` | Retained test/script | Fake/temp retrieval | Existing chunk search remains valid | Functional E2E gate | passed |
| `search_documents` | Retained test/script | Fake/temp retrieval | Existing grouped document search remains valid | Functional E2E gate | passed |
| `fetch_context` | Retained test/script | Temp SQLite | Existing document/chunk hydration remains valid | Functional E2E gate | passed |
| Internal answer helper | Retained test/script | Fake/temp evidence | Existing grounded helper remains valid | Functional E2E gate | passed |
| Live configured-source sync | Claude/Desktop MCP | User Chroma/SQLite + external/local sources | Not run without explicit approval | Nearest substitute passed through fake/temp FastMCP E2E and the full functional gate | blocked/gated |

## Architecture Constraints

- Completion waiting belongs in the ingestion service; `api/tools.py` owns only
  MCP parameter validation, public filtering, redaction, and serialization.
- SQLite remains the sync lifecycle source of truth. Waiting must read exact job
  IDs and must not add a database schema or alternate in-memory completion
  authority.
- A timeout is a caller observation boundary, not a cancellation request.
- Existing per-source running-job guards and background task finalizers remain
  authoritative.
- Public filtering must not expose non-retained sources.
- Public error text must continue through the existing redaction helpers.

## Risks and Rollback Notes

- An MCP client may impose a shorter request timeout than the tool's configured
  wait. Keep the server-side timeout bounded and return a truthful timeout
  response when the tool remains connected.
- Reading only each source's latest job could accidentally report a later sync;
  track the exact launch job IDs instead.
- Cancelling the tool coroutine must not cancel the background jobs. The wait
  loop observes persisted state and does not await the worker tasks directly.
- Rollback is removal of the new tool/service wait method and its documentation;
  existing `sync_all` and polling behavior remain untouched.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Updated clean `main`, safely removed only merged unlinked branches, and created `feature/wait-for-sync-all`. | `git fetch origin main`; `git pull --ff-only origin main`; `git switch -c feature/wait-for-sync-all`; `git rev-list --left-right --count HEAD...origin/main` -> `0 0` |
| Planning | completed | Defined exact-job, bounded-wait MCP behavior, ownership, verification, smoke, and rollback constraints. | This plan |
| Worker orchestration | completed | Dispatched disjoint production, test, and docs workers with explicit safety and ownership boundaries. | Workers `/root/sync_wait_impl`, `/root/sync_wait_tests`, `/root/sync_wait_docs` |
| Implementation | completed | Added exact-job SQLite completion observation in `IngestionService`, the public bounded `wait_for_sync_all` MCP contract, retained docs, contract tests, E2E tests, and app tool registration coverage. | `indexing/ingestion_service.py`; `api/tools.py`; `README.md`; `.agents/docs/architecture.md`; test files |
| Focused verification | completed | Compile, Ruff, diff check, and focused public contract/E2E/app composition tests passed. | `python -m compileall ...`; `uv run pytest -q tests/contracts/test_public_mcp_contracts.py tests/e2e/test_contextwiki_flow.py tests/test_app_composition.py` -> `29 passed`; `uv run ruff check ...`; `git diff --check` |
| Functional smoke | completed | All fake/temp task-relevant MCP, sync, status, retrieval, fetch, and answer rows passed; live configured-source sync remains approval-gated with the retained fake/temp suite as substitute. | `./scripts/verify_functional_e2e.sh` -> `30 passed`; matrix above |
| Middle review pass 1 | completed | Five fresh read-only reviewers found three actionable boundaries: FastMCP coerced JSON booleans before the handler's numeric check; disabled sources could label an exact existing RUNNING job as skipped; and a 10ms public poll minimum allowed excessive SQLite connection churn. One reviewer reported no findings; the remaining findings converged on these issues. | Reviewers `/root/middle_review_1_mcp`, `/root/middle_review_1_async`, `/root/middle_review_1_tests`, `/root/middle_review_1_arch`, `/root/middle_review_1_security` |
| Review fix pass 1 | completed | Added strict Pydantic numeric input at the FastMCP schema boundary, prioritized exact RUNNING jobs over disabled launch classification, raised the public/service poll minimum from 0.01s to 0.1s, and added real caller/temporary-SQLite regression coverage. | Pydantic `StrictFloat` rejects booleans before the handler; focused suite -> `32 passed`; Ruff/compile/diff checks passed |
| Post-fix functional smoke | completed | Reran every affected sync/status/MCP row plus the retained fake/temp functional suite after review fixes. | `./scripts/verify_functional_e2e.sh` -> `31 passed`; no live/user-data access |
| Middle review pass 2 | completed | Five fresh read-only reviewers found no behavioral issue. The first two identified that the shared worktree had drifted back to `main`; the orchestrator switched the unchanged dirty worktree back to the existing feature branch, and the remaining three reviewers reported no findings. | Reviewers `/root/middle_review_2_mcp`, `/root/middle_review_2_async`, `/root/middle_review_2_tests`, `/root/middle_review_2_arch`, `/root/middle_review_2_security` |
| Branch-drift fix verification | completed | Restored `feature/wait-for-sync-all` without changing the diff, confirmed `0 0` against `origin/main`, reran whitespace/status checks, and refreshed the retained functional smoke. A new fully clean reviewer pass is still required because pass 2 contained the branch-state finding. | `git switch feature/wait-for-sync-all`; `git status --short --branch`; `git rev-list --left-right --count HEAD...origin/main` -> `0 0`; `git diff --check`; functional E2E -> `31 passed` |
| Middle review pass 3 | completed | All five fresh read-only reviewers reported no actionable findings across MCP contract, async lifecycle, tests/smoke, architecture/docs, and security/data-safety lenses. | Reviewers `/root/middle_review_3_mcp`, `/root/middle_review_3_async`, `/root/middle_review_3_tests`, `/root/middle_review_3_arch`, `/root/middle_review_3_security` |
| Refactor | completed | No refactor applied: API/schema validation and service validation are intentional boundary defenses, and the retained E2E cases independently lock distinct async transitions. | Diff inspection against local module boundaries; no behavior changed after the clean middle review |
| Integration verification attempt 1 | error | Full verification reached the broad 719-test layer and found one stale expected public-tool set in `tests/api/test_tools_contract.py`; all other 718 tests passed and coverage remained above the gate. Classified as a test expectation gap for the newly registered tool, not a production failure. | `./scripts/verify_all.sh` -> `1 failed, 718 passed`; failing `test_contextwiki_mcp_tools_are_registered` |
| Integration test fix | completed | Added `wait_for_sync_all` to the remaining API tool-registration expectation; focused test, Ruff, and diff checks passed. | `uv run pytest -q tests/api/test_tools_contract.py::test_contextwiki_mcp_tools_are_registered` -> `1 passed` |
| Integration verification attempt 2 | completed | The complete repository wrapper passed static checks, public MCP contracts, all broad non-live tests with coverage, deterministic retrieval/answer evals, and the functional E2E layer. | `./scripts/verify_all.sh` -> public contracts `20 passed`; broad regression `719 passed`, coverage `87.63%`; retrieval eval `13/13`; answer eval `9/9`; functional E2E `31 passed` |
| Final review pass 1 | completed | Five fresh read-only reviewers found no code, test, architecture, or security issues; four identified the same stale duplicate pending rows in this progress log. | Reviewers `/root/final_review_1_mcp`, `/root/final_review_1_async`, `/root/final_review_1_tests`, `/root/final_review_1_arch`, `/root/final_review_1_security` |
| Final review trace fix | completed | Removed the obsolete duplicate `Refactor/integration` and `Final review` pending rows so the plan has one chronological gate state. | Plan-only edit; docs verification rerun before the next final pass |
| Final review pass 2 | completed | All five fresh read-only reviewers reported no actionable findings across MCP contract, async lifecycle, test/smoke, architecture/docs, and security/data-safety lenses. | Reviewers `/root/final_review_2_mcp`, `/root/final_review_2_async`, `/root/final_review_2_tests`, `/root/final_review_2_arch`, `/root/final_review_2_security` |
| PR delivery | in_progress | Stage only the nine task files, run cached diff checks, commit, push, and open a `main`-base PR. | Pending |
