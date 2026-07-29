---
name: harness-implement
description: Implementation lane for scoped MCPContentSearch changes after planning, including retry fixes from verification or review.
---

# Harness Implement

## Input

Read the current plan when one is required, the recorded plan-exempt reason
otherwise, `.agents/docs/harness-engineering.md`,
`.agents/docs/github-workflow.md`, `.agents/docs/architecture.md`, and the
production files in scope.

## Work

For a feature or behavior change, do not edit production code until the TDD red
gate has:

- Added or updated unit, integration, and deterministic functional E2E
  coverage.
- Run the smallest relevant new or changed test.
- Confirmed that it fails for the expected missing behavior rather than a test,
  fixture, dependency, or environment problem.
- Recorded the exact command, covered test layers/names, non-zero exit code,
  expected failure signature, missing-behavior explanation, and
  pre-production-edit ordering in the plan or plan-exempt task evidence.

Then make the smallest useful change that satisfies the plan or task context
and turns the red test green. Refactor only while the affected tests remain
green.

Follow existing module boundaries:

- MCP contract and tool formatting in `api/`.
- Search orchestration in `search/`.
- Chroma/LlamaIndex writes, source-aware chunking, and indexing status in `indexing/`.
- External source connector behavior in `fetching/`.
- SQLite lifecycle and citation metadata in `storage/`.
- Shared models/errors/utilities in `core/`.
- Configuration in `environments/`.
- Composition in `main.py`.

Avoid unrelated cleanup. A worker running this skill must never inspect secret
values or inspect/mutate user Chroma data or SQLite metadata. If the main agent
must perform a bounded user-data operation, it requires both a plan and
explicit user approval and remains main-agent-only. Do not expose secrets.

## Output

Leave changes ready for the TDD green test lane, full-suite gate, matching
eval gate when required by feature scope (only after `./scripts/verify_all.sh`;
prefer recording full-suite quality-eval evidence when already covered),
functional smoke gate, and review gate. If returning from a failure, record the
first actionable failure and the changed code path in the plan progress log, or
in the task evidence for plan-exempt work.

Do not commit from the implementation lane. Final commit, push, and PR delivery
happen only after verification, any matching eval gate when in scope, the
functional smoke matrix, integration, and the final clean three-reviewer
harness pass unless the user explicitly asks for local-only work or a safety
blocker prevents delivery.
