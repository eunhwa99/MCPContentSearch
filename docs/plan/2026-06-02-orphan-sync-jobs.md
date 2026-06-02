# Orphan Sync Jobs After Restart

## User request

After merging the configured-source sync fix and starting the Web Console with the project `.env`, clicking Sync configured still shows `0 chunks indexed. Discovering documents...` and does not appear to run a new sync.

## Branch preflight result

- Confirmed PR #18 is merged into the user's `/Users/eunhwa/IdeaProjects/MCPContentSearch` checkout.
- Confirmed the running Web Console process is from that checkout.
- Confirmed the running GitHub and Notion sync jobs reported by `/api/sources/*/sync-status` were started before the current server process, so they are persisted orphan jobs from an older process.
- Fetched `origin/main` and created `feature/fix-orphan-sync-jobs` from `origin/main`.
- Current worktree is clean except this plan document.

## Scope and non-goals

- Recover persisted `RUNNING` sync jobs that cannot belong to the current process after server restart.
- Allow a new configured-source sync request to start instead of reusing an orphan job.
- Preserve existing timeout and duplicate-running-job protections for active jobs within a live process.
- Do not inspect, delete, reset, or migrate the user's local Chroma or SQLite data.
- Do not change GitHub/Notion/Tistory fetch semantics beyond stale job recovery.

## Acceptance criteria

- Startup recovery marks persisted running jobs from a previous process as failed with an actionable error message.
- Source status is reconciled so configured-source sync can start a fresh job after restart.
- Existing single-active-job behavior remains intact for overlapping sync requests in one live process.
- Tests cover orphan recovery and the fresh-sync-after-recovery path using temporary SQLite metadata.
- Web Console status no longer stays indefinitely on the old `0 chunks indexed. Discovering documents...` job after a restart.

## Step breakdown

1. Add a `MetadataStore` recovery method for running jobs that existed before the current application process started.
2. Wire recovery into both Web Console and MCP app startup after sources are registered and before auto-sync or tool use.
3. Add focused storage and app-level tests using temporary SQLite stores.
4. Update the ContextWiki human note if the source-sync lifecycle explanation changes.
5. Run focused tests, compile checks, functional smoke, review loop, then deliver a PR.

## Files likely to change

- `storage/metadata_store.py`
- `web_console/app.py`
- `main.py`
- `tests/storage/test_metadata_store.py`
- `tests/web_console/test_app.py`
- `docs/contextwiki-core-understanding.md`

## Test and verification plan

- `PYTHONPATH=. uv run pytest tests/storage/test_metadata_store.py tests/web_console/test_app.py tests/indexing/test_ingestion_service.py`
- `python -m compileall api core environments fetching indexing search storage wiki web_console main.py`
- `./scripts/verify_functional_e2e.sh`

If `uv` is unavailable, run the nearest dependency-free compile check and report the blocker.

## Functional smoke matrix

| Surface | Scenario | Safety | Expected evidence |
| --- | --- | --- | --- |
| Metadata store | Recover orphan running GitHub job in temp SQLite. | Local temp DB only. | Old job failed, source failed, new begin starts fresh. |
| Web Console startup | App creation with persisted running job in temp dependencies. | Local temp DB only. | Startup recovery runs before sync status. |
| Configured source sync | Click/API sync after recovery. | Prefer fake/temp dependency path; live user DB only with explicit approval. | UI/API does not reuse old orphan job. |

## Architecture/ADR constraints

- Preserve source registry, ingestion, storage, and Web Console module boundaries from `.agents/docs/architecture.md`.
- ADR 0002 applies: do not add write-side wiki/source side effects outside the explicit sync lifecycle.
- ADR 0003 is the direct sync-job lifecycle contract: preserve the SQLite single-active-sync guard, heartbeat/stale recovery, additive schema evolution, and crashed `RUNNING` job recovery without resetting user data. `sync_jobs.owner_id` and `sync_job_owners` extend that guard so restart recovery can distinguish dead previous owners from live same-DB owners.
- ADR 0004 applies for citation metadata only indirectly; do not change document/chunk identity behavior.

## Risks and rollback notes

