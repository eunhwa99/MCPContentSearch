# User request

Diagnose why Notion sync appears stuck, then implement both:

- better root-cause logs while Notion fetch is running
- intermediate progress/heartbeat updates so long upstream fetch phases do not look dead

The user explicitly approved bypassing subagent orchestration for this work in the current session.

## Branch preflight result

- Current worktree: `/Users/eunhwa/.codex/worktrees/eab9/MCPContentSearch`
- Current branch: `feature/fix-dotenv-loading`
- Worktree state: dirty because the earlier dotenv-loading task is still uncommitted in this branch/worktree.
- Branch safety:
  - Because the worktree is already dirty, I did not switch branches or alter branch topology here.
  - The user explicitly approved continuing direct implementation in this worktree after the subagent-orchestration blocker was surfaced.

## Scope and non-goals

### Scope

- Add Notion fetch progress events that surface search completion and per-page fetch progress.
- Wire those events into sync-job heartbeat/progress refresh so long fetch phases do not leave a stale-looking running job.
- Add operator-facing logs that make it obvious whether sync is still in upstream fetch, which page is being fetched, and where a failure occurred.
- Add focused tests around the new progress callback/event path and ingestion-side handling.

### Non-goals

- Do not change public MCP tool names or response shapes.
- Do not inspect or print Notion secrets or page content.
- Do not redesign the full sync-job schema or add long-lived new DB columns.
- Do not change connector behavior outside the Notion progress/observability path.

## Acceptance criteria

- A long-running Notion fetch emits structured progress callbacks during page discovery/fetching.
- Ingestion uses those callbacks to keep the running sync alive during long upstream fetch phases.
- Logs clearly identify Notion search completion, page fetch start/completion, and page-context failures.
- Focused tests fail before the implementation and pass after it.

## Step breakdown

1. `notion-progress-tests`
   - Add red tests for fetch progress callback events and ingestion-side heartbeat/progress logging.
   - Files: `tests/fetching/test_notion.py`, `tests/indexing/test_ingestion_service.py`, optionally `tests/fetching/test_connectors.py`.
2. `notion-progress-implementation`
   - Extend `fetch_notion_pages()` with optional progress callback events.
   - Pass the callback through `NotionSourceConnector`.
   - Hook callback handling in `IngestionService` so long Notion fetch phases refresh heartbeat and log progress.
   - Files: `fetching/notion.py`, `fetching/connectors.py`, `indexing/ingestion_service.py`.
3. `verification`
   - Run focused tests plus syntax/functional smoke relevant to sync behavior.

## Files likely to change

- `docs/plan/2026-06-15-notion-fetch-observability.md`
- `fetching/notion.py`
- `fetching/connectors.py`
- `indexing/ingestion_service.py`
- `tests/fetching/test_notion.py`
- `tests/indexing/test_ingestion_service.py`
- `tests/fetching/test_connectors.py` if callback plumbing needs direct coverage

## Test and verification plan

- Red step:
  - focused pytest on the new Notion/ingestion tests and confirm expected failures
- Green step:
  - `uv run pytest tests/fetching/test_notion.py tests/indexing/test_ingestion_service.py tests/fetching/test_connectors.py -q`
  - `uv run python -m compileall fetching indexing main.py`
  - `./scripts/verify_functional_e2e.sh`

## Functional smoke matrix

| Surface | Scenario | Safe mode | Expected result | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| Notion sync background job | Long upstream fetch before indexing | Local fake/temp only | Running job stays alive and emits progress logs during upstream fetch | pending | Pending |
| MCP status surface | Notion running sync re-entry | Local fake/temp only | Existing running job is reused, but logs now show upstream fetch stage instead of silent `0/0/0` | pending | Pending |

## Architecture constraints

- Keep Notion API behavior inside `fetching/notion.py`.
- Keep MCP contract formatting inside `api/`.
- Keep sync-job orchestration and heartbeat refresh logic inside `indexing/` and `storage/`.
- Do not add secret-bearing logs.

## Risks and rollback notes

- Risk: too-chatty progress logs could create noise.
- Mitigation: use bounded, page-level logs and redact failure context.
- Risk: ingestion-side heartbeat refresh during fetch might mask genuinely dead jobs for longer than before.
- Mitigation: refresh only from active progress callbacks, not on an idle timer.
- Rollback: revert the new progress callback path in `fetching/` and ingestion handling in `indexing/`.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Planning | completed | Wrote a dedicated plan and recorded the explicit user approval to bypass subagent orchestration for this task. | `docs/plan/2026-06-15-notion-fetch-observability.md` |
| Root-cause analysis | completed | Verified that Notion sync is stuck-looking because the running job is reused while upstream fetch happens before indexing progress is recorded; current timeout is 24h, so the job stays `running` for a long time. | `get_sync_status(source_notion)`; `fetching/notion.py`; `indexing/ingestion_service.py`; `storage/metadata_store.py` |
| Focused test design | completed | Added red tests for Notion fetch progress callbacks, connector callback plumbing, and ingestion-side heartbeat/log handling. | Focused pytest initially failed with missing callback/plumbing/log assertions, then passed |
| Implementation | completed | Added structured Notion fetch progress events, connector callback plumbing, ingestion-side heartbeat refresh during upstream fetch, and operator-facing upstream fetch logs. | `fetching/notion.py`; `fetching/connectors.py`; `indexing/ingestion_service.py` |
| Focused verification | completed | Focused fetch/ingestion suite and syntax checks passed in both the feature worktree and the main checkout used by Claude Desktop. | worktree: `uv run pytest tests/fetching/test_notion.py tests/fetching/test_connectors.py tests/indexing/test_ingestion_service.py -q` -> `100 passed`; `uv run python -m compileall fetching indexing main.py`; main checkout: same commands passed |
| Functional smoke | completed | Retained functional E2E gate stayed green after the observability change in the feature worktree. | `./scripts/verify_functional_e2e.sh` -> `25 passed in 4.22s` |
| Review gate | blocked | Repo policy still requires `$subagent-review-loop`, but the available subagent tool policy only permits delegation when explicitly requested. I did not claim that review ran. | Active `multi_agent_v1.spawn_agent` policy; repo review requirement in `AGENTS.md` / `.agents/docs/harness-engineering.md` |
