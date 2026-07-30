# Remove Long-Lived Bulk Sync Wait

## User Request

Replace the timeout-prone `wait_for_sync_all` workflow with the reliable public
workflow that starts all syncs through `sync_all()` and then observes
completion through short `get_sync_status()` calls.

## Branch Preflight Result

- Starting worktree: clean `main`.
- Freshness: fetched and fast-forward checked against `origin/main`; ahead/behind
  was `0 0`.
- Safe cleanup: deleted only the merged, unlinked local
  `feature/date-filter-sort-tools` branch. Linked worktree branches and
  local-only work were preserved.
- Task branch: `feature/remove-sync-wait-timeout`, created from current
  `origin/main`; ahead/behind was `0 0` before plan edits.

## Scope and Non-Goals

### Scope

- Remove `wait_for_sync_all` from the public FastMCP tool surface so an MCP
  client cannot select a long-lived request that outlasts its request timeout.
- Remove the now-unused in-process bulk-wait implementation and its timeout
  validation helpers.
- Make `sync_all()` plus short exact-job
  `get_sync_status(source_id=..., job_id=...)` calls the documented bulk
  completion workflow.
- Add an exact-job read mode to `get_sync_status` without changing its existing
  latest-one-source or all-source response shapes when `job_id` is omitted.
- Preserve truthful launch outcomes and per-source status/job progress.
- Update unit, integration/contract, and deterministic functional E2E coverage
  before production code.
- Update README and the maintained architecture document.

### Non-Goals

- Do not change connector fetching, indexing, Chroma, SQLite schema, source
  identity, tombstones, or sync-job ownership.
- Do not change the response shapes of `sync_all` or existing
  `get_sync_status` calls that omit `job_id`; exact-job mode is additive.
- Do not introduce server push, notifications, a durable bulk-monitor entity,
  or a new batch-status persistence model.
- Do not call live Notion, Tistory, GitHub, Obsidian, embedding, or other
  external providers.
- Do not inspect or mutate user Chroma or SQLite data.
- Historical plans remain historical evidence and are not rewritten.

## Acceptance Criteria

- FastMCP no longer registers `wait_for_sync_all`.
- The retained public tool inventory contains `sync_all` and
  `get_sync_status`, and contract documentation directs completion-seeking
  callers to call `sync_all()` once and poll `get_sync_status()` with short,
  separate MCP requests.
- `sync_all()` still returns after launch decisions and does not imply terminal
  completion.
- `get_sync_status(source_id="")` can observe running and terminal states for
  all public sources in a deterministic fake/temp E2E flow.
- `get_sync_status(source_id=..., job_id=...)` returns the exact public sync job
  selected from `sync_all`, even when a newer job has become the source's
  latest job; a newer job is never attributed to the original launch.
- Completion-seeking callers retain only `started` and `already_running`
  `{source_id, job_id}` targets. They report `skipped`/`failed` launch outcomes
  immediately and use paced, bounded polling with explicit deadline/error
  termination rather than polling forever.
- No dead bulk-wait constants, validation helpers, serializers, or service
  methods remain in production code.
- Focused unit, integration/contract, and E2E tests pass, followed by
  `./scripts/verify_all.sh`, task-relevant functional smoke, and clean
  three-reviewer harness passes.
- README and `.agents/docs/architecture.md` describe the retained public
  workflow without presenting launch acceptance as completion.

## Step Breakdown

1. `test-contract-red`: update unit/tool inventory, public contract, and
   deterministic E2E tests to require removal of `wait_for_sync_all` and retain
   the `sync_all` plus all-source `get_sync_status` flow; run the smallest new
   assertion and capture expected failure before production edits.
2. `remove-public-wait`: remove the FastMCP handler, bulk-wait formatting and
   validation code, and the unused `IngestionService.wait_for_sync_all`
   implementation; expose safe exact-job status lookup through the existing
   status tool.
3. `document-polling-workflow`: update README and maintained architecture to
   make paced, bounded, short exact-job polling the sole bulk completion
   contract.
