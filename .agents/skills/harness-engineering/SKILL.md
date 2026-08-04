---
name: harness-engineering
description: Orchestrates context-zip harness work for implementation, fixes, refactors, tests, docs, planning, review gates, retry loops, and PR delivery.
---

# Harness Engineering

## Reference Docs

Read `.agents/docs/harness-engineering.md` and
`.agents/docs/github-workflow.md` first. For file-changing work, do not edit
target files until branch preflight is complete and the plan decision is
recorded. Docs/instruction-only changes and truly trivial atomic low-risk
changes are plan-exempt under the harness criteria; skip both the plan document
and `harness-plan` for those tasks. A docs/instruction change that authorizes or
initiates live API access, user-data/destructive action, or a substantive
runtime security/public-contract/architecture change must be reclassified as
non-exempt; process/testing/review documentation alone remains exempt.

During planning, read `.agents/docs/architecture.md`.

## Phases

Run phases and gates in this order. The main agent is the harness
orchestrator: it owns plan creation/updates, worker persona design, delegation,
result collection, synthesis, conflict resolution, review routing, and final
delivery.

0. Branch preflight: when the worktree is clean, update local `main` from
   `origin/main`, clean only safe local non-`main` work branches using
   `.agents/docs/github-workflow.md` safeguards, create a fresh `feature/...`
   task branch, and record worktree safety. Preserve local-only commits and
   linked-worktree branches; discarding/removing them requires both a plan and
   explicit user approval.
1. Plan decision: for non-exempt work, create or update
   `docs/plan/YYYY-MM-DD-short-task-name.md` and run
   `.agents/skills/harness-plan/SKILL.md`; for plan-exempt work, record the
   reason without creating a plan.
2. Worker orchestration: define task-specific implementation, testing,
   documentation, or integration subagent personas with bounded file ownership,
   acceptance criteria, non-goals, and verification expectations. Use
   `.agents/skills/harness-multitask/SKILL.md` when work needs decomposition.
   For improvement-scoped work, declare metrics early in the plan or
   plan-exempt evidence (name, unit, command/method, expected direction).
3. Improvement performance baseline: when work improves or claims to improve an
   existing measurable capability, capture declared baselines before
   production/code edits (alongside or just before TDD red) using temporary
   Chroma/SQLite paths and mocked connectors; never inspect or mutate user data
   without both explicit user approval and a plan. Otherwise record `n/a` with
   rationale; brand-new features with no prior comparable surface use
   `n/a — no prior baseline`.
4. TDD red gate: for feature/behavior changes, use
   `.agents/skills/harness-test/SKILL.md` to add or update unit, integration,
   and deterministic E2E coverage before production code, then record the
   auditable expected focused failure. Pure refactor, test-only, or other
   non-behavior work records RED as `n/a` with a rationale.
5. TDD green implementation: `.agents/skills/harness-implement/SKILL.md`,
   delegated when tool policy and ownership boundaries allow.
6. TDD green verification: use `.agents/skills/harness-test/SKILL.md` to pass
   focused unit, integration, and E2E checks.
7. TDD refactor phase: `.agents/skills/harness-refactor/SKILL.md`; refactor only
   while focused tests remain green.
8. Post-refactor full-suite gate: rerun affected focused tests, then require
   `./scripts/verify_all.sh` to pass.
9. Eval gate when required by feature scope: after `./scripts/verify_all.sh`
   succeeds, confirm the matching retained eval surface. Prefer recording the
   full-suite deterministic quality eval layer evidence when that layer already
   executed the matching surface; otherwise run the focused matching eval
   command. Never run matching eval during focused GREEN.
10. Improvement performance after/delta: when improvement-scoped, take one
    after measurement after the latest applicable gate — after
    `./scripts/verify_all.sh` and matching eval when those gates apply;
    otherwise after focused GREEN and post-refactor — using temporary
    Chroma/SQLite paths and mocked connectors; never inspect or mutate user
    data without both explicit user approval and a plan. Do not require one
    remeasure per phase. Record the delta table. Keep earlier `n/a` rationales
    otherwise. See Improvement Performance Delta in
    `.agents/docs/harness-engineering.md`.
11. Functional smoke gate: `.agents/skills/harness-functional-smoke/SKILL.md`,
   covering the task-relevant feature inventory once through the safest real
   caller surfaces before review, not only the files changed.
12. Middle review gate: `.agents/skills/harness-review/SKILL.md`, running the
   three-reviewer harness loop.
13. Integration phase: `.agents/skills/harness-integrate/SKILL.md`
14. Final review gate: `.agents/skills/harness-review/SKILL.md`, running the
   three-reviewer harness loop.
15. PR delivery: after the final clean three-reviewer pass, stage only relevant files, commit, push, and create a `main`-base PR by default unless the user explicitly asks for local-only work or a safety blocker prevents delivery. Include the improvement performance delta summary or `n/a` rationale in the PR body and final handoff.

Docs/instruction-only work skips the code-specific TDD red, green
implementation, full-suite, and functional-smoke gates and uses docs-only
verification instead. Record improvement performance delta as `n/a` with a
docs-only rationale. A trivial plan-exempt code change still runs every TDD
and full-suite gate.

