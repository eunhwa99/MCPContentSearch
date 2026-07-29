# ADR 0010: Durable All-Source Sync Worker

## Status

accepted

## Date

2026-07-29

## Context

ADR 0007 and ADR 0008 moved public source sync to immediate-return background
tasks owned by the FastMCP process. That protects the individual MCP request
from a long fetch, but it does not protect the sync from the lifetime of the
server process. Closing the MCP client, reloading its configuration, or
stopping `uv run --locked python main.py` still cancels work owned by that
process.

The existing SQLite store already owns source/job lifecycle truth, including
running owner identity, heartbeat, guarded document commits, terminal
finalization, and orphan recovery. Chroma remains the vector destination and
retrieval accelerator. Local macOS operation needs durable process ownership
without introducing a network queue or another persistence system.

## Decision

Separate source-sync acceptance from execution:

- FastMCP `sync_source` and `sync_all` atomically enqueue or reuse SQLite jobs
  and return without creating request-process background tasks.
- A generic `python -m indexing.sync_worker` process atomically claims the
  oldest queued job, marks it running with worker ownership, and executes the
  existing fetch/index/finalize lifecycle.
- One worker handles every retained source: Notion, Tistory, GitHub, and
  Obsidian. Source-specific behavior remains in connectors.
- The worker processes one job at a time by default.
- A macOS user LaunchAgent supervises the worker independently from FastMCP.
  Its generated plist contains absolute repository, `uv`, and log paths but no
  credentials. Runtime secrets continue to load from the repository-local
  `.env`.
- FastMCP and the worker each snapshot source configuration at startup.
  Operators restart both after `.env` or source-target changes.
- LaunchAgent worker logs use bounded size rotation rather than unbounded
  launchd stdout/stderr files. A bounded streaming startup diagnostic also
  captures `uv`, import, initialization, and uncaught startup stderr that
  occurs before Python logging is configured without growing unbounded during
  a long-running process. Once full, the diagnostic compacts to a half-size
  retained tail so sustained stderr has amortized rather than per-chunk
  full-tail rewrite cost. The retained Python log filter redacts credentials,
  provider URLs, local paths, and formatter-added stack or exception context.
  The installer secures the default or newly created log directory to mode
  `0700`; it rejects an existing custom directory unless its canonical absolute
  path has no symbolic-link components and the directory already has mode
  `0700`, current-user ownership, and current-user write/search access, without
  mutating that custom directory.
- SQLite remains the queue and lifecycle authority. The LaunchAgent is only a
  process supervisor, and Chroma is not used for ownership.

A graceful worker shutdown fails its in-flight job without tombstoning an
incomplete snapshot. Abruptly orphaned running work uses the existing
owner/heartbeat recovery and is not automatically resumed in this first
version. A valid queued job remains queued when no worker is available.

## Consequences

- Stopping FastMCP no longer stops a job owned by the worker.
- Stopping the worker does stop execution; callers must inspect the terminal or
  recovered failure and explicitly request a fresh sync.
- Newly accepted jobs may be observed as `queued` before becoming `running`.
  Existing queued or running jobs are reused instead of duplicated.
- Public `started` launch vocabulary means accepted into the durable queue, not
  that connector I/O has begun.
- Installation, status, restart, and removal use the stable LaunchAgent label
  `com.eunaverse.contextwiki.sync-worker`.
- Removal boots out the stable service target, so it still works if the plist
  was removed while the service remained loaded.
- Persistent INFO logs avoid document/chunk identifiers because they can
  contain local source paths; third-party INFO is suppressed; Notion
  page/block identifiers are absent or DEBUG-only; and a common handler filter
  applies centralized full-record credential sanitization before redacting
  URLs and paths. Multiword Authorization Bearer/Basic, Cookie, and API-key
  values are therefore removed as a whole. Coalesced or folded
  Cookie/Set-Cookie fragments and unlabelled Windows UNC or extended paths are
  covered at the same boundary. The launch wrapper applies the sanitizer as a
  bounded line stream through the validated `uv` environment before startup
  stderr is persisted; sanitizer failure records only a fixed safe diagnostic,
  and signal handling waits for the writer to drain. The same sanitizer
  protects persisted source/job errors and public MCP status payloads.
- Persisted source authentication metadata is reference-only: nonempty values
  must use canonical `env:UPPER_CASE_NAME` spelling. Job phases use a finite
  lifecycle vocabulary rather than free-form text. Storage normalizes invalid
  values before writes, while the MCP boundary independently rejects invalid
  legacy or bypassed values.
- An identical reinstall leaves a loaded service untouched and repairs an
  unloaded service by bootstrapping the existing plist. A changed plist
  requires an explicit restart action so installation cannot silently
  interrupt in-flight work. If the replacement fails to bootstrap, the
  installer restores the previous plist and loaded state. Replacement bootout
  uses the stable service target. A loaded service whose plist is missing also
  requires explicit `--restart`; without a prior plist, bootstrap failure
  cannot restore the earlier service and leaves it unloaded.
- Install, restart, and uninstall serialize under one per-label operation lock
  from live-state inspection through commit or rollback. Dead owners are
  recoverable using PID and process-start evidence. During a `launchctl`
  mutation the lock also publishes the child PID and process start, so killing
  the helper does not permit overlap while that child remains alive. Fresh
  unpublished owner state times out instead of being deleted; after a
  conservative grace longer than that wait, a later helper can recover an
  ownerless or partially written lock using portable directory age. Recovery
  is itself serialized by a `.reclaim` directory that publishes the same
  owner evidence. A dead published recovery owner is recoverable immediately,
  while ownerless or partial recovery state must exceed the same grace. Unsafe
  recovery-marker paths are rejected without target mutation. Read-only status
  does not take the lock.
- Actual LaunchAgent loading and live configured-source validation can touch
  user stores or external providers and therefore remain approval-gated
  operational actions.
- Exact completion attribution remains ADR 0009's concern. Callers observe
  queued or running durable jobs through paced, bounded
  `get_sync_status(source_id, job_id)` requests; observation timeout or
  cancellation must not own worker cancellation.
- Tests render the plist and exercise the worker with fake connectors and
  temporary stores. They must not call real `launchctl`, inspect user
  SQLite/Chroma data, or contact live sources.

## Alternatives Considered

- Keep MCP-owned background tasks and raise timeouts: rejected because process
  shutdown still cancels the work.
- Create a Notion-only worker: rejected because durable ownership is common to
  all retained sources even though their fetch performance differs.
- Add Redis, Celery, or another queue: rejected because SQLite already owns the
  required local lifecycle and atomic claim boundary.
- Run one worker per source or multiple concurrent jobs: deferred until shared
  Chroma-write and connector behavior justify measured concurrency.

## Supersedes

- ADR 0007's MCP-process-owned background execution decision.
- ADR 0008's statement that background sync survives only while FastMCP lives.

ADR 0008's deterministic retrieval decision remains accepted. ADR 0009's
exact-job status observation contract also remains accepted; this ADR changes
execution ownership, not completion observation.

## Related

- `.agents/docs/architecture.md`
- `.agents/docs/adr/0002-contextwiki-metadata-and-citation-store.md`
- `.agents/docs/adr/0007-sync-source-background-launch-contract.md`
- `.agents/docs/adr/0008-background-sync-all-and-deterministic-retrieval.md`
- `.agents/docs/adr/0009-exact-sync-job-status-observation.md`
- `docs/plan/2026-07-29-launchd-durable-sync-worker.md`
