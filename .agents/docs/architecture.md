# Architecture

## Purpose

This document maps the current `MCPContentSearch` architecture. Harness planning and review use it to choose changes that fit existing boundaries and to catch contract or data-safety regressions.

Decision history is indexed in `.agents/docs/adr/README.md`. Read only ADRs that directly affect the requested change.

## Runtime Structure

`MCPContentSearch` is a Python MCP server.

- MCP server: `main.py` creates a `FastMCP` server named `content-search-server`.
- MCP tools: `api/tools.py` registers search and indexing tools.
- Configuration: `environments/config.py` contains `AppConfig`, `NotionConfig`, and `setup_chroma`.
- Secrets/environment loading: `environments/token.py`.
- Shared models/errors/utilities: `core/`.
- Fetching: `fetching/` retrieves and searches Notion and Tistory content.
- Indexing: `indexing/` converts documents, detects new/updated content, and writes to Chroma/LlamaIndex.
- Search: `search/` provides local search and dynamic local-to-web fallback.
- Persistence: ChromaDB via `chromadb.PersistentClient`, defaulting to `~/.mcp_content_search/chroma_db`.

## Data Flow

```text
MCP client
  -> FastMCP server in main.py
  -> api/tools.py registered tool handler
  -> DynamicSearchService or SearchService
  -> local Chroma/LlamaIndex search
  -> optional WebSearcher fallback
  -> NotionSearcher and TistorySearcher
  -> background ContentIndexer task
  -> ChromaDB persistent collection
```

Full indexing flow:

```text
trigger_index_all_content
  -> DocumentFetcher
  -> fetch_notion_pages and fetch_tistory_posts
  -> ContentIndexer.index_documents
  -> IndexManager dedup/update checks
  -> DocumentConverter.to_llama_document
  -> VectorStoreIndex and Chroma collection
```

## Module Responsibilities

- `api`: MCP-facing tool contracts, parameter defaults, result formatting, and caller-visible error messages. It delegates business behavior to services.
- `search`: query orchestration and result formatting for local and dynamic search. It owns fallback threshold behavior.
- `indexing`: document indexing lifecycle, content hash comparison, Chroma mutation, and index status updates.
- `fetching`: external content retrieval and live search for Notion and Tistory. It owns API-specific parsing and partial failure handling.
- `core`: stable shared data models, exception classes, and utility functions.
- `environments`: configuration defaults, Chroma setup, API version constants, and environment-token access.
- `main.py`: dependency composition and server startup only.

New behavior should start in the module that owns the relevant responsibility. Avoid adding cross-module shortcuts in `api/tools.py` when a service boundary is more appropriate.

## MCP Tool Contract

Current tools:

- `search_content(query: str, n_results: int = 10) -> str`
- `search_notion(query: str, n_results: int = 10) -> str`
- `search_tistory(query: str, n_results: int = 10) -> str`
- `trigger_index_all_content() -> str`
- `get_index_status() -> dict`

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

ChromaDB stores indexed user content. Default storage is under the user's home directory.

- Do not delete, reset, or inspect local Chroma data without explicit user approval.
- Tests should prefer temporary paths or mocks when touching Chroma.
- Indexing changes must preserve document identity and content hash semantics unless the plan explains migration or reindexing behavior.
- If a change requires reindexing, the plan and final report must include user-data impact and rollback/mitigation notes.

## External Services

External integrations:

- Notion API, configured by environment token and API version.
- Tistory, configured by blog name and crawler/search behavior.
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

- Docs-only changes: path listing, `git status --short`, `git diff --check`.
- Syntax/import safety: `python -m compileall api core environments fetching indexing search main.py`.
- Unit tests: `uv run pytest` when tests exist.
- MCP contract: focused tests or smoke checks around `register_tools` and tool functions.
- Search/indexing: temp Chroma path or mock collection; avoid user data.
- Fetching: mocked HTTP/API responses; live checks only with approval.

## Harness Usage

`harness-plan` must read this document before choosing implementation boundaries. Review gates must check changed files against this architecture and directly relevant accepted ADRs.
