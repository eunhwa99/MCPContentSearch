# Architecture

## Runtime Structure

```mermaid
flowchart LR
  Client[MCP client] --> FastMCP[FastMCP in main.py]
  LaunchAgent[LaunchAgent / Docker] --> Worker["indexing.sync_worker"]
  FastMCP --> SQLite[(SQLite jobs + lifecycle)]
  Worker --> SQLite
  FastMCP --> Chroma[(Chroma vectors)]
  Worker --> Chroma
  FastMCP --> Search[search / MetadataStore reads]
  Worker --> Index[fetch + index lifecycle]
```

Two processes share one SQLite DB and one Chroma store:

| Node | Meaning |
| --- | --- |
| MCP client → FastMCP | Claude/Cursor calls MCP tools (`sync_*`, `search_*`, …) |
| LaunchAgent / Docker → Worker | Supervises one `indexing.sync_worker` process per store so sync keeps running after the MCP client stops |
| SQLite jobs + lifecycle | Same `MetadataStore`: job queue (enqueue/claim/status) **and** document/chunk/tombstone lifecycle |
| search / MetadataStore reads | FastMCP read path: active-gate search, `list_documents`, `fetch_context`, status |
| fetch + index lifecycle | Worker write path: overlapping connector fetch for up to `N` distinct-source jobs → chunk → Chroma write under in-process mutation lock → SQLite lifecycle → terminal job status |
| Chroma vectors | Semantic candidate store only; SQLite decides what is still active/citeable |

### Workflow

1. Client asks FastMCP to sync → FastMCP **enqueues** (or reuses) a job in SQLite and returns.
2. Worker **claims** queued jobs while `COUNT(RUNNING) < N`, runs fetch + index (fetch may overlap; Chroma mutations serialize in-process), writes Chroma + SQLite lifecycle, marks each job succeeded/failed.
3. Client asks FastMCP to search → FastMCP pulls Chroma candidates, applies the **active gate**, returns evidence.
4. Client can **status**-poll the exact job with `get_sync_status(source_id, job_id)`.

LaunchAgent/Docker only keep the worker process alive. They are not the queue;
SQLite is. The supported ops model is **one** LaunchAgent (or equivalent)
`sync_worker` process against a given Chroma/SQLite store — extra worker PIDs
can oversubscribe Chroma writes even when each process respects `N`. Operators
must restart both processes after `.env` or source-target changes: restart
FastMCP **and** the worker (LaunchAgent restart script, or Docker recreate with
the same `docker run ... --env-file` — `docker restart` keeps the old env).

### Job queue: enqueue / claim / status

