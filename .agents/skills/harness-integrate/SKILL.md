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
rg --files AGENTS.md README.md docs .agents/docs .agents/skills
git status --short --branch
git diff --check
git diff --cached --check
```

Stage the relevant docs-only files before `git diff --cached --check`; new
untracked docs and plan files are not checked by the cached diff until staged.

Python code:

```bash
python -m compileall api core environments fetching indexing search storage wiki main.py
uv run pytest
```

MCP contract changes should include a startup/import or tool-registration smoke when it can run without live credentials and without mutating user Chroma data or SQLite metadata.

Indexing/search/storage changes should avoid user data by using temp Chroma paths, temp SQLite paths, mocks, or clearly documented dry checks.

Live Notion/Tistory/GitHub/Web validation requires user approval. Do not print tokens.

Refresh the functional smoke matrix from
`.agents/skills/harness-functional-smoke/SKILL.md` during integration. If
refactor or integration changed any caller-visible path, rerun the affected
smoke entries. The final matrix must be present in the plan before the final
review gate and must explicitly cover configured-source sync and target/ad hoc
sync when source sync behavior is in scope, or mark live/user-data checks as
blocked/gated with a local substitute.

## Completion

If integration verification and the functional smoke matrix pass, run the final
`$subagent-review-loop` review gate before PR delivery. If review findings
require edits, rerun the affected verification and affected smoke entries, then
start a fresh five-reviewer subagent review pass.

After the final clean `$subagent-review-loop` pass, continue into PR delivery by default: stage only relevant files, commit, push the `feature/...` branch, and create a `main`-base PR using `.agents/docs/github-workflow.md`. Stop and report the blocker if the user explicitly asked for local-only work, review is unavailable, branch safety is unclear, or GitHub auth/network/permission issues prevent delivery.

Final response should include:

- Plan document path.
- Changed files.
- Verification commands and results.
- Functional smoke matrix results, including blocked/gated checks.
- `$subagent-review-loop` status, including whether all five reviewers in the final fresh pass reported no actionable findings.
- Skipped checks or blockers.
- Commit/push/PR status.

Do not reply on GitHub, monitor the PR, or push follow-up PR changes unless the user explicitly delegates that work.
