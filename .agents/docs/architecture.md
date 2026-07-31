# Architecture

## Purpose

This document maps the current slim `MCPContentSearch` architecture. Harness
planning and review use it to keep changes inside the focused MCP retrieval
scope and to catch contract or data-safety regressions. It is the single
maintained design reference beyond the README.

## Runtime Structure

`MCPContentSearch` has a Python FastMCP process and a separately supervised
Python sync-worker process.

- MCP server: `main.py` creates a `FastMCP` server named
  `content-search-server`.
- Durable sync worker: `python -m indexing.sync_worker` runs the generic
  single-job worker loop. On macOS, a user LaunchAgent keeps it alive
  independently of the MCP process.
- MCP tools: `api/tools.py` registers the retained ContextWiki MCP tools:
  `list_sources`, `sync_source`, `sync_all`, `get_sync_status`,
  `search_context`, `search_documents`, `list_documents`, and `fetch_context`.
- Configuration: `environments/config.py` contains `AppConfig`, source
  connector settings, metadata DB path, and Chroma setup.
- Secrets/environment loading: `environments/token.py` and runtime environment
  helpers. Raw tokens must not be persisted or logged.
- Shared models/errors/utilities: `core/`.
- Fetching: `fetching/` owns Notion, Tistory, GitHub, and Obsidian source
  fetching and connector registration.
- Indexing: `indexing/` chunks documents, detects unchanged/reindexed content,
  writes vectors to Chroma/LlamaIndex, and coordinates lifecycle metadata.
- Search: `search/` provides SQLite-gated chunk retrieval, grouped
  document-browsing retrieval, ranking, and citation answer scaffolding.
- Persistence: SQLite metadata via `storage/metadata_store.py` plus ChromaDB via
  `chromadb.PersistentClient`, defaulting to local user storage unless tests
  provide temporary paths.
- Process supervision: the version-controlled LaunchAgent template and helper
  scripts under `deploy/launchd/` and `scripts/` render absolute runtime paths
  but do not persist source credentials.

FastMCP and the durable worker each build their own configuration, connector
registry, and ingestion dependencies once at process startup. They do not
hot-reload `.env` or source targets. Operators must restart both processes
after source configuration changes so enqueue eligibility and worker dispatch
cannot disagree.

SQLite access is operation-scoped. Each metadata operation owns a short-lived
connection, uses the connection transaction boundary for commit or rollback,
and closes the connection deterministically before returning. Long-running MCP
or worker processes must not rely on Python garbage collection to release
SQLite file descriptors.

The LaunchAgent is only a process supervisor. It is not a queue, scheduler
database, or lifecycle authority. SQLite remains authoritative when either
process restarts. Its long-lived Python logs use a bounded rotating file
handler under the local application directory. A small launch wrapper captures
`uv`, import, and uncaught startup stderr before Python logging is available
through a bounded streaming writer that retains only the latest diagnostic
bytes during execution. Once full, the writer compacts to a half-size retained
tail before resuming appends, amortizing compaction work under sustained
output. Before any startup output reaches that bounded file, a fail-closed
line-stream sanitizer removes credentials, complete Cookie/Set-Cookie values,
provider URLs, POSIX paths, and Windows drive, UNC, or extended paths. launchd
stdout/stderr are not used as unbounded persistent logs. The sanitizer runs
through the same validated absolute `uv` and repository environment as the
worker, so the LaunchAgent does not depend on a separate system `python3`.
Sanitizer import/runtime failure appends only a fixed bounded diagnostic and
never the rejected raw stream. The wrapper latches `SIGINT`/`SIGTERM` received
during worker startup, replays the first pending signal to the worker process
group as soon as its PID is known, and waits interrupt-safely for both worker
and diagnostic-writer cleanup before exiting. Signals received while the
writer drains do not orphan it or delete its temporary chunk early. The
retained Python log filter suppresses dependency INFO noise and redacts
credentials, provider URLs, local paths, and formatter-added stack or
exception context before rotating output is written.
The installer owns and secures the default log directory to mode `0700`. It
creates a new custom log directory privately, but it never silently changes an
existing custom directory: that directory must use a canonical absolute path
without symbolic-link components, have mode `0700`, be owned by the current
user, and be writable and searchable by that user, or installation fails
before `launchctl` is called.

