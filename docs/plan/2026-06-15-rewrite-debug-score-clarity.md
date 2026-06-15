## User request

- Clarify query rewrite debug behavior so the rewrite trigger is explicitly based on the initial vector score, rename the reason from `low_initial_score` to a clearer name, expose score fields that explain the decision, and implement the recommended "compare original vs rewritten results and keep the better set" behavior.

## Branch preflight result

- Starting state: clean detached HEAD at `e7ed8a7` in `/Users/eunhwa/.codex/worktrees/0d69/MCPContentSearch`.
- `git fetch origin main` succeeded and `origin/main` resolved to `e7ed8a7`.
- `git switch main` was blocked because `main` is checked out in `/Users/eunhwa/IdeaProjects/MCPContentSearch`; this worktree instead created `feature/rewrite-debug-score-clarity` directly from `origin/main`, which is the same commit.
- Safe branch cleanup was not attempted because the repository has many linked worktrees and no obviously safe unowned local work branches to delete without deeper branch-by-branch review.

## Scope and non-goals

### Scope

- Rename the prerank rewrite reason to make the vector-score basis explicit.
- Keep rewrite gating based on the initial vector retrieval score.
- Compare original-query and rewritten-query retrieval outcomes once and keep the better candidate set instead of blindly committing to rewritten results.
- Extend debug payloads so callers can see the initial and final top scores relevant to the rewrite decision.
- Add or update focused tests for the new behavior.

### Non-goals

- Do not change the broader retrieval/rerank policy thresholds beyond the naming/visibility needed here.
- Do not introduce multi-step rewrite retry loops.
- Do not change MCP tool contracts outside the existing debug surfaces.

## Acceptance criteria

- Rewrite gating still evaluates the initial vector-stage score rather than rerank output.
- The prerank low-score reason is renamed to a clear value such as `low_initial_vector_score`.
- When rewrite produces results, the pipeline compares original and rewritten candidate sets once and keeps the stronger set using a deterministic local rule.
- Debug output exposes the score used for the initial rewrite decision and the final top score of the selected result set.
- Focused tests cover reason naming, original-vs-rewritten selection behavior, and debug score reporting.

## Step breakdown

1. `rewrite-policy-debug`
   - Read retrieval/ranking/context debug code and identify the narrowest place to rename the reason, preserve initial-vector gating, and compare original vs rewritten results.
   - Acceptance: a bounded code change plan exists for `search/retrieval_pipeline.py`, `search/context_service.py`, and matching tests.
2. `red-tests`
   - Add failing tests for the renamed reason, selected-result comparison, and debug score fields.
   - Acceptance: the focused test command fails for the intended missing behavior.
3. `green-implementation`
   - Implement the selection rule and debug payload updates with minimal contract drift.
   - Acceptance: focused tests pass and debug fields stay reviewer-readable.
4. `verification-and-smoke`
   - Run focused tests, dependency-free compile, a relevant eval or explain/debug surface if needed, and the repo functional E2E gate or record a blocker.
   - Acceptance: verification evidence is recorded in the progress log.

## Files likely to change

- `search/retrieval_pipeline.py`
- `search/context_service.py`
- `tests/search/test_context_service.py`
- Potentially `tests/api/test_tools_contract.py` if debug contract expectations need updates
- Potentially `.agents/docs/architecture.md` if the maintained rewrite-debug vocabulary needs an explicit update

## Test and verification plan

- Focused RED/GREEN command for touched behavior:
  - `uv run pytest -q tests/search/test_context_service.py -k rewrite`
- Additional focused contract coverage if needed:
  - `uv run pytest -q tests/api/test_tools_contract.py -k rewrite`
- Dependency-free syntax check:
  - `python -m compileall api core environments fetching indexing search storage main.py`
- Repo functional gate before review:
  - `./scripts/verify_functional_e2e.sh`

## Functional smoke matrix

| Surface | Scenario | Safe mode / data | Expected outcome | Status |
| --- | --- | --- | --- | --- |
| `search_context` debug | Query rewrite is triggered by low initial vector score | Existing local fake/temp test surfaces only | Debug shows renamed reason plus initial/final score fields | completed |
| Retrieval pipeline selection | Rewrite results are worse than original results | Existing local fake/temp test surfaces only | Pipeline retains original candidate set | completed |
| Retrieval pipeline selection | Rewrite results are stronger than original results | Existing local fake/temp test surfaces only | Pipeline adopts rewritten candidate set and reports rewritten queries | completed |
| Functional E2E gate | Existing retained search/citation workflows | Local-first script temp data | No regression in retained retrieval/search flows | completed |

## Architecture constraints

- Keep query rewrite optional and inspectable; do not blur rewrite decisions into opaque ranking state.
- Preserve the distinction between raw vector retrieval signals and rerank-adjusted final scores.
- Keep top-level rewrite state reviewer-readable and nested debug explanation vocabulary explicit.
- Avoid touching local Chroma or SQLite user data outside existing deterministic test paths.

## Risks and rollback notes

- Risk: debug-field changes can drift contract expectations. Mitigation: update focused tests and only add fields inside existing debug structures.
- Risk: comparing original vs rewritten candidate sets could accidentally prefer a noisier reranked set. Mitigation: keep the rule deterministic and grounded in local score signals, with targeted tests for both directions.
- Rollback: revert the rewrite-selection helper and debug field additions if focused tests or functional E2E show regressions.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Fetched `origin/main`, confirmed `origin/main=e7ed8a7`, and created `feature/rewrite-debug-score-clarity` from it because local `main` is checked out in another worktree. | `git fetch origin main`; `git switch -c feature/rewrite-debug-score-clarity origin/main` |
| Planning | completed | Wrote the plan, recorded the atomic single-owner decision, and scoped the rewrite-policy/debug update. | `docs/plan/2026-06-15-rewrite-debug-score-clarity.md` |
| Implementation | completed | Addressed pass-2 findings by capturing raw initial vector score before metadata fallback/merge, removing count-only rewrite triggering, reordering result-set comparison to prefer rank-ordered quality before totals, expanding search-side regressions for strong raw vector hits and metadata-promotion visibility, locking the MCP `answer_with_citations` debug contract, and fixing the `result_set_signature()` type annotation to satisfy mypy in pass 3. | `search/retrieval_pipeline.py`; `tests/search/test_context_service.py`; `tests/api/test_tools_contract.py`; `.agents/docs/architecture.md` |
| Focused verification | completed | Pass-3 fix verification passed across mypy and rewrite/search/answer/contract tests. | `uv run mypy search/retrieval_pipeline.py`; `uv run pytest -q tests/search/test_context_service.py -k rewrite tests/search/test_answer_service.py -k rewrite tests/api/test_tools_contract.py -k "debug or answer_with_citations_can_include_debug_payload"` |
| Functional smoke | completed | Functional E2E gate remains green from the pass-2 fix cycle; no additional behavior change beyond the type annotation was introduced afterward. | `./scripts/verify_functional_e2e.sh` |
| Review gate | in_progress | Review pass 3 produced one actionable mypy/type-annotation finding and was remediated; focused verification reran clean, and a fresh fourth pass is now required. | Reviewer pass 3 notifications plus rerun verification evidence |
