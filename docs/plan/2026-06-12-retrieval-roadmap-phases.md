# Retrieval Roadmap Phases

## User request

Record the agreed phase split for the current retained retrieval backlog so a
future agent can pick it up reliably, and mirror the same phase plan in a
GitHub roadmap issue.

## Branch preflight result

- Starting worktree: `/Users/eunhwa/.codex/worktrees/c298/MCPContentSearch`
- Initial state: dirty on `feature/slim-mcp-core` with local modifications in
  `environments/token.py`
- Safety action: did not switch, pull, or delete branches in the dirty worktree
- Freshness check: `git fetch origin main` succeeded from the dirty worktree
- Isolated worktree: created
  `/Users/eunhwa/.codex/worktrees/retrieval-roadmap/MCPContentSearch`
  on `feature/retrieval-roadmap-phases` from `origin/main`
- Current safe branch before edits:
  `## feature/retrieval-roadmap-phases...origin/main`

## Scope and non-goals

In scope:

- Add a durable `docs/plan/` roadmap document that captures the agreed phase
  ordering for retrieval-related issues.
- Create one GitHub umbrella issue that mirrors the same phase plan and links
  the issue group clearly.
- Make the roadmap scope explicit: the current retained retrieval backlog uses
  non-contiguous GitHub issue numbers and includes `#42`.

Non-goals:

- No implementation of the retained retrieval backlog issues captured in this
  roadmap
- No MCP tool contract changes
- No Chroma or SQLite mutation
- No code, config, or runtime behavior changes outside this plan document

## Acceptance criteria

- A future agent can open one plan document under `docs/plan/` and see the
  agreed phase ordering, rationale, dependencies, and verification expectations.
- A GitHub roadmap issue exists and mirrors the same phase ordering.
- The document clearly distinguishes the core ordered phases from supporting
  parallel tracks.
- Docs-only verification passes for the new plan file.

## Step breakdown

1. Capture the agreed issue split and sequencing in a durable repo-local plan
   document under `docs/plan/`.
2. Create one GitHub umbrella issue with the same phase ordering, brief
   rationale, and links to the underlying issues.
3. Run docs-only verification and record the resulting issue number/URL in the
   progress log.
4. If review finds scope drift between the repo document and the GitHub issue,
   update both sources of truth together and rerun docs-only verification.

## Files likely to change

- `docs/plan/2026-06-12-retrieval-roadmap-phases.md`

## Test and verification plan

Docs-only verification:

- `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`
- `git status --short --branch`
- `git diff --check`
- stage the new plan file
- `git diff --cached --check`

GitHub verification:

- `gh issue create ...`
- `gh issue view <new-number>`

## Functional smoke matrix

| Feature or workflow | Caller surface | Safe data mode | Expected result | Command/action | Planned result |
| --- | --- | --- | --- | --- | --- |
| Retrieval roadmap persistence | repo docs | docs-only | future agent can recover ordered phases from one file | open `docs/plan/2026-06-12-retrieval-roadmap-phases.md` | completed |
| Retrieval roadmap collaboration | GitHub issue | metadata only | contributors can see the same phase plan in one umbrella issue | `gh issue create` then `gh issue view` | completed |

## Architecture and ADR constraints

- Follow the slim MCP core boundary from
  `.agents/docs/adr/0006-slim-mcp-core-scope.md`; the roadmap must stay inside
  retained retrieval scope and must not imply a return of removed Web
  Console/Auto Wiki/web fallback surfaces.
- Keep the roadmap aligned with `.agents/docs/architecture.md` layered
  boundaries: `api`, `search`, `storage`, `indexing`, `fetching`,
  `environments`, and `core`.
- Preserve the meaning of the retained tool surface:
  `list_sources`, `sync_source`, `get_sync_status`, `search_context`,
  `fetch_context`, and `answer_with_citations`.

## Risks and rollback notes

- Main risk: roadmap drift between the local plan document and the GitHub issue.
  Mitigation: write the repo document first, then mirror it closely into the
  issue body.
- No local data rollback is needed because this is docs-only work.
- This work is atomic and docs-only, so direct main-agent implementation is
  acceptable without worker delegation. The reason is narrow ownership
  (`docs/plan` plus one issue body), no shared code-module edits, and no
  contract/runtime mutation.

## Retrieval phase split

### Scope note

- This roadmap covers the current retained retrieval backlog issues:
  `#30`, `#31`, `#32`, `#33`, `#36`, `#37`, `#38`, `#39`, `#40`, `#41`, and
  `#42`.
- GitHub issue numbers `#34` and `#35` do not exist in this repository, so the
  backlog numbering is intentionally non-contiguous.

### Ordered phases

1. `Phase 0: #39 Make embedding provider and model configuration explicit`
   - Goal: make embedding provider/model/auth state explicit before any
     retrieval tuning.
   - Gate to next phase: startup/reporting and failure-path diagnostics make
     embedding misconfiguration obvious.
2. `Phase 1: #42 Add a live query smoke script for real retrieval verification`
   and `#40 Add rewrite decision observability to search debug output`
   - Goal: establish a repeatable real-query smoke loop plus minimum rewrite
     observability.
   - Gate to next phase: one command can exercise a real query flow, and debug
     output explains rewrite attempted/applied/skipped behavior.
3. `Phase 2: #30 Promote LLM query rewrite into a visible retrieval feature`
   and `#31 Add first-class search explainability and debug surfaces`
   - Goal: promote rewrite and explainability into clear user-visible retrieval
     surfaces.
   - Gate to next phase: a caller can compare original vs rewritten behavior and
     understand retrieval decisions safely.
