# Web Console Source Sync

## User Request

Add source sync APIs to the Phase C.5 local Web Console and make source sync runnable from the browser UI. The user also asked for a less tedious target-sync flow: selecting GitHub, Notion, or Web URL and entering a URL/id should sync that target without manually editing environment configuration. GitHub owner URLs such as `github.com/eunhwa99` should discover repositories; Notion and Web URL targets should use configured local credentials/services without adding OAuth.

## Branch Preflight Result

- Worktree: `/private/tmp/MCPContentSearch-phase-c5`
- Branch: `feature/contextwiki-web-console-source-sync`, created from `origin/main`.
- Starting state: clean (`git status --short --branch` showed no file changes).
- Existing Phase C.5 branch had already been merged into `main`, so this work is a fresh follow-up branch rather than editing `main`.

## Scope and Non-Goals

- Add local-only HTTP source sync endpoints to `web_console/app.py`.
- Add a generic target sync endpoint that accepts `source_type=github|notion|web` plus a target URL/id.
- Keep the legacy GitHub target sync endpoint while making `/api/targets/sync` the browser UI path.
- Add browser controls in `web/` so configured sources or an ad hoc GitHub/Notion/Web URL target can be synced without an MCP client.
- Keep configured source sync delegated to existing `IngestionService.sync_source(source_id)`.
- Keep GitHub repo discovery and Notion/Web fetching in the `fetching` boundary, not embedded in UI code.
- Add focused tests with fake dependencies and mocked GitHub HTTP behavior; do not perform live GitHub/Web/Notion/Tistory calls.
- Do not add OAuth/login, persistent source registration, config-file editing, background queueing, stale-job cleanup UI, auth, deployment, or multi-user behavior.
- Do not inspect, delete, reset, or migrate local Chroma or SQLite user data.

## Acceptance Criteria

- `POST /api/sources/{source_id}/sync` runs a configured source sync and returns the existing sync job JSON shape.
- `GET /api/sources/{source_id}/sync-status` returns the source and latest job for quick UI refresh.
- `POST /api/github/sync` accepts a GitHub owner URL and expands it into concrete repository specs using GitHub's repository list API.
- `POST /api/targets/sync` routes GitHub, Notion, and Web URL targets through the matching local services.
- One-off target sync disables source-wide stale cleanup so an ad hoc subset cannot tombstone previously indexed documents.
- If a same-source sync is already running, target sync reports `already_running` without claiming the requested target started.
- Missing services return safe 503 responses.
- Sync failures return structured safe error responses without logging secrets.
- The browser Sources panel shows a Sync button per source, displays source status metadata, refreshes after sync, and includes a Target Sync selector/input plus progress bar.
- README documents the new local Web Console sync controls.
- Focused web console/GitHub/Notion/indexing tests pass and JavaScript syntax is checked.

## Step Breakdown

1. Extend `ConsoleDependencies` and `create_default_app()` to retain `IngestionService`.
2. Add configured source sync and status HTTP routes.
3. Add GitHub owner/repo target parsing and repository discovery under `fetching/github.py`.
4. Add one-off GitHub, Notion, and Web URL target sync that reuses `IngestionService` with stale cleanup disabled.
5. Update Web Console UI rendering and event handling for per-source sync, target sync, and progress polling.
6. Add or update tests in `tests/web_console/test_app.py`, `tests/fetching/test_github.py`, `tests/fetching/test_notion.py`, and `tests/indexing/test_ingestion_service.py`.
7. Run focused verification, then broader verification if the local uv environment is healthy.
8. Run required fresh five-reviewer `$subagent-review-loop`; fix actionable findings and repeat until clean.
9. Commit, push, and create a `main`-base PR.

## Files Likely To Change

- `fetching/connectors.py`
- `fetching/github.py`
- `fetching/notion.py`
- `indexing/ingestion_service.py`
- `web_console/app.py`
- `web/index.html`
- `web/app.js`
- `web/styles.css`
- `tests/web_console/test_app.py`
- `tests/fetching/test_github.py`
- `tests/fetching/test_notion.py`
- `tests/indexing/test_ingestion_service.py`
- `README.md`
- `docs/plan/2026-05-25-web-console-source-sync.md`

