# ADR 0002: ContextWiki Metadata and Citation Store

## Status

accepted

## Date

2026-05-20

## Context

ContextWiki needs more operational state than Chroma metadata alone can safely provide. The MVP must track sources, sync jobs, document hashes, chunk metadata, and citation fetch data while preserving the existing Chroma/LlamaIndex vector search path.

The system also needs deterministic tests that do not inspect or mutate local user Chroma data and do not require live Notion/Tistory credentials.

## Decision

Add a SQLite metadata store beside Chroma:

- Chroma remains responsible for vector retrieval.
- SQLite stores source records, sync jobs, document hashes, and citation-ready chunks.
- Source auth is stored as an environment-variable reference such as `env:NOTION_API_KEY`, never as a raw token.
- Chunk ids are deterministic from document id, chunk index, and chunk content hash.
- `answer_with_citations` may only cite chunks that exist in the metadata store.
- Required verification uses fake sources and temporary persistence. Live external API smoke tests are opt-in only.

## Consequences

- Sync code must keep SQLite metadata and Chroma writes aligned through idempotent upserts and deterministic ids.
- Existing Chroma data may not have citation metadata until sources are synced through the new ingestion path.
- Tests can cover source sync, search, fetch, and answer flows without external credentials.
- Future GitHub/Web/PDF connectors should reuse the same source/job/document/chunk schema.

## Alternatives Considered

- Store all metadata only in Chroma: rejected because sync jobs, source status, retry state, and context fetches need queryable operational records.
- Replace Chroma with a relational/vector hybrid database: rejected because it is too large for MVP A and would obscure the existing project evolution story.
- Require live external APIs for E2E tests: rejected because network, credentials, rate limits, and private data make CI unreliable and risky.

## Related

- `.agents/docs/adr/0001-layered-mcp-content-search-architecture.md`
- `docs/plan/2026-05-20-contextwiki-roadmap.md`
- `docs/plan/2026-05-20-contextwiki-mvp-a.md`