4. `verify-refactor-smoke`: pass focused tests, simplify dead imports/helpers,
   rerun affected tests, require `./scripts/verify_all.sh`, and execute the
   local fake/temp functional smoke inventory.
5. `review-integrate-deliver`: run clean middle and final three-reviewer passes,
   integrate findings through the harness retry loop, then commit, push, and
   open a `main`-base PR.

## Worker Ownership

| Worker | Owned files/modules | Acceptance and verification |
| --- | --- | --- |
| Test worker | `tests/api/test_tools_contract.py`, `tests/contracts/test_public_mcp_contracts.py`, `tests/e2e/test_contextwiki_flow.py`, `tests/e2e/test_phase_b_connectors_flow.py`, `tests/test_app_composition.py` | Tests are changed before production code; add/update unit, integration/contract, and deterministic E2E coverage; capture one expected RED; do not edit production/docs. |
| Runtime worker | `api/tools.py`, `indexing/ingestion_service.py` | Remove only the public/internal bulk-wait path and dead helpers/imports; preserve `sync_all` and status contracts; pass focused tests after RED. |
| Documentation worker | `README.md`, `.agents/docs/architecture.md` | Describe `sync_all` plus short `get_sync_status` polling as the retained workflow; keep historical plans untouched; run diff checks. |

The workers share one feature branch but have disjoint ownership. The main agent
owns sequencing, integration, diff inspection, verification, review routing,
commit, push, and PR creation. Workers must not commit, push, open PRs, inspect
secrets, inspect or mutate user data, or revert other changes.

## Files Likely to Change

- `api/tools.py`
- `indexing/ingestion_service.py`
- `tests/api/test_tools_contract.py`
- `tests/contracts/test_public_mcp_contracts.py`
- `tests/e2e/test_contextwiki_flow.py`
- `tests/e2e/test_phase_b_connectors_flow.py`
- `tests/test_app_composition.py`
- `README.md`
- `.agents/docs/architecture.md`
- `.agents/docs/adr/README.md`
- `.agents/docs/adr/0007-sync-source-background-launch-contract.md`
- `.agents/docs/adr/0008-background-sync-all-and-deterministic-retrieval.md`
- `.agents/docs/adr/0009-exact-sync-job-status-observation.md`
- `docs/plan/2026-07-30-remove-sync-wait-timeout.md`

## TDD RED Evidence

- Ordering: captured at `2026-07-30 08:52 KST` after test-only edits and before
  any runtime production edit.
- Command:
  `uv run pytest -q tests/contracts/test_public_mcp_contracts.py::test_public_fastmcp_tool_inventory_uses_short_sync_status_polling_workflow`
- Covered layer/test: integration/public real FastMCP tool inventory. The test
  worker is also updating the planned unit and deterministic E2E layers before
  runtime integration.
- Non-zero exit code: `1`.
- Expected failure signature:
  `Extra items in the left set: 'wait_for_sync_all'`.
- Missing-behavior explanation: the current server still advertises the
  timeout-prone long-lived tool instead of forcing the short-call polling
  workflow.

## TDD GREEN and Verification Plan

- Focused unit:
  `uv run pytest -q tests/api/test_tools_contract.py tests/test_app_composition.py`
- Focused integration/contract:
  `uv run pytest -q tests/contracts/test_public_mcp_contracts.py`
- Focused deterministic E2E:
  `uv run pytest -q tests/e2e/test_contextwiki_flow.py tests/e2e/test_phase_b_connectors_flow.py`
- Import/startup smoke: run the retained app-composition/tool-registration
  coverage with fake/temp dependencies.
- Post-refactor rerun: rerun all focused commands above.
- Full suite: `./scripts/verify_all.sh`.
- Matching eval gate: not required; this changes sync orchestration exposure,
  not retrieval, ranking, grounding, citation, or answer quality. The full
  suite's retained eval layer remains regression evidence only.

## Functional Smoke Matrix

