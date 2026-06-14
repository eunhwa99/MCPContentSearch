# Issue 56 Doc Alignment Plan

## User request

Issue 56 작업 후 검증, 3-agent 리뷰 루프, PR 작성까지 진행.

## Branch preflight result

- Original worktree `/Users/eunhwa/IdeaProjects/MCPContentSearch` started dirty on `feature/readme-rewrite-cleanup`; preserved without switching or cleanup.
- Fetched `origin/main` from the original worktree.
- Created isolated worktree `/private/tmp/MCPContentSearch-issue56-doc-alignment` on fresh branch `feature/issue-56-doc-alignment` from `origin/main`.

## Scope

- Align shipped documentation and accepted ADR wording with current retrieval, egress, retained-source, and debug-contract behavior for Issue 56.
- Update only docs/instructions/ADR files plus this plan.

## Non-goals

- No runtime behavior changes.
- No tests or MCP contract implementation changes.
- No local Chroma or SQLite inspection, mutation, reset, or migration.

## Acceptance criteria

- README, core understanding note, architecture doc, and directly relevant accepted ADRs no longer contradict current code on:
  - query rewrite egress covering both `search_context` and `answer_with_citations`
  - disabled-source sync semantics versus visibility of already indexed content
  - retrieval metadata fallback and SQLite gating
  - retained connector scope including Obsidian
  - actual `include_debug` behavior, including the `search_context` public no-matching-sources exception path
  - GitHub connector configuration needed to reason about fetch completeness and reproducibility
- Runtime behavior remains unchanged.

## Step breakdown

1. `docs-drift-audit`
   - Read issue requirements, relevant docs, and implementation files.
   - Confirm exact drift points against code before editing docs.
2. `docs-alignment-edit`
   - Update `README.md`, `docs/contextwiki-core-understanding.md`, `.agents/docs/architecture.md`, and relevant accepted ADRs.
   - Keep wording accurate to current behavior and scope.
3. `docs-verification`
   - Run docs-only verification and targeted grep spot checks for corrected phrases.
4. `review-and-pr`
   - Run requested 3-agent review loop, fix any findings, rerun affected verification, then commit/push/create PR.

## Files likely to change

- `README.md`
- `docs/contextwiki-core-understanding.md`
- `.agents/docs/architecture.md`
- `.agents/docs/adr/0001-layered-mcp-content-search-architecture.md`
- `.agents/docs/adr/0004-contextwiki-phase-b-connectors.md`
- `.agents/docs/adr/0006-slim-mcp-core-scope.md`
- `docs/plan/2026-06-14-issue-56-doc-alignment.md`

## Test and verification plan

- `rg` spot checks against code/docs for issue-specific drift terms.
- `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`
- `git status --short --branch`
- `git diff --check`
- stage relevant docs-only files
- `git diff --cached --check`

## Functional smoke matrix

| Feature or workflow | Caller surface | Safest data mode | Expected visible result | Command or action | Result | Evidence | Blocker / substitute |
| --- | --- | --- | --- | --- | --- | --- | --- |
| README contract wording | Docs/readme inspection | local file only | README matches current retrieval and source scope | manual diff + `rg` spot check | passed | `README.md` diff plus `rg -n "metadata fallback|no_matching_sources|CONTEXTWIKI_GITHUB_DEFAULT_REF|CONTEXTWIKI_GITHUB_MAX_FILES|CONTEXTWIKI_GITHUB_MAX_FILE_BYTES|CONTEXTWIKI_GITHUB_USER_AGENT"` | n/a |
| Core understanding note | Docs note inspection | local file only | human explanation matches current code paths | manual diff + `rg` spot check | passed | `docs/contextwiki-core-understanding.md` diff plus `rg -n "answer_with_citations inherits|metadata fallback|no_matching_sources|source_obsidian"` | n/a |
| Architecture/ADR contract wording | Docs/ADR inspection | local file only | accepted ADRs and architecture no longer contradict code | manual diff + `rg` spot check | passed | diffs in `.agents/docs/architecture.md`, ADR `0001`, `0004`, `0006`; targeted `rg -n` spot checks | n/a |
| Runtime behavior unchanged | Docs-only verification | no runtime mutation | only docs/plan files changed | `git diff --stat` | passed | `git status --short --branch`; docs-only changed files only | nearest substitute for live smoke because task is docs-only |

