# Notion Transient Error Retry

## User request

Notion configured sync failed on a transient Notion API 502 from
`/v1/blocks/{block_id}/children?page_size=100`. Add retry handling so a single
Notion 502/503/504/429 does not immediately fail the whole configured sync.

## Branch preflight result

- Starting worktree was clean on `feature/fix-orphan-sync-jobs`.
- `main` is checked out in the linked `/Users/eunhwa/IdeaProjects/MCPContentSearch` worktree, so this worktree could not safely switch to local `main`.
- Fetched `origin/main` and created fresh branch `feature/retry-notion-transient-errors` from `origin/main`.
- No local Chroma, SQLite user data, `.env`, or secret values were inspected.

## Scope and non-goals

- Add bounded retry/backoff for transient Notion API statuses: 429, 500, 502, 503, and 504.
- Apply retry to Notion search, block children fetch, page fetch, and database query requests.
- Keep 401/403/404 and validation/config errors non-retriable.
- Keep caller-visible sync failure behavior if retries are exhausted.
- Do not change Notion document identity, chunking, SQLite lifecycle, Chroma behavior, or Web Console contracts.

## Acceptance criteria

- A transient 502 from block children fetch is retried and can succeed without failing sync.
- Non-transient Notion errors such as 403 are not retried.
- Retry exhaustion still surfaces an `APIError` with the Notion status code.
- Tests use mocked HTTP behavior only and do not call live Notion.

## Files likely to change

- `fetching/notion.py`
- `tests/fetching/test_notion.py`
- `docs/contextwiki-core-understanding.md` if the Notion fetch lifecycle explanation needs updating.

## Test and verification plan

- `PYTHONPATH=. uv run pytest tests/fetching/test_notion.py`
- `PYTHONPATH=. uv run pytest tests/fetching/test_notion.py tests/fetching/test_connectors.py tests/indexing/test_ingestion_service.py`
- `python -m compileall api core environments fetching indexing search storage wiki web_console main.py`
- `./scripts/verify_functional_e2e.sh`

## Functional smoke matrix

| Surface | Scenario | Safety | Expected evidence |
| --- | --- | --- | --- |
| Notion fetcher | Block children returns 502 once, then 200. | Mocked HTTP only. | Fetch succeeds after retry. |
| Notion fetcher | Page/database/block returns 403. | Mocked HTTP only. | Error is raised without retry loop. |
| Configured sync | Retry-backed Notion fetch remains compatible with connector and ingestion boundaries. | Unit/fake substitute only; no live Notion or user data. | Notion retry tests, connector metadata tests, and ingestion tests remain green. |

## Architecture/ADR constraints

- ADR 0001 applies: Notion API-specific retry behavior belongs in `fetching/`, not API tools or Web Console.
- ADR 0003 applies indirectly: failed or partial syncs must not tombstone missing documents; retry is limited to fetching before ingestion finalization.
- External Notion validation is not required and should not be run without explicit approval.

## Risks and rollback notes

- Overly broad retries can hide permission/configuration errors; retry only transient statuses.
- Overly aggressive backoff can slow sync; tests should patch sleeps or retry waits where needed.
- Rollback is to remove the retry helper and restore direct `response.raise_for_status()` handling.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created `feature/retry-notion-transient-errors` from `origin/main`. | `git fetch origin main`; `git switch -c feature/retry-notion-transient-errors origin/main` |
| Planning | completed | Plan document created before target edits. | This file |
| Implementation | completed | Added shared Notion request retry helper, transient status tests, and source lifecycle docs. | `fetching/notion.py`, `tests/fetching/test_notion.py`, `docs/contextwiki-core-understanding.md` |
| Focused verification | completed | Notion/fetcher/ingestion tests passed after adding direct search retry coverage. | `PYTHONPATH=. uv run pytest tests/fetching/test_notion.py` -> 15 passed; `PYTHONPATH=. uv run pytest tests/fetching/test_notion.py tests/fetching/test_connectors.py tests/indexing/test_ingestion_service.py` -> 54 passed |
| Functional smoke | completed | Local functional smoke gate passed. | `./scripts/verify_functional_e2e.sh` -> 184 passed and Playwright smoke passed |
| Full verification | completed | Full repo gate passed after retry fix and review follow-up. | `./scripts/verify_all.sh` -> 697 passed; functional gate 184 passed and Playwright smoke passed |
| Review | completed | Fresh three-reviewer pass reported no actionable findings after the follow-up fix. | Reviewers 1-3 clean; residual notes only for legacy keyword search and `Retry-After` future hardening |
| Delivery | completed | Committed, pushed, and opened a `main`-base PR after clean review. | PR #20 |
