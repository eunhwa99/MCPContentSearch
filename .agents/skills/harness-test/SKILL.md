---
name: harness-test
description: Test and focused verification lane for MCPContentSearch changes, including failure classification and retry routing.
---

# Harness Test

## Input

Read the current plan document when one is required, the recorded plan-exempt
reason otherwise, `.agents/docs/harness-engineering.md`,
`.agents/docs/github-workflow.md`, expected implementation scope, and
surrounding tests or verification patterns.

## Work

This phase is mandatory twice for feature and behavior changes: once before
production code for the TDD red gate, and again after implementation for the
focused green gate. The post-refactor full-suite gate is a separate orchestrator
phase.

### Red Gate

Before production code changes:

1. Add or update unit coverage for the smallest behavior boundary.
2. Add or update integration coverage across the affected module/service or MCP
   boundary.
3. Add or update deterministic functional E2E coverage through the safest
   retained caller surface, normally the suite exercised by
   `./scripts/verify_functional_e2e.sh`.
4. Run the smallest relevant new or changed test and confirm it fails for the
   expected missing behavior. A fixture, dependency, syntax, or environment
   failure does not satisfy Red.
5. Before production edits, record the exact command, covered
   unit/integration/E2E test layers or names, non-zero exit code, expected
   failure signature, missing-behavior explanation, and timestamp/order evidence
   in the plan or plan-exempt task evidence.

### Focused Green Gate

After production code changes, run the focused unit, integration, and E2E
checks and require them all to pass. Stop this test-lane invocation at focused
GREEN so the next phase can refactor. The orchestrator reruns affected focused
tests after refactor and owns the mandatory `./scripts/verify_all.sh` execution
for the current diff before smoke or review.

Do not run matching eval commands in this focused GREEN lane. When a feature
changes retrieval quality, ranking, grounding, citation selection, answer
quality, or another quality-sensitive output already modeled by retained local
evaluations, add or update eval coverage as part of the red/coverage work, but
leave the eval gate to the orchestrator: after `./scripts/verify_all.sh`
succeeds and before functional smoke or review, satisfy the matching eval gate
by recording full-suite deterministic quality eval layer evidence when that
layer already covered the matching surface, otherwise running the focused
matching eval command. If the feature falls within retained local eval coverage
but no exact retained eval surface exists yet, extend an existing retained eval
surface during coverage work; the orchestrator still satisfies that matching
eval gate only after the full-suite gate.

Preferred checks by change type:

- Docs-only `AGENTS.md`, `README.md`, `.agents/`, and `docs/**/*.md`: path listing, `git status --short --branch`, `git diff --check`, then stage the relevant docs-only files and run `git diff --cached --check` so new files are covered.
- Python syntax safety: `python -m compileall api core environments fetching indexing search storage main.py`.
- Unit and integration behavior: run the focused new/changed tests first.
- MCP tool contracts: focused tests or smoke around `register_tools` and tool handlers.
- Search/indexing/storage: temp Chroma path, temp SQLite path, or mocked collection. Avoid user Chroma data and SQLite metadata.
- Fetching/network: mocked HTTP/API responses. Live
  Notion/Tistory/GitHub checks require both explicit user approval and a plan;
  plan-exempt work must reclassify or keep them `blocked/gated`.
- End-to-end feature verification: add or update retained deterministic E2E
  coverage for every feature or behavior change, then run
  `./scripts/verify_functional_e2e.sh`.
- Full repository verification: the separate post-refactor orchestrator gate
  owns `./scripts/verify_all.sh` for the current diff and requires it to pass
  for every code-changing work item.
- Eval verification: after successful `./scripts/verify_all.sh`, the
  orchestrator confirms the matching retained eval surface for the changed
  retrieval, ranking, grounding, citation-selection, or answer-quality
  behavior. Prefer recording the full-suite deterministic quality eval layer
  evidence when `./scripts/verify_all.sh` already executed that matching
  surface; otherwise run the focused matching command such as
  `uv run pytest -q tests/evals` or
  `PYTHONPATH=. python scripts/run_contextwiki_eval.py`. If no matching
  retained local eval surface exists yet, extend an existing retained eval
  surface during coverage work and satisfy the eval gate only after the
  full-suite gate, before smoke or review. Features outside the current
  retained local eval coverage are not subject to this eval requirement until
  the retained eval scope changes.

Use uv when it is available and healthy. If uv or the full wrapper fails
because local setup is broken, record the blocker and run a dependency-free
fallback only for diagnosis; do not mark the test gate complete.

After the orchestrator completes refactoring, affected focused-test reruns, a
successful `./scripts/verify_all.sh`, and any matching eval required by feature
scope (only after that full-suite gate), run
`.agents/skills/harness-functional-smoke/SKILL.md` before review. The test lane
must leave a smoke matrix in the plan, or in the review/final evidence for
plan-exempt work, that covers
the task-relevant inventory of retained MCP tools, source-sync and connector
fetch paths, `list_sources`/`get_sync_status` status surfaces, `sync_all`,
`search_context`, `search_documents`, `fetch_context`, retained answer
coverage, script smokes, and other retained user-visible behavior once through
the safest real caller surfaces. Include changed features, directly affected
neighboring features, and core workflows a user would expect to still work. For source sync, cover
`sync_source`, `list_sources`, and `get_sync_status` with fake/temp
dependencies where possible. Live external, LLM, or embedding-provider checks,
real-vault or private-user-data access, and destructive checks are blocked
unless the task has a plan and the user explicitly approves the bounded action.
Plan-exempt work must be reclassified as planned work or record the check as
blocked/gated with the nearest fake/temp substitute.

The test lane hands focused GREEN evidence to the refactor and post-refactor
full-suite phases. The overall harness must not proceed to review until
feature/behavior work has auditable pre-production RED evidence, focused
unit/integration/E2E GREEN results, post-refactor affected-test results,
`./scripts/verify_all.sh`, any matching eval gate required by feature scope
(only after that full-suite gate; prefer recording full-suite quality-eval
evidence when already covered), and the functional smoke matrix recorded in
the plan or plan-exempt evidence. Pure refactor, test-only, or other
non-behavior code work records the RED gate as `n/a` with a rationale and still
supplies applicable focused GREEN, full-suite, matching-eval when in scope, and
smoke evidence. If required coverage does not exist yet, add it; a
compile/import check alone is not a completion baseline for behavior-changing
code work.

## Failure Handling

Classify failures using `.agents/docs/harness-engineering.md`.

If local code or tests can fix the failure, update the plan when one is
required and return to implementation/test. If credentials, network, local
services, permissions, Python version, or dependency state block verification,
report the blocker and the diagnostic fallback checks run.
