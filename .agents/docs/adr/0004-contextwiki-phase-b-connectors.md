# ADR 0004: ContextWiki Phase B External Connectors

## Status

accepted

## Date

2026-05-22

## Context

Phase B adds GitHub repository and generic website/docs ingestion. Unlike the original Notion/Tistory connectors, these sources can contain large file trees, binary files, moved/deleted documents, canonical URLs that differ from crawl URLs, and external rate-limit or robots constraints.

ADR 0002 established SQLite as the metadata and citation store. ADR 0003 established stable document identity, tombstones, version metadata, source-aware chunking, and successful-sync cleanup rules. Phase B needs connector-specific boundaries that preserve those contracts without storing secrets or touching local Chroma data directly.

## Decision

Add GitHub and website/docs connectors as `fetching/` responsibilities and register them through the existing `SourceRegistry`.

The canonical Phase B source ids are:

- `source_github`
- `source_web`

Connector configuration is non-secret and environment-driven through `AppConfig` fields such as repository specs, web seed URLs, page/file limits, user agent, and crawl delay. GitHub authentication is optional and referenced in source metadata as `env:GITHUB_TOKEN`; the raw token is read only at runtime and must not be stored in SQLite, docs, tests, or logs.

Both connectors produce `DocumentModel` records that satisfy the Phase B-0 lifecycle contract:

- stable `external_id` and `document_id`
- `canonical_url`
- `path`
- `version_id` when available
- source id/type metadata

GitHub ingestion uses the GitHub tree/blob API or equivalent mocked client behavior, filters to bounded text/code/markdown files, stores blob SHA as `version_id`, and uses GitHub blob URLs as canonical citations.

GitHub stale cleanup is scoped to the repository identities fetched by the
current connector, such as `github:owner/repo:` document-id prefixes, rather
than every document under `source_github`. This keeps a configured GitHub sync
from tombstoning documents indexed by a separate one-off or ad hoc GitHub target
sync that shares the canonical `source_github` id. A repository removed from the
current configured repository list is therefore not automatically tombstoned by
later configured syncs until a provenance-aware or explicit manual cleanup
contract exists.

Website/docs ingestion supports bounded same-origin crawling and sitemap URLs. It fetches and applies robots.txt disallow rules before page fetches, enforces a per-response byte cap, extracts readable text/title/canonical URLs, and uses canonical URLs as stable document identities.

Connector fetches must fail the sync on required API/page fetch errors so source-wide tombstoning only runs after a complete bounded snapshot. Live external validation remains optional and must be explicitly requested.

## Consequences

- MCP tools can keep using `sync_source(source_id)` and `list_sources()` instead of adding connector-specific sync tools.
- Phase B connector tests should mock HTTP/API responses and use temporary metadata/vector state.
- GitHub/Web source cleanup can rely on existing `supports_stale_cleanup=True` only when the connector completed its bounded snapshot. GitHub cleanup is additionally limited to the current connector's fetched repository identities; website cleanup remains source-wide for its configured crawl scope.
- Large repositories and broad websites are intentionally limited by max file/page/response-size configuration until later queueing, retry/backoff, and observability phases.
- Function/class-aware code chunking, advanced HTML readability extraction, retries, audit logs, ACLs, and live smoke tests remain later-phase work.

## Alternatives Considered

- Add connector-specific MCP tools such as `sync_github_repository` or `sync_web_url`: deferred because Phase B can reuse the existing source registry and sync contract while keeping MCP surface smaller.
- Store GitHub/Web source definitions in SQLite through an MCP registration tool: deferred because static environment-driven sources are enough for the first production slice and avoid new mutation/security contracts.
- Ignore robots.txt during tests and development: rejected because the roadmap explicitly calls for robots/rate-limit safety before website/docs ingestion ships.

## Related

- `.agents/docs/adr/0001-layered-mcp-content-search-architecture.md`
- `.agents/docs/adr/0002-contextwiki-metadata-and-citation-store.md`
- `.agents/docs/adr/0003-contextwiki-phase-b0-identity-and-chunking.md`
- `docs/plan/2026-05-20-contextwiki-roadmap.md`
- `docs/plan/2026-05-22-contextwiki-phase-b-connectors.md`
