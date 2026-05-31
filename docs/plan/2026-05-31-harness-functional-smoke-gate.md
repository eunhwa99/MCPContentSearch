# Harness Functional Smoke Gate Plan

## Goal

Update the MCPContentSearch harness so implementation work cannot finish after unit/API checks alone. After implementation and before review/PR delivery, the harness must run a functional smoke pass that exercises each relevant user-visible/MCP/Web Console feature once, especially source sync paths.

## Branch Preflight

- Original checkout `/Users/eunhwa/IdeaProjects/MCPContentSearch` was dirty with `.idea` files.
- Preserved the dirty checkout and created isolated worktree `/private/tmp/MCPContentSearch-harness-functional-smoke` from `origin/main`.
- Branch: `feature/harness-functional-smoke-gate`.

## Scope

- Add or update harness instructions under `.agents/` and `AGENTS.md`.
- Add a reusable functional smoke matrix/checklist if useful.
- Keep changes instruction/docs-only; do not change runtime code or inspect user Chroma/SQLite data.

## Non-Goals

- Do not fix the current sync bug in this task.
- Do not run live GitHub/Notion/Tistory/Web sync against user data without approval.
- Do not modify secrets, local Chroma, SQLite metadata, or `.env`.

## Acceptance Criteria

- Harness requires a post-implementation functional smoke gate before `$subagent-review-loop`.
- The smoke gate starts from the task-relevant feature inventory, not only changed files, and exercises each relevant feature once through its real caller surface when safe: MCP tool, Web Console browser UI, script smoke, or local fake/temp harness.
- Source sync coverage explicitly includes configured-source Sync and ad hoc/target sync distinctions.
- Unsafe live checks must be listed as `blocked/gated` with the reason, approval needed, and nearest fake/temp substitute.
- Plans, review prompts, and PR reports must include the smoke matrix results.

## Expected Files

- `AGENTS.md`
- `.agents/docs/harness-engineering.md`
- `.agents/skills/harness-engineering/SKILL.md`
- `.agents/docs/github-workflow.md`
- `.agents/skills/harness-test/SKILL.md`
- `.agents/skills/harness-implement/SKILL.md`
- `.agents/skills/harness-integrate/SKILL.md`
- `.agents/skills/harness-review/SKILL.md`
- `.agents/skills/harness-review/agents/openai.yaml`
- new `.agents/skills/harness-functional-smoke/SKILL.md`
- optional `.agents/skills/harness-functional-smoke/agents/openai.yaml`
- optional `.agents/docs/functional-smoke-matrix.md`
- this plan under `docs/plan/`

## Verification Plan

Docs/instruction-only verification:

```bash
rg --files AGENTS.md README.md docs .agents/docs .agents/skills
rg -n "functional smoke|smoke matrix|harness-functional-smoke|configured-source|target sync" AGENTS.md .agents docs/plan

git status --short --branch
git diff --check
# after staging relevant docs/instruction files
git diff --cached --check
```

No live API, browser, Chroma, or SQLite verification is planned because this task changes harness instructions only.

## Functional Smoke Matrix

| Feature | Caller Surface | Data Mode | Expected Result | Action/Command | Result | Evidence | Blocker / Substitute |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Harness phase order and retry loop | Docs/skill review | Local docs only | Functional smoke runs before review and affected smoke entries rerun after review fixes | Read/grep `AGENTS.md`, `.agents/docs/harness-engineering.md`, and harness skills | passed | Review findings fixed in `AGENTS.md`, `harness-review`, `harness-engineering`, `harness-implement`, `harness-plan`, `harness-integrate`, and `github-workflow` | n/a |
| Review prompt evidence | Docs/skill review | Local docs only | Reviewers receive verification plus functional smoke matrix/results from the plan | Read/grep `.agents/skills/harness-review/SKILL.md` and `.agents/skills/harness-review/agents/openai.yaml` | passed | `harness-review` now requires pre-review matrix, reviewer prompt context, reruns, checklist row, handoff summary, and reviewer agent prompt coverage | n/a |
| Plan and PR evidence | Docs/skill review | Local docs only | Plans contain matrix rows before review; PR/final reports summarize or link matrix results | Read/grep `docs/plan/README.md`, `harness-plan`, `harness-functional-smoke`, `harness-engineering`, `harness-integrate`, `.agents/docs/github-workflow.md` | passed | Plan README, skills, final report docs, and GitHub workflow require matrix planning/results and PR evidence | n/a |
| Source sync distinction | Docs/skill review | Local docs only | Configured-source sync and target/ad hoc sync are separate required smoke rows when in scope | Read/grep `AGENTS.md`, `.agents/docs/functional-smoke-matrix.md`, `harness-functional-smoke` | passed | Matrix distinguishes `sync_source(source_id)` from target/ad hoc sync paths | n/a |
| Live sync or user-data smoke | Live external/API/user storage | User approval and temp/user-data plan required | Unsafe live checks are not run for this docs-only change | No live sync executed | blocked/gated | Docs-only verification uses local files; live checks require explicit approval in the harness | Local substitute: docs/skill validation and diff checks |