## Architecture / ADR constraints

- Respect `.agents/docs/architecture.md` layered boundaries and slim MCP core scope.
- Relevant accepted ADRs: `0001`, `0002`, `0003`, `0004`, `0006`.
- Do not document removed surfaces as retained production behavior.
- Do not claim fully local operation unless embedding and rewrite egress conditions are both covered accurately.

## Risks and rollback notes

- Main risk is documenting a plausible but incorrect behavior. Mitigation: verify every claim against current code before editing.
- Docs-only rollback is a normal git revert of the task commit; no user data impact.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Preserved dirty original worktree, fetched `origin/main`, created isolated worktree and fresh feature branch. | `git status --short --branch`; `git fetch origin main`; `git worktree add -b feature/issue-56-doc-alignment /private/tmp/MCPContentSearch-issue56-doc-alignment origin/main` |
| Planning | completed | Read harness docs, workflow, architecture, ADR index, relevant accepted ADRs, and issue body. Captured docs-only scope plus user-requested 3-agent review-loop exception. | repo docs and `gh issue view 56 --json ...` |
| Drift audit | completed | Confirmed code-backed drift around answer-flow rewrite egress, disabled-source visibility, metadata fallback, retained Obsidian scope, `include_debug` exception path, and GitHub connector config wording. | `rg -n ...`; `sed -n` on `api/tools.py`, `search/context_service.py`, `search/retrieval_pipeline.py`, `search/answer_service.py`, `fetching/connectors.py`, `fetching/github.py`, `environments/config.py` |
| Worker orchestration | completed | Docs-alignment worker updated owned docs and accepted ADR wording only; runtime files left unchanged. | `apply_patch` on README, core-understanding note, architecture doc, ADR 0001/0004/0006 |
| Focused verification | completed | Re-ran docs-only verification and targeted grep checks after worker edits. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check`; targeted `rg -n` spot checks for issue terms |
| Functional smoke | completed | Completed docs-only smoke matrix using local file inspection and issue-term grep substitutes. | Matrix rows above marked `passed` |
| Review pass 1 | completed | Actionable docs findings returned across the early 3-reviewer passes and were fully remediated before the final clean pass. | Reviewer findings on `.agents/docs/architecture.md`, `docs/contextwiki-core-understanding.md`, and `README.md` |
| Review remediation 1 | completed | Updated `.agents/docs/architecture.md` so the `search_documents` flow matches `_retrieve_candidates()` behavior. | `apply_patch`; targeted `rg -n "search_documents|metadata fallback" .agents/docs/architecture.md` |
| Review remediation 2 | completed | Updated the section 5 retrieval diagram so both `search_context` and `search_documents` show metadata-fallback candidates being added before SQLite validation, and closed the review-pass state. | `apply_patch`; targeted `rg -n "metadata fallback|Review pass 1|Review remediation 1" docs/contextwiki-core-understanding.md docs/plan/2026-06-14-issue-56-doc-alignment.md` |
| Review remediation 3 | completed | Softened the README GitHub token note so optional unauthenticated access is described as visibility-dependent and rate-limited, not guaranteed. | `apply_patch`; targeted `rg -n "GITHUB_TOKEN|unauthenticated|Review remediation" README.md docs/plan/2026-06-14-issue-56-doc-alignment.md` |
| Review remediation 4 | completed | Corrected docs that implied normal non-`include_debug` search responses omit `debug`; documented the literal `search_context` wrapper shape as always returning `debug`, defaulting to `{}` except for the populated `no_matching_sources` fast path. | `apply_patch`; targeted `rg -n "include_debug|no_matching_sources|debug" README.md docs/contextwiki-core-understanding.md .agents/docs/architecture.md docs/plan/2026-06-14-issue-56-doc-alignment.md` |
| Final 3-agent review pass | completed | Fresh 3-reviewer pass reported no actionable findings after remediations 1-4. | reviewer ids `019ec607-e59b-73c2-964d-7cfc40306e12`, `019ec608-1adb-79d1-a048-5357940da1c5`, `019ec608-4f04-70a0-ab83-ebbc15139408` |
| Integration / PR delivery | in_progress | Staging final docs-only diff, then commit, push, and create `main`-base PR. | Pending |
