---
name: harness-refactor
description: Refactor phase for safe simplification and local-pattern alignment after focused verification passes.
---

# Harness Refactor

## Input

Read the current plan when one is required, the recorded plan-exempt reason
otherwise, changed files, `.agents/docs/harness-engineering.md`,
`.agents/docs/github-workflow.md`, architecture docs, and surrounding code
patterns.

## Work

Look for safe refactoring opportunities introduced by this change:

- Meaningful duplication.
- Misplaced responsibility.
- Awkward names.
- Unnecessary abstraction.
- Test setup that obscures intent.
- Code that violates existing module boundaries.

Apply only refactors that reduce real complexity or align with local patterns. Avoid unrelated cleanup.

## Verification

Rerun the affected focused checks that passed before refactoring. Do not run
`./scripts/verify_all.sh` inside this phase; the next explicit post-refactor
full-suite gate owns the mandatory run for the current diff. If focused verification fails,
classify the failure, update the plan when one is required, and return to
implementation/test.
