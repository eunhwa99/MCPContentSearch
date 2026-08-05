---
name: harness-integrate
description: Integration verification phase for context-zip changes, final review gate, and PR delivery.
---

# Harness Integrate

## Input

Read the current plan document when one is required, the recorded plan-exempt
reason otherwise, local diff, `.agents/docs/harness-engineering.md`,
`.agents/docs/github-workflow.md`, changed files, and prior verification
history.

## Work

Run the most valuable final verification for the change.

Docs-only:

```bash
rg --files AGENTS.md README.md docs .agents/docs .agents/skills
git status --short --branch
git diff --check
git diff --cached --check
```

Stage the relevant docs-only files before `git diff --cached --check`; new
untracked docs and plan files are not checked by the cached diff until staged.

Python code integration first validates that the recorded post-refactor
`./scripts/verify_all.sh` success matches the current HEAD/diff. Do not rerun
the expensive full suite when no code, configuration, tests, or verification
inputs changed and the evidence is current. If integration or review changes
one of those inputs, the earlier evidence is stale: return to the applicable
TDD/focused gate, then rerun the post-refactor full-suite gate, satisfy any
matching eval gate required by feature scope only after that full-suite gate
(record full-suite quality-eval evidence when already covered; otherwise run
the focused matching eval command), and refresh the evidence.

Focused unit, integration, and deterministic E2E checks must already be green.
For feature/behavior changes, integration evidence must also show the
pre-production RED command, layers/tests, non-zero exit code, expected failure
signature, and ordering. Pure refactor, test-only, or other non-behavior work
records RED as `n/a` with a rationale. Post-refactor affected checks and the
full wrapper must pass; partial fallbacks are diagnostic only. When feature
scope requires it, matching eval gate evidence must also be current after the
full-suite gate.

MCP contract changes should include a startup/import or tool-registration smoke when it can run without live credentials and without mutating user Chroma data or SQLite metadata.

Indexing/search/storage changes should avoid user data by using temp Chroma paths, temp SQLite paths, mocks, or clearly documented dry checks.

Live Notion/Tistory/GitHub validation requires both explicit user approval and
a plan. Plan-exempt work must reclassify before the live check or keep it
`blocked/gated` with a fake/temp substitute. Do not print tokens.

When improvement-scoped, refresh improvement after/delta after the latest
applicable gate and before smoke using temporary Chroma/SQLite paths and
mocked connectors; never inspect or mutate user data without both explicit
user approval and a plan. Then refresh the functional smoke matrix from
`.agents/skills/harness-functional-smoke/SKILL.md` during integration. If
refactor or integration changed any caller-visible path, rerun the affected
smoke entries. The final matrix must be present in the plan, or in the
review/final evidence for plan-exempt work, before the final review gate and
must explicitly cover retained MCP `sync_source`, `list_sources`, and
`get_sync_status` flows when source sync behavior is in scope, or mark
live/user-data checks as blocked/gated with a local substitute.

## Completion

If integration verification and the functional smoke matrix pass, run the final
three-reviewer harness gate before PR delivery. If review findings require a
behavior-changing code/config edit, return to RED before production changes,
then rerun GREEN, refactor, affected checks, the full-suite gate, any matching
eval gate required by feature scope only after that full-suite gate (record
full-suite eval evidence when already covered; otherwise rerun the matching
eval), refresh improvement after/delta when improvement-scoped using temporary
Chroma/SQLite paths and mocked connectors; never inspect or mutate user data
without both explicit user approval and a plan, and smoke. For
a non-behavior code/config/test edit, record RED as `n/a` without manufacturing
a failure, then rerun affected focused tests, the full-suite gate, any matching
eval gate required by feature scope only after that full-suite gate (record
full-suite eval evidence when already covered; otherwise rerun the matching
eval), refresh improvement after/delta when improvement-scoped using temporary
Chroma/SQLite paths and mocked connectors; never inspect or mutate user data
without both explicit user approval and a plan, and affected
smoke. For a docs-only edit, rerun lightweight docs verification without fake
RED. Refresh integration evidence, then start a fresh three-reviewer pass with
the required distinct lenses.

After the final clean three-reviewer pass, continue into PR delivery by default: stage only relevant files, commit, push the `feature/...` branch, and create a `main`-base PR using `.agents/docs/github-workflow.md`. Stop and report the blocker if the user explicitly asked for local-only work, review is unavailable, branch safety is unclear, or GitHub auth/network/permission issues prevent delivery.

Final response should include:

- Plan document path, or plan-exempt reason.
- Changed files.
- TDD RED evidence and confirmation that it predates production edits for
  feature/behavior changes, or the non-behavior/docs-only `n/a` rationale.
- Verification commands and results.
- Matching eval gate evidence when required by feature scope (recorded
  full-suite quality-eval evidence or focused matching command result).
- Improvement performance delta summary (delta table or `n/a` rationale);
  environment notes must not include secrets, credentials, PII, user content,
  or real user-data paths.
- Functional smoke matrix results, including blocked/gated checks.
- Three-reviewer harness status, including whether the newest fresh
  bugs/correctness, security/data-safety, and performance/reliability reviewers
  all reported no actionable findings.
- Skipped checks or blockers.
- Commit/push/PR status.

Do not reply on GitHub, monitor the PR, or push follow-up PR changes unless the user explicitly delegates that work.
