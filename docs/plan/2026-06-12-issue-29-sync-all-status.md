# Issue 29 Sync All Status Implementation Plan

## User request

- Implement GitHub Issue #29: add `sync_all` and richer retained-source status surfaces.
- Use the latest `main` as the base and proceed with the main agent.

## Branch preflight result

- Original checkout `/Users/eunhwa/.codex/worktrees/c298/MCPContentSearch` was dirty (`environments/token.py` modified), so branch switching there was skipped.
- Fetched latest `origin/main` on 2026-06-12.
- Created isolated worktree `/Users/eunhwa/.codex/worktrees/issue29-sync-all/MCPContentSearch`.
- Current branch: `feature/issue-29-sync-all-status`
- Current status: clean worktree on branch created from `origin/main` commit `4143aae`.

## Scope

- Add retained-source bulk sync through a new MCP tool `sync_all`.
- Keep source sync safety by preserving existing per-source SQLite running-job guards.
- Expand source/status payloads with richer reviewer-readable operational state.
- Add focused tests and update README with the new workflow.

## Non-goals

- No background scheduler or daemon.
- No UI/dashboard work.
- No expansion beyond retained slim MCP sources.
- No local user Chroma or SQLite inspection/reset/migration.

## Acceptance criteria

- A caller can trigger `sync_all()` across retained sources.
- `sync_all()` runs per-source sync work concurrently and returns aggregate results.
- `list_sources()` and `get_sync_status()` expose:
  - `latest_success_at`
  - `latest_failure_at`
  - `document_count`
  - `chunk_count`
  - `latest_failure_reason`
  - `stale_cleanup_disabled_reason`
- Public error/failure fields remain redacted and reviewer-readable.
- Mixed success/failure/blocked scenarios are covered by tests.
- README documents the retained bulk-sync workflow.

## Step breakdown

1. `mcp-tool-contract`
   - Add `sync_all()` contract in `api/tools.py`.
   - Define aggregate response shape and safe per-source result payload formatting.
2. `metadata-status-surface`
   - Extend storage queries/model payload support for latest success/failure timestamps, counts, and stale-cleanup-disabled reason.
3. `ingestion-fanout`
   - Add concurrent retained-source fan-out in `IngestionService` while preserving existing per-source job guards.
4. `tests-and-docs`
   - Update contract/integration tests and README.

## Files likely to change

- Modify: `api/tools.py`
- Modify: `indexing/ingestion_service.py`
- Modify: `storage/metadata_store.py`
- Modify: `core/models.py`
- Modify: `README.md`
- Modify: `tests/api/test_tools_contract.py`
- Modify: `tests/indexing/test_ingestion_service.py`
- Possibly modify: `tests/e2e/test_phase_b_connectors_flow.py`
- Possibly modify: `tests/e2e/test_obsidian_connector_flow.py`

## Test and verification plan

- Focused syntax/contracts:
  - `python -m compileall api core environments fetching indexing search storage main.py`
  - `uv run pytest tests/api/test_tools_contract.py tests/indexing/test_ingestion_service.py -q`
- If needed for broader contract coverage:
  - `uv run pytest tests/e2e/test_phase_b_connectors_flow.py tests/e2e/test_obsidian_connector_flow.py -q`
- Required functional smoke before review:
  - `./scripts/verify_functional_e2e.sh`

## Functional smoke matrix

| Surface | Scenario | Safe mode | Expected result |
| --- | --- | --- | --- |
| MCP `sync_all` | Mixed retained sources with concurrent fan-out | Fake/temp sources only | Returns `summary` plus per-source results |
| MCP `list_sources` | Rich source payload after success/failure history | Temp SQLite | Count/timestamp/reason fields populated and redacted |
| MCP `get_sync_status` | Single source and all-source status views | Temp SQLite | Latest job plus rich source surface stay consistent |
| Per-source guard | Concurrent `sync_all` plus existing running job | Temp SQLite | Running source is reported as blocked/skipped without corruption |
| Stale cleanup visibility | Connector without stale cleanup support | Fake/temp connector | Disabled reason is surfaced truthfully |

## Architecture/ADR constraints

- Respect `.agents/docs/architecture.md` retained MCP boundaries.
- ADR 0002: SQLite remains the operational/citation source of truth.
- ADR 0003: active retrieval gates and stale cleanup semantics must stay truthful.
- ADR 0006: keep to slim MCP core retained sources and tool surface discipline.

## Risks and rollback notes

- Main risk: concurrent `sync_all` fan-out could blur per-source guard semantics if aggregate result mapping is sloppy.
- Main compatibility risk: changing source payload shape must remain additive and safe for existing callers.
- Rollback point: revert `sync_all` registration and status-field additions while preserving existing `sync_source` behavior.

## Additional notes

- Repository harness normally requires worker/reviewer subagents for non-atomic work. The user explicitly asked to proceed with the main agent first, so this execution uses main-agent implementation in the isolated worktree while still preserving the rest of the harness gates as far as tooling allows.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Fetched latest `origin/main` and created isolated worktree/branch from it. | `git fetch origin main`, `git worktree add -b feature/issue-29-sync-all-status ... origin/main` |
| Planning | completed | Wrote Issue 29 plan with concurrent `sync_all` design and verification scope. | `docs/plan/2026-06-12-issue-29-sync-all-status.md` |
| Implementation | completed | Added concurrent `sync_all`, additive rich source status payloads, SQLite status snapshot queries, README updates, and focused tests. | `git diff --stat` |
| Focused verification | completed | `compileall` and focused pytest passed. | `python -m compileall ...`, `uv run pytest tests/api/test_tools_contract.py tests/indexing/test_ingestion_service.py tests/test_app_composition.py -q` |
| Functional smoke | completed | Local retained-flow functional E2E gate passed. | `./scripts/verify_functional_e2e.sh` |
| Review gate | completed | First reviewer pass found doc drift, stale cleanup truthfulness, and indexer concurrency issues; fixes landed, verification reran, and the final fresh five-reviewer pass reported no actionable findings. | Fresh final reviewer pass: `Descartes`, `Rawls`, `Hypatia`, `Halley`, `Meitner` |
