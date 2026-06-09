# Portfolio Hardening Case Study CI Refactor

## User request

Improve the repository as a portfolio-grade case study:

- Rewrite `README.md` around problem, architecture decisions, tradeoffs, results, seeded demo flow, verification evidence, privacy safety, stale chunk prevention, and evals.
- Add GitHub Actions CI for Python 3.13/uv, compile checks, JS syntax, non-live pytest, and fake smoke.
- Split oversized implementation files, especially `web_console/app.py` and `search/context_service.py`, into maintainable service/pipeline/ranking/redaction modules.
- Add professional lint/type/security/coverage gates and wire them into `scripts/verify_all.sh`.
- Clarify dependency source of truth between `pyproject.toml`, `uv.lock`, and `requirements.txt`.
- Simplify the Web Console demo UX around "Sync GitHub repo -> Ask question -> See citations -> Download wiki".
- Surface local eval coverage in the README.

## Branch preflight result

- Starting worktree: `/Users/eunhwa/.codex/worktrees/8bf8/MCPContentSearch`.
- Initial state: clean detached HEAD at `2cf3bc6`.
- Local `main` is checked out in `/Users/eunhwa/IdeaProjects/MCPContentSearch` and is behind `origin/main`; this linked worktree cannot switch to local `main` safely.
- Ran `git fetch origin main`; `FETCH_HEAD` and current detached HEAD were both `2cf3bc6`, matching `origin/main`.
- Existing non-main branches are linked to other worktrees or historical PR work; no local branch cleanup was performed to avoid deleting linked or local-only work.
- Created fresh branch `feature/portfolio-case-study-ci-refactor` from `origin/main`.
- Current state before target edits: `## feature/portfolio-case-study-ci-refactor...origin/main`.

## Scope and non-goals

In scope:

- Documentation, CI, verification script, dependency metadata, Web Console UI polish, and bounded refactors that preserve existing public behavior.
- New helper modules that reduce responsibility concentration without changing MCP tool names, endpoint paths, response shapes, or source sync semantics.
- Tests or compatibility shims needed to keep existing behavior verified after splitting modules.

Non-goals:

- No MCP tool contract changes.
- No live Notion, Tistory, GitHub, website/docs, or LLM validation without explicit approval.
- No inspection, deletion, reset, or migration of local user ChromaDB or SQLite metadata.
- No broad rewrite of retrieval quality algorithms beyond extracting current responsibilities into smaller modules.
- No use of the stale `feature/project-improvement-sweep` branch as a source of truth; it deletes many current files and is unsuitable as a base.

## Acceptance criteria

- `README.md` reads like a case study and explains:
  - Problem and product outcome.
  - Why SQLite plus Chroma.
  - How tombstones and SQLite active-chunk gates prevent stale citations.
  - Why private data is safer by default.
  - Seeded demo flow with exact commands.
  - Verification, eval, and CI evidence.
- `.github/workflows/ci.yml` exists and runs at least:
  - `uv sync --locked --python 3.13 --dev`
  - `uv run --locked python -m compileall api core environments fetching indexing search storage wiki web_console main.py`
  - `node --check web/app.js`
  - `uv run --locked pytest -m "not live"`
  - fake wiki smoke when dependencies are ready.
- `scripts/verify_all.sh` runs professional local gates before functional E2E:
  - Python compile.
  - JS syntax.
  - `ruff check`.
  - a type gate using `mypy` or `pyright`.
  - a security gate using `bandit` and/or `pip-audit`, with any network-sensitive audit documented or gated.
  - non-live pytest and coverage support.
- `pyproject.toml` contains the required dev tools and configuration needed for local/CI gates.
- `requirements.txt` clearly states whether it is an exported mirror or is removed if redundant. If kept, it must not present itself as a competing primary dependency source.
- `web_console/app.py` is reduced by moving target sync services, Codex answer service, and smoke runner into `web_console/services/` modules while preserving imports where tests rely on old symbols.
- `search/context_service.py` is reduced by moving retrieval pipeline, ranking, and debug redaction helpers into focused `search/` modules while preserving behavior and compatibility.
- Web Console UI presents a clearer demo path for first-time portfolio reviewers without removing configured-source sync, one-off target sync, source filters, citations, chunks, downloads, or progress polling.
- `docs/contextwiki-core-understanding.md` is updated if source sync, chunk lifecycle, citation metadata, retrieval, or answer behavior documentation must stay aligned.

