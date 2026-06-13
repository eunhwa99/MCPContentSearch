# Architecture

## Purpose

This document maps the current slim `MCPContentSearch` architecture. Harness
planning and review use it to keep changes inside the focused MCP retrieval
scope and to catch contract or data-safety regressions.

Decision history is indexed in `.agents/docs/adr/README.md`. ADR 0006 is the
current scope decision for the slim MCP core and supersedes the website/docs
portion of ADR 0004 plus ADR 0005's Auto Wiki decision for current work.

## Runtime Structure

`MCPContentSearch` is a Python FastMCP server.

- MCP server: `main.py` creates a `FastMCP` server named
  `content-search-server`.
- MCP tools: `api/tools.py` registers only retained ContextWiki retrieval tools:
  `list_sources`, `sync_source`, `sync_all`, `get_sync_status`, `search_context`,
  `fetch_context`, and `answer_with_citations`.
- Configuration: `environments/config.py` contains `AppConfig`, source
  connector settings, metadata DB path, and Chroma setup.
- Secrets/environment loading: `environments/token.py` and runtime environment
  helpers. Raw tokens must not be persisted or logged.
- Shared models/errors/utilities: `core/`.
- Fetching: `fetching/` owns Notion, Tistory, GitHub, and Obsidian source
  fetching and connector registration.
- Indexing: `indexing/` chunks documents, detects unchanged/reindexed content,
  writes vectors to Chroma/LlamaIndex, and coordinates lifecycle metadata.
- Search: `search/` provides SQLite-gated context search, ranking, direct
  context fetch, and citation answer scaffolding.
- Persistence: SQLite metadata via `storage/metadata_store.py` plus ChromaDB via
  `chromadb.PersistentClient`, defaulting to local user storage unless tests
  provide temporary paths.

The current architecture does not include a production Web Console, Auto Wiki
generation, generic website/docs crawling, dynamic web fallback, or legacy
live-search/indexing MCP tools.

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

Source sync flow:

```text
sync_source
  -> IngestionService
  -> SourceRegistry connector lookup
  -> MetadataStore source registration and sync job guard
  -> Notion, Tistory, GitHub, or Obsidian connector fetch
  -> DocumentChunker
  -> ContentIndexer and Chroma collection
  -> MetadataStore SQLite source/job/document/chunk/tombstone metadata
```

Retrieval and answer flow:

```text
search_context
  -> optional default-disabled LLM query rewrite
  -> ContextSearchService
  -> Chroma/LlamaIndex candidate retrieval
  -> MetadataStore active chunk/document validation
  -> structured search result payload

fetch_context
  -> MetadataStore direct document/chunk hydration
  -> document or chunk payload

answer_with_citations
  -> CitationAnswerService
  -> ContextSearchService
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
- `search`: query orchestration, ranking, SQLite-backed active-result
  validation, direct context fetch, and citation answer support.
- `storage`: SQLite source/job/document/chunk lifecycle metadata, tombstones,
  sync-job ownership, and active retrieval checks.
- `core`: stable shared data models, exception classes, and utility functions.
- `environments`: configuration defaults, Chroma setup, API version constants,
  and environment-token access.
- `main.py`: dependency composition and server startup only.

New behavior should start in the module that owns the relevant responsibility.
Avoid adding cross-module shortcuts in `api/tools.py` when a service boundary is
more appropriate.

## MCP Tool Contract

Current tools:

- `list_sources() -> dict`
- `sync_source(source_id: str) -> dict`
- `sync_all() -> dict`
- `get_sync_status(source_id: str = "") -> dict`
- `search_context(query: str, filters: dict = None, top_k: int = 10, include_debug: bool = False) -> dict`
- `fetch_context(document_id: str = "", chunk_id: str = "") -> dict`
- `answer_with_citations(question: str, filters: dict = None, top_k: int = 5, include_debug: bool = False) -> dict`

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
  local retrieval. This path is external egress, is not dynamic web fallback,
  does not fetch source content, and must not mutate SQLite or Chroma.
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
- If a configuration default changes long-term behavior, update architecture
  docs or ADRs in the same work item.

## Error Handling

Domain exceptions live in `core/exceptions.py`.

- Fetching errors should be classified close to fetchers.
- Search errors should not leak implementation details to MCP clients.
- Indexing errors should update status before surfacing a failure.
- Tool handlers may return user-readable messages, but logs should preserve
  enough context to debug without exposing secrets.

## Testing Strategy

Use the smallest useful check first.

- Docs-only changes: path listing, `git status --short --branch`,
  `git diff --check`, then stage relevant docs-only files and run
  `git diff --cached --check` so new docs are covered.
- Syntax/import safety:
  `python -m compileall api core environments fetching indexing search storage main.py`.
- Unit/integration tests: `uv run --locked pytest -m "not live"` when the uv
  workspace is healthy.
- MCP contract: focused tests around `register_tools` and retained tool
  functions.
- Search/indexing/storage: temp Chroma path, temp SQLite path, or mock
  collection; avoid user data.
- Fetching: mocked Notion/Tistory/GitHub responses and temporary Obsidian vaults;
  live API or real-vault checks only with explicit approval.
- Functional E2E: `./scripts/verify_functional_e2e.sh`, which must cover
  retained MCP sync/search/fetch/answer paths without browser, wiki, live API,
  or LLM dependencies.

## Harness Usage

`harness-plan` must read this document before choosing implementation
boundaries. Review gates must check changed files against this architecture and
directly relevant accepted ADRs.
