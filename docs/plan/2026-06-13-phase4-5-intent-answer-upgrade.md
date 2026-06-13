# Phase 4 and 5 Retrieval Intent and Answer Upgrade

## User request

`phase4, 5 전부 진행해줘. 구현 계획 + 구현 + 리뷰 +PR 까지 다 해줘 그리고 이 변경을 확인하려면 내가 뭐를 봐야 하는지도 마지막에 설명해줘`

## Branch preflight result

- Starting worktree: `/Users/eunhwa/.codex/worktrees/00e1/MCPContentSearch`
- Initial state: clean detached `HEAD` at `a678da8`
- Safety actions:
  - fetched `origin/main`
  - switched to local `main`
  - fast-forwarded `main` from `origin/main` to `a678da8`
  - created fresh branch `feature/phase4-5-intent-answer-upgrade`
- Local branch cleanup: not performed because visible non-`main` work branches are checked out in linked worktrees and are not safe to delete from this task branch

## Scope and non-goals

In scope:

- Implement phase 4 issue goals from roadmap issue `#43`:
  - `#37` retrieval intent classification for search and answer flows
  - `#41` source-aware ranking improvements for broad topical queries
- Implement phase 5 issue goal from roadmap issue `#43`:
  - `#36` stronger grounded answer generation for `answer_with_citations`
- Add or update focused tests, retained eval coverage, and retained functional E2E coverage as needed for the changed retrieval and answer surfaces
- Update repo docs that explain retrieval and answer behavior when the implementation changes that human-facing understanding

Non-goals:

- No new MCP tools
- No changes to retained slim MCP core scope from ADR `0006`
- No live external API verification unless a later step explicitly requires user approval
- No inspection, deletion, or migration of user ChromaDB or SQLite data
- No web fallback, browser UI, or non-retained source work

## Acceptance criteria

- Search and answer flows expose an explicit retrieval-intent classification that differentiates at least:
  - strict document lookup
  - broad topical search
  - list/collection request
  - comparison request
- Broad topical queries receive more coherent top-ranked results through intent-aware ranking changes with deterministic regression coverage
- `answer_with_citations` produces stronger evidence-grounded answers for list, summary, and comparison-style prompts without citing unsupported content
- Insufficient-evidence behavior remains explicit and safe
- Focused tests pass for new classification, ranking, and answer rendering behavior
- Matching retained eval coverage is added or updated and executed
- Functional E2E coverage is added or updated for changed MCP-visible behavior and the retained functional gate is executed before review
- Review and PR delivery complete unless blocked by required delegation authorization or another safety blocker

## Step breakdown

1. `intent-model`
   - Read current `search/ranking.py`, `search/context_service.py`, `search/answer_service.py`, and retrieval/answer tests.
   - Define an explicit intent classification model inside `search/` so search and answer layers share the same decision instead of inferring behavior separately from raw term groups.
   - Keep MCP contract changes additive only through debug/answer wording unless a public payload change becomes necessary.
2. `red-tests`
   - Add failing focused tests for:
     - strict lookup vs broad topical classification
     - broad topical ranking coherence
     - grounded answer generation for list/summary/comparison requests
     - explicit insufficient-evidence preservation
   - Add or extend deterministic eval cases for mixed retrieval/answer intent behavior.
3. `phase4-implementation`
   - Implement intent classification and thread it through context retrieval, ranking, and answer grounding state.
   - Tune broad-topic ranking using source-aware and topic-aware signals inside `search/`.
4. `phase5-implementation`
   - Replace or augment the current rigid answer template with intent-aware grounded answer rendering that still uses only retrieved evidence chunks.
   - Preserve redaction, citation safety, and insufficient-evidence safeguards.
5. `verification-and-docs`
   - Update `docs/contextwiki-core-understanding.md` if retrieval or answer behavior explanation changes.
   - Run focused tests, retained evals, and retained functional E2E gate.
6. `review-and-pr`
   - Run the required review gate.
   - If clean, stage relevant files, commit, push, and create a `main`-base PR.

## Files likely to change

- `search/context_service.py`
- `search/answer_service.py`
- `search/ranking.py`
- `search/query_terms.py` or a new `search/intent.py`
- `tests/search/test_context_service.py`
- `tests/search/test_answer_service.py`
- `tests/search/test_ranking.py`
- `tests/e2e/test_contextwiki_flow.py`
- `evals/contextwiki_answer_quality_cases.json`
- `evals/retrieval_quality_cases.json`
- `docs/contextwiki-core-understanding.md`
- `docs/plan/2026-06-13-phase4-5-intent-answer-upgrade.md`