| Inventory | Caller surface | Safe data mode | Expected result | Status |
| --- | --- | --- | --- | --- |
| Public tool discovery | Real `FastMCP` tool listing | Fake/temp composition | `sync_all` and exact-job-capable `get_sync_status` are present; `wait_for_sync_all` is absent | passed — public contract test and functional E2E wrapper |
| Source inventory | Real `FastMCP.call_tool("list_sources")` | Fake connectors and temporary SQLite/index | Public configured sources are listed without touching live provider or user data | passed — `tests/e2e/test_phase_b_connectors_flow.py` retained GitHub and Obsidian fake/temp flows |
| Single-source launch | Real `FastMCP.call_tool("sync_source", {"source_id": "..."})` | Fake connectors and temporary SQLite/index | Returns a prompt background job launch result whose exact ID can be observed separately | passed — `tests/e2e/test_contextwiki_flow.py` and `tests/e2e/test_phase_b_connectors_flow.py` retained fake/temp caller flows |
| Bulk launch | Real `FastMCP.call_tool("sync_all")` | Fake connectors and temporary SQLite/index | Returns prompt launch outcomes while work is running | passed — retained blocking-connector E2E |
| Exact-job observation | Repeated real `FastMCP.call_tool("get_sync_status", {"source_id": "...", "job_id": "..."})` | Same fake/temp flow | Target jobs become terminal without one long-lived MCP call and remain correctly attributable after newer-job supersession | passed — retained exact-job and supersession E2E |
| Failure/status truthfulness | Real status calls | Fake deterministic failure and temp store | Per-source failure remains visible without leaking sensitive data | passed — retained failed/disabled E2E |
| Live configured sources | Not run | Would access external providers or user data | Blocked/gated pending explicit approval; fake/temp substitute above | blocked/gated — `./scripts/verify_functional_e2e.sh` fake/temp substitute passed `38` tests |

## Architecture Constraints

- SQLite remains authoritative for source and sync-job lifecycle status.
- `sync_all` remains launch-only and preserves per-source concurrency guards.
- `get_sync_status` remains the public completion/progress observation surface.
- No persistence or schema change is needed.
- Background jobs continue independently of individual MCP request lifetimes.
- Public contract removal must be reflected in both README and the maintained
  architecture document.

## Risks and Rollback

- Risk: existing clients explicitly calling `wait_for_sync_all` receive an
  unknown-tool response after upgrade. This is intentional because the user
  requested the timeout-prone public workflow be replaced; README and tool
  descriptions must provide the migration path.
- Risk: tests may contain useful exact-job lifecycle coverage tied only to the
  removed service. Preserve equivalent launch/status lifecycle assertions where
  they protect retained behavior.
- Risk: latest-job polling can misattribute a newer sync to the original bulk
  launch. Exact-job status mode and a deterministic supersession E2E are
  required before delivery.
- Rollback point: restore the removed handler/service/helpers and their focused
  tests from the eventual task commit. No data rollback or reindex is required.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Updated clean `main`, preserved linked/local-only work, created fresh feature branch. | `git fetch origin main`; `git pull --ff-only origin main`; ahead/behind `0 0` |
