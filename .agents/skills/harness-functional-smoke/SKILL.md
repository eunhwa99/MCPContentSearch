---
name: harness-functional-smoke
description: Use when context-zip changes are implemented and user-visible behavior, MCP tools, source sync, search, citation answer, storage, or PR evidence need final functional validation.
---

# Harness Functional Smoke

## Purpose

Run this gate only after focused unit/integration/E2E tests pass, refactoring
and affected-test reruns finish, `./scripts/verify_all.sh` succeeds, any
matching eval gate required by feature scope has already been satisfied after
that full-suite gate (record full-suite quality-eval evidence when already
covered; otherwise run the focused matching eval command), and improvement
after/delta has been recorded (or an explicit `n/a` rationale) after the
latest applicable gate. Run it before any three-reviewer harness loop.
It proves the task-relevant feature inventory works once through the safest real
caller surfaces, not only through unit tests or helper functions.

## Inputs

Read the current plan when one is required, the recorded plan-exempt reason
otherwise, local diff, `.agents/docs/harness-engineering.md`,
`.agents/docs/architecture.md`, and `.agents/docs/functional-smoke-matrix.md`.

## Build The Matrix

Create or update a smoke matrix in the plan before review. For plan-exempt
work, put the matrix in the reviewer context and final/PR evidence instead of
creating a plan solely for the matrix. Include rows for the task-relevant
feature inventory: every changed
feature, every directly affected neighboring feature, and the core workflows a
user would naturally expect to still work after the change.

- Feature or workflow.
- Caller surface: MCP tool/client, CLI/script smoke, or documented local
  fake/temp harness.
- Safest data mode: fake fixture, temporary Chroma/SQLite paths, mock source,
  dry run, or live source with explicit user approval and a plan.
- Expected visible result or error state.
- Command, browser action, or MCP call used.
- Result: `passed`, `failed`, `not affected`, or `blocked/gated`.
- Evidence location: plan entry, reviewer/final evidence, screenshot/log path,
  or exact command summary.
- Blocker and nearest substitute when `blocked/gated`.

Every task-relevant feature gets a row. A `blocked/gated` row is acceptable only
when it records the blocker, approval needed if any, and the nearest safe
substitute.

## Caller Surface Rules

Prefer the highest real surface that can run safely:

1. MCP tool call or FastMCP/local client smoke for MCP contract behavior.
2. Repo smoke script or retained functional test for workflows already covered
   by deterministic scripts/tests.
3. Unit-level or import-only checks only as the nearest substitute when the real
   caller surface is blocked; record the reason.

Do not use live external APIs, configured source syncs that touch user
Chroma/SQLite, local user-data mutation, or destructive actions unless the user
explicitly approved the exact source/action and a plan records
temporary-storage or rollback safety. If plan-exempt work discovers such a
need, reclassify it as non-exempt and write the plan before acting, or record
the check as `blocked/gated` and use a fake/temp substitute.

## Required Coverage

Start from the full inventory below, then mark rows `passed`, `failed`,
`not affected`, or `blocked/gated` rather than silently omitting them. Common
context-zip surfaces:

- MCP tools: `list_sources`, `sync_source`, `sync_all`, `get_sync_status`,
  `search_context`, `search_documents`, `fetch_context`.
- Retained answer coverage through local demo/smoke scripts or retained tests
  when grounded answer behavior is affected.
- Configured-source sync: the normal `sync_source(source_id)` path for
  configured Notion, Tistory, or GitHub sources.
- Storage-sensitive flows: prefer temporary Chroma/SQLite paths and fake
  fixtures; never inspect or mutate local user Chroma/SQLite data without
  both explicit user approval and a plan. Plan-exempt work must reclassify or
  keep the check `blocked/gated`.
- External connector flows: use mocked/fake/temp checks by default. Live
  Notion, Tistory, or GitHub checks need both explicit user approval and a plan
  and must avoid printing tokens or source-private content. Reclassify
  plan-exempt work before a live check or keep it `blocked/gated`.

## Evidence

Before review, record:

- Matrix rows with result, skip reason, and nearest substitute.
- Commands run and concise outcomes.
- Browser UI actions and visible result when UI behavior changed.
- Live-check approval status, source scope, and storage mode when applicable.

PR text must include the same matrix summary or link to the plan section when
one exists. If a review finding changes behavior, rerun the affected matrix
rows plus any dependent smoke rows before a fresh review pass.