| Term | Who | Meaning |
| --- | --- | --- |
| **enqueue** | FastMCP (`sync_source` / `sync_all`) | Insert a new sync job as `queued`, or return the existing queued/running job for that source (no duplicate). Returns immediately — does not wait for fetch/index. |
| **claim** | Worker | Atomically take the next `queued` job → `running` while `COUNT(RUNNING) < N`, attach owner/pid/heartbeat, then execute fetch + index. Up to `N` distinct-source jobs may run concurrently in one worker. |
| **status** | FastMCP (`get_sync_status`) | Read the job (or source's latest job) from SQLite: `queued` / `running` / `succeeded` / `failed`. Exact-job mode uses both `source_id` and `job_id`. |

### Document / chunk / tombstone lifecycle

SQLite tracks what content exists and whether it may be cited — separate from Chroma vectors:

| Unit | Role |
| --- | --- |
| **document** | One synced page/note/file (identity, timestamps, active vs deleted). Sync unit. |
| **chunk** | Search/citation unit carved from a document (text + citation metadata). |
| **tombstone** | Soft-delete marker (`deleted_at`, …) for content that disappeared from a **complete successful** cleanup-capable sync. Stale Chroma hits for tombstoned docs must not be returned as evidence. Failed/partial syncs must not tombstone absences. |

### Active-gate search

Chroma only proposes semantic **candidates**. Before results go to the client, SQLite checks each hit is still an **active** (non-tombstoned) document/chunk and passes date/source filters. That check is the **active gate**. Without it, search could cite deleted or out-of-range content just because an old vector still exists.

## Module Map

| Module | Owns |
| --- | --- |
| `api/` | MCP tool contracts, formatting, caller-visible errors |
| `fetching/` | Notion, Tistory, GitHub, Obsidian connectors |
| `indexing/` | Worker claim/dispatch under bounded global RUNNING budget, chunking, in-process Chroma mutation lock, sync lifecycle |
| `search/` | Retrieval, ranking, SQLite active gates, CitationAnswerService |
| `storage/` | SQLite source/job/document/chunk/tombstone metadata |
| `core/` | Shared models, exceptions, utilities |
| `environments/` | AppConfig, Chroma setup, token/env access |
| `main.py` | FastMCP composition/startup only |
| `deploy/launchd`, `scripts/` | macOS supervision helpers (not ingestion/persistence) |

New behavior starts in the owning module; avoid cross-module shortcuts in
`api/tools.py` when a service boundary fits.

## Core Mental Model

End-to-end data path: **sync writes** into stores, then **search reads** through
the active gate. Chroma sits in the middle.

```mermaid
flowchart TD
  subgraph syncWrite [Sync — write path]
    Sync[Configured source sync] --> Identity[Stable document identity + content hash]
    Identity --> Chunk[Deterministic chunking]
    Chunk --> WriteChroma[Write / update Chroma vectors]
    Chunk --> WriteSQL[Write SQLite document / chunk lifecycle]
  end
  subgraph searchRead [Search — read path]
    Cand[Chroma semantic candidates] --> Gate[SQLite active-document / date gate]
    Gate --> Evidence[Chunk evidence / grouped browse / citation helpers]
  end
  WriteChroma -.-> Cand
  WriteSQL -.-> Gate
```

## Sync / Job Ownership

```mermaid
sequenceDiagram
  participant MCP as FastMCP / sync_* tools
  participant Q as SQLite job queue
  participant W as sync_worker
  participant S as Connectors + indexer

  MCP->>Q: enqueue or reuse active job
  Note over Q: Queued jobs are unowned
  W->>Q: claim while COUNT(RUNNING) < N
  Q-->>W: running + owner/pid/heartbeat
  W->>S: up to N concurrent distinct-source syncs
  Note over W,S: Fetch may overlap; ContentIndexer mutation lock serializes Chroma writes in-process
  S->>Q: documents/chunks + terminal status
  Note over S,Q: Soft-delete missing docs only after a full successful sync on cleanup-capable sources
  MCP->>Q: get_sync_status(source_id, job_id) exact job
```

### Bounded concurrency

The generic worker may run up to `N` distinct-source jobs concurrently across
Notion, Tistory, GitHub, and Obsidian. Default `N` is 2 via
`CONTEXTWIKI_SYNC_WORKER_MAX_CONCURRENT` (integer `1..8`, fail-closed at
startup). `N=1` restores the previous global single-flight behavior. SQLite
claim remains authoritative with a `COUNT(RUNNING) < N` gate, and enqueue still
allows at most one active queued/running job per `source_id`. Within one
worker process, connector fetch may overlap while `ContentIndexer`'s
in-process mutation lock serializes Chroma mutations. That lock is not
cross-process: multiple worker PIDs against the same store can oversubscribe
writes even when each process respects `N`. Detail: ADR 0010.

### Ownership & recovery

- Queued jobs stay pending when no worker is available; do not fail them for
  lack of a running owner.
- Running jobs carry owner/heartbeat; same-source sync is blocked while queued
  or live-owned.
- Recovery: stale jobs, unowned grace, dead owners; **PID-reuse caution**
  (especially Docker PID 1). Process-start identity (Linux `linux-v2` /
  Darwin) is definitive only in-scope; unknown/malformed/cross-namespace falls
  back to heartbeat staleness.
- MCP shutdown does not cancel worker jobs. Graceful worker signal → fail
  **all** in-flight claimed jobs **without** tombstones. Abrupt death → orphan
  recovery marks failed; v1 does not auto-resume partial work. After the
  orphaned job is terminal `failed`, callers enqueue a **fresh** sync.

### Tombstone safety

Tombstones only after a **complete successful** cleanup-capable snapshot.
Failed, partial, or bound-truncated snapshots must not tombstone absences.

**cleanup-capable:** the source can prove a full remote/vault inventory for this
sync (Notion search, GitHub tree, Obsidian walk when bounds are not exceeded).
Tistory is not — its id-scan cannot prove “everything that exists” — so it never
tombstones absences.

### Job status (and optional phase)

- **`job.status`:** `queued` → `running` → `succeeded` | `failed` (what clients poll).
- **`phase`:** optional running-only progress hint (`starting`, `discovering_pages`,
  `fetching_page_content`, `indexing_documents`, …). Not a substitute for
  `status`. Detail / polling policy: ADR 0009.

### Running progress hints (`get_sync_status` only)

While a job is `running`, exact-job / latest-job payloads may also expose:

| Hint | Meaning |
| --- | --- |
| `upstream_total` | Source-neutral list/scan size so far (Notion pages, GitHub selected blobs, Obsidian notes, Tistory scan ids) |
| `upstream_done` | Items fetched, skipped, or otherwise completed so far |
| `last_progress_at` | Public liveness stamp for status polls (advances on coalesced progress writes) |
| `status_message` | Short sanitized, source-neutral text (e.g. “Fetching upstream items 25/100…”) |

```text
discovering_pages:     upstream_total = discovered so far; upstream_done = 0
fetching_page_content: upstream_total = final list/scan size; upstream_done = completed items
```

- Public MCP uses **only** `upstream_total` / `upstream_done`. Legacy SQLite
  columns `upstream_total_pages` / `upstream_fetched_pages` may be dual-written
  for older DBs but are **not** exposed. Reads prefer the new columns whenever
  they exist (including intentional `0`); one-time schema migrate backfills
  legacy → new.
- Hint writes are coalesced: full counter/`status_message` about every 25 items
  (and always on the last item); `last_progress_at` + heartbeat more often
  (~every 5) so polls do not look stuck and orphan detection stays fresh.
- Connectors emit Notion-shaped progress events; ingestion maps them for all
  sources. Cooperative cancel: stop signal / `_InactiveJobStop` re-raises
  through emit helpers; Tistory cancels `create_task` fan-out in `finally`
  (including `CancelledError`); GitHub checks stop during owner-resolve
  pagination and between repo planning steps. Inactive-job stop must not be
  swallowed by stop-checker helpers.

Hints are suppressed again once the job is terminal. They do not widen
`sync_source` / `sync_all` response shapes.

### `sync_source` / `sync_all`

- `sync_source`: enqueue or reuse active job; return immediately (not wait for
  fetch/index). Disabled source → terminal `failed` in the same write.
- Worker claims work from SQLite (see Runtime Structure); MCP does not run
  long sync in-process when the worker is down.
- `sync_all`: launch acceptance only (`started` / `already_running` / `skipped` /
  `failed`). Completion = client polls exact
  `get_sync_status(source_id, job_id)` until terminal — not `latest_job`.
  Timing/backoff: ADR 0009.

## Retrieval

```mermaid
flowchart TD
  SC[search_context] --> CSS[ContextSearchService]
  SD[search_documents] --> CSS
  CSS --> Norm[Deterministic query normalize]
  Norm --> Cand[Chroma / LlamaIndex candidates]
  Cand --> Val[SQLite active + inclusive date gate]
  Val --> Rank[Rank / select]
  Rank --> ChunkOut[Chunk payload]
  Rank --> DocOut["Grouped docs + matched_context"]
  LD[list_documents] --> MS[MetadataStore active listing]
  MS --> Browse[Date-ordered keyset pages]
  FC[fetch_context] --> Hydrate[MetadataStore hydrate doc/chunks]
  CA[CitationAnswerService] --> CSS
```

## MCP Tool Contract

```text
list_sources() -> dict
sync_source(source_id: str) -> dict
sync_all() -> dict
get_sync_status(source_id: str = "", job_id: str = "") -> dict
search_context(query, filters=None, top_k=10, include_debug=False) -> dict
search_documents(query, filters=None, sort_by="relevance", sort_order="desc", top_k=10) -> dict
list_documents(filters=None, sort_by="indexed_at", sort_order="desc", page_size=20, cursor=None) -> dict
fetch_context(document_id="", chunk_id="") -> dict
```

**Intent (short):**

- Filters: `source_id` / `source_ids` (union if both), inclusive UTC date bounds
  (`published_*`, `modified_*`, `indexed_*`); offset-free timestamps = UTC;
  date-only values start at midnight UTC; unknown fields rejected.
- `search_context`: chunk evidence; date gate in SQLite; optional `debug`
  (`{}` when `include_debug=False` except `no_matching_sources` exception).
- `search_documents`: same path, one row/doc, best chunk text as
  `matched_context` (not `preview`); optional date sort within semantic set.
- `list_documents`: no query; SQLite browse; `page_size` 1–50; opaque
  `next_cursor` must be reused unchanged with the same filters/sort; public
  rows expose only `document_id`, `source_id`, `title`, `url`, `canonical_url`,
  `platform`, `published_at`, `modified_at`, `indexed_at`, `date_provenance`
  — no content/local paths.
- Public search results expose normalized timestamps + `date_provenance` (not
  legacy `date` / `updated_at` reinterpretation).
- `auth_ref` only `env:UPPER_CASE_NAME`; other forms normalize to empty; public
  formatting rejects noncanonical refs.
- Status fields: `latest_success_at`, `latest_failure_at`, `document_count`,
  `chunk_count`, `latest_failure_reason`, `stale_cleanup_disabled_reason`.
- Tool annotations: retrieval tools are not read-only/idempotent (schema init /
  heartbeat); `search_*` `openWorldHint=True` (default embeddings may egress);
  `list_documents` / `fetch_context` `openWorldHint=False`.
- Keep names, params, return shapes, and error vocabulary stable unless the
  user requested a contract change; never leak secrets or full local paths.

## Identity & Chunking

| Source | Stable identity | Dates / version | Notes |
| --- | --- | --- | --- |
| Notion | page id | `published_at`/`modified_at`, `date_provenance="notion"` | Skip `fetch_block_content` when active stored doc has content and canonical `modified_at` matches page `last_edited_time` (or `created_time`); batched skip/reuse fields only; skipped pages still in snapshot for `last_seen` / cleanup |
| Tistory | `blog_name:post_id` | `published_at`, `date_provenance="tistory"` | — |
| GitHub | repository path | blob SHA → `version_id` only (not a mod timestamp) | Prefix-scoped stale cleanup (below) |
| Obsidian | relative note path | mtime → `modified_at`, `date_provenance="filesystem"`; `obsidian://open` as citation URL | Local vault Markdown; not a live Obsidian app |

Lifecycle fields: `external_id`, `document_id`, `canonical_url`, `version_id`,
`published_at`, `modified_at`, `indexed_at`, `date_provenance`, `last_seen_at`,
`last_seen_sync_id`, `deleted_at`.

Chunking: heading markdown → plain-text windows → line-range code chunks.
Citation metadata per chunk includes `chunk_id`, `document_id`, `source_id`,
`title`, `url`, `path`, `chunk_index`, `line_start`/`line_end`, hashes, version,
timestamps, `date_provenance`.

## Persistence & Safety

| Store | Role |
| --- | --- |
| SQLite | Authority for source/job/document/chunk lifecycle, active gates, dates, listing, tombstones |
| Chroma | Semantic candidate accelerator; stale hits filtered via SQLite before evidence |

- Do not inspect, delete, reset, or migrate local Chroma/SQLite without explicit
  user approval, a plan, and rationale. Tests use temp paths/mocks.
- Soft-delete provenance: tombstones must suppress stale vectors even if
  best-effort Chroma cleanup lags.
- Normalized date columns are additive; legacy `date`/`updated_at` intact;
  empty timestamps until later sync — no forced reindex for that alone.
- **GitHub prefix cleanup:** under shared `source_github`, cleanup prefixes come
  only from repositories successfully resolved in that snapshot. One repo must
  not tombstone another; owner discovery never enables broad owner-prefix
  cleanup. Confirmed-empty repo (GitHub metadata) may tombstone its exact
  prefix after a complete sync; ambiguous empty metadata cannot.

### External sources (compressed)

- Notion / Tistory / GitHub / Obsidian as configured; disabled source blocks new
  syncs but does not hide already-active docs.
- GitHub: targets `owner`, `owner/repo`, `owner/repo@ref`; mixed targets must
  not overlap identities; bounds (files/bytes/pages) make incomplete snapshots
  and disable stale cleanup; listing endpoints capped (full page 100 → fail
  before index).
- Obsidian: vault path + file/byte bounds; over-bound → incomplete fail before cleanup.
- Default embeddings may egress to OpenAI unless overridden; no AppConfig
  embedding-provider switch. Local demo/E2E inject `MockEmbedding`.
- Live API or real-vault checks need explicit approval + plan; never print tokens
  or local path details.

## Configuration, Secrets, Errors

- Secrets: `environments/token.py`, `.env`, env vars, API keys — never in docs,
  tests, logs, screenshots, or examples.
- Domain exceptions in `core/exceptions.py`. Classify fetch errors near fetchers;
  search errors must not leak internals; indexing updates status before failure
  surfaces; tool messages stay user-readable without secrets.

## Testing & Harness

| Change type | Gate |
| --- | --- |
| Docs-only | path listing, `git status --short --branch`, `git diff --check`, stage + `git diff --cached --check` |
| Code/behavior | TDD unit + integration + deterministic E2E → `./scripts/verify_all.sh` before review/delivery |
| Functional E2E | `./scripts/verify_functional_e2e.sh` (temp data; no live API/LLM) |
| Live smoke / real vault | Explicit approval + plan only |

`harness-plan` and review gates must read this document before choosing
boundaries or approving contracts.

## Out of Scope

No production Web Console, Auto Wiki generation, generic website/docs crawling,
dynamic web fallback, or legacy live-search/indexing MCP tools.