4. `Phase 3: #38 Add document-grouped retrieval surface for search results`
   - Goal: improve browsing UX for document-seeking flows without disturbing
     chunk-level citation contracts.
   - Gate to next phase: callers can choose a grouped document view for search
     results.
5. `Phase 4: #37 Add retrieval intent classification for search and answer flows`
   and `#41 Tune source-aware ranking for broad topical queries`
   - Goal: make broad-topic retrieval policy explicit before tuning ranking
     behavior.
   - Gate to next phase: intent-aware ranking improves topical coherence with
     regression coverage.
6. `Phase 5: #36 Improve grounded answer generation for answer_with_citations`
   - Goal: strengthen final answers only after retrieval signals and retrieval
     policy are stable.
   - Gate to completion: answer generation remains evidence-grounded and does
     not mask weak retrieval.

### Parallel support track

- `#32 Extend Phase D evals with mixed-query metrics and CI artifacts`
- `#33 Improve reproducibility with Dockerfile, .env.example, and launch docs`

These are supporting tracks, not the main retrieval dependency chain. They may
run in parallel when they do not blur the retrieval-quality signal of the
ordered phases.

## Progress log

Review note: the review passes recorded below were user-approved 1-reviewer and
2-reviewer advisory passes. They do not satisfy the repository's formal fresh
five-reviewer `$subagent-review-loop` gate.

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Dirty source worktree was preserved; fetched `origin/main`; created isolated worktree and fresh branch `feature/retrieval-roadmap-phases`. | `git status --short --branch`; `git fetch origin main`; `git worktree add -b feature/retrieval-roadmap-phases ... origin/main` |
| Plan document | completed | Wrote the durable retrieval phase plan under `docs/plan/`. | This file |
| GitHub roadmap issue | completed | Created umbrella issue `#43` with the same phase ordering and rationale. | `https://github.com/eunhwa99/MCPContentSearch/issues/43` |
| Docs-only verification | completed | Docs-only checks passed after staging the new plan file; the umbrella issue was also verified with `gh issue view 43`. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git add docs/plan/2026-06-12-retrieval-roadmap-phases.md`; `git diff --cached --check`; `gh issue view 43` |
| Review pass 1 findings | completed | A single independent reviewer found scope drift: the roadmap claimed `#30`-`#41` coverage while actually including `#42`; the reviewer also asked for explicit handling of missing `#34` and `#35`. | reviewer `019eb993-ea41-7fc3-a610-e2455856866a` |
| Review pass 1 fixes | completed | Updated the repo plan and GitHub issue to state the real retained retrieval backlog scope, include `#42`, and explain that `#34` and `#35` do not exist. | `gh issue edit 43 ...`; updated this file |
| Docs-only reverification | completed | Reran docs-only checks after the review-driven scope fix and rechecked issue `#43`. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git add docs/plan/2026-06-12-retrieval-roadmap-phases.md`; `git diff --cached --check`; `gh issue view 43` |
| Review pass 2 findings | completed | First user-approved 2-reviewer pass found one actionable docs-state drift: the matrix rows and reverification progress entry were stale after the scope fix. | reviewer `019eb99b-509b-7ba0-80d6-47244b6f33b2` |
| Review pass 2 fixes | completed | Marked the matrix rows and reverification progress entry completed so the repo-local roadmap state matches the already-run verification history. | updated this file; `git add docs/plan/2026-06-12-retrieval-roadmap-phases.md`; `git diff --cached --check` |
| Review pass 3 findings | completed | Fresh user-approved 2-reviewer pass found two documentation-quality gaps: the umbrella issue lacked the slim-scope guardrails from the plan, and the progress log should not preserve an in-progress advisory review row as if it were durable state. One reviewer reported no actionable findings. | reviewers `019eb99e-b004-76b0-a1fe-d83b9b650a28`, `019eb99e-b206-7ee1-8530-68e0086df62f` |
| Review pass 3 fixes | completed | Updated `#43` to mirror the slim-scope guardrails and converted the advisory review tracking to completed durable states only. | `gh issue edit 43 ...`; updated this file |
| Docs-only reverification 2 | completed | Reran docs-only checks after the pass-3 fixes and rechecked issue `#43` before the next fresh advisory pass. | `git add docs/plan/2026-06-12-retrieval-roadmap-phases.md`; `git diff --cached --check`; `gh issue view 43` |
| Review pass 4 findings | completed | Fresh user-approved 2-reviewer pass found one remaining stale progress row: `Docs-only reverification 2` needed to be marked completed after the pass-3 fixes. No other actionable findings remained. | reviewers `019eb9a0-dda0-73b0-815a-a774bc130526`, `019eb9a0-dfb7-70a3-9f05-257570907fd8` |
| Review pass 4 fixes | completed | Marked `Docs-only reverification 2` completed so the roadmap state matches the already-run post-fix verification history. | updated this file; `git add docs/plan/2026-06-12-retrieval-roadmap-phases.md`; `git diff --cached --check` |
| Review pass 5 | completed | Fresh user-approved 2-reviewer pass reported no actionable findings. The repo-local plan and issue `#43` are aligned on scope, phases, guardrails, and advisory-review disclaimer. | reviewers `019eb9a2-65c3-7401-8f83-89f1c9a48807`, `019eb9a2-67fe-7443-8f0f-ceb34a0e2999` |
