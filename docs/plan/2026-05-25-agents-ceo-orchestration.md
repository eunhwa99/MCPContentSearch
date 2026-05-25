# AGENTS CEO Orchestration Instruction Plan

## User Request

Update `AGENTS.md` so future MCPContentSearch work makes the main agent act as
the CEO/orchestrator and spawn role-appropriate subagents instead of silently
collapsing worker orchestration into direct self-implementation.

## Branch Preflight Result

- Primary checkout `/Users/eunhwa/IdeaProjects/MCPContentSearch` was dirty on
  `feature/contextwiki-phase-b-connectors`.
- Fetched `origin/main`.
- Created isolated worktree `/private/tmp/MCPContentSearch-agents-ceo` on
  `feature/agents-ceo-orchestration` from `origin/main`.
- Target edits will happen only in the isolated worktree.

## Scope and Non-Goals

Scope:

- Tighten `AGENTS.md` Project Harness guidance for mandatory worker
  orchestration.
- Preserve existing branch, verification, review, and PR policies.
- Add this plan document.

Non-goals:

- Do not change runtime Python code, MCP tools, tests, architecture docs, ADRs,
  or user data.
- Do not rewrite the full harness documentation set unless review finds a
  consistency bug that must be fixed.

## Acceptance Criteria

- `AGENTS.md` says the main agent is the orchestrator/CEO for file-changing
  work.
- It requires subagent/delegation tool discovery before non-plan target edits
  when work is not truly atomic.
- It requires role-specific workers with clear ownership, acceptance criteria,
  and no-revert constraints.
- It defines narrow exceptions where the main agent may implement directly and
  requires recording the reason in the plan.
- It keeps review subagents separate from implementation/test/docs workers.

## Files Likely to Change

- `AGENTS.md`
- `docs/plan/2026-05-25-agents-ceo-orchestration.md`

## Test and Verification Plan

Docs-only verification:

```bash
rg --files AGENTS.md .agents/docs .agents/skills docs/plan
git status --short --branch
git diff --check
git diff --cached --check
```

Review gate:

- Run a fresh five-reviewer subagent review pass after verification.
- Fix any actionable findings, rerun affected verification, and repeat with
  five fresh reviewers until the newest pass is clean.

## Architecture/ADR Constraints

- This is instruction-only harness work. It does not affect MCP runtime
  architecture, persistence, connectors, Chroma, SQLite metadata, or LLM policy.
- No accepted ADR directly constrains this wording change.
- No new ADR is required.

## Risks and Rollback Notes

- Risk: Wording becomes too broad and forces unnecessary worker spawning for
  tiny edits. Mitigation: keep a clear atomic-change exception that must be
  recorded in the plan.
- Risk: Worker and reviewer roles get conflated. Mitigation: explicitly separate
  implementation/test/docs/integration workers from `$subagent-review-loop`
  reviewers.
- Rollback: revert the AGENTS.md section and remove this plan document.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created isolated worktree from `origin/main` because primary checkout was dirty. | `git fetch origin main`; `git worktree add -b feature/agents-ceo-orchestration /private/tmp/MCPContentSearch-agents-ceo origin/main` |
| Planning | completed | Scoped docs-only AGENTS orchestration wording update. | This file |
| Implementation | completed | Updated `AGENTS.md` with mandatory CEO/worker orchestration rules. Main agent edited directly because this is an atomic docs-only change to a single target instruction surface; final review still uses fresh reviewer subagents. | `AGENTS.md` |
| Verification | completed | Docs-only path listing, status, unstaged whitespace, and staged whitespace checks passed before review. | `rg --files AGENTS.md .agents/docs .agents/skills docs/plan`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 1 | completed | Fresh five-reviewer pass found actionable issues: unavailable subagent fallback was too broad, worker secret/local-data carveout was too permissive, and plan evidence omitted cached whitespace check. | Reviewers: Pascal, Locke, Herschel, Darwin, Singer |
| Review pass 1 remediation | completed | Tightened non-atomic unavailable-subagent behavior to stop and ask the user, made secret values non-delegable, required explicit user approval plus plan rationale for local data access/mutation, and recorded cached diff verification evidence. | `AGENTS.md`; this file |
| Review pass 2 | completed | Fresh five-reviewer pass found one actionable issue: shared-file overlap wording could still let future agents bypass workers for non-atomic work. | Reviewers: Carson, Jason, Beauvoir, Feynman, Anscombe |
| Review pass 2 remediation | completed | Narrowed direct implementation to truly atomic changes only; unsafe overlap now requires stopping to ask the user before bypassing workers, with single-owner or sequential worker handoff preferred. | `AGENTS.md` |
| Verification | completed | Docs-only path listing, status, unstaged whitespace, and staged whitespace checks passed after pass 2 remediation. | `rg --files AGENTS.md .agents/docs .agents/skills docs/plan`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 3 | completed | Fresh five-reviewer pass was not counted clean because one response was not a valid read-only review result, even though the other four reported no actionable findings. | Reviewers: Franklin, Einstein, Averroes, Mencius, Bacon |
| Review pass 4 | completed | Fresh five-reviewer pass reported no actionable findings. | Reviewers: Heisenberg, Gauss, Parfit, Erdos, Mill |
| PR delivery | completed | Committed, pushed, and opened a main-base PR after clean review. | PR #10 |
