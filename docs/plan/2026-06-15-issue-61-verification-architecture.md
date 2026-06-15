# Issue 61 Verification Architecture

## User request

- Work issue `#61`.
- Use the broader "approach 3" path: restructure verification architecture rather than only patching local gaps.
- Leave documentation behind.
- Implement directly in this session, then run review loops with three subagents per pass, and open a PR.

## Branch preflight result

- Date: `2026-06-15`
- Starting worktree state: clean detached HEAD at `0343fe8`.
- Freshness check: fetched `origin/main`, found remote advanced to `33d4f52`.
- Safety actions: switched to local `main`, fast-forwarded with `git pull --ff-only origin main`, then created `feature/issue-61-verification-architecture`.
- Current branch: `feature/issue-61-verification-architecture`

## Scope

- Reframe verification into clearer layers: public MCP contract, deterministic functional E2E, deterministic quality evals, and manual live smoke.
- Align local full-gate behavior with CI on the important trust-bearing dimensions.
- Add or strengthen true public-tool contract coverage through real `FastMCP.call_tool(...)` paths.
- Extend deterministic demo or quality coverage where the current story is still too happy-path or too ambiguous.
- Update README and supporting docs so verification claims are explicit about what is covered and what remains manual or out of scope.

## Non-goals

- Do not change retained MCP tool names, request shapes, or response contracts unless required for truthful testing.
- Do not introduce real live external API tests that need secrets unless an opt-in path is clearly isolated and still safe.
- Do not inspect or mutate user Chroma data or user SQLite metadata.
- Do not broaden product scope beyond retained MCP retrieval verification.

## Acceptance criteria

- `scripts/verify_all.sh` and `.github/workflows/ci.yml` express the same verification story for core dimensions, or document any intentional delta in a visible way.
- Public retained MCP tools have at least one true app-surface contract path through real `FastMCP.call_tool(...)`.
- The `live` policy is truthful: either real opt-in live pytest exists, or the marker/docs wording is revised so the project clearly distinguishes manual smoke from automated gates.
- Demo/eval/verification docs explain what each layer validates and what it does not validate.
- Negative-path or ambiguity gaps in the current demo/eval story are covered by deterministic tests where feasible.

## Step breakdown

1. `verification-inventory`
   - Read current gate, CI, contract-path tests, eval runner, and live/demo wording.
   - Decide the target verification layer map and file ownership.
2. `contract-and-gate-alignment`
   - Update local full gate and CI so they agree on compile, static checks, non-live tests, evals, and functional E2E sequencing.
   - Add or reorganize public MCP contract tests through real `FastMCP.call_tool(...)`.
3. `quality-lane-clarity`
   - Tighten demo/eval/manual-live boundaries.
   - Add deterministic negative-path or temp-Chroma quality coverage where it materially improves trust.
4. `docs-sync`
   - Update README and `docs/contextwiki-core-understanding.md` to match the new verification architecture.
5. `verification-and-review`
   - Run focused checks, full gate, functional smoke, then three-reviewer subagent loops per user instruction until clean.

## Files likely to change

- `scripts/verify_all.sh`
- `.github/workflows/ci.yml`
- `tests/api/test_tools_contract.py`
- `tests/test_app_composition.py`
- `tests/e2e/test_contextwiki_flow.py`
- `tests/scripts/test_demo_public_flow.py`
- `tests/scripts/test_live_query_smoke.py`
- `tests/evals/test_retrieval_quality.py`
- `tests/evals/test_answer_quality.py`
- `scripts/run_contextwiki_eval.py`
- `evals/README.md`
- `README.md`
- `docs/contextwiki-core-understanding.md`

## Test and verification plan

- Focused contract/eval/demo checks as relevant while iterating:
  - `uv run --locked pytest -q tests/test_app_composition.py tests/api/test_tools_contract.py`
  - `uv run --locked pytest -q tests/e2e/test_contextwiki_flow.py`
  - `uv run --locked pytest -q tests/scripts/test_demo_public_flow.py tests/scripts/test_live_query_smoke.py`
  - `uv run --locked pytest -q tests/evals`
  - `uv run --locked python scripts/run_contextwiki_eval.py --output-dir artifacts/contextwiki-evals --include-latency`
- Full code-changing gate before review:
  - `./scripts/verify_all.sh`
- Functional smoke gate before review:
  - `./scripts/verify_functional_e2e.sh`

## Functional smoke matrix

| Row | Surface | Mode | Expected outcome | Command / evidence |
| --- | --- | --- | --- | --- |
| Public tool contracts | `FastMCP.call_tool(...)` | temp/local deterministic | one retained contract path per public tool succeeds with JSON payloads | focused pytest |
| Deterministic functional E2E | retained sync/search/fetch/answer flows | temp/local deterministic | retained flows stay green after gate refactor | `./scripts/verify_functional_e2e.sh` |
| Quality evals | fixture retrieval + answer evals | temp/local deterministic | eval suite passes and artifact output stays structured | eval pytest + eval runner |
| Demo trust story | public demo scripts | temp/local deterministic | success and selected negative-path messaging stay truthful | focused pytest |
| Manual live smoke policy | local configured runtime | approval-free docs only | README/docs classify this as manual smoke, not full automated assurance | doc diff |

## Architecture / ADR constraints

- Follow `.agents/docs/architecture.md`.
- ADR `0001`: keep layered boundaries. Tool contract and app composition tests should not move business behavior into tool handlers.
- ADR `0006`: stay within slim retained MCP core scope. Verification changes must describe only retained sync/search/fetch/answer flows and configured source connectors.

## Risks and rollback notes

