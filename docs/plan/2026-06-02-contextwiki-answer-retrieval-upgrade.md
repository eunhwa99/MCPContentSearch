# ContextWiki Answer and Retrieval Upgrade

## User Request

Improve two related areas:

- The current ContextWiki answer output is weak and often reads like raw chunk
  concatenation rather than a helpful answer.
- Raw ContextWiki debug output is hard to use because unrelated content gets
  mixed together without a stable, human-readable structure.
- Retrieval quality should improve for natural-language requests such as
  `AWS에 적은 문서를 찾아줘`, including stronger query normalization and
  better matching for domain aliases/synonyms.

## Branch Preflight Result

- Starting worktree was clean.
- Current worktree began on detached `HEAD` at commit `540798d`.
- `git fetch origin main` succeeded.
- This worktree could not switch to local `main` because `main` is already
  checked out in another linked worktree. To preserve worktree safety while
  still starting from the latest fetched base, this task branch was created
  directly from `origin/main`.
- Working branch: `feature/contextwiki-answer-retrieval-upgrade`.

## Scope and Non-Goals

### Scope

- Improve `answer_with_citations` so the returned answer is structured and
  useful instead of a raw chunk dump.
- Add a stable raw debug format for ContextWiki answer/search flows so users can
  understand what was retrieved and why.
- Improve retrieval quality with deterministic query normalization and alias
  expansion rather than immediate LLM-only rewriting.
- Update focused tests and the maintained ContextWiki understanding note when
  retrieval/answer behavior changes.

### Non-Goals

- No live external sync or user-data mutation in local Chroma/SQLite.
- No MCP tool name or parameter changes unless required for the raw debug UI
  path and kept backward compatible.
- No full LLM answer pipeline conversion for base `answer_with_citations`.
- No changes to source sync lifecycle, indexing identity, or tombstone policy.

## Acceptance Criteria

- `answer_with_citations` returns a concise structured answer that is more
  readable than raw chunk concatenation and still remains evidence-grounded.
- Raw ContextWiki debug output is rendered in a fixed, human-readable structure
  that separates query, normalization, selected chunks, and final answer/debug
  payload sections.
- Retrieval improves for alias/synonym style queries such as AWS-related
  document searches without requiring an LLM rewrite path.
- Existing answer/search contract tests are updated or expanded to cover the new
  behavior.
- `docs/contextwiki-core-understanding.md` reflects the new retrieval and answer
  behavior.

## Step Breakdown

1. `answer-output-shape`
   - Read current answer/debug rendering paths in `search/` and `web_console/`.
   - Design a structured answer summary that remains deterministic and grounded.
2. `retrieval-normalization`
   - Extend deterministic query normalization/alias handling in the retrieval
     path.
   - Preserve current layered boundaries and SQLite/Chroma responsibilities.
3. `tests-and-docs`
   - Update focused tests for answer output and retrieval behavior.
   - Update core understanding docs.
4. `verification-and-smoke`
   - Run focused syntax/tests, then task-relevant functional smoke through the
     safest local caller surfaces.

## Files Likely To Change

- `search/answer_service.py`
- `search/context_service.py`
- `web_console/app.py`
- `web_console/payloads.py`
- `tests/search/test_answer_service.py`
- `tests/search/test_context_service.py`
- `tests/web_console/test_app.py`
- `docs/contextwiki-core-understanding.md`
- `docs/plan/2026-06-02-contextwiki-answer-retrieval-upgrade.md`

## Test and Verification Plan

- `python -m compileall api core environments fetching indexing search storage wiki web_console main.py`
- `PYTHONPATH=. uv run pytest tests/search/test_answer_service.py tests/search/test_context_service.py tests/web_console/test_app.py`
- `./scripts/verify_functional_e2e.sh` if the focused verification passes and
  the local environment supports the existing deterministic gate.

## Functional Smoke Matrix

| Feature or workflow | Caller surface | Safest data mode | Expected visible result | Command or action | Result | Evidence location | Blocker / substitute |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ContextWiki answer readability | focused pytest / local HTTP wrapper if needed | fake/temp test fixtures | grounded answer is structured and concise | `tests/search/test_answer_service.py`, `tests/web_console/test_app.py` | passed | Focused pytest passed; answer now emits structured summary + debug markdown | Browser UI smoke also passed in repo gate |
| Raw ContextWiki debug readability | focused pytest / local HTTP wrapper if needed | fake/temp test fixtures | debug output has fixed sections and stable ordering | focused tests around web console answer/debug payloads | passed | Focused pytest passed; console payload now prefers `debug_markdown` and preserves `summary` | Browser UI smoke also passed in repo gate |
| Retrieval alias expansion | search unit tests | fake/temp test fixtures | alias queries match intended evidence | `tests/search/test_context_service.py` | passed | New alias-variant retrieval regression passed in focused pytest | None |
| Existing answer insufficiency behavior | search unit tests | fake/temp test fixtures | weak/unrelated evidence still returns insufficient | focused tests | passed | Focused pytest passed | None |
| Broader repo functional gate | repo e2e script | deterministic local fake/temp paths | no regression in existing functional harness | `./scripts/verify_functional_e2e.sh` | passed | Fake wiki smoke passed; e2e/web console suite passed; Playwright smoke passed | None |

