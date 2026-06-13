## User Request

Strengthen E2E coverage so phases 1, 2, and 3 of retrieval behavior are
visible in end-to-end style tests instead of only service-level tests.

Validated direction from the conversation:

- Phase 1: alias-only retrieval hit
- Phase 2: rewrite-required retrieval hit
- Phase 3: repository-lookup docs-before-code ranking hit

## Branch Preflight Result

- Source workspace: `/Users/eunhwa/.codex/worktrees/c298/MCPContentSearch`
- Git metadata was broken in that source workspace. `.git` points at
  `/Users/eunhwa/IdeaProjects/MCPContentSearch/.git/worktrees/MCPContentSearch5`,
  and `git status --short --branch` fails because that gitdir no longer exists.
- Per harness policy, that blocker was recorded before file edits in the source
  workspace.
- PR delivery resumed in a fresh isolated worktree:
  `/private/tmp/MCPContentSearch-phase-retrieval-e2e`
- Created branch `feature/phase-retrieval-e2e-coverage-pr` there from
  `origin/main` after fetching the latest remote state.

## Scope and Non-Goals

In scope:

- Add or update E2E tests under `tests/e2e/` so each retrieval phase has a
  caller-surface scenario that demonstrates its user-visible value.
- Reuse fake connectors, temporary SQLite paths, fake vector retrievers, and
  fake query rewriters so tests remain deterministic and non-live.

Non-goals:

- No MCP tool contract change.
- No search runtime behavior change unless a test uncovers a real bug that must
  be fixed to support the intended retained behavior.
- No inspection or mutation of local user Chroma or SQLite data.
- No live LLM, Notion, Tistory, or GitHub network calls.
- No git repair, branch creation, commit, push, or PR work unless the gitdir
  blocker is resolved first.

## Acceptance Criteria

- A retained E2E test demonstrates Phase 1 behavior by retrieving a document
  only when alias-expanded query variants are used.
- A retained E2E test demonstrates Phase 2 behavior by retrieving a hit only
  when rewrite-enabled search is allowed to issue the rewritten retrieval
  query.
- A retained E2E test demonstrates Phase 3 behavior by preferring a docs-like
  GitHub result over many synchronized code-like results for a repository lookup
  query.
- The new tests are deterministic, fake/local only, and safe for
  `./scripts/verify_functional_e2e.sh`.

## Step Breakdown

1. Add plan and record git/worktree blocker.
2. Extend `tests/e2e/` fixtures/helpers for phase-focused retrieval scenarios.
3. Run focused E2E pytest first, then broaden to the retained functional gate if
   feasible.
4. If failures occur, classify them and update this plan before retrying.

## Files Likely to Change

- `docs/plan/2026-06-13-phase-retrieval-e2e-coverage.md`
- `tests/e2e/test_contextwiki_flow.py`

## Test and Verification Plan

Focused first:

```bash
python -m pytest -q tests/e2e/test_contextwiki_flow.py
```

Broader retained gate if focused tests pass and environment allows:

```bash
./scripts/verify_functional_e2e.sh
```

Fallback:

- If the uv-backed retained gate fails for environment reasons, record the
  blocker and keep the focused pytest result as the nearest safe evidence.

## Functional Smoke Matrix

| Feature or workflow | Caller surface | Safe data mode | Expected result | Planned result |
| --- | --- | --- | --- | --- |
| Phase 1 alias expansion | MCP `search_context` after fake source sync | temp SQLite + fake vector retriever | alias-expanded query variants surface the matching chunk | passed: `test_contextwiki_e2e_phase1_alias_expansion_recovers_aws_document` |
| Phase 2 query rewrite | MCP `search_context` after fake source sync | temp SQLite + fake vector retriever + fake rewriter | rewrite-enabled retrieval surfaces a hit while the no-rewrite baseline returns no results | passed: `test_contextwiki_e2e_phase2_query_rewrite_recovers_rewrite_required_search_hit` |
| Phase 3 docs-before-code ranking | MCP `search_context` after fake GitHub-like sync | temp SQLite + fake indexed docs | docs-like result outranks synchronized code-like competitors for a repository lookup query | passed: `test_contextwiki_e2e_phase3_repository_lookup_prefers_docs_before_code` |

## Architecture and ADR Constraints

- Keep layered ownership per ADR 0001: tests may exercise MCP handlers and
  services but should not move search logic into `api/tools.py`.