## Test and verification plan

Smallest-first verification:

- focused pytest for new or changed unit/integration tests under `tests/search/`
- focused eval pytest under `tests/evals/`
- `PYTHONPATH=. python scripts/run_contextwiki_eval.py`
- `python -m compileall api core environments fetching indexing search storage main.py`

Broader verification before review:

- `uv run pytest`
- `./scripts/verify_functional_e2e.sh`

If retained functional E2E coverage must be extended for the changed behavior, add that coverage first and rerun the matching focused E2E test plus the retained gate.

## Functional smoke matrix

| Feature or workflow | Caller surface | Safe data mode | Expected result | Command/action | Planned result |
| --- | --- | --- | --- | --- | --- |
| Intent-aware broad topical retrieval | MCP `search_context` | temp SQLite + fake retriever/test fixtures | broad topical query returns coherent top-ranked results with explicit debug intent | focused pytest and retained E2E case | pending |
| Intent-aware document lookup preservation | MCP `search_context` | temp SQLite + fake retriever/test fixtures | strict lookup behavior stays anchored and does not regress into broad-topic behavior | focused pytest | pending |
| Grounded list/summary/comparison answers | MCP `answer_with_citations` | temp SQLite + fake retrieval fixtures | answer uses only evidence chunks and renders stronger grounded output by intent | focused pytest + eval case | pending |
| Insufficient evidence safety | MCP `answer_with_citations` | temp SQLite + fake retrieval fixtures | missing or weak evidence still returns safe insufficient response | focused pytest + eval case | pending |
| Retained regression sweep | retained functional suite | local temp data only | existing sync/search/fetch/answer flows still pass after phase 4/5 changes | `./scripts/verify_functional_e2e.sh` | pending |

## Architecture and ADR constraints

- Keep the change inside retained slim MCP core scope from ADR `0006`.
- Preserve layered ownership from ADR `0001`: MCP contract shaping in `api/`, retrieval/ranking/answer logic in `search/`, no Chroma mutation from search formatting changes.
- Preserve SQLite/Chroma trust model from ADR `0002`: answer citations may use only retrieved chunks that exist in metadata-backed results.
- Preserve active retrieval gate assumptions from ADR `0003`: ranking and answer changes may not bypass SQLite-backed active chunk/document gating.
- Do not add live-network-only behavior to required verification.

## Risks and rollback notes

- Main implementation risk: intent classification may overfit broad topical queries and weaken strict lookup precision.
  - Mitigation: RED tests must cover at least one strict lookup and one broad topical query before implementation.
- Main answer risk: stronger answer rendering might imply unsupported synthesis.
  - Mitigation: keep answer generation extractive/grounded from cited chunks only and extend insufficient-evidence tests/evals.
- Process risk: formal worker delegation and formal five-reviewer `$subagent-review-loop` require explicit subagent authorization in the current tool policy.
  - Mitigation: ask the user for explicit approval before bypassing worker orchestration or invoking reviewer agents; if not approved, stop before the relevant gate and report the blocker instead of silently claiming completion.
