# ADR 0009: Exact Sync Job Status Observation

## Status

accepted

## Date

2026-07-30

## Context

ADR 0007 made public `sync_source` an immediate-return background launcher, and
ADR 0008 applied the same background-launch model to `sync_all`. Their original
completion guidance observed each source's latest job. That is not a stable way
to attribute completion to a particular launch: job A can finish and job B can
become the source's latest job before the caller's next status request.

A single long-lived MCP wait can exceed client or transport request deadlines.
Unbounded short polling avoids that one request but can still run forever or
create unnecessary load. Completion observation therefore needs an exact job
identity, short independent requests, and explicit pacing and termination
bounds. The existing per-source SQLite jobs remain authoritative; this decision
does not require a batch table, scheduler, notification channel, or server
push.

## Decision

Completion-seeking callers use exact-job observation:

- After `sync_source`, retain the returned `source_id` and `job_id`. If the
  launch returns a running job, call
  `get_sync_status(source_id=..., job_id=...)` and read `job`, not
  `latest_job`.
- After `sync_all`, retain `{source_id, job_id}` only for results whose
  `launch_outcome` is `started` or `already_running`, using
  `results[].source_id` and `results[].job.job_id`.
- Report `skipped` and `failed` bulk launch outcomes immediately and do not
  poll them. If a launch that should be observed has no job ID, report that
  exact observation is unavailable and do not fall back to latest-source
  status.
- `get_sync_status(source_id, job_id)` returns the selected public job under
  `job`. It never substitutes a newer `latest_job`.
- Invalid, missing, non-public, or source/job-mismatched exact selectors use
  the uniform null response `{"source": null, "job": null}`. This avoids
  existence disclosure and gives callers one missing-exact-job condition.
- When `job_id` is omitted, the existing modes remain available for current
  status inspection: `get_sync_status(source_id)` returns the source and its
  `latest_job`, while `get_sync_status()` returns all public sources and their
  latest jobs. These modes are not completion attribution for a retained
  launch.

Exact-job observation uses paced, bounded, independent MCP requests:

- Start at a 2-second interval and back off through 4 and 8 seconds, capped at
  10 seconds between observation rounds.
- Use one overall 5-minute observation deadline, measured from the start of
  completion observation after the launch response.
- Stop observing a target after three consecutive status errors or responses
  with no exact `job`. A successful exact-job response resets that target's
  consecutive error count.
- Treat `job.status` values `succeeded` and `failed` as terminal.
- At the deadline, report every still-running `{source_id, job_id}` without
  marking it failed or cancelling it. The background sync continues, and a
  later caller can resume observation with the same exact IDs.
- The client or agent owns the timing and repetition of these calls. The server
  does not automatically schedule another status request or push completion.

## Partial Supersession

This ADR supersedes only the completion-attribution and latest-job polling
wording in ADR 0007 and ADR 0008.

The following earlier decisions remain accepted:

- ADR 0007's public `sync_source` background launcher, internal blocking
  service boundary, per-source SQLite ownership rules, and cancellation
  reconciliation.
- ADR 0008's `sync_all` background-launch aggregation, per-source launch
  outcomes, absence of a batch scheduler/table, and deterministic retrieval
  without LLM query rewrite.

## Consequences

- Completion-seeking clients must retain transient source/job identifiers from
  the launch response instead of rediscovering completion from latest-source
  state.
- Exact-job reads prevent a newer launch from being attributed to the job the
  caller originally started or reused.
- A deadline or repeated observation error yields a truthful incomplete
  observation report, not a false sync failure or cancellation.
- Existing latest-one-source and all-source status shapes remain compatible for
  current-state inspection.
- Contract and deterministic E2E coverage must protect exact-job selection,
  supersession races, uniform null responses, target filtering, bounded
  observation wording, and no-cancellation semantics.
- No SQLite/Chroma schema change, migration, deletion, or user-data inspection
  follows from this decision.

## Alternatives Considered

- Attribute completion through each source's latest job: rejected because a
  newer job can supersede the target between status requests.
- Restore a long-lived bulk wait: rejected because completion would again
  depend on one MCP request outliving client and transport timeouts.
- Poll exact jobs without pacing or a deadline: rejected because a stalled job
  or broken status path could create an indefinite, high-frequency loop.
- Add a batch table, scheduler, or server-push channel: rejected because exact
  per-source SQLite jobs already provide the required completion truth without
  expanding persistence or runtime scope.

## Related

- `.agents/docs/architecture.md`
- `.agents/docs/adr/0002-context-zip-metadata-and-citation-store.md`
- `.agents/docs/adr/0007-sync-source-background-launch-contract.md`
- `.agents/docs/adr/0008-background-sync-all-and-deterministic-retrieval.md`
- `docs/plan/2026-07-30-remove-sync-wait-timeout.md`
