---
name: harness-review
description: Middle and final $subagent-review-loop gate for MCPContentSearch changes, focused on bugs, regressions, tests, contracts, data safety, secrets, and architecture/ADR compliance.
---

# Harness Review

## Location

Run this gate with `$subagent-review-loop`:

- After implementation, test lanes, and the functional smoke gate are merged.
- After integration verification and before final response.

Before starting this gate, relevant verification and the functional smoke matrix
must already have run and be recorded in the plan. If actionable findings exist,
update the plan, route each issue to the responsible implementation, test, docs,
refactor, or integration worker persona or a fresh replacement with the same
ownership boundary, rerun the affected verification and affected functional
smoke entries, then start a new fresh five-reviewer subagent review pass. Stop
only when all five reviewers in the newest pass report no actionable findings.

## Input

Read the plan, local diff, `.agents/docs/harness-engineering.md`, `.agents/docs/architecture.md`, `.agents/docs/functional-smoke-matrix.md`, `.agents/docs/adr/README.md`, relevant accepted ADRs, verification history, functional smoke matrix/results, and changed files.

## Subagent Review Loop

Use `$subagent-review-loop` exactly:

1. Finish the local change, run relevant verification, and complete the
   functional smoke matrix first.
2. Spawn exactly five new reviewer subagents for the pass.
3. Give each reviewer task-local context: requirements, changed files, relevant
   docs, verification output, and functional smoke matrix/results from the plan.
4. Ask each reviewer for findings first, ordered by severity, with file and line references.
5. Fix every actionable finding. When delegation is available and safe, assign
   it back to the responsible worker persona or a fresh replacement; if the main
   agent fixes it directly because delegation is unavailable or unsafe, record
   that reason in the plan.
6. Rerun affected verification and affected functional smoke entries.
7. Spawn another fresh five-reviewer pass.
8. Repeat until all five reviewers in the newest pass report no actionable findings.

If subagent review is unavailable or unauthorized, do not replace it silently. Stop and report the blocker. Continue with self-review only after explicit user approval to bypass `$subagent-review-loop`.

## Review Lenses

Apply relevant lenses:

- MCP contract: tool names, parameters, return types, error messages, README/client docs.
- Indexing/vector-store/storage: Chroma mutation, SQLite lifecycle/tombstone metadata, content hash, dedup/update, status, local data safety.
- Fetching/network: external source connector behavior, partial snapshots/failures, rate limits, timeouts, credentials.
- Async/background: `asyncio.create_task`, hidden failures, concurrency, status truthfulness.
- Config/secrets: token handling, `.env`, logging, local paths.
- Test-quality: focused coverage, mocked external APIs, compile/import checks, smoke checks.
- Functional-smoke quality: task-relevant feature inventory, caller surfaces,
  safe data modes, blocked/gated rows, and nearest substitutes.
- Change-size/staging: whether the diff should be split.
- Docs-only: path references, phase names, skill names, command examples, whitespace, and staged diff checks.

## Output

Produce a checklist:

| Item | Result | Notes |
| --- | --- | --- |
| Architecture/ADR compliance | pass/fail/n/a | Relevant violation or n/a reason |
| Acceptance criteria | pass/fail/n/a | Missing behavior |
| Tests/verification | pass/fail/n/a | Commands run or gaps |
| Functional smoke matrix | pass/fail/n/a | Rows covered, blocked/gated checks, substitutes, and evidence |
| Security/data/API risk | pass/fail/n/a | Secrets, Chroma, SQLite metadata, MCP contract, external API |
| Change size/staging | pass/fail/n/a | Split or stacked PR need |
| Docs-only policy | pass/fail/n/a | Path listing, status, unstaged/staged diff checks |

Findings must include file path, reason, and suggested fix. After the final clean review pass, return to integration/PR delivery instead of stopping at local completion. The final handoff must state the verification command, functional smoke matrix result summary, that the final fresh five-reviewer `$subagent-review-loop` pass had no actionable findings, and the PR URL or PR delivery blocker. If the loop was explicitly bypassed by user approval, state that instead.
