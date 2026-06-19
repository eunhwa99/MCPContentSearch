# User request

Make the repository load its own `.env` reliably so Claude/Desktop local MCP execution does not require plaintext env injection in `claude_desktop_config.json`.

## Branch preflight result

- Start state: clean worktree in `/Users/eunhwa/.codex/worktrees/eab9/MCPContentSearch` on detached `HEAD`.
- Freshness: `git fetch origin main` completed successfully on 2026-06-15.
- Branch safety:
  - `main` is already checked out in another linked worktree, so this worktree stayed off `main`.
  - Created `feature/fix-dotenv-loading` from `origin/main` in the current clean worktree.
- Branch/worktree evidence:
  - `git status --short --branch` -> `## HEAD (no branch)` before branch creation.
  - `git branch -vv` / `git worktree list` confirmed linked worktrees already holding `main`.
  - `git switch -c feature/fix-dotenv-loading origin/main` succeeded.

## Scope and non-goals

### Scope

- Centralize repo-local dotenv loading so environment-backed configuration can resolve values from `/Users/eunhwa/IdeaProjects/MCPContentSearch/.env` regardless of the launcher working directory.
- Remove the need to keep non-secret config such as Obsidian vault path in Claude Desktop MCP `env` config.
- Add focused regression tests for absolute-path dotenv loading behavior.

### Non-goals

- Do not change MCP tool contracts or source enable/disable semantics.
- Do not inspect or print `.env` secret values.
- Do not change Chroma, SQLite, sync, or retrieval behavior beyond env discovery.
- Do not update external Claude/Desktop configuration files as part of this repo change.

## Acceptance criteria

- `AppConfig` and env-backed token helpers can resolve repo-local `.env` values even when the Python process starts from a different current working directory.
- Obsidian/GitHub/Notion source enablement still depends on the same environment variable names and validation rules.
- Focused regression tests fail before the implementation and pass after the fix.

## Step breakdown

1. `dotenv-regression-test`
   - Add a failing test that proves repo-local `.env` loading must not depend on `cwd`.
   - Files: `tests/environments/`.
2. `centralized-dotenv-loader`
   - Add a shared loader/helper in `environments/` that resolves the repository `.env` path explicitly and loads it idempotently.
   - Update env consumer modules to use that helper instead of implicit cwd-based loading.
   - Files: `environments/runtime_env.py`, `environments/token.py`, optionally `environments/config.py`.
3. `focused-verification`
   - Run the smallest relevant test set and syntax check for touched modules.

## Files likely to change

- `docs/plan/2026-06-15-fix-dotenv-loading.md`
- `environments/runtime_env.py`
- `environments/token.py`
- `environments/config.py` if needed to guarantee config-path coverage
- `tests/environments/test_token.py`
- `tests/environments/test_runtime_env.py` if a new focused regression file is clearer

## Test and verification plan

- Red step: run the new focused environment regression test and confirm it fails for the expected reason.
- Green step:
  - `uv run pytest tests/environments/test_token.py tests/environments/test_runtime_env.py -q`
  - `uv run python -m compileall environments main.py`
- If `uv run` is blocked locally, fall back to `python -m pytest ...` or `python -m compileall ...` and record the blocker.

## Functional smoke matrix

| Surface | Scenario | Safe mode | Expected result | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| MCP startup | Local MCP server launch from repo path-independent command | Local only, no live sync | App initialization still succeeds without Claude config env injection | pending | Pending |
| Source registry | Obsidian source registration when vault path exists in repo `.env` | Local only | Source enablement depends on resolved env value, not process cwd | pending | Pending |

## Architecture constraints

- Keep environment loading in `environments/`; do not push launcher-specific logic into `main.py` or MCP tool handlers.
- Do not log secret values or persist them in plan/test output.
- Preserve the current environment variable names used by fetchers and source registration.

## Risks and rollback notes

- Risk: changing dotenv load timing could affect tests that monkeypatch `os.environ` after import.
- Mitigation: keep the loader idempotent and safe to call repeatedly; write tests that verify monkeypatched env still wins.
- Rollback: revert the shared loader changes in `environments/` and the focused regression tests.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Fetched `origin/main` and created `feature/fix-dotenv-loading` from clean detached worktree because `main` is checked out in another linked worktree. | `git fetch origin main`; `git switch -c feature/fix-dotenv-loading origin/main` |
| Planning | completed | Wrote plan and recorded atomic direct-implementation rationale. This task is atomic because all intended edits are confined to `environments/` env loading and focused tests with no shared-contract or multi-module ownership split required. | `docs/plan/2026-06-15-fix-dotenv-loading.md` |
| Focused test design | completed | Added red-first regression coverage for explicit repo `.env` path loading and module-level env initialization. | `uv run pytest tests/environments/test_runtime_env.py tests/environments/test_token.py tests/environments/test_config.py -q` initially failed with 4 missing-loader failures, then passed |
| Implementation | completed | Centralized repo-local dotenv loading in `environments.runtime_env` and routed `config`/`token` through it. | `environments/runtime_env.py`; `environments/config.py`; `environments/token.py` |
| Focused verification | completed | Focused env test suite and syntax check passed. | `uv run pytest tests/environments/test_runtime_env.py tests/environments/test_token.py tests/environments/test_config.py -q` -> `36 passed`; `uv run python -m compileall environments main.py` |
| Functional smoke | completed | Retained functional E2E gate stayed green after the env-loader change. | `./scripts/verify_functional_e2e.sh` -> `25 passed in 3.69s` |
| Review gate | blocked | Repo policy requires `$subagent-review-loop`, but the available subagent tool policy only allows spawning agents when the user explicitly asks for delegation. I did not bypass that policy with self-review. | Active tool policy for `multi_agent_v1.spawn_agent`; repo review-gate instruction in `AGENTS.md` / `.agents/docs/harness-engineering.md` |
