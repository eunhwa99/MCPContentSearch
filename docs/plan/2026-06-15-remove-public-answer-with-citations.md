# Remove Public answer_with_citations

## User request

- Remove `answer_with_citations` from the public MCP surface.
- Keep the internal helper/eval path so demo, smoke, and eval flows can still use `CitationAnswerService`.

## Branch preflight result

- Current branch: `feature/remove-public-answer-with-citations`
- Starting worktree state: clean detached worktree at `/Users/eunhwa/.codex/worktrees/3d22/MCPContentSearch`
- Safety actions:
  - Fetched `origin/main` from the detached worktree.
  - Fast-forwarded `/Users/eunhwa/IdeaProjects/MCPContentSearch` `main` with `git pull --ff-only origin main` because `main` was checked out there.
  - Created `feature/remove-public-answer-with-citations` from `origin/main` in this worktree.
- Delegation note: this is a non-atomic contract/documentation/test/script change. The user explicitly approved inline execution after discussing the boundary, so worker-orchestration bypass is intentional for this task.

## Scope

- Remove the public FastMCP tool registration for `answer_with_citations`.
- Update MCP-facing contract tests, app composition tests, and E2E expectations so the retained public tool list excludes that tool.
- Preserve `search/answer_service.py` and service-level tests.
- Preserve demo/smoke/eval flows by switching them from MCP tool calls to direct `CitationAnswerService` calls.
- Update maintained docs to describe the retained public MCP surface and the internal helper role accurately.

## Non-goals

- Do not delete `CitationAnswerService`.
- Do not change `search_context` or `search_documents` ranking/grounding behavior.
- Do not remove eval coverage that uses grounded helper answers internally.
- Do not mutate local Chroma or SQLite user data outside existing temp-fixture flows.

## Acceptance criteria

- Public MCP registration no longer exposes `answer_with_citations`.
- Public contract and composition tests assert the retained MCP tool list without `answer_with_citations`.
- Demo, smoke, and eval code paths still produce grounded helper answers through direct `CitationAnswerService` usage.
- Architecture/README wording no longer advertises `answer_with_citations` as a public MCP tool.
- Focused verification passes for changed contract, service, script, and composition paths.

## Step breakdown

1. `public-contract-red`
   - Read MCP registration, composition, and contract tests.
   - Add or update tests so they fail while the tool is still publicly registered.
2. `public-surface-green`
   - Remove the FastMCP tool wrapper from `api/tools.py`.
   - Update app composition and MCP contract expectations.
3. `internal-helper-callers`
   - Reroute demo/smoke/eval callers from `mcp.tools["answer_with_citations"]` to direct `CitationAnswerService.answer_with_citations(...)`.
   - Keep their existing result shapes as stable as practical.
4. `docs-alignment`
   - Update `README.md` and `.agents/docs/architecture.md` so retained public MCP tools and internal helper responsibilities are accurate.
5. `verification`
   - Run focused pytest/compile checks for changed files.
   - Run the repo functional E2E gate if feasible; if blocked, record the blocker explicitly.

## Files likely to change

- `api/tools.py`
- `main.py` or composition tests if tool exposure expectations need updates
- `scripts/demo_public_flow.py`
- `scripts/live_query_smoke.py`
- `evals/contextwiki_eval.py`
- `tests/api/test_tools_contract.py`
- `tests/contracts/test_public_mcp_contracts.py`
- `tests/test_app_composition.py`
- `tests/e2e/test_contextwiki_flow.py`
- `tests/e2e/test_phase_b_connectors_flow.py`
- `tests/e2e/test_obsidian_connector_flow.py`
- `tests/scripts/test_live_query_smoke.py`
- `README.md`
- `.agents/docs/architecture.md`

## Test and verification plan

- Red test first on MCP public-tool expectations:
  - `uv run pytest -q tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py tests/test_app_composition.py -k 'answer_with_citations or registered_tools or tool_names'`
- Focused verification after implementation:
  - `uv run pytest -q tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py tests/test_app_composition.py`
  - `uv run pytest -q tests/search/test_answer_service.py tests/scripts/test_live_query_smoke.py tests/e2e/test_contextwiki_flow.py tests/e2e/test_phase_b_connectors_flow.py tests/e2e/test_obsidian_connector_flow.py`
  - `python -m compileall api core environments fetching indexing search storage main.py scripts evals`

## Functional smoke matrix

| Surface | Caller | Safe data mode | Expected result | Status |
| --- | --- | --- | --- | --- |
| Public MCP retrieval | local test MCP | temp fixture only | `search_context` and `search_documents` remain public; `answer_with_citations` absent | passed: `uv run pytest -q tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py tests/test_app_composition.py` |
| Internal helper answer | demo/smoke/eval services | temp fixture or existing local runtime flow | direct `CitationAnswerService` answer payload still works | passed: `uv run pytest -q tests/e2e/test_contextwiki_flow.py tests/e2e/test_phase_b_connectors_flow.py tests/e2e/test_obsidian_connector_flow.py tests/scripts/test_live_query_smoke.py tests/scripts/test_demo_public_flow.py` |
| Public source sync/search flow | retained E2E suite | temp fixture only | no regression in retained MCP flows after public-tool removal | passed: `./scripts/verify_functional_e2e.sh` |

## Architecture constraints

- Keep `search_context` as the primary chunk-level evidence surface.
- Keep `search_documents` as the grouped document-browsing surface.
- Keep `CitationAnswerService` as an internal helper built on validated retrieval evidence.
- Do not create a second retrieval stack or bypass SQLite-backed validation.

## Risks and rollback notes

