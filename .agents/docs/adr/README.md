# Architecture Decision Records

This directory records historical architecture decisions for
`context-zip`.

For current work, the maintained source of truth is
`.agents/docs/architecture.md`. Harness planning and review do not require
reading ADRs. Keep this directory as optional historical context for older plan
docs, superseded decisions, or cases where a reader explicitly wants the
original decision record.

If an ADR and `.agents/docs/architecture.md` disagree, follow the architecture
doc and update the historical ADR wording only when that extra archival cleanup
is worth doing.

## ADR Format

Use `template.md` only when you explicitly want to preserve a standalone
decision record in this archive.

Required fields:

- `Status`: `proposed`, `accepted`, `deprecated`, or `superseded`
- `Date`: `YYYY-MM-DD`
- `Context`: problem and constraints
- `Decision`: selected approach and boundary
- `Consequences`: tradeoffs and follow-up obligations

File names should be numbered and descriptive:

```text
0001-layered-context-zip-architecture.md
```

## Index

| ADR | Status | Topic |
| --- | --- | --- |
| [0001](0001-layered-context-zip-architecture.md) | accepted, query-rewrite wording superseded by [0008](0008-background-sync-all-and-deterministic-retrieval.md) | Layered MCP content search architecture |
| [0002](0002-context-zip-metadata-and-citation-store.md) | accepted | ContextZip metadata and citation store |
| [0003](0003-context-zip-phase-b0-identity-and-chunking.md) | accepted | ContextZip Phase B-0 identity lifecycle and source-aware chunking |
| [0004](0004-context-zip-phase-b-connectors.md) | accepted, website/docs superseded by [0006](0006-slim-mcp-core-scope.md) | ContextZip Phase B GitHub connector, retained Obsidian local-vault connector, and superseded website/docs connector |
| [0005](0005-context-zip-auto-wiki-llm-synthesis.md) | superseded by [0006](0006-slim-mcp-core-scope.md) for current scope | Historical ContextZip Auto Wiki LLM synthesis boundary |
| [0006](0006-slim-mcp-core-scope.md) | accepted, query-rewrite wording superseded by [0008](0008-background-sync-all-and-deterministic-retrieval.md) | Slim MCP core scope |
| [0007](0007-sync-source-background-launch-contract.md) | accepted; completion attribution superseded by [0009](0009-exact-sync-job-status-observation.md), MCP-process execution ownership superseded by [0010](0010-durable-all-source-sync-worker.md) | Public MCP `sync_source` background-launch contract and internal blocking execution split |
| [0008](0008-background-sync-all-and-deterministic-retrieval.md) | deterministic retrieval and launch aggregation accepted; completion attribution superseded by [0009](0009-exact-sync-job-status-observation.md), MCP-process execution ownership superseded by [0010](0010-durable-all-source-sync-worker.md) | Public `sync_all` launch aggregation and removal of LLM query rewrite |
| [0009](0009-exact-sync-job-status-observation.md) | accepted | Exact sync-job status observation through paced, bounded short MCP requests |
| [0010](0010-durable-all-source-sync-worker.md) | accepted | Durable SQLite execution ownership plus LaunchAgent-supervised all-source sync worker (bounded cross-source concurrency) |

## When to Add or Update ADRs

Add or update an ADR only when preserving the separate historical decision
record is itself useful.

Typical cases:

- New cross-module architecture patterns.
- MCP tool contract strategy changes.
- Search/indexing persistence strategy changes.
- Chroma data migration or reindexing policy.
- External integration strategy changes.
- Configuration/secrets policy changes.
- Architecture changes worth preserving historically after updating
  `.agents/docs/architecture.md`.

Do not add ADRs for ordinary local refactors, one-off bug fixes, or
implementation details that do not constrain future work.
