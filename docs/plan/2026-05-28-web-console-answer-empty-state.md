# Web Console Answer Empty-State Cleanup

## User Request

The user asked why `AWS 관련 문서찾아줘` produced an unrelated-looking answer
in the browser and why `UIUC 관련 글 찾아줘` cannot find posts.

## Branch Preflight Result

- Continued existing PR branch `feature/contextwiki-web-console-source-sync`
  because this is a follow-up to the same Phase C.5 Web Console PR.
- Worktree was clean at start: `git status --short --branch`.
- Branch/worktree check confirmed the isolated worktree remains
  `/private/tmp/MCPContentSearch-phase-c5`.

## Reproduction Notes

- Latest `/api/answer/codex` returns a clean AWS answer with AWS, EC2/ELB, and
  IAM citations for `AWS 관련 문서찾아줘`.
- `/api/answer` still returns long raw ContextWiki chunk text, which is useful
  as a debug fallback but confusing as a user-facing answer.
- `UIUC 관련 글 찾아줘` returns insufficient evidence because the current sources
  do not contain indexed UIUC evidence. `source_notion` is idle/not synced and
  `source_web` is disabled according to `/api/sources`.

## Scope and Non-Goals

- Clarify the browser's raw ContextWiki fallback label so users do not mistake it
  for the polished answer path.
- Improve the Codex CLI insufficient-evidence message with an actionable sync
  hint for missing topics like UIUC.
- Keep backend contracts and source sync behavior unchanged.
- Do not inspect or mutate local Chroma/SQLite data.
- Do not start Notion/GitHub/Web sync without explicit user approval.

## Acceptance Criteria

- Browser answer mode labels make clear that Codex CLI is the polished default
  and ContextWiki is a raw/debug fallback.
- When no evidence is found, `/api/answer/codex` returns a user-facing message
  explaining that no indexed evidence was found and that a relevant source/target
  should be synced.
- Focused tests cover the new label/message behavior.
- AWS query remains successful through `/api/answer/codex`; UIUC query remains
  safely insufficient until relevant content is indexed.

## Step Breakdown

1. Update Web Console copy and Codex insufficient-evidence message.
2. Add or adjust focused tests.
3. Run syntax, focused tests, full verification, local HTTP smoke, and fresh
   five-reviewer review before commit/push.

## Files Likely To Change

- `web/index.html`
- `web_console/app.py`
- `tests/web_console/test_app.py`
- `README.md` if documentation copy needs to match UI wording.
- `docs/plan/2026-05-28-web-console-answer-empty-state.md`

## Test and Verification Plan

- `python -m py_compile web_console/app.py tests/web_console/test_app.py`
- `node --check web/app.js`
- `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q`
- `./scripts/verify_all.sh`
- Local HTTP smoke:
  - `POST /api/answer/codex` for `AWS 관련 문서찾아줘`
  - `POST /api/answer/codex` for `UIUC 관련 글 찾아줘`

## Architecture and ADR Constraints

- Architecture: Web Console remains a thin local HTTP wrapper.
- ADR 0001: preserve layered boundaries.
- ADR 0005: Codex CLI Answer remains explicit local developer tooling with
  bounded evidence and no hard offline/sandbox guarantee.

## Risks and Rollback Notes

- Message changes should not imply that syncing will automatically find UIUC
  unless the user provides a source that contains UIUC content.
- Rollback is reverting the copy and insufficient-evidence message.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Continued existing Phase C.5 PR branch from a clean worktree. | `git status --short --branch`; `git branch --show-current`; `git branch -vv`; `git worktree list` |
| Reproduction | completed | Latest Codex route gives good AWS results; UIUC has no indexed evidence; raw ContextWiki route still emits long chunks. | `POST /api/answer/codex`; `POST /api/answer`; `GET /api/sources` |
| Planning | completed | Scoped a small copy/message fix; direct implementation is acceptable because this is an atomic UI/backend message adjustment with no shared-state mutation. | This plan |
| Implementation | completed | Relabeled raw ContextWiki fallback, improved no-evidence Codex message, and added focused regression assertions. | `web/index.html`; `web_console/app.py`; `tests/web_console/test_app.py`; `README.md` |
| Verification | completed | Syntax/diff checks, focused tests, full verification, and local HTTP smoke passed after restarting the local server with latest code. | `python -m py_compile web_console/app.py tests/web_console/test_app.py`; `node --check web/app.js`; `git diff --check`; `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q` -> 78 passed; `./scripts/verify_all.sh` -> 727 passed; `POST /api/answer/codex` AWS -> grounded; UIUC -> actionable insufficient |
| Review gate | completed | Fresh five-reviewer review pass reported no actionable findings. | Reviewer lenses: insufficient evidence behavior, UI copy, test coverage, docs consistency, end-to-end contract stability |
