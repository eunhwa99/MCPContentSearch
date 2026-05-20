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

- Docs-only `AGENTS.md`, `.agents/`, `docs/plan`: path listing, `git status --short`, `git diff --check`.
- Python syntax safety: `python -m compileall api core environments fetching indexing search main.py`.
- Unit behavior: `uv run pytest` when tests exist.
- MCP tool contracts: focused tests or smoke around `register_tools` and tool handlers.
- Search/indexing: temp Chroma path or mocked collection. Avoid user Chroma data.
- Fetching/network: mocked HTTP/API responses. Live Notion/Tistory checks require user approval.

Use uv when it is available and healthy. If uv fails because local setup is broken, record the blocker and run a dependency-free fallback when useful.

Do not proceed to review until the relevant verification has been run and recorded in the plan. If there are no tests yet, record the compile/import check or another focused smoke check as the current verification baseline.

## Failure Handling

Classify failures using `.agents/docs/harness-engineering.md`.

If local code or tests can fix the failure, update the plan and return to implementation/test. If credentials, network, local services, permissions, Python version, or dependency state block verification, report the blocker and the fallback checks run.
