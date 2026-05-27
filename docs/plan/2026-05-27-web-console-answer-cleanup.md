# Phase C.5 Web Console Answer Cleanup

## User Request

The user found the Web Console confusing: Fake Smoke and GitHub Smoke buttons do
not have an obvious purpose, Generate Wiki is unclear, and Answer output is
still messy. Clean up the local test console so the primary workflow is easier
to understand and answer output is more readable.

## Branch Preflight Result

- Continued existing PR branch `feature/contextwiki-web-console-source-sync`
  because the request is a follow-up to the same Phase C.5 Web Console PR.
- Worktree was clean at start of this follow-up.
- Original `main` worktree remains separate at
  `/Users/eunhwa/IdeaProjects/MCPContentSearch`; this isolated worktree is
  `/private/tmp/MCPContentSearch-phase-c5`.

## Scope and Non-Goals

- Remove smoke and wiki controls from the browser UI because they are developer
  endpoint checks, not the user's primary console workflow.
- Keep the existing HTTP endpoints and Python tests for smoke/wiki behavior so
  no backend contract is removed.
- Make the browser Answer workflow default to Codex CLI Answer, since live
  verification showed it produces a concise, structured answer for the user's
  Korean graph-code question while the raw ContextWiki answer returns a long
  chunk excerpt.
- Render answer Markdown more cleanly in the Answer tab: paragraphs, lists,
  inline code, bold text, and fenced code blocks should be readable instead of
  one flat escaped text block.
- Do not change MCP tool contracts, indexing, Chroma/SQLite state, or source
  sync behavior.

## Acceptance Criteria

- Browser UI no longer shows `Fake Smoke`, `GitHub Smoke`, `Generate Wiki`, the
  wiki topic input, GitHub smoke repository input, or require-generated smoke
  toggle.
- `Answer mode` defaults to `Codex CLI Answer`, with `ContextWiki Answer`
  available as a fallback option.
- The Answer tab renders common Markdown answer structure cleanly, including
  fenced code blocks and bullet lists.
- Downloads still work: Markdown uses answer text, JSON uses sanitized payload.
- Backend endpoints remain available for tests and scripts.
- Focused tests cover removed controls, default answer mode, and formatted
  answer rendering.

## Step Breakdown

1. Web UI cleanup: update `web/index.html`, `web/app.js`, and `web/styles.css`
   to remove non-primary controls, default answer mode to Codex, and add
   lightweight safe Markdown rendering for answers.
2. Tests/docs: update web console tests and README wording so browser UI
   expectations match the simplified console while backend endpoint tests remain.
3. Verification: run JS syntax, focused web console tests, full verification,
   and direct local HTTP/browser smoke at `http://127.0.0.1:8765/`.
4. Review: run a fresh five-reviewer subagent review pass before commit/push.

## Files Likely To Change

- `web/index.html`
- `web/app.js`
- `web/styles.css`
- `tests/web_console/test_app.py`
- `README.md`
- `docs/plan/2026-05-27-web-console-answer-cleanup.md`

## Test and Verification Plan

- `node --check web/app.js`
- `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q`
- `./scripts/verify_all.sh`
- Local HTTP smoke:
  - `GET /api/health`
  - `GET /`
  - `POST /api/answer/codex` with the user's NeetCode graph-code question

## Architecture and ADR Constraints

- Architecture: Web Console remains a local-only HTTP wrapper around existing
  services. No MCP tool contracts change.
- ADR 0001: preserve layered module boundaries.
- ADR 0005: Codex CLI mode remains explicit local developer tooling with
  bounded/redacted evidence and no hard offline/sandbox guarantee.

## Risks and Rollback Notes

- Defaulting to Codex CLI Answer depends on local Codex CLI availability and may
  be slower. Rollback is changing the selected option back to ContextWiki.
- Lightweight Markdown rendering must escape all text and only emit controlled
  tags to avoid introducing HTML injection.
- Removing UI buttons could hide useful developer checks, so endpoints and tests
  are preserved and docs should mention they remain script/API-only.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Continued existing Phase C.5 PR branch from a clean worktree. | `git status --short --branch`; `git branch --show-current`; `git branch -vv`; `git worktree list` |
| Planning | completed | Scoped browser simplification, Codex-default answer mode, and safe Markdown rendering without backend contract removal. | This plan |
| Worker orchestration | completed | Spawned bounded UI implementation worker and bounded tests/docs worker before target edits; main agent integrated their scoped changes. | worker ids `019e69a3-c05e-72a0-9af0-25f084d5342f`, `019e69a3-c283-7ee0-8e22-655ed1a40240` |
| Implementation | completed | Removed smoke/wiki browser controls, defaulted Answer mode to Codex CLI, and added safe lightweight Markdown rendering for answer text. | `web/index.html`; `web/app.js`; `web/styles.css`; `tests/web_console/test_app.py`; `README.md` |
| Verification | completed | Syntax/diff checks, focused web console tests, full verification, and local HTTP smoke passed. The first focused run exposed a test-harness string escaping bug, which was fixed with JSON serialization before rerun. | `python -m py_compile web_console/app.py tests/web_console/test_app.py`; `node --check web/app.js`; `git diff --check`; `PYTHONPATH=. uv run pytest tests/web_console/test_app.py -q` -> 78 passed; `./scripts/verify_all.sh` -> 727 passed; `GET /`; `GET /api/health`; `POST /api/answer/codex` |
| Review gate | completed | Fresh five-reviewer review pass reported no actionable findings. | Reviewer lenses: UI simplification, Markdown rendering safety, test coverage, docs consistency, backend contract stability |
