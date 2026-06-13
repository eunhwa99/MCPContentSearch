## User request

Initial user request: update the harness engineering guidance so that whenever a feature is added, the work also adds test coverage, E2E coverage, and eval coverage, then verifies them.

## Branch preflight result

- Started from a clean detached-HEAD worktree at `7a316a9`.
- Read branch policy and harness docs before edits.
- Fetched `origin/main`, switched to local `main`, and fast-forwarded to `34ba8c6`.
- Deleted one safe local non-`main` branch not checked out in any linked worktree: `feature/fix-ci-workflow-and-indexer-tests-pr`.
- Created fresh task branch `feature/harness-require-test-e2e-eval`.

## Scope and non-goals

- Scope:
  - Update harness guidance to require feature additions to include focused tests for the changed behavior.
  - Require adding or updating retained functional E2E coverage when the feature changes a user-visible or MCP-visible workflow, while keeping the repo-wide functional E2E regression gate as a separate broader check for code-changing work.
  - Require adding or updating eval coverage when the feature changes retrieval, ranking, grounding, citation selection, answer quality, or another quality-sensitive output already modeled by retained local evaluations, and extend an existing retained eval surface when the feature is inside retained eval coverage but no exact retained surface exists yet.
  - Clarify that those additions must be verified before review.
- Non-goals:
  - No runtime code changes.
  - No new repository tests or eval cases in this work item.
  - No change to the repository's default PR-delivery workflow; commit/push/PR still proceed after a clean final review unless the user asks for local-only work or a blocker appears.

## Acceptance criteria

- `.agents/docs/harness-engineering.md` explicitly states that feature additions require focused tests, adding or updating retained functional E2E only for user-visible or MCP-visible workflow changes, and adding or updating eval coverage for already-modeled retained local evaluation outputs, including extending an existing retained eval surface when needed, while preserving the separate repo-wide functional E2E regression gate for code-changing work.
- The retry loop explicitly reruns focused verification, including newly added or updated retained E2E coverage and any matching eval command, before smoke/review after fixes.
- Skill guidance that orchestrates or runs harness verification is aligned with the same expectation.
- Docs-only verification passes for the touched files.

## Step breakdown

1. `plan-and-scope`
   - Confirm the exact harness files that define the policy.
   - Keep the change atomic and documentation-only.
2. `update-guidance`
   - Add the new requirement to the harness engineering doc and the directly related skill guidance.
   - Keep wording specific about when eval coverage applies.
3. `docs-verification`
   - Run docs-only verification commands after staging the relevant files.

## Files likely to change

- `.agents/docs/harness-engineering.md`
- `.agents/skills/harness-engineering/SKILL.md`
- `.agents/skills/harness-test/SKILL.md`
- `docs/plan/2026-06-13-harness-require-test-e2e-eval.md`

## Test and verification plan

- `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`
- `git status --short --branch`
- `git diff --check`
- Stage only the relevant docs files.
- `git diff --cached --check`

## Functional smoke matrix or planned matrix rows before review

| Surface | Check | Mode | Status | Notes |
| --- | --- | --- | --- | --- |
| Harness guidance docs | Path and whitespace validation for touched docs | local docs-only | passed | Docs-only path listing, status, whitespace, and cached staged diff checks completed; no runtime smoke was needed for this atomic docs-only update. |

## Architecture/ADR constraints

- This is a docs-only harness policy update.
- Do not change runtime architecture, MCP contracts, or ADR decisions.
- Keep guidance aligned with the retained local-first verification model documented in architecture and existing eval/functional E2E docs.

## Risks and rollback notes

- Risk: wording could over-require evals for purely mechanical changes.
- Mitigation: scope evals to feature additions and quality-sensitive behavior rather than every code diff.
- Rollback: revert the touched docs files on this feature branch if the wording is judged too broad.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Fetched `origin/main`, fast-forwarded local `main`, deleted one safe local branch, and created `feature/harness-require-test-e2e-eval`. | `git status --short --branch`; `git branch -vv`; `git worktree list`; `git fetch origin main`; `git pull --ff-only origin main`; `git switch -c feature/harness-require-test-e2e-eval` |
| Planning | completed | Wrote the docs-only plan and recorded that this is an atomic documentation change, so the main agent may edit directly without worker orchestration. | This plan |
| Guidance update | completed | Updated harness engineering doc and aligned skills to require focused tests for feature additions, adding or updating retained E2E only for user-visible or MCP-visible workflow changes, and adding or updating eval coverage only for already-modeled retained local evaluation outputs, then verify them before review. | `.agents/docs/harness-engineering.md`; `.agents/skills/harness-engineering/SKILL.md`; `.agents/skills/harness-test/SKILL.md` |
| Docs verification | completed | Ran docs-only path listing, status, whitespace checks, staged-file cached diff check, and confirmed the staged docs diff is clean. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git add ...`; `git diff --cached --check` |
| Review remediation 1 | completed | Fixed review findings by aligning the plan with default PR-delivery policy, marking the docs-only smoke row as passed, tightening the focused-test/E2E/eval requirement split, and naming the retained `tests/evals` verification path alongside the fixture runner. | Reviewers `019ebef6-ecd0-7a32-b4c4-57f2ef5b891b`, `019ebef7-1fd7-73a2-8a75-cefff7200e04`, `019ebef7-45ae-7bd3-ae50-2d9de7f51c73`, `019ebef7-6a35-71a0-a024-e3b4e6d28595`, `019ebef7-8f61-7aa3-bd54-a48e610f5113` |
| Post-remediation verification 1 | completed | Reran the full affected docs-only verification after review remediation and before the next fresh review pass. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git add .agents/docs/harness-engineering.md .agents/skills/harness-engineering/SKILL.md .agents/skills/harness-test/SKILL.md docs/plan/2026-06-13-harness-require-test-e2e-eval.md`; `git diff --cached --check` |
