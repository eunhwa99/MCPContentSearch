# Architecture

## Purpose

This document maps the current slim `MCPContentSearch` architecture. Harness
planning and review use it to keep changes inside the focused MCP retrieval
scope and to catch contract or data-safety regressions. It is the single
maintained design reference beyond the README.

## Runtime Structure

`MCPContentSearch` is a Python FastMCP server.

- MCP server: `main.py` creates a `FastMCP` server named
  `content-search-server`.
- MCP tools: `api/tools.py` registers only retained ContextWiki retrieval tools:
  `list_sources`, `sync_source`, `sync_all`, `get_sync_status`,
  `search_context`, `search_documents`, and `fetch_context`.
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
  document-browsing retrieval, ranking, direct context fetch, and citation
  answer scaffolding.
- Persistence: SQLite metadata via `storage/metadata_store.py` plus ChromaDB via
  `chromadb.PersistentClient`, defaulting to local user storage unless tests
  provide temporary paths.

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
  validated retrieval path.
- `CitationAnswerService.answer_with_citations(...)` is an internal helper
  answer surface built on top of validated evidence, not a separate retrieval
  stack.
- Search query rewrite is optional, disabled by default, and any egress it
  performs is limited to query rewriting rather than source fetching or data
  mutation.

## Data Flow

```text
MCP client
  -> FastMCP server in main.py
  -> api/tools.py registered tool handler
  -> service boundary in indexing/search/storage/fetching
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

When a sync job is actively running, `get_sync_status` may additionally expose
`latest_job` progress hints that explain whether the system is still upstream
discovery/fetch-bound or already indexing. Those hints are intentionally
running-only and are suppressed again once the latest job reaches a terminal
state. Maintained reviewer-facing hints now include:

```text
phase
upstream_total_pages
upstream_fetched_pages
last_progress_at
status_message
```

Those running-job hints are intentionally limited to `get_sync_status`; they do
not broaden the public `sync_source` or `sync_all` response shapes.

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

Running-job ownership is part of that status story. A source reports as
effectively blocked when SQLite still sees an active sync owner/heartbeat for
that source, which prevents overlapping syncs from starting.

That blocked state is intentionally recoverable rather than permanent. Recovery
distinguishes stale jobs, unowned-job grace, dead owners, and the container
PID-reuse case where an old and new container can both appear as PID `1`.
During that same-PID edge case, reclaim falls back to the running job's own
heartbeat staleness window instead of reclaiming immediately from a transient
owner mismatch.

Source sync flow:

```text
sync_source
  -> IngestionService.start_sync_source() for MCP callers
  -> SourceRegistry connector lookup
  -> MetadataStore source registration and sync job guard
  -> immediate running-job payload returned to caller
  -> background IngestionService worker fetch/index lifecycle
  -> Notion, Tistory, GitHub, or Obsidian connector fetch
  -> DocumentChunker
  -> ContentIndexer and Chroma collection
  -> MetadataStore SQLite source/job/document/chunk/tombstone metadata
  -> get_sync_status reads terminal completion
```

Retained sync safety rule:

- Tombstoning stale documents is allowed only for cleanup-capable sources after
  a complete successful snapshot. Failed or incomplete syncs must not tombstone
  documents simply because they were absent from a partial fetch.

Bulk source sync flow:

```text
sync_all
  -> enumerate retained configured sources
  -> start one sync_source task per source
  -> preserve per-source running-job guards in SQLite
  -> aggregate per-source results as succeeded, failed, blocked, or skipped
  -> return completed only when results are succeeded/skipped only
  -> return partial for mixed success/skip/failure/block combinations
  -> return failed when every source failed or was blocked by failure conditions
```

Retrieval and answer flow:

```text
search_context
  -> optional default-disabled LLM query rewrite
  -> ContextSearchService
  -> Chroma/LlamaIndex candidate retrieval
  -> metadata fallback candidates when ranking decides they are needed
  -> MetadataStore active chunk/document validation
  -> chunk-level structured search result payload

search_documents
  -> ContextSearchService
  -> Chroma/LlamaIndex candidate retrieval
  -> metadata fallback candidates when ranking decides they are needed
  -> MetadataStore active chunk/document validation
  -> group by document_id
  -> choose highest-ranked representative chunk per document
  -> grouped document-browsing payload

fetch_context
  -> MetadataStore direct document/chunk hydration
  -> document or chunk payload

internal helper answer flows
  -> CitationAnswerService
  -> search_context_for_answer / search_context
  -> MetadataStore-validated evidence chunks
  -> citation-gated answer payload