## Step breakdown

1. Planning and ownership:
   - Confirm branch state, architecture, ADR constraints, and subagent availability.
   - Split work into disjoint worker scopes.
2. Documentation and CI/dependency gate:
   - README case study rewrite, eval exposure, seeded demo flow.
   - CI workflow and local verification gate updates.
   - Dev dependency and `requirements.txt` source-of-truth cleanup.
3. Web Console backend refactor:
   - Move target sync services, Codex answer service, and smoke runner out of route composition.
   - Keep `create_console_app`, request models, route contracts, and compatibility imports stable.
4. Search service refactor:
   - Move redaction helpers and ranking/retrieval helper functions into focused modules.
   - Keep `ContextSearchService.search_context` output unchanged.
5. Web Console UX polish:
   - Reframe controls around a visible demo workflow.
   - Keep existing endpoints and JS functions compatible with tests.
6. Verification, smoke, review, integration:
   - Run focused tests for changed modules.
   - Run `./scripts/verify_functional_e2e.sh` or document blockers.
   - Run five-reviewer subagent review loop until clean.
   - Commit, push, and open a `main`-base PR if all gates pass.

## Worker ownership plan

Delegation is available through `multi_agent_v1`. Because this is non-atomic, use role-specific workers after this plan exists and before non-plan target edits.

- Worker A, docs/CI/dependencies:
  - Owns `README.md`, `.github/workflows/ci.yml`, `pyproject.toml`, `requirements.txt`, `scripts/verify_all.sh`, `evals/README.md` if needed.
  - Must not edit application behavior files.
- Worker B, Web Console backend:
  - Owns `web_console/app.py`, `web_console/services/**`, and targeted `tests/web_console/test_app.py` compatibility updates.
  - Must preserve API routes, payload shapes, local-only safety, and redaction.
- Worker C, search retrieval maintainability:
  - Owns `search/context_service.py`, new `search/retrieval_pipeline.py`, `search/ranking.py`, `search/debug_redaction.py`, and targeted `tests/search/test_context_service.py`.
  - Must preserve retrieval output, stale chunk filtering through SQLite metadata, and debug redaction.
- Worker D, Web Console UX:
  - Owns `web/index.html`, `web/app.js`, `web/styles.css`, and browser-facing copy/layout only.
  - Must preserve configured sync vs one-off target sync functionality and download controls.
- Main agent:
  - Owns integration, conflict resolution, plan updates, focused verification, functional smoke matrix, review routing, staging, commit, push, and PR delivery.

## Files likely to change

- `README.md`
- `.github/workflows/ci.yml`
- `pyproject.toml`
- `requirements.txt`
- `scripts/verify_all.sh`
- `web_console/app.py`
- `web_console/services/__init__.py`
- `web_console/services/target_sync.py`
- `web_console/services/codex_answer.py`
- `web_console/services/smoke_runner.py`
- `search/context_service.py`
- `search/retrieval_pipeline.py`
- `search/ranking.py`
- `search/debug_redaction.py`
- `web/index.html`
- `web/app.js`
- `web/styles.css`
- `tests/web_console/test_app.py`
- `tests/search/test_context_service.py`
- `docs/contextwiki-core-understanding.md` if behavior docs need alignment
- `uv.lock`

## Test and verification plan

Focused checks:

- `python -m compileall api core environments fetching indexing search storage wiki web_console main.py`
- `node --check web/app.js`
- `uv run --locked pytest -q tests/web_console/test_app.py`
- `uv run --locked pytest -q tests/search/test_context_service.py`
- `uv run --locked pytest -q tests/evals`
- `uv run --locked pytest -m "not live"`

Professional gates:

- `uv run --locked ruff check api core environments fetching indexing search storage wiki web_console main.py`
- `uv run --locked mypy`
- `uv run --locked bandit -q -c pyproject.toml -r api core environments fetching indexing search storage wiki web_console main.py --severity-level medium --confidence-level low`
- `uv run --locked pytest --cov=api --cov=core --cov=environments --cov=fetching --cov=indexing --cov=search --cov=storage --cov=wiki --cov=web_console --cov-report=term-missing -m "not live"`

