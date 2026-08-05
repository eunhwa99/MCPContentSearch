# ADR 0006: Slim MCP Core Scope

## Status

accepted

Status note: ADR 0008 supersedes this ADR's optional search LLM query-rewrite
decision. The remaining slim MCP core scope stays accepted.

## Date

2026-06-10

## Context

`context-zip` grew from a focused MCP retrieval server into a broader demo
surface with generic website/docs crawling, dynamic web fallback, Auto Wiki
generation, a local Web Console, browser smokes, and extra legacy MCP tools.

That wider scope made the project harder to explain, verify, and maintain. The
current product direction is to return to a small MCP server that an LLM client
can attach to for source sync, retrieval, context fetch, and citation-backed
answers over explicitly configured knowledge sources.

ADR 0002 and ADR 0003 remain central: SQLite is the lifecycle/citation source of
truth beside Chroma, and source sync must preserve stable identity, active chunk
gating, and tombstone safety.

## Decision

Slim the current architecture to the MCP retrieval core.

Retain:

- FastMCP composition in `main.py`.
- Layered boundaries across `api`, `fetching`, `indexing`, `search`, `storage`,
  `core`, and `environments`.
- Source connectors for:
  - `source_notion`
  - `source_tistory`
  - `source_github`
  - `source_obsidian`
- SQLite source/job/document/chunk lifecycle metadata and Chroma vector
  retrieval.
- MCP tools:
  - `list_sources`
  - `sync_source`
  - `sync_all`
  - `get_sync_status`
  - `search_context`
  - `search_documents`
  - `fetch_context`
- Internal helper answer surface:
  - `CitationAnswerService.answer_with_citations(...)`
- Internal connector helper functions may still parse or fetch explicit
  Notion/GitHub targets for connector tests and implementation reuse, but they
  are not a retained MCP tool surface and must not become one-off target sync
  APIs without a new ADR.
- Obsidian is retained as a configured local-vault Markdown source through
  `CONTEXTZIP_OBSIDIAN_VAULT_PATH`, bounded by
  `CONTEXTZIP_OBSIDIAN_MAX_FILES` and
  `CONTEXTZIP_OBSIDIAN_MAX_FILE_BYTES`. It does not require a live Obsidian
  app, plugin, or API server, and verification should use temporary vaults
  unless the task has a plan and the user explicitly approves the bounded real
  vault check. Plan-exempt work must be reclassified as planned work or keep
  using a temporary vault.
- Optional search LLM query rewrite behind `search_context`, disabled by
  default. If explicitly enabled through `CONTEXTZIP_SEARCH_LLM_ENABLED=true`
  and a configured provider API key, the server may send the user's search
  query and normalized query terms to that external provider before
  Chroma/LlamaIndex retrieval. Internal helper-answer flows inherit that same
  egress because they reuse `search_context_for_answer` / `search_context`.
  This is external egress, not dynamic web fallback, and it must not fetch
  source content or mutate SQLite/Chroma.

Remove from the current production scope:

- Generic website/docs crawling and `source_web`.
- Website/docs configuration such as `CONTEXTZIP_WEB_*`.
- Auto Wiki generation, `generate_wiki_page`, wiki LLM synthesis, and wiki
  smoke scripts.
- Local Web Console, HTTP/browser reviewer UI, Web Console tests, and
  Playwright browser smoke gates.
- Dynamic web fallback and legacy live search/indexing MCP tools such as
  `search_content`, `search_notion`, `search_tistory`, `search_github`,
  `trigger_index_all_content`, and `get_index_status`.

This ADR supersedes the website/docs portion of ADR 0004 for current scope. ADR
0004's GitHub connector decision remains accepted. This ADR also supersedes ADR
0005's Auto Wiki decision for current scope.

## Consequences

- README, architecture docs, CI, and verification scripts must describe and run
  only retained MCP retrieval paths.
- Tests should focus on retained source registry behavior, source sync,
  metadata lifecycle, Chroma/SQLite active-result gating, chunk search,
  grouped document browsing, context fetch, and citation answers. Obsidian
  tests must use temporary vault directories by default and cover bounded-vault
  failure without stale cleanup.
- Stale vectors from previously indexed removed sources may remain in a user's
  local Chroma store, but retained retrieval must continue to gate managed hits
  through SQLite metadata before returning citations.
- A retained source disabled in configuration blocks future syncs but does not
  automatically hide already indexed active documents from retrieval. Those
  documents remain visible until cleanup or metadata changes mark them inactive.
- The optional query rewrite path remains in scope only as a default-disabled
  LLM-assistant aid. Documentation and tests must make clear that enabling
  rewrite permits external egress of query text. Disabling rewrite only
  disables query-rewrite egress; embeddings may still use external providers
  depending on the configured LlamaIndex embedding setup. Fully local or
  otherwise non-egress operation also requires local or non-egress embeddings.
- No local Chroma/SQLite data deletion, migration, reset, or inspection is part
  of this decision.
- Reintroducing website/docs crawling, Auto Wiki, a browser UI, dynamic web
  fallback, or live-search/indexing tools requires a new ADR that explains the
  scope, contracts, verification, and data-safety plan.

## Alternatives Considered

- Keep the broad portfolio demo and document it better: rejected because the
  user-facing product direction is a smaller MCP retrieval server.
- Hide the removed surfaces behind optional flags: rejected because dormant
  runtime paths still expand CI, docs, dependency, and review scope.
- Remove GitHub or Obsidian too and return to only Notion/Tistory: rejected
  because GitHub repository retrieval and local Obsidian vault retrieval are
  retained source connectors.

## Related

- `.agents/docs/architecture.md`
- `.agents/docs/adr/0001-layered-context-zip-architecture.md`
- `.agents/docs/adr/0002-context-zip-metadata-and-citation-store.md`
- `.agents/docs/adr/0003-context-zip-phase-b0-identity-and-chunking.md`
- `.agents/docs/adr/0004-context-zip-phase-b-connectors.md`
- `.agents/docs/adr/0005-context-zip-auto-wiki-llm-synthesis.md`
- `.agents/docs/adr/0008-background-sync-all-and-deterministic-retrieval.md`
- `docs/plan/2026-06-10-slim-mcp-core.md`
- `docs/plan/2026-06-11-restore-obsidian-pr25.md`