- Startup recovery mutates metadata job status. Keep the mutation bounded to `RUNNING` jobs persisted before current process startup.
- If recovery is too broad, it could fail an active job in another same-DB process. The local server workflow assumes one Web Console/MCP process owns sync execution; document this behavior in the code/tests.
- Rollback is to remove startup recovery wiring; old timeout behavior then applies again.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created `feature/fix-orphan-sync-jobs` from `origin/main`. | `git status --short --branch` |
| Root-cause diagnosis | completed | Running source jobs predate the current Web Console process and block new sync. | `/api/sources/*/sync-status`, `lsof -p` |
| Planning | completed | Plan document created before target edits. | This file |
| Implementation | completed | Added MetadataStore orphan recovery, startup wiring, temp-SQLite tests, and source lifecycle docs. | `git diff -- storage/metadata_store.py web_console/app.py main.py tests/storage/test_metadata_store.py tests/web_console/test_app.py docs/contextwiki-core-understanding.md` |
| Focused verification | completed | Storage/Web Console/ingestion focused suites and syntax check passed. | `PYTHONPATH=. python -m pytest tests/storage/test_metadata_store.py tests/web_console/test_app.py tests/indexing/test_ingestion_service.py` -> 161 passed; `python -m compileall api core environments fetching indexing search storage wiki web_console main.py` -> passed. |
| Functional smoke | completed | Local fake/temp functional smoke gate passed, including Web Console Playwright checks. | `./scripts/verify_functional_e2e.sh` -> fake wiki smoke passed, 183 passed, Playwright web-console smoke passed. |
| Full verification | completed | Full repo verification passed after the focused checks. | `./scripts/verify_all.sh` -> 682 passed, functional gate 183 passed with 1 known StarletteDeprecationWarning, Playwright smoke passed; `git diff --check` -> passed. |
| Review | error | Review pass found actionable findings: timestamp-only recovery can fail another live same-DB process, API/UI redacts the safe recovery message, and Web Console regression coverage is too indirect. | Reviewer pass 1 findings |
| Delivery | pending | Commit, push, and open a main-base PR after clean review. | Pending |
| Review fixes | completed | Tightened recovery with sync owner metadata, exposed the safe recovery message, and added direct API regression coverage. | `PYTHONPATH=. uv run pytest tests/storage/test_metadata_store.py tests/web_console/test_app.py tests/indexing/test_ingestion_service.py` -> 164 passed; `./scripts/verify_all.sh` -> 684 passed, functional gate 184 passed. |
| Review pass 2 | error | Review pass found actionable findings: owned jobs from crashed post-fix processes still waited for the 24h timeout, owner-less migration lacked direct coverage, and owner-less grace had live-legacy risk. | Reviewer pass 2 findings |
| Review fixes 2 | completed | Added `sync_job_owners` process liveness tracking, owner_id migration coverage, dead-owner/live-owner recovery tests, docs updates, and reran verification. | `PYTHONPATH=. uv run pytest tests/storage/test_metadata_store.py tests/web_console/test_app.py tests/indexing/test_ingestion_service.py` -> 167 passed; `./scripts/verify_all.sh` -> 687 passed, functional gate 184 passed. |
| Review pass 3 | error | Review pass found actionable findings: owner-less jobs skipped during startup were not re-evaluated on later status/sync, and EPERM from PID liveness checks was treated as dead. | Reviewer pass 3 findings |
| Review fixes 3 | completed | Applied owner-less grace inside active job resolution, treated EPERM/PermissionError as live, added regression tests, and reran verification. | `PYTHONPATH=. uv run pytest tests/storage/test_metadata_store.py tests/web_console/test_app.py tests/indexing/test_ingestion_service.py` -> 169 passed; `./scripts/verify_all.sh` -> 689 passed, functional gate 184 passed. |
| Review pass 4 | error | Review pass found actionable findings: owner-less grace was applied to owned rows, dead previous owners were not recovered during later active-job resolution, and plan ADR notes missed ADR 0003. | Reviewer pass 4 findings |
| Review fixes 4 | completed | Limited owner-less grace to owner-less rows, added active resolution liveness checks for non-current owners, added regressions, updated ADR 0003 notes, and reran verification. | `PYTHONPATH=. uv run pytest tests/storage/test_metadata_store.py tests/web_console/test_app.py tests/indexing/test_ingestion_service.py` -> 171 passed; `./scripts/verify_all.sh` -> 691 passed, functional gate 184 passed. |
| Review pass 5 | completed | User-requested three-reviewer pass completed with no actionable findings. | 3/3 reviewers clean |
| Delivery | in_progress | Commit, push, and open a main-base PR. | Pending |
