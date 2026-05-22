---
name: harness-integrate
description: Integration verification phase for MCPContentSearch changes, final review gate, and PR delivery.
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

If integration verification passes, run the final `$subagent-review-loop` review gate before PR delivery. If review findings require edits, rerun the affected verification and then start a fresh five-reviewer subagent review pass.

After the final clean `$subagent-review-loop` pass, continue into PR delivery by default: stage only relevant files, commit, push the `feature/...` branch, and create a `main`-base PR using `.agents/docs/github-workflow.md`. Stop and report the blocker if the user explicitly asked for local-only work, review is unavailable, branch safety is unclear, or GitHub auth/network/permission issues prevent delivery.

Final response should include:

- Plan document path.
- Changed files.
- Verification commands and results.
- `$subagent-review-loop` status, including whether all five reviewers in the final fresh pass reported no actionable findings.
- Skipped checks or blockers.
- Commit/push/PR status.

Do not reply on GitHub, monitor the PR, or push follow-up PR changes unless the user explicitly delegates that work.