An identical LaunchAgent install checks live service state: a loaded service is
left untouched, while an unloaded service is bootstrapped from the existing
plist. An explicit changed-config restart is transactional at the plist/service
boundary. If the replacement cannot bootstrap, the installer restores the
previous plist and its prior loaded state. A loaded service with a missing
plist requires explicit `--restart` before replacement bootout. Since no prior
plist is available in that state, replacement bootstrap failure leaves the
service unloaded and cannot restore its earlier configuration automatically.
During a changed-config transaction, catchable `SIGTERM`/`SIGINT` is latched
until the tracked `launchctl` child finishes and commit or rollback completes.
The previous snapshot is retained until that outcome is known, and incomplete
rollback preserves the snapshot for diagnosis. `SIGKILL` cannot run shell
cleanup and is outside this guarantee without a separate durable transaction
journal.
Install, restart, and uninstall share one exclusive per-label operation lock.
The lock spans the first live-service/plist state read through the final
mutation, commit, or rollback, so concurrently invoked helpers cannot apply a
decision made from a stale snapshot. While a helper waits for `launchctl`, the
lock atomically publishes both the helper and child PID/process-start
identities. A killed helper therefore cannot make the lock recoverable while
its already-running service mutation remains alive; dead or PID-reused child
identity is required before reclaim. Outside that window, a dead lock owner is
reclaimed only when its PID is absent or its recorded process start no longer
matches. Malformed or otherwise unpublished ownership younger than a
conservative 60-second grace is never deleted, and the current helper fails
after its shorter bounded wait. This protects an owner still publishing its
identity. A later helper may recover an ownerless or partially published
directory only after that portable mtime grace has elapsed. The exclusive
`.reclaim` directory used to serialize that recovery publishes its own PID and
process-start identity under the same rules. A crashed recovery with a dead
published owner is reclaimable immediately; an ownerless or partially
published recovery marker is reclaimable only after the same mtime grace,
while fresh or live markers remain protected. Symbolic-link, non-directory, or
foreign-owned recovery markers are rejected without touching their targets.
The status helper remains read-only and does not take this lock.

LaunchAgent removal targets the stable `gui/<uid>/<label>` service identifier,
not the plist path, so a loaded service remains removable after its plist is
missing.

Persistent worker INFO logs are limited to project aggregate counts and
source/job lifecycle. Third-party INFO is suppressed. Per-document, per-chunk,
Notion page, and Notion block identifiers are absent or DEBUG-only because
source identity—especially Obsidian identity—can contain local filesystem
paths. A common handler privacy filter redacts HTTP(S) URLs, token-like values,
and paths from retained context. It applies the centralized credential
sanitizer to the complete record first, so multiword Authorization
Bearer/Basic, Cookie, and API-key values cannot leave trailing fragments.
The same shared sanitizer runs before error text is persisted in SQLite and
again at the MCP response boundary, so job/source status cannot expose token,
provider URL, or local-path details that the worker log would redact. Cookie
and Set-Cookie values remain fail-closed through their complete header and
folded-continuation boundary, including oversized lines and cookie names that
look like diagnostic fields. Diagnostic fields are retained only after an
unambiguous non-folded line boundary. Unlabelled Windows UNC and extended paths
are treated as local paths.

The current architecture does not include a production Web Console, Auto Wiki
generation, generic website/docs crawling, dynamic web fallback, or legacy
live-search/indexing MCP tools.

## Core Mental Model

The shortest accurate description of the current system is:

```text
configured source sync
-> normalized document identity and content hashes
-> deterministic chunking
-> Chroma semantic retrieval candidates
-> SQLite active-document validation
-> chunk evidence, grouped document browsing, or citation-gated answer helpers
```

Keep these design assumptions aligned with implementation:

- Source sync is the only supported ingestion entrypoint for retained sources.
- SQLite is the lifecycle source of truth for source status, sync jobs,
  document/chunk activity, and citation-safe evidence gating.
- Chroma is the retrieval accelerator, not the final authority on whether a hit
  is still active or citeable.
- `search_context` is the primary chunk-level evidence surface.
- `search_documents` is the grouped browsing surface built from the same
  validated retrieval path. It returns one row per document and exposes the
  selected best-matching chunk's full text as `matched_context`.
- `list_documents` is the query-less browsing surface over all active public
  SQLite documents. It uses deterministic normalized-date ordering and opaque
  keyset pagination rather than Chroma retrieval.
- `search_context` remains a separate chunk-level contract; its preview
  behavior is unchanged.
- `CitationAnswerService.answer_with_citations(...)` is an internal helper
  answer surface built on top of validated evidence, not a separate retrieval
  stack.
- Search uses deterministic local query normalization and retrieval variants.
  There is no LLM query-rewrite step.

## Data Flow

```text
MCP client
  -> FastMCP server in main.py
  -> api/tools.py registered tool handler
  -> enqueue/reuse sync jobs in SQLite or query search/status services

macOS LaunchAgent
  -> generic indexing.sync_worker process
  -> atomically claim the oldest queued SQLite job
  -> service boundary in indexing/storage/fetching
  -> connector fetch plus Chroma/SQLite indexing lifecycle
```

Source status flow:

```text
list_sources / get_sync_status
  -> MetadataStore SQLite source/job metadata
```

Reviewer-facing source/status fields should remain understandable from the
maintained docs. Current status payloads are expected to center on fields such
as:

```text
latest_success_at
latest_failure_at
document_count
chunk_count
latest_failure_reason
stale_cleanup_disabled_reason
```

Those fields explain recent source health, retained indexed volume, and whether
cleanup is intentionally disabled for safety.

When a sync job is queued or actively running, `get_sync_status` exposes its
authoritative SQLite state. Running jobs may additionally expose progress hints
that explain whether the worker is still upstream discovery/fetch-bound or
already indexing. Exact-job mode exposes them under `job`; the
latest-one-source and all-source modes expose them under `latest_job`. Those
hints are intentionally running-only and are suppressed again once the selected
job reaches a terminal state. Maintained reviewer-facing hints include:

```text
phase
upstream_total_pages
upstream_fetched_pages
last_progress_at
status_message
```

