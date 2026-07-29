# ADR 0008: Background `sync_all` and Deterministic Retrieval

## Status

accepted

## Date

2026-07-29

## Context

The public MCP `sync_source(source_id)` contract already launches one source
sync in the background and asks callers to poll `get_sync_status(source_id)`.
The public `sync_all()` tool still waited for every selected source to finish.
That mismatch made bulk sync more likely to exceed an MCP request timeout and
forced clients to handle two different completion models.

The optional search LLM query-rewrite path also added configuration, external
query egress, debug vocabulary, tests, and runtime branches. Retrieval already
has deterministic query normalization, Chroma candidate lookup, SQLite active
validation, metadata fallback, and ranking. The project does not need a second
LLM call before retrieval to provide its retained MCP search contract.

## Decision

Make public `sync_all()` a background-launch aggregator:

- enumerate the selected configured sources
- launch or reuse each source's existing background sync through the same
  source-level SQLite guard used by `sync_source`
- return after launch decisions are known, without waiting for fetching,
  indexing, or cleanup to finish
- report a launch outcome for every selected source and an aggregate
  launch-oriented status
- require callers to poll `get_sync_status(source_id)` for each source until
  its latest job reaches `succeeded` or `failed`

This does not add a batch job table, scheduler, or separate batch-status tool.
The existing per-source SQLite job records remain authoritative. Background
work survives only while the MCP server process remains alive.

Remove the optional LLM query-rewrite feature completely:

- remove the query-rewriter module, constructor dependencies, runtime branches,
  debug fields, configuration, scripts, and focused tests
- make `search_context`, `search_documents`, and internal citation-answer
  helpers use the same deterministic retrieval path
- retain local query normalization and retrieval variants, Chroma candidate
  retrieval, SQLite active-result validation, metadata fallback, ranking,
  intent handling, and citation behavior

Default LlamaIndex embeddings remain a separate concern. Indexing may still
send document chunks and search may still send queries to the configured
embedding provider.

## Consequences

- `sync_source` and `sync_all` now share immediate-return plus per-source
  polling semantics.
- A successful bulk launch response means work was accepted or already
  running; it does not mean source content has finished syncing.
- Clients must inspect each launch result and then use `get_sync_status` before
  searching newly refreshed content.
- Bulk launch failures remain visible without hiding successfully launched
  sources.
- Search no longer has query-rewrite-specific environment variables, external
  chat-completion calls, failure modes, or debug vocabulary.
- Deterministic retrieval is easier to test and explain, but it cannot rely on
  an LLM to expand an underspecified query before vector retrieval.
- No local Chroma or SQLite migration, deletion, reset, or user-data inspection
  is part of this decision.

## Alternatives Considered

- Keep `sync_all` blocking and only increase client timeouts: rejected because
  bulk work should not depend on a single MCP request remaining open.
- Add a batch job table and `get_sync_all_status`: rejected because existing
  per-source jobs already provide the required completion truth.
- Keep query rewrite disabled by default: rejected because dormant code still
  expands configuration, external-egress, testing, and review scope.

## Supersedes

- ADR 0001's optional query-rewrite wording.
- ADR 0006's decision to retain optional search LLM query rewrite.
- The completion semantics of `sync_all` documented before this ADR.

## Related

- `.agents/docs/architecture.md`
- `.agents/docs/adr/0002-contextwiki-metadata-and-citation-store.md`
- `.agents/docs/adr/0006-slim-mcp-core-scope.md`
- `.agents/docs/adr/0007-sync-source-background-launch-contract.md`
- `docs/plan/2026-07-29-background-sync-all-remove-query-rewrite.md`
