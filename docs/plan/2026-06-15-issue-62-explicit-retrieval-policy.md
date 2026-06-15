# Issue 62 Explicit Retrieval Policy

## User request

- Implement GitHub issue `#62` in `MCPContentSearch`.
- After implementation, run a subagent review loop with 3 reviewers and create a PR.

## Branch preflight result

- Starting worktree: `/Users/eunhwa/.codex/worktrees/e1be/MCPContentSearch`
- Initial state: clean detached `HEAD` at `d02de70`
- Existing branch note: `feature/issue-62-explicit-retrieval-policy` already exists in another linked worktree, so this task uses a fresh branch instead of reusing it.
- Preflight actions completed:
  - `git switch main`
  - `git fetch origin main`
  - `git pull --ff-only origin main`
  - `git switch -c feature/issue-62-explicit-retrieval-policy-pass1`

## Scope

- Replace one confirmed brittle GitHub-biased retrieval heuristic with a more explicit policy rule.
- Centralize retrieval/rerank/query-rewrite thresholds so magic numbers become named policy constants.
- Preserve existing MCP tool contracts while making debug output easier to inspect during retrieval reasoning.
- Add focused regression coverage for the issue 62 failure classes that fit the current retained eval surface.
- Update maintained human-facing retrieval notes when the policy behavior changes.

## Non-goals

- Full retrieval-stack redesign across every heuristic in one PR.
- MCP contract renames or payload shape breakage.
- Live source sync or mutation of real local Chroma/SQLite user data.
- New ADR unless the implementation crosses current accepted architecture boundaries.

## Acceptance criteria

- Policy thresholds and rerank bonuses relevant to this change are centralized and named rather than left as scattered literals.
- Plain lowercase long technical tokens no longer act as an implicit GitHub repo probe by themselves.
- Retrieval debug still preserves `vector_score`, `score`, and `rerank_score`, with policy decisions inspectable through named rule paths or reasons.
- New or updated tests cover:
  - false GitHub bias from lowercase long tokens
  - mixed-language list/comparison behavior
  - query rewrite trigger/suppression edges
  - query-term expansion collision behavior
- Retained eval coverage is updated when the changed behavior falls under the existing local eval surface.
- `docs/contextwiki-core-understanding.md` reflects the new retrieval-policy behavior.

## Step breakdown

1. `policy-boundary`
   - Read retrieval policy hotspots in `search/query_terms.py`, `search/ranking.py`, `search/retrieval_pipeline.py`, and `search/answer_service.py`.
   - Confirm which literals and repo-probe branches are in scope for the first PR.
2. `test-first-regressions`
   - Add or update failing focused tests and eval fixtures for the issue 62 cases before production edits.
3. `policy-implementation`
   - Introduce named retrieval-policy constants/helpers and remove the lowercase-long-token GitHub bias path.
   - Keep existing response shapes stable.
4. `docs-alignment`
   - Update `docs/contextwiki-core-understanding.md` with the revised rule set.
5. `verification-and-review`
   - Run focused tests, retained eval coverage, compile/pytest, functional smoke, then a 3-reviewer subagent loop per explicit user request.
6. `delivery`
   - Stage task files only, commit, push, and create a `main`-base PR with `closes #62`.

## Likely files to change

- `search/query_terms.py`
- `search/ranking.py`
- `search/retrieval_pipeline.py`
- `search/answer_service.py`
- `tests/search/test_context_service.py`
- `tests/evals/test_retrieval_quality.py`
- `evals/retrieval_quality_cases.json`
- `docs/contextwiki-core-understanding.md`
- `docs/plan/2026-06-15-issue-62-explicit-retrieval-policy.md`

## Worker orchestration plan

- This is not atomic because it spans shared retrieval policy, regression coverage, retained evals, and documentation.
- Planned worker boundaries:
  - Worker A: focused failing tests/eval coverage for issue 62 without production edits outside test fixtures.
  - Worker B: retrieval-policy implementation in `search/` modules only.
  - Worker C: docs alignment and final verification support after integration.
