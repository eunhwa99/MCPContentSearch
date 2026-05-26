# Web Console Source Sync

## User Request

Add source sync APIs to the Phase C.5 local Web Console and make source sync runnable from the browser UI. The user also asked for a less tedious GitHub flow: entering a GitHub owner URL such as `github.com/eunhwa99` should discover that owner's repositories and sync them without manually editing `CONTEXTWIKI_GITHUB_REPOSITORIES`.

## Branch Preflight Result

- Worktree: `/private/tmp/MCPContentSearch-phase-c5`
- Branch: `feature/contextwiki-web-console-source-sync`, created from `origin/main`.
- Starting state: clean (`git status --short --branch` showed no file changes).
- Existing Phase C.5 branch had already been merged into `main`, so this work is a fresh follow-up branch rather than editing `main`.

## Scope and Non-Goals

- Add local-only HTTP source sync endpoints to `web_console/app.py`.
- Add a GitHub target sync endpoint that accepts `owner/repo@branch`, repository URLs, and owner URLs such as `github.com/eunhwa99`.
- Add browser controls in `web/` so configured sources or an ad hoc GitHub target can be synced without an MCP client.
- Keep configured source sync delegated to existing `IngestionService.sync_source(source_id)`.
- Keep GitHub repo discovery in the `fetching` boundary, not embedded in UI code.
- Add focused tests with fake dependencies and mocked GitHub HTTP behavior; do not perform live GitHub/Web/Notion/Tistory calls.
- Do not add persistent source registration, config-file editing, background queueing, stale-job cleanup UI, auth, deployment, or multi-user behavior.
- Do not inspect, delete, reset, or migrate local Chroma or SQLite user data.

## Acceptance Criteria

- `POST /api/sources/{source_id}/sync` runs a configured source sync and returns the existing sync job JSON shape.
- `GET /api/sources/{source_id}/sync-status` returns the source and latest job for quick UI refresh.
- `POST /api/github/sync` accepts a GitHub owner URL and expands it into concrete repository specs using GitHub's repository list API.
- One-off GitHub target sync disables source-wide stale cleanup so an ad hoc subset cannot tombstone previously indexed GitHub documents.
- Missing services return safe 503 responses.
- Sync failures return structured safe error responses without logging secrets.
- The browser Sources panel shows a Sync button per source, displays source status metadata, refreshes after sync, and includes a GitHub target input.
- README documents the new local Web Console sync controls.
- Focused web console/GitHub discovery tests pass and JavaScript syntax is checked.

## Step Breakdown

1. Extend `ConsoleDependencies` and `create_default_app()` to retain `IngestionService`.
2. Add configured source sync and status HTTP routes.
3. Add GitHub owner/repo target parsing and repository discovery under `fetching/github.py`.
4. Add one-off GitHub target sync that reuses `IngestionService` with stale cleanup disabled.
5. Update Web Console UI rendering and event handling for per-source and GitHub target sync.
6. Add or update tests in `tests/web_console/test_app.py` and `tests/fetching/test_github.py`.
7. Run focused verification, then broader verification if the local uv environment is healthy.
8. Run required fresh five-reviewer `$subagent-review-loop`; fix actionable findings and repeat until clean.
9. Commit, push, and create a `main`-base PR.

## Files Likely To Change

- `fetching/connectors.py`
- `fetching/github.py`
- `web_console/app.py`
- `web/index.html`
- `web/app.js`
- `web/styles.css`
- `tests/web_console/test_app.py`
- `tests/fetching/test_github.py`
- `README.md`
- `docs/plan/2026-05-25-web-console-source-sync.md`

## Test and Verification Plan

- Focused API/UI-related tests: `uv run --python 3.13 pytest tests/web_console/test_app.py tests/fetching/test_github.py`
- JavaScript syntax: `node --check web/app.js`
- Broader if healthy: `./scripts/verify_all.sh`
- Review gate: fresh five-reviewer `$subagent-review-loop` after verification.

## Architecture and ADR Constraints

- ADR 0001: preserve layered boundaries; Web Console routes delegate to service objects, and GitHub discovery stays under `fetching`.
- ADR 0002: SQLite metadata remains the source/job/chunk lifecycle store; tests must use fake or temporary state.
- ADR 0004: keep canonical source ids (`source_github`, `source_web`) and reuse `sync_source(source_id)` semantics rather than adding connector-specific MCP tools.

## Risks and Rollback Notes

