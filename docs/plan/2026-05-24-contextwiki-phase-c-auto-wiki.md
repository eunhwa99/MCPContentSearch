# ContextWiki Phase C Auto Wiki Foundation

## User Request

Start implementing Phase C from the latest `main`.

## Branch Preflight Result

- Original worktree: `/Users/eunhwa/IdeaProjects/MCPContentSearch`.
- Original branch: `feature/contextwiki-phase-b-connectors`.
- Original worktree state: dirty with existing documentation and harness changes, so no branch switching, pulling, or cleanup was performed there.
- Freshness check: `git fetch origin main` succeeded and updated `origin/main` to `08b73fc`.
- Isolated worktree: `/private/tmp/MCPContentSearch-phase-c`.
- Task branch: `feature/contextwiki-phase-c-auto-wiki`, created from `origin/main`.

## Scope and Non-Goals

Scope:

- Add the first Phase C Auto Wiki slice: citation-backed wiki page generation over existing ContextWiki search results.
- Keep the feature read-only: it may query ContextWiki search/metadata but must not mutate user ChromaDB or SQLite metadata.
- Add a wiki service module with deterministic output suitable for unit and MCP contract tests.
- Expose a new MCP tool for generating a wiki page from indexed ContextWiki evidence.
- Update README/client-facing docs for the new MCP tool.

Non-goals:

- No persistent wiki page store yet.
- No UI/dashboard.
- No LLM summarization dependency.
- No new external API calls.
- No local ChromaDB or SQLite reset, migration, inspection, or deletion.
- No advanced stale wiki page lifecycle yet; tombstoned documents remain filtered by existing ContextWiki retrieval gates.

## Acceptance Criteria

- `generate_wiki_page(topic, filters=None, top_k=8)` returns a stable dict with:
  - `topic`
  - `status`
  - `title`
  - `markdown`
  - `sections`
  - `citations`
  - `backlinks`
  - `used_chunks`
- When no evidence is available, the tool returns `status="insufficient_evidence"` with empty citations/backlinks and a caller-readable message.
- Generated Markdown includes citation markers that map back to returned chunk citations.
- Backlinks are derived from distinct source documents represented in the used chunks.
- The service respects existing source filters by delegating to `ContextSearchService`.
- Tests cover service behavior, MCP tool registration/contract, and a fake E2E flow.
- README documents the new Auto Wiki MCP tool and service module.

## Step Breakdown

| Step | Label | Work | Acceptance |
| --- | --- | --- | --- |
| 1 | `wiki-service` | Add a `wiki/` service module that consumes `ContextSearchService.search_context`. | Unit tests can generate a citation-backed Markdown page without Chroma or live APIs. |
| 2 | `mcp-tool` | Wire `WikiGenerationService` through `main.py` and `api/tools.py` as `generate_wiki_page`. | MCP contract tests show the new tool is registered and returns the documented shape. |
| 3 | `fake-e2e` | Extend fake ContextWiki E2E coverage to sync, search, and generate a wiki page. | E2E test verifies the tool uses managed chunks and returns citations/backlinks. |
| 4 | `docs` | Update README with the Phase C tool and module. | README reflects the new service without claiming persistent wiki storage or UI. |
| 5 | `verification-review` | Run focused tests and required verification before subagent review. | Verification results are recorded before review. |

## Files Likely To Change

- `wiki/__init__.py`
- `wiki/service.py`
- `api/tools.py`
- `main.py`
- `README.md`
- `tests/wiki/test_wiki_service.py`
- `tests/api/test_tools_contract.py`
- `tests/e2e/test_contextwiki_flow.py`
- `docs/plan/2026-05-24-contextwiki-phase-c-auto-wiki.md`

## Test and Verification Plan

Focused checks:

```bash
PYTHONPATH=. uv run pytest tests/wiki/test_wiki_service.py tests/api/test_tools_contract.py
```

Syntax/import check:

```bash
python -m compileall api core environments fetching indexing search storage wiki main.py
```

Full non-live test suite when feasible:

```bash
uv run pytest
```

If `uv run ...` fails because the local environment is unavailable, record the failure and run the closest dependency-free fallback.

## Architecture/ADR Constraints

- ADR 0001 keeps MCP tool formatting in `api/`, composition in `main.py`, search orchestration in `search/`, and new application behavior in a dedicated service module rather than tool handlers.
- ADR 0002 keeps SQLite metadata authoritative for citation-ready chunks; the wiki tool must use existing ContextWiki retrieval and citations instead of raw Chroma data.
- ADR 0003 tombstone behavior means wiki generation must rely on active search results and not hydrate deleted documents directly.
- ADR 0004 keeps GitHub/Web connectors behind source sync; wiki generation must not call external connectors directly.
- No new ADR is required for this first slice because it adds a bounded application service and MCP tool without changing persistence, connector, or metadata contracts.

## Risks and Rollback Notes

- Risk: The feature could imply persistent wiki storage. Mitigation: document this as read-only generation only.
- Risk: Wiki content could overclaim if generated without evidence. Mitigation: return insufficient evidence when no chunks are found and include citations for used chunks.
- Risk: Tool handlers could become business-logic heavy. Mitigation: keep generation logic in `wiki/service.py`.
- Rollback: remove the `wiki/` module, unregister the MCP tool wiring, and remove related tests/docs.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created isolated branch from latest `origin/main` because the primary worktree was dirty. | `git fetch origin main`; `git worktree add -b feature/contextwiki-phase-c-auto-wiki /private/tmp/MCPContentSearch-phase-c origin/main` |
| Plan document | completed | Added Phase C Auto Wiki foundation plan. | This file |
| Planning | in_progress | Reading architecture, ADRs, current service boundaries, and test patterns. | `.agents/docs/architecture.md`; ADR 0001-0004; `api/tools.py`; `main.py` |
| Implementation | completed | Added read-only wiki generation service, MCP wiring, README updates, and verification script coverage for `wiki/`. | `wiki/service.py`; `api/tools.py`; `main.py`; `README.md`; `scripts/verify_all.sh` |
| Focused verification | completed | Focused tests, full non-live verification, and diff whitespace checks passed after renaming the wiki test module. | `PYTHONPATH=. uv run pytest tests/wiki/test_wiki_service.py tests/api/test_tools_contract.py` -> 10 passed; `./scripts/verify_all.sh` -> 581 passed; `git diff --check` |
| Middle review | completed | First five-reviewer pass found actionable issues: raw error exposure, not-configured status, low-score evidence gating, stale architecture/client docs, and staged diff caveat. | Reviewers: Leibniz, Popper, Turing, Kant, Sartre |
| Refactor | completed | Addressed first-pass review findings: sanitized wiki errors, fixed not-configured status, added score gating, updated architecture/client docs, and staged all Phase C files. | `api/tools.py`; `wiki/service.py`; `.agents/docs/architecture.md`; `AGENTS.md`; `docs/contextwiki-core-understanding.md`; `git diff --cached --check` |
| Integration verification | completed | Reran focused and full non-live verification after review fixes. | `PYTHONPATH=. uv run pytest tests/wiki/test_wiki_service.py tests/api/test_tools_contract.py` -> 11 passed; `./scripts/verify_all.sh` -> 582 passed; `git diff --cached --check` |
| Final review | completed | Fresh five-reviewer pass 2 reported no actionable findings. | Reviewers: Harvey, Faraday, Russell, Carson, Kepler |
| PR delivery | in_progress | Preparing commit, push, and main-base PR after clean final review. | Pending |
