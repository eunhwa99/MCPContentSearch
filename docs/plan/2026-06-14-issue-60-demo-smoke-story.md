# Issue 60 Demo Smoke Story

## User Request

- 60번 이슈 작업
- 인라인 진행
- 완료 후 서브에이전트 루프 리뷰 수행
- PR 생성

## Branch Preflight Result

- Original worktree: `/Users/eunhwa/IdeaProjects/MCPContentSearch`
- Original branch: `feature/readme-rewrite-cleanup`
- Original worktree state: dirty; preserved without branch switching or cleanup
- Freshness: `git fetch origin main` completed on `2026-06-14`
- Isolated worktree: `/Users/eunhwa/IdeaProjects/MCPContentSearch/.worktrees/issue-60-demo-smoke-story`
- Task branch: `feature/issue-60-demo-smoke-story` from `origin/main`
- Inline implementation reason: this task spans scripts, tests, and README, so it is not atomic. Repository harness would normally require worker orchestration, but explicit user approval was given to proceed inline first and still run the required five-reviewer subagent review loop before PR delivery.

## Scope

- Define one canonical reviewer-friendly public demo path and one aligned non-canonical live smoke validation path.
- Remove misleading wording that can make separate retrieval and answer inputs look like one validated chain.
- Align script defaults, transcript text, and README examples around the same honest contract.
- Add or update focused tests for the primary happy path and important negative-path messaging.

## Non-Goals

- Do not change retained MCP tool contracts.
- Do not add new connectors, new demo surfaces, or broader portfolio breadth.
- Do not touch user Chroma or SQLite data.
- Do not introduce live external-source verification.

## Acceptance Criteria

- One documented canonical public demo path exists and is easy for reviewers to understand quickly.
- The live smoke is documented as an aligned same-input validation path, not a second canonical reviewer path.
- `scripts/live_query_smoke.py` no longer implies a single end-to-end chain while running unrelated inputs.
- README examples and narrative use the same canonical flow and contract language as the scripts.
- Focused tests cover the main happy path and important “query vs question” messaging.

## Step Breakdown

1. Lock the canonical public demo path, then identify mismatched wording/defaults in the existing demo and smoke scripts plus README.
2. Add or update failing tests for transcript wording, default argument behavior, and mismatch messaging.
3. Implement the minimal script and README changes to center one honest end-to-end path.
4. Run focused script tests, then broader compile/functional verification.
5. Record functional smoke matrix results, run the five-reviewer subagent loop until clean, then commit, push, and open a PR.

## Files Likely To Change

- `scripts/live_query_smoke.py`
- `scripts/demo_public_flow.py`
- `README.md`
- `tests/scripts/test_live_query_smoke.py`
- `tests/scripts/test_demo_public_flow.py`
- `docs/plan/2026-06-14-issue-60-demo-smoke-story.md`

## Test And Verification Plan

- Focused TDD:
  - `uv run --locked pytest tests/scripts/test_demo_public_flow.py tests/scripts/test_live_query_smoke.py -q`
- Syntax safety:
  - `uv run python -m compileall api core environments fetching indexing search storage main.py`
- Repo functional gate:
  - `./scripts/verify_functional_e2e.sh`

## Functional Smoke Matrix