```

## Module Responsibilities

- `api`: MCP-facing tool contracts, parameter defaults, result formatting, and
  caller-visible error messages. It delegates business behavior to services.
- `fetching`: Notion, Tistory, GitHub, and Obsidian content retrieval plus
  source connector registration. It owns API-specific or filesystem-specific
  parsing, bounded fetch behavior, and partial failure handling. Internal
  Notion/GitHub target parsing helpers are implementation utilities only; the
  retained MCP surface is still configured source sync through `sync_source`.
  Obsidian is a configured local-vault Markdown source, not a live Obsidian app
  or plugin integration.
- `indexing`: document indexing lifecycle, deterministic chunking, content
  hash/chunk-id comparison, Chroma mutation, and index status updates.
- `search`: query orchestration, ranking, metadata fallback, SQLite-backed
  active-result validation, direct context fetch, and internal citation answer
  support.
- `storage`: SQLite source/job/document/chunk lifecycle metadata, tombstones,
  sync-job ownership, and active retrieval checks.
- `core`: stable shared data models, exception classes, and utility functions.
- `environments`: configuration defaults, Chroma setup, API version constants,
  and environment-token access.
- `main.py`: dependency composition and server startup only.

New behavior should start in the module that owns the relevant responsibility.
Avoid adding cross-module shortcuts in `api/tools.py` when a service boundary is
more appropriate.

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

- Notion: page id drives stable identity.
- Tistory: `blog_name:post_id` drives stable identity.
- GitHub: repository path drives stable identity, while blob SHA is revision
  metadata.
- Obsidian: relative note path drives stable identity, while the
  `obsidian://open` URL stays the citation-friendly canonical URL.

The lifecycle fields that matter for reviewer understanding are:

```text
external_id
document_id
canonical_url
version_id
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
```

That deterministic chunking plus stable identity is what makes unchanged-doc
skip behavior, reappeared-document recovery, and citation stability predictable
across syncs.

## Four-Layer View

ContextWiki is easiest to reason about as four layers:

```text
MCP client
-> FastMCP tool surface
-> ingestion/search services
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
- `get_sync_status(source_id: str = "") -> dict`
- `search_context(query: str, filters: dict = None, top_k: int = 10, include_debug: bool = False) -> dict`
- `search_documents(query: str, filters: dict = None, top_k: int = 10) -> dict`
- `fetch_context(document_id: str = "", chunk_id: str = "") -> dict`

Contract intent:

- `search_context` remains the chunk-level evidence and citation surface.
- `sync_all` is an aggregate orchestration helper, not a separate ingestion
  stack. It fans out retained-source `sync_source` runs concurrently, preserves
  each source's existing running-job guard, and reports mixed source outcomes
  truthfully instead of pretending the whole batch succeeded when one source was
  blocked or failed. Disabled sources may surface as `skipped`, and the
  top-level batch status remains `completed` only when the aggregate outcomes
  are limited to `succeeded` and `skipped`.
- `search_documents` is additive and document-oriented: it uses the same
  retained-source retrieval path but returns one representative chunk-backed row
  per document for browsing.
- Internal `CitationAnswerService.answer_with_citations(...)` reuses
  `search_context_for_answer` / `search_context`, so query-rewrite egress and
  retrieval semantics stay aligned across search and helper-answer flows.
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
- Retrieval policy keeps vector retrieval, metadata fallback, and rerank/debug
  reporting as distinct concerns. Query rewrite, fallback candidate addition,
  and final SQLite validation should stay inspectable without blurring them into
  one opaque score.
- When query rewrite debug is included, the caller-visible explanation should
  keep the current public fields aligned with behavior: `rewrite_enabled`,
  `rewrite_attempted`, `rewrite_applied`, and `rewrite_skipped_reason` explain
  whether rewrite was disabled, skipped, attempted but unused, or actually
  applied before retrieval.
- The top-level `rewrite_skipped_reason` field should stay coarse and
  reviewer-readable. Current values explain state such as `disabled`,
  `not_needed`, `rewrite_failed`, `no_matching_sources`, `no_term_groups`, and
  `not_better_than_original`.
- The nested `debug.query_rewrite.reason` field is the retrieval-pipeline
  explanation vocabulary. Current stable values include
  `no_initial_candidates`, `missing_textual_match`, and
  `low_initial_vector_score`.
- `debug.query_rewrite.initial_top_vector_score` captures the prerank vector
  score that triggered rewrite evaluation, while
  `debug.query_rewrite.final_top_score` captures the selected final reranked top
  score after the pipeline chooses between original and rewritten result sets.
- A single strong exact-match candidate can also suppress rewrite even when the
  caller asked for a larger `top_k`; that guardrail keeps clearly correct
  direct hits from being rewritten unnecessarily.

Retained debug-oriented answer inspection surfaces should stay documented and
stable enough for local evaluation and reviewer use:

- `search_context` debug explains retrieval/rewrite decisions.
- Current reviewer-facing search debug commonly includes retrieval query and
  result-selection surfaces such as `retrieval_queries`,
  `rewritten_queries`, and `selected_results[]`.
- Deterministic intent policy should remain readable in debug output when
  present. The current retained intent vocabulary includes `strict_lookup`,
  `broad_topic`, `list`, and `comparison`, and that intent is reused by
  ranking and grounded answer rendering.
- `CitationAnswerService.answer_with_citations(...)` exposes helper-answer
  inspection surfaces such as `citations`, `used_chunks`, `debug`, and
  `debug_markdown` when the current implementation returns them.
- Public debug payloads may also surface deterministic intent and retrieval
  inspection sections such as `intent.*`, `retrieval_queries`,
  `rewritten_queries`, and `selected_results[]` so reviewers can explain why a
  grounded result set was chosen.
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
filtered through SQLite metadata before being returned as evidence.

GitHub stale cleanup remains repository-prefix scoped under the shared
`source_github` source id. A sync for one configured repository must not
tombstone documents that belong to another repository prefix.

Soft-delete provenance matters here: SQLite tombstone metadata must remain able
to suppress stale managed vector hits even when best-effort vector cleanup
cannot remove every old Chroma candidate immediately.

## External Services and Local Sources

Current integrations and local configured sources:

- Notion API, configured by environment token and API version.
- Tistory, configured by blog name and bounded post fetching.
- GitHub repositories, configured by repository specs and optional
  `GITHUB_TOKEN`.
- Obsidian local vaults, configured by `CONTEXTWIKI_OBSIDIAN_VAULT_PATH`,
  `CONTEXTWIKI_OBSIDIAN_MAX_FILES`, and
  `CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES`. Obsidian sync reads bounded Markdown
  notes from the filesystem and does not require a live Obsidian app. If the
  file count or file byte bound is exceeded, the sync fails as an incomplete
  snapshot before stale cleanup. Real vault validation requires explicit user
  approval; tests must use temporary vaults.
- Optional search LLM query rewrite, disabled by default. When
  `CONTEXTWIKI_SEARCH_LLM_ENABLED=true`, `search_context` may send the user's
  search query and normalized query terms to the configured provider before
  local retrieval. Internal helper-answer flows inherit that same egress
  because they reuse `search_context_for_answer` / `search_context`. This path
  is external egress, is not dynamic web fallback, does not fetch source
  content, and must not mutate SQLite or Chroma.
- A disabled retained source blocks future sync attempts but does not
  automatically hide already indexed active documents. Those documents remain
  retrievable until later cleanup or metadata changes mark them inactive.
- Embedding provider behavior comes from the configured LlamaIndex embedding
  setup. Disabling search query rewrite only disables query-rewrite egress;
  fully local operation also requires local or otherwise non-egress embeddings.

Testing should prefer mocked external APIs and temporary local vaults. Live
network or real-vault validation requires explicit user approval and must not
print credentials or local path details.

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

The maintained verification model is layered:

- docs-only verification for README, harness docs, plans, and other markdown
  changes
- focused syntax, import, or targeted pytest checks for the directly changed
  modules
- retained functional E2E coverage for MCP-visible sync/search/fetch
  workflows plus internal helper-answer coverage where retained tests depend on
  `CitationAnswerService`
- full-wrapper verification through `./scripts/verify_all.sh` when the work item
  needs the repo's broader default gate instead of only a narrow focused check
- optional manual live smoke through `scripts/live_query_smoke.py` only when the
  user explicitly approves real configured-source validation
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
  workspace is healthy.
- MCP contract: focused tests around `register_tools` and retained tool
  functions. The strongest public contract layer uses real
  `FastMCP.call_tool(...)` payload checks rather than only internal helper
  assertions.
- Search/indexing/storage: temp Chroma path, temp SQLite path, or mock
  collection; avoid user data.
- Fetching: mocked Notion/Tistory/GitHub responses and temporary Obsidian vaults;
  live API or real-vault checks only with explicit approval.
- Functional E2E: `./scripts/verify_functional_e2e.sh`, which must cover
  retained MCP sync/search/fetch paths, grouped document browsing, and any
  retained internal helper-answer flows without browser, wiki, live API, or
  LLM dependencies.
- Full wrapper: `./scripts/verify_all.sh`, which includes compile, lint, type,
  non-live pytest, and the functional E2E gate when that broader default repo
  verification is required.
- Manual live smoke: `python scripts/live_query_smoke.py`, only with explicit
  approval because it can touch real configured sources or local user data.
- Retained eval runner: `PYTHONPATH=. python scripts/run_contextwiki_eval.py`
  or the repo wrapper that invokes it, used when a work item changes retrieval
  or answer quality on a modeled local eval surface.
- Deterministic reviewer-visible eval artifacts should stay separate from
  optional runtime or latency metrics such as `runtime_metrics.json` so repeated
  runs remain comparable.

## Harness Usage

`harness-plan` must read this document before choosing implementation
boundaries. Review gates must check changed files against this architecture.