## Architecture/ADR Constraints

- ADR 0001: keep module boundaries clear; this change is process documentation only.
- ADR 0002: required verification should use fake/temp persistence and avoid user data.
- ADR 0004: live connector checks require explicit approval and bounded temporary/source-safe setup.

## Risks and Rollback

- Risk: requiring “all features” too literally could force unsafe live sync. Mitigation: require feature matrix with safe fake/temp substitutes and explicit `blocked/gated` rows.
- Risk: agents ignore the new gate if only one doc is updated. Mitigation: wire it into AGENTS, harness docs, orchestrator skill, test skill, and integrate skill.
- Rollback: revert this docs/skill-only commit.

## Progress Log

| Phase | Status | Evidence |
| --- | --- | --- |
| Branch preflight | completed | isolated worktree from `origin/main`, branch `feature/harness-functional-smoke-gate` |
| Plan | completed | `docs/plan/2026-05-31-harness-functional-smoke-gate.md` |
| Baseline gap | completed | Explorer baseline confirmed current harness had affected-feature checks but no full functional smoke matrix gate and no `harness-functional-smoke` skill. |
| Worker orchestration | completed | Spawned baseline explorer `019e7d6a-ac8d-7862-821b-becc8b4b7abd` and docs/skill worker `019e7d6a-af23-70a1-aa3b-a69e4d39ff40`. |
| Implementation | completed | Added `harness-functional-smoke` skill, functional smoke matrix doc, and wired the gate into AGENTS plus harness engineering/test/integrate/review/plan docs. |
| Verification | completed | `python /Users/eunhwa/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/harness-functional-smoke` -> valid; `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 1 | completed | Fresh five-reviewer pass found stale review-gate, retry-loop, plan-evidence, implement-handoff, and result-vocabulary gaps. |
| Review pass 1 remediation | completed | Updated `harness-review`, `AGENTS.md`, `harness-engineering`, `harness-functional-smoke`, matrix docs, plan README, `harness-plan`, `harness-implement`, and `harness-integrate`; affected functional smoke matrix rows are recorded above. |
| Post-remediation verification | completed | `python /Users/eunhwa/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/harness-functional-smoke` -> valid; `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; stale-pattern `rg`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 2 | completed | Fresh five-reviewer pass had three clean reviews and two actionable findings: stale GitHub delivery policy and stale `harness-review` reviewer prompt. |
| Review pass 2 remediation | completed | Updated `.agents/docs/github-workflow.md` commit/PR evidence policy and `.agents/skills/harness-review/agents/openai.yaml`; affected functional smoke matrix rows are refreshed above. |
| Post-pass-2 remediation verification | completed | `python /Users/eunhwa/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/harness-functional-smoke` -> valid; `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; targeted `rg` for functional-smoke evidence; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 3 | completed | Fresh five-reviewer pass had two clean reviews, one non-blocking note, and three actionable findings: missing functional-smoke review lens, stale implement delivery sentence, and missing post-pass-2 verification evidence. |
| Review pass 3 remediation | completed | Added functional-smoke review lenses to harness engineering docs/skill, updated implement delivery precondition, and recorded post-pass-2 verification evidence; affected functional smoke matrix rows are refreshed above. |
| Post-pass-3 remediation verification | completed | `python /Users/eunhwa/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/harness-functional-smoke` -> valid; `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; targeted `rg` for functional-smoke evidence; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 4 | completed | Fresh five-reviewer pass found one repeated actionable finding: post-pass-3 verification evidence had not been recorded before the pass. |
| Review pass 4 remediation | completed | Added the missing post-pass-3 remediation verification row; affected functional smoke matrix rows remain refreshed above. |
| Post-pass-4 remediation verification | completed | `python /Users/eunhwa/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/harness-functional-smoke` -> valid; `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; targeted `rg` for functional-smoke evidence; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 5 | completed | Fresh five-reviewer pass found the same process-log issue: post-pass-4 verification evidence had not been recorded before the pass. |
| Review pass 5 remediation | completed | Added the missing post-pass-4 remediation verification row and this current pre-review evidence trail; affected functional smoke matrix rows remain refreshed above. |
| Current pre-review verification | completed | `python /Users/eunhwa/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/harness-functional-smoke` -> valid; `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; targeted `rg` for latest functional-smoke and review-evidence wiring; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review gate | completed | Fresh five-reviewer pass 6 reported no actionable findings from all five reviewers. |
| PR delivery | pending | Pending |
