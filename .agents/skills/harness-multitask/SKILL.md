---
name: harness-multitask
description: Multi-task orchestration phase for splitting independent MCPContentSearch work into safe ownership, branches/worktrees, review gates, and PR boundaries.
---

# Harness Multitask

## Input

Read the user request, current plan document, `.agents/docs/harness-engineering.md`, `.agents/docs/github-workflow.md`, branch preflight result, and relevant architecture docs.

## Task Split

Split tasks only when they are independently executable and reviewable.

Each task needs:

- Acceptance criteria.
- Owner files/modules.
- Verification scope.
- Expected PR boundary, if PRs are requested.

Do not split tasks as independent when they modify the same MCP tool contract, shared config, Chroma/indexing semantics, external fetcher contract, or public response shape.

## Worker Boundaries

When delegation is allowed:

- Assign disjoint file ownership.
- Use separate `feature/...` branches or isolated worktrees.
- Tell workers they are not alone in the codebase, must not revert others' changes, and must adapt to concurrent changes.
- Do not let workers commit, push, or open PRs unless the user explicitly delegated that.

## Integration

The main agent owns result collection, conflict resolution, focused verification, review gates, and final reporting.

For PRs:

- Independent PRs use `base=main`.
- Ordered changes use stacked PRs, with each later PR based on the previous feature branch.
