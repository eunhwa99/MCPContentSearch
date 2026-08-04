---
name: harness-review
description: Middle and final three-reviewer gate for context-zip changes with distinct correctness, security/data-safety, and performance/reliability lenses.
---

# Harness Review

## Location

Run this gate with the repository-local three-reviewer harness loop:

- After implementation, test lanes, improvement after/delta (or an explicit
  `n/a` rationale), and the functional smoke gate are merged.
- For docs/instruction-only work, after docs-only verification with
  improvement performance delta and functional smoke recorded as `n/a`.
- After integration verification and before final response.

Before starting this gate, feature/behavior code changes must have auditable
pre-production RED evidence, focused unit/integration/E2E GREEN results,
post-refactor affected-test results, a successful `./scripts/verify_all.sh`,
any matching eval gate required by feature scope satisfied only after that
full-suite gate (record full-suite quality-eval evidence when already covered;
otherwise run the focused matching eval command), and improvement after/delta
when improvement-scoped (or an explicit `n/a` rationale) recorded before
functional smoke. Pure refactor, test-only, or
other non-behavior code work records TDD chronology as `n/a` with a rationale,
then provides applicable focused GREEN, full-suite, matching-eval, and
improvement after/delta (or `n/a`) evidence.
Relevant verification, matching eval evidence when in scope, improvement
after/delta when improvement-scoped (or an explicit `n/a` / docs-only
rationale), and the functional smoke matrix must be recorded in the plan or in
reviewer/final evidence for plan-exempt work. If actionable findings exist,
update the plan
when one is required, route each issue to the responsible implementation, test,
docs, refactor, or integration worker persona or a fresh replacement with the
same ownership boundary, rerun the affected verification, any matching eval
gate required by feature scope only after `./scripts/verify_all.sh`, refresh
improvement after/delta evidence when improvement-scoped using temporary
Chroma/SQLite paths and mocked connectors; never inspect or mutate user data
without both explicit user approval and a plan, and affected
functional smoke entries, then start a new fresh three-reviewer pass.
Stop only when all three reviewers in the newest pass report no actionable
findings.

## Input

Read the plan when one is required, the recorded plan-exempt reason otherwise,
local diff, `.agents/docs/harness-engineering.md`,
`.agents/docs/architecture.md`, `.agents/docs/functional-smoke-matrix.md`,
verification history, improvement performance delta evidence or `n/a`
rationale, functional smoke matrix/results, and changed files.

## Three-Reviewer Loop

Use this loop exactly:

1. Finish the local change, run relevant verification, satisfy any matching
   eval gate required by feature scope only after `./scripts/verify_all.sh`
   (prefer recording full-suite quality-eval evidence when already covered),
   record improvement after/delta when improvement-scoped (one after
   measurement after the latest applicable gate using temporary Chroma/SQLite
   paths and mocked connectors; never inspect or mutate user data without both
   explicit user approval and a plan; do not require one remeasure per phase),
   and complete the functional smoke matrix first;
   docs/instruction-only work records the matrix and improvement delta as
   `n/a`.
2. Spawn exactly three fresh read-only reviewer subagents for the pass.
3. Give each reviewer task-local context: requirements, changed files, relevant
   docs, the RED command/test layers/non-zero exit/failure signature/order
   evidence for feature/behavior work or a non-behavior `n/a` rationale,
   focused and full-suite GREEN output, matching eval gate evidence when in
   scope, improvement performance delta evidence or `n/a` rationale, and
   functional smoke matrix/results or docs-only `n/a` from the plan or
   plan-exempt evidence.
4. Give each reviewer a different primary prompt:
   - Reviewer 1 — bugs and correctness: regressions, API/MCP contracts, error
     handling, architecture correctness, TDD chronology, and
     unit/integration/E2E quality.
   - Reviewer 2 — security and data safety: secrets, privacy,
     authentication/authorization, input validation, dependency risk, external
     service exposure, destructive behavior, and local Chroma/SQLite safety.
   - Reviewer 3 — performance and reliability: latency/complexity regressions,
     resource use, async/concurrency, timeouts/retries, lifecycle cleanup,
     scalability, observability, operational failure modes, and
     improvement-scoped before/after/delta coherence. Treat missing or
     incoherent delta evidence for improvement-scoped work as actionable.
5. Tell all reviewers they may report issues outside the primary lens, must
   report findings first in severity order with file/line references, and must
   not edit files.
