# Architecture Decision Records

This directory records architecture decisions for `MCPContentSearch`.

Harness planning reads this index first, then opens only accepted ADRs directly related to the requested change. Review gates treat accepted ADRs as contracts. If a diff conflicts with an accepted ADR, either change the diff or add a new ADR that supersedes the old decision.

## ADR Format

Use `template.md` for new records.

Required fields:

- `Status`: `proposed`, `accepted`, `deprecated`, or `superseded`
- `Date`: `YYYY-MM-DD`
- `Context`: problem and constraints
- `Decision`: selected approach and boundary
- `Consequences`: tradeoffs and follow-up obligations

File names should be numbered and descriptive:

```text
0001-layered-mcp-content-search-architecture.md
```

## Index

| ADR | Status | Topic |
| --- | --- | --- |
| [0001](0001-layered-mcp-content-search-architecture.md) | accepted | Layered MCP content search architecture |

## When to Add or Update ADRs

Add or update an ADR for:

- New cross-module architecture patterns.
- MCP tool contract strategy changes.
- Search/indexing persistence strategy changes.
- Chroma data migration or reindexing policy.
- External integration strategy changes.
- Configuration/secrets policy changes.
- Intentional departures from `.agents/docs/architecture.md`.

Do not add ADRs for ordinary local refactors, one-off bug fixes, or implementation details that do not constrain future work.