| Feature or Workflow | Caller Surface | Safest Data Mode | Expected Result | Command | Result | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Public canonical demo flow | `scripts/demo.sh` / `scripts/demo_public_flow.py` | bundled `sample_vault`, temp SQLite/Chroma, `MockEmbedding` | Sync, search, and helper answer preview all succeed with aligned transcript wording | `uv run --locked pytest -q tests/scripts/test_demo_public_flow.py` | passed | Latest focused rerun `29 passed in 12.63s`, including `./scripts/demo.sh` default and mismatch text-mode transcript coverage plus normalized timestamp assertions |
| Live query smoke same-input path | `scripts/live_query_smoke.py` | mocked/stubbed MCP tools in focused tests | Default `question=args.query`; summary describes the aligned same-input smoke path without overstating it as the canonical reviewer path | `uv run --locked pytest -q tests/scripts/test_live_query_smoke.py` | passed | Latest focused rerun `29 passed in 12.63s`, including omitted-question CLI wiring through `main()` in both text and JSON modes |
| Live query smoke mismatch honesty | `scripts/live_query_smoke.py` | mocked/stubbed MCP tools in focused tests | When `--question` differs, summary clearly states retrieval and answer are different probes, even if redacted display strings collide, including JSON mode via `same_input` metadata | `uv run --locked pytest -q tests/scripts/test_live_query_smoke.py` | passed | Latest focused rerun `29 passed in 12.63s`, including explicit mismatch CLI wiring, redaction-collision coverage, and JSON `same_input` marker coverage |
| README/demo contract alignment | README examples and script docs | docs-plus-focused regression assertions | Commands and wording match canonical path and warn correctly about helper preview vs full answer | `uv run --locked pytest -q tests/scripts/test_demo_public_flow.py` | passed | Latest focused rerun `29 passed in 12.63s`, including positive and negative README contract phrase assertions plus canonical example and keyless-path coverage |
| Retained ContextWiki functional regression | `verify_functional_e2e.sh` | temp/fake deterministic harness data | Existing retained sync/search/fetch/answer workflows still pass | `./scripts/verify_functional_e2e.sh` | passed | `355 passed in 18.70s` |

## Architecture And ADR Constraints

- Follow `.agents/docs/architecture.md` retained MCP boundaries.
- Respect ADR `0006`: stay within slim MCP core scope and retained demo/smoke surfaces.
- Keep `answer_with_citations` framed as a helper grounded preview rather than the full downstream user answer.
- Do not change MCP tool names, parameters, or return shapes.

## Risks And Rollback Notes

- Risk: documentation or transcript wording could still overclaim a single chain where there are actually separate probes.
- Risk: over-correcting wording could make the public demo less useful or obscure the answer helper purpose.
- Rollback: revert only the demo/smoke/README wording and test changes on this feature branch; no data migration or local storage mutation is involved.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Preserved dirty original worktree and created isolated worktree/branch from `origin/main`. | `git status --short`, `git fetch origin main`, `git worktree add -b feature/issue-60-demo-smoke-story ... origin/main` |
| Planning | completed | Captured scope, acceptance criteria, verification, and smoke matrix for issue 60. | This plan document |
| Focused tests | completed | Added failing expectations for canonical reviewer demo wording, live same-input smoke wording, redaction-collision honesty, CLI omission wiring, JSON honesty, help text, README contract phrases, canonical example coverage, real `./scripts/demo.sh` text-mode coverage for both canonical and mismatch paths, normalized timestamp stability, live-smoke JSON `same_input` metadata, and live-smoke sensitivity wording; then brought them green. | Initial `4 failed, 10 passed`; latest rerun `29 passed in 12.63s` |
| Implementation | completed | Updated demo/smoke scripts and README so only the public demo is canonical, live smoke is an aligned same-input validation path, and same-vs-separate detection uses raw inputs. | Diff in `scripts/demo_public_flow.py`, `scripts/live_query_smoke.py`, `README.md` |
| Focused verification | completed | Script-focused pytest and compile check both passed after the latest review-driven fixes. | `29 passed in 12.63s`; `uv run python -m compileall ...` passed |
| Functional smoke | completed | Retained functional E2E regression gate passed again after the review-driven fixes, and smoke matrix rows were refreshed. | `./scripts/verify_functional_e2e.sh` -> `355 passed in 18.53s` |
| Review gate | completed | Final fresh five-reviewer pass reported no actionable findings after the latest README wording-tightening update. | Five fresh reviewers: no actionable findings |
| PR delivery | pending | Commit, push, and create PR to `main`. | Pending |
