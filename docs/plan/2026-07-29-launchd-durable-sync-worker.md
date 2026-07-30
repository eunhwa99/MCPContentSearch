# Launchd Durable Sync Worker

## User Request

Implement the previously proposed option 3 for local macOS operation:

- Keep the FastMCP stdio server started with
  `uv run --locked python main.py`.
- Move configured-source sync execution into a separate long-lived worker.
- Run that worker as a macOS LaunchAgent so stopping the MCP server with
  `Ctrl+C` does not stop an already accepted sync.
- Cover every retained source (`Notion`, `Tistory`, `GitHub`, and `Obsidian`)
  through one common durable worker rather than creating a Notion-only worker.
- Write a complete implementation plan covering all required code, tests,
  documentation, operations, and rollout safety.

## Desired Behavior

The durable runtime should have two independently managed processes:

```text
Claude/Codex
  -> FastMCP stdio process
  -> enqueue or reuse a SQLite sync job
  -> return the accepted job

macOS LaunchAgent
  -> long-lived generic sync worker
  -> atomically claim one queued job
  -> select the configured source connector
  -> fetch, index, and finalize the exact claimed job
```

Stopping the FastMCP process must not terminate a sync owned by the LaunchAgent
worker. Stopping the worker is different: a graceful worker stop must finalize
the in-flight job as failed, while an abrupt worker death must be recovered by
the existing owner/heartbeat rules on a later worker start or status read.

SQLite remains the authoritative lifecycle store. Chroma remains the retrieval
accelerator and vector destination. The LaunchAgent is a process supervisor,
not a second persistence or scheduling system.

## Branch Preflight Result

- Original worktree:
  `/Users/eunhwa/IdeaProjects/MCPContentSearch`
- Original branch: `feature/wait-for-sync-all`
- Original state: dirty with active changes in architecture, README, MCP tools,
  ingestion, contract tests, E2E tests, and its own plan document.
- Safety action: preserved the original worktree without switching, pulling,
  deleting branches, or editing its files.
- Freshness: fetched `origin/main`; it remained at
  `38cddedf7de4ee85d7b9e2d5e145df44b7b399c8`.
- Isolated worktree:
  `/Users/eunhwa/IdeaProjects/MCPContentSearch-launchd-sync-worker`
- Fresh branch: `feature/launchd-durable-sync-worker`, created from
  `origin/main`.
- This plan intentionally does not copy uncommitted `wait-for-sync-all`
  changes. That branch must later rebase onto the durable-worker contract and
  treat queued/running jobs plus observation timeouts truthfully.

## Scope

### Common Durable Job Lifecycle

- Add an atomic SQLite enqueue operation that:
  - returns an existing active `queued` or `running` job for the same source;
  - otherwise creates exactly one `queued` job;
  - keeps source-level status truthful while work is waiting for a worker;
  - never creates an unowned fake `running` job.
- Add an atomic worker claim operation that:
  - selects the oldest eligible queued job;
  - transitions that exact job from `queued` to `running`;
  - records the worker owner id, process id, and heartbeat;
  - cannot be claimed by two workers.
- Preserve queued jobs when no worker is available. A queued job is pending,
  not stale running work and not a terminal failure.
- Preserve the existing running-job owner, heartbeat, document-claim, guarded
  commit, success/failure finalization, and tombstone safety invariants.
- Default to one in-flight job per worker process. Cross-source parallelism is a
  future measured optimization because Chroma writes and connector state are
  shared.

### MCP Dispatch Contract

- Route public `sync_source` and per-source `sync_all` launch decisions through
  durable enqueue/reuse behavior instead of `asyncio.create_task` owned by the
  FastMCP process.
- Preserve current tool names, parameters, error redaction, public-source
  filtering, and aggregate acceptance vocabulary.
- Allow a newly accepted job to be returned as `queued`. `started` continues to
  mean the durable job was newly accepted, not that connector I/O has already
  begun.
- Return an existing queued or running job without creating duplicates.
- Keep direct blocking ingestion execution available for worker internals and
  deterministic tests; do not let MCP request cancellation own worker task
  cancellation.
- `get_sync_status` must truthfully return queued, running, succeeded, or failed
  latest-job state. It must not reconcile a valid queued source to failed merely
  because no running owner exists.

### Generic Sync Worker

- Add a long-lived generic worker entrypoint that uses the same runtime config,
  source registry, chunker, indexer, MetadataStore, and connectors as the MCP
  application.
- Avoid duplicating composition logic or importing a running FastMCP server from
  the worker entrypoint. Extract the smallest shared composition boundary
  needed by both processes.
- The worker loop must:
  - poll SQLite at a bounded, configurable interval;
  - claim and run one job at a time;
  - sleep efficiently when no job exists;
  - handle `SIGINT` and `SIGTERM`;
  - finish or fail the claimed job consistently on graceful cancellation;
  - log job/source lifecycle without raw credentials, document contents,
    Notion page titles, or local content paths.
- Use the same generic worker for Notion, Tistory, GitHub, and Obsidian. Source
  differences remain inside existing connectors.

### macOS LaunchAgent

- Add a version-controlled LaunchAgent template plus safe helper scripts for:
  - rendering/installing the user-specific plist;
  - bootstrap/restart;
  - status inspection;
  - bootout/uninstall.
- The installer must resolve and write absolute paths for:
  - the repository working directory;
  - the selected `uv` executable;
  - worker stdout/stderr logs under the existing local application directory.
- The plist must not contain secret values. Runtime configuration continues to
  load from the repository-local `.env` through the existing explicit path
  loader.
- Installation must be idempotent and use a stable LaunchAgent label.
- Add a no-side-effect render or dry-run mode so tests can verify the plist in a
  temporary directory without calling `launchctl`.
- Actual `launchctl bootstrap`, LaunchAgent execution, real local SQLite/Chroma
  access, and live configured-source sync remain an explicit approval-gated
  operational step.

### Documentation and Architecture

- Update README setup and troubleshooting with:
  - MCP-only versus durable-worker commands;
  - install, status, restart, and uninstall workflow;
  - what happens when MCP or worker is stopped;
  - queued versus running status interpretation;
  - log locations and safe diagnostic commands.
- Update `.agents/docs/architecture.md` to describe the two-process runtime,
  queue/claim lifecycle, all-source worker boundary, shutdown recovery, and
  the `wait_for_sync_all` observer relationship now present on `main`.
- Add or update an ADR if the implementation establishes durable process
  ownership as a maintained contract rather than an optional script detail.

## Non-Goals

- Do not optimize Notion fetch concurrency, recursive block traversal, unchanged
  page detection, or API request volume in this work item.
- Do not add Redis, Celery, RabbitMQ, a network scheduler, or a second database.
- Do not add automatic retry of a partially executed job after abrupt worker
  death. The safe first version marks orphaned running work failed; the caller
  may enqueue a fresh idempotent sync.
- Do not run more than one sync concurrently in a worker by default.
- Do not change document identity, chunk ids, content hashes, retrieval,
  ranking, citation behavior, or answer quality.
- Do not inspect, reset, migrate, or delete the user's current SQLite or Chroma
  data.
- Do not load the LaunchAgent or run a live Notion/Tistory/GitHub/Obsidian sync
  without a separate explicit approval immediately before that operation.
- Do not merge or overwrite the active `feature/wait-for-sync-all` worktree.

## Acceptance Criteria

1. Calling public `sync_source(source_id)` with a configured fake source creates
   one queued SQLite job and returns promptly without creating an in-process
   background task.
2. Repeating `sync_source` before claim returns the same queued job.
3. Repeating it while the worker owns the running job returns the same running
   job.
4. A separate worker service instance sharing only the temporary SQLite/Chroma
   paths can claim and complete the exact job after the MCP-facing service
   instance is discarded or cancelled.
5. Cancelling or closing the MCP call does not cancel the worker-owned sync.
6. Two worker instances racing to claim one queued job produce exactly one
   winner.
7. A queued job remains queued and visible while no worker is running.
8. A graceful worker stop marks its in-flight job failed with a redacted,
   actionable lifecycle message; it does not tombstone a partial snapshot.
9. An abruptly orphaned running job is recovered without stealing a live job
   owned by another process.
10. Notion, Tistory, GitHub, and Obsidian all dispatch through the same worker
    loop in fake/temp coverage.
11. Existing successful-sync cleanup and failed/incomplete-sync tombstone safety
    remain unchanged.
12. The LaunchAgent plist renders with absolute executable/working/log paths,
    contains no secret values, passes `plutil -lint`, and can be tested without
    calling `launchctl`.
13. Install/status/uninstall documentation matches the scripts and clearly
    states that stopping the worker stops sync execution.
14. Existing search, grouped document browsing, fetch, citation helper, and
    retained source E2E behavior remain green.
15. No live provider, user vault, user SQLite, or user Chroma validation is
    reported as passed unless separately approved and actually executed.

## Ordered Step Breakdown

### 1. `queued-job-storage`

Read:

- `core/models.py`
- `storage/metadata_store.py`
- `tests/storage/test_metadata_store.py`
- existing sync lifecycle ADRs

Implement atomic enqueue/reuse and claim operations behind `MetadataStore`.
Update queued-aware latest-job/source reconciliation and active-job resolution.
Preserve operation-scoped SQLite connections and `BEGIN IMMEDIATE` claim
serialization. Use additive behavior with the existing schema where possible;
do not require a destructive user-data migration.

Executable acceptance:

```bash
uv run --locked pytest -q tests/storage/test_metadata_store.py
```

### 2. `worker-execution-boundary`

Read:

- `main.py`
- `api/tools.py`
- `indexing/ingestion_service.py`
- `fetching/connectors.py`
- `indexing/indexer.py`

Extract minimal shared runtime composition and add a worker execution path for a
previously claimed job. Route MCP launch methods to durable enqueue rather than
request-process tasks. Preserve direct deterministic execution for internal
tests without giving MCP ownership of the worker task.

Executable acceptance:

```bash
uv run --locked pytest -q \
  tests/indexing/test_ingestion_service.py \
  tests/api/test_tools_contract.py
```

### 3. `launchd-service`

Add the worker CLI/entrypoint, LaunchAgent template, and safe management
scripts. Keep the service generic across all retained sources and keep secret
values out of plist/log output. Test rendering in temporary directories.

Executable acceptance:

```bash
python -m compileall api core environments fetching indexing search storage main.py
uv run --locked pytest -q tests/scripts tests/test_app_composition.py
```

### 4. `durable-worker-e2e`

Add a retained fake/temp workflow that creates an MCP-facing service and a
separate worker-facing service over the same temporary metadata path. Prove
enqueue, MCP lifetime independence, exact-job claim, terminal completion, and
all-source dispatch without live credentials or user data.

Executable acceptance:

```bash
uv run --locked pytest -q \
  tests/contracts/test_public_mcp_contracts.py \
  tests/e2e/test_contextwiki_flow.py \
  tests/e2e/test_phase_b_connectors_flow.py \
  tests/e2e/test_obsidian_connector_flow.py
```

### 5. `docs-and-operations`

Update README, architecture, ADR/indexes, and this plan with exact implemented
commands, contract vocabulary, safety boundaries, and verification evidence.
Document the follow-up coordination required for
`feature/wait-for-sync-all`.

### 6. `verification-review-delivery`

Run focused verification, retained functional E2E, full verification, functional
smoke, fresh reviewer passes, remediation loops, final integration, and
PR delivery according to the repository harness.

## Worker Orchestration Plan

This is not an atomic change. The main agent remains the orchestrator and owns
integration, plan updates, verification, review routing, commits, push, and PR.

### Runtime and persistence worker

- Owns: `storage/metadata_store.py`, sync lifecycle portions of
  `indexing/ingestion_service.py`, minimal shared runtime composition, worker
  loop/entrypoint, and focused storage/ingestion tests if needed to prove its
  production boundary.
- Acceptance: atomic enqueue/claim, exact-job execution, graceful shutdown,
  active-owner safety, no user-data access.
- Must not edit LaunchAgent scripts or user-facing docs unless handed off
  sequentially.

### LaunchAgent and operations worker

- Owns: LaunchAgent template, install/status/uninstall scripts, script-focused
  tests, and the operational README section.
- Acceptance: idempotent render/install design, dry-run support, absolute
  paths, `plutil -lint`, no secrets, no actual `launchctl` mutation.
- Must target the worker entrypoint contract recorded in this plan and adapt to
  production changes without reverting them.

### Contract and E2E test worker

- Owns: public MCP contract tests and retained fake/temp E2E coverage.
- Acceptance: queued/running vocabulary, duplicate prevention, MCP lifetime
  independence, all-source dispatch, no live services or user data.
- Must not weaken existing assertions or edit production code.

Workers share the fresh feature branch but have disjoint primary ownership.
Shared-file follow-ups use sequential handoff rather than overlapping edits.
Workers must not commit, push, open PRs, inspect secrets, inspect or mutate user
SQLite/Chroma, or revert other worker/user changes.

## Files Likely to Change

Production/runtime:

- `main.py`
- `api/tools.py`
- `core/models.py` only if public queued metadata needs a compatible additive
  field
- `indexing/ingestion_service.py`
- `indexing/sync_worker.py` or an equivalent indexing-owned worker module
- a minimal shared composition module if needed
- `storage/metadata_store.py`

macOS operations:

- `Dockerfile`
- `deploy/launchd/...plist.template` or equivalent
- `scripts/install_sync_worker_launch_agent.sh`
- `scripts/status_sync_worker_launch_agent.sh`
- `scripts/uninstall_sync_worker_launch_agent.sh`
- worker entrypoint script/module

Tests:

- `tests/storage/test_metadata_store.py`
- `tests/indexing/test_ingestion_service.py`
- `tests/api/test_tools_contract.py`
- `tests/contracts/test_public_mcp_contracts.py`
- `tests/e2e/test_contextwiki_flow.py`
- `tests/e2e/test_durable_sync_worker_flow.py`
- `tests/e2e/test_phase_b_connectors_flow.py`
- `tests/e2e/test_obsidian_connector_flow.py`
- `tests/test_app_composition.py`
- `tests/indexing/test_sync_worker_logging.py`
- `tests/scripts/test_verification_architecture.py`
- a new script/LaunchAgent test module if local structure warrants it

Verification integration:

- `scripts/verify_functional_e2e.sh`
- `scripts/verify_all.sh`
- `scripts/demo_public_flow.py`
- `tests/scripts/test_demo_public_flow.py`

Docs:

- `README.md`
- `.agents/docs/architecture.md`
- `.agents/docs/adr/README.md`
- a new or updated sync-worker ADR
- `docs/contextwiki-core-understanding.md` if maintained explanations require
  alignment
- this plan

The final changed-file set may be narrower. Any expansion beyond these
boundaries must be recorded here before editing.

## Test and Verification Plan

### Focused checks

```bash
python -m compileall api core environments fetching indexing search storage main.py

uv run --locked pytest -q \
  tests/storage/test_metadata_store.py \
  tests/indexing/test_ingestion_service.py \
  tests/api/test_tools_contract.py \
  tests/contracts/test_public_mcp_contracts.py \
  tests/test_app_composition.py
```

### Retained MCP/source E2E

```bash
uv run --locked pytest -q \
  tests/e2e/test_contextwiki_flow.py \
  tests/e2e/test_durable_sync_worker_flow.py \
  tests/e2e/test_phase_b_connectors_flow.py \
  tests/e2e/test_obsidian_connector_flow.py
```

### LaunchAgent artifact checks

```bash
plutil -lint <rendered-temp-plist>
git diff --check
```

Script tests must render into a temporary directory and must not bootstrap,
restart, or unload a real LaunchAgent.

### Repository gates

```bash
./scripts/verify_functional_e2e.sh
./scripts/verify_all.sh
```

Retrieval/answer eval behavior is not intentionally changed. Existing evals
inside `verify_all.sh` remain regression coverage; no new quality metric or
claim is required.

## Functional Smoke Matrix

