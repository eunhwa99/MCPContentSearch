# ADR 0004: ContextWiki Phase B External Connectors

## Status

accepted

Status note: ADR 0006 supersedes the website/docs and `source_web` portion of
this ADR for current scope. The GitHub connector decision remains accepted.

## Date

2026-05-22

## Context

Phase B originally added GitHub repository and generic website/docs ingestion.
Unlike the original Notion/Tistory connectors, these sources can contain large
file trees, binary files, moved/deleted documents, canonical URLs that differ
from crawl URLs, and external rate-limit or robots constraints. The current slim
MCP scope retains GitHub and removes website/docs ingestion.

ADR 0002 established SQLite as the metadata and citation store. ADR 0003 established stable document identity, tombstones, version metadata, source-aware chunking, and successful-sync cleanup rules. Phase B needs connector-specific boundaries that preserve those contracts without storing secrets or touching local Chroma data directly.

## Decision

Add the GitHub connector as a current `fetching/` responsibility and register it
through the existing `SourceRegistry`.

Historical note: this ADR originally also added website/docs ingestion. ADR 0006
supersedes that part of the decision for current scope; website/docs is no
longer a production connector.

The current Phase B source id is:

- `source_github`

Historical superseded source id:

- `source_web` (superseded by ADR 0006 and removed from current scope)

Connector configuration is non-secret and environment-driven through `AppConfig`
fields such as GitHub repository specs, file limits, and user agent. GitHub
authentication is optional and referenced in source metadata as
`env:GITHUB_TOKEN`; the raw token is read only at runtime and must not be stored
in SQLite, docs, tests, or logs.

The GitHub connector produces `DocumentModel` records that satisfy the Phase B-0 lifecycle contract:

- stable `external_id` and `document_id`
- `canonical_url`
- `path`
- `version_id` when available
- source id/type metadata

GitHub ingestion uses the GitHub tree/blob API or equivalent mocked client behavior, filters to bounded text/code/markdown files, stores blob SHA as `version_id`, and uses GitHub blob URLs as canonical citations.

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

Connector fetches must fail the sync on required API/page fetch errors so source-wide tombstoning only runs after a complete bounded snapshot. Live external validation remains optional and must be explicitly requested.

## Consequences

- MCP tools can keep using `sync_source(source_id)` and `list_sources()` instead of adding connector-specific sync tools.
- Phase B connector tests should mock HTTP/API responses and use temporary metadata/vector state.
- GitHub cleanup can rely on existing `supports_stale_cleanup=True` only when
  the connector completed its bounded snapshot. GitHub cleanup is additionally
  limited to the current connector's fetched repository identities.
- Large repositories are intentionally limited by max file/response-size
  configuration until later queueing, retry/backoff, and observability phases.
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