- Live source sync can contact external services and write to configured local Chroma/SQLite stores when a user clicks Sync. The UI must make this an explicit button action.
- Owner URL sync can be slow or broad for accounts with many repositories; this is local developer tooling and still bounded by existing per-repository file limits.
- If sync is interrupted, existing metadata stale-running-job behavior still applies; this change does not implement cleanup.
- Rollback is removing the new local HTTP routes and UI controls; existing MCP `sync_source` behavior remains unchanged.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created `feature/contextwiki-web-console-source-sync` from `origin/main` in the existing clean Phase C.5 worktree. | `git status --short --branch`, `git switch -c feature/contextwiki-web-console-source-sync origin/main` |
| Planning | completed | Defined configured sync plus GitHub owner URL discovery scope and data-safety rules. | This plan |
| Implementation | completed | Added configured source sync/status routes, GitHub owner/repo discovery and one-off sync, browser controls, README docs, and focused tests. | Local diff |
| Focused verification | completed | Focused tests, JS syntax check, compile smoke, and full verification passed before review. | `PYTHONPATH=. uv run --python 3.13 pytest tests/web_console/test_app.py tests/fetching/test_github.py` -> 118 passed; `node --check web/app.js`; `python -m compileall fetching web_console tests/web_console/test_app.py tests/fetching/test_github.py`; `./scripts/verify_all.sh` -> 664 passed |
| Review pass 1 | completed | Five fresh reviewers found actionable secret-redaction gaps in GitHub target errors and source/job sync payloads, plus one UI busy-state race. | Reviewers `019e5f1a-7882`, `019e5f1a-9ba2`, `019e5f1a-c2ad`, `019e5f1a-e777`, `019e5f1b-1024` |
| Review pass 1 remediation | completed | Added safe source/job/GitHub sync payload projection, stopped echoing raw failing GitHub targets, added secret regression tests, and applied busy state to dynamically rendered source buttons. | `PYTHONPATH=. uv run --python 3.13 pytest tests/web_console/test_app.py tests/fetching/test_github.py` -> 123 passed; `node --check web/app.js`; `python -m compileall fetching web_console tests/web_console/test_app.py tests/fetching/test_github.py`; `./scripts/verify_all.sh` -> 669 passed |
| Review pass 2 | completed | Five fresh reviewers found actionable auth_ref source projection and ingestion exception redaction gaps. One reviewer had no actionable findings. | Reviewers `019e5f20-9a3f`, `019e5f21-0d48`, `019e5f21-31d8`, `019e5f21-58dd`, `019e5f21-7b7f` |
| Review pass 2 remediation | completed | Redacted unsafe `auth_ref` values from Web Console source payloads and redacted token-like sync exception details before ingestion logging/storage while preserving non-secret diagnostic text. | `PYTHONPATH=. uv run --python 3.13 pytest tests/web_console/test_app.py tests/fetching/test_github.py tests/indexing/test_ingestion_service.py` -> 151 passed; `node --check web/app.js`; `python -m compileall indexing fetching web_console tests/web_console/test_app.py tests/fetching/test_github.py tests/indexing/test_ingestion_service.py`; `./scripts/verify_all.sh` -> 670 passed |
| Review pass 3 | completed | Five fresh reviewers found actionable ingestion redaction coverage gaps and ad hoc GitHub target sync source metadata mutation risk. Two reviewers had no actionable findings. | Reviewers `019e5f26-a44c`, `019e5f26-cb7e`, `019e5f26-ec79`, `019e5f27-1046`, `019e5f27-33e9` |
| Review pass 3 remediation | completed | Broadened ingestion secret redaction coverage and added `IngestionService(register_source_config=False)` so one-off GitHub target sync does not rewrite configured source metadata. | `PYTHONPATH=. uv run --python 3.13 pytest tests/web_console/test_app.py tests/fetching/test_github.py tests/indexing/test_ingestion_service.py` -> 152 passed; `node --check web/app.js`; `python -m compileall indexing fetching web_console tests/web_console/test_app.py tests/fetching/test_github.py tests/indexing/test_ingestion_service.py`; `git diff --check`; `./scripts/verify_all.sh` -> 671 passed |
| Review pass 4 | completed | Five fresh reviewers found one actionable security gap in ingestion redaction and vector cleanup logging. Four reviewers had no actionable findings. | Reviewers `019e5f2f-9267`, `019e5f2f-9367`, `019e5f2f-94af`, `019e5f2f-9665`, `019e5f2f-9896` |
| Review pass 4 remediation | completed | Added ingestion redaction coverage for credential/x-amz credential assignments and sanitized vector cleanup failure logs. | `PYTHONPATH=. uv run --python 3.13 pytest tests/indexing/test_ingestion_service.py` -> 30 passed; `PYTHONPATH=. uv run --python 3.13 pytest tests/web_console/test_app.py tests/fetching/test_github.py tests/indexing/test_ingestion_service.py` -> 153 passed; `node --check web/app.js`; `python -m compileall indexing fetching web_console tests/web_console/test_app.py tests/fetching/test_github.py tests/indexing/test_ingestion_service.py`; `git diff --check`; `./scripts/verify_all.sh` -> 672 passed |
| Review pass 5 | completed | Five fresh reviewers reported no actionable findings after the fourth remediation. | Reviewers `019e5f33-58ef`, `019e5f33-59f0`, `019e5f33-5b06`, `019e5f33-5c75`, `019e5f33-5f35` |
| PR delivery | completed | Committed, pushed, and opened a `main`-base PR. | PR #12 |
| Answer error follow-up | completed | Diagnosed browser Answer failures, added safe OpenAI auth diagnostics, prevented irrelevant indexed chunks from being treated as grounded evidence, and clarified embedding key docs. | `OPENAI_API_KEY` 401 reproduced without project `.env`; sourced project `.env` fixed the API error; relevance gate changed NeetCode graph query from unrelated MCPContentSearch chunks to `insufficient`; reviewer follow-up added strong NeetCode anchors and README embedding-key wording; `PYTHONPATH=. uv run --python 3.13 pytest tests/search/test_answer_service.py tests/search/test_context_service.py tests/e2e/test_contextwiki_flow.py tests/web_console/test_app.py` -> 61 passed. |
| Browser verification instruction follow-up | completed | Added durable AGENTS guidance requiring actual Web Console browser checks for every affected feature, not only answer questions, with deterministic/local paths preferred and live sync/user-data mutation gated by approval or an explicit plan. This was an atomic instruction-only edit, so the main agent edited directly. | User requested future testing to verify success/failure through the web UI for questions and all other console features; review follow-up tightened live sync safety. |
