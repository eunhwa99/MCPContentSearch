# README and Docs Simplification

## User request

Update the reader-facing docs so setup mistakes encountered during Docker,
Claude Desktop MCP registration, GitHub repository config, and Obsidian vault
wiring are less likely to happen again. Consolidate the maintained
architecture/explanation path onto `README.md` plus `.agents/docs/architecture.md`
so readers do not need a separate core-understanding note or ADR trail for
current setup.

## Branch preflight result

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Started from a clean detached worktree, fetched `origin/main`, and created `feature/readme-mcp-docker-obsidian` from `origin/main` because local `main` is checked out in another linked worktree. | `git status --short`; `git branch --show-current`; `git branch -vv`; `git worktree list`; `git fetch origin main`; `git switch -c feature/readme-mcp-docker-obsidian origin/main` |

## Scope and non-goals

- Clarify README guidance for:
  - quoted `.env` pitfalls in `CONTEXTWIKI_GITHUB_REPOSITORIES`
  - Tistory blog-name format expectations
  - Docker Obsidian vault mount requirements
  - stdio MCP behavior in Docker and Claude Desktop
  - Claude Desktop launching the server automatically
- Simplify the active docs structure so `Architecture` is the single maintained
  design/reference document beyond the README.

Non-goals:

- No runtime behavior changes
- No Claude Desktop config file edits as part of this repo change
- No connector or Dockerfile code changes
- No historical plan-doc cleanup

## Acceptance criteria

- README no longer suggests `.env` examples that are known to break the current
  parser for GitHub repository specs.
- README explicitly explains that Dockerized Obsidian sync needs both a mount
  and a container-visible vault path.
- README explains that this MCP server is stdio-based, so detached Docker runs
  are not the normal MCP client integration path.
- README includes a Claude Desktop example that can use Docker as the spawned
  MCP command.
- README no longer points readers to `docs/contextwiki-core-understanding.md`
  or ADRs as required current docs.
- Harness docs point to `.agents/docs/architecture.md` as the active design
  source of truth.
- The maintained design explanation now lives only in
  `.agents/docs/architecture.md`, with the previously duplicated core
  understanding content absorbed there.

## Step breakdown

1. Review the current README and harness/doc structure against the observed
   setup failures and current duplicate-doc paths.
2. Update README examples and troubleshooting so the documented path matches the
   current parser and MCP runtime model.
3. Remove active references that force readers through the extra
   core-understanding and ADR path, and keep architecture as the maintained
   design note.
4. Run docs-only verification checks.

## Files likely to change

- `README.md`
- `AGENTS.md`
- `.agents/docs/architecture.md`
- `.agents/docs/harness-engineering.md`
- `.agents/docs/adr/README.md`
- `.agents/docs/github-workflow.md`
- `.agents/skills/harness-engineering/SKILL.md`
- `.agents/skills/harness-plan/SKILL.md`
- `.agents/skills/harness-review/SKILL.md`
- `.agents/skills/harness-functional-smoke/SKILL.md`
- `.agents/skills/harness-implement/SKILL.md`
- `docs/images/claude-desktop-dynamodb-star-example.png`
- `docs/plan/README.md`
- `docs/plan/2026-06-15-readme-mcp-docker-obsidian.md`

## Test and verification plan

- `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`
- `git status --short --branch`
- `git diff --check`
- Stage docs-only files
- `git diff --cached --check`

## Functional smoke matrix or planned matrix rows before review

| Surface | Planned check | Why |
| --- | --- | --- |
| README setup guidance | manual diff inspection | Docs-only request; no runtime behavior change |
| Docs structure redirects | manual diff inspection | Confirm architecture-only active-doc path, ADR archive wording, and the intentional deletion of the duplicated core-understanding file stay reader-clear |
| Harness and instruction contracts | manual diff inspection | Confirm AGENTS, harness docs, phase skills, and plan template all reflect the same architecture-first active-doc policy |

## Architecture constraints

- Follow `.agents/docs/architecture.md` slim MCP scope and keep README aligned
  with retained MCP tools and local source connectors.
- Preserve the current documented local-vault Obsidian behavior and
  client-attached stdio MCP framing.

## Risks and rollback notes

- Risk: over-documenting one client path while obscuring the local `uv` path.
  Mitigation: keep both local and Docker/client-spawn paths explicit.
- Rollback: revert the staged docs bundle for this work item if the new
  architecture/README simplification wording proves inaccurate.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created `feature/readme-mcp-docker-obsidian` from `origin/main` in this clean worktree. | Git evidence above |
