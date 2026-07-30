# ADR 0007: Public `sync_source` Background Launch Contract

## Status

accepted

## Date

2026-06-15

## Partial Supersession

ADR 0009 supersedes only this ADR's original completion-attribution guidance
that polled the source's latest job. The background-launch contract, internal
blocking execution split, SQLite ownership rules, and cancellation
reconciliation decisions remain accepted.

## Context

`MCPContentSearch` originally treated `sync_source(source_id)` as a blocking
end-to-end sync call. That worked for short source fetches, but it breaks down
for larger configured sources such as Notion when the MCP client, transport, or
caller timeout cancels the request before the sync finishes. In the observed
Notion failure, the configured `NOTION_API_KEY` was valid and live requests were
still returning `200 OK`, but the client disconnected during a long sync and
left the SQLite job status in `running`.

The repo already uses SQLite metadata as the authoritative source/job state
store under ADR 0002, and existing MCP clients already depend on the retained
`sync_source` and `get_sync_status` tool names from ADR 0004/ADR 0006 scope.
The fix therefore needs to preserve the current MCP surface while changing the
public completion semantics so long-running syncs survive request lifetime.

## Decision

Keep two distinct sync boundaries:

- Internal direct callers use `IngestionService.sync_source(source_id)` as the
  blocking execution path when they start a new sync themselves or when they
  can join a same-process local background task for that source. If another
  owner already holds the running SQLite job and there is no joinable local
  task, the direct path returns that current running job instead of pretending
  it can block on foreign in-flight work.
- Public MCP callers use `IngestionService.start_sync_source(source_id)` through
  the `sync_source` tool as an immediate-return background launcher.

Under this contract, public MCP `sync_source(source_id)`:

- starts a new background sync job or reuses the active running job for the
  same source
- returns the current job payload immediately, typically with `status=running`
- requires completion-seeking callers to retain the returned `source_id` and
  `job_id`, then observe that exact job through the paced, bounded short-call
  contract in ADR 0009
- must not silently fall back to the blocking path when a background launcher
  is unavailable

Background execution still owns the full fetch, chunk, index, metadata commit,
and stale-cleanup lifecycle. Cancellation or early background failure must
reconcile the job out of `running` through SQLite metadata so later retries are
not blocked by stuck state. When a direct same-process caller encounters a
just-cancelled local background task, it may surface that reconciled failed job
once instead of opening fresh duplicate work immediately, but only while that
same cancelled job is still the latest authoritative SQLite job for the
source. This exception is for cancelled-local handoff only; a successfully
completed background sync must not suppress the next fresh direct sync, and a
newer foreign retry must override the stale local cancelled handoff.

## Consequences

- MCP tool names remain stable: callers keep using `sync_source` and
  `get_sync_status` instead of introducing a new public launcher tool.
- Client-facing docs, architecture notes, and retained MCP contract tests must
  describe and verify the immediate-return plus exact-job observation behavior.
- Review gates must treat public `sync_source` as a contract boundary change,
  not just an implementation detail.
- Background-task cancellation, early startup failure, and overlapping
  same-source launch reuse need focused regression coverage because they can
  otherwise leave stale `running` jobs behind.
- Direct service-level tests and internal call sites can continue to rely on the
  blocking `IngestionService.sync_source()` path when they start the work
  themselves or join a same-process local background task. They must not assume
  cross-process blocking over a foreign running job that only exists in SQLite
  metadata.

## Alternatives Considered

- Keep public `sync_source` blocking and only raise client timeouts: rejected
  because the real failure mode was a cancelled long-running sync leaving
  incorrect SQLite state.
- Add a brand-new MCP tool such as `start_sync_source`: rejected because the
  current retained MCP surface can support the new behavior without expanding
  the public tool set.
- Convert every sync caller to background-only semantics, including direct
  service callers: rejected because internal focused tests and direct service
  flows still benefit from a blocking path with immediate terminal state.

## Related

- `.agents/docs/architecture.md`
- `.agents/docs/adr/0002-contextwiki-metadata-and-citation-store.md`
- `.agents/docs/adr/0004-contextwiki-phase-b-connectors.md`
- `.agents/docs/adr/0006-slim-mcp-core-scope.md`
- `.agents/docs/adr/0009-exact-sync-job-status-observation.md`
- `docs/contextwiki-core-understanding.md`
- `docs/plan/2026-06-15-notion-cancel-sync-stuck.md`
