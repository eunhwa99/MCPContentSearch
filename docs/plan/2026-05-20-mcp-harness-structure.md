# MCP Harness Structure

## User Request

Apply the strict `be-bang9` harness-related structure to `MCPContentSearch`, adapting it to this Python MCP content search project.

## Branch Preflight Result

- Starting repository: `/Users/eunhwa/IdeaProjects/MCPContentSearch`
- Initial branch: `main`
- Initial worktree state: clean
- Working branch created: `feature/mcp-harness-structure`
- Remote freshness was not checked because network access is restricted in this session.

## Scope and Non-goals

In scope:

- Add root repository instructions.
- Add harness docs under `.agents/docs`.
- Add phase skills under `.agents/skills`.
- Add ADR index/template and an accepted architecture ADR.
- Add `docs/plan` plan-document contract and this plan.
- Tailor branch, verification, review, and architecture guidance for Python/FastMCP/LlamaIndex/ChromaDB.

Non-goals:

- Do not change runtime Python code.
- Do not add tests, dependencies, formatters, CI, or GitHub Actions.
- Do not inspect secrets or local ChromaDB data.
- Do not commit or push unless the user explicitly asks.

## Acceptance Criteria

- `AGENTS.md` tells agents to use the local harness before file-changing work.
- `.agents/docs/harness-engineering.md` defines phase and gate order for this repo.
- `.agents/docs/github-workflow.md` uses `main` as the base branch and preserves no-commit/no-push defaults.
- `.agents/docs/architecture.md` reflects the actual MCPContentSearch module boundaries and data flow.
- `.agents/docs/adr/README.md`, `template.md`, and at least one accepted ADR exist.
- `.agents/skills/harness-*` exists for plan, multitask, implement, test, review, refactor, and integrate phases.
- `docs/plan/README.md` defines the plan-document contract.
- Code-changing work runs relevant tests or verification before review.
- `$subagent-review-loop` is explicitly required after verification and before final handoff, repeating fresh reviews until no actionable findings remain.
- Lightweight docs verification passes or any limitation is reported.

## Step Breakdown

| Step | Boundary | Acceptance Criteria |
| --- | --- | --- |
| `plan-document` | Create `docs/plan` contract and this plan. | Plan exists before non-plan target docs are edited. |
| `harness-docs` | Add root instructions, architecture, ADR, and GitHub workflow docs. | Docs reference Python/MCP commands and current module paths. |
| `phase-skills` | Add phase skill files and metadata. | Skill names and paths match `AGENTS.md` and harness docs. |
| `verification` | Run docs-only checks. | Path listing, `git status --short`, and `git diff --check` are captured. |

## Files Likely to Change

- `AGENTS.md`
- `.agents/docs/harness-engineering.md`
- `.agents/docs/github-workflow.md`
- `.agents/docs/architecture.md`
- `.agents/docs/adr/README.md`
- `.agents/docs/adr/template.md`
- `.agents/docs/adr/0001-layered-mcp-content-search-architecture.md`
- `.agents/skills/harness-*/SKILL.md`
- `.agents/skills/harness-*/agents/openai.yaml`
- `docs/plan/README.md`
- `docs/plan/2026-05-20-mcp-harness-structure.md`

## Test and Verification Plan

Docs-only verification:

```bash
rg --files AGENTS.md .agents/docs .agents/skills docs/plan
git status --short
git diff --check
```

No Python compile, pytest, or MCP smoke is required because runtime code is not changing.

## Architecture/ADR Constraints

- Preserve the existing module boundaries: `api`, `core`, `environments`, `fetching`, `indexing`, and `search`.
- Treat MCP tool response contract, local ChromaDB data, and external API credentials as review-sensitive surfaces.
- The new ADR must describe current architecture rather than forcing runtime refactors.

## Risks and Rollback Notes

- Risk: importing `be-bang9` wording too literally could leave Spring/Gradle/Bruno assumptions in this Python repo.
- Mitigation: adapt all commands, branch base, review lenses, and architecture references to MCPContentSearch.
- Rollback: remove `AGENTS.md`, `.agents/`, and `docs/plan/` additions from this branch.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created `feature/mcp-harness-structure` from clean `main`. | `git status --short`, `git branch --show-current` |
| Plan document | completed | Added this plan before non-plan target docs. | `docs/plan/2026-05-20-mcp-harness-structure.md` |
| Harness docs | completed | Added Python/MCP-tailored harness docs, ADR, phase skills, and root instructions. | `AGENTS.md`, `.agents/docs`, `.agents/skills`, `docs/plan` |
| Verification | completed | Docs-only checks passed; stale source-project wording scan only found intentional risk/context mentions. | `rg --files AGENTS.md .agents/docs .agents/skills docs/plan`, `git status --short --branch`, `git diff --check`, `rg -n "be-bang9|Spring|Gradle|Bruno|Flyway|PostgreSQL|Redis|Bang9|Java 21|JUnit|spotless" AGENTS.md .agents docs/plan` |
| Review-loop reinforcement | completed | Added explicit test-before-review and `$subagent-review-loop` requirements to root instructions, harness docs, review/test/integrate skills, and skill metadata. | `AGENTS.md`, `.agents/docs/harness-engineering.md`, `.agents/skills/harness-engineering/SKILL.md`, `.agents/skills/harness-test/SKILL.md`, `.agents/skills/harness-review/SKILL.md`, `.agents/skills/harness-integrate/SKILL.md` |
| Re-verification | completed | Docs-only checks passed after review-loop reinforcement. | `rg --files AGENTS.md .agents/docs .agents/skills docs/plan`, `git diff --check`, `git status --short --branch`, `rg -n "docs/superpowers|superpowers|subagent-review-loop|uv run pytest|compileall|review gate|test lane" AGENTS.md .agents docs/plan` |