Those running-job hints are intentionally limited to `get_sync_status`; they
do not broaden the public `sync_source` or `sync_all` response shapes.

Persisted source `auth_ref` values are references, not secret storage. The only
retained nonempty form is `env:UPPER_CASE_NAME`; direct source upsert or
registration normalizes every other form to empty before SQLite writes. Public
payload formatting independently rejects noncanonical references so legacy or
bypassed rows cannot expose raw credential material.

Persisted job phases use the finite lifecycle vocabulary `starting`,
`discovering_pages`, `fetching_page_content`, `indexing_documents`, `completed`,
and `failed`, plus empty when no phase is set. Storage writes normalize every
other value to empty, and the MCP boundary omits a noncanonical running phase.
Free-form diagnostic text belongs only in the separately sanitized
`status_message` and `error_message` fields.

The numeric hint semantics are phase-aware but should remain monotonic within a
running sync:

```text
discovering_pages:
  upstream_total_pages = discovered-page count so far
  upstream_fetched_pages = 0

fetching_page_content:
  upstream_total_pages = final discovered page count
  upstream_fetched_pages = page bodies fetched so far
```

Queued jobs are deliberately unowned. They remain pending when no worker is
available and must not be reconciled as failed merely because no running owner
exists.

Running-job ownership is part of the status story. A source reports as
effectively blocked when SQLite sees either an active queued job or an active
worker owner/heartbeat for the running job. That guard prevents overlapping
same-source syncs.

That blocked state is intentionally recoverable rather than permanent. Recovery
distinguishes stale jobs, unowned-job grace, dead owners, and the container
PID-reuse case where an old and new container can both appear as PID `1`.
Workers persist a process-start identity with each owner heartbeat. On Linux,
that identity includes the boot id, PID namespace, and process start ticks, so
a changed start identity is definitive evidence of PID reuse only within the
same boot and PID namespace. Likewise, an owner PID reported as absent is
definitive only when the observer is in that same scope. A cross-namespace or
otherwise unknown scope—including a missing, legacy, malformed, or
cross-platform process-start identity observed from Linux or macOS—falls back
to the running job's heartbeat staleness window instead of reclaiming a
potentially live owner immediately.
Linux identities are valid only when all four `linux-v2` fields are present and
the start ticks are a positive ASCII-decimal integer. Darwin identities require
exactly the prefix, positive ASCII-decimal seconds, and ASCII-decimal
microseconds from 0 through 999999. Recognized but malformed identities remain
unknown rather than becoming definitive process mismatches. Numeric fields use
canonical decimal spelling without leading zeroes; Darwin microseconds may be
the exact value `0`.

Source sync flow:

```text
sync_source
  -> IngestionService.enqueue_sync_source() for MCP callers
  -> SourceRegistry connector lookup
  -> MetadataStore atomic enqueue/reuse
  -> return a queued or already-running job immediately

LaunchAgent worker
  -> MetadataStore atomic oldest-job claim
  -> queued -> running with worker owner, pid, and heartbeat
  -> blocking IngestionService execution for that exact claimed job
  -> Notion, Tistory, GitHub, or Obsidian connector fetch
  -> DocumentChunker
  -> ContentIndexer and Chroma collection
  -> MetadataStore SQLite source/job/document/chunk/tombstone metadata
  -> get_sync_status(source_id, job_id) reads that exact job's completion
```

If the source already has a queued or running job, `sync_source` returns that
exact active job instead of creating a duplicate. Under the same SQLite write
transaction, a new disabled-source request is inserted directly as terminal
failed rather than becoming claimable queue work. A disabled source or enqueue
failure may therefore return a failed terminal result without entering the
worker lifecycle. The MCP process never silently falls back to an in-process
long-running task when the worker is unavailable.

Retained sync safety rule:

- Tombstoning stale documents is allowed only for cleanup-capable sources after
  a complete successful snapshot. Failed or incomplete syncs must not tombstone
  documents simply because they were absent from a partial fetch.

Bulk source sync flow:

```text
sync_all
  -> enumerate retained configured sources
  -> enqueue or reuse one durable job per selected source
  -> preserve per-source queued/running guards in SQLite
  -> return before the worker claims or completes those jobs
  -> report each launch as started, already_running, skipped, or failed
  -> aggregate launch acceptance as accepted, partial, or failed
  -> caller keeps each started or already_running {source_id, job_id} as a
     completion target
  -> caller reports skipped and failed launches without treating them as
     pending work
  -> caller issues short, separate
     get_sync_status(source_id=..., job_id=...) requests
  -> caller reads the exact job rather than a newer latest_job
  -> caller repeats with paced, capped backoff while a target job remains
     non-terminal and the observation bounds allow
  -> succeeded or failed is the terminal per-source completion result
```

The retained observation policy starts at a 2-second interval, backs off with a
10-second cap, and has one overall 5-minute deadline measured from the start of
completion observation after `sync_all` returns. A target stops after three
consecutive status errors or responses with no exact `job`; a successful
exact-job response resets its consecutive error count. At the deadline, the
caller reports still-running `{source_id, job_id}` values without cancelling
their background syncs and may resume observation later with the same IDs. Each
status invocation is an independent MCP request initiated by the client or
agent. The server does not automatically schedule later calls or push a
completion notification.