| Harness planning | completed | Defined public-tool removal, retained polling workflow, worker boundaries, and safety gates. | This plan; architecture and workflow docs read |
| TDD RED | completed | Real FastMCP inventory still exposed the long-lived tool after test-only edits. | `uv run pytest -q tests/contracts/test_public_mcp_contracts.py::test_public_fastmcp_tool_inventory_uses_short_sync_status_polling_workflow` -> exit `1`; extra `wait_for_sync_all`; captured before production edits |
| Runtime implementation | completed | Removed the public handler, service waiter, wait-only helpers/constants/imports, and clarified short-call tool descriptions. | Runtime worker; former RED now passed; compileall and Ruff passed |
| Documentation | completed | Removed the wait tool from retained docs and documented client/agent-owned paced, bounded exact-job polling without server push. | `README.md`; `.agents/docs/architecture.md`; doc diff check passed |
| Focused unit GREEN | completed | Tool registration and app-composition inventories now exclude the wait tool. | Included in combined focused command: `126 passed` |
| Focused integration GREEN | completed | Real FastMCP inventory, tool descriptions, and launch/all-source status lifecycle passed. | Included in combined focused command: `126 passed` |
| Focused E2E GREEN | completed | Fake/temp flows exercise launch, all-source running/terminal observation, reuse, failure, and retained search flow. | `uv run pytest -q tests/api/test_tools_contract.py tests/test_app_composition.py tests/contracts/test_public_mcp_contracts.py tests/e2e/test_contextwiki_flow.py tests/e2e/test_phase_b_connectors_flow.py` -> `126 passed in 4.70s` |
| Refactor and affected rerun | completed | No further refactor was needed; no wait-only non-historical references remained; Ruff/diff checks and focused rerun passed. | Focused command -> `126 passed in 5.09s`; Ruff and `git diff --check` passed |
| Full suite GREEN | completed | Mandatory wrapper rerun after pass-2 remediation passed every layer. | `./scripts/verify_all.sh` -> public contracts `37 passed`; non-live `842 passed`; quality eval retrieval `14/14`, document sort `2/2`, answer `9/9`; functional E2E `38 passed` |
| Functional smoke | completed | Real FastMCP caller paths with fake connectors and temporary stores covered discovery, launch, exact-job polling, supersession, real-SQLite stale recovery, failure truthfulness, and retained search flow. Live configured sources remain safety-gated. | `./scripts/verify_functional_e2e.sh` -> `38 passed in 4.01s`; live external/user-data check remains `blocked/gated` with this fake/temp substitute |
| Middle review | completed/actionable | Three fresh reviewers found exact-job supersession, unbounded/ambiguous polling guidance, and a skipped-target E2E issue. | Correctness, security/data-safety, and reliability reviewers; remediation section below |
| Review remediation RED | completed | Exact-job schema test failed before remediation runtime edits because `job_id` was absent. | Focused schema test -> exit `1`, `KeyError: 'job_id'` |
| Review remediation GREEN | completed | Added secure source/job-paired exact status, supersession isolation, bounded polling guidance, skipped-target exclusion, running hints, and stale-job recovery. The first combined run's two missing-source failures were fixed within the same RED cycle. | Final focused command -> `131 passed in 5.42s`; Ruff and diff check passed |
| Middle review pass 2 | completed/actionable | Fresh reviewers found missing live 10-second cap wording, distinguishable missing/mismatch exact-job payloads, and missing composed stale-recovery E2E. | Correctness, security/data-safety, and reliability reviewers; pass-2 remediation section below |
| Pass-2 remediation | completed | Tests preceded production edits; exposed the exact 10-second cap, normalized every invalid exact-job request, and added real FastMCP/temp-SQLite stale recovery E2E. | Focused command -> `132 passed in 5.25s`; Ruff and diff check passed |
| Middle review pass 3 | completed/actionable | Code/security/reliability were clean; correctness found only incomplete oracle RED fields in this plan. | Three fresh reviewers; no runtime finding |
| Pass-3 docs remediation | completed | Added test layers/names, missing-behavior explanation, and pre-production ordering to the oracle RED record. | Plan-only edit; `git diff --check` |
| Middle review pass 4 | completed/actionable | Security and reliability were clean; correctness found stale latest-job polling in README troubleshooting and accepted ADR 0007/0008. | Three fresh reviewers; docs/ADR remediation required |
| Pass-4 docs/ADR remediation | completed | Migrated troubleshooting to exact polling; added accepted ADR 0009 and explicit partial-supersession notes/index entries for ADR 0007/0008. | README/ADR path checks, tracked/untracked whitespace checks, and cached diff check passed |
| Middle review pass 5 | completed/actionable | Fresh reviewers found FastMCP validation-input exposure, two changed completion tests that still attributed completion through all-source `latest_job`, and missing explicit `sync_source`/`list_sources` smoke rows. | Three fresh correctness, security/data-safety, and reliability reviewers |
| Pass-5 remediation | completed | Added a real FastMCP secret-redaction regression before changing registration, migrated changed completion tests to exact-job observation, and made the full safe source-sync smoke inventory explicit. | Focused five-file suite `134 passed`; full suite public `39 passed`, non-live `844 passed`, evals `14/14`, `2/2`, `9/9`, functional E2E `38 passed`; post-full smoke `38 passed` |
| Middle review pass 6 | completed/clean | All three fresh reviewers reported no actionable correctness, security/data-safety, or reliability findings. | Correctness reran five changed test files: `134 passed`; security selected checks: `4 passed`; reliability selected checks: `6 passed`; reviewer diff/Ruff checks passed |
| Integration | completed | Inspected the final cohesive diff and refreshed the highest-risk public contracts plus whitespace/reference checks without repeating the already-current full suite. | Tool inventory, malformed-input redaction, and exact-job supersession tests: `4 passed`; `git diff HEAD --check`; no non-historical wait implementation/test-helper references |
| Final review | completed/clean | Exactly three fresh read-only reviewers reported no actionable correctness, security/data-safety, or reliability findings. | Correctness reran changed and high-risk contracts; security selected checks `5 passed`; reliability selected checks `6 passed`; reviewer diff/Ruff checks passed |
| Delivery | pending | Stage relevant files, commit, push, and create `main`-base PR. | Pending |

