---
name: harness-implement
description: Implementation lane for scoped MCPContentSearch changes after planning, including retry fixes from verification or review.
---

# Harness Implement

## Input

Read the current plan, `.agents/docs/harness-engineering.md`, `.agents/docs/github-workflow.md`, `.agents/docs/architecture.md`, relevant ADRs, and the production files in scope.

## Work

Make the smallest useful change that satisfies the plan or fixes the first actionable failure.

Follow existing module boundaries:

- MCP contract and tool formatting in `api/`.
- Search orchestration in `search/`.
- Chroma/LlamaIndex writes, source-aware chunking, and indexing status in `indexing/`.
- External source connector behavior in `fetching/`.
- SQLite lifecycle and citation metadata in `storage/`.
- Shared models/errors/utilities in `core/`.
- Configuration in `environments/`.
- Composition in `main.py`.

Avoid unrelated cleanup. Do not inspect or mutate local Chroma data or SQLite
metadata unless the plan and user approval allow it. Do not expose secrets.

## Output

Leave changes ready for the test lane and review gate. If returning from a failure, record the first actionable failure and the changed code path in the plan progress log.

Do not commit from the implementation lane. Final commit, push, and PR delivery happen only after verification, integration, and the final clean `$subagent-review-loop` pass unless the user explicitly asks for local-only work or a safety blocker prevents delivery.
