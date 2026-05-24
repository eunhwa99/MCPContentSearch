# Harness Subagent Orchestration Plan

## User request

Update the MCPContentSearch harness instructions so the main agent minimizes
human intervention by acting as an orchestrator: inspect or create the plan,
define task-specific subagent personas, delegate implementation or execution,
collect results, review them, and re-delegate fixes to the responsible
subagent when either the main-agent review or `$subagent-review-loop` finds
actionable issues.

## Branch preflight result

- Starting worktree: `/Users/eunhwa/IdeaProjects/MCPContentSearch`
- Starting branch: `feature/contextwiki-phase-b-connectors`
- Starting state: dirty with existing user/agent changes, so no switching,
  pulling, cleanup, or target edits were performed there.
- Freshness: ran `git fetch origin main` successfully on 2026-05-24.
- Isolated worktree: `/private/tmp/MCPContentSearch-harness-subagent-orchestration`
- Task branch: `feature/harness-subagent-orchestration` from `origin/main`
  at `08b73fc`.

## Scope and non-goals

- Scope: update repo-local harness instructions and skills for main-agent
  orchestration, implementation/execution subagent personas, result synthesis,
  review-driven re-delegation, and minimal-human-intervention retry loops.
- Non-goals: change Python MCP runtime behavior, change MCP tool contracts,
  change local Chroma/SQLite data, or rewrite the global
  `/Users/eunhwa/.codex/skills/subagent-review-loop/SKILL.md`.

## Acceptance criteria

- Harness instructions say the main agent owns plan verification/creation,
  worker persona design, delegation, result collection, synthesis, conflict
  resolution, and final delivery.
- Harness instructions say implementation/execution workers should be
  task-specific personas with clear ownership, context, acceptance criteria,
  and verification expectations.
- Retry loop explicitly routes actionable findings back to the responsible
  worker or a replacement worker, then reruns affected verification and a fresh
  five-reviewer `$subagent-review-loop`.
- Human input is required only for unsafe ambiguity, data loss, secrets,
  credentials, destructive operations, unavailable delegation/review tools, or
  external approvals.
- Docs-only verification passes.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| branch-preflight | completed | Created isolated branch from `origin/main` because the original worktree was dirty. | `git fetch origin main`; `git worktree add -b feature/harness-subagent-orchestration ... origin/main` |
| planning | completed | Read harness docs, architecture docs, ADR index, AGENTS, and relevant harness skills. | Current plan document |
| docs-update | completed | Updated repo-local harness docs and skills for main-agent orchestration, worker personas, and review-driven re-delegation. | `git diff -- AGENTS.md .agents/docs/harness-engineering.md .agents/skills/...` |
| verification | completed | Reran docs-only checks after the latest review remediation, including staged cached diff so new files are covered. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| review | in_progress | Fresh five-reviewer pass is required after the latest remediation; continue until the newest pass has no actionable findings. | `$subagent-review-loop` findings were routed back into docs fixes and verification |
| delivery | pending | Commit, push, and open `main`-base PR if final review is clean. | Pending |

## Files likely to change

- `AGENTS.md`
- `.agents/docs/architecture.md`
- `.agents/docs/github-workflow.md`
- `.agents/docs/harness-engineering.md`
- `.agents/skills/harness-engineering/SKILL.md`
- `.agents/skills/harness-integrate/SKILL.md`
- `.agents/skills/harness-multitask/SKILL.md`
- `.agents/skills/harness-review/SKILL.md`
- `.agents/skills/harness-test/SKILL.md`
- `docs/plan/2026-05-24-harness-subagent-orchestration.md`

## Test and verification plan

Docs-only verification:

```bash
rg --files AGENTS.md README.md docs .agents/docs .agents/skills
git status --short --branch
git diff --check
git diff --cached --check
```

## Architecture/ADR constraints

- Read `.agents/docs/architecture.md` and `.agents/docs/adr/README.md`.
- No accepted ADR directly governs harness-only orchestration wording.
- Do not change MCP tool contracts, persistence, external connector behavior,
  or local data handling.

## Risks and rollback notes

- Risk: wording could conflict with existing `$subagent-review-loop` semantics.
  Mitigation: keep review-loop reviewer behavior separate from implementation
  worker delegation.
- Risk: over-broad delegation could hide ownership. Mitigation: require main
  agent synthesis, responsibility routing, and explicit worker boundaries.
- Rollback: revert this docs-only branch/PR; no runtime state is mutated.