## Test and Verification Plan

- Focused API/UI-related tests: `uv run --python 3.13 pytest tests/web_console/test_app.py tests/fetching/test_notion.py tests/fetching/test_github.py tests/fetching/test_web_docs.py tests/indexing/test_ingestion_service.py tests/e2e/test_phase_b_connectors_flow.py`
- JavaScript syntax: `node --check web/app.js`
- Browser verification: open the local Web Console and directly verify target selector/options/placeholders, invalid target failure state, progress panel behavior, source list loading, and deterministic smoke/download behavior.
- Broader if healthy: `./scripts/verify_all.sh`
- Review gate: fresh five-reviewer `$subagent-review-loop` after verification.

## Architecture and ADR Constraints

- ADR 0001: preserve layered boundaries; Web Console routes delegate to service objects, and GitHub discovery stays under `fetching`.
- ADR 0002: SQLite metadata remains the source/job/chunk lifecycle store; tests must use fake or temporary state.
- ADR 0003: preserve stable document identity, tombstone semantics, and SQLite as the authoritative active-document gate; this follow-up narrows GitHub stale cleanup to connector repository prefixes to keep ad hoc target-sync repos safe.
- ADR 0004: keep canonical source ids (`source_github`, `source_web`) and reuse `sync_source(source_id)` semantics rather than adding connector-specific MCP tools. GitHub cleanup is connector repository-prefix scoped when multiple GitHub repo scopes share `source_github`.

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
| GitHub cleanup scope follow-up | completed | Scoped GitHub stale cleanup to the connector's fetched repository document-id prefixes so configured `source_github` sync can tombstone missing files for its own repos without tombstoning ad hoc target-sync repos that share `source_github`. Added storage, ingestion, GitHub connector, e2e regression coverage, core docs, and ADR alignment. Review follow-up replaced SQL `LIKE` prefix checks with literal `substr` comparisons so underscores in repo names cannot match unrelated repos. The safer tradeoff is that removing a repo from current GitHub config leaves its previous documents active until a future provenance-aware or manual cleanup path exists. | Pre-fix non-secret metadata showed configured GitHub sync succeeded with only `eunhwa99/MCPContentSearch` active while prior target-sync repos had been tombstoned. Deterministic tests now verify prefix-scoped cleanup preserves `github:eunhwa99/leetcode:*` when syncing `github:eunhwa99/mcpcontentsearch:*`, plus underscore-literal coverage for `foo_bar` vs `fooxbar`. Verification: focused suite -> 235 passed; `./scripts/verify_all.sh` -> 682 passed; final fresh five-reviewer pass reported no actionable findings. |
| Target Sync UI and progress follow-up | completed | Replaced the GitHub-only target sync control with a single Target Sync selector for GitHub, Notion, and Web URL; added sync progress visibility; added target sync HTTP routing for Notion URL and Web URL using configured credentials/services without OAuth. | Browser verified dropdown options/placeholders, empty target validation, source loading, and fake smoke/download enablement. Verification: `node --check web/app.js`; `git diff --check`; focused suite -> 505 passed; `./scripts/verify_all.sh` -> 692 passed. OAuth/login discovery is deferred because it needs a separate token/permission contract. |
| Target Sync review pass 1 remediation | completed | Fixed reviewer findings: target sync now reports `already_running` without claiming the typed target started when a same-source sync is already active; progress polling stays attached to running jobs and stops only on terminal statuses; stale GitHub-only plan wording was updated. | First five-reviewer pass had two actionable findings and three clean reviews. Verification after fix: `node --check web/app.js`; `python -m compileall web_console tests/web_console/test_app.py`; `git diff --check`; focused suite -> 506 passed; browser checked empty target validation and deterministic fake smoke/download flow; API invalid Notion target returned structured safe error. |
| Target Sync review pass 2 remediation | completed | Fixed reviewer finding that an in-flight sync-status request could overwrite a failed/no-job target request with a stale latest job. Added a polling token guard so late status responses are ignored after polling stops or the active source changes. | Second five-reviewer pass had two actionable reports of the same UI race and three clean reviews. |
| Target Sync review pass 3 remediation | completed | Fixed reviewer finding that initial polling could stop on a previous terminal latest job before the new sync job was visible. The UI now ignores terminal jobs before the current attempt observes/receives a running job, then pins progress updates to the active job id. | Third five-reviewer pass had one actionable polling race finding and four clean reviews. |
| Target Sync review pass 4 remediation | completed | Fixed reviewer findings that configured source sync top-level job payloads were not recognized by the progress UI and that `already_running` could still be labeled as completed in the status line. | Fourth five-reviewer pass had two actionable UI truthfulness findings and three clean reviews. |
| Target Sync review pass 5 remediation | completed | Fixed reviewer finding that configured source sync top-level `running` job payloads could still be labeled as completed in the status line. | Fifth five-reviewer pass had two actionable reports of the same running-status copy issue and three clean reviews. |
| Target Sync review pass 6 remediation | completed | Fixed reviewer finding that Notion URL parsing could bleed a hex character from the title into a compact page/database id. Parser now reads the final path segment and matches hyphenated UUIDs before trailing compact ids. | Sixth five-reviewer pass had one actionable Notion parser finding and four clean reviews. |
| Target Sync review pass 7 remediation | completed | Fixed reviewer findings that Notion parsing could still choose a UUID-like title token before the trailing id and that pre-POST polling could pin a stale running job id before the POST returned the current job. Parser now prefers trailing ids, and the UI pins active job ids only from POST-returned jobs. | Seventh five-reviewer pass had three actionable reports across these two issues and two clean reviews. |
| Target Sync review pass 8 remediation | completed | Fixed reviewer findings that pre-POST polling could still render a stale running job, structured sync-status failures could overwrite active progress with a waiting state, no-job sync responses could be overwritten before polling stopped, and progress-update failure logs could include raw exception text. | Eighth five-reviewer pass had four actionable reports across these three UI progress races plus one log-redaction gap and one clean review. |
| Target Sync review pass 9 remediation | completed | Fixed reviewer findings that late null-job sync-status responses could overwrite active progress and empty target validation could leave stale progress visible. | Ninth five-reviewer pass had two actionable progress-state findings, two clean reviews, and one reviewer timed out before completion. |
| Target Sync review pass 10 remediation | completed | Fixed reviewer findings by adding mocked `fetch_notion_target` coverage for page success, page-404 database fallback, and non-404 no-fallback behavior; clarified README live-network wording for GitHub, Notion, and Web Target Sync. | Tenth five-reviewer pass had a Notion target coverage finding and a README live-network wording finding. Verification after Notion tests: `tests/fetching/test_notion.py` -> 9 passed; focused suite -> 512 passed; `./scripts/verify_all.sh` -> 699 passed. |
| Target Sync review pass 11 remediation | completed | Fixed reviewer finding that GitHub owner Target Sync could perform live repository discovery before noticing an active `source_github` sync. The service now returns `already_running` before discovery, and a regression test fails if discovery is called while a GitHub sync is already running. | Eleventh five-reviewer pass had one actionable GitHub owner discovery safety finding. Verification after fix: `tests/web_console/test_app.py` -> 51 passed; focused suite -> 513 passed; `./scripts/verify_all.sh` -> 700 passed. |
| Target Sync review pass 12 remediation | completed | Fixed reviewer findings that sync progress lacked screen-reader state and terminal sync errors could remain visible after later answer/wiki/smoke success. The progress panel now exposes status/progressbar ARIA attributes, updates `aria-valuenow`, cache-busts the JS, and hides inactive terminal sync progress before non-sync actions. | Twelfth five-reviewer pass had two UI/a11y findings. Verification after fix: `node --check web/app.js`; `git diff --check`; focused suite -> 513 passed; `./scripts/verify_all.sh` -> 700 passed; browser verified invalid target ARIA state and fake smoke hides terminal progress. |
| Final review pass | completed | Five fresh reviewers reported no actionable findings after the Target Sync review pass 12 remediation. | Reviewers `019e6917-7156`, `019e6917-72fe`, `019e6917-75c7`, `019e6917-7891`, `019e6917-7b50`. |
