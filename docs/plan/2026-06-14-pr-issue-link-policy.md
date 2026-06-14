# PR Issue Link Policy

## User request

- Update the harness and/or PR-create skill so PR creation links the related
  issue when a real issue exists and avoids fake closing keywords otherwise.

## Branch preflight result

- Source worktree
  `/Users/eunhwa/IdeaProjects/MCPContentSearch/.worktrees/issue-59-answer-helper`
  was dirty with the staged repo diff that needed a separate PR.
- Fetched `origin/main`, then created isolated worktree
  `/Users/eunhwa/IdeaProjects/MCPContentSearch/.worktrees/pr-issue-link-policy`
  on branch `feature/pr-issue-link-policy` from `origin/main`.
- Applied only the staged repo diff into the new worktree so the PR can stay
  isolated from merged issue 59 work.

## Scope and non-goals

### Scope

- Update repository PR workflow guidance so PR bodies must include a dedicated
  closing-keyword line when the work item is tied to a real GitHub issue.
- Update harness delivery wording so PR creation checks for the same
  closing-keyword rule.
- Carry only the staged repository changes in a separate PR from `main`.

### Non-goals

- No behavioral change to GitHub CLI itself.
- No broad PR template rewrite.
- No automatic issue discovery scripting.

## Acceptance criteria

- `.agents/docs/github-workflow.md` explicitly says PRs should include a
  dedicated closing-keyword line such as `closes #59` when a real issue exists.
- `.agents/docs/harness-engineering.md` reflects that the same closing-keyword
  rule is part of PR delivery for this repo and that no fake closing keyword
  should be added when no real issue exists.
- This PR contains only the staged repository changes and stays isolated from
  merged issue 59 work.

## Step breakdown

1. `policy-scan`
   - Read the current repo PR workflow wording and the local PR-create skill.
2. `policy-update`
   - Update the repo workflow and harness docs.
   - Atomic single-owner execution is appropriate because the change is one
     tightly coupled policy slice across the repo workflow docs.
3. `verification`
   - Run diff checks and spot-check the new closing-keyword language.
4. `pr-separation`
   - Move only the staged repo diff into a fresh `origin/main`-based branch for
     independent PR delivery.

## Files likely to change

- `.agents/docs/github-workflow.md`
- `.agents/docs/harness-engineering.md`
- `docs/plan/2026-06-14-pr-issue-link-policy.md`

## Test and verification plan

- `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`
- `git status --short --branch`
- `git diff --check`
- `git diff --cached --check`
- `rg -n "closes #|issue|이슈" .agents/docs/github-workflow.md .agents/docs/harness-engineering.md docs/plan/2026-06-14-pr-issue-link-policy.md`

## Functional smoke matrix

| Feature or workflow | Caller surface | Safest data mode | Expected result | Command | Result | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Repo PR policy wording | repo doc spot-check | docs-only | workflow doc requires a dedicated PR-body closing-keyword line when a real issue exists and forbids fake keywords otherwise | `rg -n "closes #|issue" .agents/docs/github-workflow.md` | passed | `.agents/docs/github-workflow.md:88-91`; `.agents/docs/github-workflow.md:105-106` |
| Harness PR delivery wording | repo doc spot-check | docs-only | harness PR delivery mentions both the dedicated closing-keyword-line rule and the no-fake-keyword rule | `rg -n "closes #|issue" .agents/docs/harness-engineering.md` | passed | `.agents/docs/harness-engineering.md:57`; `.agents/docs/harness-engineering.md:312` |
| Separate PR branch carries only staged repo diff | repo branch spot-check | docs-only | isolated branch contains only the repo policy docs and plan file for this PR | `git status --short --branch`; `git diff --cached --stat` | passed | `feature/pr-issue-link-policy`; cached diff contains 3 repo files only |

## Architecture/ADR constraints

- No architecture or ADR behavior change; this is workflow/policy wording only.
- Preserve existing branch/base rules and no-fake-keyword behavior when no real
  issue exists.

## Risks and rollback notes

- Risk: repo docs and local skill could drift because the local skill update is
  not part of this repository PR.
- Risk: over-broad wording could imply an issue must always exist, which is not
  true for all PRs.
- Rollback: revert only these policy wording edits if the team prefers a
  different linkage convention.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Moved the staged repo diff from the dirty source worktree into a fresh `origin/main`-based isolated branch for separate PR delivery. | `git fetch origin main`; `git worktree add -b feature/pr-issue-link-policy ... origin/main`; `git apply /tmp/pr-issue-link-policy.patch` |
| Planning | completed | Updated the plan for repo-only PR scope and isolated branch delivery. | `docs/plan/2026-06-14-pr-issue-link-policy.md` |
| Policy scan | completed | Re-read repo workflow rules and PR-create skill guidance before separating the PR. | `sed`; `rg -n "closes #|issue|이슈"` |
| Policy update | completed | Applied the staged repo policy docs into the isolated PR branch. | `.agents/docs/github-workflow.md`; `.agents/docs/harness-engineering.md` |
| Verification | completed | Ran the repo docs-only verification checklist and confirmed the closing-keyword wording in the repo docs and plan. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check`; `rg -n "closes #|issue|이슈" .agents/docs/github-workflow.md .agents/docs/harness-engineering.md docs/plan/2026-06-14-pr-issue-link-policy.md` |
| Review retry 4 | completed | Aligned the plan's recorded verification set with the repo's required docs-only checklist and reran the added checks. | `docs/plan/2026-06-14-pr-issue-link-policy.md` |
| Review retry 5 | completed | Corrected the workflow-doc evidence lines in the plan so the recorded proof points match the actual closing-keyword policy text. | `docs/plan/2026-06-14-pr-issue-link-policy.md` |
| Review retry 6 | completed | Tightened the policy wording to require a dedicated PR-body closing-keyword line instead of a closable token embedded in arbitrary prose. | `.agents/docs/github-workflow.md`; `.agents/docs/harness-engineering.md`; `docs/plan/2026-06-14-pr-issue-link-policy.md` |
| Review retry 1 | completed | Fixed the first review pass findings by removing the undefined no-issue placeholder wording and by aligning the plan's recorded `rg` verification command with the actual verification scope. | `.agents/docs/github-workflow.md`; `docs/plan/2026-06-14-pr-issue-link-policy.md` |
| Review retry 2 | completed | Fixed the remaining review findings by making the plan request conditional and by mirroring the no-fake-keyword rule in harness PR delivery guidance. | `.agents/docs/harness-engineering.md`; `docs/plan/2026-06-14-pr-issue-link-policy.md` |
| Review retry 3 | completed | Tightened the wording from generic issue-link text to explicit PR-body closing-keyword guidance and clarified the `gh pr create` example path for adding the closing line. | `.agents/docs/github-workflow.md`; `.agents/docs/harness-engineering.md`; `docs/plan/2026-06-14-pr-issue-link-policy.md` |
| PR separation | in_progress | Preparing commit, review gate, and PR delivery from the isolated branch that contains only the staged repo diff. | `git status --short --branch` |