When `job_id` is omitted, `get_sync_status(source_id)` retains the existing
latest-one-source response with `latest_job`, and omitting both arguments
retains the existing all-source response. Those modes describe current state;
they are not used to attribute completion to a retained `sync_all` job.

ADR 0009 governs exact-job completion observation through short, paced
`get_sync_status(source_id, job_id)` requests. ADR 0010 separately governs
durable execution ownership: SQLite queue/claim/heartbeat state and the
LaunchAgent-supervised worker, not the observing MCP caller, own execution.

The generic worker claims one job at a time across Notion, Tistory, GitHub, and
Obsidian. This conservative default avoids concurrent writes through shared
Chroma and connector state. Source-specific fetching remains inside each
connector; the durable ownership model is common to every retained source.

MCP and worker shutdown have different semantics:

- MCP shutdown does not own or cancel a worker job.
- A graceful worker `SIGINT`/`SIGTERM` finalizes its in-flight job as failed
  without authorizing tombstones from a partial snapshot.
- An abrupt worker death leaves a running owner/heartbeat record. Existing
  orphan recovery marks that work failed after the owner is no longer live; v1
  does not automatically resume a partially executed job.
- A later caller may enqueue a fresh sync after the failed/orphaned job is
  reconciled.

Retrieval and answer flow:

```text
search_context
  -> ContextSearchService
  -> deterministic query normalization and retrieval variants
  -> Chroma/LlamaIndex candidate retrieval
  -> SQLite active-hit and inclusive normalized-date validation
  -> deterministic ranking and result selection
  -> chunk-level structured search result payload

search_documents
  -> ContextSearchService
  -> same deterministic validated retrieval path
  -> Chroma/LlamaIndex candidate retrieval
  -> SQLite active-hit and inclusive normalized-date validation
  -> group by document_id
  -> choose highest-ranked representative chunk per document
  -> optionally sort the bounded semantic matches by normalized document time
  -> expose that chunk text as matched_context
  -> grouped document-browsing payload

list_documents
  -> MetadataStore active public document listing
  -> inclusive normalized-date and source filtering
  -> deterministic normalized-date ordering with nulls last
  -> opaque keyset cursor pagination
  -> browse-safe metadata payload without content or local paths

fetch_context
  -> MetadataStore direct document/chunk hydration
  -> optional drill-down to stored document content and chunks, or one chunk

internal helper answer flows
  -> CitationAnswerService
  -> search_context_for_answer / search_context
  -> MetadataStore-validated evidence chunks
  -> citation-gated answer payload
```

## Module Responsibilities

- `api`: MCP-facing tool contracts, parameter defaults, result formatting, and
  caller-visible error messages. It delegates sync and search orchestration to
  services, while using `MetadataStore` directly for limited source/status
  reads, `list_documents`, and `fetch_context` document/chunk hydration.
- `fetching`: Notion, Tistory, GitHub, and Obsidian content retrieval plus
  source connector registration. It owns API-specific or filesystem-specific
  parsing, bounded fetch behavior, and partial failure handling. Internal
  Notion/GitHub target parsing helpers are implementation utilities only; the
  retained MCP surface is still configured source sync through `sync_source`.
  Obsidian is a configured local-vault Markdown source, not a live Obsidian app
  or plugin integration.
- `indexing`: durable worker polling/dispatch, document indexing lifecycle,
  deterministic chunking, content hash/chunk-id comparison, Chroma mutation,
  index status updates, and worker-owned execution under SQLite queue, claim,
  heartbeat, and per-source concurrency guards.
- `search`: query orchestration, ranking, metadata fallback, SQLite-backed
  active-result validation, and internal citation answer support.
- `storage`: SQLite source/job/document/chunk lifecycle metadata, normalized
  document times, tombstones, sync-job ownership, active retrieval/date checks,
  deterministic document listing, and direct stored document/chunk hydration
  used by `fetch_context`.
- `core`: stable shared data models, exception classes, and utility functions.
- `environments`: configuration defaults, Chroma setup, API version constants,
  and environment-token access.
- shared runtime composition: builds the same config, source registry, chunker,
  indexer, and metadata store for either process without importing an executing
  FastMCP transport.
- `main.py`: FastMCP composition and server startup only.
- `deploy/launchd` and LaunchAgent helper scripts: macOS user-process
  supervision and diagnostics. They are not ingestion or persistence layers.

New behavior should start in the module that owns the relevant responsibility.
Avoid adding cross-module shortcuts in `api/tools.py` when a service boundary is
more appropriate.

## MetadataStore Maintainability Boundary

`storage/metadata_store.py` currently centralizes several SQLite concerns in one
large class. That is a known maintainability risk, but it is not permission to
replace the store, alter its public methods, or change the database schema in a
single redesign. A safe future extraction should preserve these responsibility
boundaries:

- connection setup plus operation-scoped transaction, commit/rollback, and
  deterministic close behavior
- source registration/status plus sync-job ownership, heartbeat, recovery, and
  terminal lifecycle
- document and chunk lifecycle reads/writes, including tombstones and the
  active-document/active-chunk gates used before retrieval evidence is returned

