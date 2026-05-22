# GitHub Workflow

## Purpose

This document defines branch, commit, push, PR, and PR-watch policy for `MCPContentSearch`.

## Branch Policy

- Feature work starts from the latest `main`.
- Use `feature/...` branch names.
- File-changing work must pass branch preflight before target edits.
- Do not edit target files directly on `main`.
- Start every new task by checking `git status --short`, `git branch -vv`, and `git worktree list`.
- If the current worktree is dirty, do not switch branches, pull, or delete branches there. If network is available, fetch `origin/main`, then protect user changes by creating an isolated worktree with a fresh `feature/...` branch from `origin/main`. If network is restricted, record the freshness blocker and ask before creating an isolated branch from a possibly stale `origin/main`.
- If the current worktree is clean, switch to `main`, fetch `origin/main`, and fast-forward local `main` before creating the task branch. If network is restricted, record that freshness was not checked.
- For clean worktrees, delete only safe existing local non-`main` work branches before creating the new task branch. For dirty-start isolated worktrees, run cleanup only from the isolated worktree after its fresh task branch exists, never from the original dirty worktree. This cleanup deletes only local branch refs, never remote branches.
- Do not force-delete a local branch with local-only commits unless the user explicitly approves discarding that work. If a branch is checked out in another worktree, inspect `git worktree list` and detach/remove that worktree only with user approval; otherwise report the blocker.
- Create a fresh `feature/...` branch from updated `main` for each task. Reusing an existing `feature/...` branch is allowed only when the user explicitly asks to continue that branch.

Example:

```bash
# Always inspect first.
git status --short
git branch -vv
git worktree list

# Dirty worktree path: fetch, then create an isolated fresh feature branch; otherwise ask before switching/pulling/deleting.
git fetch origin main
git worktree add -b feature/short-description ../MCPContentSearch-short origin/main

# Clean worktree path:
git switch main
git fetch origin main
git pull --ff-only origin main
git branch -d feature/merged-local-branch
# If a branch is unmerged or checked out elsewhere, stop and resolve the safety check before cleanup.
git switch -c feature/short-description
```

Do not run destructive commands or delete local data without explicit user approval. The branch cleanup above applies only to local Git branch refs for new task setup.

## Commit Policy

- Use Conventional Commit style: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`.
- Keep commits small and focused.
- Do not commit before required verification and `$subagent-review-loop` pass.
- After final clean `$subagent-review-loop` verification for file-changing harness work, commit relevant staged files by default as part of PR delivery unless the user explicitly asks for local-only work.
- If the user asks to commit, never commit directly to `main`. Move to a `feature/...` branch first.
- Do not revert user or other-agent changes unless the user explicitly requests it.

Commit-time checks should match the change:

Docs-only:

```bash
rg --files AGENTS.md .agents/docs .agents/skills docs/plan
git status --short
git diff --check
```

Python/runtime changes:

```bash
python -m compileall api core environments fetching indexing search main.py
uv run pytest
```

If `uv run ...` is not available because dependencies or workspace metadata are not installed, report it and run a dependency-free fallback when useful.

## Push and PR Policy

- Final clean `$subagent-review-loop` verification flows into push and PR creation by default. Do not stop at a local diff unless the user explicitly asks for local-only work or a safety blocker prevents delivery.
- PR base is `main` unless a stacked PR requires another feature branch as base.
- For multi-task work, independent PRs use `base=main`.
- Ordered or contract-dependent tasks use stacked PRs.
- Include verification results and known skipped checks in PR text.
- Stage only files relevant to the current task, commit them, push the current `feature/...` branch, then create the PR.
- If GitHub CLI auth fails, use the configured PR-create skill fallback when available. If no documented fallback is available in the active environment, report the auth blocker instead of improvising around credentials.

Example:

```bash
git push -u origin feature/short-description
gh pr create --base main --head feature/short-description
```

## PR Monitoring Policy

Apply this only when the user explicitly asks to watch, monitor, or respond to a PR.

Check:

- CI/check status and failing logs
- New review comments or issue comments
- Mergeability and conflicts
- Base/head branch state

Classify CI failures:

- `branch-related`: compile, tests, formatting, static analysis, smoke, or contract failures caused by the diff.
- `flaky/unrelated`: runner, registry, network, API outage, or unrelated service failure.
- `ambiguous`: cannot determine without deeper analysis.

Safety rules:

- Work only on the PR head branch.
- Check `git status --short` before edits.
- Fix only branch-related failures or valid actionable review comments.
- Do not weaken tests, workflow, dependency policy, or security posture to pass unrelated checks.
- Do not reply to human review comments without user-approved wording.
- Do not push fixes unless the user has delegated PR/CI/review handling.

Stop monitoring when the PR is merged/closed, a blocker needs user judgment, or the user asks to stop.

## Release Policy

This repository currently uses `main` as the default branch. If a later workflow introduces `develop` or release branches, update this document and relevant ADRs before applying that workflow.

## Failure Handling

If GitHub CLI auth, permissions, protected branches, remote settings, or network access block the requested operation, report the blocker and propose the safest next action. Do not bypass protections.