6. Route every actionable finding to the responsible worker persona or a fresh
   replacement when delegation is available and safe. For a behavior-changing
   code/config fix, return first to the unit/integration/E2E RED gate and record
   fresh auditable pre-edit failure evidence; then implement GREEN, refactor,
   rerun affected tests, run `./scripts/verify_all.sh`, satisfy any matching
   eval gate required by feature scope only after that full-suite gate (record
   full-suite eval evidence when already covered; otherwise rerun the matching
   eval), refresh improvement after/delta when improvement-scoped using
   temporary Chroma/SQLite paths and mocked connectors; never inspect or mutate
   user data without both explicit user approval and a plan, and refresh
   affected smoke entries. For a non-behavior
   code/config/test fix, record RED as `n/a` without manufacturing a failure,
   then rerun affected focused tests, `./scripts/verify_all.sh`, any matching
   eval gate required by feature scope only after that full-suite gate (record
   full-suite eval evidence when already covered; otherwise rerun the matching
   eval), refresh improvement after/delta when improvement-scoped using
   temporary Chroma/SQLite paths and mocked connectors; never inspect or mutate
   user data without both explicit user approval and a plan, and affected
   smoke entries. For a docs-only fix,
   rerun the lightweight docs checks without manufacturing a fake RED. If the
   main agent fixes directly
   because delegation is unavailable or unsafe, record that reason in the plan
   or plan-exempt task evidence.
7. Confirm all affected verification, matching eval evidence when in scope,
   improvement after/delta when improvement-scoped, and functional smoke
   evidence matches the current diff.
8. Spawn another fresh three-reviewer pass with the same three distinct lenses.
9. Repeat until all three reviewers in the newest pass report no actionable
   findings.

If subagent review is unavailable or unauthorized, do not replace it silently.
Stop and report the blocker. Continue with self-review only after explicit user
approval to bypass the three-reviewer harness loop.

## Review Lenses

The three assigned primary lenses cover these task-relevant checks:

- MCP contract: tool names, parameters, return types, error messages, README/client docs.
- Indexing/vector-store/storage: Chroma mutation, SQLite lifecycle/tombstone metadata, content hash, dedup/update, status, local data safety.
- Fetching/network: external source connector behavior, partial snapshots/failures, rate limits, timeouts, credentials.
- Async/background: `asyncio.create_task`, hidden failures, concurrency, status truthfulness.
- Config/secrets: token handling, `.env`, logging, local paths.
- Test-quality: focused coverage, mocked external APIs, compile/import checks, smoke checks.
- Functional-smoke quality: task-relevant feature inventory, caller surfaces,
  safe data modes, blocked/gated rows, and nearest substitutes.
- Improvement-performance-delta: declared metrics, pre-edit baseline, one after
  measurement after the latest applicable gate, coherent delta table or
  explicit `n/a` rationale, quality claims via retained eval scores versus
  informational runtime/latency metrics, and environment notes free of secrets,
  credentials, PII, user content, and real user-data paths.
- Change-size/staging: whether the diff should be split.
- Docs-only: path references, phase names, skill names, command examples, whitespace, and staged diff checks.

## Output

Produce a checklist:

| Item | Result | Notes |
| --- | --- | --- |
| Architecture compliance | pass/fail/n/a | Relevant violation or n/a reason |
| Acceptance criteria | pass/fail/n/a | Missing behavior |
| TDD chronology | pass/fail/n/a | Pre-production RED command, layers/tests, non-zero exit, expected signature, and ordering |
| Tests/verification | pass/fail/n/a | Focused unit/integration/E2E, post-refactor reruns, `verify_all.sh`, and matching eval gate when in scope |
| Improvement performance delta | pass/fail/n/a | Before/after/delta table or explicit `n/a` rationale |
| Functional smoke matrix | pass/fail/n/a | Rows covered, blocked/gated checks, substitutes, and evidence |
| Security/data/API risk | pass/fail/n/a | Secrets, Chroma, SQLite metadata, MCP contract, external API |
| Performance/reliability | pass/fail/n/a | Complexity, latency, resource use, async/concurrency, retries, cleanup, observability, delta coherence |
| Change size/staging | pass/fail/n/a | Split or stacked PR need |
| Docs-only policy | pass/fail/n/a | Path listing, status, unstaged/staged diff checks |

Findings must include file path, reason, and suggested fix. After a clean middle
review pass, proceed to integration; after the clean final review pass, proceed
directly to PR delivery instead of stopping at local completion. The final
handoff for feature/behavior code changes must state the
RED evidence summary; other work states the TDD `n/a` rationale. It must also
state focused and full-suite GREEN commands, matching eval gate evidence when
in scope, the improvement performance delta summary or `n/a` rationale,
functional smoke matrix result summary, that the final fresh
bugs/correctness, security/data-safety, and performance/reliability reviewers
all reported no actionable findings, and the PR URL or PR delivery blocker. If
the loop was explicitly bypassed by user approval, state that instead.