- Preserve ADR 0002 safety: use temporary persistence and fake external sources
  only.
- Preserve ADR 0006 scope: tests target retained MCP sync/search/fetch/answer
  paths and optional query rewrite, not removed browser/wiki/live-search
  surfaces.

## Risks and Rollback Notes

- The main risk is writing brittle E2E tests that duplicate internal service
  implementation details too closely. Mitigation: assert caller-visible outcomes
  plus minimal collaborator evidence such as fake rewriter calls only where
  necessary.
- Git metadata is broken, so final branch/PR delivery may be blocked even if the
  code changes are correct. If needed, rollback is limited to reverting the test
  file edits in this workspace after git is repaired.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Source workspace was blocked by a broken linked-worktree gitdir, so PR delivery was resumed from a fresh isolated worktree at `/private/tmp/MCPContentSearch-phase-retrieval-e2e` on `feature/phase-retrieval-e2e-coverage-pr` from `origin/main`. | Source workspace: `cat .git`; `git status --short --branch` -> fatal missing gitdir. PR worktree: `git worktree add -b feature/phase-retrieval-e2e-coverage-pr /private/tmp/MCPContentSearch-phase-retrieval-e2e origin/main` |
| Planning | completed | Scoped work to deterministic `tests/e2e` additions for phases 1/2/3 and recorded verification targets. | This plan document |
| Worker orchestration | completed | Single-owner implementation boundary is `tests/e2e/test_contextwiki_flow.py`; no parallel write split is needed for this focused test task. | Plan scope and files likely to change |
| Implementation | completed | Added deterministic E2E scenarios for alias expansion, rewrite-required retrieval, and docs-before-code ranking in `tests/e2e/test_contextwiki_flow.py`. | `tests/e2e/test_contextwiki_flow.py` |
| Focused verification | completed | Focused E2E pytest passed after initial implementation and through each reviewer-driven hardening pass, including the latest PR-worktree reruns after copying into the fresh branch. | `python -m pytest -q tests/e2e/test_contextwiki_flow.py` -> `6 passed in 26.64s`; reruns -> `6 passed in 7.22s`, `6 passed in 7.64s`, `6 passed in 8.82s`, `6 passed in 4.00s`, `6 passed in 9.25s`, `6 passed in 4.00s`, `6 passed in 8.15s` |
| Functional smoke | completed | Retained functional gate passed after initial implementation and through each reviewer-driven hardening pass, including the latest PR-worktree rerun after the final phase-3 tightening. | `./scripts/verify_functional_e2e.sh` -> `275 passed in 39.18s`; reruns -> `275 passed in 40.50s`, `275 passed in 52.19s`, `275 passed in 52.50s`, `327 passed in 76.31s`, `327 passed in 51.05s` |
| Review pass 1 | completed/actionable | Five-reviewer pass found real coverage gaps: phase 1 could still pass through metadata fallback, phase 2 did not prove rewrite dependency strongly enough, and phase 3 mostly proved filtering/string-match rather than docs-before-code competition. | Reviewer findings from Linnaeus, Ohm, Newton, Meitner |
| Review remediation 1 | completed | Hardened fixtures to remove the phase 1 metadata fallback escape hatch, compared no-rewrite vs rewrite-enabled MCP results for phase 2, restored retrieval-query history assertions for phases 1 and 2, removed brittle monkeypatching by injecting `vector_retriever_cls`, changed phase 3 to a repository lookup with synchronized code competitors, and moved the docs file behind code fixtures so insertion order cannot explain the result. | `tests/e2e/test_contextwiki_flow.py`; focused pytest + retained functional gate reruns |
| Review remediation 2 | completed | Switched the new phase tests onto FastMCP tool invocation, narrowed plan wording to match the actual proof surface, moved phase 3 onto the indexed retrieval path with code-heavy fake vector candidates, and split phase 2 retrieval history so baseline and rewrite-enabled calls are asserted independently. | `tests/e2e/test_contextwiki_flow.py`; `docs/plan/2026-06-13-phase-retrieval-e2e-coverage.md`; focused pytest + retained functional gate reruns |
| Review pass 2 | pending | A fresh five-reviewer pass was started, but repeated actionable findings kept the loop open; the latest remediations are verified, yet a final clean five-reviewer pass was not re-collected before handoff. | Pending |