| Planning | completed | Expanded the earlier README-only scope into a docs-structure simplification. Direct implementation remains appropriate because this is still an atomic docs-only change owned by one author across tightly related files. | This plan |
| README update | completed | Rebased the README structure around the simplified reader flow, then folded in the reproduced GitHub quoting, Tistory format, Docker stdio, Claude Desktop auto-launch, and Dockerized Obsidian mount pitfalls so the documented path is safer to follow verbatim. | `README.md` |
| README Docker clarification follow-up | completed | Added an explicit split between the minimum Docker run example and the Obsidian-enabled Docker run example so readers do not assume the vault mount is optional when `source_obsidian` is in use. | `README.md` |
| README Claude Desktop example | completed | Added a real Claude Desktop screenshot plus a short example prompt so readers can see a Claude client workflow using ContextWiki after setup. | `README.md`; `docs/images/claude-desktop-dynamodb-star-example.png` |
| README wording cleanup follow-up | completed | Removed the extra explanatory sentence under the Claude Desktop example so the README stays focused on the prompt plus screenshot evidence only. | `README.md` |
| Functional smoke | completed | Completed the docs-only smoke rows by manually inspecting the README setup flow, Docker/Obsidian notes, Claude Desktop example placement, architecture-only active-doc wording, ADR archive wording, and harness/instruction-contract consistency against the final staged wording. | `README.md`; `AGENTS.md`; `.agents/docs/harness-engineering.md`; `.agents/docs/github-workflow.md`; `.agents/skills/harness-engineering/SKILL.md`; `.agents/skills/harness-functional-smoke/SKILL.md`; `.agents/skills/harness-review/SKILL.md`; `.agents/docs/adr/README.md`; `.agents/docs/architecture.md`; `docs/plan/README.md`; matrix rows above |
| Review-fix pass 1 | completed | Resolved reviewer findings by converting `.agents/docs/adr/README.md` into a historical archive note instead of an active harness contract. | `.agents/docs/adr/README.md` |
| Review-fix pass 2 | completed | Clarified that the documented Claude Desktop config path is the macOS path, added the missing Docker image build prerequisite, fixed the stale AGENTS connector inventory to include Obsidian, and synced the plan file list with the actual staged scope. | `README.md`; `AGENTS.md`; `docs/plan/2026-06-15-readme-mcp-docker-obsidian.md` |
| Review-fix pass 3 | completed | Reframed the screenshot section as a Claude Desktop client workflow example rather than a direct server-answer capability, and updated the plan template wording from `Architecture/ADR constraints` to `Architecture constraints` so active plan guidance matches the simplified harness contract. | `README.md`; `docs/plan/README.md`; `docs/plan/2026-06-15-readme-mcp-docker-obsidian.md` |
| Docs structure simplification | completed | Removed the active `Core Understanding` reader path from README and harness flows, dropped current-reader ADR dependencies from active harness docs, and positioned `Architecture` as the single maintained design reference beyond the README. | `README.md`; `AGENTS.md`; `.agents/docs/architecture.md`; `.agents/docs/adr/README.md`; harness docs/skills |
| Review-fix pass 4 | completed | Separated local `uv` prerequisites from Docker prerequisites and clarified that the Docker Claude Desktop config is minimal by default and needs an extra mount only for Obsidian. | `README.md`; `docs/plan/2026-06-15-readme-mcp-docker-obsidian.md` |
| Review-fix pass 5 | completed | Added an explicit warning about reusing a host-path Obsidian env var with the minimum Docker example. | `README.md`; `docs/plan/2026-06-15-readme-mcp-docker-obsidian.md` |
| Follow-up docs merge | completed | Merged the remaining setup and runtime mental model into `.agents/docs/architecture.md`, then deleted `docs/contextwiki-core-understanding.md` per the latest request so current docs have a single maintained design document. Historical plan-doc mentions remain archival only. | `.agents/docs/architecture.md`; `docs/plan/2026-06-15-readme-mcp-docker-obsidian.md`; delete `docs/contextwiki-core-understanding.md` |
| Docs verification | completed | Reran docs-only file listing, branch status, whitespace checks, staged diff checks, and cached diff checks after the follow-up architecture merge and file deletion. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git add ...`; `git diff --cached --check` |
| Review-fix pass 6 | completed | Cleared the stale post-merge plan state by marking docs verification complete and keeping the verification evidence path on the real `SKILL.md` files rather than a nonexistent markdown pattern. | `docs/plan/2026-06-15-readme-mcp-docker-obsidian.md` |
| Review-fix pass 7 | completed | Expanded `.agents/docs/architecture.md` so the single maintained design doc now covers retained `sync_all` aggregate semantics, retrieval/debug policy boundaries, and the layered verification model that previously lived in the duplicated note. | `.agents/docs/architecture.md`; `docs/plan/2026-06-15-readme-mcp-docker-obsidian.md` |
| Final review gate | completed | Ran a fresh five-reviewer `$subagent-review-loop` pass after the final architecture backfill fixes. The newest pass reported no actionable findings, so the architecture-only maintained-doc transition and file deletion are review-clean. | reviewers `Ramanujan`, `Poincare`, `Locke`, `Carson`, and `Hubble` in this thread |
