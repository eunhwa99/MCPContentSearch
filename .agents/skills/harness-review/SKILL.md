---
name: harness-review
description: Middle and final $subagent-review-loop gate for MCPContentSearch changes, focused on bugs, regressions, tests, contracts, data safety, secrets, and architecture/ADR compliance.
---

# Harness Review

## Location

Run this gate with `$subagent-review-loop`:

- After implementation and test lanes are merged.
- After integration verification and before final response.

Before starting this gate, relevant verification must already have run. If actionable findings exist, update the plan, return to implementation/test, rerun the affected verification, then start a new fresh subagent review pass. Stop only when the newest fresh reviewer reports no actionable findings.

## Input

Read the plan, local diff, `.agents/docs/harness-engineering.md`, `.agents/docs/architecture.md`, `.agents/docs/adr/README.md`, relevant accepted ADRs, verification history, and changed files.

## Subagent Review Loop

Use `$subagent-review-loop` exactly:

1. Finish the local change and run relevant verification first.
2. Spawn a new subagent for review.
3. Give the reviewer task-local context: requirements, changed files, relevant docs, and verification output.
4. Ask for findings first, ordered by severity, with file and line references.
5. Fix every actionable finding.
6. Rerun affected verification.
7. Spawn another fresh subagent.
8. Repeat until the newest reviewer reports no actionable findings.

If subagent review is unavailable or unauthorized, do not replace it silently. Stop and report the blocker. Continue with self-review only after explicit user approval to bypass `$subagent-review-loop`.

## Review Lenses

Apply relevant lenses:

- MCP contract: tool names, parameters, return types, error messages, README/client docs.
- Indexing/vector-store: Chroma mutation, content hash, dedup/update, status, local data safety.
- Fetching/network: Notion/Tistory behavior, partial failures, rate limits, timeouts, credentials.
- Async/background: `asyncio.create_task`, hidden failures, concurrency, status truthfulness.
- Config/secrets: token handling, `.env`, logging, local paths.
- Test-quality: focused coverage, mocked external APIs, compile/import checks, smoke checks.
- Change-size/staging: whether the diff should be split.
- Docs-only: path references, phase names, skill names, command examples, whitespace.

## Output

Produce a checklist:

| Item | Result | Notes |
| --- | --- | --- |
| Architecture/ADR compliance | pass/fail/n/a | Relevant violation or n/a reason |
| Acceptance criteria | pass/fail/n/a | Missing behavior |
| Tests/verification | pass/fail/n/a | Commands run or gaps |
| Security/data/API risk | pass/fail/n/a | Secrets, Chroma, MCP contract, external API |
| Change size/staging | pass/fail/n/a | Split or stacked PR need |
| Docs-only policy | pass/fail/n/a | Path listing, status, diff check |

Findings must include file path, reason, and suggested fix. The final handoff must state the verification command and that the final fresh `$subagent-review-loop` review had no actionable findings. If the loop was explicitly bypassed by user approval, state that instead.
