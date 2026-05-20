---
name: harness-integrate
description: Integration verification phase for MCPContentSearch changes and final no-commit delivery reporting.
---

# Harness Integrate

## Input

Read the current plan document, local diff, `.agents/docs/harness-engineering.md`, `.agents/docs/github-workflow.md`, changed files, and prior verification history.

## Work

Run the most valuable final verification for the change.

Docs-only:

```bash
rg --files AGENTS.md .agents/docs .agents/skills docs/plan
git status --short
git diff --check
```

Python code:

```bash
python -m compileall api core environments fetching indexing search main.py
uv run pytest
```

MCP contract changes should include a startup/import or tool-registration smoke when it can run without live credentials and without mutating user Chroma data.

Indexing/search changes should avoid user data by using temp Chroma paths, mocks, or clearly documented dry checks.

Live Notion/Tistory validation requires user approval. Do not print tokens.

## Completion

If integration verification passes, run the final `$subagent-review-loop` review gate before final response. If review findings require edits, rerun the affected verification and then start a fresh subagent review pass.

Final response should include:

- Plan document path.
- Changed files.
- Verification commands and results.
- `$subagent-review-loop` status, including whether the final fresh reviewer reported no actionable findings.
- Skipped checks or blockers.
- Commit/push/PR status.

Do not commit, push, or create PRs unless the user explicitly asks.