- All workers must preserve other changes, avoid secret/local-data inspection, and avoid commit/push/PR actions.

## Test and verification plan

- Focused TDD checks:
  - targeted `uv run pytest` on retrieval/search tests updated for issue 62
  - targeted `uv run pytest` on eval tests and any changed retained eval fixtures
- Focused eval command when coverage changes:
  - `uv run pytest -q tests/evals`
- Syntax/import safety:
  - `uv run python -m compileall api core environments fetching indexing search storage main.py`
- Broader regression:
  - `uv run pytest`
- Functional smoke gate:
  - `./scripts/verify_functional_e2e.sh`
- Repo diff hygiene before delivery:
  - `git diff --check`

## Functional smoke matrix

| Surface | Scenario | Data mode | Expected result | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| `search_context` | plain lowercase technical topic without GitHub filter | temp SQLite + fake vector retriever | non-GitHub evidence remains retrievable without implicit repo bias | passed | `uv run pytest tests/search/test_context_service.py -q -k 'single_high_confidence_exact_match or false_github_bias or mixed_language_comparison or explicit_lowercase_repository_lookup or plain_lowercase_topic or vector_search_uses_llm_rewrite_queries_when_initial_results_are_low_confidence or vector_search_skips_query_rewrite_when_metadata_only_identity_match_is_already_confident'` -> `12 passed, 123 deselected` |
| `search_context` | mixed-language list/comparison prompt | temp SQLite + fake vector retriever | grounded results keep intended retrieval mode without broad false positives | passed | same focused search command above; includes `mixed_language_comparison` regression |
| `answer_with_citations` | retrieval-backed answer using changed policy | local fake/temp only | answer grounding still respects required term groups | passed | `uv run pytest tests/search/test_answer_service.py -q -k 'query_rewrite_explainability or comparison or grounding_uses_vector_score_not_rerank_bonus or keeps_original_topical_constraint_when_rewrite_relaxes_query'` -> `12 passed, 41 deselected` |
| retained eval runner | issue-62 cases in local eval surface | deterministic local fixtures | suite stays deterministic and passes with updated expectations | passed | `uv run pytest -q tests/evals` -> `22 passed`; `uv run python - <<'PY' ... run_contextwiki_eval() ... PY` -> `True` |
| repo functional E2E | retained sync/search/fetch/answer regression | local deterministic script | no task-relevant regression | passed | `./scripts/verify_functional_e2e.sh` -> `25 passed in 3.41s` |

## Architecture and ADR constraints

- Respect layered module boundaries from `.agents/docs/architecture.md`; avoid moving policy into MCP tool handlers.
- ADR `0002`: SQLite remains the citation/metadata authority; no user-data inspection or contract bypass.
- ADR `0003`: retrieval must continue to gate managed hits through SQLite-backed metadata and preserve stable chunk/document identity semantics.
- ADR `0006`: stay within the slim MCP retrieval core; no new web/wiki surfaces.

## Risks and rollback notes