## Middle Review Findings and Remediation

The first middle pass was actionable:

- Correctness/reliability: all-source `latest_job` polling could observe a
  newer job B after target job A completed and misreport B as A.
- Reliability: the public `sync_all` description did not restrict completion
  targets to `started`/`already_running` launches and did not define paced,
  bounded polling or error/deadline termination.
- Test quality: the disabled/skipped E2E incorrectly included a skipped source
  in the pending completion target set.

Remediation returns to TDD RED before production changes:

- Add unit/contract coverage for additive `get_sync_status(..., job_id=...)`
  exact-job behavior and precise tool descriptions.
- Add deterministic E2E coverage where job A finishes, job B starts before the
  next status call, and exact-job mode still returns A.
- Build pending targets only from `started` and `already_running` launch
  outcomes and assert skipped/failed launches are reported immediately.
- Document a 2-second initial polling interval with capped backoff, a 5-minute
  observation deadline, termination after three consecutive status
  errors/missing exact jobs, and reporting of still-running job IDs without
  cancellation when the deadline expires.

Review-remediation RED evidence must be added here before runtime edits:

- Command:
  `uv run pytest -q tests/contracts/test_public_mcp_contracts.py::test_get_sync_status_real_fastmcp_schema_supports_optional_exact_job_id`
- Layers/tests: real FastMCP input-schema contract; the worker is continuing
  unit/tool description, exact-job contract, and deterministic supersession E2E
  coverage before remediation runtime integration.
- Non-zero exit: `1`.
- Expected signature: `KeyError: 'job_id'` at
  `status_properties["job_id"]`.
- Missing behavior: the public `get_sync_status` schema has no additive
  exact-job selector.
- Ordering: captured after review test-only edits and before any remediation
  runtime edit.

Main-agent integration synthesis added one further pre-edit RED for retained
stale-job recovery:

- Command:
  `uv run pytest -q tests/api/test_tools_contract.py::test_get_sync_status_exact_job_triggers_stale_running_job_recovery`
- Layer: unit/tool-service status recovery.
- Non-zero exit: `1`.
- Expected signature: `AssertionError: assert 'running' == 'failed'`.
- Missing behavior: exact-job mode read the stored job directly without
  invoking the retained source/job recovery path used by latest-status reads,
  so a stale running target could remain permanently running.
- Ordering: the test edit and failure predate the recovery runtime fix.

## Middle Review Pass 2 Findings and Remediation

The second fresh middle pass was actionable:

- The live FastMCP `sync_all` description said only `capped backoff` and did
  not expose the maintained 10-second maximum to the agent.
