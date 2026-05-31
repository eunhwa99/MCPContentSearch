---
name: harness-plan
description: Planning phase for MCPContentSearch harness work; converts a request into acceptance criteria, module boundaries, verification, risks, and retryable steps.
---

# Harness Plan

## Input

Read:

- User request
- `AGENTS.md`
- `.agents/docs/harness-engineering.md`
- Current `docs/plan/...` plan document
- `.agents/docs/architecture.md`
- `.agents/docs/adr/README.md`
- Directly relevant accepted ADRs
- Minimal code or docs context needed for the work

Read `.agents/docs/github-workflow.md` when branch, commit, push, PR, or release work is involved.

## Output

The plan must include:

- Desired behavior and acceptance criteria.
- Plan document path and latest update.
- Branch preflight result: current branch, worktree state, and `main`/feature branch safety.
- Step breakdown when the work has ordered parts.
- For multi-task requests, independent task split, owner modules/files, and parallel-worker suitability.
- Likely changed files and module boundaries.
- Tests or verification to add or run.
- Focused verification commands.
- Functional smoke matrix plan: rows to cover, caller surfaces, safe data modes,
  and approval-gated rows before review.
- Integration or additional smoke scenario when needed.
- Whether the change is docs-only.
- MCP tool contract documentation updates when tool behavior changes.
- Local ChromaDB or SQLite metadata impact, if any.
- External source connector credential or network requirements, including
  Notion, Tistory, GitHub, and website/docs, if any.
- Risks, open questions, environment requirements, and rollback point.
- Architecture/ADR constraints.
- PR split or stacked PR plan if PRs are requested.
- Progress table with `Phase`, `Status`, `Summary`, and `Evidence`.

## Rules

If `docs/plan/...` does not exist for file-changing work, stop and create it first.

Use conservative assumptions when safe and record them. Ask one short question
only when a wrong assumption could cause data loss, expose secrets, change MCP
contracts unexpectedly, or mutate user Chroma data or SQLite metadata.

Each step must be self-contained enough for a future agent to execute without hidden conversation context. Include files to read, previous outputs, explicit boundaries, and executable acceptance criteria.