- Retrieval-policy changes can silently reorder results. Mitigation: add explicit regressions before edits and run retained eval coverage plus functional smoke.
- Centralizing thresholds can accidentally change scoring math. Mitigation: keep behavioral deltas narrow and visible through tests/debug fields.
- Shared `search/` files are overlap-prone. Mitigation: use single-owner write boundaries and main-agent integration.
- Rollback point: revert the issue 62 commit or PR branch only; no local data migration is involved.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Updated local `main` and created `feature/issue-62-explicit-retrieval-policy-pass1`. | `git switch main`; `git fetch origin main`; `git pull --ff-only origin main`; `git switch -c feature/issue-62-explicit-retrieval-policy-pass1` |
| Planning context | completed | Read harness docs, workflow docs, architecture, ADR `0002`/`0003`/`0006`, issue `#62`, and retrieval hotspots. | `.agents/docs/harness-engineering.md`; `.agents/docs/github-workflow.md`; `.agents/docs/architecture.md`; `.agents/docs/adr/README.md`; relevant ADRs; `gh issue view 62`; `search/*`; `tests/search/test_context_service.py`; `tests/evals/test_retrieval_quality.py` |
| Worker orchestration | completed | Dispatched bounded test/eval and production-search workers; integrated their outputs locally. | Worker A updated `tests/search/test_context_service.py`, `tests/evals/test_retrieval_quality.py`, `evals/retrieval_quality_cases.json`; Worker B updated `search/ranking.py`, `search/retrieval_pipeline.py`, `search/answer_service.py` |
| Implementation | completed | Centralized retrieval/rerank/rewrite thresholds, removed implicit lowercase GitHub repo bias, tightened document-intent topical matching, and fixed expansion collision handling. | `search/query_terms.py`; `search/intent.py`; `search/ranking.py`; `search/retrieval_pipeline.py`; `search/answer_service.py` |
| Focused verification | completed | Ran compile, focused search/answer/query-term tests, eval tests, deterministic eval runner, and fresh full pytest after the final remediation. | `uv run python -m compileall api core environments fetching indexing search storage main.py`; focused `uv run pytest ...`; `uv run pytest -q tests/evals` -> `22 passed`; `uv run pytest` -> `618 passed in 39.00s` |
| Functional smoke | completed | Ran retained functional E2E gate after the final remediation. | `./scripts/verify_functional_e2e.sh` -> `25 passed in 3.66s` |
| Review pass 1 | completed | Three fresh reviewers found rewrite suppression, Korean prefix intent matching, GitHub-only fallback narrowing, latent context-service wrapper drift, and missing competitive false-bias coverage. | Reviewers `019ec931-5ee6-7c72-8374-84605bf013dd`, `019ec931-7ce0-75d3-8509-67db85491825`, `019ec931-9c3f-7d11-ae1c-806cd5ddf03c` |
| Review pass 1 remediation | completed | Narrowed rewrite suppression to single exact high-confidence vector hits, limited Korean request-hint prefix matching, removed generic document-intent GitHub narrowing, fixed context-service wrapper drift, added competitive false-bias regressions, and restored explicit ASCII compound aliases without reopening `awslambda` collisions. | `search/query_terms.py`; `search/intent.py`; `search/ranking.py`; `search/retrieval_pipeline.py`; `search/context_service.py`; `tests/search/test_context_service.py`; `tests/search/test_query_terms.py`; `tests/search/test_ranking.py` |
| Post-review verification | completed | Re-ran focused reviewer-targeted tests, deterministic eval coverage, compile, full pytest, functional smoke, and diff checks after remediation. | focused `uv run pytest ...` -> passing; `uv run pytest tests/search/test_query_terms.py -q` -> `6 passed`; `uv run pytest -q tests/evals` -> `22 passed`; `uv run pytest` -> `618 passed in 39.45s`; `./scripts/verify_functional_e2e.sh` -> `25 passed in 3.66s`; `git diff --check` |
| Final review pass | completed | Fresh final reviewers found no code or contract issues; one docs-only trace finding was resolved by refreshing this plan to the latest verified evidence. | Clean reviewers `019ec944-cf7b-7e80-9bfa-7f0e0d021490`, `019ec945-16a0-7371-905c-2ae22a8ed0d9`; docs-trace reminder `019ec944-f577-72a2-a398-760a3a7ff6ed` resolved here |
| Review loop | completed | Final 3-reviewer loop finished with no remaining actionable findings after the plan-trace refresh. | Final review pass above; latest verification remained `618 passed` pytest, `22 passed` evals, `25 passed` functional E2E, `git diff --check` clean |
| PR delivery | pending | Commit, push, and open `main`-base PR with `closes #62`. | Pending |