- Rollback point: if ranking or answer changes prove unstable, revert the phase 4/5 branch to the last green commit on this feature branch before push.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Updated local `main` from `origin/main` and created `feature/phase4-5-intent-answer-upgrade`. | `git status --short --branch`; `git branch -vv`; `git worktree list`; `git fetch origin main`; `git switch main`; `git pull --ff-only origin main`; `git switch -c feature/phase4-5-intent-answer-upgrade` |
| Plan document | completed | Wrote the phase 4/5 implementation plan and verification matrix. | `docs/plan/2026-06-13-phase4-5-intent-answer-upgrade.md` |
| Scope inspection | completed | Inspected current retrieval, ranking, answer, eval, and roadmap context to pin the exact phase 4/5 delta from `origin/main`. | `rg -n ...`; `gh issue view 36`; `gh issue view 37`; `gh issue view 41`; `gh issue view 43`; `sed -n ... search/... tests/...` |
| Worker/review authorization | blocked | Formal harness worker/reviewer delegation still needs explicit user authorization under the active tool-policy boundary before the required review gate can run. | Current session tool policy plus repo harness rules |
| RED tests | completed | Added failing focused tests for intent classification, broad-topic ranking visibility, and grounded list/comparison answer rendering before implementation. | `tests/search/test_ranking.py`; `tests/search/test_context_service.py`; `tests/search/test_answer_service.py`; first `uv run pytest ...` run failed on missing `search.intent` and renderer gaps |
| Implementation | completed | Added deterministic intent policy, intent-aware reranking, intent debug payloads, stronger grounded list/comparison answer rendering, eval fixtures/cases, E2E coverage, and retrieval/answer behavior docs. | `search/intent.py`; `search/ranking.py`; `search/context_service.py`; `search/answer_service.py`; `tests/...`; `evals/...`; `docs/contextwiki-core-understanding.md` |
| Verification | completed | Focused search tests, focused eval/E2E tests, compile, local eval runner, retained functional E2E gate, and full pytest all passed. | `uv run pytest tests/search/test_ranking.py tests/search/test_context_service.py tests/search/test_answer_service.py -q`; `uv run pytest tests/evals/test_answer_quality.py tests/evals/test_retrieval_quality.py tests/e2e/test_contextwiki_flow.py -q`; `python -m compileall api core environments fetching indexing search storage main.py`; `PYTHONPATH=. python scripts/run_contextwiki_eval.py`; `./scripts/verify_functional_e2e.sh`; `uv run pytest` |
| Review pass 1 findings | completed | User-approved 3-reviewer pass found comparison overclaim, list duplicate-document output, strict-lookup debug-name drift, and order-sensitive duplicate-document reranking. | reviewer agents `019ec144-0c6b-7ea0-acf3-0b1da5d51236`, `019ec144-4985-7651-9191-9ef22d96ee7e`, `019ec144-7fba-7793-bbf3-a5df8d7f6b9c` |
| Review remediation 1 | completed | Tightened comparison grounding to require multiple sides, deduped collection/comparison evidence by document, aligned debug intent naming with `strict_lookup`, and changed broad/list duplicate penalties to apply after best-sibling scoring instead of first-seen order. | `search/intent.py`; `search/ranking.py`; `search/answer_service.py`; `tests/search/test_context_service.py`; `tests/search/test_answer_service.py` |
| Reverification after review 1 | completed | Reran focused search tests, focused eval/E2E tests, local eval runner, retained functional E2E gate, and full pytest after review fixes. | `uv run pytest tests/search/test_ranking.py tests/search/test_context_service.py tests/search/test_answer_service.py -q`; `uv run pytest tests/evals/test_answer_quality.py tests/evals/test_retrieval_quality.py tests/e2e/test_contextwiki_flow.py -q`; `PYTHONPATH=. python scripts/run_contextwiki_eval.py`; `./scripts/verify_functional_e2e.sh`; `uv run pytest` |
| Review pass 2 findings | completed | Fresh user-approved 3-reviewer pass found that list/comparison grounding still let intent-hint-only matches through in edge cases. | reviewer agents `019ec14a-2866-72e1-90cf-146c63152f2f`, `019ec14a-519c-78b0-a43b-9254026c8842`, `019ec14a-79ed-7720-aa60-065807339907` |
| Review remediation 2 | completed | Excluded list/comparison hint groups from evidence support, required list answers to match at least one specific topic group, and added regressions for off-topic list grounding and one-sided comparison wording edge cases. | `search/answer_service.py`; `tests/search/test_answer_service.py` |
| Reverification after review 2 | completed | Reran focused answer/search tests, focused eval/E2E tests, local eval runner, retained functional E2E gate, and full pytest after the second review fixes. | `uv run pytest tests/search/test_answer_service.py -q`; `uv run pytest tests/search/test_ranking.py tests/search/test_context_service.py tests/search/test_answer_service.py -q`; `uv run pytest tests/evals/test_answer_quality.py tests/evals/test_retrieval_quality.py tests/e2e/test_contextwiki_flow.py -q`; `PYTHONPATH=. python scripts/run_contextwiki_eval.py`; `./scripts/verify_functional_e2e.sh`; `uv run pytest` |
| Review and PR | in_progress | Fresh user-approved 3-reviewer final re-review pass is in progress after the second remediation reruns. | Pending |
