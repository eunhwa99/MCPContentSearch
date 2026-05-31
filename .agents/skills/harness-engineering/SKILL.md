---
name: harness-engineering
description: Orchestrates MCPContentSearch harness work for implementation, fixes, refactors, tests, docs, planning, review gates, retry loops, and PR delivery.
---

# Harness Engineering

## Reference Docs

Read `.agents/docs/harness-engineering.md` and `.agents/docs/github-workflow.md` first. For file-changing work, do not edit target files until branch preflight is complete and a `docs/plan/` plan exists.

During planning, read `.agents/docs/architecture.md` and `.agents/docs/adr/README.md`. Open only accepted ADRs directly related to the change.

## Phases

Run phases and gates in this order. The main agent is the harness
orchestrator: it owns plan creation/updates, worker persona design, delegation,
result collection, synthesis, conflict resolution, review routing, and final
delivery.

0. Branch preflight: when the worktree is clean, update local `main` from `origin/main`, clean only safe local non-`main` work branches using `.agents/docs/github-workflow.md` safeguards, create a fresh `feature/...` task branch, and record worktree safety. Preserve local-only commits and linked-worktree branches unless the user explicitly approves cleanup.
1. Plan document: create or update `docs/plan/YYYY-MM-DD-short-task-name.md`.
2. `.agents/skills/harness-plan/SKILL.md`
3. Worker orchestration: define task-specific implementation, testing,
   documentation, or integration subagent personas with bounded file ownership,
   acceptance criteria, non-goals, and verification expectations. Use
   `.agents/skills/harness-multitask/SKILL.md` when work needs decomposition.
4. Implementation lane: `.agents/skills/harness-implement/SKILL.md`, delegated
   when tool policy and ownership boundaries allow.
5. Test lane: `.agents/skills/harness-test/SKILL.md`, delegated to a distinct
   verification persona when useful and safe.
6. Functional smoke gate: `.agents/skills/harness-functional-smoke/SKILL.md`,
   covering the task-relevant feature inventory once through the safest real
   caller surfaces before review, not only the files changed.
7. Middle review gate: `.agents/skills/harness-review/SKILL.md`, invoking `$subagent-review-loop`
8. Refactor phase: `.agents/skills/harness-refactor/SKILL.md`
9. Integration phase: `.agents/skills/harness-integrate/SKILL.md`
10. Final review gate: `.agents/skills/harness-review/SKILL.md`, invoking `$subagent-review-loop`
11. PR delivery: after the final clean `$subagent-review-loop` pass, stage only relevant files, commit, push, and create a `main`-base PR by default unless the user explicitly asks for local-only work or a safety blocker prevents delivery.

Implementation and test lanes may run in parallel when ownership is disjoint and the active tool policy allows delegation. Code-changing work must run relevant tests or verification plus the functional smoke gate before any review gate.

## Loop Rules

Treat implementation, testing, review, refactor, integration, and final review as a retryable loop. Every new task must start from an updated `main` and a fresh `feature/...` branch after safe local non-`main` branch cleanup, unless the user explicitly asks to continue an existing branch. If the worktree is dirty, fetch `origin/main` when network is available, create an isolated worktree with a fresh `feature/...` branch from `origin/main`, and ask or report a blocker before switching, pulling, or deleting branches in the dirty worktree.

Minimize human intervention by routing routine work through subagents when delegation is available. The main agent should inspect or update the plan, define worker personas, delegate bounded implementation/test/docs/integration tasks, collect outputs, inspect the resulting diff, and synthesize the integrated result. Ask the user only for unsafe ambiguity, credentials, destructive operations, local data mutation, unavailable delegation/review tools, or external approval.

If the main-agent synthesis or `$subagent-review-loop` produces actionable findings, update the plan, assign each issue back to the responsible worker persona or a fresh replacement with the same ownership boundary, rerun affected verification and affected functional smoke entries, and continue the loop. Review gates must use `$subagent-review-loop`: run relevant verification and functional smoke first, spawn exactly five fresh reviewer subagents per pass until all five reviewers in the newest pass report no actionable findings, and rerun affected verification plus affected functional smoke entries before each new review pass after fixes. Worker subagents may edit only within delegated boundaries; reviewer subagents inspect only and must not edit files.

Use review lenses from `.agents/docs/harness-engineering.md`: MCP contract, indexing/vector-store/storage including SQLite lifecycle/tombstone metadata, fetching/network for external connectors, async/background, config/secrets, test-quality, functional-smoke quality, and docs-only.

If `$subagent-review-loop` cannot run because subagent review is unavailable or unauthorized, stop and report the blocker instead of silently using self-review. Do not respond on GitHub, watch PRs, or push follow-up PR changes unless the user explicitly delegates that work. For file-changing harness work, the repository standing workflow is to commit, push, and create a PR after the final clean `$subagent-review-loop` pass unless the user explicitly asks for local-only work.
