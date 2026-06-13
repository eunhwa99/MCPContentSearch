## User request

Rewrite `README.md` so it is cleaner, easier to scan, and grounded in the current implemented ContextWiki behavior rather than an older simplified example.

## Branch preflight result

- Starting state: clean worktree on detached `HEAD` at `223a3d9`
- Safety note: local `main` is checked out in another linked worktree, so this worktree did not switch to `main`
- Freshness check: ran `git fetch origin main` and created `feature/readme-rewrite-cleanup` from updated `origin/main`
- Active branch for this task: `feature/readme-rewrite-cleanup`

## Scope and non-goals

### Scope

- Rewrite `README.md` for clarity and stronger visual hierarchy
- Preserve accuracy for the retained slim MCP core scope, source connectors, tool surface, setup, verification, demo, and live smoke guidance
- Add a plan record for the docs-only harness workflow

### Non-goals

- No MCP contract changes
- No Python, script, or configuration changes
- No architecture or ADR changes unless documentation review proves they are inaccurate

## Acceptance criteria

- `README.md` opens with a concise overview that makes the current product scope obvious at a glance
- The retained MCP tools, current connectors, and SQLite plus Chroma split remain accurate to current implementation and accepted ADRs
- Setup, verification, demo, and live smoke commands match the repository scripts and current docs
- Historical removed scope is clearly separated from retained scope so readers do not confuse old features with the current product
- Docs-only verification passes

## Step breakdown

1. Confirm current scope and contract from architecture, ADRs, README, scripts, and tool/config references.
2. Rewrite `README.md` into a clearer top-down structure focused on quick comprehension first and operational detail second.
3. Run docs-only verification and record any review-gate blocker.

## Files likely to change

- `README.md`
- `docs/plan/2026-06-13-readme-rewrite.md`

## Test and verification plan

- `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`
- `git status --short --branch`
- `git diff --check`
- Stage relevant docs-only files
- `git diff --cached --check`

## Functional smoke matrix

| Surface | Expectation | Mode | Status |
| --- | --- | --- | --- |
| README tool surface section | Matches retained tools in `api/tools.py` | Docs/code comparison | passed |
| README connector/config section | Matches retained connectors and env vars | Docs/code comparison | passed |
| README verification/demo section | Matches script entrypoints and behavior | Docs/script comparison | passed |
| Runtime MCP behavior | Not changed by this docs-only rewrite | N/A | not affected |

## Architecture/ADR constraints

- Follow `.agents/docs/architecture.md` for retained module boundaries and current MCP tool surface
- Preserve ADR 0002 SQLite-plus-Chroma explanation
- Preserve ADR 0004 retained GitHub and Obsidian connector scope
- Preserve ADR 0006 slim MCP core scope and historical removals

## Risks and rollback notes

- Main risk is documentation drift if the rewrite compresses away important retained behavior or overstates setup/runtime guarantees
- Rollback is straightforward: revert the README-only diff on this feature branch before any commit

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Fetched `origin/main` and created `feature/readme-rewrite-cleanup` from updated remote main because local `main` was occupied by another linked worktree. | `git fetch origin main`; `git switch -c feature/readme-rewrite-cleanup origin/main` |
| Planning | completed | Captured docs-only scope, acceptance criteria, verification, and ADR constraints. | `docs/plan/2026-06-13-readme-rewrite.md` |
| Worker orchestration | completed | Main agent will handle the edit directly because the task is an atomic single-file documentation rewrite plus its required plan update. | Atomic docs-only scope recorded here |
| README rewrite | completed | Reorganized README into a faster top-down narrative with retained-scope summary, current tool surface, connector/config guidance, verification, demo, and live smoke sections. | `README.md` staged diff |
| Docs verification | completed | Ran docs-only listing, status, whitespace, and cached whitespace checks after staging the README and plan file. | `rg --files ...`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review gate | blocked | Repository policy requires delegated subagent review, but current tool policy allows spawning only when the user explicitly requests delegation. | `tool_search` exposed delegation tools, but spawn authorization is absent |
