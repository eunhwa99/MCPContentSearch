# ADR 0001: Layered MCP Content Search Architecture

## Status

accepted

## Date

2026-05-20

## Context

`MCPContentSearch` exposes MCP tools over FastMCP and combines local vector search, live Notion/Tistory search, and background indexing. The existing repository already has clear module directories: `api`, `search`, `indexing`, `fetching`, `core`, `environments`, and `main.py`.

Without a documented boundary, future changes can easily put external API logic in tool handlers, mutate Chroma from search formatting code, or make configuration and secret handling harder to review.

## Decision

Keep a layered MCP content search architecture:

- `main.py` composes dependencies and starts FastMCP.
- `api/tools.py` owns MCP tool registration, parameters, caller-visible formatting, and delegation.
- `search/` owns local search and dynamic fallback orchestration.
- `indexing/` owns document conversion, dedup/update checks, status, and Chroma/LlamaIndex writes.
- `fetching/` owns Notion/Tistory API and crawler behavior.
- `core/` owns shared models, exceptions, and utilities.
- `environments/` owns configuration and environment-token access.

Cross-module behavior should flow through these boundaries instead of reaching across layers for convenience.

## Consequences

- MCP tool changes must review tool contract and service delegation separately.
- Search changes must not mutate Chroma except through indexing services.
- Fetcher changes must keep API-specific parsing and error handling in `fetching/`.
- Configuration and secret handling remains isolated under `environments/`.
- Review gates should flag code that bypasses these boundaries without an explicit plan and ADR update.

## Alternatives Considered

- Tool-centric design with all behavior in `api/tools.py`: rejected because it would make MCP contracts, fetching, search, and indexing harder to test and review.
- Framework-heavy service container: rejected because current project size does not need new dependency injection infrastructure.

## Related

- `.agents/docs/architecture.md`
