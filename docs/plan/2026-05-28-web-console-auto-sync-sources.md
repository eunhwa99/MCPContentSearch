# Web Console Auto Sync Sources

## User Request

The user wants Tistory, Notion, and GitHub to always sync automatically.
Earlier context also showed `.env` may contain a Notion token under a nonstandard
name, so Notion auto-sync should not silently miss a common token alias.

## Branch Preflight Result

- Continued existing PR branch `feature/contextwiki-web-console-source-sync`
  because this is a follow-up to Phase C.5 Web Console behavior.
- Worktree had only an unrelated untracked `.env.swp` at start. This appears to
  be a user/editor temporary file and must be preserved.
- Branch/worktree check confirmed this isolated worktree is
  `/private/tmp/MCPContentSearch-phase-c5`.

## Scope and Non-Goals

- Add Web Console startup auto-sync for configured source IDs.
- Default startup auto-sync sources to `source_github`, `source_notion`, and
  `source_tistory`.
- Keep source sync status truthful through existing SQLite job/status metadata.
- Do not inspect secrets, local Chroma, or SQLite contents.
- Do not delete or reset existing indexed data.
- Do not auto-sync website/docs unless explicitly configured in a later request.

## Acceptance Criteria

- Starting the local Web Console schedules sync for GitHub, Notion, and Tistory
  configured sources.
- The startup hook does not block server startup and does not expose secrets in
  responses or logs.
- If a source is disabled or already running, existing `IngestionService`
  behavior handles it and the job/status remains visible through `/api/sources`
  and `/api/sources/{source_id}/sync-status`.
- `CONTEXTWIKI_AUTO_SYNC_SOURCES` can override the source list; setting it to an
  empty string disables startup auto-sync.
- Notion token aliases are handled safely enough for local `.env` compatibility
  without printing token values.
- Tests cover default source selection, disabled override, and startup scheduling.

## Step Breakdown

1. Configuration: add `contextwiki_auto_sync_sources` to `AppConfig`, defaulting
   to GitHub, Notion, and Tistory source IDs.
2. Token compatibility: accept common Notion token aliases while preserving
   `NOTION_API_KEY` as the canonical auth ref.
3. Web Console startup: schedule non-blocking sync tasks at startup for the
   configured source IDs using existing `IngestionService.sync_source`.
4. Tests/docs: add focused tests and update README.
5. Verification/review: run focused and full verification, then fresh
   five-reviewer review before commit/push.

## Files Likely To Change

- `environments/config.py`
- `web_console/app.py`
- `tests/web_console/test_app.py`
- `tests/environments/test_config.py` if needed
- `README.md`
- `docs/plan/2026-05-28-web-console-auto-sync-sources.md`

## Test and Verification Plan

- `python -m py_compile web_console/app.py environments/config.py tests/web_console/test_app.py`
- `PYTHONPATH=. uv run pytest tests/web_console/test_app.py tests/environments/test_config.py -q`
- `./scripts/verify_all.sh`
- Local smoke after server restart:
  - `GET /api/health`
  - `GET /api/sources`
  - inspect sync status for `source_github`, `source_notion`, `source_tistory`

## Architecture and ADR Constraints

- Architecture: source sync remains owned by `IngestionService`; Web Console only
  schedules configured source sync at startup.
- ADR 0001: preserve layered boundaries.
- ADR 0002/0003: do not mutate metadata or Chroma outside existing sync service
  contracts.
- ADR 0004: GitHub and web connector behavior remains unchanged.

## Risks and Rollback Notes

- Startup auto-sync may contact external services and can take time. It must run
  in the background and expose status through existing sync metadata.
- If credentials are missing or invalid, existing sync error paths should record
  failure safely without printing token values.
- Rollback is disabling `CONTEXTWIKI_AUTO_SYNC_SOURCES` or reverting the startup
  scheduling change.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Continued existing Phase C.5 PR branch; preserved unrelated `.env.swp`. | `git status --short --branch`; `git branch --show-current`; `git branch -vv`; `git worktree list` |
| Planning | completed | Scoped startup auto-sync for GitHub/Notion/Tistory via existing sync service with env override. | This plan |
| Worker orchestration | completed | Spawned bounded implementation and tests/docs workers before target edits; main agent integrated the diff. | worker ids `019e6b98-981d-70d2-be77-f8fa0be8ef2c`, `019e6b98-9a57-7cb1-8d30-23e73f59c2ab` |
| Implementation | completed | Added config default/override, FastAPI lifespan startup scheduler, Web Console Notion alias compatibility, tests, and README docs. Did not edit `environments/token.py` because Web Console alias handling was sufficient and avoids widening the secret surface. | `environments/config.py`; `web_console/app.py`; `tests/web_console/test_app.py`; `tests/environments/test_config.py`; `README.md` |
| Verification | completed | Compile, focused tests, full verification, API smoke, and Browser UI smoke passed. Runtime smoke confirmed startup auto-sync scheduled GitHub, Notion, and Tistory; GitHub and Tistory reached succeeded while Notion was still running during the final status check. After review fixes, focused tests and full verification passed again. | `python -m py_compile web_console/app.py environments/config.py tests/web_console/test_app.py tests/environments/test_config.py tests/wiki/test_wiki_synthesis.py`; `PYTHONPATH=. uv run pytest tests/wiki/test_wiki_synthesis.py tests/web_console/test_app.py tests/environments/test_config.py -q` -> 143 passed; `./scripts/verify_all.sh` -> 737 passed; `GET /api/health`; source sync-status smoke; Browser smoke screenshot `/private/tmp/contextwiki-auto-sync-smoke.png` |
| Review gate | completed | First fresh five-reviewer pass found actionable docs/test findings. Fixed README live-network wording, restored wiki default test isolation via env cleanup, and made startup auto-sync test wait for completed sync calls. Second fresh five-reviewer pass reported no actionable findings. | first pass reviewer ids `019e6ba6-7ba9-7383-914a-0a54f76822e3`, `019e6ba6-7e13-7ac0-a78c-50264a53cd67`, `019e6ba6-81af-7d50-9ac7-8272ccce41ce`, `019e6ba6-85f6-75c0-806b-45cf4dd906df`, `019e6ba6-89e7-73e0-99bc-677c2dbd4ecc`; second pass reviewer ids `019e6ba9-7006-7a73-8583-b0e6794d11ef`, `019e6ba9-7380-7181-b43d-1aa9a8d95cb9`, `019e6ba9-7864-7630-a5d2-4f072378236f`, `019e6ba9-7e1a-7fe2-9185-4eddc6d37c2e`, `019e6ba9-8484-7082-80f9-d490ce449255` |
