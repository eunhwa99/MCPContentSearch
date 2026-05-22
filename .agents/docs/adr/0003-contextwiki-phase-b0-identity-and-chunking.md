# ADR 0003: ContextWiki Phase B-0 Identity Lifecycle and Source-Aware Chunking

## Status

accepted

## Date

2026-05-22

## Context

Phase B will add GitHub and Web connectors. Those connectors make document identity, deletion, and citation precision more important than the MVP A Notion/Tistory flow:

- GitHub files can move, disappear, or change content often.
- Code citations need file path and line ranges, not only character-window excerpts.
- Web/docs sources can produce canonical URLs that differ from crawl URLs.
- Chroma vector entries can remain searchable unless metadata and retrieval agree on tombstone behavior.

ADR 0002 established SQLite as the metadata and citation store. Phase B-0 extends that contract before adding real GitHub/Web connectors.

## Decision

Extend the ContextWiki metadata contract with document identity lifecycle fields:

- `external_id`: connector-native stable identity across content revisions, such as `owner/repo:path` for GitHub.
- `canonical_url`: stable citation URL for the source document or file.
- `last_seen_at`: timestamp updated for every document observed during a successful full source sync.
- `last_seen_sync_id`: job-scoped marker updated for every observed document and used for stale cleanup decisions.
- `deleted_at`: soft-delete/tombstone timestamp for documents absent from a successful full source sync.
- `version_id`: optional connector version metadata, such as commit SHA or blob SHA, kept separate from stable identity.

Use `external_id` as the stable `document_id` when it is present. Keep `version_id` separate so content revisions do not create new logical documents.

After a successful full source sync, tombstone previously active documents from that source whose `document_id` was not seen in the sync. Tombstoning removes active chunk metadata from retrieval and returns managed chunk ids so the vector indexer can delete those Chroma documents when delete support is available. Failed or partial syncs must not tombstone missing documents.

Treat SQLite chunk metadata as the authoritative active-document gate. Vector cleanup happens after metadata commits and is best-effort; stale managed vector hits must be filtered through SQLite-backed retrieval and legacy search formatting.

Successful sync finalization, including optional stale document tombstoning and source/job success metadata, must commit atomically before best-effort vector cleanup runs.

At most one sync may run per source at a time. Sync start must be guarded in SQLite so overlapping same-source sync requests do not create competing lifecycle markers. The guard checks active `sync_jobs` rows directly, refreshes an internal heartbeat while a job is running, fails stale running jobs after a conservative timeout, and collapses duplicate running rows before allowing a replacement job to start. Per-document metadata commits and successful finalization are conditional on the owning job still being the active `RUNNING` job for that source, so a superseded job cannot publish active chunks or tombstone documents after losing its lease during an awaited vector operation.

Ingestion owns the source boundary. Documents fetched by a connector are normalized to the registry source id before chunking, cross-source `document_id` collisions are reserved and rejected in SQLite before vector writes, stale document claims from expired jobs are recoverable, guarded metadata commits reject source/document/chunk mismatches, and SQLite active chunk reads and stale cleanup require chunk/document source equality.

Add minimum source-aware chunking:

- Markdown files chunk by ATX or setext headings with heading-scoped line ranges.
- Code files chunk by contiguous line ranges sized by the current character budget.
- Plain text continues using the existing character-window chunker.

Answer citations preserve path and line-range metadata from structured context results so code/document answers can point back to source locations.

Function/class-aware code chunking, fingerprint deduplication, worker queues, retries, ACLs, and audit logs remain later-phase work.

## Consequences

- Existing SQLite metadata stores need additive schema evolution in `ensure_schema`; no table drop or user data reset is allowed.
- Search and citation fetches must treat tombstoned documents as inactive even if stale vector candidates are returned by Chroma. Managed vector hits require SQLite hydration; raw fallback is only for unmanaged legacy documents.
- Reappearing documents clear `deleted_at`, update `last_seen_at` and `last_seen_sync_id`, and reindex if tombstoned or content changed.
- Rechunking an unchanged document must reindex when generated chunk ids differ from stored chunk ids.
- A crashed process can leave a `RUNNING` job row behind, but the next sync attempt can recover it after the heartbeat timeout instead of requiring manual SQLite repair.
- GitHub/Web connector implementation can rely on stable identity, canonical URLs, tombstones, and line-range chunks before adding connector-specific fetching.
- Tests should use fake sources and temporary SQLite paths. Live external validation remains opt-in only.

## Alternatives Considered

- Delay lifecycle fields until real GitHub connector implementation: rejected because deleted/moved files would immediately produce stale search results.
- Use commit/blob SHA as `document_id`: rejected because stable document identity would change on every revision and break lifecycle cleanup.
- Keep all source-aware chunking for Phase I: rejected because GitHub code citations need file and line range precision in Phase B.
- Only filter tombstoned documents at vector metadata level: rejected because stale Chroma entries can still surface; SQLite metadata remains the authoritative retrieval gate.

## Related

- `.agents/docs/adr/0001-layered-mcp-content-search-architecture.md`
- `.agents/docs/adr/0002-contextwiki-metadata-and-citation-store.md`
- `docs/plan/2026-05-20-contextwiki-roadmap.md`
- `docs/plan/2026-05-22-contextwiki-phase-b0-readiness.md`