- Risk: over-refactoring test layers can blur the difference between contract tests and broader functional flows.
- Risk: CI/local alignment changes can accidentally make the gate weaker or slower without improving truthfulness.
- Risk: docs may overstate hermetic coverage if demo/eval/live boundaries are not phrased carefully.
- Rollback point: branch head before verification architecture edits; keep changes concentrated in scripts/tests/docs so rollback is contained.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Fetched latest `origin/main`, fast-forwarded local `main`, created `feature/issue-61-verification-architecture`. | `git fetch origin main`; `git pull --ff-only origin main`; `git switch -c feature/issue-61-verification-architecture` |
| Plan | completed | Created issue 61 plan for verification architecture restructuring. | `docs/plan/2026-06-15-issue-61-verification-architecture.md` |
| Verification inventory | completed | Mapped current gates to a four-layer structure: public MCP contracts, deterministic functional E2E, deterministic quality evals, and manual live smoke. | current session file reads across `scripts/`, `tests/`, `README.md`, `pyproject.toml`, and CI |
| Implementation | completed | Added a real `FastMCP.call_tool(...)` contract suite, aligned local/CI gate steps, clarified live-marker semantics, documented the four-layer verification architecture, added a demo insufficient-evidence regression test, and fixed `run_contextwiki_eval.py` direct CLI execution without `PYTHONPATH`. | modified scripts/tests/docs plus new `tests/contracts/test_public_mcp_contracts.py`, `tests/scripts/test_verification_architecture.py`, and `tests/scripts/test_run_contextwiki_eval.py` |
| Focused verification | completed | Targeted contract, app composition, E2E, demo coverage, live-smoke script contract coverage, eval tests, and direct eval runner checks all passed. | `uv run --locked pytest -q tests/contracts/test_public_mcp_contracts.py tests/scripts/test_verification_architecture.py tests/scripts/test_demo_public_flow.py`; `uv run --locked pytest -q tests/test_app_composition.py tests/api/test_tools_contract.py`; `uv run --locked pytest -q tests/e2e/test_contextwiki_flow.py`; `uv run --locked pytest -q tests/scripts/test_demo_public_flow.py tests/scripts/test_live_query_smoke.py`; `uv run --locked pytest -q tests/evals`; `uv run --locked pytest -q tests/scripts/test_run_contextwiki_eval.py`; `uv run --locked python scripts/run_contextwiki_eval.py --output-dir artifacts/contextwiki-evals --include-latency` |
| Functional smoke | completed | Retained functional E2E gate passed in local deterministic mode. | `./scripts/verify_functional_e2e.sh` -> `25 passed` |
| Review pass 1 findings | completed | Reviewer feedback found six actionable gaps: stale `PYTHONPATH` eval docs, weak demo negative-path assertion, missing default `debug={}` public contract check, eval README taxonomy drift, README ambiguity around script-vs-manual live smoke, and overclaiming around `IS_TESTING`/interpreter alignment plus overly broad functional E2E scope. | reviewer reports from `Meitner`, `Ptolemy`, and `Popper` |
| Review pass 1 fixes | completed | Updated eval docs and commands, tightened demo insufficient-answer assertion, added default `search_context` debug contract coverage, changed local compile to locked uv when healthy, narrowed functional E2E to true retained E2E modules, clarified README/live-smoke wording, and reframed `IS_TESTING` as current CI env-shape mirroring instead of a distinct runtime mode. | `uv run --locked pytest -q tests/contracts/test_public_mcp_contracts.py tests/scripts/test_demo_public_flow.py tests/scripts/test_verification_architecture.py tests/scripts/test_run_contextwiki_eval.py` passed; `./scripts/verify_all.sh` passed with `608 passed`; `./scripts/verify_functional_e2e.sh` passed with `25 passed` |
| Review pass 2 findings | completed | Reviewer feedback found three remaining actionable gaps: `docs/contextwiki-core-understanding.md` still had an older flat retained-check list, the public contract layer lacked the `search_context` `no_matching_sources` debug-path case, and the eval runner script test covered only `--help` instead of a real repo-root execution with artifact output. | reviewer reports from `Sartre`, `Rawls`, and `Kuhn` |
| Review pass 2 fixes | completed | Rewrote the maintained verification model section in `docs/contextwiki-core-understanding.md`, added the `no_matching_sources` real `FastMCP.call_tool(...)` contract assertion, and strengthened the eval runner script test to execute with `--output-dir` and assert generated artifacts without `PYTHONPATH`. | `uv run --locked pytest -q tests/contracts/test_public_mcp_contracts.py tests/scripts/test_run_contextwiki_eval.py tests/scripts/test_verification_architecture.py` passed; `./scripts/verify_all.sh` passed with `610 passed` and `25 passed` functional E2E layer |
| Review pass 3 fixes | completed | Corrected the remaining artifact/live-smoke wording drift in `README.md`, `docs/contextwiki-core-understanding.md`, and the plan log so docs match the actual CI artifact contents and script-vs-manual live smoke boundary. | `uv run --locked pytest -q tests/scripts/test_verification_architecture.py tests/scripts/test_run_contextwiki_eval.py tests/contracts/test_public_mcp_contracts.py` passed; `./scripts/verify_all.sh` passed with `610 passed` and `25 passed` functional E2E layer |
| Review loop | completed | Final fresh reviewer pass completed clean with the user-requested three-reviewer override instead of the repo-default five-reviewer pass count. | clean reviewers: `Franklin`, `Chandrasekhar`, `Russell` -> all reported no actionable findings |
| PR delivery | in_progress | Stage, commit, push, and create PR to `main`. | Pending |