Functional smoke:

- `uv run --locked python scripts/smoke_generate_wiki_page.py --mode fake`
- `./scripts/verify_functional_e2e.sh`
- Web Console browser smoke through the existing Playwright script if feasible.

Fallback rules:

- If `uv` or dependency resolution is unavailable, record the failure and run the closest dependency-free checks.
- If Playwright browser binaries are missing and auto-install is blocked, record the blocker and nearest substitute.

## Functional smoke matrix

| Feature or workflow | Caller surface | Safe data mode | Expected result | Command/action | Planned result |
| --- | --- | --- | --- | --- | --- |
| CI/local gate commands | CLI | local repo only | compile, JS syntax, lint/type/security, pytest pass or documented blocker | `./scripts/verify_all.sh` | passed: full gate passed after Playwright smoke fix |
| Fake wiki generation | CLI/script | temp SQLite/Chroma and fake source | generated or deterministic fake smoke success | `uv run --locked python scripts/smoke_generate_wiki_page.py --mode fake` | passed through `./scripts/verify_all.sh` with temp storage |
| Context search/answer contracts | pytest | temp SQLite and fake chunks | existing tests pass with unchanged payloads | `uv run --locked pytest -q tests/search/test_context_service.py tests/web_console/test_app.py` | passed: 221 baseline, then 239 focused tests with evals |
| Evals | pytest/CLI | fixture temp state | grounding/retrieval checks pass | `uv run --locked pytest -q tests/evals` and `PYTHONPATH=. uv run --locked python scripts/run_contextwiki_eval.py` | passed in focused checks and full gate |
| Web Console first-run demo path | Browser/Playwright | local console with fake/temp paths | visible sync/answer/wiki/citation/download workflow remains navigable | existing Playwright smoke in functional E2E | passed after adding Build Wiki browser assertion |
| Configured-source sync | MCP/Web Console | fake/temp or existing deterministic tests | configured-source sync path remains separate from target sync | functional E2E/tests | passed through web console tests and Playwright configured-source sync click |
| One-off target sync | Web Console/API | fake or mocked service | target sync route still returns safe payload and progress state | web console tests | passed through web console tests and Playwright target sync click |
| Live external source sync | Live APIs | approval required | not run without explicit approval | n/a | blocked/gated |
| Local user Chroma/SQLite inspection or mutation | user data | approval required | not touched | n/a | blocked/gated |

## Architecture and ADR constraints

- ADR 0001: keep layered boundaries. Route handlers delegate to services; search refactors stay inside `search/`; fetcher/indexer behavior remains in existing layers.
- ADR 0002: SQLite is the citation metadata source of truth. `answer_with_citations` and ContextWiki retrieval may cite only metadata-backed chunks.
- ADR 0003: stale chunks must be filtered through SQLite active chunk/document gates. Refactors must preserve tombstone behavior and source-aware chunk metadata.
- ADR 0004: configured GitHub/Web connector sync and one-off target sync must remain distinct, with stale cleanup disabled for one-off target sync.
- ADR 0005: LLM synthesis remains opt-in; docs and UI must not imply private content is sent externally by default.

## Risks and rollback notes

