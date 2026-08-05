---
name: harness-plan
description: Planning phase for context-zip harness work; converts a request into acceptance criteria, module boundaries, verification, risks, and retryable steps.
---

# Harness Plan

## Input

Run this phase only for non-exempt work. Docs/instruction-only changes and
truly trivial atomic low-risk changes skip both the plan document and this
phase under `.agents/docs/harness-engineering.md`.

Read:

- User request
- `AGENTS.md`
- `.agents/docs/harness-engineering.md`
- Current `docs/plan/...` plan document
- `.agents/docs/architecture.md`
- Minimal code or docs context needed for the work

Read `.agents/docs/github-workflow.md` when branch, commit, push, PR, or release work is involved.

## Output

The plan must include:

- Desired behavior and acceptance criteria.
- Plan document path and latest update.
- Branch preflight result: current branch, worktree state, and `main`/feature branch safety.
- Step breakdown when the work has ordered parts.
- For multi-task requests, independent task split, owner modules/files, and parallel-worker suitability.
- Likely changed files and module boundaries.
- For feature/behavior changes, unit, integration, and deterministic E2E
  coverage to add or update before production code.
- For feature/behavior changes, TDD RED evidence fields for the exact command,
  tests/layers, non-zero exit code, expected failure signature,
  missing-behavior explanation, and pre-production ordering.
- For pure refactor, test-only, or other non-behavior code work, a TDD RED
  `n/a` rationale instead of a manufactured missing-behavior failure.
- Focused GREEN and post-refactor verification commands.
- Full-suite `./scripts/verify_all.sh` command and result field.
- Matching eval gate when required by feature scope: after
  `./scripts/verify_all.sh` and before improvement after/delta and functional
  smoke; prefer recording full-suite quality-eval evidence when already
  covered, otherwise the focused matching eval command.
- Improvement performance delta fields when improvement-scoped: metric name(s),
  unit, measurement command or method, expected improvement direction, baseline
  before production edits using temporary Chroma/SQLite paths and mocked
  connectors (never inspect or mutate user data without both explicit user
  approval and a plan), one after measurement after the latest applicable gate
  (after `./scripts/verify_all.sh` and matching eval when those gates apply;
  otherwise after focused GREEN and post-refactor; do not require one remeasure
  per phase) using temporary Chroma/SQLite paths and mocked connectors; never
  inspect or mutate user data without both explicit user approval and a plan,
  and a delta table with absolute and relative deltas plus a
  one-line interpretation. Quality claims use retained eval scores as
  delta-table metrics; latency/throughput use runtime metrics and remain
  informational for quality gates. Environment notes must not include secrets,
  credentials, PII, user content, or real user-data paths. Brand-new features
  with no prior comparable surface use `n/a — no prior baseline` with
  rationale; non-improvement work uses `n/a` with a short rationale. Do not
  invent fake before numbers.
- Functional smoke matrix plan: rows to cover, caller surfaces, safe data modes,
  and approval-gated rows before review.
- Integration or additional smoke scenario when needed.
- Whether the change is docs-only.
- MCP tool contract documentation updates when tool behavior changes.
- Local ChromaDB or SQLite metadata impact, if any.
- External source connector credential or network requirements, including
  Notion, Tistory, GitHub, or embedding
  providers, if any.
- Risks, open questions, environment requirements, and rollback point.
- Architecture constraints.
- PR split or stacked PR plan if PRs are requested.
- Progress table with `Phase`, `Status`, `Summary`, and `Evidence`.

## Rules

If `docs/plan/...` does not exist for non-exempt file-changing work, stop and
create it first. Do not create a plan merely to process plan-exempt work.

Use conservative assumptions when safe and record them. Ask one short question
only when a wrong assumption could cause data loss, expose secrets, change MCP
contracts unexpectedly, or mutate user Chroma data or SQLite metadata.

Each step must be self-contained enough for a future agent to execute without hidden conversation context. Include files to read, previous outputs, explicit boundaries, and executable acceptance criteria.
