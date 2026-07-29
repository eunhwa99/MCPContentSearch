# ADR 0004: ContextWiki Phase B External Connectors

## Status

accepted

Status note: ADR 0006 supersedes the website/docs and `source_web` portion of
this ADR for current scope. The GitHub connector decision remains accepted, and
the PR #25 Obsidian local-vault connector is retained as a configured source.

## Date

2026-05-22

## Context

Phase B originally added GitHub repository and generic website/docs ingestion.
Unlike the original Notion/Tistory connectors, these sources can contain large
file trees, binary files, moved/deleted documents, canonical URLs that differ
from crawl URLs, and external rate-limit or robots constraints. PR #25 later
added an Obsidian local-vault connector with similar filesystem traversal and
partial-snapshot safety concerns. The current slim MCP scope retains GitHub and
Obsidian while removing website/docs ingestion.

ADR 0002 established SQLite as the metadata and citation store. ADR 0003 established stable document identity, tombstones, version metadata, source-aware chunking, and successful-sync cleanup rules. Phase B needs connector-specific boundaries that preserve those contracts without storing secrets or touching local Chroma data directly.

## Decision

Add the GitHub and Obsidian connectors as current `fetching/` responsibilities
and register them through the existing `SourceRegistry`.

Historical note: this ADR originally also added website/docs ingestion. ADR 0006
supersedes that part of the decision for current scope; website/docs is no
longer a production connector.

The current retained connector source ids from this ADR family are:

- `source_github`
- `source_obsidian`

Historical superseded source id:

- `source_web` (superseded by ADR 0006 and removed from current scope)

Connector configuration is non-secret and environment-driven through `AppConfig`
fields such as GitHub repository specs with optional `@ref`,
`CONTEXTWIKI_GITHUB_DEFAULT_REF`, file limits, user agent, and
`CONTEXTWIKI_OBSIDIAN_VAULT_PATH`. GitHub authentication is optional and
referenced in source metadata as `env:GITHUB_TOKEN`; the raw token is read only
at runtime and must not be stored in SQLite, docs, tests, or logs. Obsidian
does not require a token, live app, plugin, or API server.

The GitHub connector produces `DocumentModel` records that satisfy the Phase B-0 lifecycle contract:

- stable `external_id` and `document_id`
- `canonical_url`
- `path`
- `version_id` when available
- source id/type metadata

GitHub ingestion uses the GitHub tree/blob API or equivalent mocked client
behavior, filters to bounded text/code/markdown files, stores blob SHA as
`version_id`, uses GitHub blob URLs as canonical citations, and falls back to
`CONTEXTWIKI_GITHUB_DEFAULT_REF` when a repository spec omits `@ref`.

Obsidian ingestion reads bounded Markdown files from the configured local vault,
skips hidden and Obsidian metadata directories, parses frontmatter titles when
available, and uses `obsidian://open` canonical URLs. Obsidian bounds are
configured through positive integer `CONTEXTWIKI_OBSIDIAN_MAX_FILES` and
`CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES` values. Verification must use temporary
vaults unless both a plan and explicit user approval authorize the real vault
path.

GitHub stale cleanup is scoped to the repository identities fetched by the
current connector, such as `github:owner/repo:` document-id prefixes, rather
than every document under `source_github`. This keeps a configured GitHub sync
from tombstoning documents indexed by another GitHub connector instance or
historical helper flow that shares the canonical `source_github` id. A
repository removed from the current configured repository list is therefore not
automatically tombstoned by later configured syncs until a provenance-aware or
explicit manual cleanup contract exists.

Historical website/docs ingestion was designed to support bounded same-origin
crawling and sitemap URLs. ADR 0006 removes that path from current scope,
including `source_web` and website/docs configuration.

Connector fetches must fail the sync on required API/page fetch errors so
source-wide tombstoning only runs after a complete bounded snapshot. A disabled
connector blocks future syncs but does not automatically hide already indexed
active documents from retrieval until later cleanup or metadata changes.
Live external validation is blocked unless the task has a plan and the user
explicitly approves the bounded check. Plan-exempt work must be reclassified as
planned work or keep using fake/temporary substitutes.
For Obsidian, unreadable notes, traversal errors, or exceeded file count/byte
bounds must fail the sync before stale cleanup can tombstone missing active
documents.

## Consequences

- MCP tools can keep using `sync_source(source_id)` and `list_sources()` instead of adding connector-specific sync tools.
- Phase B connector tests should mock HTTP/API responses, use temporary
  Obsidian vaults, and use temporary metadata/vector state.
- GitHub cleanup can rely on existing `supports_stale_cleanup=True` only when
  the connector completed its bounded snapshot. GitHub cleanup is additionally
  limited to the current connector's fetched repository identities.
- Obsidian cleanup can rely on stale cleanup only after a complete local-vault
  snapshot. Incomplete filesystem snapshots, including exceeded file count or
  file byte bounds, must fail safely and preserve active metadata.
- Large repositories and Obsidian vaults are intentionally limited by max
  file/response-size configuration until later queueing, retry/backoff, and
  observability phases.
- Website/docs cleanup, broad-site crawling, robots handling, and web
  page-limit constraints are historical superseded scope under ADR 0006.
- Function/class-aware code chunking, advanced HTML readability extraction, retries, audit logs, ACLs, and live smoke tests remain later-phase work.

## Alternatives Considered

- Add connector-specific MCP tools such as `sync_github_repository`: deferred
  because Phase B can reuse the existing source registry and sync contract while
  keeping MCP surface smaller.
- Store GitHub source definitions in SQLite through an MCP registration tool:
  deferred because static environment-driven sources are enough for the first
  production slice and avoid new mutation/security contracts.
- Website/docs-specific alternatives such as `sync_web_url` and robots handling
  are historical superseded scope under ADR 0006.

## Related

- `.agents/docs/adr/0001-layered-mcp-content-search-architecture.md`
- `.agents/docs/adr/0002-contextwiki-metadata-and-citation-store.md`
- `.agents/docs/adr/0003-contextwiki-phase-b0-identity-and-chunking.md`
- `docs/plan/2026-05-20-contextwiki-roadmap.md`
- `docs/plan/2026-05-22-contextwiki-phase-b-connectors.md`
