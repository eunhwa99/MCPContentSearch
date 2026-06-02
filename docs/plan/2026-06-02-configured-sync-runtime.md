# Configured Sync Runtime Follow-up

## User Request

The Web Console configured sync still does not work correctly after the
previous GitHub search/sync PR. The visible symptom is that pressing configured
sync still does not actually index usable GitHub content.

## Branch Preflight Result

- Started from clean `feature/fix-configured-github-sync-search` worktree.
- `git fetch origin main` succeeded.
- Created fresh branch `feature/fix-configured-sync-runtime` from `origin/main`.
- Local `main` is checked out in another linked worktree, so this worktree was
  switched directly to a fresh feature branch from `origin/main` instead of
  checking out `main` here.
- No local user Chroma or SQLite data will be inspected or mutated without
  explicit approval.

## Scope And Non-goals

Scope:

- Find the root cause for configured source sync not indexing expected GitHub
  content through the Web Console/MCP source sync path.
- Distinguish configuration/runtime setup issues from code bugs.
- If code changes are needed, keep them scoped to configured source sync
  behavior, status/error reporting, and deterministic fake/temp tests.

Non-goals:

- Do not run live GitHub sync, mutate local user Chroma/SQLite, or inspect local
  indexed data without explicit user approval.
- Do not change GitHub authentication/token handling beyond safe diagnostics or
  error messages.
- Do not broaden search quality work unrelated to configured sync execution.

## Acceptance Criteria

- The root cause is identified with concrete evidence from the configured sync
  data flow.
- Configured sync either indexes deterministic fake/temp GitHub evidence
  correctly or returns a truthful actionable error when no repositories are
  configured.
- Web Console configured sync status does not leave the user with a misleading
  "running/discovering" state when the sync is actually blocked or completed
  with no configured work.
- Regression tests cover the failing path.

## Step Breakdown

1. Trace Web Console configured sync request through FastAPI endpoint,
   `IngestionService.sync_source`, `SourceRegistry`, GitHub connector config,
   sync job status, and UI polling.
2. Reproduce locally using fake/temp metadata/vector state and sanitized config
   diagnostics without reading secrets or user data.
3. Implement the smallest code or diagnostic/status fix supported by evidence.
4. Run focused tests, compile/diff checks, functional smoke, and review loop
   before delivery.

## Files Likely To Change

- `web_console/app.py`
- `web/app.js`
- `fetching/connectors.py`
- `indexing/ingestion_service.py`
- `environments/config.py`
- `tests/web_console/test_app.py`
- `tests/fetching/test_connectors.py`
- `tests/indexing/test_ingestion_service.py`
- `docs/contextwiki-core-understanding.md`

## Test And Verification Plan

- Focused pytest around configured source sync and GitHub connector config.
- `python -m compileall api core environments fetching indexing search storage wiki web_console main.py`
- `git diff --check`
- `./scripts/verify_functional_e2e.sh` or `./scripts/verify_all.sh` after code changes.

## Functional Smoke Matrix

| Feature | Surface | Data Mode | Expected Result | Status |
| --- | --- | --- | --- | --- |
| Configured GitHub sync | Web Console/API | fake/temp | Sync indexes deterministic documents or returns truthful config error | pending |
| Sync status polling | Web Console/API | fake/temp | Running/completed/failed state is accurate and actionable | pending |
| Live GitHub sync | Web Console/API | approval-gated | Not run without user approval and explicit source/data plan | blocked/gated |

## Architecture And ADR Constraints

- ADR 0002: SQLite metadata is the active citation/sync state store; tests use
  temporary persistence and must not inspect or mutate user data.
- ADR 0004: GitHub connector configuration is environment-driven, authentication
  is optional and secret values must never be stored or logged.
- `api/tools.py` and Web Console should remain thin wrappers around service
  boundaries.

## Risks And Rollback Notes

- Live GitHub sync can contact external services and mutate local Chroma/SQLite;
  keep validation fake/temp unless explicitly approved.
- Configuration diagnostics must not print token values or `.env` contents.
- If the issue is runtime config rather than code, report the exact missing
  setting and avoid unnecessary behavior changes.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created `feature/fix-configured-sync-runtime` from `origin/main`. | `git fetch origin main`; `git switch -c feature/fix-configured-sync-runtime origin/main` |
| Root-cause trace | completed | Local env has `GITHUB_TOKEN` but zero `CONTEXTWIKI_GITHUB_REPOSITORIES`, so `source_github` is disabled. Temp Web Console repro shows the internal job error is `Source source_github is disabled`, but API/UI redaction exposes only generic `Sync failed. See server logs for details.` and stopped-state UI ignores `error_message`. | Sanitized env diagnostic; temp `IngestionService.sync_source("source_github")`; temp `TestClient` `/api/sources/source_github/sync` and `/sync-status` |
| Implementation | completed | Worker `019e87f7-9865-7401-a580-1d411b6d0ed2` added a public GitHub disabled reason for empty `CONTEXTWIKI_GITHUB_REPOSITORIES`, used it in disabled-source failed jobs, allowlisted that exact public config error in Web Console safe payloads, and made stopped sync UI prefer `error_message`. Docs updated. | Changed `fetching/connectors.py`, `indexing/ingestion_service.py`, `web_console/payloads.py`, `web/app.js`, focused tests, and `docs/contextwiki-core-understanding.md` |
| Focused verification | completed | Focused tests and temp Web Console repro pass. API/status now show the actionable missing repository config message while secret-bearing errors remain redacted. | `PYTHONPATH=. uv run pytest tests/fetching/test_connectors.py tests/indexing/test_ingestion_service.py tests/web_console/test_app.py` -> 130 passed, 1 warning; temp `TestClient` repro |
| Functional smoke | completed | Syntax/diff checks and full local functional gate passed. Live GitHub sync remains approval-gated and was not run. | `node --check web/app.js`; `python -m compileall api core environments fetching indexing search storage wiki web_console main.py`; `git diff --check`; `./scripts/verify_all.sh` -> 680 passed; fake wiki smoke passed; 182 E2E/Web Console passed; Playwright smoke passed |
| Review loop | completed | Five fresh reviewers ran. All reported no actionable findings. Reviewers covered connector/ingestion disabled-source behavior, Web Console payload redaction, frontend sync UX, tests/docs, and integration risk. | Reviewers `019e87fd-96b2`, `019e87fd-991e`, `019e87fd-9d03`, `019e87fd-a10c`, `019e87fd-a500` |
| Delivery | completed | Committed, pushed `feature/fix-configured-sync-runtime`, and opened a main-base PR after the clean review pass. | PR #18: https://github.com/eunhwa99/MCPContentSearch/pull/18 |
