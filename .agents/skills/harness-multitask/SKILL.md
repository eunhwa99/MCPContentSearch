---
name: harness-multitask
description: Multi-task orchestration phase for splitting independent MCPContentSearch work into safe ownership, branches/worktrees, review gates, and PR boundaries.
---

# Harness Multitask

## Input

Read the user request, current plan document, `.agents/docs/harness-engineering.md`, `.agents/docs/github-workflow.md`, branch preflight result, and relevant architecture docs.

## Task Split

Split tasks when they are independently executable and reviewable. This can
apply to multiple user-requested tasks or to one larger task that has distinct
implementation, testing, documentation, refactor, or integration ownership.

Each task needs:

- Acceptance criteria.
- Owner files/modules.
- Verification scope.
- Expected PR boundary, if PRs are requested.

Do not split tasks as independent when they modify the same MCP tool contract, shared config, Chroma/indexing semantics, SQLite lifecycle/tombstone metadata, external source connector contract, or public response shape.

## Worker Boundaries

When delegation is allowed:

- The main agent defines the worker persona before dispatch: role, goal, files
  or modules owned, required context, non-goals, acceptance criteria, and
  verification expectation.
- Assign disjoint file ownership.
- Use separate `feature/...` branches or isolated worktrees only for independent
  parallel work, separate PR boundaries, or dirty-worktree isolation. Routine
  same-feature worker personas can share the main task branch when ownership is
  disjoint and the main agent owns integration.
- Tell workers they are not alone in the codebase, must not revert others' changes, and must adapt to concurrent changes.
- Do not let workers commit, push, or open PRs unless the user explicitly delegated that.
- Tell workers not to inspect secrets, mutate local Chroma/SQLite data, or make
  destructive changes unless the plan and user authorization explicitly allow it.

## Integration

The main agent owns result collection, diff inspection, conflict resolution,
focused verification, review gates, issue routing, and final reporting. If a
worker result is incomplete or a later review finding maps to that worker's
ownership boundary, send the issue back to that worker persona or a fresh
replacement before rerunning verification and review.

For PRs:

- Independent PRs use `base=main`.
- Ordered changes use stacked PRs, with each later PR based on the previous feature branch.