All feature and behavior changes use strict Red-Green-Refactor TDD. The red gate
must happen before production code; unit, integration, and deterministic
functional E2E coverage are all required. Before production edits, record the
exact RED command, layers/tests, non-zero exit code, expected failure signature,
and missing-behavior explanation. After focused tests turn green, refactor
while they remain green, rerun affected tests, then run
`./scripts/verify_all.sh` and require it to pass before review or delivery. An
environment or dependency failure blocks completion rather than permitting a
partial fallback. If the feature changes retrieval quality,
ranking, grounding, citation selection, answer quality, or another
quality-sensitive output already modeled by retained local evaluations, also
require adding or updating eval coverage and satisfying the matching eval gate
only after `./scripts/verify_all.sh` succeeds and before improvement after/delta
(or an explicit `n/a` rationale), functional smoke, or review; never during
focused GREEN. Prefer recording full-suite deterministic
quality eval layer evidence when that layer already executed the matching
surface; otherwise run the focused matching eval command. If the feature falls
within retained local eval coverage but no exact retained eval surface exists
yet, also require extending an existing retained eval surface and satisfying
the eval gate only after that full-suite gate.

## Loop Rules

Treat implementation, testing, review, refactor, integration, and final review as a retryable loop. Every new task must start from an updated `main` and a fresh `feature/...` branch after safe local non-`main` branch cleanup, unless the user explicitly asks to continue an existing branch. If the worktree is dirty, fetch `origin/main` when network is available, create an isolated worktree with a fresh `feature/...` branch from `origin/main`, and ask or report a blocker before switching, pulling, or deleting branches in the dirty worktree.

Minimize human intervention by routing routine work through subagents when delegation is available. The main agent should inspect or update the plan when one is required, define worker personas, delegate bounded implementation/test/docs/integration tasks, collect outputs, inspect the resulting diff, and synthesize the integrated result. Ask the user only for unsafe ambiguity, credentials, destructive operations, local data mutation, unavailable delegation/review tools, or external approval.

If the main-agent synthesis or harness review produces an actionable
behavior-changing code/config finding, return to the unit/integration/E2E RED
gate before production edits, record fresh auditable RED evidence, then rerun
GREEN, refactor, affected tests, `./scripts/verify_all.sh`, any matching eval
required by feature scope only after that full-suite gate (record full-suite
eval evidence when already covered; otherwise rerun the matching eval), refresh
improvement after/delta when improvement-scoped using temporary Chroma/SQLite
paths and mocked connectors; never inspect or mutate user data without both
explicit user approval and a plan, and functional smoke. For a
non-behavior code/config/test finding, record RED as `n/a` without
manufacturing a failure, then rerun affected focused tests,
`./scripts/verify_all.sh`, any matching eval required by feature scope only
after that full-suite gate (record full-suite eval evidence when already
covered; otherwise rerun the matching eval), refresh improvement after/delta
when improvement-scoped using temporary Chroma/SQLite paths and mocked
connectors; never inspect or mutate user data without both explicit user
approval and a plan, and affected smoke. For a docs-only finding, rerun
lightweight docs verification without fake RED. Update the plan when one is
required, assign each issue back to the responsible worker persona or a fresh
replacement with the same ownership boundary, and continue directly to the next
fresh review pass rather than restarting the already-completed initial RED
phase.
Every review pass must run relevant verification, any matching eval gate
required by feature scope only after `./scripts/verify_all.sh` (prefer
recording full-suite quality-eval evidence when already covered), improvement
after/delta when improvement-scoped, and functional smoke first, then spawn
exactly three fresh read-only reviewers with distinct primary lenses:
bugs/correctness/contracts/tests; security/privacy/data safety/secrets; and
performance/reliability/async/concurrency/operability, including
improvement-scoped before/after/delta coherence. Continue until all three in
the newest pass report no actionable findings. For code changes, review-fix
verification must include affected unit/integration/E2E tests and
`./scripts/verify_all.sh`, plus any matching eval gate only after that
full-suite gate (prefer recording full-suite eval-layer evidence when it
already covered the matching surface); then refresh after/delta measurements
when improvement-scoped using temporary Chroma/SQLite paths and mocked
connectors (never inspect or mutate user data without both explicit user
approval and a plan) before the next smoke/review pass. Worker subagents may
edit only within delegated boundaries; reviewer subagents must not edit files.

Use review lenses from `.agents/docs/harness-engineering.md`: MCP contract, indexing/vector-store/storage including SQLite lifecycle/tombstone metadata, fetching/network for external connectors, async/background, config/secrets, test-quality, functional-smoke quality, improvement-performance-delta, and docs-only.

If the three-reviewer harness loop cannot run because subagent review is
unavailable or unauthorized, stop and report the blocker instead of silently
using self-review. Do not respond on GitHub, watch PRs, or push follow-up PR
changes unless the user explicitly delegates that work. For file-changing
harness work, the repository standing workflow is to commit, push, and create a
PR after the final clean three-reviewer pass unless the user explicitly asks
for local-only work.

Remember that `./scripts/verify_functional_e2e.sh` still runs for code-changing
work as the repo-wide functional E2E regression gate. Every feature or behavior
change must also add or update retained deterministic E2E coverage for its own
behavior before production code.

Use the current local eval surfaces when they apply, including deterministic
eval tests under `tests/evals` such as `uv run pytest -q tests/evals` and
`PYTHONPATH=. python scripts/run_context_zip_eval.py`. Satisfy the matching
eval gate only after `./scripts/verify_all.sh` succeeds and before improvement
after/delta (or an explicit `n/a` rationale), functional smoke, or review; do
not run matching eval during focused GREEN. Prefer
recording the full-suite deterministic quality eval layer evidence when that
layer already executed the matching surface; otherwise run the focused matching
eval command. If a feature falls within the repo's retained local eval coverage
but no matching retained eval surface exists yet, extend an existing retained
eval surface such as `tests/evals` or the fixture runner during coverage work
and satisfy the eval gate only after the full-suite gate. Features outside the
current retained local eval coverage are not subject to this eval requirement
until the retained eval scope changes.