- Exact-job missing, mismatched, and hidden-job cases returned distinguishable
  payloads, creating a low-severity job-existence oracle.
- Exact-job stale recovery was covered only at the fake/unit boundary while the
  smoke matrix claimed composed functional E2E coverage.

Before the next production edit, the test worker must:

- Tighten the real FastMCP description contract to require the explicit
  `2, 4, 8, then 10 seconds maximum` policy.
- Require identical `{source: null, job: null}` payloads for missing,
  mismatched, hidden, or source-less exact-job requests.
- Add real FastMCP plus temporary real SQLite E2E coverage for exact-job stale
  running-job recovery. This composes an already-implemented behavior whose
  unit RED is recorded above; do not manufacture a second missing-behavior RED
  for the E2E layer.

Pass-2 remediation RED evidence:

- Command:
  `uv run pytest -q tests/contracts/test_public_mcp_contracts.py::test_search_tool_descriptions_explain_when_the_llm_should_select_each_tool`
- Layer: real FastMCP description contract.
- Non-zero exit: `1`.
- Expected signature:
  `AssertionError: assert '2, 4, 8, then 10 seconds maximum' in sync_all_description`.
- Missing behavior: the live tool description exposed only generic capped
  backoff, not the maintained 10-second cap/schedule.
- Ordering: captured after test-only edits and before pass-2 production edits.
- The real FastMCP/temp-SQLite stale-recovery E2E is composition coverage for
  behavior whose earlier unit RED predates implementation; no second artificial
  failure was manufactured.

Exact-job oracle normalization also produced pre-edit RED evidence:

- Command:
  `uv run pytest -q tests/api/test_tools_contract.py::test_get_sync_status_exact_job_mode_is_additive_and_never_crosses_source_boundary tests/contracts/test_public_mcp_contracts.py::test_get_sync_status_exact_job_contract_does_not_misattribute_newer_latest_job`
- Layers/tests: unit tool-service isolation through
  `test_get_sync_status_exact_job_mode_is_additive_and_never_crosses_source_boundary`
  and real FastMCP exact-job contract through
  `test_get_sync_status_exact_job_contract_does_not_misattribute_newer_latest_job`.
- Non-zero exit: `1` with `2 failed`.
- Expected signature: both missing-job assertions received
  `{job: None, source: <public source payload>}` instead of the normalized
  `{job: None, source: None}`.
- Missing behavior: missing exact jobs were distinguishable from mismatched,
  hidden, and source-less requests, exposing a job-existence oracle instead of
  the required uniform null response.
- Ordering: captured after test-only edits and before the oracle-normalization
  production edit.

## Middle Review Pass 5 Findings and Remediation

The fifth fresh middle pass was actionable:

- FastMCP/Pydantic rejected malformed non-string `source_id` and `job_id`
  values before the handler and included their raw secret-like input values in
  the resulting tool error.
- Two changed completion flows still used all-source `latest_job` state to
  decide that the jobs returned by `sync_all` were terminal.
- The functional-smoke matrix did not explicitly list the required
  `sync_source` and `list_sources` caller surfaces, although the retained
  fake/temp E2E suite already exercises both.

Pass-5 remediation RED evidence was captured before the registration change:

- Command:
  `uv run pytest -q tests/contracts/test_public_mcp_contracts.py::test_real_fastmcp_redacts_secret_like_non_string_sync_status_ids`
- Layer/test: real FastMCP public validation/error contract through
  `test_real_fastmcp_redacts_secret_like_non_string_sync_status_ids`.
- Non-zero exit: `1` with `2 failed`.
- Expected signature:
  `AssertionError: assert 'super-secret-sync-status-value' not in message`.
  The `source_id` error contained the raw dict input and the `job_id` error
  contained the raw list input through Pydantic's `input_value`.
- Missing behavior: `get_sync_status` was registered through the plain tool
  decorator, so malformed identifiers could be echoed before the handler's
  normal safe-response path.
- Ordering: captured after pass-5 test-only edits and before the pass-5
  production registration change.
