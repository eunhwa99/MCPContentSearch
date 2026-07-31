# Parallel Sync Worker Concurrency

## User Request

Allow durable sync to run multiple connectors in parallel with a conservative
bounded concurrency (suggested 2–N), instead of the current one-job-at-a-time
worker default.

## Desired Behavior

- `sync_all` / `sync_source` enqueue contracts stay launch/enqueue-only.
- The durable sync worker may run up to `N` distinct-source jobs concurrently.
- Default `N = 2` (conservative). Configurable via
  `CONTEXTWIKI_SYNC_WORKER_MAX_CONCURRENT` with bounds `1..8`.
- `N = 1` preserves today’s global single-flight behavior.
- Still at most one active (queued/running) job per `source_id`.
- Cross-process safety: SQLite claim uses a global RUNNING count gate
  (`COUNT(RUNNING) < N`), not per-process wishful thinking.
- Chroma mutations remain serialized by the existing `ContentIndexer`
  mutation lock; parallel work is primarily connector fetch overlap.
- Graceful worker stop cancels/fails all in-flight claimed jobs without
  authorizing tombstones from partial snapshots.
- Public MCP response shapes do not change.

## Acceptance Criteria

1. With `max_concurrent=2` and two queued distinct sources, two workers (or one
   worker filling slots) can have two RUNNING jobs at once.
2. With `max_concurrent=1`, two racing claimers still get only one RUNNING job.
3. A worker process fills up to `N` in-flight asyncio tasks and claims again
   when a slot frees.
4. Invalid env values (`0`, `9`, non-integer, boolean-looking) fail closed at
   worker startup with a clear error (no silent clamp to unsafe defaults).
5. Architecture + ADR 0010 + README document the bounded concurrency model.
6. Unit, integration, and deterministic E2E coverage exist before production
   edits and stay green through `./scripts/verify_all.sh`.

## Branch Preflight Result

