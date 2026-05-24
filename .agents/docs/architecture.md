# Architecture

## Purpose

This document maps the current `MCPContentSearch` architecture. Harness planning and review use it to choose changes that fit existing boundaries and to catch contract or data-safety regressions.

Decision history is indexed in `.agents/docs/adr/README.md`. Read only ADRs that directly affect the requested change.

## Runtime Structure

`MCPContentSearch` is a Python MCP server.

- MCP server: `main.py` creates a `FastMCP` server named `content-search-server`.
- MCP tools: `api/tools.py` registers legacy search/indexing tools plus
  ContextWiki source sync, context retrieval/fetch, citation answer, and status
  tools.
- Configuration: `environments/config.py` contains `AppConfig`, `NotionConfig`, source connector settings, and `setup_chroma`.
- Secrets/environment loading: `environments/token.py`.
- Shared models/errors/utilities: `core/`.
- Fetching: `fetching/` provides legacy Notion/Tistory live search and ContextWiki source connectors for Notion, Tistory, GitHub, and website/docs content.
- Indexing: `indexing/` chunks documents, detects unchanged or reindexed content, writes vectors to Chroma/LlamaIndex, and coordinates lifecycle metadata.
- Search: `search/` provides legacy local search, dynamic local-to-web fallback, SQLite-gated context search, and citation answer scaffolding.
- Persistence: SQLite metadata via `storage/metadata_store.py` plus ChromaDB via `chromadb.PersistentClient`, defaulting to `~/.mcp_content_search/chroma_db`.

## Data Flow

```text
MCP client
  -> FastMCP server in main.py
  -> api/tools.py registered tool handler
```

Legacy search/indexing flow:

```text
search_content
  -> DynamicSearchService
  -> SearchService and local Chroma/LlamaIndex search
  -> optional WebSearcher fallback
  -> NotionSearcher and TistorySearcher
  -> background ContentIndexer task
  -> ChromaDB persistent collection

search_notion / search_tistory
  -> WebSearcher
  -> NotionSearcher or TistorySearcher
  -> background ContentIndexer task
  -> ChromaDB persistent collection

trigger_index_all_content
  -> DocumentFetcher
  -> fetch_notion_pages and fetch_tistory_posts
  -> ContentIndexer.index_documents
  -> IndexManager dedup/update checks
  -> DocumentConverter.to_llama_document
  -> VectorStoreIndex and Chroma collection
```

ContextWiki source status flow:

```text
list_sources / get_sync_status
  -> MetadataStore SQLite source/job metadata
```

ContextWiki source sync flow:

```text
sync_source
  -> IngestionService
  -> SourceRegistry connector lookup
  -> MetadataStore source registration and sync job guard
  -> Notion, Tistory, GitHub, or Website Docs connector fetch
  -> DocumentChunker
  -> ContentIndexer and Chroma collection
  -> MetadataStore SQLite source/job/document/chunk/tombstone metadata
```

ContextWiki retrieval flow:

```text
search_context
  -> ContextSearchService
  -> Chroma/LlamaIndex candidate retrieval
  -> MetadataStore active chunk/document validation
  -> structured search result payload

answer_with_citations
  -> CitationAnswerService
  -> ContextSearchService
  -> MetadataStore-validated evidence chunks
  -> citation-gated answer payload

fetch_context
  -> MetadataStore direct document/chunk hydration
  -> document and chunk payload
```

## Module Responsibilities

- `api`: MCP-facing tool contracts, parameter defaults, result formatting, and caller-visible error messages. It delegates business behavior to services.
- `search`: query orchestration and result formatting for local, dynamic, and ContextWiki retrieval. It owns fallback threshold behavior and SQLite-backed active-result validation.
- `indexing`: document indexing lifecycle, deterministic chunking, content hash/chunk-id comparison, Chroma mutation, and index status updates.
- `fetching`: external content retrieval, legacy live search, and ContextWiki source connectors for Notion, Tistory, GitHub, and website/docs. It owns API-specific parsing, bounded fetch behavior, and partial failure handling.
- `core`: stable shared data models, exception classes, and utility functions.
- `environments`: configuration defaults, Chroma setup, API version constants, and environment-token access.
- `storage`: SQLite source/job/document/chunk lifecycle metadata, tombstones, and active retrieval checks.
- `main.py`: dependency composition and server startup only.