Move one boundary at a time behind the existing `MetadataStore` interface.
Each stage must preserve current method signatures, transaction semantics,
exception behavior, SQL schema, and caller-visible MCP payloads. Verify every
stage first with focused storage tests against temporary SQLite databases, then
with the affected sync/retrieval contract and functional E2E tests. Do not
inspect or migrate user databases as part of an internal extraction; any later
schema or user-data migration requires a separate explicit plan with rollback
and compatibility coverage.

## Incremental Indexing and Tombstone Safety

The retained ingestion model depends on stable document identity plus cautious
cleanup:

- Source connectors should preserve stable document identity fields such as
  source-specific external ids, canonical URLs, and version or freshness
  markers when available.
- Indexing compares content hashes and chunk ids so unchanged documents can skip
  unnecessary vector rewrites.
- Reappeared or reactivated documents should return to the active set through a
  normal successful sync rather than through ad hoc metadata repair.
- Tombstone and stale-cleanup behavior is safety-gated. Missing documents may be
  marked stale only after a cleanup-capable source completes a full successful
  snapshot. Failed, partial, or byte/file-limit-truncated snapshots must not
  tombstone documents simply because they were absent from that incomplete run.

## Source Identity and Chunking Model

The retained indexing model distinguishes document management from chunk
retrieval:

- `DocumentModel` is the sync and lifecycle unit.
- Chunks are the search and citation unit.
- Chunk metadata carries the reviewer-visible citation context used by
  `search_context`, `search_documents`, and internal helper-answer flows.

Stable identity and version expectations stay source-aware:

- Notion: page id drives stable identity; creation/edit times become
  `published_at`/`modified_at` with `date_provenance="notion"`. During fetch,
  Notion skips `fetch_block_content` when an active stored document already has
  non-empty content and a canonical `modified_at` that matches the page
  `last_edited_time` (or `created_time` when edit time is absent), reusing the
  stored body instead of re-downloading unchanged pages. Existing documents are
  loaded after search for the searched page ids only via one batched
  metadata read of skip/reuse fields (`content`, `modified_at`,
  `content_hash`, `deleted_at`) rather than per-id full-row gets or a
  full-corpus browse; skip equality uses the public
  `MetadataStore.canonical_document_timestamp` helper. Skipped pages still
  return in the fetch snapshot so stale cleanup can refresh `last_seen` and
  tombstone only truly missing remotes.
- Tistory: `blog_name:post_id` drives stable identity; the upstream publication
  time becomes `published_at` with `date_provenance="tistory"` when present.
- GitHub: repository path drives stable identity, while blob SHA is revision
  metadata in `version_id`, never a normalized modification timestamp.
- Obsidian: relative note path drives stable identity, while the
  `obsidian://open` URL stays the citation-friendly canonical URL and filesystem
  mtime becomes `modified_at` with `date_provenance="filesystem"`.

The lifecycle fields that matter for reviewer understanding are:

```text
external_id
document_id
canonical_url
version_id
published_at
modified_at
indexed_at
date_provenance
last_seen_at
last_seen_sync_id
deleted_at
```

Current chunking remains deterministic and source-aware:

- heading-based markdown chunking when structure exists
- deterministic plain-text fallback windows when structure does not
- line-range-preserving code chunking for citeable code evidence

Representative citation metadata per chunk should remain understandable from the
maintained docs:

```text
chunk_id
document_id
source_id
title
url
path
chunk_index
line_start
line_end
content_hash
version_id
updated_at
published_at
modified_at
indexed_at
date_provenance
```

That deterministic chunking plus stable identity is what makes unchanged-doc
skip behavior, reappeared-document recovery, and citation stability predictable
across syncs.

## Four-Layer View

ContextWiki is easiest to reason about as four layers:

```text
MCP client
-> FastMCP tool surface
-> durable SQLite job handoff or search services
-> generic worker and ingestion services
-> SQLite metadata plus Chroma retrieval storage
```

That division is intentional:

- MCP clients interact with tools, not storage internals.
- Service boundaries own ingestion, retrieval, ranking, and answer assembly.
- Chroma finds semantically relevant candidates.
- SQLite decides whether those candidates are still active, valid, and safe to
  return as evidence.

## MCP Tool Contract

Current tools:

- `list_sources() -> dict`
- `sync_source(source_id: str) -> dict`
- `sync_all() -> dict`
- `get_sync_status(source_id: str = "", job_id: str = "") -> dict`
- `search_context(query: str, filters: SearchFilters | None = None, top_k: int = 10, include_debug: bool = False) -> dict`
- `search_documents(query: str, filters: SearchFilters | None = None, sort_by: SearchSortBy = "relevance", sort_order: SortOrder = "desc", top_k: int = 10) -> dict`
- `list_documents(filters: SearchFilters | None = None, sort_by: DocumentSortBy = "indexed_at", sort_order: SortOrder = "desc", page_size: int = 20, cursor: str | None = None) -> dict`
- `fetch_context(document_id: str = "", chunk_id: str = "") -> dict`

Contract intent:

- `SearchFilters` exposes `source_id`, `source_ids`, `published_from`,
  `published_to`, `modified_from`, `modified_to`, `indexed_from`, and
  `indexed_to`. Date/time bounds are inclusive, normalized to UTC, and
  validated as ordered ranges. Offset-free timestamps are treated as UTC and
  date-only values start at midnight UTC. `source_id` and `source_ids` are
  single-source and multi-source alternatives; if both are supplied, all
  nonblank ids form one deduplicated union. Unknown filter fields are rejected.