| Feature | Caller surface | Data mode | Expected visible result | Action / command | Result | Evidence / blocker and substitute |
| --- | --- | --- | --- | --- | --- | --- |
| `sync_source` enqueue | Real FastMCP tool | Fake connector + temp SQLite | Immediate `queued` job acceptance | `./scripts/verify_functional_e2e.sh` | passed | Durable and retained functional E2E gate; timestamped run evidence and current test count are recorded in the progress log below |
| Duplicate enqueue | Real FastMCP tool called twice | Fake connector + temp SQLite | Same queued/running job id is reused | `./scripts/verify_functional_e2e.sh` | passed | `tests/e2e/test_durable_sync_worker_flow.py` |
| Worker claim | `SyncWorker` + separate MetadataStore instance | Fake connector + shared temp SQLite | One atomic claimant changes the exact job to `running` | `./scripts/verify_functional_e2e.sh` | passed | Durable worker E2E plus focused storage race tests |
| MCP lifetime independence | Separate MCP and worker service instances | Fake connector + shared temp SQLite | Cancelling the MCP-side owner does not cancel worker execution | `./scripts/verify_functional_e2e.sh` | passed | Exact accepted job IDs reached `succeeded` in durable worker E2E |
| `sync_all` | Real FastMCP tool | Four fake retained sources + temp SQLite | Every eligible source is queued or reused truthfully | `./scripts/verify_functional_e2e.sh` | passed | Notion, Tistory, GitHub, and Obsidian all completed through one generic worker |
| Exact-job `get_sync_status` completion | Real FastMCP tool + separate worker service | Fake connectors + shared temp SQLite | Caller observes the exact queued/running jobs through terminal completion without owning them | `./scripts/verify_functional_e2e.sh` | passed | Durable exact-job E2Es; exact job IDs preserved after `wait_for_sync_all` removal |
| Exact-job without worker / later worker completion | Real FastMCP tool, then separate worker | Fake Notion + shared temp SQLite | No-worker observation leaves job queued; a later worker completes the exact job | `./scripts/verify_functional_e2e.sh` | passed | Rewritten TDD cases in `tests/e2e/test_durable_sync_worker_flow.py` |
| `list_sources` / `get_sync_status` | Real FastMCP tools | Fake/temp metadata | Source plus queued/running/terminal status remain observable | `./scripts/verify_functional_e2e.sh` | passed | Retained source flows and durable status assertions |
| Notion dispatch | Generic worker | Fake Notion connector + temp SQLite | Job reaches its terminal state through common worker | `./scripts/verify_functional_e2e.sh` | passed | `tests/e2e/test_durable_sync_worker_flow.py` |
| Tistory dispatch | Generic worker | Fake Tistory connector + temp SQLite | Job reaches its terminal state through common worker | `./scripts/verify_functional_e2e.sh` | passed | Durable and retained phase-B E2E |
| GitHub dispatch | Generic worker | Fake GitHub connector + temp SQLite | Job reaches its terminal state through common worker | `./scripts/verify_functional_e2e.sh` | passed | Durable and retained phase-B E2E |
| Obsidian dispatch | Generic worker + real temp-vault path | Temporary vault and stores | Job reaches terminal state without touching the user vault | `./scripts/verify_functional_e2e.sh` | passed | Durable plus retained Obsidian E2E |
| Failed snapshot safety | Ingestion service with failure fixtures | Fake connector + temp stores | Failed/incomplete snapshot does not tombstone active documents | `./scripts/verify_functional_e2e.sh` | passed | Retained GitHub and Obsidian lifecycle E2E |
| `search_context` / `search_documents` / `list_documents` / `fetch_context` | Real FastMCP tools | Temporary indexed fixture | Existing active-gated search, date browsing, and citation fetch remain green | `./scripts/verify_functional_e2e.sh` | passed | Retained ContextWiki/source E2E is part of the functional gate; latest post-main-integration `./scripts/verify_all.sh` -> functional E2E `49 passed in 3.16s` |
| LaunchAgent render and helper UX | Installer/runner plus fake `launchctl` and helper dry-runs | Temporary plist/path/log only | Valid secret-free plist, unloaded-service repair, changed-install rollback, continuously bounded startup diagnostics, and missing-plist uninstall | focused pytest; `bash -n ...`; render-only; `plutil -lint`; every helper `--dry-run` | passed | Failure-first fake/temp regression cases, including live stderr size observation; temporary plist linted `OK`; no real `launchctl` call |
| LaunchAgent lifecycle | `launchctl` bootstrap/status/bootout | Real macOS user service | Worker stays alive independently of MCP | Not run | blocked/gated | Needs explicit approval because bootstrap starts a persistent process. Nearest substitute: render/lint plus helper dry-runs passed. |
| Live configured sync | Installed worker + real source/user stores | User SQLite/Chroma + provider APIs | A selected source finishes after MCP stops | Not run | blocked/gated | Needs explicit provider and user-data authorization. Nearest substitute: separate-service fake/temp lifetime E2E passed for all four source types. |

## Local Data and External Service Impact

- Production code will read and write the same default SQLite and Chroma paths
  only when the user actually runs or installs the worker.
- Automated verification must inject temporary SQLite/Chroma paths or fakes.
- The desired queue behavior should use the existing sync job table and queued
  enum if possible, avoiding a user-data migration.
- Any schema addition discovered during implementation must be additive,
  backward-compatible, and recorded in this plan before editing.
- Real Notion/Tistory/GitHub calls, real Obsidian vault reads, embeddings, and
  user-data indexing are not authorized by plan creation alone.
- LaunchAgent installation is an external-state change and can automatically
  start the worker; it therefore remains gated until the user explicitly
  approves installation after reviewing the implementation.

## Architecture Constraints

- SQLite remains the sync lifecycle authority.
- Chroma is not used as a queue or lifecycle lock.
- `api/tools.py` remains a thin MCP boundary; queue/claim execution belongs in
  storage and indexing/service modules.
- Source connectors remain responsible only for source-specific fetching.
- One generic worker dispatches every retained source.
- Tombstoning occurs only after a complete successful snapshot from a
  cleanup-capable source.
- Worker and MCP processes use short-lived SQLite connections; neither holds a
  transaction while performing network or indexing work.
- Claim is atomic, but work occurs outside the claim transaction.
- No secrets or raw user content enter the plist, plan, tests, or INFO logs.
- The worker must not import an executing FastMCP server or start a second MCP
  transport.
- The MCP server must not silently fall back to in-process long-running tasks
  when the durable worker is unavailable.
- `wait_for_sync_all` observes the exact jobs returned by durable enqueue.
  Waiting, timing out, or cancelling the MCP request must never own or cancel
  worker execution.

## Risks and Mitigations

### Queued/running contract change

Risk: clients or tests may assume a newly started job is immediately `running`.

Mitigation: keep tool names and aggregate acceptance vocabulary, document the
additive queued state, update strongest FastMCP contract tests, and ensure
status polling handles both queued and running.

### Queued job mistaken for orphaned running work

Risk: existing status reconciliation only resolves running owners and may mark
a queued source failed.

Mitigation: add explicit queued-aware active-job selection and focused storage
tests before changing MCP dispatch.

### Duplicate worker claims

Risk: two LaunchAgents or manual workers may execute the same source.

Mitigation: `BEGIN IMMEDIATE` atomic claim, exact status predicate
`queued -> running`, stable owner id, and race coverage.

### Worker crash after partial vector writes

Risk: abrupt exit may leave uncommitted vectors or an orphan running job.

Mitigation: retain guarded metadata commits, best-effort vector cleanup,
document claims, orphan failure recovery, and no automatic resume in v1.

### LaunchAgent environment mismatch

Risk: `launchd` has a minimal `PATH` and does not inherit interactive shell
variables.

Mitigation: render absolute `uv` and working-directory paths, use the existing
repository-local dotenv loader, keep secrets out of the plist, and capture
pre-logging startup stderr in a bounded local diagnostic.

### Accidental live data mutation during verification

Risk: starting the worker with default config touches user SQLite/Chroma or
external providers.

Mitigation: temp/fake-only tests, no real `launchctl` calls, explicit blocked
smoke rows, and a separate approval before installation/live smoke.

### Coordination with `wait-for-sync-all`

Risk: the `wait_for_sync_all` feature originally assumed MCP-owned background
tasks.

Mitigation: PR #83 is now part of the branch base. The integrated implementation
uses explicit durable enqueue and observes exact queued/running/terminal jobs
through SQLite without owning worker execution.

## Rollback

- Before installation: revert the feature PR. No user LaunchAgent or user-data
  change exists.
- After plist rendering but before bootstrap: delete the rendered plist with
  the uninstall helper; no process was started.
- After LaunchAgent installation: boot out the stable label, remove the plist,
  and run the legacy MCP-only command. Any queued jobs remain visible and can be
  failed or retried through a separately approved metadata recovery procedure.
- During an explicit changed-config install, a replacement bootstrap failure
  automatically restores the previous plist and its prior loaded state.
- Code rollback must preserve existing SQLite rows and must not delete queued,
  running, document, chunk, claim, or tombstone records.
- Do not perform automated database cleanup as part of rollback.

## PR Strategy

Use one `main`-base PR because queue persistence, worker execution, MCP launch
semantics, LaunchAgent packaging, and contract/E2E coverage form one tightly
coupled behavior change. Keep commits focused if useful, but do not publish a
LaunchAgent that targets an unavailable worker entrypoint.

