# Branch Workflow Docs

## User request

Update repository instructions so every new task starts by updating from the latest `main`, creating a fresh branch, deleting only safe existing local work branches when a new branch is created, and creating a PR after a clean subagent review pass.

## Branch preflight result

- Original repository worktree was on `main`, behind `origin/main`, with dirty Markdown workflow changes and an unrelated untracked `.idea/` directory.
- Did not switch, pull, or commit in that dirty worktree.
- Ran `git fetch origin main`.
- Inspected the existing local `feature/update-branch-workflow-docs` branch; it had no unique commits and was not checked out in a worktree.
- Deleted that safe stale local branch and created a fresh isolated worktree at `/private/tmp/MCPContentSearch-branch-workflow-docs` from `origin/main`.
- Current worktree branch: `feature/update-branch-workflow-docs`.

## Scope and non-goals

- Scope: docs-only workflow changes in repository instruction and harness workflow surfaces.
- Non-goals: no Python behavior changes, no MCP tool contract changes, no Chroma/indexing/fetching changes, no local data changes, and no `.idea/` changes.

## Acceptance criteria

- `AGENTS.md` requires fresh work to start from updated `main` and a fresh `feature/...` branch.
- `.agents/docs/github-workflow.md` defines the dirty and clean branch-start sequence, including freshness checks, safe local branch cleanup, and isolated worktree handling.
- `.agents/docs/harness-engineering.md` and `.agents/skills/harness-engineering/SKILL.md` align with the branch-start policy.
- The branch cleanup rule protects `main`, remote branches, local-only commits, and branches checked out in linked worktrees.
- Existing PR-delivery policy remains: after final clean `$subagent-review-loop`, stage relevant files, commit, push, and create a `main`-base PR by default.
- Docs-only verification passes.
- Fresh `$subagent-review-loop` reports no actionable findings before PR delivery.

## Step breakdown

1. Apply the branch workflow changes to `AGENTS.md`, `.agents/docs/github-workflow.md`, `.agents/docs/harness-engineering.md`, and `.agents/skills/harness-engineering/SKILL.md`.
2. Keep wording consistent: every task should refresh `main`, clean safe local branches, then create a fresh `feature/...` branch.
3. Preserve safety boundaries: do not delete `main`, remote branches, local-only commits, or branches checked out in linked worktrees without user approval.
4. Run docs-only verification.
5. Run fresh subagent review; fix findings and rerun verification if needed.
6. Commit the Markdown changes, push the branch, and create a `main`-base PR.

## Files expected to change

- `AGENTS.md`
- `.agents/docs/github-workflow.md`
- `.agents/docs/harness-engineering.md`
- `.agents/skills/harness-engineering/SKILL.md`
- `docs/plan/2026-05-22-branch-workflow-docs.md`

## Test and verification plan

Docs-only verification:

```bash
rg --files AGENTS.md .agents/docs .agents/skills docs/plan
git status --short
git diff --check
git diff --cached --check
```

## Architecture/ADR constraints

- Read `.agents/docs/architecture.md` and `.agents/docs/adr/README.md`.
- No accepted ADR directly affects this branch-policy documentation change.
- The change must not alter Python module boundaries, MCP contracts, Chroma persistence, or external service behavior.

## Risks and rollback notes

- Risk: branch cleanup language could accidentally encourage deleting unpushed work. Mitigation: document safeguards around `main`, remote branches, ahead/local-only commits, and linked worktrees.
- Risk: dirty worktree handling could encourage manipulating user changes. Mitigation: require isolated worktrees and prohibit switching, pulling, or branch deletion in the dirty worktree.
- Rollback: revert the docs-only commit on `feature/update-branch-workflow-docs`.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Protected the dirty original worktree, refreshed `origin/main`, deleted only a safe stale local branch, and created an isolated fresh feature branch. | `git status --short --branch`; `git fetch origin main`; `git branch -D feature/update-branch-workflow-docs`; `git worktree add -b feature/update-branch-workflow-docs ... origin/main` |
| Plan document | completed | Added this plan before target doc edits in the isolated worktree. | `docs/plan/2026-05-22-branch-workflow-docs.md` |
| Implementation | completed | Updated workflow docs consistently, including dirty worktree safeguards, safe cleanup, and PR delivery preservation. | Local diff |
| Focused verification | completed | Docs-only checks passed, including the new plan file after staging. | `rg --files AGENTS.md .agents/docs .agents/skills docs/plan`; `git diff --check`; `git diff --cached --check` |
| Review gate | pending | Run fresh `$subagent-review-loop`. | Pending |
| PR delivery | pending | Commit, push, and create PR after clean review. | Pending |
