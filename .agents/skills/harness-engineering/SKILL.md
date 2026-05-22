---
name: harness-engineering
description: Orchestrates MCPContentSearch harness work for implementation, fixes, refactors, tests, docs, planning, review gates, retry loops, and PR delivery.
---

# Harness Engineering

## Reference Docs

Read `.agents/docs/harness-engineering.md` and `.agents/docs/github-workflow.md` first. For file-changing work, do not edit target files until branch preflight is complete and a `docs/plan/` plan exists.

During planning, read `.agents/docs/architecture.md` and `.agents/docs/adr/README.md`. Open only accepted ADRs directly related to the change.

## Phases

Run phases and gates in this order:

0. Branch preflight: when the worktree is clean, update local `main` from `origin/main`, clean only safe local non-`main` work branches using `.agents/docs/github-workflow.md` safeguards, create a fresh `feature/...` task branch, and record worktree safety. Preserve local-only commits and linked-worktree branches unless the user explicitly approves cleanup.
1. Plan document: create or update `docs/plan/YYYY-MM-DD-short-task-name.md`.
2. `.agents/skills/harness-plan/SKILL.md`
3. `.agents/skills/harness-multitask/SKILL.md`, if multiple tasks need decomposition.
4. Implementation lane: `.agents/skills/harness-implement/SKILL.md`
5. Test lane: `.agents/skills/harness-test/SKILL.md`
6. Middle review gate: `.agents/skills/harness-review/SKILL.md`, invoking `$subagent-review-loop`
7. Refactor phase: `.agents/skills/harness-refactor/SKILL.md`
8. Integration phase: `.agents/skills/harness-integrate/SKILL.md`
9. Final review gate: `.agents/skills/harness-review/SKILL.md`, invoking `$subagent-review-loop`
10. PR delivery: after the final clean `$subagent-review-loop` pass, stage only relevant files, commit, push, and create a `main`-base PR by default unless the user explicitly asks for local-only work or a safety blocker prevents delivery.

Implementation and test lanes may run in parallel when ownership is disjoint and the active tool policy allows delegation. Code-changing work must run relevant tests or verification before any review gate.

## Loop Rules

Treat implementation, testing, review, refactor, integration, and final review as a retryable loop. Every new task must start from an updated `main` and a fresh `feature/...` branch after safe local non-`main` branch cleanup, unless the user explicitly asks to continue an existing branch. If the worktree is dirty, fetch `origin/main` when network is available, create an isolated worktree with a fresh `feature/...` branch from `origin/main`, and ask or report a blocker before switching, pulling, or deleting branches in the dirty worktree. Review gates must use `$subagent-review-loop`: run relevant verification first, spawn exactly five fresh reviewer subagents per pass until all five reviewers in the newest pass report no actionable findings, and rerun affected verification before each new review pass after fixes.

Use review lenses from `.agents/docs/harness-engineering.md`: MCP contract, indexing/vector-store, fetching/network, async/background, config/secrets, test-quality, and docs-only.

If `$subagent-review-loop` cannot run because subagent review is unavailable or unauthorized, stop and report the blocker instead of silently using self-review. Do not respond on GitHub, watch PRs, or push follow-up PR changes unless the user explicitly delegates that work. For file-changing harness work, the repository standing workflow is to commit, push, and create a PR after the final clean `$subagent-review-loop` pass unless the user explicitly asks for local-only work.