- Worktree: `/Users/eunhwa/IdeaProjects/MCPContentSearch` was clean on `main`.
- Fetched/ff `origin/main` at `9d68643` (Merge PR #91).
- Deleted safe merged local non-`main` branches not checked out in other
  worktrees; left linked-worktree branches untouched.
- Fresh branch: `feature/parallel-sync-worker-concurrency`.

## Scope / Likely Files

| Area | Files |
| --- | --- |
| Claim gate | `storage/metadata_store.py` |
| Worker pool | `indexing/sync_worker.py` |
| Config wiring | `indexing/sync_worker.py` (env parse); optionally `environments/config.py` if already the pattern |
| Docs | `.agents/docs/architecture.md`, `.agents/docs/adr/0010-durable-all-source-sync-worker.md`, `README.md` |
| Tests | `tests/storage/test_metadata_store.py`, `tests/indexing/test_sync_worker.py`, `tests/e2e/test_durable_sync_worker_flow.py` |
| Plan | this file |

## Non-Goals

- Changing public MCP tool names or response shapes.
- Live Notion/Tistory/GitHub sync or mutating user Chroma/SQLite.
- Unlimited parallelism or per-source-type process pools.
- Removing the ContentIndexer mutation lock.
- Auto-resuming orphaned partial jobs.

## Architecture Constraints

- SQLite remains job ownership authority.
- Shared Chroma writes stay serialized by indexer lock.
- Per-source enqueue/reuse guards remain authoritative.
- Observation still uses exact `get_sync_status(source_id, job_id)`.

## Worker Personas

| Persona | Owns | Must not touch |
| --- | --- | --- |
| test-red | New/updated unit, integration, E2E tests only under `tests/` | production code, docs outside plan |
| implement-green | `storage/metadata_store.py`, `indexing/sync_worker.py` (+ minimal wiring) | unrelated connectors/search |
| docs-arch | architecture, ADR 0010, README concurrency notes | production logic beyond doc-required wording |
| integrate | conflict resolution, plan progress, verify_all orchestration | secrets, user data |

Main agent remains orchestrator; secrets/user-data/destructive actions stay
non-delegable.

## TDD Plan

### RED (before production)

Add/update:

- **Unit**: claim allows two RUNNING when `max_concurrent=2`; blocks third;
  `max_concurrent=1` keeps single-flight; rejects invalid concurrency.
- **Integration**: SyncWorker.run holds two fake slow jobs concurrently and
  completes both; stop cancels all in-flight.
- **E2E**: durable fake multi-source enqueue + worker with `max_concurrent=2`
  reaches two RUNNING overlapping before both succeed.

Update existing
`test_two_workers_cannot_claim_different_sources_concurrently` to pin
`max_concurrent=1` (or rename and split into max=1 vs max=2 cases).

### GREEN / refactor

Minimum production change to satisfy focused tests, then simplify while green.

## Verification

- Focused RED/GREEN commands recorded in progress log.
- `./scripts/verify_all.sh` must pass.
- Eval gate: n/a for sync concurrency (no retrieval/answer quality change);
  still record if full suite runs deterministic evals.
- Functional smoke: `sync_all` / `get_sync_status` / durable worker path via
  fake/temp E2E only; live sync blocked/gated.

## Risks / Rollback

| Risk | Mitigation |
| --- | --- |
| Chroma write races | Keep indexer mutation lock; default N=2 |
| SQLite claim races | `BEGIN IMMEDIATE` + COUNT gate in claim |
| Tombstone from partial parallel sync | unchanged incomplete-snapshot rules |
| Operator surprise | document env + default; N=1 restores old behavior |

Rollback: set `CONTEXTWIKI_SYNC_WORKER_MAX_CONCURRENT=1` or revert the branch.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Clean main ff; feature branch created | `feature/parallel-sync-worker-concurrency` @ `9d68643` |
| Plan | completed | This document | `docs/plan/2026-07-31-parallel-sync-worker-concurrency.md` |
| TDD RED | completed | Worker [RED tests parallel sync](84e48fb8-5cf9-4c26-a5e1-2686f4f88e7a) added unit/integration/E2E; focused suite exit 1, 27 failed on missing kwargs/helpers | TypeError max_concurrent_*; AssertionError missing _max_concurrent_jobs / _default_max_concurrent_jobs |
| TDD GREEN | completed | Worker [GREEN implement](dd15e452-6086-4b74-87d5-06ade5d1d336) implemented claim/worker concurrency; integrator set MetadataStore default N=1 (worker raises to env default 2) after claim-blocker regression | Focused parallel suite 27 passed; claim/global subset 70 passed |
| Docs | completed | Worker [Docs parallel sync](e8035c2f-3c77-46ae-8c64-dde982b246f9) updated architecture, ADR 0010, README | docs-only |
| Refactor | completed | No further structural refactor beyond default-N split; focused tests stay green | 70 passed claim/worker subset |
| verify_all | completed | Full suite green outside sandbox | `./scripts/verify_all.sh` exit 0; 1269 tests; retrieval 14/14, document sort 2/2, answer 9/9; functional E2E 52 passed; coverage 88% |
| Eval | recorded via verify_all | No retrieval/answer change; full-suite deterministic evals already PASS | retrieval 14/14, document_sort 2/2, answer 9/9 |
| Functional smoke | completed | Fake/temp durable + MCP sync surfaces | See smoke matrix below; 51 passed |
| Review pass 1 | actionable | Correctness/security/reliability found cancel-RUNNING leak, store/worker N mismatch, unvalidated assignment, Chroma multi-PID honesty | Reviewers 74a756fd, 904a18af, 4c4bb84b |
| Review remediation RED | completed | Added cancel-finalize, store-align, setter validation tests | exit 1; 5 failed, 2 passed |
| Review remediation GREEN | completed | Cancel finalize + property setter + SyncWorker aligns store + drain siblings | focused 5 passed; broader 31/36 passed |
| Docs remediation | completed | Chroma in-process lock honesty; Purpose restored | architecture/ADR/README |
| Review passes 2–6 | remediated | Cancel drain, chroma shield+join, CancelledError precedence, docs honesty | multiple verify_all greens |
| Review pass 7 | completed/clean | All three lenses CLEAN | Reviewers 3e89d084, dd136f66, c252a918 |
| PR delivery | completed | Opened main-base PR | https://github.com/eunaverse/MCPContentSearch/pull/93 |

## Functional Smoke Matrix

| Feature | Caller surface | Data mode | Expected | Command | Result |
| --- | --- | --- | --- | --- | --- |
| Bounded parallel durable sync | deterministic E2E | fake connectors + temp SQLite | two sources overlap RUNNING then succeed | `uv run --locked pytest -q tests/e2e/test_durable_sync_worker_flow.py` | passed |
| SyncWorker pool + stop | indexing integration | fake/temp | concurrent run + cancel-all-in-flight | `tests/indexing/test_sync_worker.py` | passed |
| MCP sync_all / sync_source / get_sync_status contracts | public MCP contracts + contextwiki E2E | fake/temp | enqueue/status shapes unchanged | contracts `-k sync` + contextwiki sync selectors | passed |
| Live Notion/Tistory/GitHub sync | MCP live | live user data | n/a | — | blocked/gated (needs explicit approval) |
