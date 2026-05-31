---
name: harness-test
description: Test and focused verification lane for MCPContentSearch changes, including failure classification and retry routing.
---

# Harness Test

## Input

Read the current plan document, `.agents/docs/harness-engineering.md`, `.agents/docs/github-workflow.md`, expected implementation scope, and surrounding tests or verification patterns.

## Work

Add or update the smallest useful verification for the changed behavior. This phase is mandatory for code-changing work before any `$subagent-review-loop` review gate.

Preferred checks by change type:

- Docs-only `AGENTS.md`, `README.md`, `.agents/`, and `docs/**/*.md`: path listing, `git status --short --branch`, `git diff --check`, then stage the relevant docs-only files and run `git diff --cached --check` so new files are covered.
- Python syntax safety: `python -m compileall api core environments fetching indexing search storage wiki main.py`.
- Unit behavior: `uv run pytest` when tests exist.
- MCP tool contracts: focused tests or smoke around `register_tools` and tool handlers.
- Search/indexing/storage: temp Chroma path, temp SQLite path, or mocked collection. Avoid user Chroma data and SQLite metadata.
- Fetching/network: mocked HTTP/API responses. Live Notion/Tistory/GitHub/Web checks require user approval.

Use uv when it is available and healthy. If uv fails because local setup is broken, record the blocker and run a dependency-free fallback when useful.

After focused verification, run `.agents/skills/harness-functional-smoke/SKILL.md`
before review. The test lane must leave a smoke matrix in the plan that covers
the task-relevant inventory of MCP tools, Web Console workflows, source-sync
paths, script smokes, status surfaces, downloads, and other user-visible
behavior once through the safest real caller surfaces. Include changed features,
directly affected neighboring features, and core workflows a user would expect
to still work. For source sync, distinguish configured-source sync from target
or ad hoc sync. If a live or user-data check is unsafe, record it as
blocked/gated with the approval needed and the nearest fake/temp substitute.

Do not proceed to review until the relevant verification and functional smoke
matrix have been run and recorded in the plan. If there are no tests yet, record
the compile/import check or another focused smoke check as the current
verification baseline.

## Failure Handling

Classify failures using `.agents/docs/harness-engineering.md`.

If local code or tests can fix the failure, update the plan and return to implementation/test. If credentials, network, local services, permissions, Python version, or dependency state block verification, report the blocker and the fallback checks run.
