---
name: harness-test
description: Test and focused verification lane for MCPContentSearch changes, including failure classification and retry routing.
---

# Harness Test

## Input

Read the current plan document, `.agents/docs/harness-engineering.md`, `.agents/docs/github-workflow.md`, expected implementation scope, and surrounding tests or verification patterns.

## Work

Add or update the smallest useful verification for the changed behavior. This phase is mandatory for code-changing work before any `$subagent-review-loop` review gate.
When a work item adds a feature, the test lane must require focused tests for
the changed behavior before review. It must also require adding or updating
retained functional E2E coverage when the feature changes a user-visible or
MCP-visible workflow and executing that added or updated retained E2E coverage
before review, normally by extending the retained suite exercised by
`./scripts/verify_functional_e2e.sh`. It must also require adding or updating
eval coverage when the feature changes retrieval quality, ranking, grounding,
citation selection, answer quality, or another quality-sensitive output already
modeled by retained local evaluations, and running the matching eval command
before review. If the feature falls within retained local eval coverage but no
exact retained eval surface exists yet, it must also require extending an
existing retained eval surface and running the matching eval command before
review.

Preferred checks by change type:

- Docs-only `AGENTS.md`, `README.md`, `.agents/`, and `docs/**/*.md`: path listing, `git status --short --branch`, `git diff --check`, then stage the relevant docs-only files and run `git diff --cached --check` so new files are covered.
- Python syntax safety: `python -m compileall api core environments fetching indexing search storage main.py`.
- Unit behavior: `uv run pytest` when tests exist.
- MCP tool contracts: focused tests or smoke around `register_tools` and tool handlers.
- Search/indexing/storage: temp Chroma path, temp SQLite path, or mocked collection. Avoid user Chroma data and SQLite metadata.
- Fetching/network: mocked HTTP/API responses. Live Notion/Tistory/GitHub checks require user approval.
- End-to-end feature verification: run `./scripts/verify_functional_e2e.sh` before review for code-changing work (covers retained source sync, MCP contracts, search, citation answer, indexing, and storage paths with deterministic tests). This repo-wide regression gate is separate from the narrower requirement to add or update retained functional E2E coverage only when the feature changes a user-visible or MCP-visible workflow.
- Eval verification: use deterministic local eval checks such as
  `uv run pytest -q tests/evals` or `PYTHONPATH=. python scripts/run_contextwiki_eval.py`,
  whichever matches the already-modeled retained eval surface for the changed
  retrieval, ranking, grounding, citation-selection, or answer-quality
  behavior. If no matching retained local eval surface exists yet, extend an
  existing retained eval surface and run the matching eval command before
  review. Features outside the current retained local eval coverage are not
  subject to this eval requirement until the retained eval scope changes.

Use uv when it is available and healthy. If uv fails because local setup is broken, record the blocker and run a dependency-free fallback when useful.

After focused verification, run `.agents/skills/harness-functional-smoke/SKILL.md`
before review. The test lane must leave a smoke matrix in the plan that covers
the task-relevant inventory of retained MCP tools, source-sync and connector
fetch paths, `list_sources`/`get_sync_status` status surfaces,
`search_context`, `fetch_context`, `answer_with_citations`, script smokes, and
other retained user-visible behavior once through the safest real caller
surfaces. Include changed features, directly affected neighboring features, and
core workflows a user would expect to still work. For source sync, cover
`sync_source`, `list_sources`, and `get_sync_status` with fake/temp
dependencies where possible. If a live or user-data check is unsafe, record it
as blocked/gated with the approval needed and the nearest fake/temp substitute.

Do not proceed to review until the relevant verification and functional smoke
matrix have been run and recorded in the plan. If there are no tests yet, record
the compile/import check or another focused smoke check as the current
verification baseline.

## Failure Handling

Classify failures using `.agents/docs/harness-engineering.md`.

If local code or tests can fix the failure, update the plan and return to implementation/test. If credentials, network, local services, permissions, Python version, or dependency state block verification, report the blocker and the fallback checks run.