New behavior should start in the module that owns the relevant responsibility. Avoid adding cross-module shortcuts in `api/tools.py` when a service boundary is more appropriate.

## MCP Tool Contract

Current tools:

Legacy tools:

- `search_content(query: str, n_results: int = 10) -> str`
- `search_notion(query: str, n_results: int = 10) -> str`
- `search_tistory(query: str, n_results: int = 10) -> str`
- `trigger_index_all_content() -> str`
- `get_index_status() -> dict`

ContextWiki tools:

- `list_sources() -> dict`
- `sync_source(source_id: str) -> dict`
- `get_sync_status(source_id: str = "") -> dict`
- `search_context(query: str, filters: dict = None, top_k: int = 10) -> dict`
- `fetch_context(document_id: str = "", chunk_id: str = "") -> dict`
- `answer_with_citations(question: str, filters: dict = None, top_k: int = 5) -> dict`

When changing a tool:

- Keep names, parameters, return types, and error messages stable unless the user requested a contract change.
- Update README or client-facing docs when behavior changes.
- Ensure exceptions do not expose tokens, filesystem secrets, or full local data paths unnecessarily.
- Treat background indexing as caller-visible behavior. If a tool returns before indexing completes, status reporting must remain truthful.

## Async and Background Work

The project uses async functions and `asyncio.create_task` for background indexing.

- Do not use background tasks for work the caller expects to be complete before the tool returns.
- Log background failures and keep status state accurate when possible.
- Avoid shared mutable state changes that race with search or indexing status without a clear owner.

## Persistence and Local Data

ChromaDB stores indexed user content for semantic retrieval. SQLite stores
ContextWiki source/job/document/chunk lifecycle and citation metadata.

- Do not delete, reset, or inspect local Chroma data or SQLite metadata without
  explicit user approval, a plan, and user-visible rationale.
- Tests should prefer temporary paths or mocks when touching Chroma or SQLite metadata.
- Indexing changes must preserve document identity and content hash semantics unless the plan explains migration or reindexing behavior.
- If a change requires reindexing, the plan and final report must include user-data impact and rollback/mitigation notes.

## External Services

External integrations:

- Notion API, configured by environment token and API version.
- Tistory, configured by blog name and crawler/search behavior.
- GitHub repositories, configured by repository specs and optional `GITHUB_TOKEN`.
- Website/docs URLs, configured by allowed URLs and bounded crawler settings.
- Optional web/network access through HTTP clients used by fetchers.

Testing should prefer mocked external APIs. Live network validation requires user approval and must not print credentials.

## Configuration and Secrets

- `environments/token.py`, `.env`, shell environment variables, and API keys are sensitive.
- Do not add secret values to docs, tests, logs, screenshots, or examples.
- If a configuration default changes, update architecture docs or ADRs when it changes long-term behavior.

## Error Handling

Domain exceptions live in `core/exceptions.py`.

- Fetching errors should be classified close to fetchers.
- Search errors should not leak implementation details to MCP clients.
- Indexing errors should update index status before surfacing a failure.
- Tool handlers may return user-readable Korean messages, but logs should preserve enough context to debug without exposing secrets.

## Testing Strategy

Use the smallest useful check first.

- Docs-only changes: path listing, `git status --short --branch`, `git diff --check`, and `git diff --cached --check`.
- Syntax/import safety: `python -m compileall api core environments fetching indexing search storage main.py`.
- Unit tests: `uv run pytest` when tests exist.
- MCP contract: focused tests or smoke checks around `register_tools` and tool functions.
- Search/indexing/storage: temp Chroma path, temp SQLite path, or mock collection; avoid user data.
- Fetching: mocked HTTP/API responses; live checks only with approval.

## Harness Usage

`harness-plan` must read this document before choosing implementation boundaries. Review gates must check changed files against this architecture and directly relevant accepted ADRs.