- `search_context` remains the relevance-ordered chunk-level evidence and
  citation surface. Date filtering is part of the SQLite-authoritative
  candidate gate, and bounded candidate expansion happens before final
  `top_k` truncation when early semantic candidates are out of range.
- `sync_all` is an aggregate orchestration helper, not a separate ingestion
  stack. It enqueues or reuses retained-source durable jobs, preserves each
  source's queued/running guard, and returns after acceptance decisions instead
  of waiting for worker claim or ingestion completion. Per-source
  `launch_outcome` values are `started`, `already_running`, `skipped`, or
  `failed`; the aggregate launch status is `accepted`, `partial`, or `failed`.
  Completion-seeking callers retain `{source_id, job_id}` only from
  `started`/`already_running` results, report `skipped`/`failed` launches
  immediately, and do not poll a retained result that lacks a job ID.
- `get_sync_status(source_id, job_id)` returns the exact public sync job under
  `job`; it must not substitute a newer `latest_job`. Completion observers use
  short separate requests with a 2-second initial interval, capped backoff up
  to 10 seconds, and one overall 5-minute deadline. They stop a target after
  three consecutive status errors or missing exact jobs, report observation
  uncertainty without substituting latest state, and report still-running job
  IDs at the deadline without cancellation. Observation may resume later with
  the same exact IDs. Repeating requests is client or agent behavior, not
  automatic client scheduling, server-side waiting, or server push.
- Existing calls that omit `job_id` keep their response shapes:
  `get_sync_status(source_id)` returns one source plus `latest_job`, while
  `get_sync_status()` returns all public sources plus each `latest_job`. These
  modes are current-state inspection, not exact completion attribution.
- `search_documents` is document-oriented: it uses the same retained-source
  retrieval path but returns one representative chunk-backed row per document
  for browsing. Its public result contract intentionally replaces the earlier
  `preview` field with the representative chunk's full text in
  `matched_context`. It defaults to relevance and can sort its bounded semantic
  matches by `published_at`, `modified_at`, or `indexed_at`, ascending or
  descending, with document-id tie breaking and null timestamps last. It does
  not promise a global date ordering over documents outside the retrieved
  semantic candidate set.
- `list_documents` is the global active-document date-browsing contract. It
  takes no semantic query, supports the same source/date filters, sorts by
  `published_at`, `modified_at`, or `indexed_at`, and returns `documents` plus
  an opaque `next_cursor`. MCP `page_size` is bounded to 1 through 50; cursors
  must be reused unchanged with the same filters and sort settings. Its public
  document rows include only `document_id`, `source_id`, `title`, `url`,
  `canonical_url`, `platform`, the three normalized timestamps, and
  `date_provenance`.
- Public `search_context` and `search_documents` results also expose the three
  normalized timestamp fields plus `date_provenance`; they do not reinterpret
  legacy `date` or `updated_at`.
- `fetch_context(document_id)` remains an optional drill-down when the caller
  needs the selected document's stored content and chunks. Direct
  `fetch_context(chunk_id=...)` lookup remains supported.
- Internal `CitationAnswerService.answer_with_citations(...)` reuses
  `search_context_for_answer` / `search_context`, so deterministic retrieval
  semantics stay aligned across search and helper-answer flows.
- `search_context` returns a `debug` key on configured search-service paths.
  On the normal path, `include_debug=False` leaves that key as `{}`, while
  `include_debug=True` populates it with structured retrieval detail. The
  current service-unconfigured fallback returns only `query` and `results`.
- The current public exception is `search_context`'s `no_matching_sources`
  fast path, which still returns a small populated `debug` object even when
  `include_debug=False`.
- Internal helper-answer flows keep `include_debug` as a true opt-in debug
  surface, do not mirror the `no_matching_sources` exception path, and do not
  guarantee debug fields on default or service-unconfigured paths.
- Retrieval policy keeps vector retrieval, metadata fallback, SQLite
  validation, and rerank/debug reporting as distinct, inspectable concerns.
- When the active FastMCP-compatible decorator supports annotations,
  `search_context`, `search_documents`, `list_documents`, and `fetch_context`
  advertise `readOnlyHint=False`, `destructiveHint=False`, and
  `idempotentHint=False`. Their caller-visible purpose is retrieval, but the
  shared metadata-store path can initialize additive SQLite schema and refresh
  sync-owner heartbeat metadata, so read-only or idempotent hints would be
  inaccurate.
  `search_context` and `search_documents` advertise `openWorldHint=True`
  because default embeddings may send queries to an external provider;
  `list_documents` and `fetch_context` advertise `openWorldHint=False`.
  `list_sources` and `get_sync_status` do not advertise read-only/idempotent
  hints even though observation overlays configured registry state in memory
  without persisting that overlay. `get_sync_status` may still reconcile
  running-job lifecycle through `get_latest_sync_job` / schema init, so
  read-only or idempotent hints would remain inaccurate. Sync tools also
  remain mutating operations.

Retained debug-oriented answer inspection surfaces should stay documented and
stable enough for local evaluation and reviewer use:

- `search_context` debug explains deterministic retrieval and ranking decisions.
- Current reviewer-facing search debug commonly includes retrieval query and
  result-selection surfaces such as `retrieval_queries` and
  `selected_results[]`.
- Deterministic intent policy should remain readable in debug output when
  present. The current retained intent vocabulary includes `strict_lookup`,
  `broad_topic`, `list`, and `comparison`, and that intent is reused by
  ranking and grounded answer rendering.
- `CitationAnswerService.answer_with_citations(...)` exposes helper-answer
  inspection surfaces such as `citations`, `used_chunks`, `debug`, and
  `debug_markdown` when the current implementation returns them.
- Public debug payloads may also surface deterministic intent and retrieval
  inspection sections such as `intent.*`, `retrieval_queries`, and
  `selected_results[]` so reviewers can explain why a grounded result set was
  chosen.
- Eval and reviewer workflows should be able to explain why a retrieval or
  answer path was chosen without reading raw vector-store internals.

When changing a tool:

- Keep names, parameters, return types, and error vocabulary stable unless the
  user requested a contract change.
- Update README or client-facing docs when behavior changes.
- Ensure exceptions do not expose tokens, filesystem secrets, or full local data
  paths unnecessarily.
- Treat source sync completion/status as caller-visible behavior. If a workflow
  returns before all work is complete, status reporting must remain truthful.

## Persistence and Local Data

ChromaDB stores indexed user content for semantic retrieval. SQLite stores
ContextWiki source/job/document/chunk lifecycle and citation metadata.

- Do not delete, reset, or inspect local Chroma data or SQLite metadata without
  explicit user approval, a plan, and user-visible rationale.
- Tests should prefer temporary paths or mocks when touching Chroma or SQLite
  metadata.
- Indexing changes must preserve document identity and content hash semantics
  unless the plan explains migration or reindexing behavior.
- If a change requires reindexing, the plan and final report must include
  user-data impact and rollback/mitigation notes.

SQLite is the authoritative active-document gate. Stale Chroma hits must be
filtered through SQLite metadata before being returned as evidence. It is also
the authoritative normalized-date filter and query-less listing store.

The `published_at`, `modified_at`, `indexed_at`, and `date_provenance` columns
are added through the existing additive-column schema initialization. Normal
future sync/indexing populates them, legacy `date`/`updated_at` remain intact,
and existing Chroma vectors do not require reindexing. Existing rows can keep
empty normalized source timestamps until later normal ingestion updates them.

GitHub stale cleanup remains repository-prefix scoped under the shared
`source_github` source id. GitHub targets are resolved during each sync, and
cleanup prefixes are derived only from the exact repositories successfully
resolved for that snapshot. A sync for one configured repository must not
tombstone documents that belong to another repository prefix. Owner discovery
must never enable broad owner-prefix cleanup: a historical or private
repository absent from the current token-visible result remains outside the
cleanup scope. A repository confirmed empty by GitHub metadata is a complete
zero-document scope whose exact prefix can tombstone its previous documents
after a complete sync; missing or ambiguous empty metadata is not assumed
complete and cannot enable cleanup for that apparent empty state.

Soft-delete provenance matters here: SQLite tombstone metadata must remain able
to suppress stale managed vector hits even when best-effort vector cleanup
cannot remove every old Chroma candidate immediately.

## External Services and Local Sources

Current integrations and local configured sources:

- Notion API, configured by environment token and API version.
- Tistory, configured by blog name and bounded post fetching.
- GitHub repositories, configured by comma- or newline-separated targets and
  optional `GITHUB_TOKEN`. A bare owner target discovers repositories owned by
  that account at each sync: public repositories via `/users/{owner}/repos`,
  and when a token is set, authenticated extras via owner-scoped
  `/orgs/{owner}/repos` for organizations or, when the authenticated login
  matches a personal owner, `/user/repos?affiliation=owner` (with
  `visibility=all`). Discovery does not paginate the token's global
  `/user/repos?visibility=all` set. Each discovered repository uses its GitHub
  API-reported default branch. An `owner/repo` target bypasses owner discovery
  and uses `CONTEXTWIKI_GITHUB_DEFAULT_REF`, while `owner/repo@ref` uses the
  explicit ref. Mixed targets resolve to exact repository identities before
  fetching and must not overlap; an exact target does not override the ref of
  the same repository discovered by an owner target, and the duplicate identity
  fails the sync. The fetcher considers supported text/code files only and
  defaults to 200 eligible files per repository and 512000 bytes per file;
  bound-driven omissions make the snapshot incomplete and disable stale
  cleanup.
  Each owner-listing endpoint (`/users/{owner}/repos`, `/orgs/{owner}/repos`,
  and affiliation-scoped `/user/repos`) is bounded to 100 pages of 100 returned
  items, up to 10,000 per endpoint. A full page 100 leaves completion unproven,
  so the sync fails before repository indexing rather than accepting a partial
  list, and stale cleanup remains disabled.
