---
name: harness-functional-smoke
description: Use when MCPContentSearch changes are implemented and user-visible behavior, MCP tools, sync paths, Web Console flows, smoke scripts, or PR evidence need final functional validation.
---

# Harness Functional Smoke

## Purpose

Run this gate after focused tests pass and before any `$subagent-review-loop`.
It proves the task-relevant feature inventory works once through the safest real
caller surfaces, not only through unit tests or helper functions.

## Inputs

Read the current plan, local diff, `.agents/docs/harness-engineering.md`,
`.agents/docs/architecture.md`, `.agents/docs/functional-smoke-matrix.md`,
and directly relevant accepted ADRs.

## Build The Matrix

Create or update a smoke matrix in the plan before review. PR notes may later
copy or link to that plan section, but the pre-review source of truth is the
plan. Include rows for the task-relevant feature inventory: every changed
feature, every directly affected neighboring feature, and the core workflows a
user would naturally expect to still work after the change.

- Feature or workflow.
- Caller surface: MCP tool/client, Web Console browser UI, CLI/script smoke, or
  documented local fake/temp harness.
- Safest data mode: fake fixture, temporary Chroma/SQLite paths, mock source,
  dry run, or explicitly approved live source.
- Expected visible result or error state.
- Command, browser action, or MCP call used.
- Result: `passed`, `failed`, `not affected`, or `blocked/gated`.
- Evidence location: plan entry, screenshot/log path, or exact command summary.
- Blocker and nearest substitute when `blocked/gated`.

Every task-relevant feature gets a row. A `blocked/gated` row is acceptable only
when it records the blocker, approval needed if any, and the nearest safe
substitute.

## Caller Surface Rules

Prefer the highest real surface that can run safely:

1. Web Console UI for browser-facing behavior. Start the local Web Console when
   feasible, use the in-app browser, click the affected controls, and verify
   visible success or safe failure text.
2. MCP tool call or FastMCP/local client smoke for MCP contract behavior.
3. Repo smoke script for workflows already covered by deterministic scripts,
   such as fake wiki generation.
4. Unit-level or import-only checks only as the nearest substitute when the real
   caller surface is blocked; record the reason.

Do not use live external APIs, live LLM synthesis, configured source syncs that
touch user Chroma/SQLite, or local user-data mutation unless the user explicitly
approved the exact source/action and the plan records temporary-storage or
rollback safety.

## Required Coverage

Start from the full inventory below, then mark rows `passed`, `failed`,
`not affected`, or `blocked/gated` rather than silently omitting them. Common
MCPContentSearch surfaces:

- MCP tools: `list_sources`, `sync_source`, `get_sync_status`,
  `search_context`, `fetch_context`, `answer_with_citations`,
  `generate_wiki_page`, legacy search/index/status tools.
- Configured-source sync: the normal `sync_source(source_id)` path for
  configured Notion, Tistory, GitHub, or website/docs sources.
- Target or ad hoc sync: one-off GitHub repository, URL, fake source, or script
  path that does not share cleanup assumptions with configured-source sync.
- Web Console UI: Answer, Generate Wiki, Markdown/JSON download, source filters,
  `source_id` filters, configured source Sync buttons, GitHub target sync, Fake
  Smoke, GitHub Smoke, health/status, citations, backlinks, used chunks,
  sources, and safe error text.
- Storage-sensitive flows: prefer temporary Chroma/SQLite paths and fake
  fixtures; never inspect or mutate local user Chroma/SQLite data without
  explicit approval.
- External connector flows: use mocked/fake/temp checks by default. Live
  Notion, Tistory, GitHub, website/docs, or LLM checks need approval and must
  avoid printing tokens or source-private content.

## Evidence

Before review, record:

- Matrix rows with result, skip reason, and nearest substitute.
- Commands run and concise outcomes.
- Browser UI actions and visible result when UI behavior changed.
- Live-check approval status, source scope, and storage mode when applicable.

PR text must include the same matrix summary or link to the plan section. If a
review finding changes behavior, rerun the affected matrix rows plus any
dependent smoke rows before a fresh review pass.