## Architecture and ADR Constraints

- Keep behavior in existing layers per ADR 0001: tool handlers delegate, search
  owns retrieval/answer orchestration, and persistence/indexing boundaries stay
  intact.
- Preserve ADR 0002: answers may only ground on metadata-store-backed chunks and
  should not inspect or mutate local user Chroma/SQLite state.
- Keep answer/debug improvements within `search/` and `web_console/` rather than
  pushing business logic into `api/tools.py`.

## Risks and Rollback Notes

- More aggressive alias expansion could increase false positives if too broad.
  Mitigation: keep the initial alias set deterministic and bounded, and cover it
  with focused tests.
- Structured answer summarization could hide useful detail if it compresses too
  much. Mitigation: preserve citations, used chunks, and raw debug visibility.
- Web console debug formatting may affect existing UI/test expectations.
  Mitigation: update focused tests alongside the implementation.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Fetched `origin/main` and created `feature/contextwiki-answer-retrieval-upgrade` from `origin/main` because local `main` is checked out in another linked worktree. | `git status --short --branch`; `git branch --show-current`; `git branch -vv`; `git worktree list`; `git fetch origin main`; `git switch -c feature/contextwiki-answer-retrieval-upgrade origin/main` |
| Planning | completed | Created the initial plan and recorded scope, verification, smoke matrix, and branch-safety rationale. | This plan |
| Worker orchestration | completed | User explicitly approved bypassing subagent worker orchestration for this task; main agent will implement directly and report review-loop bypass as required. | User message approving option 2 |
| Implementation | completed | Added shared query normalization/alias expansion, more generic focused/topic-only query variants, multi-variant retrieval probing, structured answer summaries, structured debug payloads, and web console debug wrapping. | `search/query_terms.py`; `search/context_service.py`; `search/answer_service.py`; `web_console/app.py`; `web_console/payloads.py`; focused tests |
| Focused verification | completed | Compile + focused answer/retrieval/web-console suites passed. | `python -m compileall ...`; `PYTHONPATH=. uv run pytest tests/search/test_answer_service.py tests/search/test_context_service.py tests/web_console/test_app.py` -> 213 passed, 1 warning |
| Functional smoke | completed | Repo functional E2E gate passed, including fake wiki smoke, connector/web console suites, and Playwright smoke. | `./scripts/verify_functional_e2e.sh` -> passed; fake wiki smoke generated `/private/tmp/contextwiki-wiki-smoke/fake-ContextWiki-citations.md`; Playwright smoke reported passed with filter + answer request and download checks |
| Docs update | completed | Updated core understanding note for structured answer/debug output and deterministic alias expansion. | `docs/contextwiki-core-understanding.md` |
| Follow-up fix for `neetcode 문제` | completed | Added generic problem-term normalization (`문제` -> `problem/question/solution` family), kept strong-anchor topical matching strict for specific topics like `graph`, and verified the live local server now grounds `neetcode 문제` instead of returning `No indexed evidence...`. | `tests/search/test_query_terms.py`; `tests/search/test_answer_service.py`; `tests/web_console/test_app.py`; local `POST /api/answer`; local `POST /api/answer/codex` |
| Broader generic hint normalization | completed | Expanded broad hint normalization beyond `문제` to shared usage/example/guide/setup/concept families and made those hints optional in non-strong-anchor answer grounding when a concrete anchor/topic already matches. Added focused regressions for `AWS 사용법`-style behavior. | `search/query_terms.py`; `search/answer_service.py`; `tests/search/test_query_terms.py`; `tests/search/test_answer_service.py`; `tests/web_console/test_app.py`; focused pytest `123 passed` |
| LLM rewrite + rerank follow-up | completed | Added optional OpenAI-backed low-confidence query rewrites, rewrite-aware candidate reranking, debug surfacing of retrieval/rewrite variants, and rewrite-aware answer/codex evidence gating so semantic rewrites can survive to final output. | `search/query_rewrite.py`; `search/context_service.py`; `search/answer_service.py`; `web_console/app.py`; `environments/config.py`; `tests/search/test_context_service.py`; `tests/search/test_answer_service.py`; `tests/web_console/test_app.py`; `tests/environments/test_config.py`; focused pytest `261 passed` |
| Review gate | completed | `$subagent-review-loop` was intentionally bypassed because the user explicitly approved single-agent execution for this task. | User message approving option 2 |
