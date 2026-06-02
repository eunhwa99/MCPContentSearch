# Phase D1 and J1 Foundation

## User Request

Implement the most important remaining next step for ContextWiki:

- `Phase D1`: create a practical local evaluation foundation for retrieval and
  answer quality.
- `Phase J1`: improve non-LLM retrieval quality in a measurable way.
- Reframe the roadmap so Phase D and J are split into realistic sub-stages
  (`D1/D2`, `J1/J2`) instead of one large future bucket.

## Branch Preflight Result

- Continued the existing clean branch
  `feature/contextwiki-answer-retrieval-upgrade` at the user's request.
- No branch switch or worktree isolation was needed because the worktree was
  already clean and the user explicitly wanted to keep building on the current
  PR branch.

## Scope and Non-Goals

### Scope

- Add a deterministic local retrieval-eval layer that exercises
  `search_context` and `answer_with_citations` with fixed fixture data and
  reports pass/fail in a reusable machine-readable format.
- Extend local eval fixtures beyond payload-only answer checks to cover
  retrieval ranking expectations and answer grounding expectations together.
- Improve `ContextSearchService` deterministic reranking without requiring LLM
  rewrite or LLM answer generation.
- Update roadmap/docs so Phase D and Phase J are explicitly split into
  `D1/D2` and `J1/J2`.

### Non-Goals

- No live user-data inspection, no user Chroma/SQLite mutation, and no live
  external sync.
- No Phase J2 LLM answer generation work in this slice.
- No remote API/deployment (`Phase E`) work.
- No full dashboard or tracing backend for observability.

## Acceptance Criteria

- A local deterministic eval command exists for the new D1 retrieval/answer
  foundation and runs on fixture data only.
- Eval output distinguishes retrieval expectations from grounded-answer
  expectations and can fail specific golden cases.
- J1 reranking changes measurably improve deterministic fixture retrieval for at
  least the added regression cases.
- README, roadmap, and ContextWiki understanding notes explain `D1/D2` and
  `J1/J2` clearly.
- Focused tests and repo functional smoke pass after the changes.

## Step Breakdown

1. `d1-eval-foundation`
   - Add retrieval/answer eval case models and suite runner.
   - Add deterministic fixture data and a local runner script.
2. `j1-retrieval-quality`
   - Strengthen non-LLM rerank heuristics in `search/context_service.py`.
   - Add focused retrieval regression coverage tied to the new eval cases.
3. `phase-doc-reframe`
   - Update roadmap and maintained ContextWiki docs to split `D1/D2` and
     `J1/J2`.
4. `verification`
   - Run focused tests first, then repo functional smoke.

## Files Likely To Change

- `evals/README.md`
- `evals/answer_quality.py`
- `evals/retrieval_quality.py`
- `evals/retrieval_quality_cases.json`
- `scripts/run_contextwiki_eval.py`
- `search/context_service.py`
- `tests/evals/test_answer_quality.py`
- `tests/evals/test_retrieval_quality.py`
- `tests/search/test_context_service.py`
- `README.md`
- `docs/contextwiki-core-understanding.md`
- `docs/plan/2026-05-20-contextwiki-roadmap.md`
- `docs/plan/2026-06-02-phase-d1-j1-foundation.md`

## Test and Verification Plan

- `python -m compileall evals scripts search tests/evals tests/search`
- `PYTHONPATH=. uv run pytest tests/evals/test_answer_quality.py tests/evals/test_retrieval_quality.py tests/search/test_context_service.py`
- `PYTHONPATH=. python scripts/run_contextwiki_eval.py`
- `./scripts/verify_functional_e2e.sh`

## Functional Smoke Matrix

| Feature or workflow | Caller surface | Safest data mode | Expected visible result | Command or action | Result | Evidence location | Blocker / substitute |
| --- | --- | --- | --- | --- | --- | --- | --- |
| D1 eval runner | local CLI script | deterministic fixture data + temp SQLite | JSON summary with retrieval and answer suite pass/fail counts | `PYTHONPATH=. python scripts/run_contextwiki_eval.py` | pending | Pending | None |
| J1 retrieval regressions | focused pytest | deterministic fixture data + temp SQLite | new ranking cases pass | `tests/search/test_context_service.py` | pending | Pending | None |
| Existing answer quality checks | focused pytest | deterministic fixture data | answer eval suite still passes | `tests/evals/test_answer_quality.py` | pending | Pending | None |
| Broader repo functional gate | repo e2e script | deterministic local fake/temp paths | no regression in current browser/source-sync/wiki smoke | `./scripts/verify_functional_e2e.sh` | pending | Pending | None |

## Architecture and ADR Constraints

- Keep evaluation logic in `evals/` and script orchestration in `scripts/`,
  not inside MCP tool handlers.
- Preserve ADR 0001 boundaries: retrieval quality stays in `search/`; docs and
  roadmap changes do not move business logic into `api/`.
- Preserve ADR 0002/0004 safety boundaries: fixture eval must use temp/local
  metadata state and must not inspect user SQLite or Chroma contents.

## Risks and Rollback Notes

- Over-tuned rerank heuristics could improve fixture cases while hurting general
  behavior. Mitigation: keep boosts small, generic, and covered by focused
  regression tests instead of hardcoded query exceptions.
- Eval cases could drift into repo-specific toy checks. Mitigation: split cases
  into repo-specific and generic-behavior coverage from the beginning.
- Phase renaming in docs could confuse existing references. Mitigation: keep
  top-level `Phase D` and `Phase J` names, but explain `D1/D2` and `J1/J2`
  as sub-stages rather than replacing the roadmap.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Continued the clean existing PR branch at the user's request. | `git status --short --branch`; `git branch --show-current` |
| Planning | completed | Added D1/J1 plan with eval scope, rerank scope, docs, and verification. | This plan |
| Worker orchestration | completed | Continuing under the user's previously approved single-agent path for this branch. | Earlier user approval for option 2 |
| Implementation | completed | Added D1 deterministic retrieval/answer eval modules, fixture data, local eval runner, J1 rerank boosts for source-type and metadata-phrase matches, and roadmap/doc updates for D1/D2 and J1/J2. | `evals/retrieval_quality.py`; `evals/contextwiki_eval.py`; `evals/contextwiki_fixture_documents.json`; `evals/retrieval_quality_cases.json`; `evals/contextwiki_answer_quality_cases.json`; `scripts/run_contextwiki_eval.py`; `search/context_service.py`; docs |
| Focused verification | completed | Compile, focused eval/search tests, and the D1 eval runner passed. | `python -m compileall evals scripts search tests/evals tests/search`; `PYTHONPATH=. uv run pytest tests/evals/test_answer_quality.py tests/evals/test_retrieval_quality.py tests/search/test_context_service.py` -> 113 passed; `PYTHONPATH=. python scripts/run_contextwiki_eval.py` -> passed |
| Functional smoke | completed | Repo functional E2E gate passed after D1/J1 changes. | `./scripts/verify_functional_e2e.sh` -> fake wiki smoke passed; e2e/web console suite passed; Playwright smoke passed |
| Review gate | completed | `$subagent-review-loop` remains intentionally bypassed on this user-approved single-agent branch. | Earlier user approval for option 2 |