- Refactoring `ContextSearchService` is high risk because retrieval ranking is heavily tested and subtle. Rollback point is the pre-refactor branch head after docs/CI changes, and focused search tests must pass after every extraction.
- Refactoring `web_console/app.py` can break route-level imports used by tests. Preserve compatibility exports from `web_console.app` unless tests and docs are deliberately updated.
- Adding mypy/pyright and security gates can reveal broad pre-existing debt. Use a scoped, honest gate rather than disabling meaningful checks globally.
- `pip-audit` can be network or advisory-feed sensitive. If included, gate it with a clear env flag or document it as optional unless it is stable locally and in CI.
- No local user data rollback is needed because planned tests use fake/temp stores only.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Skill and harness read | completed | Read harness engineering, GitHub workflow, architecture, ADR index, relevant accepted ADRs, and phase skills. | `sed -n ... .agents/...` |
| Memory pass | completed | Checked MCPContentSearch memory for prior README, eval, uv, and harness context. | `/Users/eunhwa/.codex/memories/MEMORY.md` hits |
| Branch preflight | completed | Started clean detached, fetched `origin/main`, created `feature/portfolio-case-study-ci-refactor` from `origin/main`, skipped unsafe branch cleanup. | `git status --short --branch`, `git fetch origin main`, `git switch -c ... origin/main` |
| Plan document | completed | Created this plan before non-plan target edits. | `docs/plan/2026-06-09-portfolio-hardening-case-study-ci-refactor.md` |
| Subagent discovery | completed | Found `multi_agent_v1` delegation tools. | `tool_search` for subagent tools |
| Worker dispatch | completed | Dispatched four bounded workers: docs/CI/deps, Web Console backend services, search retrieval modules, and Web Console UX. | `multi_agent_v1.spawn_agent` ids `019eabf9-b638-77a2-8af8-c32a2672b8a6`, `019eabf9-b838-7772-b082-ca69c5ee64cb`, `019eabf9-ba00-7b62-a235-4cf4004bd354`, `019eabf9-bbe2-7a12-b34d-f9eb7d5c6bc2` |
| Baseline focused verification | completed | Pre-change compile, JS syntax, and focused search/web console tests pass. | `python -m compileall ...` passed; `node --check web/app.js` passed; `uv run pytest -q tests/search/test_context_service.py tests/web_console/test_app.py` passed: 221 tests, 1 warning |
| Implementation | completed | Integrated worker patches, restored README demo screenshots and CI badge, aligned Web Console test contract with the new portfolio flow, expanded scoped mypy files, added `web_console` to Ruff gate, and updated the ContextWiki understanding note for new search modules. | Worker final reports plus main-agent integration edits |
| Focused verification | completed | Compile, JS syntax, focused search/web console/eval tests, Ruff, scoped mypy, Bandit, and whitespace checks pass. | `python -m compileall ...` passed; `node --check web/app.js` passed; `uv run pytest -q tests/search/test_context_service.py tests/web_console/test_app.py tests/evals` passed: 239 tests, 1 warning; `uv run ruff check ...` passed; `uv run mypy` passed; `uv run bandit ...` passed; `git diff --check` passed |
| Functional smoke | completed | Full verification gate passes after updating browser smoke for rendered markdown. Matrix rows covered local gates, fake wiki smoke, focused contracts, evals, configured-source sync, one-off target sync, downloads, and browser-safe error text. Live external sync and local user-data mutation remain blocked/gated. | `./scripts/verify_all.sh` passed: Ruff, mypy, Bandit, 764 non-live tests with coverage, fake wiki smoke, 193 functional E2E tests, and Playwright web console smoke |
| Middle review gate | completed | Completed the required five-reviewer loop after routing each actionable finding through fixes, affected verification, and fresh reviewer passes. The newest pass, pass 11, had all five reviewers report no actionable findings. | Final middle-review pass ids `019eac91-a553-7610-a7c8-95c3038e5ceb`, `019eac91-a771-7f12-963c-f5002cd2da55`, `019eac91-a963-7733-9cb0-561c2ae8d497`, `019eac91-ab88-74e3-a0eb-06b95028e05b`, `019eac91-ae38-7330-bfac-ad7a22d0c273` |
| Review pass 1 fixes | completed | Addressed actionable findings: portable temp roots for fake/Web Console smoke and uv cache, locked CI sync, coverage fail-under gate, expanded mypy scope, visible Build Wiki markdown plus Playwright assertion, stale Web Console wiki docs, and completed smoke matrix rows. | Targeted checks passed plus full gate passed: `./scripts/verify_all.sh` passed with Ruff, mypy, Bandit, 764 non-live tests with coverage, fake wiki smoke, 193 functional E2E tests, and Playwright Web Console smoke |
| Review pass 2 findings | completed | Fresh five-reviewer pass found actionable gaps: wiki insufficient-evidence UX, wiki download smoke coverage, Codex CLI temp portability, standalone functional E2E uv cache portability, CI professional gates, README evidence numbers, and cached diff check still pending until staging. Badge 404 was evaluated as expected before the new workflow lands on `main`, not a local code fix. | Reviewer ids `019eac18-1271-7980-80ec-da963c5913b7`, `019eac18-1472-7bb3-bc41-b7386c913155`, `019eac18-1676-7033-8de8-23744483538d`, `019eac18-1864-7cb1-80bf-a3b1d7ef09f7`, `019eac18-1a54-7a62-9fe9-17465c31b177` |
| Review pass 2 fixes | completed | Added wiki status-aware result copy/download labels, wiki download Playwright assertions, portable Codex CLI temp roots, portable standalone E2E uv cache defaults, hard-fail behavior when `verify_all.sh` cannot run professional uv gates, CI Ruff/mypy/Bandit/coverage steps, scoped mypy coverage for `web_console/codex_cli.py`, and README latest verification evidence. | Targeted checks passed, then `./scripts/verify_all.sh` passed with Ruff, mypy over 11 files, Bandit, 764 non-live tests with 74.31% coverage, fake wiki smoke, 194 functional E2E tests, and Playwright Web Console smoke including wiki downloads |
| Review pass 3 findings | completed | Fresh five-reviewer pass found actionable dependency/status gaps: `verify_all.sh` could probe with `uv run` before lock check, README Bandit command missed CI severity/confidence flags, Web Console treated configuration/insufficient/skipped outcomes as completed, and CI did not run Web Console contract tests. Three reviewers reported no actionable findings. | Reviewer ids `019eac27-5ab7-7be1-b190-b90d28e8a1fc`, `019eac27-5ca6-7e23-82d0-eab1a5c9715e`, `019eac27-5ebe-7cb0-8de4-86724b5134b8`, `019eac27-60a0-77f0-9830-049cf71a5410`, `019eac27-62c8-72d3-b27d-14606964d3a0` |
| Review pass 3 fixes | completed | Moved local lock check ahead of `uv run`, used locked uv runs in local/CI gates, aligned README Bandit flags with CI, added CI Web Console contract tests, and added Web Console status handling/tests for configuration, insufficient evidence, and skipped sync states. | Targeted checks passed, then `./scripts/verify_all.sh` passed with Ruff, mypy over 11 files, Bandit, 764 non-live tests with 74.31% coverage, fake wiki smoke, 195 functional E2E tests, and Playwright Web Console smoke including wiki downloads |
| Review pass 4 findings | completed | Fresh five-reviewer pass found actionable gaps: coverage artifacts not ignored, standalone functional E2E still using unlocked uv, CI Web Console tests outside coverage run, and result meta labels still saying ready for non-success answer/sync states. One retrieval reviewer and one broad reviewer reported no actionable findings. | Reviewer ids `019eac31-846c-7560-9493-7d15817b0878`, `019eac31-8709-7003-b54e-0c1fe2b26411`, `019eac31-890c-7132-8324-b7a753bf0896`, `019eac31-8af6-7220-be08-11390ccd79d9`, `019eac31-8d53-7933-995b-1060f8a6130c` |
| Review pass 4 fixes | completed | Added coverage artifact ignores, locked standalone functional E2E uv probes/runs, included Web Console tests in the CI/local coverage run, and made result meta labels reflect configuration, insufficient-evidence, failed, and skipped states. | Targeted checks passed, standalone `./scripts/verify_functional_e2e.sh` passed with locked uv, then `./scripts/verify_all.sh` passed with Ruff, mypy over 11 files, Bandit, 868 non-live tests with 84.49% coverage, fake wiki smoke, 195 functional E2E tests, and Playwright Web Console smoke including wiki downloads |
| Review pass 5 findings | completed | Fresh five-reviewer pass found actionable gaps: README demo/focused commands still used unlocked uv, Web Console README command did not disable startup auto-sync or state default local-store writes, and Playwright configured-source sync smoke only checked the early label instead of terminal completion. CI badge 404 was evaluated as expected before the new workflow lands on `main`, not a local code fix. Two reviewers reported no actionable findings. | Reviewer ids `019eac3c-84cd-7b00-80db-e391edd469b9`, `019eac3c-86b6-7423-a6b8-2473b4376b24`, `019eac3c-88ec-7272-8ffd-63e5a54c94fe`, `019eac3c-8abb-7ad2-9db2-a77e22ea8e16`, `019eac3c-8cd2-7580-b081-ade3e4b041bb` |
| Review pass 5 fixes | completed | Updated README demo/focused commands to locked uv, disabled startup auto-sync in the Web Console demo command, documented default local-store writes for manual live sync, and made Playwright configured-source sync wait for terminal completion plus assert fake ingestion received `source_github`. | Targeted checks passed, then `./scripts/verify_all.sh` passed with Ruff, mypy over 11 files, Bandit, 868 non-live tests with 84.49% coverage, fake wiki smoke, 195 functional E2E tests, and Playwright Web Console smoke including terminal configured-source sync completion |
| Review pass 6 findings | completed | Fresh five-reviewer pass found actionable gaps: result meta labels still showed ready for running/already-running sync states, and lowercase technical queries with a full but irrelevant vector window could skip all-source non-GitHub metadata recovery. Three reviewers reported no actionable findings. | Reviewer ids `019eac49-1820-7911-ad99-31ee2a68466d`, `019eac49-1a39-7570-ae40-ceaf72ab30f4`, `019eac49-1c3c-75a1-82ac-123b4e09c363`, `019eac49-1e5e-7302-b350-23ee9ae5cbcb`, `019eac49-207e-7df0-a37d-d2d2991c7efe` |
| Review pass 6 fixes | completed | Added running/already-running/succeeded result meta labels and tests, and allowed lowercase all-source metadata recovery when existing candidates lack textual matches even if the vector window is full. Added regression coverage for a full irrelevant vector window plus a non-GitHub body match. | Targeted checks passed, then `./scripts/verify_all.sh` passed with Ruff, mypy over 11 files, Bandit, 869 non-live tests with 84.49% coverage, fake wiki smoke, 195 functional E2E tests, and Playwright Web Console smoke |
| Review pass 7 findings | completed | Fresh five-reviewer pass found actionable gaps: ContextWiki eval commands still used unlocked forms, CI did not run the full deterministic functional gate, CI uv/cache setup could drift, Ruff/mypy caches were not ignored, source-list and active sync-status UI errors were hidden, and Playwright did not assert visible citations. Two reviewers reported no actionable findings. | Reviewer ids `019eac58-af1f-7b32-a6f1-ffff65799490`, `019eac59-633a-7460-9d4b-bef4e117f88a`, `019eac59-657f-7802-87e5-fb50c64b1fcf`, `019eac59-6770-7b91-ba4d-989163bbb1a4`, `019eac59-699b-78b2-b6e5-f6dc1555c609` |
| Review pass 7 fixes | completed | Locked ContextWiki eval commands, pinned/setup `uv` in CI with runner temp cache, ran CI functional E2E through `verify_functional_e2e.sh`, ignored Ruff/mypy caches, surfaced source-listing and active sync-status structured errors in the browser UI, and asserted visible citations in Playwright smoke. | Red-green JS regressions passed, `tests/web_console/test_app.py` passed: 106 tests, Playwright Web Console smoke passed with visible citations, then `./scripts/verify_all.sh` passed with Ruff, mypy over 11 files, Bandit, 871 non-live tests with 84.49% coverage, fake wiki smoke, 197 functional E2E tests, and Playwright Web Console smoke |
| Review pass 8 findings | completed | Fresh five-reviewer pass found actionable gaps: README still showed the old CI tail commands, Playwright missing-package hint used unlocked `uv sync`, standalone functional E2E could false-green on system Python fallback, and Bandit `B608` was globally skipped. Three reviewers reported no actionable findings. | Reviewer ids `019eac68-b5d7-7522-a56e-be1f1740cec3`, `019eac68-b7db-7763-8d1e-5ef59b326eb2`, `019eac68-b9c1-79a3-8276-b444b181200f`, `019eac68-bbee-7c52-93b6-278e3b873f00`, `019eac68-be7a-7913-a578-8b5a4edc82c8` |
| Review pass 8 fixes | completed | Updated README CI commands to call `verify_functional_e2e.sh`, documented its fake wiki/E2E/Web Console/Playwright coverage, changed the Playwright package hint to locked uv sync, made unlocked functional E2E fallback fail unless explicitly allowed, removed global `B608` skip, and moved safe dynamic SQL fragments into named query variables with rationale comments. | Targeted Bandit passed without `B608` skip, storage/search tests passed: 164 tests, standalone `./scripts/verify_functional_e2e.sh` passed with locked uv, then `./scripts/verify_all.sh` passed with Ruff, mypy over 11 files, Bandit, 871 non-live tests with 84.50% coverage, fake wiki smoke, 197 functional E2E tests, and Playwright Web Console smoke |
| Review pass 9 findings | completed | Fresh five-reviewer pass found actionable gaps: Bandit used medium confidence and would miss low-confidence B608 dynamic-SQL patterns, Playwright smoke could miss target-sync/source-sync mixups and stale answer citations, required new files were still untracked, the plan had one stale unlocked command, and GitHub one-off target sync stale-running recovery needed explicit coverage. One reviewer reported no actionable findings for search/storage. | Reviewer ids `019eac73-f326-7790-b76e-e768b97674de`, `019eac73-f53f-7573-b21b-3a62871e6622`, `019eac73-f714-7843-b5a5-e99aedc5c149`, `019eac73-f954-7510-9d67-93a1eeb89016`, `019eac73-fd14-7691-b107-68ba7bc53f56` |
| Review pass 9 fixes | completed | Raised Bandit gate to low confidence in local/CI/README, rewrote safe dynamic SQL fragments to avoid B608 warnings without global skip, tightened Playwright smoke with distinct wiki citations plus exact target-sync call assertions, updated remaining plan command drift, and added a real MetadataStore regression proving stale GitHub target-sync running jobs recover before target sync proceeds. | Targeted Bandit passed with low confidence, GitHub stale-running regression passed, Playwright smoke passed, focused Web Console/storage/search tests passed: 271 tests, Ruff/mypy/JS syntax passed, then `./scripts/verify_all.sh` passed with Ruff, mypy over 11 files, Bandit low-confidence gate, 872 non-live tests with 84.74% coverage, fake wiki smoke, 198 functional E2E tests, and Playwright Web Console smoke |
| Review pass 10 findings | completed | Fresh five-reviewer pass found one actionable docs drift: plan acceptance criteria still showed unlocked CI bootstrap commands. Four reviewers reported no actionable findings across Web Console, search/storage, verification, and integration lenses. | Reviewer ids `019eac86-5aac-7850-a1be-9efc6195e6d5`, `019eac86-5ca0-73e1-b228-0a2470489d39`, `019eac86-5e8d-7bd0-a163-708a73d4ad67`, `019eac86-6031-7440-b2c7-2eca21ea9007`, `019eac86-625a-7712-ba78-c69005041d70` |
| Review pass 10 fixes | completed | Updated the plan acceptance criteria to use locked uv sync and locked compile commands, matching README and CI. | `git diff --check` passed; `git diff --cached --check` passed after restaging |
| Review pass 11 | completed | Fresh five-reviewer pass after pass 10 docs-only fix found no actionable findings across docs/CI/dependency, Web Console, retrieval/storage, verification, and integration lenses. | Reviewer ids `019eac91-a553-7610-a7c8-95c3038e5ceb`, `019eac91-a771-7f12-963c-f5002cd2da55`, `019eac91-a963-7733-9cb0-561c2ae8d497`, `019eac91-ab88-74e3-a0eb-06b95028e05b`, `019eac91-ae38-7330-bfac-ad7a22d0c273` |
| Refactor/integration | completed | Re-read refactor/integration phase guidance, inspected the staged diff, and found no additional cleanup that would reduce complexity without adding churn. Functional smoke matrix remains current and live/user-data checks remain gated. | `git status --short --branch`, `git diff --cached --stat`; latest full gate remains `./scripts/verify_all.sh` passed |
| Final review gate | completed | Final fresh five-reviewer pass reported no actionable findings across docs/CI/dependency, Web Console contract, retrieval/storage, verification, and integration/readiness lenses. | Reviewer ids `019eac99-4c8e-74c3-b4d2-fb4ff3081248`, `019eac99-4ebb-7c03-8a82-bb6a307fec2c`, `019eac99-508a-7ed2-b5f3-3b55dda93000`, `019eac99-52a1-7293-aea5-b130aa3ddf66`, `019eac99-587e-7ae3-9838-cdafcc6828bb` |
| PR delivery | in_progress | Commit, push, and open PR after clean final gate. | Pending commit, push, and PR URL |