The branch is rebased onto `origin/main` after PR #83. This PR therefore
delivers the durable execution layer and its completed `wait_for_sync_all`
integration together, without a stacked base.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Harness discovery | completed | Read repository harness, architecture, Git workflow, and planning requirements. | `.agents/docs/harness-engineering.md`; `.agents/skills/harness-engineering/SKILL.md`; `.agents/skills/harness-plan/SKILL.md`; `.agents/docs/architecture.md`; `.agents/docs/github-workflow.md` |
| Branch preflight | completed | Preserved dirty `feature/wait-for-sync-all`; fetched `origin/main`; created isolated fresh feature worktree. | `git status --short --branch`; `git branch -vv`; `git worktree list --porcelain`; `git fetch origin main`; `git worktree add -b feature/launchd-durable-sync-worker ... origin/main` |
| Architecture trace | completed | Confirmed in-process `asyncio.create_task`, existing queued enum, owner heartbeat/recovery, operation-scoped SQLite, and repo-local dotenv loading. | `indexing/ingestion_service.py`; `storage/metadata_store.py`; `core/models.py`; `environments/runtime_env.py`; `main.py` |
| Planning | completed | Defined common durable queue/claim worker, LaunchAgent operations, all-source scope, tests, smoke, risks, and rollback. | This document |
| Worker orchestration | completed | Dispatched three bounded workers with disjoint production/runtime, LaunchAgent/docs, and contract/E2E test ownership; API lifecycle ownership was assigned to the runtime worker because it shares the durable dispatch contract. | Workers: `runtime_persistence`, `launchd_ops`, `contract_e2e` |
| Implementation | completed | Added the atomic SQLite queue/claim lifecycle, generic all-source worker, durable MCP enqueue contract, shared runtime composition, LaunchAgent packaging, operational docs, and fake/temp contract coverage. | `runtime_persistence`: 234 combined tests; 62 focused tests; race loops, Ruff, scoped mypy, compile, and CLI help passed. `launchd_ops`: 7 tests, shell syntax, worker help, rendered-plist lint, and diff check passed. `contract_e2e`: 103 tests, Ruff, and diff check passed. |
| Original implementation TDD chronology audit | audited; strict initial RED not substantiated | The three original worker sessions were re-read after pass 14. The core feature was not implemented in strict test-first order, so this plan does not claim an original pre-production RED. Runtime production edits preceded its storage/worker tests; its first recorded test command was `python -m compileall app_runtime.py api core environments fetching indexing search storage main.py && uv run --locked pytest -q tests/storage/test_metadata_store.py tests/indexing/test_sync_worker.py` -> exit `0`, `61 passed in 1.54s`. LaunchAgent production and tests arrived in the same patch; its first run `uv run --locked pytest -q tests/scripts/test_sync_worker_launch_agent.py` -> exit `0`, `3 passed in 1.27s`. Contract fake failures (`3 failed, 9 passed` and later `1 failed, 2 passed`) modeled source lifecycle incorrectly and are not missing-behavior RED evidence; the first real durable owner-cancellation E2E run was already `1 passed in 1.04s`. | Audited sessions: `runtime_persistence` `019fadf6-f64a-74c0-8d46-54699eaa321b`; `launchd_ops` original worker session; `contract_e2e` `019fadf7-363b-7330-8b34-76c517399e16`. Subsequent review fixes below do have explicit failure-first selectors. No absent RED command, exit, or signature was invented |
| Main-agent integration | completed | Added the new durable-worker E2E to the retained functional gate and the new shared composition module to the static/coverage gate. These test-list changes are atomic integration ownership, so no additional implementation worker was needed. | `scripts/verify_functional_e2e.sh`; `scripts/verify_all.sh` |
| Baseline verification | completed | Fresh `origin/main` focused storage/ingestion/API/contract/composition suite passed before worker edits. | `uv run --locked pytest -q tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py tests/test_app_composition.py` -> `199 passed in 10.89s` |
| Focused verification | completed | Re-ran compile plus storage, ingestion, worker, API, public-contract, composition, LaunchAgent-script, and retained source tests from the integrated worktree. | `python -m compileall -q ...`; combined `uv run --locked pytest -q ...` -> `238 passed in 7.91s`; all four helper scripts passed `bash -n`; `git diff --check` passed |
| Retained E2E | completed | Verified fake/temp MCP enqueue, exact job handoff, MCP lifetime independence, all-source worker dispatch, search/citation retention, and source connector flows. | Included in the combined 238-test run; rendered temporary plist passed `plutil -lint`; all helper `--dry-run` paths passed without `launchctl` |
| Functional smoke | completed | Exercised the full changed MCP/source/storage/search inventory through real FastMCP or the nearest safe script surface with fake/temp data. LaunchAgent lifecycle and live configured sync remain explicitly gated. | Latest `./scripts/verify_functional_e2e.sh` -> `35 passed in 4.81s`; matrix above |
| Full verification attempt 1 | failed | Static compile, Ruff, and scoped mypy passed, then Bandit rejected the dynamically assembled source-scope SQL in `claim_next_sync_job`. Route the fix to the runtime/persistence owner and rerun affected storage/worker smoke plus the full gate. | `./scripts/verify_all.sh`; Bandit `B608` at `storage/metadata_store.py:448` |
| Full verification attempt 2 | failed | Static/security/public-contract gates passed; broad suite reached 711 passes. Eight demo-flow tests timed out because the local demo enqueued durable work but did not run its temp-store worker. Expand the integration boundary to the demo and route the fix to the contract/E2E owner. | `./scripts/verify_all.sh` -> `8 failed, 711 passed`; every failure in `tests/scripts/test_demo_public_flow.py` with latest job `queued` |
| Full verification attempt 3 | completed | Static compile, Ruff, mypy, Bandit, public MCP contracts, broad non-live suite with coverage, deterministic retrieval/answer evals, and functional E2E all passed after the storage and demo fixes. | `./scripts/verify_all.sh` -> `13 passed` public contracts; `719 passed in 42.69s`; coverage `87.16%`; retrieval `13/13`; answer `9/9`; functional E2E `28 passed in 3.77s` |
| Latest-main integration | completed | Rebased onto PR #83 and integrated `wait_for_sync_all` as an explicit durable enqueue observer. The wait path, timeout, and request cancellation never own worker execution. | Base `origin/main` `12043e6`; focused integrated suite `170 passed`, then expanded TDD-focused suite `110 passed` |
| Latest-main focused integration attempt 1 | failed | Rebase completed and production wait dispatch now routes through durable enqueue when `durable_dispatch` is enabled. Two newly merged wait E2Es still assumed public `sync_source` starts an in-process task, so no worker claimed their queued job. Route those tests and durable wait coverage to the contract/E2E owner. | Focused integration suite -> `2 failed, 167 passed`; both failures timed out waiting for connector entry after MCP enqueue |
| Latest-main integration finding | completed | Changed the merged wait implementation from indirect `sync_all` dispatch to explicit `enqueue_all`, then moved all five wait E2Es to separate MCP/worker service instances sharing temp SQLite. | Runtime focused suite `73 passed`; wait E2E `5 passed`; combined ContextWiki/durable E2E `14 passed` before TDD expansion |
| TDD expansion | completed | Added explicit no-worker queued-timeout and cancelled-wait queue-persistence/later-exact-worker-completion cases. Both passed on first execution, so no further production gap existed. | New tests `2 passed`; related wait/durable `9 passed`; full related E2E `16 passed`; Ruff/diff check passed |
| Latest-main full verification | completed | Re-ran all static, security, contract, broad non-live, quality-eval, and functional gates on the rebased code with the new TDD cases. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `735 passed in 43.90s`; coverage `87.06%`; retrieval `13/13`; answer `9/9`; functional E2E `35 passed in 4.67s` |
| Middle review pass 1 | findings | Exactly five fresh read-only reviewers inspected concurrency, contracts, ops/security, tests/docs, and architecture. Findings: global single-flight was not enforced across two workers; custom uninstall/template-root paths were inconsistent; orphan wording was MCP-specific; Docker omitted `app_runtime.py` and restart policy; identical reinstall restarted in-flight work; persistent logs lacked rotation; config restart docs were incomplete; plan still described the already-merged wait branch as future work. | Reviewers `p1_r1_concurrency`, `p1_r2_contracts`, `p1_r3_security_ops`, `p1_r4_tests_docs`, `p1_r5_architecture` |
| Middle review fixes | completed | Enforced global single-flight and neutral orphan recovery; made identical LaunchAgent installs no-op and changed installs explicit; aligned custom uninstall and target-repo templates; added Docker runtime/restart coverage; bounded worker logs; documented dual-process config reload; corrected stale plan text. | Runtime focused `135 passed`; three race tests repeated 10x; ops/docs focused `15 passed`; Ruff, mypy, Bandit, compile, bash syntax, temp plist lint, and diff checks passed in worker lanes |
| Post-review verification | completed | Re-ran focused and full integration gates after all pass-1 findings were fixed. | Focused `164 passed`; `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `743 passed in 44.48s`; coverage `87.01%`; retrieval `13/13`; answer `9/9`; functional E2E `35 passed in 4.81s` |
| Refactor | completed | Reviewed the integrated diff for meaningful duplication, misplaced responsibility, and local-pattern violations. No safe additional refactor reduced complexity after the review fixes, so no refactor-only edits were made. | Current diff and surrounding module boundaries inspected |
| Integration | completed | Confirmed latest-main ancestry, clean diff formatting, startup/tool-registration coverage, full fake/temp verification, and the refreshed functional smoke matrix. | `git rev-list --left-right --count HEAD...origin/main` -> `1 0`; full gate and matrix evidence above |
| Final review policy | updated | User requested three reviewers from this point forward, overriding the repository default of five for subsequent passes. | User instruction after final pass 2 began |
| Final review pass 2 | findings | One completed reviewer found that PID reuse can make a dead execution owner appear live and, with global single-flight, block all queued jobs. The second reviewer was interrupted when the turn was stopped and does not count. | `p2_r1_concurrency`; `p2_r2_contracts` interrupted |
| Final review pass 2 fix | completed | Added additive owner process-start identity storage, macOS/Linux process-instance lookup, legacy fallback, and recovery of a live-but-reused PID before the next global claim. | Focused suite `166 passed`; real macOS identity lookup returned `darwin`; compile and diff checks passed |
| Post-pass-2 full verification | completed | Re-ran every static, security, contract, broad non-live, deterministic quality, and functional E2E gate after the PID-reuse fix. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `745 passed in 47.28s`; coverage `86.78%`; retrieval `13/13`; answer `9/9`; functional E2E `35 passed in 4.86s` |
| Final review pass 3 | findings | Exactly three fresh reviewers inspected runtime/concurrency, MCP contracts/TDD, and operations. MCP contracts were clean. Runtime review found cross-container PID-namespace identity mismatch could falsely recover a live owner. Operations review found identical-but-unloaded installs remain unloaded, startup stderr is discarded, and changed-install bootstrap failure lacks rollback. | Reviewers `p3_r1_runtime`, `p3_r2_contracts`, `p3_r3_ops` |
| Final review pass 3 fixes | completed | Added failure-first regressions; scoped Linux process-birth comparison to boot/PID namespace with heartbeat fallback across unknown scopes; corrected the documented MCP enqueue path; repaired identical-but-unloaded installs; preserved bounded pre-logging startup diagnostics; made changed-install replacement transactional with previous plist/service rollback; and retained graceful signal forwarding through the launch wrapper. | Runtime focused `140 passed`, liveness/race `7 passed` x10; operations regressions initially `3 failed`, then passed; combined LaunchAgent/logging `11 passed`; integrated focused suite `173 passed`; shell syntax/ShellCheck, Ruff, mypy, Bandit, compile, temp plist lint, architecture tests, and diff check passed |
| Post-pass-3 full verification | completed | Re-ran every static, security, public-contract, broad non-live, deterministic quality, and functional E2E gate after integrating all pass-3 fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `752 passed in 47.99s`; coverage `86.72%`; retrieval `13/13`; answer `9/9`; functional E2E `35 passed in 5.10s` |
| Final review pass 4 | findings | Exactly three fresh reviewers inspected runtime/concurrency, MCP contracts/TDD, and operations. MCP contracts were clean. Runtime review found cross-namespace `ESRCH` was still treated as definitive owner death before scope comparison. Operations review found the startup diagnostic was bounded only before/after a run and uninstall could not stop a loaded service after its plist disappeared. | Reviewers `p4_r1_runtime`, `p4_r2_contracts`, `p4_r3_ops` |
| Final review pass 4 fixes | completed | Added failure-first same/cross-namespace `ESRCH`, live-process diagnostic, and missing-plist uninstall regressions; made dead-PID evidence definitive only in the same Linux boot/PID namespace; replaced start/end-only trimming with bounded streaming capture; and changed uninstall bootout to the stable service target. | Runtime regressions initially `2 failed`, then focused `145 passed`, liveness/race `12 passed` x10; operations regressions initially `2 failed`, then focused `13 passed`; integrated focused suite `180 passed`; Ruff, mypy, Bandit, compile, shell/ShellCheck, plist lint, architecture, and diff checks passed |
| Post-pass-4 full verification | completed | Re-ran every static, security, public-contract, broad non-live, deterministic quality, and functional E2E gate after integrating all pass-4 fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `759 passed in 53.30s`; coverage `86.73%`; retrieval `13/13`; answer `9/9`; functional E2E `35 passed in 5.01s` |
| Final review pass 5 | findings | Exactly three fresh reviewers inspected runtime/concurrency, MCP contracts/TDD, and operations. MCP contracts were clean. Runtime review found Linux legacy/empty owner identity still made cross-namespace `ESRCH` definitive and one startup test used the same owner id, bypassing its intended branch. Operations review found reinstall with loaded service/missing plist still booted out by missing path, and INFO-level indexing logs can persist document/chunk identifiers containing local note paths. | Reviewers `p5_r1_runtime`, `p5_r2_contracts`, `p5_r3_ops` |
| Final review pass 5 fixes | completed | Added failure-first legacy/empty-identity Linux scope, loaded-service/missing-plist reinstall, and sensitive-path worker-log regressions; treated unparseable Linux owner scope as heartbeat-fallback evidence; corrected the startup test observer identity; changed replacement bootout to the stable service target; and lowered per-document indexing identifiers to DEBUG. | Runtime regressions initially `6 failed`, then focused `151 passed`, liveness/race `18 passed` x10; operations regressions initially `2 failed`, then focused `23 passed`; integrated focused suite `196 passed`; Ruff, mypy, Bandit, compile, bash/ShellCheck, plist lint, architecture, and diff checks passed |
| Post-pass-5 full verification | completed | Re-ran every static, security, public-contract, broad non-live, deterministic quality, and functional E2E gate after integrating all pass-5 fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `767 passed in 51.83s`; coverage `86.73%`; retrieval `13/13`; answer `9/9`; functional E2E `35 passed in 4.90s` |
| Final review pass 6 | findings | Exactly three fresh reviewers inspected the full liveness truth table, MCP contracts/TDD, and the LaunchAgent/logging/Docker state matrix. Runtime review found recognized-but-malformed Linux/Darwin process identities could still create a false definitive mismatch. Operations/contracts review found loaded-service/missing-plist install still interrupted work without explicit restart; root INFO logging could retain dependency URLs and Notion page/block IDs; and Docker log limits lacked an explicit compatible driver. | Reviewers `p6_r1_runtime`, `p6_r2_contracts`, `p6_r3_ops` |
| Final review pass 6 fixes | completed | Added failure-first strict identity, install-state, HTTP/Notion privacy, and Docker docs-contract tests; made recognized-but-malformed Linux/Darwin identities fall back to heartbeat; required explicit restart before touching a loaded service whose plist is missing; documented the no-prior-plist rollback limit; added a project-lifecycle/dependency-suppression/privacy filter; removed page/block identifiers from persistent levels; and pinned Docker's local log driver. | Runtime regressions initially `12 failed`, then focused `172 passed`, liveness/race `39 passed` x10; operations/privacy regressions initially `6 failed`, then focused `144 passed`; integrated focused suite `259 passed`; bash/ShellCheck, Ruff, official mypy, Bandit, compile, temp plist lint, and diff check passed. Direct file-target mypy surfaced the pre-existing out-of-scope `fetching/notion.py:338` list annotation; no unrelated edit made |
| Post-pass-6 full verification | completed | Re-ran every static, security, public-contract, broad non-live, deterministic quality, and functional E2E gate after integrating all pass-6 fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `792 passed in 50.55s`; coverage `86.83%`; retrieval `13/13`; answer `9/9`; functional E2E `35 passed in 4.93s` |
| Final review pass 7 | findings | Exactly three fresh reviewers inspected runtime liveness, MCP/contracts/privacy, and operations. Operations were clean. Runtime review found zero and Unicode-digit process-start values still parsed as valid identities. Contracts/privacy review found multiword `Authorization: Bearer/Basic ...` and cookie-like values could leave the trailing credential in persistent logs. | Reviewers `p7_r1_runtime`, `p7_r2_contracts`, `p7_r3_ops` |
| Final review pass 7 fixes | completed | Added failure-first zero/Unicode/leading-zero identity plus direct-redactor and rotating-log cases; required canonical ASCII positive Linux ticks/Darwin seconds and bounded canonical ASCII microseconds; and applied the centralized `safe_error_message` sanitizer without truncation to every retained record before URL/path redaction. | Runtime regressions initially `16 failed` across zero/Unicode/leading-zero cases, then focused `182 passed`, liveness/race `49 passed` x10, plus `38 passed` canonical hardening; privacy regressions initially `2 failed`, then focused `7 passed`; integrated focused suite `277 passed`; Ruff, official mypy, Bandit, compile, and diff checks passed |
| Post-pass-7 full verification | completed | Re-ran every static, security, public-contract, broad non-live, deterministic quality, and functional E2E gate after integrating all pass-7 fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `810 passed in 50.69s`; coverage `86.83%`; retrieval `13/13`; answer `9/9`; functional E2E `35 passed in 5.03s` |
| Final review pass 8 | findings | Exactly three fresh reviewers inspected runtime/liveness, MCP/contracts/privacy, and operations. Operations were otherwise clean and runtime found no new liveness defect, but all reviewers confirmed the branch was 17 commits behind a new `origin/main` GitHub owner-wide sync that changes shared runtime surfaces. Privacy review also found bare Notion token signatures, whitespace-containing paths, and formatter-appended stack/exception text could bypass retained-log redaction. | Reviewers `p8_r1_runtime`, `p8_r2_contracts`, `p8_r3_ops`; stale base `c01cc3d`, new `origin/main` `c5227ff` |
| Latest-main integration 2 | completed | Rebased the isolated feature branch onto `c5227ff`, preserved the durable-worker and owner-wide GitHub discovery/cleanup paths, and retained both owner-specific and durable-worker documentation through the README conflict. | `git rev-list --left-right --count HEAD...origin/main` -> `1 0`; no conflict markers; initial latest-main focused suite `463 passed` |
| Latest-main TDD fixes | completed | Added failure-first retained-log regressions for bare Notion token signatures, whitespace-bearing Unix/Windows paths, `stack_info`, and preformatted `exc_text`; redact full context before and after the centralized sanitizer. Updated two newly merged owner-wide E2Es that still assumed MCP-owned execution to run a separate worker over shared temporary SQLite, then added owner discovery/confirmed-empty/historical-private cleanup coverage across separate MCP and worker registries. | Privacy RED `2 failed`, GREEN `2 passed`, logging file `7 passed`; owner-wide RED `2 failed, 6 passed`, GREEN focused `8 passed`; integrated changed-flow suite `29 passed`; expanded latest-main focused suite `511 passed in 17.36s`; Ruff and `git diff --check` passed |
| Latest-main full verification 2 | completed | Re-ran the complete static, security, public-contract, broad non-live, quality-eval, and functional E2E gates after the latest-main and TDD fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `855 passed in 50.97s`; coverage `87.12%`; retrieval `13/13`; answer `9/9`; functional E2E `39 passed in 5.28s` |
| Final review pass 9 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and operations. Findings: a macOS observer could treat a missing or malformed cross-platform owner identity as definitive death; actual `exc_info` could be truncated before full redaction; one oversized record could exceed the rotating-file bound; install could chmod a pre-existing custom log directory; and deterministic E2E did not cover visibility between independent Chroma clients. | Reviewers `p9_r1_runtime`, `p9_r2_contracts`, `p9_r3_ops` |
| Final review pass 9 fixes | completed | Added failure-first cross-platform liveness, real `exc_info`, oversized-record, custom-directory-mode, and independent-Chroma-client regressions. Unknown or cross-platform owner identity now uses heartbeat fallback; exception context is scrubbed before truncation; records and rotation checks are UTF-8 byte-bounded; existing custom log directories are validated without chmod; and two independent temp Chroma clients prove worker writes become visible to the already-running MCP search runtime. | Runtime RED `9 failed, 4 passed`, GREEN `13 passed`, related `133 passed`; logging RED exposed path tail and >1024-byte file, GREEN `9 passed`; ops RED `2 failed`, GREEN LaunchAgent `19 passed` and architecture `26 passed`; integrated affected suite `187 passed in 11.47s`; Ruff, compile, shell/ShellCheck, and diff checks passed |
| Post-pass-9 full verification | completed | Re-ran every static, security, public-contract, broad non-live, deterministic quality, and functional E2E gate after pass-9 fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `875 passed in 54.23s`; coverage `87.21%`; retrieval `13/13`; answer `9/9`; functional E2E `40 passed in 5.50s` |
| Final review pass 10 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and operations. Findings: concurrent two-process legacy schema upgrade could race on `ALTER TABLE`; comma/semicolon-bearing paths could leak suffixes; the bounded startup diagnostic rewrote its entire 1 MiB tail for each post-limit chunk; custom log-directory validation could miss noncanonical symlink spellings and did not verify current-user ownership/writability; and two functional-smoke matrix cells still labeled older 35-test evidence as latest. | Reviewers `p10_r1_runtime`, `p10_r2_contracts`, `p10_r3_ops` |
| Final review pass 10 fixes | completed | Added failure-first concurrent migration, delimiter/final-component path durable logging, amortized diagnostic retention, symlink-component/ownership/writability, and current smoke-evidence regressions. Legacy migration check-and-alter now runs under `BEGIN IMMEDIATE`; central-sanitizer residual path tails are removed before persistence; startup diagnostics compact to half-size only at an amortized threshold; and existing custom directories require a canonical non-symlink path, current-user ownership, mode `0700`, and write/search access without mutation. | Migration RED duplicate-column failure, GREEN race x10 and storage `128 passed`; privacy RED `2 failed, 1 passed`, durable E2E RED, no-extension RED `2 failed`, then owned `19 passed`; ops RED `6 failed`, GREEN `6 passed`, related `33 passed`; integrated affected suite `180 passed in 19.25s`; Ruff, mypy, compile, bash/ShellCheck, and diff checks passed |
| Post-pass-10 full verification | completed | Re-ran every static, security, public-contract, broad non-live, deterministic quality, and functional E2E gate after pass-10 fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `887 passed in 59.77s`; coverage `87.24%`; retrieval `13/13`; answer `9/9`; functional E2E `41 passed in 5.27s` |
| Final review pass 11 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and operations. Findings: disabled-source enqueue committed a claimable queued job before terminal rejection; strong worker-log redaction did not protect persisted job/source errors or MCP status payloads; and a signal arriving before the launch wrapper assigned the child PID could be lost. | Reviewers `p11_r1_runtime`, `p11_r2_contracts`, `p11_r3_ops` |
| Final review pass 11 TDD RED audit | completed; exact privacy evidence recovered from original worker session | Before production edits, disabled-source unit/integration/E2E RED ran with `uv run --locked pytest -q tests/storage/test_metadata_store.py::test_disabled_source_enqueue_and_worker_claim_race_never_claims_new_job tests/indexing/test_ingestion_service.py::test_durable_disabled_source_is_failed_atomically_without_completion_handoff tests/e2e/test_durable_sync_worker_flow.py::test_disabled_mcp_enqueue_cannot_be_claimed_by_stale_enabled_worker` -> exit `1`, `3 failed`: a queued disabled job was claimable, completion required a second transaction, and a stale enabled worker fetched/succeeded. Pre-child-signal RED ran with `uv run --locked pytest -q tests/scripts/test_sync_worker_launch_agent.py -k 'replays_signal_received_before_child_pid_assignment'` -> exit `1`, `2 failed/timeouts`: pre-assignment `TERM`/`INT` was swallowed. The original privacy worker session confirms the final pre-production RED command `uv run --locked pytest -q tests/indexing/test_background_tasks.py::test_safe_error_message_redacts_notion_tokens_and_complete_paths_without_losing_fields tests/indexing/test_ingestion_service.py::test_ingestion_persists_strongly_sanitized_failure_with_structured_fields tests/api/test_tools_contract.py::test_status_payloads_redact_public_error_paths_and_whitespace_secrets tests/e2e/test_durable_sync_worker_flow.py::test_mcp_enqueued_job_failure_does_not_persist_delimiter_paths_in_worker_log` -> exit `1`, `4 failed in 1.16s`; unit exposed bare `ntn_`/`secret_`, integration exposed persisted/completed tokens plus `<redacted> notes.md`, API exposed `notes.md` while preserving `job_id`/`source_id`, and E2E exposed completed/persisted `notes.md`. The same selector's initial less-strict run exited `1` with `2 failed, 2 passed in 1.47s` before assertions were strengthened. | Required pre-production ordering, exact selectors, non-zero exits, and missing-behavior signatures are now explicit; recovered privacy evidence is attributed to the original worker session |
| Final review pass 11 fixes | completed | Added failure-first atomic disabled-source enqueue/reuse, shared persistence/API privacy, and pre-child signal regressions. A single SQLite transaction now reuses active work or creates a terminal disabled result without exposing queued work; one shared sanitizer protects persistence, MCP payloads, and logs; and the wrapper latches startup TERM/INT then forwards it to the worker process group once assigned. | Disabled RED `3 failed`, GREEN `5 passed`, race `40/40`; privacy RED `4 failed`, GREEN owned `157 passed` plus max-length coverage; signal RED `2 failed/timeouts`, GREEN `2 passed` x10 and LaunchAgent `28 passed`; integrated affected suite `344 passed in 22.77s`; Ruff, mypy, compile, bash/ShellCheck, and diff checks passed |
| Post-pass-11 full verification | completed | Re-ran every static, security, public-contract, broad non-live, deterministic quality, and functional E2E gate after pass-11 fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `897 passed in 60.31s`; coverage `87.25%`; retrieval `13/13`; answer `9/9`; functional E2E `42 passed in 5.43s` |
| Final review pass 12 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and operations. Runtime/concurrency was clean. Findings: direct MetadataStore lifecycle writes could bypass the shared sanitizer; colon-delimited `path:`/`file:` forms bypassed path redaction; the pass-11 RED row lacked the repository-required command/signature detail; and concurrent LaunchAgent mutations were not serialized. | Reviewers `p12_r1_runtime`, `p12_r2_contracts`, `p12_r3_ops` |
| Final review pass 12 privacy/storage TDD RED | completed before production edits | Added unit `test_safe_error_message_redacts_colon_labeled_paths_without_losing_fields`, logging integration `test_worker_log_redactor_removes_colon_labeled_paths_and_keeps_fields`, direct temporary-SQLite integration tests for source upsert/register, job status/failure/success-cleanup, disabled enqueue/recovery, MCP contract `test_get_sync_status_keeps_direct_storage_failure_sanitized_at_rest`, and deterministic worker E2E `test_mcp_enqueued_job_failure_does_not_persist_delimiter_paths_in_worker_log`. Ran `uv run --locked pytest -q tests/indexing/test_background_tasks.py::test_safe_error_message_redacts_colon_labeled_paths_without_losing_fields tests/indexing/test_sync_worker_logging.py::test_worker_log_redactor_removes_colon_labeled_paths_and_keeps_fields tests/storage/test_metadata_store.py::test_metadata_store_sanitizes_direct_source_lifecycle_writes tests/storage/test_metadata_store.py::test_metadata_store_sanitizes_direct_job_status_failure_and_cleanup_writes tests/storage/test_metadata_store.py::test_metadata_store_sanitizes_direct_disabled_enqueue_and_recovery_writes tests/api/test_tools_contract.py::test_get_sync_status_keeps_direct_storage_failure_sanitized_at_rest tests/e2e/test_durable_sync_worker_flow.py::test_mcp_enqueued_job_failure_does_not_persist_delimiter_paths_in_worker_log` -> exit `1`, `7 failed`. | Expected signatures: raw `ntn_`/`secret_` remained in direct source/job model and SQLite rows; `path:/Users/...` and `file:C:\...` remained in sanitizer/log/status/E2E values. This demonstrated missing shared-storage enforcement and colon-label coverage rather than a fixture/environment failure |
| Final review pass 12 privacy/storage GREEN | completed | Moved the common sanitizer to `core/error_sanitizer.py`, retained a compatibility export from `indexing/background_tasks.py`, added colon-labeled path support, and sanitized direct source/job error, status, recovery, failure, and cleanup values inside the storage write boundary while preserving public payload shapes, structured `job_id`/`source_id` fields, and the 300-character contract. | Exact RED selector rerun -> `7 passed`; affected unit/integration/API/E2E suite `uv run --locked pytest -q tests/indexing/test_background_tasks.py tests/indexing/test_sync_worker_logging.py tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py tests/api/test_tools_contract.py tests/e2e/test_durable_sync_worker_flow.py` -> `295 passed in 5.97s`; public contracts/composition `21 passed`; Ruff clean; repository-configured mypy clean; `git diff --check` clean. Direct file-target mypy still reports the three pre-existing `api/tools.py` typing findings that the repository mypy configuration excludes |
| Final review pass 12 fixes | completed | Moved lifecycle sanitization to a shared core boundary and enforced it within direct SQLite writes, covered colon path forms and exact RED evidence, and serialized all mutating per-label LaunchAgent operations with deterministic concurrent fake-launchctl tests. LaunchAgent serialization RED ran before production edits: `uv run --locked pytest -q tests/scripts/test_sync_worker_launch_agent.py -k 'operation_lock or concurrent_first_installs or management_mutations'` covered the lock unit boundary, concurrent-install integration, and install-vs-restart/uninstall deterministic E2E; exit `1`, `4 failed`, with the expected signatures that the stale lock remained and the second operation reached fake `launchctl` while the first operation was blocked. A follow-up safety RED `test_launch_agent_operation_lock_never_recovers_through_a_symlink` failed because recovery followed a symlink; crash-window RED `-k 'recovers_old_unpublished_owner'` failed twice because ownerless/partial locks timed out forever; and live-owner RED `test_launch_agent_operation_lock_never_reclaims_old_published_live_owner` failed because an old published live owner was reclaimed. The final macOS-portable lock covers install, restart, and uninstall from state inspection through commit/rollback, recovers only definitive dead/PID-reused or grace-expired unpublished owners, and rejects unsafe state. | Privacy/storage exact RED `7 failed`, GREEN `7 passed`, affected `295 passed`; LaunchAgent RED `4 + 1 + 2 + 1` failures, GREEN file `37 passed`, concurrency/recovery subsets x10; integrated affected suite `359 passed in 25.91s`; Ruff, mypy, compile, bash/ShellCheck, Docker packaging contract, and diff checks passed |
| Post-pass-12 full verification | completed | Re-ran every static, security, public-contract, broad non-live, deterministic quality, and functional E2E gate after pass-12 fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `912 passed in 65.84s`; coverage `87.33%`; retrieval `13/13`; answer `9/9`; functional E2E `42 passed in 5.38s` |
| Final review pass 13 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and operations. Runtime/concurrency was clean. Findings: unsafe `auth_ref` values could persist at rest; free-form job `phase` could bypass storage/MCP privacy; pass-11 privacy RED still lacked its exact historical selector; and a crashed stale `.reclaim` marker could permanently block LaunchAgent mutations. | Reviewers `p13_r1_runtime`, `p13_r2_contracts`, `p13_r3_ops` |
| Final review pass 13 auth-ref/phase TDD RED | completed before production edits | Inventoried the production phase vocabulary as `""`, `starting`, `discovering_pages`, `fetching_page_content`, `indexing_documents`, `completed`, and `failed`; added unit public-boundary, temporary-SQLite integration, MCP contract, and deterministic durable E2E coverage. Ran `uv run --locked pytest -q tests/api/test_sync_lifecycle_safety.py tests/storage/test_metadata_store.py::test_metadata_store_never_persists_noncanonical_auth_refs tests/storage/test_metadata_store.py::test_metadata_store_preserves_canonical_auth_refs tests/storage/test_metadata_store.py::test_metadata_store_never_persists_noncanonical_job_phase tests/storage/test_metadata_store.py::test_metadata_store_preserves_canonical_job_phases tests/api/test_tools_contract.py::test_status_payload_drops_legacy_noncanonical_phase_and_auth_ref tests/e2e/test_durable_sync_worker_flow.py::test_mcp_registration_and_running_status_never_expose_or_persist_unsafe_lifecycle_metadata` -> exit `1`, `16 failed, 15 passed in 1.27s`. | Expected missing behavior: direct upsert/register persisted raw `ntn_`/`secret_` and malformed env refs; free-form phase persisted path/token content; unit/API returned invalid running phase; E2E persisted/exposed unsafe source/job lifecycle metadata. Canonical auth refs and phase values remained passing |
| Final review pass 13 reclaim-marker TDD RED | completed before production edits | Added operation-lock unit/integration/deterministic fake-launchctl E2E cases for grace-expired ownerless and partially published `.reclaim` directories, fresh unpublished and old live published recovery owners, symlink/regular/foreign-owned marker rejection without target mutation, and two simultaneous helpers recovering one stale marker without overlapping service operations. Ran `uv run --locked pytest -q tests/scripts/test_sync_worker_launch_agent.py -k 'old_reclaim_marker or preserves_fresh_reclaim_marker or preserves_live_reclaim_marker or unsafe_reclaim_marker or concurrent_reclaim_marker_recovery'` before editing the lock helper. | Exit `1`: `6 failed, 2 passed, 37 deselected`. Expected missing-behavior signatures: old ownerless/partial markers and concurrent recoverers timed out forever; symlink/regular/foreign-owned markers were not rejected as unsafe. Fresh and live markers already remained protected, confirming the fixture distinguished safe active state from the crash gap. |
| Final review pass 13 auth-ref/phase GREEN | completed | Added shared canonical lifecycle validators; source upsert/register now retains only `env:UPPER_CASE_NAME` auth references; job updates persist only the inventoried finite phase vocabulary; row hydration and MCP formatting independently normalize or omit noncanonical phase values; public auth formatting keeps canonical references and redacts bypassed unsafe legacy values. Updated the architecture and ADR boundaries without changing normal MCP payload shapes. | Exact RED selector rerun -> `31 passed in 1.00s`; expanded ingestion/connectors/composition/storage/API/public-contract/durable-E2E suite -> `354 passed in 6.20s`; focused Ruff clean; repository mypy clean (`9 source files`); `git diff --check` clean |
| Final review pass 13 reclaim-marker GREEN | completed | The recovery marker now publishes PID plus process-start identity before inspecting an existing lock. A dead published recovery owner is recoverable immediately; ownerless/partial state is recoverable only after the configured grace; fresh/live state is preserved. Symlink, non-directory, foreign-owned marker directories, and unsafe owner entries are rejected before mutation. Concurrent stale-marker observers converge on the same atomic marker/lock path, and the deterministic fake `launchctl` test holds the first service operation to prove the second cannot enter concurrently. Architecture and ADR recovery rules were updated. | Exact RED selector rerun -> `8 passed`; direct helper unit coverage added; full LaunchAgent script file -> `47 passed in 22.74s`; stale/active/unsafe/concurrent recovery subset completed x10 without failure; `bash -n`, ShellCheck, focused Ruff, architecture verification `7 passed`, and scoped `git diff --check` passed |
| Final review pass 13 fixes | completed | Added failure-first auth-ref/phase boundary and stale/live/unsafe reclaim-marker cases, restored the recovered pass-11 selector evidence, and applied bounded storage/API/lock fixes. | Auth-ref/phase RED `16 failed, 15 passed`, GREEN `31 passed`, expanded `354 passed`; reclaim RED `6 failed, 2 passed`, GREEN `8 passed`, full LaunchAgent `47 passed`, recovery/concurrency subset x10; integrated storage/API/contracts/durable/LaunchAgent/architecture suite `310 passed in 27.89s`; Ruff, bash syntax, and `git diff --check` passed |
| Post-pass-13 full verification | completed | Re-ran every static, security, public-contract, broad non-live, deterministic quality, and functional E2E gate after integrating both pass-13 fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `953 passed in 67.56s`; coverage `87.37%`; retrieval `13/13`; answer `9/9`; functional E2E `43 passed in 5.39s` |
| Final review pass 14 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and operations. Runtime/concurrency was clean. Findings: semicolon-delimited cookie fragments and unlabelled UNC paths bypassed the shared sanitizer; startup diagnostic capture persisted raw worker output; a killed helper could expose overlapping service mutation while its `launchctl` child remained alive; and the original core feature RED chronology was not yet auditable in this plan. | Reviewers `p14_r1_runtime`, `p14_r2_contracts`, `p14_r3_ops`; runtime focused `76 passed`; ops focused `67 passed` |
| Final review pass 14 privacy/startup TDD RED | completed before production edits | Added unit, temporary-SQLite, worker-log, MCP, durable-E2E, and startup-wrapper cases for semicolon-delimited Cookie/Set-Cookie suffixes, unlabelled UNC/extended Windows paths, and raw startup diagnostics. Ran `uv run --locked pytest -q tests/indexing/test_background_tasks.py::test_safe_error_message_redacts_semicolon_cookie_headers_and_unc_paths tests/storage/test_metadata_store.py::test_metadata_store_sanitizes_cookie_headers_and_unc_paths_at_rest tests/indexing/test_sync_worker_logging.py::test_worker_log_redactor_removes_semicolon_cookie_headers_and_unc_paths tests/api/test_tools_contract.py::test_status_payloads_redact_semicolon_cookie_headers_and_unc_paths tests/e2e/test_durable_sync_worker_flow.py::test_mcp_enqueued_job_failure_does_not_persist_delimiter_paths_in_worker_log tests/scripts/test_sync_worker_startup_privacy.py::test_launch_agent_runner_sanitizes_startup_stderr_before_persisting`. | Exit `1`, `6 failed in 2.05s`: cookie suffixes, UNC/extended paths, and raw Authorization/Cookie/token/path startup text remained across their intended boundaries |
| Final review pass 14 lock-child TDD RED | completed before production edits | Added deterministic fake-`launchctl` E2E that blocks the child mutation, kills only its helper parent, and starts a second helper. Ran `uv run --locked pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_operation_lock_survives_sigkill_while_launchctl_child_is_live`. | Exit `1`, `1 failed in 0.90s`: the second `kill` call entered the fake service mutation while the orphan child was still blocked |
| Final review pass 14 fixes | completed | The shared sanitizer now removes complete semicolon cookie values plus UNC/extended paths, and the launch wrapper sanitizes startup output through a bounded fail-closed line stream. The operation lock atomically publishes a `launchctl` child PID/process-start identity before opening its execution gate, protects a live child after parent death, and reclaims only after child death or PID reuse. The original implementation chronology is documented as production-first rather than mislabeled TDD. | Privacy exact selector GREEN `6 passed in 1.47s`; broader privacy `29 passed`; runner suite `48 passed`; lock truth table/E2E `4 passed`, concurrency subset x10, full LaunchAgent `51 passed in 23.92s`; integrated affected suite `336 passed in 28.58s`; Ruff, mypy, bash syntax, ShellCheck, and diff checks passed |
| Post-pass-14 full verification | completed | Re-ran static, security, public-contract, broad non-live, deterministic quality, and functional E2E gates after pass-14 integration. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `962 passed in 69.16s`; coverage `87.05%`; retrieval `13/13`; answer `9/9`; functional E2E `43 passed in 5.21s` |
| Final review pass 15 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and operations. Findings: coalesced/folded cookie values could escape; read-only store initialization accumulated unreferenced owner rows; the startup sanitizer had an undeclared system-Python dependency and opaque failure path; signal interruption during writer drain could orphan cleanup; and the production-first delivery branch still did not satisfy strict TDD chronology. | Reviewers `p15_r1_runtime`, `p15_r2_contracts`, `p15_r3_ops`; focused runtime `42 passed`; contracts `36 passed`; existing ops coverage passed but omitted the reported paths |
| Final review pass 15 cookie TDD RED | completed before production edits | Added shared text/stream, temporary-SQLite, MCP, worker-log, and durable-E2E coverage for comma-coalesced and RFC-style folded Cookie/Set-Cookie continuations while retaining explicit structured diagnostic fields. Ran `uv run --locked pytest -q tests/indexing/test_background_tasks.py::test_safe_error_message_redacts_coalesced_and_folded_cookie_pairs tests/indexing/test_background_tasks.py::test_error_stream_redacts_folded_cookie_continuations_across_lines tests/storage/test_metadata_store.py::test_metadata_store_sanitizes_cookie_headers_and_unc_paths_at_rest tests/api/test_tools_contract.py::test_status_payloads_redact_semicolon_cookie_headers_and_unc_paths tests/e2e/test_durable_sync_worker_flow.py::test_mcp_enqueued_job_failure_does_not_persist_delimiter_paths_in_worker_log`. | Exit `1`, `5 failed in 1.08s`: `theme=private`, `unknown_attribute=top-secret`, and folded `folded_cookie=delta` remained in intended boundaries |
| Final review pass 15 owner-row TDD RED | completed before production edits | Added temp-SQLite and durable-E2E coverage for repeated read-only store construction, real claim/heartbeat owner registration, safe unreferenced-owner pruning, and concurrent begin ownership. Ran `uv run --locked pytest -q tests/storage/test_metadata_store.py::test_repeated_read_only_store_initialization_does_not_register_sync_owners tests/storage/test_metadata_store.py::test_claim_and_heartbeat_register_owner_and_prune_unreferenced_owners tests/e2e/test_durable_sync_worker_flow.py::test_durable_worker_completes_exact_jobs_after_mcp_request_owner_is_cancelled`. | Exit `1`, `3 failed`: 25 read-only stores left 25 owner rows, heartbeat retained the requester plus 25 stale rows, and durable E2E retained an extra MCP owner |
| Final review pass 15 runner TDD RED | completed before production edits | Added uv-only minimal-PATH, sanitizer failure, and TERM/INT-during-writer-drain cases. Ran `uv run --locked pytest -q tests/scripts/test_sync_worker_startup_privacy.py::test_launch_agent_runner_uses_uv_managed_sanitizer_without_path_python tests/scripts/test_sync_worker_startup_privacy.py::test_launch_agent_runner_reports_safe_bounded_error_when_sanitizer_fails tests/scripts/test_sync_worker_launch_agent.py::test_launch_agent_runner_waits_for_writer_drain_after_signal`. | Exit `1`, `4 failed in 2.44s`: uv-only startup exited 70, sanitizer failure left no fixed diagnostic, and TERM/INT let the wrapper exit and delete its chunk before the live writer finished |
| Final review pass 15 fixes | completed | Cookie header parsing now fails closed across coalesced/folded unknown pairs and preserves only an explicit diagnostic allowlist. Schema reads no longer create owners; actual begin/claim/heartbeat paths register ownership and prune noncurrent owners unreferenced by running jobs. The startup sanitizer runs through the validated absolute uv/repository environment, records a fixed bounded failure message, and uses interrupt-aware writer cleanup. | Cookie exact GREEN `5 passed`, broader privacy `254 passed`; owner GREEN `6 passed`, race subset x10, storage/worker/durable `170 passed`; runner exact GREEN `4 passed in 2.26s`, full startup/LaunchAgent `56 passed in 26.34s`; integrated affected suite `339 passed in 29.84s`; Ruff, mypy, bash syntax, ShellCheck, and diff checks passed |
| Post-pass-15 full verification | completed | Re-ran static, security, public-contract, broad non-live, deterministic quality, and functional E2E gates after pass-15 integration. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `970 passed in 63.36s`; coverage `87.20%`; retrieval `13/13`; answer `9/9`; functional E2E `43 passed in 4.54s` |
| TDD delivery reconstruction | completed | Reconstructed the final delivery on fresh branch `feature/launchd-durable-sync-worker-tdd` from `origin/main` `c5227ff`. Commit `4eeb782` applies the final unit/integration/E2E and verification changes before any feature production files. RED 1 ran `uv run --locked pytest -q tests/storage/test_metadata_store.py::test_enqueue_sync_job_reuses_queued_and_running_jobs tests/storage/test_metadata_store.py::test_two_workers_racing_claim_one_queued_job_have_one_winner tests/storage/test_metadata_store.py::test_two_requesters_racing_enqueue_reuse_one_queued_job tests/storage/test_metadata_store.py::test_two_workers_cannot_claim_different_sources_concurrently` -> exit `1`, `4 failed`, all because `MetadataStore.enqueue_sync_job` did not exist. RED 2 ran `uv run --locked pytest -q tests/api/test_tools_contract.py::test_sync_source_returns_new_durable_job_as_queued tests/contracts/test_public_mcp_contracts.py::test_sync_source_contract_reuses_queued_job_and_polls_to_terminal_status` -> exit `1`, `2 failed`, because `sync_source` returned `error` instead of durable `queued`. RED 3 ran `uv run --locked pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_render_only_creates_valid_absolute_secret_free_plist tests/scripts/test_sync_worker_launch_agent.py::test_launch_agent_runner_forwards_sigterm_and_waits_for_worker` -> exit `1`, `2 failed`, because the install and runner scripts did not exist. RED 4 ran `uv run --locked pytest -q tests/e2e/test_durable_sync_worker_flow.py` -> exit `2`, collection error because `indexing.sync_worker` did not exist. Only after those missing-behavior failures was the final production tree applied. | GREEN ran `uv run --locked pytest -q tests/storage/test_metadata_store.py::test_enqueue_sync_job_reuses_queued_and_running_jobs tests/storage/test_metadata_store.py::test_two_workers_racing_claim_one_queued_job_have_one_winner tests/storage/test_metadata_store.py::test_two_requesters_racing_enqueue_reuse_one_queued_job tests/storage/test_metadata_store.py::test_two_workers_cannot_claim_different_sources_concurrently tests/api/test_tools_contract.py::test_sync_source_returns_new_durable_job_as_queued tests/contracts/test_public_mcp_contracts.py::test_sync_source_contract_reuses_queued_job_and_polls_to_terminal_status tests/scripts/test_sync_worker_launch_agent.py::test_render_only_creates_valid_absolute_secret_free_plist tests/scripts/test_sync_worker_launch_agent.py::test_launch_agent_runner_forwards_sigterm_and_waits_for_worker tests/e2e/test_durable_sync_worker_flow.py` -> `17 passed in 3.74s`. The original production-first history remains documented above, but it is not part of this delivery branch |
| Reconstructed-branch full verification | completed | Re-ran the full static, security, public-contract, broad non-live, deterministic quality, and functional E2E gate from the final tests-first delivery branch after production application. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `970 passed in 65.88s`; coverage `87.20%`; retrieval `13/13`; answer `9/9`; functional E2E `43 passed in 4.54s`; `git diff --check` clean |
| Final review pass 16 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and operations. Findings: cookie names colliding with diagnostic-field allowlist names could escape redaction; oversized cookie headers lost folded-continuation state; changed-config install did not guarantee rollback after catchable interruption or intermediate failure; and the reconstructed RED/GREEN plan row omitted exact commands. Other runtime, owner lifecycle, all-source, and startup/LaunchAgent focused scopes were green. | Reviewers `p16_r1_runtime`, `p16_r2_contracts`, `p16_r3_ops_retry`; runtime focused `170 passed`; contracts/privacy focused `37 passed` plus `30 passed`; operations focused `70 passed` |
| Final review pass 16 cookie-edge TDD RED | completed before production edits | Added text/stream, temporary-SQLite, MCP, startup-runner, worker-log, and durable-E2E cases for Cookie/Set-Cookie names colliding with diagnostic names plus oversized header folded continuations. Ran `uv run --locked pytest -q tests/indexing/test_background_tasks.py::test_safe_error_message_fails_closed_for_cookie_names_that_match_diagnostic_fields tests/indexing/test_background_tasks.py::test_error_stream_keeps_cookie_mode_after_an_oversized_header tests/storage/test_metadata_store.py::test_metadata_store_fails_closed_for_cookie_names_that_match_diagnostic_fields tests/api/test_tools_contract.py::test_status_payloads_fail_closed_for_cookie_names_that_match_diagnostic_fields tests/scripts/test_sync_worker_startup_privacy.py::test_launch_agent_runner_redacts_oversized_cookie_header_and_folded_continuation tests/e2e/test_durable_sync_worker_flow.py::test_mcp_enqueued_job_failure_does_not_persist_delimiter_paths_in_worker_log`. | Exit `1`, `6 failed`: allowlist-collision cookie values and oversized folded secrets remained in text, stream, storage, MCP, runner, or durable log boundaries. A broader RED then exposed seven stale same-line diagnostic expectations and one worker path-tail newline interaction; an unterminated oversized stream regression also initially failed |
| Final review pass 16 install-transaction TDD RED | completed before production edits | Added safe test-env filtering, explicit catchable-interrupt scope, TERM/INT during replacement bootstrap, failing bootstrap plus signal, and lock-held-through-child/rollback coverage. Ran `uv run --locked pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_safe_test_env_excludes_secret_shaped_parent_keys tests/scripts/test_sync_worker_launch_agent.py::test_installer_limits_transaction_guarantee_to_catchable_interrupts tests/scripts/test_sync_worker_launch_agent.py::test_changed_install_rolls_back_after_interrupt_during_replacement_bootstrap tests/scripts/test_sync_worker_launch_agent.py::test_changed_install_waits_for_failing_bootstrap_then_rolls_back_interrupt tests/scripts/test_sync_worker_launch_agent.py::test_interrupted_install_holds_lock_through_child_completion_and_rollback`. | Exit `1`, `5 failed, 1 passed`: SIGTERM exited `-15`, SIGINT exited `129`, the installer lacked a catchable-signal contract, and rollback/concurrent helpers completed before the blocked child was released. Only the sensitive-parent-env filter unit already passed |
| Final review pass 16 fixes | completed | Added the exact reconstructed RED/GREEN commands above. Cookie headers now remain fail-closed through colliding names, oversized lines, and folded continuations; diagnostic fields are preserved only after a clear non-folded line boundary; worker path-tail cleanup no longer consumes that boundary. Changed-config install now latches TERM/INT, waits for the tracked child, completes rollback, preserves snapshots until commit/rollback, and holds the operation lock throughout. SIGKILL remains explicitly outside the shell-only guarantee. LaunchAgent test subprocess environments remove sensitive-shaped parent/override keys. | Cookie focused GREEN `8 passed in 1.05s`, broader privacy `336 passed in 6.19s`; install exact GREEN `6 passed in 4.88s`, all `59` LaunchAgent cases passed, concurrency `4 passed`; integrated affected suite `329 passed in 36.82s`; Ruff, mypy, bash syntax, ShellCheck, and diff checks passed |
| Post-pass-16 full verification | completed | Re-ran static, security, public-contract, broad non-live, deterministic quality, and functional E2E gates after pass-16 fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `982 passed in 70.31s`; coverage `87.39%`; retrieval `13/13`; answer `9/9`; functional E2E `43 passed in 4.65s` |
| Final review pass 17 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and launchd operations. Runtime was clean. Both privacy/contracts and operations independently reproduced one P2 edge on reviewed HEAD `22463f7`: the oversized-line scanner retained only 16 trailing characters, so arbitrary whitespace splitting `Cookie` or `Set-Cookie` from `:` across chunks could lose folded-header state and persist the following folded secret. No additional launchd transaction, lock, signal, cleanup, documentation, or live-operation-gating issue was found. | Reviewers `p17_r1_runtime`, `p17_r2_contracts`, `p17_r3_ops`; runtime focused `257 passed`; operations/startup fake-temp scope `64 passed`, bash syntax and ShellCheck passed |
| Final review pass 17 cookie-split TDD RED | completed before production edit | Added parametrized stream coverage for `Cookie` and `Set-Cookie` plus a deterministic launchd-wrapper fake-`uv` startup test where more than the retained tail width of whitespace separates the header name from `:` across oversized read chunks and a folded line follows. Ran `uv run --locked pytest -q tests/indexing/test_background_tasks.py::test_error_stream_keeps_cookie_mode_when_oversized_header_separator_crosses_chunk tests/scripts/test_sync_worker_startup_privacy.py::test_launch_agent_runner_redacts_cookie_separator_split_across_oversized_chunks`. | Exit `1`, `3 failed in 0.60s`: each case wrote `<redacted oversized diagnostic>` but retained `job_id=folded-cookie-secret; phase=folded-phase-secret`, directly proving the fixed-tail parser gap |
| Final review pass 17 cookie-split GREEN | completed | Removed the insufficient 16-character cross-chunk scan and conservatively keeps folded-continuation mode after every oversized diagnostic. A subsequent clear non-folded line still resets the mode. The exact RED selector now passes, as do the broader privacy, storage, API, ingestion, durable E2E, and startup-wrapper scopes. | Exact selector `3 passed in 0.21s`; focused files `17 passed in 2.02s`; broader affected scope `339 passed in 7.21s`; Ruff and `git diff --check` passed; main-agent rerun of the exact selector `3 passed in 0.39s` |
| Post-pass-17 full verification | completed | Re-ran the complete static, security, public-contract, broad non-live, quality-eval, and functional E2E gates after integrating the cookie-split fix. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `985 passed in 68.01s`; coverage `87.38%`; retrieval `13/13`; answer `9/9`; functional E2E `43 passed in 4.54s` |
| Final review pass 18 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and launchd operations on the pass-17 tree. Runtime and launchd operations were clean. Contracts/privacy found one P2 malformed-input edge: a non-oversized line containing only `Cookie` or `Set-Cookie`, followed by an indented line containing `:` and values, did not enter folded-header mode even though whole-text sanitization failed closed. No additional source dispatch, API, storage, launchd, documentation, or live-gating finding remained. | Reviewers `p18_r1_runtime`, `p18_r2_contracts`, `p18_r3_ops`; runtime fake/temp `264 passed` plus exact sanitizer `3 passed`; contracts/privacy focused `28 passed`, public/durable `31 passed`, 288 synthesized oversized cases; operations/startup fake-temp `64 passed`, concurrency subset x5, bash syntax and ShellCheck passed |
| Final review pass 18 cookie-name-fold TDD RED | completed before production edit | Added LF and CRLF coverage for both `Cookie` and `Set-Cookie` name-only lines followed by an indented delimiter/value line, plus deterministic fake-`uv` startup-wrapper coverage. Ran `uv run --locked pytest -q tests/indexing/test_background_tasks.py::test_error_stream_fails_closed_when_cookie_name_precedes_folded_delimiter_and_value tests/scripts/test_sync_worker_startup_privacy.py::test_launch_agent_runner_redacts_name_only_cookie_header_and_folded_value`. | Exit `1`, `5 failed`: all four stream variants and the retained startup diagnostic exposed synthetic `cookie-source-secret` or `cookie-job-secret`, proving the missing pending-state transition |
| Final review pass 18 cookie-name-fold GREEN | completed | Added an exact name-only Cookie/Set-Cookie recognizer to the bounded stream state machine. The following indented delimiter/value line is sanitized fail-closed, while the next clear non-folded diagnostic resets state and retains allowed structured fields. | Exact selector `5 passed`; broader storage/API/log/durable/startup scope `271 passed in 5.19s`; post-refactor exact selector `5 passed in 0.39s`; Ruff and `git diff --check` passed |
| Post-pass-18 full verification | completed | Re-ran the complete static, security, public-contract, broad non-live, quality-eval, and functional E2E gates after integrating the cookie-name-fold fix. | Exact selector `5 passed in 0.40s`; `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `990 passed in 68.28s`; coverage `87.39%`; retrieval `13/13`; answer `9/9`; functional E2E `43 passed in 4.57s` |
| Final review pass 19 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and launchd operations on the pass-18 tree. Runtime was clean. Contracts/privacy found one P2 cross-boundary inconsistency: stream sanitization handled a name-only Cookie header followed by folded lines, but whole-text sanitization used by SQLite, MCP payloads, and worker logs retained diagnostic-shaped suffix fields when the folded continuation omitted a repeated delimiter. Operations found no launchd defect on its reviewed snapshot; the contracts finding was fixed concurrently, so a fresh stable-tree pass remains required. | Reviewers `p19_r1_runtime`, `p19_r2_contracts`, `p19_r3_ops`; runtime/integration `330 passed` plus `82 passed`; contracts/privacy focused `264 passed` before the missing case; operations/startup fake-temp `65 passed`, concurrency subset x5, bash syntax and ShellCheck passed |
| Final review pass 19 whole-text-fold TDD RED | completed before production edit | Added LF/CRLF and Cookie/Set-Cookie whole-text cases plus temporary SQLite, MCP status payload, worker-log, and deterministic durable E2E coverage for name-only headers followed by SP/HTAB continuation lines without a repeated delimiter. Ran `uv run --locked pytest -q tests/indexing/test_background_tasks.py::test_safe_error_message_redacts_name_only_cookie_header_folded_lines tests/storage/test_metadata_store.py::test_metadata_store_redacts_name_only_cookie_header_folded_lines_at_rest tests/api/test_tools_contract.py::test_status_payloads_redact_name_only_cookie_header_folded_lines tests/indexing/test_sync_worker_logging.py::test_worker_log_redactor_removes_name_only_cookie_header_folded_lines tests/e2e/test_durable_sync_worker_flow.py::test_mcp_enqueued_job_failure_does_not_persist_delimiter_paths_in_worker_log`. | Exit `1`, `8 failed`: four unit variants plus storage, API, log, and E2E retained synthetic `folded-cookie-job-secret` or `folded-name-cookie-job-secret`, proving the shared whole-text gap |
| Final review pass 19 whole-text-fold GREEN | completed | The shared whole-text sanitizer now recognizes an exact name-only Cookie/Set-Cookie line and redacts every immediately SP/HTAB-folded line while preserving LF/CRLF, indentation, and the first clear non-folded structured diagnostic. | Exact selector `8 passed in 0.83s`; broader affected scope `278 passed in 5.51s`; Ruff and `git diff --check` passed |
| Post-pass-19 full verification | completed | Re-ran the complete static, security, public-contract, broad non-live, quality-eval, and functional E2E gates after integrating the whole-text folded-header fix. | Exact selector `8 passed in 0.82s`; `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `997 passed in 70.29s`; coverage `87.40%`; retrieval `13/13`; answer `9/9`; functional E2E `43 passed in 4.60s` |
| Final review pass 20 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and launchd operations on the stable pass-19 tree. Contracts/privacy and operations were clean. Runtime found no code defect but reported one P3 plan-accuracy issue: two Functional Smoke Matrix cells still repeated pass-16's `4.65s` timing while claiming to be latest, although the newest progress row recorded `4.60s`. | Reviewers `p20_r1_runtime`, `p20_r2_contracts`, `p20_r3_ops`; runtime fake/temp `373 passed`; contracts/privacy `285 passed`, public/all-source `21 passed`, synthetic Cookie matrix 168 checks; operations/startup fake-temp `65 passed`, bash syntax and ShellCheck passed |
| Final review pass 20 docs fix | completed | Removed duplicated timing/latest claims from the two matrix cells. They now state that the functional gate covers 43 tests and route timestamped evidence to the append-only progress log, preventing the summary matrix from becoming stale after future reruns. This was an atomic main-agent plan-integration edit; no production/test file changed. | `rg` confirmed both matrix cells use the progress-log pointer and no stale `latest overall` or `Latest durable` wording remains; `git diff --check` passed |
| Final review pass 21 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and launchd operations after the pass-20 docs fix. Runtime was clean. Contracts/privacy found a P2 whole-text sanitizer edge where a name-only Cookie folded continuation ending with lone CR retained a diagnostic-shaped suffix. Operations found a P3 false-success path where TERM/INT received while identical-config `cmp` was blocked remained latched but the no-op install branch still printed success and exited zero. | Reviewers `p21_r1_runtime`, `p21_r2_contracts`, `p21_r3_ops`; runtime/storage/API/worker/E2E/public `332 passed` plus sanitizer/startup `41 passed`; contracts/privacy `305 passed` plus 1,440 LF/CRLF/chunk/reset variants; launchd/startup fake-temp and repeated lock/signal subsets passed before the missing no-op interrupt case |
| Final review pass 21 lone-CR TDD RED | completed before production edit | Extended the existing whole-text folded-header selectors with lone-CR variants across sanitizer unit, temporary SQLite, MCP payload, worker log, and deterministic durable E2E boundaries. Ran `uv run --locked pytest -q tests/indexing/test_background_tasks.py::test_safe_error_message_redacts_name_only_cookie_header_folded_lines tests/storage/test_metadata_store.py::test_metadata_store_redacts_name_only_cookie_header_folded_lines_at_rest tests/api/test_tools_contract.py::test_status_payloads_redact_name_only_cookie_header_folded_lines tests/indexing/test_sync_worker_logging.py::test_worker_log_redactor_removes_name_only_cookie_header_folded_lines tests/e2e/test_durable_sync_worker_flow.py::test_mcp_enqueued_job_failure_does_not_persist_delimiter_paths_in_worker_log`. | Exit `1`, `6 failed, 4 passed`: every new lone-CR path retained `folded-cookie-job-secret`; the existing LF/CRLF controls remained green |
| Final review pass 21 lone-CR GREEN | completed | Added one explicit shared line-ending expression for LF, CRLF, and lone CR to the name-only Cookie block and folded-line recognizers. Clear non-folded diagnostics still terminate the sensitive block and retain allowed structured fields. | Exact selector `10 passed in 0.82s`; broader affected scope `280 passed in 5.17s`; Ruff and `git diff --check` passed |
| Final review pass 21 no-op-interrupt TDD RED | completed before production edit | Added a deterministic process-level fake-`cmp` test parameterized over TERM and INT. The test blocks comparison in the identical-config loaded-service path, signals the installer, releases comparison, and verifies exit status, success output, no plist/launchctl mutation, and lock release. Ran `uv run --locked pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_identical_loaded_install_does_not_report_success_after_interrupt_during_cmp`. | Exit `1`, `2 failed`: both TERM and INT returned `0` and printed `Already installed` instead of reporting interruption |
| Final review pass 21 no-op-interrupt GREEN | completed | The identical-config branch now checks the latched interrupt immediately after successful `cmp`, before cleanup and success output. TERM/INT exit as `143`/`130`; the EXIT cleanup still releases the operation lock and no service state is touched. | Exact selector `2 passed in 2.34s`; full LaunchAgent file `61 passed in 31.97s`; signal/rollback/lock/concurrency subset `9 passed` x3; bash syntax, `shellcheck -x`, Ruff, and `git diff --check` passed |
| Post-pass-21 full verification | completed | Re-ran the complete static, security, public-contract, broad non-live, quality-eval, and functional E2E gates after integrating the lone-CR privacy and no-op interrupt fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `1001 passed in 72.71s`; coverage `87.40%`; retrieval `13/13`; answer `9/9`; functional E2E `43 passed in 4.58s` |
| Final review pass 22 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and launchd operations on the stable pass-21 tree. Runtime was clean. Contracts/privacy found the remaining P2 lone-CR inconsistency in normal `Cookie:`/`Set-Cookie:` value headers because their folded-line regex still used LF/CRLF only. Operations found one P3 interrupt-finalization class with two windows: a signal during identical no-op candidate cleanup or during committed transaction snapshot cleanup was latched but still reported success. | Reviewers `p22_r1_runtime`, `p22_r2_contracts`, `p22_r3_ops`; runtime/storage/API/worker/E2E `332 passed`; contracts/privacy focused/public `294 passed`; operations/startup fake-temp `67 passed`, bash syntax and ShellCheck passed |
| Final review pass 22 colon-header lone-CR TDD RED | completed before production edit | Added normal Cookie/Set-Cookie value-header cases followed by SP/HTAB lone-CR continuations and a clear structured diagnostic across whole-text/stream unit, temporary SQLite, legacy-row MCP status, worker log, and deterministic durable E2E boundaries. Ran `uv run --locked pytest -q tests/indexing/test_background_tasks.py::test_cookie_value_header_redacts_lone_cr_folded_lines_and_matches_stream tests/storage/test_metadata_store.py::test_metadata_store_redacts_lone_cr_cookie_value_continuations_at_rest tests/api/test_tools_contract.py::test_status_payloads_redact_lone_cr_cookie_value_continuations_from_legacy_rows tests/indexing/test_sync_worker_logging.py::test_worker_log_redactor_removes_lone_cr_cookie_value_continuations tests/e2e/test_durable_sync_worker_flow.py::test_mcp_enqueued_job_failure_does_not_persist_delimiter_paths_in_worker_log`. | Initial pre-production run exited `1` with `6 failed`; the finalized test shape against the old regex exited `1` with `5 failed, 1 passed`. Synthetic `folded-delta-value` remained past the header boundary |
| Final review pass 22 colon-header lone-CR GREEN | completed | The normal Cookie/Set-Cookie folded-value regex now uses the same explicit LF/CRLF/lone-CR expression as the name-only block, aligning whole-text and stream boundaries while preserving the first clear structured diagnostic. | Exact selector `6 passed in 0.83s`; broader affected scope `279 passed in 3.62s`; Ruff and `git diff --check` passed |
| Final review pass 22 interrupt-finalization TDD RED | completed before production edit | Added deterministic fake-`launchctl`, blocking-`rm`, TERM/INT tests for identical-loaded candidate cleanup and successful changed-install commit cleanup. Ran `uv run --locked pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_identical_loaded_install_exits_after_interrupt_during_candidate_cleanup tests/scripts/test_sync_worker_launch_agent.py::test_successful_changed_install_reports_interrupt_during_commit_cleanup`. | Exit `1`, `4 failed`: all TERM/INT variants returned `0` and emitted success/status/log output after the interrupt |
| Final review pass 22 interrupt-finalization GREEN | completed | No-op cleanup now rechecks the latched signal after removing its candidate. After a transaction is committed, a separate finalizer exits `143`/`130` with a fixed message that installation committed before interruption; it does not roll back committed state and never emits false success. | Exact selector `4 passed in 4.68s`; full LaunchAgent file `65 passed in 37.78s`; signal/lock/concurrency subset `31 passed` x2; bash syntax, `shellcheck -x`, Ruff, and `git diff --check` passed |
| Post-pass-22 full verification | completed | Re-ran the complete static, security, public-contract, broad non-live, quality-eval, and functional E2E gates after integrating the normal-header lone-CR and interrupt-finalization fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `1010 passed in 78.25s`; coverage `87.40%`; retrieval `13/13`; answer `9/9`; functional E2E `43 passed in 4.59s` |
| Final review pass 23 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and launchd operations on the stable pass-22 tree. Runtime was clean. Contracts/privacy found a P2 mixed-ending stream state gap: one `readline()` chunk could contain a lone-CR name-only Cookie line plus an LF/CRLF folded line, causing pending state to reset before a second folded line. Operations found a P3 pre-decision interrupt gap: changed-config `cmp` interrupted before the restart decision was reported as a configuration error without `--restart`, or as a needless rollback with `--restart`. | Reviewers `p23_r1_runtime`, `p23_r2_contracts`, `p23_r3_ops`; runtime/storage/API/E2E `334 passed` plus retained source E2E `34 passed`; contracts/privacy `285 passed` plus auth/source `17 passed`; operations/startup fake-temp `71 passed`, bash syntax and ShellCheck passed |
| Final review pass 23 mixed-ending-stream TDD RED | completed before production edit | Added Cookie/Set-Cookie mixed-ending cases where a name-only header ends in lone CR, a first SP/HTAB folded line ends in LF or CRLF inside the same bounded `readline()` chunk, a second folded line follows, and a clear diagnostic resets state. Covered both direct stream calls and the real `python -m core.error_sanitizer --stream` subprocess. Ran `uv run --locked pytest -q tests/indexing/test_background_tasks.py::test_error_stream_keeps_cookie_state_across_mixed_endings_in_one_read tests/indexing/test_background_tasks.py::test_error_sanitizer_cli_keeps_cookie_state_across_mixed_endings_in_one_read`. | Exit `1`, `8 failed`: every combination exposed the second folded line's `folded-beta-value` |
| Final review pass 23 mixed-ending-stream GREEN | completed | The bounded stream sanitizer now evaluates Cookie pending state over CRLF/CR/LF logical lines inside each raw `readline()` chunk. State survives folded lines and chunk boundaries, resets on a clear non-folded line, and preserves existing output and oversized fail-closed memory bounds. | Exact selector `8 passed in 0.11s`; broader privacy/storage/API/log/durable/startup scope `293 passed in 6.77s`; Ruff and `git diff --check` passed |
| Final review pass 23 pre-decision-interrupt TDD RED | completed before production edit | Added blocking-`cmp` TERM/INT tests for changed configuration with and without `--restart`, verifying status, stderr truth, no plist/service mutation, and lock release before any restart decision or transaction begins. Ran `uv run --locked pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_changed_install_interrupt_during_cmp_exits_before_restart_decision`. | Exit `1`, `4 failed in 4.80s`: no-restart cases exited `1` with the false changed-config error; restart cases exited with signal status but falsely claimed rollback/restoration before any mutation |
| Final review pass 23 pre-decision-interrupt GREEN | completed | The installer now checks its latched interrupt immediately after the non-identical comparison and before either restart validation or transaction start. TERM/INT exit `143`/`130` with the pre-mutation interruption message, no state mutation, and normal lock cleanup in both modes. | Exact selector `4 passed in 4.38s`; full LaunchAgent file `69 passed in 41.17s`; interrupt/signal/lock/concurrency subset `35 passed` x2; bash syntax, `shellcheck -x`, Ruff, and `git diff --check` passed |
| Post-pass-23 full verification | completed | Re-ran the complete static, security, public-contract, broad non-live, quality-eval, and functional E2E gates after integrating the mixed-ending stream and pre-decision interrupt fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `1022 passed in 82.42s`; coverage `87.38%`; retrieval `13/13`; answer `9/9`; functional E2E `43 passed in 4.60s` |
| Final review pass 24 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and launchd operations on the stable pass-23 tree. Runtime was clean. Contracts/privacy found a P3 observability edge where the multiword assignment redactor treated LF but not lone CR as a line boundary and consumed a clear diagnostic after Cookie redaction. Operations found a P3 terminal success-output window where TERM/INT arriving after the last pending check was merely latched, then ignored while success output completed. | Reviewers `p24_r1_runtime`, `p24_r2_contracts`, `p24_r3_ops`; runtime/integration `380 passed` plus claim races x20; contracts/privacy `395 passed` plus 124,416 generated no-leak checks; operations/startup fake-temp `75 passed`, bash syntax and ShellCheck passed |
| Final review pass 24 CR-diagnostic-boundary TDD RED | completed before production edit | Added whole-text/stream, temporary SQLite, MCP legacy-row status, worker-log, and deterministic durable E2E coverage for Cookie redaction followed by a lone-CR clear diagnostic without a comma. Ran `uv run --locked pytest -q tests/indexing/test_background_tasks.py::test_cookie_redaction_preserves_lone_cr_clear_diagnostic_without_comma tests/storage/test_metadata_store.py::test_metadata_store_preserves_lone_cr_clear_diagnostic_without_comma tests/api/test_tools_contract.py::test_status_payloads_preserve_lone_cr_clear_diagnostic_from_legacy_rows tests/indexing/test_sync_worker_logging.py::test_worker_log_redactor_preserves_lone_cr_clear_diagnostic_without_comma tests/e2e/test_durable_sync_worker_flow.py::test_mcp_enqueued_job_failure_does_not_persist_delimiter_paths_in_worker_log`. | Exit `1`, `6 failed`: `clear diagnostic ... retry_count=24` was consumed. An adjacent fail-closed audit also initially failed by exposing synthetic `alpha value` |
| Final review pass 24 CR-diagnostic-boundary GREEN | completed | The multiword assignment secret pattern now treats lone CR as a boundary in both its value span and termination lookahead. Clear diagnostics remain observable without weakening multiword secret redaction. | Focused scope `7 passed in 0.86s`; broader privacy/storage/API/log/durable/startup scope `299 passed in 6.51s`; Ruff and `git diff --check` passed |
| Final review pass 24 terminal-signal TDD RED | completed before production edit | Added blocking success-`printf`, TERM/INT tests for both identical-loaded no-op and committed changed-install paths. Ran `uv run --locked pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_identical_loaded_success_output_uses_default_signal_disposition tests/scripts/test_sync_worker_launch_agent.py::test_committed_install_success_output_uses_default_signal_disposition`. | Exit `1`, `4 failed in 4.43s`: every signal was latched but ignored, producing exit `0` plus success/status/log output |
| Final review pass 24 terminal-signal GREEN | completed | A terminal success finalizer restores TERM/INT default disposition before any final success output, then handles any already-latched signal with the correct pre-mutation or committed-state exit. Signals after that point terminate by the OS default; committed state is retained, no-op state remains unchanged, and cleanup/locks remain correct. | Exact selector `4 passed in 3.97s`; combined interrupt boundaries `14 passed in 15.38s`; full LaunchAgent file `73 passed in 46.28s`; interrupt/signal/lock/concurrency subset `39 passed` x2; bash syntax, `shellcheck -x`, Ruff, and `git diff --check` passed |
| Post-pass-24 full verification | completed | Re-ran the complete static, security, public-contract, broad non-live, quality-eval, and functional E2E gates after integrating the CR diagnostic-boundary and terminal-signal fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `1032 passed in 85.53s`; coverage `87.38%`; retrieval `13/13`; answer `9/9`; functional E2E `43 passed in 4.56s` |
| Final review pass 25 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and launchd operations on the stable pass-24 tree. Runtime found a P2 scoped-lifecycle defect: `claim_next_sync_job(retained_ids)` reconciled and failed out-of-scope legacy running jobs before filtering candidates. Contracts/privacy found a P3 path observability edge where lone CR was missing from path boundaries and a clear diagnostic was over-redacted. Operations found a P2 unbounded writer-drain path where a hung sanitizer could prevent wrapper shutdown indefinitely. | Reviewers `p25_r1_runtime`, `p25_r2_contracts`, `p25_r3_ops`; runtime focused `274 passed`; contracts/privacy `293 passed` plus auth/phase/legacy `11 passed`; operations/startup fake-temp `79 passed`, bash syntax and ShellCheck passed |
| Final review pass 25 scoped-claim TDD RED | completed before production edit | Added temporary-SQLite and deterministic worker E2E cases for out-of-scope stale/live running jobs, plus the retained scoped-startup invariant. Ran `uv run --locked pytest -q tests/storage/test_metadata_store.py::test_scoped_claim_does_not_recover_out_of_scope_stale_legacy_source tests/storage/test_metadata_store.py::test_scoped_claim_preserves_live_out_of_scope_global_running_blocker tests/storage/test_metadata_store.py::test_scoped_orphan_recovery_does_not_mutate_legacy_removed_sources tests/e2e/test_durable_sync_worker_flow.py::test_scoped_worker_does_not_recover_stale_legacy_source_to_claim_retained_job`. | Exit `1`, `2 failed, 2 passed`: scoped storage and worker E2E mutated the stale out-of-scope legacy job, then incorrectly claimed/completed retained work |
| Final review pass 25 scoped-claim GREEN | completed | Allowed source IDs are now applied before running-job reconciliation, so scoped workers never mutate out-of-scope lifecycle rows. The global SQL `NOT EXISTS(any RUNNING)` blocker remains unscoped, so live or stale out-of-scope work still prevents concurrent shared-Chroma writes until an explicit recovery path handles it. | Exact selector `4 passed in 0.80s`; broader storage/worker/API/all-source scope `341 passed in 5.80s`; claim/race/global-blocker subset `5 passed` x10; Ruff, mypy, and `git diff --check` passed |
| Final review pass 25 path-CR-boundary TDD RED | completed before production edit | Added whole-text/stream, temporary SQLite, MCP legacy-row, worker-log, and deterministic durable E2E cases for labeled/unlabeled absolute paths ending before lone CR and a clear diagnostic without a comma. Ran `uv run --locked pytest -q tests/indexing/test_background_tasks.py::test_path_redaction_preserves_lone_cr_clear_diagnostic_without_comma tests/storage/test_metadata_store.py::test_metadata_store_preserves_lone_cr_clear_diagnostic_after_labeled_path tests/api/test_tools_contract.py::test_status_payloads_preserve_lone_cr_clear_diagnostic_after_legacy_path tests/indexing/test_sync_worker_logging.py::test_worker_log_redactor_preserves_lone_cr_clear_diagnostic_after_path tests/e2e/test_durable_sync_worker_flow.py::test_mcp_enqueued_job_failure_does_not_persist_delimiter_paths_in_worker_log`. | Exit `1`, `6 failed`: paths remained redacted but `clear diagnostic` or `filesystem clear diagnostic` was lost |
| Final review pass 25 path-CR-boundary GREEN | completed | Path terminator and extension-content/lookahead boundaries now treat lone CR like LF, retaining the next clear diagnostic while continuing to redact labeled and unlabeled absolute paths fail-closed. | Exact selector `6 passed in 0.82s`; broader privacy/storage/API/log/durable/startup scope `307 passed in 6.56s`; Ruff and `git diff --check` passed |
| Final review pass 25 writer-drain TDD RED | completed before production edit | Added process-level TERM/INT cases with a fake sanitizer that receives EOF then waits forever, plus teardown that deterministically cleans the RED fixture. Ran `uv run --locked pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_launch_agent_runner_forces_cleanup_of_hung_writer_during_drain`. | Exit `1`, `2 failed in 9.46s`: both cases exceeded the four-second wrapper timeout in unbounded `wait(writer_pid)` |
| Final review pass 25 writer-drain GREEN | completed | The runner now gives normal drain five seconds; a drain-stage TERM/INT gets one second grace; then the tracked writer/sanitizer tree receives TERM and, if needed, KILL after another second. The original worker status is retained, a fixed safe forced-cleanup diagnostic is written, and sanitizer PID plus both FIFOs, chunk, and PID files are removed. | Exact selector `2 passed in 5.78s`; brief+hung drain `4 passed in 7.42s`; startup privacy plus LaunchAgent `81 passed in 53.22s`; signal/writer subset `5 passed` x3; bash syntax, `shellcheck -x`, Ruff, and `git diff --check` passed |
| Post-pass-25 full verification | completed | Re-ran the complete static, security, public-contract, broad non-live, quality-eval, and functional E2E gates after integrating scoped-claim, path-CR-boundary, and bounded writer-drain fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `1042 passed in 92.96s`; coverage `87.39%`; retrieval `13/13`; answer `9/9`; functional E2E `44 passed in 4.59s` |
| Final review pass 26 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and launchd operations on the stable pass-25 tree. Runtime found only a P3 plan drift because two matrix cells still hardcoded 43 while the functional gate had grown to 44. Contracts/privacy found a P2 short explicit-auth gap because Bearer/Basic values shorter than eight characters bypassed the shared sanitizer. Operations found a P2 writer-first orphan path where internal writer failure could leave the tracked sanitizer alive because forced tree cleanup ran only while the writer remained alive. | Reviewers `p26_r1_runtime`, `p26_r2_contracts`, `p26_r3_ops`; runtime/scoped/source `362 passed` plus contracts/architecture `28 passed`; contracts/privacy `321 passed` plus 1,944 mixed-ending cases; operations/startup fake-temp `81 passed`, bash syntax and ShellCheck passed |
| Final review pass 26 docs fix | completed | Removed the functional test count entirely from the two summary-matrix cells. They now route volatile count and timing evidence only to the append-only progress log, so adding deterministic cases no longer makes the matrix stale. This was an atomic main-agent plan-integration edit. | Matrix has no hardcoded functional count; `git diff --check` passed |
| Final review pass 26 short-auth TDD RED | completed before production edit | Added unit, temporary SQLite, MCP list/status legacy-row, retained worker-log, and deterministic durable E2E coverage for explicit `Bearer abc123` and `Basic Og==`. Ran `uv run --locked pytest -q tests/indexing/test_background_tasks.py::test_short_explicit_auth_credentials_redact_without_losing_fields_or_prose tests/storage/test_metadata_store.py::test_metadata_store_redacts_short_explicit_auth_credentials_at_rest tests/api/test_tools_contract.py::test_list_and_status_redact_short_explicit_auth_credentials_from_legacy_rows tests/indexing/test_sync_worker_logging.py::test_worker_log_redactor_removes_short_explicit_auth_credentials tests/e2e/test_durable_sync_worker_flow.py::test_mcp_enqueued_job_failure_does_not_persist_delimiter_paths_in_worker_log`. | Exit `1`, `5 failed`: both short explicit credentials crossed their intended durable/public/log boundaries |
| Final review pass 26 short-auth GREEN | completed | Added a scheme-specific Bearer/Basic credential redactor that accepts any nonempty credential and preserves surrounding punctuation and structured fields. Generic token length thresholds remain unchanged, and prose such as `bearer authentication failed` is preserved. | Exact selector `5 passed in 0.83s`; broader privacy/storage/API/log/durable/startup scope `311 passed in 5.90s`; Ruff and `git diff --check` passed |
| Final review pass 26 writer-first TDD RED | completed before production edit | Added a fake writer dependency that exits `71` while its tracked sanitizer continues waiting and the worker exits `17`. The test verifies wrapper status, sanitizer liveness, fixed safe diagnostics, and temp cleanup. Ran `uv run --locked pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_launch_agent_runner_cleans_sanitizer_when_writer_fails_first`. | Exit `1`, `1 failed in 2.40s`: wrapper returned `17` but the sanitizer remained alive after writer failure |
| Final review pass 26 writer-first GREEN | completed | Drain liveness now considers both writer and tracked sanitizer even when the writer exits first. Writer-first failure triggers TERM, bounded wait, KILL if needed, bounded tree-death confirmation, a fixed safe forced-cleanup diagnostic, and complete FIFO/chunk/PID cleanup while retaining worker status `17`. | Exact selector `1 passed in 2.30s`; writer-first plus brief/hung drain `5 passed in 9.71s`; startup privacy plus LaunchAgent `82 passed in 59.91s`; writer/signal subset `6 passed` x3; bash syntax, `shellcheck -x`, Ruff, and `git diff --check` passed |
| Post-pass-26 full verification | completed | Re-ran the complete static, security, public-contract, broad non-live, quality-eval, and functional E2E gates after integrating count-free matrix evidence, short-auth redaction, and writer-first cleanup. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `1047 passed in 93.50s`; coverage `87.39%`; retrieval `13/13`; answer `9/9`; functional E2E `44 passed in 4.59s` |
| Final review pass 27 | findings | Exactly three fresh read-only reviewers inspected runtime/concurrency, MCP/contracts/privacy, and launchd operations on the stable pass-26 tree. Runtime was clean. Contracts/privacy found a P2 token-boundary gap where short explicit Bearer/Basic credentials followed by ordinary prose were not redacted, plus a P3 oversized-stream gap where a logical line ending in lone CR caused the following clear diagnostic to be discarded. Operations found a P3 render-only interrupt gap where a signal during blocking `plutil` could leave the target overwritten while reporting that mutation had not occurred. | Reviewers `p27_r1_runtime`, `p27_r2_contracts`, `p27_r3_ops`; runtime `301 + 139 + 9` focused tests passed |
| Final review pass 27 auth-prose TDD RED | completed before production edit | Added unit, temporary-SQLite, MCP legacy-row, worker-log, and deterministic durable E2E coverage for `Bearer abc123 while syncing` and `Basic Og== because retrying`. The exact cross-layer selector exited `1` with `5 failed`: both credentials remained visible when followed by ordinary prose. | The regression fixtures also require surrounding prose, punctuation, `source_id`, and `job_id` to remain observable |
| Final review pass 27 auth-prose GREEN | completed | The scheme-specific redactor now consumes only the first non-whitespace credential token after canonical Bearer/Basic, replaces it with `<redacted-auth>`, and preserves subsequent prose and structured fields. Lowercase descriptive phrases such as `bearer authentication failed` remain unchanged. | Exact cross-layer selector `5 passed in 1.04s`; integrated main-agent selector including stream cases `10 passed in 0.86s` |
| Pass-27 integration lowercase-auth follow-up | completed; atomic main-agent TDD exception recorded before target edit | A read-only integration probe found that HTTP auth schemes are case-insensitive but lowercase explicit `bearer lower123` / `basic b2c=` were not covered, while the existing lowercase descriptive prose must remain visible. The change was a single sanitizer matcher plus one focused unit boundary and was therefore truly atomic. The user-imposed three-agent limit and the active render-only worker left no safe additional worker slot, so the orchestrator owned this non-overlapping test-first correction. Focused RED exited `1` with `1 failed` because `lower123` crossed the whole-text boundary. The matcher is now case-insensitive while preserving the explicit non-credential prose words `authentication` and `troubleshooting`. | Exact GREEN `1 passed`; broader privacy/storage/API/log/durable/startup scope `316 passed in 7.15s`; Ruff and `git diff --check` passed |
| Final review pass 27 oversized-lone-CR TDD RED | completed before production edit | Added direct bounded-stream, actual `python -m core.error_sanitizer --stream`, and folded-Cookie fail-closed cases. The combined selector exited `1` with `4 failed, 1 passed`: the oversized placeholder was emitted, but the clear diagnostic after a lone-CR logical boundary was lost; the retained folded fail-closed baseline already passed. | The failing behavior was deterministic and required no provider or user data |
| Final review pass 27 oversized-lone-CR GREEN | completed | Oversized discard now stops at the first CRLF, lone CR, or LF logical boundary and returns the same chunk's remainder to the bounded processing loop. The placeholder and Cookie continuation fail-closed state remain, with no unbounded buffer. | Exact direct/CLI/folded selector `5 passed in 0.07s`; broader privacy/storage/API/log/durable/startup scope `316 passed in 5.83s`; Ruff and `git diff --check` passed |
| Final review pass 27 render-only-interrupt TDD RED | completed before production edit | Added deterministic fake-blocking-`plutil` TERM/INT cases for both a pre-existing render target and an absent target. Ran `uv run --locked pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_render_only_interrupt_during_validation_preserves_target`. | Exit `1`, `4 failed in 1.94s`: existing targets were overwritten with rendered XML, absent targets were created, and exits `143`/`130` falsely claimed interruption before installation mutation |
| Final review pass 27 render-only-interrupt GREEN | completed | Render-only now snapshots any prior target, renders and validates a private temporary candidate, checks the interrupt latch before publication, and rolls back a signal racing atomic publication by restoring the snapshot or removing the newly created target. Cleanup removes candidates/snapshots and diagnostics distinguish preserved, restored, removed-new, and already-committed outcomes. | Exact selector `4 passed in 1.74s`; render-only subset `7 passed`; full LaunchAgent `80 passed in 56.65s`; signal/lock/concurrency/render subset `46 passed in 32.18s`; bash syntax, ShellCheck, Ruff, and `git diff --check` passed |
| Post-pass-27 full verification | completed | Re-ran the complete static, security, public-contract, broad non-live, deterministic quality, and functional E2E gates after integrating all pass-27 fixes and the lowercase-auth boundary. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `1056 passed in 97.00s`; coverage `87.41%`; retrieval `13/13`; answer `9/9`; functional E2E `44 passed in 4.57s` |
| Final review pass 28 | findings | Exactly three fresh read-only reviewers inspected bugs/correctness, security/data safety, and performance/reliability on the fully verified pass-27 tree. Correctness and reliability found that scoped orphan reconciliation plus an unscoped global single-flight blocker lets a definitively stale removed/unsupported source starve every retained source forever. Security found that a folded `Authorization: Bearer/Basic` credential can cross sanitizer, SQLite, MCP, and log boundaries. Reliability additionally reproduced normal metadata reads waiting on repeated `ensure_schema()` write-lock acquisition and failing after about five seconds while another writer was held. | Fresh reviewers `p28_r1_correctness`, `p28_r2_security`, `p28_r3_reliability`; no live provider, launchctl, user SQLite/Chroma, or real credential was used |
| Final review pass 28 folded-Authorization TDD RED | completed before production edit | Added whole-text/stream LF, CRLF, lone-CR, and mixed-chunk cases plus temporary SQLite, MCP legacy status, worker log, startup wrapper, and deterministic durable E2E coverage. Exact command: `uv run pytest -q tests/indexing/test_background_tasks.py::test_folded_authorization_credentials_redact_without_losing_next_diagnostic tests/indexing/test_background_tasks.py::test_error_stream_keeps_folded_authorization_state_across_mixed_line_chunks tests/indexing/test_sync_worker_logging.py::test_worker_log_redactor_removes_folded_authorization_credentials tests/storage/test_metadata_store.py::test_metadata_store_redacts_folded_authorization_credentials_at_rest tests/api/test_tools_contract.py::test_status_payloads_redact_folded_authorization_credentials_from_legacy_rows tests/scripts/test_sync_worker_startup_privacy.py::test_launch_agent_runner_redacts_folded_authorization_credentials tests/e2e/test_durable_sync_worker_flow.py::test_mcp_enqueued_job_failure_does_not_persist_delimiter_paths_in_worker_log`. | Exit `1`, `12 failed`: folded Bearer/Basic credentials remained across every intended boundary while the following clear diagnostics remained visible |
| Final review pass 28 folded-Authorization GREEN | completed | Whole-text redaction now covers folded Authorization blocks, and the bounded stream maintains an independent Authorization continuation state until the first clear non-folded logical line. Cookie precedence, oversized fail-closed behavior, bounded memory, and clear diagnostic observability remain intact across LF, CRLF, lone CR, and mixed chunks. | The identical literal seven-selector command rerun returned `12 passed in 1.10s`; broader owned privacy scope `110 passed, 217 deselected in 3.12s`; Ruff, core mypy, compileall, and `git diff --check` passed |
| Final review pass 28 storage/concurrency TDD RED | completed before production edit | Added temporary-SQLite and deterministic worker E2E cases for a stale out-of-scope legacy source recovering before a retained claim, a live out-of-scope owner remaining a blocker, document/chunk/tombstone snapshots remaining unchanged, initialized `list_sources()`/`get_sync_job()` reading promptly behind an unrelated held writer, and a waiter reading during a worker claim transaction. The valid exact selector was run after correcting a test-only cleanup error. | Exit `1`, `3 failed, 1 passed in 1.59s`: stale legacy storage/E2E cases returned no retained claim and initialized reads timed out behind `BEGIN IMMEDIATE`; the live blocker safety case already passed |
| Final review pass 28 storage/concurrency GREEN | completed | Global liveness reconciliation is now separate from connector-scoped dispatch: only definitively inactive owners terminalize before global single-flight; live or ambiguous unsupported owners remain blockers. Recovery changes lifecycle metadata only. Schema initialization is retry-safe and once per `MetadataStore` instance under an `RLock`; normal reads after initialization no longer request the migration write lock, while distinct store/database instances still initialize independently. | Exact final scope `5 passed in 0.91s`; broader storage/durable/API/worker `254 passed in 3.34s`; six concurrency/recovery/migration/read selectors repeated 10 times `60/60 passed`; compile, Ruff, repository mypy, and `git diff --check` passed |
| Final review pass 28 fix routing | completed | Folded Authorization was handled by a dedicated privacy worker; global definitive recovery and one-time schema initialization were handled by a separate storage/concurrency worker. Ownership did not overlap and both workers preserved other changes. | Run integrated full verification before a fresh exactly-three-reviewer pass |
| Post-pass-28 full verification | completed | Re-ran the complete static, security, public-contract, broad non-live, deterministic quality, and functional E2E gates after integrating all pass-28 fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `1069 passed in 91.15s`; coverage `87.44%`; retrieval `13/13`; answer `9/9`; functional E2E `44 passed in 3.80s` |
| Final review pass 29 | findings | Exactly three fresh read-only reviewers inspected bugs/correctness, security/data safety, and performance/reliability on the fully verified pass-28-fix tree. Correctness was clean after a focused 16-test recheck. Reliability was clean after a focused 13-test operational/concurrency recheck. Security found one P2 multi-stage folded Authorization gap: header name, scheme, and credential on three successive logical lines could expose the credential across storage, MCP, and logs. | Fresh reviewers `p29_r1_correctness`, `p29_r2_security`, `p29_r3_reliability`; the known security finding is the only pass blocker |
| Final review pass 29 multi-stage Authorization TDD RED | completed before production edit | Added whole-text/stream coverage for Authorization colon/equal, Bearer/Basic, LF, CRLF, lone CR, and mixed chunks where header, scheme, and credential occupy successive logical lines, plus temporary SQLite, MCP legacy status, worker log, startup wrapper, and durable E2E. Exact command: `uv run pytest -q tests/indexing/test_background_tasks.py::test_multistage_folded_authorization_credentials_redact_without_losing_next_diagnostic tests/indexing/test_background_tasks.py::test_error_stream_keeps_multistage_authorization_state_across_mixed_line_chunks tests/storage/test_metadata_store.py::test_metadata_store_redacts_multistage_folded_authorization_credentials_at_rest tests/api/test_tools_contract.py::test_status_payloads_redact_multistage_folded_authorization_from_legacy_rows tests/indexing/test_sync_worker_logging.py::test_worker_log_redactor_removes_multistage_folded_authorization_credentials tests/scripts/test_sync_worker_startup_privacy.py::test_launch_agent_runner_redacts_multistage_folded_authorization_credentials tests/e2e/test_durable_sync_worker_flow.py::test_mcp_enqueued_job_failure_does_not_persist_delimiter_paths_in_worker_log`. | Exit `1`, `12 failed in 2.06s`: credentials crossed all intended layers; the next clear diagnostic remained visible |
| Final review pass 29 multi-stage Authorization GREEN | completed | The bounded state machine now recognizes name/delimiter-only Authorization, accepts a scheme-only folded continuation, and redacts subsequent folded credential/continuation lines through the first clear non-folded line. Whole-text behavior follows the same boundary. Existing same-line/folded auth, Cookie precedence, oversized bounds, clear diagnostics, and lowercase prose remain intact. | The identical literal seven-selector command rerun returned `12 passed in 1.27s`; broader six-file privacy scope `114 passed, 226 deselected in 4.60s`; Ruff, core mypy, compileall, and `git diff --check` passed |
| Final review pass 29 fix routing | completed | A fresh privacy worker owned the sanitizer and layered privacy regressions without overlapping storage, launchd, docs, or other changes. | Run full integrated verification before another fresh exactly-three-reviewer pass |
| Post-pass-29 full verification | completed | Re-ran the complete static, security, public-contract, broad non-live, deterministic quality, and functional E2E gates after integrating the pass-29 security fix. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `1080 passed in 91.32s`; coverage `87.44%`; retrieval `13/13`; answer `9/9`; functional E2E `44 passed in 3.78s` |
| Final review pass 30 | findings | Exactly three fresh read-only reviewers inspected bugs/correctness, security/data safety, and performance/reliability on the fully verified pass-29-fix tree. Correctness found a P2 audit gap: pass-28/pass-29 privacy rows referred to exact seven-selector RED commands without including their literal commands. Security found a P2 production-boundary split where an oversized Authorization/Cookie line ending in lone CR could leave only folded indentation in one physical read and emit the credential from the next read. Reliability found a P2 supervision gap where an early sanitizer/writer failure remained invisible indefinitely while the long-lived worker stayed alive. | Fresh reviewers `p30_r1_correctness`, `p30_r2_security`, `p30_r3_reliability`; safe synthetic/fake/temp checks only |
| Final review pass 30 physical-read-fragment TDD RED | completed before production edit | Added production-boundary direct stream, actual CLI, bounded fragmented-reader, and startup-wrapper coverage for folded Authorization/Cookie across LF, CRLF, and lone CR, while retaining the next clear diagnostic. Exact command: `uv run --locked pytest -q 'tests/indexing/test_background_tasks.py::test_error_stream_redacts_folded_credential_when_indent_and_body_split_at_production_boundary' 'tests/indexing/test_background_tasks.py::test_error_sanitizer_cli_redacts_folded_credential_when_indent_and_body_split_at_production_boundary' 'tests/indexing/test_background_tasks.py::test_error_stream_keeps_fragment_tracking_bounded_until_logical_terminator' 'tests/scripts/test_sync_worker_startup_privacy.py::test_launch_agent_runner_redacts_folded_credentials_when_indent_and_body_split_at_production_boundary'`. | Exit `1`, `6 failed, 8 passed in 0.51s`: direct Auth/Cookie lone-CR, real CLI, fragmented-reader, and startup persistence exposed the credential; LF/CRLF variants already passed |
| Final review pass 30 physical-read-fragment GREEN | completed | Two booleans retain fail-closed Cookie/Authorization ownership when an indentation-only physical fragment precedes the credential body. No credential body is buffered; reads remain bounded at `max_line_chars + 1`, and state resets at the logical terminator. | Identical command rerun `14 passed in 0.46s`; broader owned privacy `79 passed in 2.95s`; Ruff, repository mypy, core compileall, and `git diff --check` passed |
| Final review pass 30 runner-supervision TDD RED | completed before production edit | Recorded pre-edit runner checksum `2197320472 9725`. Added a process-level fake sanitizer exiting `73` immediately and a TERM-ignoring long-lived worker. Exact command: `uv run --locked pytest -q 'tests/scripts/test_sync_worker_launch_agent.py::test_launch_agent_runner_stops_worker_when_sanitizer_fails_first'`. | Exit `1`, `1 failed in 5.69s`; `subprocess.TimeoutExpired` at `process.wait(timeout=5)` showed the wrapper waiting only for the worker |
| Final review pass 30 runner-supervision GREEN | completed | Writer completion now interrupts the blocking parent wait with SIGUSR1. If writer/sanitizer fails first, the wrapper terminates the worker process group, escalates to KILL after one second, reaps worker/timer/writer, preserves sanitizer exit `73`, writes only the fixed safe diagnostic, and exits non-zero without busy polling. | Identical selector `1 passed in 1.84s`; new plus retained writer-failure regression `2 passed in 3.66s`; full runner/startup privacy `90 passed in 59.84s`; race selector `10/10`; bash syntax, ShellCheck, Ruff, and `git diff --check` passed |
| Final review pass 30 docs audit fix | completed; atomic main-agent docs integration | Replaced the pass-28/pass-29 references to an external “exact selector” with the literal seven-selector RED commands and explicitly tied each GREEN result to the identical command. No behavior or tests changed. | Run architecture/doc verification and include the updated plan in the next fresh review pass |
| Final review pass 30 fix routing | completed | Dedicated privacy and launchd runner workers owned non-overlapping behavior fixes. The orchestrator added only the literal audit evidence to the plan after worker results were stable. | Run focused docs checks, integrated full verification, then another fresh exactly-three-reviewer pass |
| Post-pass-30 full verification | completed | Re-ran architecture/docs checks, then the complete static, security, public-contract, broad non-live, deterministic quality, and functional E2E gates after integrating all pass-30 fixes. | Architecture docs `7 passed`; `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `1095 passed in 92.31s`; coverage `87.51%`; retrieval `13/13`; answer `9/9`; functional E2E `44 passed in 3.80s` |
| Final review pass 31 | findings | Exactly three fresh read-only reviewers inspected bugs/correctness, security/data safety, and performance/reliability on the fully verified pass-30-fix tree. Correctness was clean. Security found a P2 CRLF physical-boundary gap: a trailing CR at character 65,537 followed by leading LF in the next read reset sensitive-header state before the folded credential. Reliability found a P2 status-read contention gap: `get_latest_sync_job()` acquired `BEGIN IMMEDIATE` for ordinary observation and failed after about five seconds behind an unrelated writer. | Fresh reviewers `pass31_correctness`, `pass31_security`, `pass31_reliability_fresh`; safe synthetic/fake/temp checks only |
| Final review pass 31 CRLF-split TDD RED | completed before production edit | Added direct stream, real CLI, small fragmented-reader, and startup-wrapper cases for Auth/Cookie where CR ends one physical read and LF begins the next, including production and bounded test sizes. Exact command: `uv run --locked pytest -q tests/indexing/test_background_tasks.py tests/scripts/test_sync_worker_startup_privacy.py -k 'coalesces_boundary_split_crlf or coalesces_fragmented_crlf or crlf_is_split_at_production_boundary'`. | Exit `1`, `7 failed, 79 deselected`; all seven exposed the raw folded credential |
| Final review pass 31 CRLF-split GREEN | completed | One boolean `trailing_cr_pending` preserves sensitive state when the next physical read begins with the paired LF. A non-LF next character retains lone-CR behavior; no credential body or unbounded buffer is introduced. | Identical selector `7 passed, 79 deselected in 0.38s`; broader privacy/startup `107 passed in 5.62s`; Ruff, compileall, and `git diff --check` passed |
| Final review pass 31 latest-status-lock TDD RED | completed before production edit | Added initialized temporary-SQLite queued/running/terminal observation under unrelated `BEGIN IMMEDIATE`, stale-reconciliation atomicity control, real FastMCP single/all-source status, and durable queued-status E2E. Exact command: `uv run --locked pytest -q tests/storage/test_metadata_store.py tests/api/test_tools_contract.py tests/e2e/test_durable_sync_worker_flow.py -k 'latest_sync_job_observation_does_not_wait_for_unrelated_writer or latest_sync_job_stale_reconciliation_is_atomic or real_fastmcp_get_sync_status_does_not_wait_for_unrelated_writer or durable_queued_status_remains_observable_during_unrelated_sqlite_write'`. | Exit `1`, `6 failed, 1 passed, 254 deselected in 5.70s`: all ordinary observations exceeded the bounded latency; stale atomicity control already passed |
| Final review pass 31 latest-status-lock GREEN | completed | `get_latest_sync_job()` first takes a read-only snapshot. Queued, healthy running, and terminal states return without a write reservation. Only potentially stale/inconsistent state enters `BEGIN IMMEDIATE`, re-reads and rechecks every predicate, timestamps after lock acquisition, then reconciles atomically. Owner/process-start/heartbeat rules remain unchanged. | Identical selector `7 passed, 254 deselected in 0.85s`; post-refactor `0.84s`; repeated `70/70`; latest/status `19 passed`; assigned files `261 passed`; storage/API/worker/durable scope `336 passed in 4.15s`; Ruff, repository mypy, compileall, and `git diff --check` passed |
| Final review pass 31 fix routing | completed | Dedicated privacy and storage/API workers owned non-overlapping fixes and preserved all other changes. | Run full integrated verification before another fresh exactly-three-reviewer pass |
| Post-pass-31 full verification | completed | Re-ran the complete static, security, public-contract, broad non-live, deterministic quality, and functional E2E gates after integrating both pass-31 fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `1109 passed in 91.38s`; coverage `87.44%`; retrieval `13/13`; answer `9/9`; functional E2E `45 passed in 3.78s` |
| Final review pass 32 | findings | Exactly three fresh read-only reviewers inspected bugs/correctness, security/data safety, and performance/reliability on the fully verified pass-31-fix tree. Correctness was clean. Security found and reliability independently confirmed one P2 runtime-permission drift: after deletion, runner and direct worker paths could recreate the log directory as `0755` and the worker log as `0644` under inherited `umask 022`. | Fresh reviewers `pass32_correctness_fresh`, `pass32_security_fresh`, `pass32_reliability_fresh`; safe synthetic/fake/temp checks only |
| Final review pass 32 log-permission TDD RED | completed before production edit | Added direct worker and launchd wrapper cases under `umask 022` for missing runtime directory, directory/log modes, symlink directory/file, simulated wrong owner, and non-directory rejection. Exact command: `uv run --locked pytest -q tests/indexing/test_sync_worker_logging.py::test_worker_logging_recreates_private_runtime_directory_and_log tests/indexing/test_sync_worker_logging.py::test_worker_logging_rejects_unsafe_runtime_log_paths_without_mutating_targets tests/scripts/test_sync_worker_launch_agent.py::test_launch_agent_runner_recreates_private_runtime_log_paths_and_temp_files tests/scripts/test_sync_worker_launch_agent.py::test_launch_agent_runner_rejects_unsafe_runtime_log_paths_without_mutation`. | Exit `1`, `7 failed, 2 passed in 2.54s`: recreated directory was `0755`; symlink directory/file and wrong-owner cases were accepted; non-directory cases already failed closed |
| Final review pass 32 log-permission GREEN | completed | Wrapper creation now uses `umask 077` and validates canonical path, type, symlinks, current owner, and private modes. Direct worker logging creates directories as `0700`, opens rotating logs no-follow as `0600`, validates ownership/type/mode, and fails closed on unsafe paths without mutating targets. Existing rotation, supervision, and private runtime temp behavior remain. | Identical selector `9 passed in 1.33s`; broader three files `121 passed in 61.80s`; repeated `90/90`; bash syntax, compileall, Ruff, focused mypy, and `git diff --check` passed |
| Final review pass 32 fix routing | completed | One dedicated worker owned runner/direct-worker log-path privacy and its tests, preserving all other changes. | Run full integrated verification before another fresh exactly-three-reviewer pass |
| Post-pass-32 full verification | completed | Re-ran the complete static, security, public-contract, broad non-live, deterministic quality, and functional E2E gates after integrating the pass-32 fix. | `./scripts/verify_all.sh` -> public contracts `21 passed`; broad suite `1118 passed in 93.59s`; coverage `87.41%`; retrieval `13/13`; answer `9/9`; functional E2E `45 passed in 3.77s` |
| Final review pass 33 | findings | Exactly three fresh read-only reviewers inspected bugs/correctness, security/data safety, and performance/reliability on the fully verified pass-32-fix tree. Correctness and security were clean. Reliability found one P2 installer/runtime invariant mismatch: a symlinked parent of the default `~/.mcp_content_search/logs` path could pass installation but be rejected by the runner forever under KeepAlive. | Fresh reviewers `p33_r1_correctness`, `p33_r2_security_fresh`, `p33_r3_reliability_fresh`; safe fake/temp and read-only checks only |
| Final review pass 33 default-log-parent TDD RED | completed before production edit | Added fake-HOME/fake-launchctl coverage with a symlinked default `.mcp_content_search` parent and assertions that target, plist, and launchctl state remain untouched. Exact command: `uv run --locked pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_install_rejects_symlink_in_default_log_parent_before_mutation`. | Exit `1`, `1 failed in 0.88s`; installer followed the symlink and returned `0` |
| Final review pass 33 default-log-parent GREEN | completed | Default and explicit log paths now receive the same canonical component/type/symlink validation before mutation. Existing default directories validate owner/write/search before private chmod; explicit paths retain the no-silent-chmod policy. Unsafe symlink targets, plist, and launchctl remain untouched. | Identical selector `1 passed in 0.25s`; focused default/custom/runner `10 passed`; full LaunchAgent `86 passed in 57.44s`; repeated `50/50`; bash syntax, Ruff, ShellCheck excluding the existing dynamic-source informational SC1091, and `git diff --check` passed |
| Final review pass 33 fix routing | completed | One dedicated installer worker owned default-path validation and its deterministic fake-launchctl tests. | Run full integrated verification before another fresh exactly-three-reviewer pass |
| Post-pass-33 full verification | completed | Re-ran the complete static, security, public-contract, broad non-live, deterministic quality, and functional E2E gates after integrating the pass-33 fix. | `./scripts/verify_all.sh` -> public contracts `21 passed in 0.84s`; broad suite `1119 passed in 90.25s`; coverage `87.41%`; retrieval `13/13`; answer `9/9`; functional E2E `45 passed in 3.78s` |
| Final review pass 34 | findings | Exactly three fresh read-only reviewers inspected bugs/correctness, security/data safety, and performance/reliability on the fully verified pass-33-fix tree. Correctness found a P2 supervisor gap where a sanitizer/writer exiting early with status zero stops a healthy worker but lets the wrapper report success without a fixed diagnostic. Security found a P2 bare name-only `Authorization` folding gap that can retain a folded scheme/credential across SQLite, MCP, and worker logs. Reliability found a P2 status-observation contention gap because `list_sources` and `get_sync_status` re-register unchanged sources and therefore acquire SQLite's writer lock. | Fresh reviewers `p34_r1_correctness`, `p34_r2_security`, `p34_r3_reliability`; safe synthetic/fake/temp checks only; no live provider, `launchctl`, user SQLite, or Chroma access |
| Final review pass 34 fix routing | completed | Routed the three non-overlapping findings to dedicated supervisor, privacy, and API/status workers. Each worker added the failing regression before its production edit, retained literal RED/GREEN evidence, and preserved all other user/agent changes. | Run integrated full verification and affected functional smoke, then another fresh exactly-three-reviewer pass |
| Final review pass 34 bare-Authorization TDD RED | completed before production edit | Added whole-text and fragmented-stream unit coverage across LF, CRLF, lone CR, Bearer, and Basic; temporary SQLite persistence, legacy MCP status, worker-log, startup-wrapper, and durable E2E coverage. Exact command: `uv run --locked pytest -q 'tests/indexing/test_background_tasks.py::test_bare_name_folded_authorization_credentials_redact_without_losing_next_diagnostic' 'tests/indexing/test_background_tasks.py::test_error_stream_keeps_bare_name_authorization_state_across_mixed_chunks' 'tests/storage/test_metadata_store.py::test_metadata_store_redacts_bare_name_folded_authorization_credentials_at_rest' 'tests/api/test_tools_contract.py::test_status_payloads_redact_bare_name_folded_authorization_from_legacy_rows' 'tests/indexing/test_sync_worker_logging.py::test_worker_log_redactor_removes_bare_name_folded_authorization_credentials' 'tests/scripts/test_sync_worker_startup_privacy.py::test_launch_agent_runner_redacts_bare_name_folded_authorization_credentials' 'tests/e2e/test_durable_sync_worker_flow.py::test_mcp_enqueued_job_failure_does_not_persist_delimiter_paths_in_worker_log'`. | Exit `1`, `12 failed in 2.06s`: bare name-only `Authorization` did not establish the folded sensitive boundary, so credentials crossed every intended privacy layer while clear non-folded diagnostics remained visible |
| Final review pass 34 bare-Authorization GREEN | completed | A bare header now establishes Authorization state only when the complete logical line is exactly `Authorization` (case-insensitive, surrounding horizontal whitespace allowed). Whole-text handling requires the following folded Bearer/Basic scheme and credential lines; stream handling reuses the existing bounded booleans and mixed-line-ending state. Cookie handling and the first clear non-folded diagnostic remain unchanged. The first post-edit run was `11 passed, 1 failed`: its E2E fixture had appended `Authorization` to preceding prose instead of placing it on a bare logical line; correcting that test boundary required no production change. | The identical literal seven-selector command then returned `12 passed in 1.16s`; broader owned privacy scope `105 passed, 279 deselected in 6.24s`; Ruff, focused mypy, compileall, and `git diff --check` passed |
| Final review pass 34 supervisor-exit-zero TDD RED | completed before production edit | Added a deterministic process-level case where the sanitizer exits zero after the worker is ready and the TERM-handling worker also exits zero. Exact command: `uv run --locked pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_launch_agent_runner_fails_when_sanitizer_exits_zero_before_worker`. | Exit `1`, `1 failed in 0.92s`: the wrapper returned `0` instead of the expected fixed non-zero status and wrote no fixed diagnostic |
| Final review pass 34 supervisor-exit-zero GREEN | completed | An early writer/sanitizer exit while the worker is live is now always a pipeline failure. Status zero maps to fixed status `70`; a real non-zero writer status remains authoritative. The wrapper emits only the fixed bounded diagnostic `error: startup diagnostic pipeline stopped unexpectedly` and retains TERM/KILL escalation plus complete process and temporary-file reaping. | Identical selector `1 passed in 0.67s`; relevant combined selectors `8 passed in 12.76s`; post-refactor zero/non-zero selectors `2 passed in 2.23s`; full LaunchAgent file `87 passed in 56.93s`; bash syntax, ShellCheck, Ruff, and `git diff --check` passed |
| Final review pass 34 read-only-status TDD RED | completed before production edit | Added real FastMCP, real source-registry, and temporary-SQLite coverage for `list_sources`, single-source `get_sync_status`, all-source `get_sync_status`, unrelated held writers, and unchanged `source.updated_at`. Exact command: `uv run --locked pytest -q tests/api/test_status_observation_concurrency.py`. | Exit `1`, `4 failed in 3.16s`: all three observation calls exceeded the 0.75-second bound behind `BEGIN IMMEDIATE`, and `list_sources` changed the existing source timestamp |
| Final review pass 34 read-only-status GREEN | completed | Public observation now overlays the configured registry in memory without re-registering unchanged source rows. Startup registration and enqueue-time registration remain authoritative. Existing auth normalization, dynamic Obsidian display state, error redaction, response shapes, and public-source filtering remain intact. | Identical selector `4 passed in 0.55s`; storage/API/new-status/durable scope `267 passed in 3.75s`; API contract plus new status tests `80 passed`; Ruff, compileall, repository mypy, and `git diff --check` passed |
| Post-pass-34 full verification | completed | Re-ran the complete static, security, public-contract, broad non-live, deterministic quality, and functional E2E gates after integrating all three pass-34 fixes. | `./scripts/verify_all.sh` -> public contracts `21 passed in 1.28s`; broad suite `1135 passed in 100.19s`; coverage `87.43%`; retrieval `13/13`; answer `9/9`; functional E2E `45 passed in 4.20s` |
| Final review | pending | Repeat with exactly three fresh reviewers until the newest pass is clean. | Pending |
| PR delivery | pending | Stage relevant files, commit, push, and open a `main`-base PR. | Pending |
| LaunchAgent install/live smoke | blocked | Requires explicit approval because it starts a persistent process and may touch user data/providers. | Not authorized by plan creation alone |
| Latest-main integration 3 | completed | Rebased onto `origin/main` `4b1948c` (date-aware `list_documents` + exact-job status observation; `wait_for_sync_all` removed). Preserved durable enqueue/claim/LaunchAgent behavior; renumbered durable-worker ADR to `0010`; made list/status observation read-only with registerable-source merge; rewrote wait/supersession E2Es to exact-job `get_sync_status` + separate `SyncWorker`. | Base `origin/main` `4b1948c`; branch ahead `4 0`; focused integration suite `382 passed in 6.48s`; ADR `0010-durable-all-source-sync-worker.md`; no `wait_for_sync_all` in production/docs |
| Post-main-integration verification | completed | After latest-main integration 3 plus locale-safe LaunchAgent lock test fix, re-ran full static/security/contract/broad/eval/functional gates. | `./scripts/verify_all.sh` -> exit `0`; functional E2E `49 passed in 3.16s`; coverage `88.07%` |
| Final review pass post-main-integration | findings | Correctness reviewer found exact-job `get_sync_status` skips registry overlay used by list/latest, and architecture still claims registration refresh on list/status. Security and reliability reported no actionable findings. | Reviewers: correctness `719a3d0c`, security `7b638f9f`, reliability `9fdb500c` |
| Post-main-integration review remediation RED | completed before production edit | Exact-job overlay missing; `uv run --locked pytest -q tests/api/test_tools_contract.py::test_list_sources_and_status_refresh_dynamic_registry_state_without_sync tests/api/test_status_observation_concurrency.py::test_real_fastmcp_status_observation_is_read_only_during_unrelated_write -k 'exact or refresh_dynamic'` | Exit `1`; `assert True is False` at exact `enabled` |
| Post-main-integration review remediation GREEN | completed | Applied `_merge_observed_source` in exact-job branch; updated architecture rationale; added exact-job contention coverage. | Focused `6 passed in 0.87s` |
| Post-remediation full verification | completed | Re-ran full gates after exact-job overlay fix. | `./scripts/verify_all.sh` -> exit `0`; functional E2E `49 passed in 3.28s` |
| Final review pass post-remediation | completed | Fresh three-reviewer pass reported no actionable findings (correctness, security, reliability). | Reviewers `3a775cfb`, `d84b1c07`, `ada01835` |