- Risk: existing tests or scripts may assume public MCP tool presence indirectly.
- Risk: removing the wrapper may desynchronize docs or app composition expectations.
- Rollback point: revert only the public-tool removal patch and related expectation updates; internal service files should remain intact.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created `feature/remove-public-answer-with-citations` from `origin/main` after fetching and updating canonical `main`. | `git fetch origin main`; `git pull --ff-only origin main`; `git switch -c feature/remove-public-answer-with-citations origin/main` |
| Planning | completed | Locked scope to removing the public MCP wrapper while preserving internal helper/eval callers. | This plan document |
| Red tests | completed | Public MCP registration expectations were updated first and failed while the wrapper still existed. | `uv run pytest -q tests/api/test_tools_contract.py tests/contracts/test_public_mcp_contracts.py tests/test_app_composition.py tests/e2e/test_contextwiki_flow.py` -> 2 expected failures |
| Implementation | completed | Removed the public FastMCP wrapper, kept `CitationAnswerService`, and rerouted demo/smoke/E2E helper-answer callers to direct service calls. | `api/tools.py`; `scripts/demo_public_flow.py`; `scripts/live_query_smoke.py`; `tests/e2e/*`; `tests/scripts/test_live_query_smoke.py` |
| Docs alignment | completed | Updated README and maintained architecture wording to reflect the smaller public MCP surface and retained internal helper role. | `README.md`; `.agents/docs/architecture.md` |
| Verification | completed | Focused suite, compileall, functional E2E gate, and whitespace diff check passed. | `uv run pytest -q ...` -> `98 passed`; `python -m compileall api core environments fetching indexing search storage main.py scripts evals`; `./scripts/verify_functional_e2e.sh` -> `25 passed`; `git diff --check` |
| Review pass 1 | completed/actionable | Fresh reviewer pass found two documentation-only mismatches: maintained architecture still described a public MCP answer path in verification wording, and the functional smoke matrix rows in this plan were left pending after passing evidence existed. | Reviewer agents `Confucius`, `Kant`; no-actionable reviewers `Fermat`, `Euler` |
| Review pass 1 remediation | completed | Narrowed maintained architecture verification wording to public MCP sync/search/fetch plus internal helper-answer coverage, and marked the plan smoke matrix rows with passed evidence. | `.agents/docs/architecture.md`; `docs/plan/2026-06-15-remove-public-answer-with-citations.md` |
| Review pass 1 late findings | completed/actionable | Remaining reviewers found adjacent harness/ADR docs still treating `answer_with_citations` as a retained public MCP tool. | Reviewer agent `Dirac` |
| Review pass 1 late remediation | completed | Updated functional smoke matrix guidance, harness functional-smoke/test instructions, and ADR 0006 to distinguish retained public MCP retrieval tools from internal helper-answer coverage. | `.agents/docs/functional-smoke-matrix.md`; `.agents/skills/harness-functional-smoke/SKILL.md`; `.agents/skills/harness-test/SKILL.md`; `.agents/docs/adr/0006-slim-mcp-core-scope.md` |
| Review pass 2 | completed/actionable | Fresh final-pass review found one remaining ADR reference that still named `answer_with_citations` like a public surface. | Reviewer agent `Erdos` |
| Review pass 2 remediation | completed | Updated ADR 0002 so citation constraints refer to internal helper-answer flows instead of the removed public MCP tool. | `.agents/docs/adr/0002-contextwiki-metadata-and-citation-store.md` |
| Review pass 3 | completed/actionable | Fresh final-pass review found one remaining eval README wording reference that still named `answer_with_citations` without the internal-helper boundary. | Reviewer agent `Einstein` |
| Review pass 3 remediation | completed | Updated `evals/README.md` so eval guidance explicitly refers to the internal `CitationAnswerService.answer_with_citations(...)` helper path. | `evals/README.md` |
| Review pass 4 | completed/actionable | Fresh final-pass review preferred removing the symbol name from eval README entirely so only the agreed reviewer-facing surfaces mention the helper boundary explicitly. | Reviewer agent `Poincare` |
| Review pass 4 remediation | completed | Reworded `evals/README.md` to use generic internal helper-answer service wording without repeating the removed public tool name. | `evals/README.md` |
| Review pass 5 | completed/actionable | Fresh final-pass review preferred generalizing adjacent harness/eval wording further so only README, maintained architecture, and ADR 0006 explain the internal helper boundary explicitly. | Reviewer agent `Russell` |
| Review pass 5 remediation | completed | Generalized harness smoke/test docs, ADR 0002, and eval README to answer-coverage wording while keeping explicit internal-helper naming only in README, maintained architecture, and ADR 0006. | `.agents/docs/functional-smoke-matrix.md`; `.agents/skills/harness-functional-smoke/SKILL.md`; `.agents/skills/harness-test/SKILL.md`; `.agents/docs/adr/0002-contextwiki-metadata-and-citation-store.md`; `evals/README.md` |
| Review pass 6 | completed/actionable | Fresh final-pass review found harness inventory had accidentally dropped still-public MCP tools `sync_all` and `search_documents` while removing only `answer_with_citations`. | Reviewer agent `Ramanujan` |
| Review pass 6 remediation | completed | Restored `sync_all` and `search_documents` to harness functional-smoke/test public MCP inventories while keeping answer coverage generic and non-public. | `.agents/skills/harness-functional-smoke/SKILL.md`; `.agents/skills/harness-test/SKILL.md` |
| Review pass 7 | completed/actionable | Fresh final-pass review asked for the functional smoke matrix retrieval row to name the retained public MCP retrieval inventory explicitly rather than only saying “retrieval surfaces.” | Reviewer agent `Kuhn` |
| Review pass 7 remediation | completed | Expanded the functional smoke matrix retrieval row to name `sync_all`, `search_context`, `search_documents`, and `fetch_context` explicitly while keeping answer/eval coverage local-only. | `.agents/docs/functional-smoke-matrix.md` |