- Obsidian local vaults, configured by `CONTEXTWIKI_OBSIDIAN_VAULT_PATH`,
  `CONTEXTWIKI_OBSIDIAN_MAX_FILES`, and
  `CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES`. Obsidian sync reads bounded Markdown
  notes from the filesystem and does not require a live Obsidian app. If the
  file count or file byte bound is exceeded, the sync fails as an incomplete
  snapshot before stale cleanup. Real vault validation requires both explicit
  user approval and a plan; tests must use temporary vaults.
- A disabled retained source blocks future sync attempts but does not
  automatically hide already indexed active documents. Those documents remain
  retrievable until later cleanup or metadata changes mark them inactive.
- Embedding behavior is inherited from the LlamaIndex runtime/default
  configuration, and the repository's default server setup resolves to OpenAI
  embeddings. Unless the embedding settings are overridden, indexing may send
  document chunks and search may send queries to that provider. The local demo
  and embedding-dependent E2E tests explicitly inject `MockEmbedding` instead.
  This project does not expose a separate embedding-provider switch in
  `AppConfig`. Fully local operation requires local or otherwise non-egress
  embeddings.

Testing should prefer mocked external APIs and temporary local vaults. Live
network or real-vault validation requires both explicit user approval and a
plan and must not print credentials or local path details. Plan-exempt work
must reclassify before the live check or keep it `blocked/gated`.

Owner-wide GitHub sync is intentionally an explicit configuration choice. It
can increase GitHub API requests, sync duration, indexed volume, and embedding
provider cost in proportion to the repositories and bounded files discovered.
Automated verification uses fake GitHub responses and temporary stores; it does
not perform live owner discovery or mutate configured user stores.

## Configuration and Secrets

- `environments/token.py`, `.env`, shell environment variables, and API keys are
  sensitive.
- Do not add secret values to docs, tests, logs, screenshots, or examples.
- If a configuration default changes long-term behavior, update this document in
  the same work item.

## Error Handling

Domain exceptions live in `core/exceptions.py`.

- Fetching errors should be classified close to fetchers.
- Search errors should not leak implementation details to MCP clients.
- Indexing errors should update status before surfacing a failure.
- Tool handlers may return user-readable messages, but logs should preserve
  enough context to debug without exposing secrets.

## Testing Strategy

Use the smallest useful check first.

The maintained verification model is layered and test-first:

- docs-only verification for README, harness docs, plans, and other markdown
  changes
- Red-Green-Refactor for feature and behavior changes: write or update unit,
  integration, and deterministic functional E2E coverage before production
  code; record the RED command, tests/layers, non-zero exit, expected failure
  signature, and ordering; make the minimum implementation pass; refactor while
  focused tests stay green; then rerun affected tests
- focused syntax, import, or targeted pytest checks for the directly changed
  modules
- retained functional E2E coverage for MCP-visible sync/search/fetch
  workflows plus internal helper-answer coverage where retained tests depend on
  `CitationAnswerService`
- mandatory full-wrapper verification through `./scripts/verify_all.sh` for
  every code-changing work item after refactor and before review or delivery
- optional manual live smoke through `scripts/live_query_smoke.py` only when the
  user explicitly approves real configured-source validation and a plan records
  its source/data/rollback scope
- retained local eval surfaces such as `python scripts/run_contextwiki_eval.py`
  when the change affects a quality-sensitive retrieval or answer surface that
  already has modeled eval coverage
- retained eval or higher-level verification only when the change touches an
  already-modeled quality-sensitive surface such as retrieval or answer quality

- Docs-only changes: path listing, `git status --short --branch`,
  `git diff --check`, then stage relevant docs-only files and run
  `git diff --cached --check` so new docs are covered.
- Syntax/import safety:
  `python -m compileall api core environments fetching indexing search storage main.py`.
- Unit/integration tests: `uv run --locked pytest -m "not live"` when the uv
  workspace is healthy; feature and behavior changes must cover both layers.
- MCP contract: focused tests around `register_tools` and retained tool
  functions. The strongest public contract layer uses real
  `FastMCP.call_tool(...)` payload checks rather than only internal helper
  assertions.
- Search/indexing/storage: temp Chroma path, temp SQLite path, or mock
  collection; avoid user data.
- Fetching: mocked Notion/Tistory/GitHub responses and temporary Obsidian vaults;
  live API or real-vault checks only with explicit approval and a plan.
- Functional E2E: `./scripts/verify_functional_e2e.sh`, which must cover
  retained MCP sync/search/fetch paths, grouped document browsing, and any
  retained internal helper-answer flows without browser, wiki, live API, or
  LLM dependencies.
- Full wrapper: `./scripts/verify_all.sh`, which includes compile, lint, type,
  non-live pytest, deterministic local evaluation, and the functional E2E gate;
  it is mandatory for every code-changing work item.
- Manual live smoke: `python scripts/live_query_smoke.py`, only with explicit
  approval and a plan because it can touch real configured sources or local
  user data.
- Retained eval runner: `PYTHONPATH=. python scripts/run_contextwiki_eval.py`
  or the repo wrapper that invokes it, used when a work item changes retrieval
  or answer quality on a modeled local eval surface.
- Deterministic reviewer-visible eval artifacts should stay separate from
  optional runtime or latency metrics such as `runtime_metrics.json` so repeated
  runs remain comparable.

## Harness Usage

`harness-plan` must read this document before choosing implementation
boundaries. Review gates must check changed files against this architecture.
