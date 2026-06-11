# Slim MCP Core Plan

## User Request

Reduce the project back to its original intent: a small MCP server that can be
attached behind an LLM and retrieve indexed knowledge. Keep GitHub, Tistory, and
Notion behavior. Remove the broader website/docs crawler, dynamic web fallback,
Auto Wiki, Web Console, and old extra APIs that made the project too large.

Requested scope:

1. Remove website/docs and other generic web/API surfaces.
2. Remove Auto Wiki.
3. Remove or isolate the Web Console.
4. Rewrite the project framing around a smaller MCP server.

## Branch Preflight Result

- Starting worktree: `/Users/eunhwa/.codex/worktrees/c298/MCPContentSearch`.
- Initial state: clean detached HEAD at `62976ec`.
- `main` is checked out in linked worktree
  `/Users/eunhwa/IdeaProjects/MCPContentSearch`, so this worktree was not
  switched to local `main`.
- Ran `git fetch origin main`; `HEAD`, `FETCH_HEAD`, and `origin/main` all
  resolved to `62976ec613234bd02c9b2932b35b125804a5cd5d`.
- Existing non-main branches are linked to other worktrees or historical PR
  work; no local branch cleanup was performed to avoid deleting linked or
  local-only work.
- Created fresh branch `feature/slim-mcp-core` from `origin/main`.
- Current state before target edits: `## feature/slim-mcp-core...origin/main`.

## Scope and Non-Goals

In scope:

- Keep ContextWiki MCP source sync and retrieval for:
  - `source_github`
  - `source_notion`
  - `source_tistory`
- Keep SQLite-backed source/job/document/chunk metadata, tombstone behavior,
  Chroma indexing, `search_context`, `fetch_context`, and
  `answer_with_citations`.
- Remove generic website/docs ingestion and configuration:
  `source_web`, `CONTEXTWIKI_WEB_*`, `fetching/web_docs.py`,
  `fetching/web_media.py`, `fetching/web_safety.py`, and tests that only cover
  website/docs crawling.
- Remove legacy dynamic fallback and realtime search/indexing MCP tools:
  `search_content`, `search_notion`, `search_tistory`, `search_github`,
  `trigger_index_all_content`, and `get_index_status` unless still needed for
  an MCP startup compatibility test.
- Remove dead legacy local-search formatting code after the MCP tools no longer
  call it.
- Remove Auto Wiki runtime and contract:
  `generate_wiki_page`, `wiki/`, wiki LLM config, wiki smoke scripts, wiki
  tests, and related README/eval docs.
- Remove Web Console runtime, UI, services, scripts, tests, and CI/smoke gates.
- Update architecture docs, ADR index, and README to make the smaller product
  direction explicit.

Non-goals:

- No deletion, reset, migration, or inspection of local user ChromaDB or SQLite
  metadata.
- No live Notion, Tistory, GitHub, website/docs, or LLM validation.
- No new source connector.
- No broad rewrite of SQLite metadata, Chroma indexing, citation answer logic,
  or source identity rules beyond compatibility changes needed after removal.
- No replacement web UI in this change.

## Acceptance Criteria

- `main.py` composes only the MCP server, three-source registry, ingestion,
  ContextWiki search, citation answer, metadata store, and Chroma indexer.
- `api/tools.py` registers only the slim MCP tool surface:
  - `list_sources`
  - `sync_source`
  - `get_sync_status`
  - `search_context`
  - `fetch_context`
  - `answer_with_citations`
- The production source registry lists only GitHub, Notion, and Tistory.
- `SourceType.WEB`, website/docs fetchers, dynamic search fallback, Auto Wiki,
  and Web Console modules are removed or made unreachable from production code.
- Tests are updated so deterministic non-live verification covers the retained
  source sync/search/answer behavior without website/docs, wiki, or web UI
  dependencies.
- README describes the project as a focused MCP retrieval server rather than a
  Web Console/Auto Wiki case study.
- Architecture and ADR docs record that the project intentionally slimmed down
  and that ADR 0004/0005 are superseded where they added website/docs and Auto
  Wiki.
- No local user Chroma/SQLite data is mutated by verification.

## Step Breakdown

1. Planning and worker orchestration:
   - Record branch safety, ADR constraints, and subagent availability.
   - Split the removal by disjoint ownership areas.
2. Source/config/API contraction:
   - Remove website/docs source registration and config.
   - Reduce MCP tool registration to the retained ContextWiki tools.
   - Update application composition in `main.py`.
3. Runtime removal:
   - Remove dynamic search, legacy search/indexing helpers, Auto Wiki, Web
     Console, and script entrypoints.
   - Remove orphan imports and package references.
4. Tests and verification alignment:
   - Delete or rewrite tests that only validate removed features.
   - Keep focused tests for source registry, sync, search, fetch, answer, and
     metadata safety.
5. Documentation and ADR update:
   - Rewrite README and architecture for the slim surface.
   - Add ADR 0006 documenting the scope reduction and superseding website/docs
     and Auto Wiki decisions.
6. Integration and review:
   - Run compile, focused tests, deterministic functional smoke substitutes,
     full local verification where feasible, five-reviewer review loop, then
     commit, push, and create a main-base PR.

## Worker Ownership Plan

Delegation is available through `multi_agent_v1`; this work is not atomic, so
use role-specific workers after this plan exists and before non-plan target
edits. Workers must not commit, push, open PRs, inspect secrets, mutate local
Chroma/SQLite data, or revert other user/agent changes.

- Worker A, runtime/API contraction:
  - Owns `main.py`, `api/tools.py`, `fetching/connectors.py`,
    `environments/config.py`, `core/models.py`, and import compatibility needed
    in retained runtime modules.
  - Must preserve retained MCP tool names, parameters, and safe error behavior.
- Worker B, removal and test alignment:
  - Owns removal of `fetching/web_docs.py`, `fetching/web_media.py`,
    `fetching/web_safety.py`, `fetching/web_searcher.py`,
    `search/dynamic_search.py`, `wiki/**`, `web_console/**`, `web/**`, relevant
    scripts, and tests for removed features.
  - Must keep retained tests passing and avoid deleting source data paths.
- Worker C, docs/ADR/CI verification alignment:
  - Owns `README.md`, `.agents/docs/architecture.md`,
    `.agents/docs/adr/README.md`, new ADR 0006,
    `docs/contextwiki-core-understanding.md`, `.github/workflows/ci.yml`,
    `scripts/verify_all.sh`, `scripts/verify_functional_e2e.sh`, and
    `pyproject.toml` if needed.
  - Must keep docs synchronized with actual commands and retained modules.
- Main agent:
  - Owns integration, conflict resolution, diff inspection, plan updates,
    focused verification, functional smoke matrix, review routing, staging,
    commit, push, and PR delivery.

## Files Likely to Change

- `main.py`
- `api/tools.py`
- `core/models.py`
- `environments/config.py`
- `fetching/connectors.py`
- `fetching/fetcher.py`
- `search/service.py`
- `search/answer_service.py`
- `search/context_service.py`
- `storage/metadata_store.py`
- `.github/workflows/ci.yml`
- `scripts/verify_all.sh`
- `scripts/verify_functional_e2e.sh`
- `README.md`
- `.agents/docs/architecture.md`
- `.agents/docs/adr/README.md`
- `.agents/docs/adr/0006-slim-mcp-core-scope.md`
- `docs/contextwiki-core-understanding.md`
- `tests/**`
- Removed paths for website/docs, dynamic fallback, wiki, Web Console, web UI,
  and removed smoke scripts.

## Test and Verification Plan

Focused commands:

```bash
python -m compileall api core environments fetching indexing search storage main.py
uv run --locked pytest -q tests/fetching/test_connectors.py
uv run --locked pytest -q tests/api/test_tools_contract.py
uv run --locked pytest -q tests/e2e/test_contextwiki_flow.py
uv run --locked pytest -q tests/search/test_context_service.py tests/search/test_answer_service.py
uv run --locked pytest -q tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py
```

Broader commands:

```bash
uv run --locked pytest -m "not live"
./scripts/verify_all.sh
git diff --check
```

Fallback:

- If `uv` is unavailable or dependency metadata breaks after removals, record
  the failure and run dependency-free compile/import checks where useful.

## Functional Smoke Matrix

| Feature or workflow | Caller surface | Safe data mode | Expected result | Planned result |
| --- | --- | --- | --- | --- |
| MCP app startup/import | Python import/FastMCP composition | local repo only, no live credentials | `create_app()` can register retained tools without removed modules | passed: retained modules compile and tool contract tests register the slim surface |
| Source list/status | MCP handlers via tests | temp SQLite / fake registry | only GitHub, Notion, Tistory source ids appear | passed: connector/tool/e2e tests assert the three retained source ids |
| Source sync | MCP `sync_source` / E2E tests | fake/temp source data | retained sync path writes metadata/chunks and preserves tombstone gates | passed: fake GitHub/Notion/Tistory sync tests and ingestion metadata tests passed |
| Context search | MCP `search_context` / service tests | temp SQLite/fake vectors | active chunks hydrate through SQLite and stale vectors are suppressed | passed: context service/e2e tests passed with SQLite active-chunk gates |
| Citation answer | MCP `answer_with_citations` / service tests | temp SQLite/fake chunks | answer uses only cited evidence or returns insufficient evidence | passed: answer service tests passed after removing stale deleted-path fixture text |
| Removed website/docs source | import/tests/docs | no user data | `source_web` and `CONTEXTWIKI_WEB_*` are absent from production registry/docs | passed: production registry/config no longer includes website/docs source or config |
| Removed Auto Wiki | import/tests/docs | no LLM/live calls | `generate_wiki_page`, `wiki/`, and wiki smoke are absent | passed: runtime package, MCP tool, tests, smoke script, and CI references removed |
| Removed Web Console | file/docs/CI | no browser/live calls | Web Console scripts/tests/CI browser smoke are removed from verification | passed: `web_console/`, `web/`, browser smoke script, tests, CI/runtime docs removed |
| Live external APIs | live services | approval required | not run without explicit approval | blocked/gated |
| Local user Chroma/SQLite mutation | user data | approval required | not touched | blocked/gated |

## Architecture and ADR Constraints

- ADR 0001 still applies: keep tool contracts in `api`, fetching in `fetching`,
  indexing in `indexing`, search in `search`, config in `environments`, and
  composition in `main.py`.
- ADR 0002 still applies: SQLite remains the metadata/citation source of truth
  beside Chroma and raw secrets are not persisted.
- ADR 0003 still applies: document identity, tombstone behavior, source-aware
  chunking, and SQLite active chunk hydration remain intact.
- ADR 0004 is directly affected: GitHub remains, but website/docs connector
  scope is being intentionally removed.
- ADR 0005 is directly affected: Auto Wiki LLM synthesis is being removed.
- Add ADR 0006 to supersede the affected parts of ADR 0004/0005 and document
  the smaller long-term MCP surface.

## Risks and Rollback Notes

- Risk: removing broad tests may accidentally weaken coverage for retained
  metadata or connector behavior. Mitigation: keep focused tests around source
  registry, ingestion, context search, citation answer, and storage.
- Risk: references to removed modules may remain in CI, scripts, docs, or
  imports. Mitigation: compile all retained runtime modules and run `rg` checks
  for removed symbols.
- Risk: local Chroma may still contain old website/docs vectors. Mitigation:
  do not delete user data; retained citation-safe paths hydrate through SQLite
  and production code no longer creates `source_web`.
- Rollback: revert this branch/PR. No local data migration or destructive
  cleanup is part of this change.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Fetched `origin/main`, confirmed current commit matches, created `feature/slim-mcp-core`; linked worktree branches were preserved. | `git fetch origin main`; `git rev-parse HEAD origin/main FETCH_HEAD`; `git switch -c feature/slim-mcp-core origin/main` |
| Plan | completed | Created this plan with scoped removal, worker boundaries, verification, smoke matrix, and ADR constraints. | `docs/plan/2026-06-10-slim-mcp-core.md` |
| Subagent discovery | completed | Delegation/review tooling discovered before target edits. | `tool_search` exposed `multi_agent_v1` |
| Worker implementation | completed | Runtime/API, removal/tests, and docs/ADR/CI workers completed their bounded slices. | Worker A: compile/tool smoke/focused tests passed; Worker B: focused changed suite 63 passed; Worker C: full `./scripts/verify_all.sh` passed before main-agent integration cleanup |
| Integration | completed | Removed stale harness/runtime references, deleted unused legacy `search/service.py`, removed direct FastAPI/Playwright dependencies, refreshed `uv.lock`, and aligned retained tests. | `rg` for removed symbols; `uv lock`; `git diff --stat` |
| Focused verification | completed | Retained runtime modules compile, dependency lock is current, and focused source/tool/search/answer/storage/ingestion tests pass. | `python -m compileall api core environments fetching indexing search storage main.py`; `uv lock --check`; `uv run --locked pytest -q tests/fetching/test_connectors.py tests/api/test_tools_contract.py tests/e2e/test_contextwiki_flow.py tests/search/test_context_service.py tests/search/test_answer_service.py tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py` -> 242 passed |
| Functional smoke | completed | Local deterministic functional gate now exercises the retained MCP/source/search/answer inventory without browser/wiki/Web Console surfaces. | `./scripts/verify_functional_e2e.sh` -> 242 passed |
| Review pass 1 | completed/actionable | Five fresh reviewers found upgrade-safety and cleanup issues: legacy `source_web` SQLite rows can break or leak through MCP-visible paths; functional gate omits retained connector E2E; `requirements.txt`, Web Console auto-sync config, legacy fetcher/background registry, harness wording, ADR statuses, and optional search LLM docs need alignment. | Reviewers: Planck, Ramanujan, Pasteur, Euler, McClintock |
| Review remediation 1 | completed | Fixed legacy `source_web` upgrade safety without deleting data, constrained production search to registry source ids, filtered public MCP fetch/status/search paths, removed leftover Web Console deps/config/dead helpers, added connector E2E to the functional gate, and aligned ADR/docs/harness language including default-disabled search LLM egress. | Worker R1/R2 outputs; `rg` for removed runtime symbols |
| Post-remediation verification 1 | completed | Reran affected compile/tests, dependency lock check, functional smoke, and full local verification after review fixes. | `python -m compileall api core environments fetching indexing search storage main.py`; `uv run --locked pytest -q tests/api/test_tools_contract.py tests/storage/test_metadata_store.py tests/search/test_context_service.py tests/environments/test_config.py tests/indexing/test_background_tasks.py tests/e2e/test_phase_b_connectors_flow.py` -> 196 passed; `uv lock --check`; `./scripts/verify_functional_e2e.sh` -> 256 passed; `./scripts/verify_all.sh` -> Ruff passed, mypy passed, 433 non-live tests passed with 82.93% coverage, functional gate 256 passed; `git diff --check` |
| Review pass 2 | completed/actionable | Five fresh reviewers found remaining issues: hidden legacy-source status/recovery could still mutate old rows before filtering, mixed source filters should be sanitized before answer delegation, egress docs overclaimed fully local retrieval without embedding-provider caveat, active harness docs still named Web/target surfaces, maintained docs had stale `insufficient_evidence`, and unused `*Searcher` live keyword-search classes remained. | Reviewers: James, Hubble, Avicenna, Curie, Epicurus |
| Review remediation 2 | completed | Moved retained-source checks ahead of stateful status lookup, scoped startup orphan recovery to retained registry source ids, sanitized mixed source filters before answer/search delegation, added no-mutation regressions, removed unused Notion/Tistory/GitHub live searcher classes and searcher-only tests, and corrected egress/harness/docs wording. | `rg` for removed searcher/Web/target symbols; code diff inspection |
| Post-remediation verification 2 | completed | Reran focused API/storage/fetching regressions, functional smoke, and full local verification after pass 2 fixes. | `uv run --locked pytest -q tests/api/test_tools_contract.py tests/storage/test_metadata_store.py tests/fetching/test_notion.py tests/fetching/test_tistory.py tests/fetching/test_github.py` -> 159 passed; `./scripts/verify_functional_e2e.sh` -> 258 passed; `./scripts/verify_all.sh` -> Ruff passed, mypy passed, Bandit passed, 434 non-live tests passed with 84.81% coverage, functional gate 258 passed; `git diff --check` |
| Review pass 3 | completed/actionable | Five fresh reviewers found remaining issues: `sync_source` could echo secret-like unknown source ids in error text, Tistory had a non-empty default blog name, ADR/docs still needed clearer historical/utility framing for superseded web targets, and this plan had stale verification evidence. | Reviewers: Halley, Descartes, Poincare, Dirac, Ptolemy |
| Review remediation 3 | completed | Redacted `sync_source` failure text, disabled Tistory until an explicit blog name is configured, clarified superseded ADR 0004/0006 target-helper wording, and updated the plan evidence. | `uv run --locked pytest -q tests/api/test_tools_contract.py tests/fetching/test_connectors.py` -> 15 passed; `python -m compileall api environments fetching storage main.py tests/api/test_tools_contract.py tests/fetching/test_connectors.py`; `git diff --check` |
| Post-remediation verification 3 | completed | Reran the deterministic functional gate and full local verification after pass 3 fixes. | `./scripts/verify_functional_e2e.sh` -> 260 passed; `uv lock --check`; `./scripts/verify_all.sh` -> Ruff passed, mypy passed, Bandit passed, 436 non-live tests passed with 84.82% coverage, functional gate 260 passed |
| Review pass 4 | completed/actionable | Fresh review before remediation 4 found remaining docs/startup issues: ADR 0006 needed a clearer egress caveat, the old roadmap and ADR 0003 needed current-scope banners, and the functional gate needed an app composition smoke. | Pass 4 reviewer findings routed into `Review remediation 4` |
| Review remediation 4 | completed | Fixed pass 4 review findings through bounded workers, then integrated the new startup smoke into the functional gate: ADR 0006 egress caveat, current-scope banners in ADR 0003 and the historical roadmap, and a temp-safe `create_app()` composition smoke. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --no-index --check /dev/null .agents/docs/adr/0006-slim-mcp-core-scope.md`; `git diff --no-index --check /dev/null tests/test_app_composition.py` |
| Startup composition review fix | completed | Added a deterministic temp-safe app composition smoke that imports/calls `main.create_app()`, mocks Chroma/SQLite-heavy dependencies, asserts only retained FastMCP tools are registered, verifies only GitHub/Notion/Tistory sources are exposed while unconfigured, and included it in `scripts/verify_functional_e2e.sh`. | `uv run --locked pytest -q tests/test_app_composition.py` -> 1 passed; `uv run --locked pytest -q tests/test_app_composition.py tests/api/test_tools_contract.py tests/fetching/test_connectors.py` -> 16 passed; `git diff --check` -> passed |
| Post-remediation verification 4 | completed | Reran focused checks, the deterministic functional gate with startup composition included, and the full local gate after pass 4 fixes. | `uv run --locked pytest -q tests/test_app_composition.py tests/api/test_tools_contract.py tests/fetching/test_connectors.py` -> 16 passed; `python -m compileall main.py tests/test_app_composition.py`; `uv lock --check`; `git diff --check`; `./scripts/verify_functional_e2e.sh` -> 261 passed; `./scripts/verify_all.sh` -> Ruff passed, mypy passed, Bandit passed, 437 non-live tests passed with 84.96% coverage, functional gate 261 passed |
| Review pass 5 | completed/actionable | Fresh review found remaining issues: functional smoke missed retained Notion/Tistory sync paths, ranking still had removed `web` source-type intent, and docs drift made old roadmap/ADR/plan sections read active or out of order. | User-provided pass-5 review findings |
| Review remediation 5: docs drift | completed | Fixed through a bounded docs worker: relabeled the old roadmap capability snapshot as historical/superseded, added the missing Review pass 4 chronology row, and updated ADR 0001 fetching boundaries to include retained GitHub/Notion/Tistory without implying current website/docs crawler scope. | `docs/plan/2026-05-20-contextwiki-roadmap.md`; `docs/plan/2026-06-10-slim-mcp-core.md`; `.agents/docs/adr/0001-layered-mcp-content-search-architecture.md`; `git diff --check` |
| Review remediation 5: ranking web intent | completed | Fixed through a bounded ranking worker: removed removed web/source_web source-type intent and source-id fallback bonus while preserving ordinary `web`/`docs`/`site` content-term matching. | `search/ranking.py`; `tests/search/test_context_service.py`; `docs/plan/2026-06-10-slim-mcp-core.md` |
| Review remediation 5: ranking verification | completed | First focused run exposed a test setup bug: an unfiltered direct service search allowed legacy `source_web` chunks to compete as ordinary candidates. Tightened the regression to use retained production source ids for content matching while keeping the direct no-source-type-bonus assertion. Reran focused tests and whitespace checks successfully. | `python -m py_compile search/ranking.py tests/search/test_context_service.py` -> passed; `uv run --locked pytest -q tests/search/test_context_service.py` -> first run 1 failed, 114 passed; rerun 115 passed; `git diff --check` -> passed |
| Review remediation 5: retained Notion/Tistory smoke | completed | Dirty-start preflight preserved existing `feature/slim-mcp-core` worktree changes and linked branches; no switching, pulling, cleanup, commits, pushes, secret inspection, or local Chroma/SQLite inspection/mutation. Added deterministic temp-safe MCP `sync_source` smoke coverage for configured `source_notion` and `source_tistory`, including source/job status, document/chunk persistence, search, fetch, and citation answer assertions; retained GitHub e2e behavior remains in the same file. | `git status --short --branch`; `git branch --show-current`; `git branch -vv`; `git worktree list`; `uv run --locked pytest -q tests/e2e/test_phase_b_connectors_flow.py` -> 13 passed; `./scripts/verify_functional_e2e.sh` -> 264 passed |
| Post-remediation verification 5 | completed | Reran focused integration/search/app/API/connector tests, compile, lock, functional smoke, and the full local gate after pass 5 fixes. | `uv run --locked pytest -q tests/e2e/test_phase_b_connectors_flow.py tests/search/test_context_service.py tests/test_app_composition.py tests/api/test_tools_contract.py tests/fetching/test_connectors.py` -> 144 passed; `python -m compileall search/ranking.py tests/search/test_context_service.py tests/e2e/test_phase_b_connectors_flow.py main.py tests/test_app_composition.py`; `uv lock --check`; `git diff --check`; `./scripts/verify_functional_e2e.sh` -> 264 passed; `./scripts/verify_all.sh` -> Ruff passed, mypy passed, Bandit passed, 440 non-live tests passed with 85.13% coverage, functional gate 264 passed |
| Review pass 6 | completed/actionable | Fresh review found two remaining gaps: retained GitHub sync was only covered through `IngestionService` rather than MCP tool serialization/status/search/fetch/answer, and non-live verification scripts could inherit ambient `CONTEXTWIKI_SEARCH_LLM_ENABLED=true`. | Reviewers: Galileo, Dewey, Mendel, Hilbert, Sagan |
| Review remediation 6 | completed | Added a deterministic temp-safe MCP `sync_source("source_github")` smoke using `FakeGitHubHTTP`, and forced `CONTEXTWIKI_SEARCH_LLM_ENABLED=false` inside both non-live verification scripts before pytest runs. | `tests/e2e/test_phase_b_connectors_flow.py`; `scripts/verify_all.sh`; `scripts/verify_functional_e2e.sh` |
| Post-remediation verification 6 | completed | Reran the retained connector E2E, script syntax/diff checks, ambient-env functional smoke, and full local verification with `CONTEXTWIKI_SEARCH_LLM_ENABLED=true` to prove the scripts force non-live behavior. | `uv run --locked pytest -q tests/e2e/test_phase_b_connectors_flow.py` -> 14 passed; `python -m compileall tests/e2e/test_phase_b_connectors_flow.py`; `bash -n scripts/verify_all.sh`; `bash -n scripts/verify_functional_e2e.sh`; `git diff --check`; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_functional_e2e.sh` -> 265 passed; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_all.sh` -> Ruff passed, mypy passed, Bandit passed, 441 non-live tests passed with 85.24% coverage, functional gate 265 passed |
| Review pass 7 | completed/actionable | Fresh review found two remaining defensive gaps: CI's direct non-live pytest step needed the same no-LLM env guard as local scripts, and MCP `answer_with_citations` should inject retained source filters even when callers omit source filters. | Reviewers: Feynman, Erdos, Banach, Averroes, Schrodinger |
| Review remediation 7 | completed | Added `CONTEXTWIKI_SEARCH_LLM_ENABLED: "false"` to CI job env, injected retained `source_ids` into unfiltered MCP answer calls, and added a regression for the answer filter injection. | `.github/workflows/ci.yml`; `api/tools.py`; `tests/api/test_tools_contract.py` |
| Post-remediation verification 7 | completed | Reran focused API/E2E tests, compile, CI YAML parse, diff checks, ambient-env functional smoke, and full local verification with `CONTEXTWIKI_SEARCH_LLM_ENABLED=true`. | `uv run --locked pytest -q tests/api/test_tools_contract.py tests/e2e/test_phase_b_connectors_flow.py` -> 24 passed; `python -m compileall api/tools.py tests/api/test_tools_contract.py tests/e2e/test_phase_b_connectors_flow.py`; `ruby -e 'require "yaml"; YAML.load_file(".github/workflows/ci.yml")'`; `git diff --check`; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_functional_e2e.sh` -> 266 passed; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_all.sh` -> Ruff passed, mypy passed, Bandit passed, 442 non-live tests passed with 85.26% coverage, functional gate 266 passed |
| Review pass 8 | completed/actionable | Fresh review found two remaining issues: retained sync smoke still used a fake MCP decorator instead of real FastMCP `call_tool()` for sync/status/search/fetch/answer, and ADR 0003 still referenced removed legacy search formatting. | Reviewers: Lorentz, Hegel, Gibbs, Copernicus, Euclid |
| Review remediation 8 | completed | Converted retained GitHub/Notion/Tistory sync smoke to a real `FastMCP` instance and `call_tool()` JSON serialization path, and replaced ADR 0003's legacy-search wording with retained search/fetch/answer response gating. | `tests/e2e/test_phase_b_connectors_flow.py`; `.agents/docs/adr/0003-contextwiki-phase-b0-identity-and-chunking.md` |
| Post-remediation verification 8 | completed | Reran focused real-FastMCP E2E/app smoke, functional smoke, and full local gate after pass 8 fixes. | `uv run --locked pytest -q tests/e2e/test_phase_b_connectors_flow.py tests/test_app_composition.py` -> 15 passed; `python -m compileall tests/e2e/test_phase_b_connectors_flow.py tests/test_app_composition.py`; `git diff --check`; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_functional_e2e.sh` -> 266 passed; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_all.sh` -> Ruff passed, mypy passed, Bandit passed, 442 non-live tests passed with 85.26% coverage, functional gate 266 passed |
| Review pass 9 | blocked | Five fresh reviewer agents were spawned but all errored before review with the same Codex authentication refresh failure. Repository policy forbids replacing the five-reviewer gate with self-review, so commit/PR delivery is blocked until Codex authentication is restored and a new fresh review pass can run. | Reviewer ids `019eaf7b-66f2-7cb1-ab75-03ef41593c1b`, `019eaf7b-690c-7cc1-adb2-79a3202902e9`, `019eaf7b-6ac9-74b0-b303-be47b142dca9`, `019eaf7b-6ce0-76f0-8fa8-f33c7f301587`, `019eaf7b-6e9f-72f0-8560-39f4c0dbc1c2`; error: access token could not be refreshed after logout/account change |
| Main refresh conflict resolution | completed | User reported `main` changed and asked to resolve conflicts with our branch. Fetched `origin/main`, fast-forwarded `feature/slim-mcp-core` to new main after stashing local slim changes, resolved Obsidian/Web Console conflicts in favor of the GitHub/Notion/Tistory-only scope, removed Obsidian runtime/test/docs files from the slim branch, and deleted Obsidian-specific registration refresh logic while preserving ad-hoc source config semantics. | `git fetch origin main`; `git stash push --include-untracked -m slim-mcp-core-before-origin-main-01447e9`; `git merge --ff-only origin/main`; `git stash pop`; `rg -n "obsidian|source_obsidian|SourceType\\.OBSIDIAN|CONTEXTWIKI_OBSIDIAN|refresh_source_state" . --glob '!docs/plan/2026-06-10-slim-mcp-core.md'` -> no matches; `python -m compileall api core environments fetching indexing search storage main.py tests/e2e/test_phase_b_connectors_flow.py tests/test_app_composition.py`; `uv lock --check`; `git diff --check`; `uv run --locked pytest -q tests/api/test_tools_contract.py tests/e2e/test_phase_b_connectors_flow.py tests/fetching/test_connectors.py tests/test_app_composition.py tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py` -> 111 passed |
| Post-main verification | completed | Reran deterministic functional smoke and full verification after resolving the `origin/main` conflict set. | `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_functional_e2e.sh` -> 266 passed; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_all.sh` -> Ruff passed, mypy passed, Bandit passed, 442 non-live tests passed with 85.32% coverage, functional gate 266 passed |
| Review pass 10 | completed/actionable | Fresh five-reviewer pass found two delivery blockers: the staged index did not yet include the full verified worktree/untracked files, and MCP source/status payloads could expose secret-like persisted `last_error`, `error_message`, or non-`env:` `auth_ref` values from existing SQLite rows. | Reviewers: Banach, Kuhn, Gauss, Dalton, Aristotle |
| Review remediation 10 | completed | Added slim safe source/job payload wrappers at the MCP boundary, added regressions for persisted secret-like status fields and sync job returns, and reran affected plus broad verification before staging the verified worktree for cached checks. | `uv run --locked pytest -q tests/api/test_tools_contract.py::test_sync_source_redacts_returned_job_error_payload tests/api/test_tools_contract.py::test_status_payloads_redact_persisted_secret_fields` -> 2 passed; `python -m compileall api/tools.py tests/api/test_tools_contract.py indexing/ingestion_service.py storage/metadata_store.py`; `uv run --locked pytest -q tests/api/test_tools_contract.py tests/e2e/test_phase_b_connectors_flow.py tests/test_app_composition.py tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py` -> 107 passed; `git diff --check`; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_functional_e2e.sh` -> 268 passed; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_all.sh` -> Ruff passed, mypy passed, Bandit passed, 444 non-live tests passed with 85.33% coverage, functional gate 268 passed |
| Review remediation 10 packaging | completed | Staged the full verified worktree, including new ADR/plan/app-smoke files and post-conflict Obsidian cleanup edits, without using broad `git add -A` because a sensitive config path was present. Cached whitespace check passed and there are no unstaged/untracked files. | `git diff --cached --check`; `git status --short --branch`; `git diff --name-only` -> no unstaged files |
| Review pass 11 | completed/actionable | Fresh five-reviewer pass found three remaining issues: OpenAI query rewrite prompt redaction did not cover assignment/query-string secrets, `_safe_auth_ref` passed malformed `env:` auth refs through, and `tenacity` remained as an unused direct dependency. | Reviewers: Dalton, Russell, Pasteur, Dirac, Sartre |
| Review remediation 11 | completed | Reused `search.debug_redaction.redact_debug_query_text` for query rewrite prompt fields, required public `env:` auth refs to match a safe env-var-name pattern, added regressions for both egress paths, removed direct `tenacity` declarations, and refreshed `uv.lock`. | `uv run --locked pytest -q tests/search/test_query_rewrite.py tests/api/test_tools_contract.py::test_source_payload_keeps_only_valid_env_auth_refs tests/api/test_tools_contract.py::test_status_payloads_redact_persisted_secret_fields tests/api/test_tools_contract.py::test_sync_source_redacts_returned_job_error_payload` -> 4 passed; `python -m compileall search/query_rewrite.py tests/search/test_query_rewrite.py api/tools.py tests/api/test_tools_contract.py`; `uv run --locked pytest -q tests/api/test_tools_contract.py tests/search/test_query_rewrite.py tests/search/test_context_service.py tests/e2e/test_phase_b_connectors_flow.py tests/test_app_composition.py` -> 144 passed; `rg -n "tenacity|from tenacity|import tenacity" pyproject.toml requirements.txt search api tests` -> no matches; `uv lock --check`; `git diff --check`; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_functional_e2e.sh` -> 269 passed; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_all.sh` -> Ruff passed, mypy passed, Bandit passed, 446 non-live tests passed with 85.55% coverage, functional gate 269 passed |
| Review pass 12 | completed/actionable | Fresh five-reviewer pass found remaining public egress redaction issues: MCP public error/status payloads could leak path-shaped values and whitespace key-value secrets, and query rewrite `normalized_terms` could still include a secret value removed from the redacted query. | Reviewers: McClintock, Galileo, Feynman, Averroes, Einstein |
| Review remediation 12 | completed | Extended MCP-safe error redaction to cover whitespace key-value secrets and filesystem-style paths, derived query rewrite prompt terms from the redacted query instead of original term groups, and added regressions for both public egress paths. | `python -m compileall indexing/background_tasks.py search/query_rewrite.py tests/search/test_query_rewrite.py api/tools.py tests/api/test_tools_contract.py`; `uv run --locked pytest -q tests/search/test_query_rewrite.py tests/api/test_tools_contract.py::test_sync_source_redacts_public_error_paths_and_whitespace_secrets tests/api/test_tools_contract.py::test_status_payloads_redact_public_error_paths_and_whitespace_secrets tests/api/test_tools_contract.py::test_sync_source_redacts_returned_job_error_payload tests/api/test_tools_contract.py::test_status_payloads_redact_persisted_secret_fields` -> 6 passed; `uv run --locked pytest -q tests/api/test_tools_contract.py tests/search/test_query_rewrite.py tests/search/test_context_service.py tests/e2e/test_phase_b_connectors_flow.py tests/test_app_composition.py` -> 147 passed; `uv lock --check`; `git diff --check`; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_functional_e2e.sh` -> 271 passed; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_all.sh` -> Ruff passed, mypy passed, Bandit passed, 449 non-live tests passed with 85.63% coverage, functional gate 271 passed |
| Review pass 13 | completed/actionable | Fresh five-reviewer pass found that the same whitespace/path redaction strength needed to apply to `search.debug_redaction` and `IngestionService` before optional query-rewrite egress, logging, or SQLite sync-error storage. It also flagged stale staged-path-count wording in this plan. | Reviewers: Popper, Darwin, Raman, Newton, Boyle |
| Review remediation 13 | completed | Added whitespace key-value secret redaction to `search.debug_redaction`, changed `IngestionService` to reuse the stronger `safe_error_message` helper before logging/storing failures, extended query rewrite and ingestion/vector-cleanup regressions, and stopped relying on brittle staged-path counts in current evidence. | `python -m compileall search/debug_redaction.py indexing/ingestion_service.py tests/search/test_query_rewrite.py tests/indexing/test_ingestion_service.py`; `uv run --locked pytest -q tests/search/test_query_rewrite.py tests/indexing/test_ingestion_service.py::test_ingestion_redacts_secret_failed_sync_for_retry tests/indexing/test_ingestion_service.py::test_vector_delete_failure_logs_redacted_error` -> 5 passed; `uv run --locked pytest -q tests/indexing/test_ingestion_service.py tests/api/test_tools_contract.py tests/search/test_query_rewrite.py tests/search/test_context_service.py tests/e2e/test_phase_b_connectors_flow.py tests/test_app_composition.py` -> 182 passed; `uv lock --check`; `git diff --check`; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_functional_e2e.sh` -> 271 passed; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_all.sh` -> Ruff passed, mypy passed, Bandit passed, 450 non-live tests passed with 85.60% coverage, functional gate 271 passed |
| Review pass 14 | completed/actionable | Fresh five-reviewer pass found three remaining polish/safety issues: query rewrite prompt redaction was too broad for benign hyphenated technical identifiers, unfiltered MCP `search_context` did not inject retained source filters at the API boundary, and `numpy` remained as an unused direct dependency while only transitive dependencies still require it. | Reviewers: Carson, Wegener, Arendt, Franklin, Huygens |
| Review remediation 14 | completed | Split prompt redaction from stronger debug-log redaction so optional query rewrite preserves benign repo/doc identifiers while still removing explicit secrets and locations, injected retained source filters for unfiltered `search_context`, removed direct `numpy` declarations, refreshed `uv.lock`, and added regressions for the changed contracts. | `uv run --locked pytest -q tests/search/test_query_rewrite.py` -> 4 passed; `uv run --locked pytest -q tests/api/test_tools_contract.py::test_search_context_injects_retained_source_filter_when_unfiltered tests/api/test_tools_contract.py::test_answer_with_citations_injects_retained_source_filter_when_unfiltered` -> 2 passed; `python -m compileall api/tools.py search/debug_redaction.py search/query_rewrite.py tests/api/test_tools_contract.py tests/search/test_query_rewrite.py`; `uv run --locked pytest -q tests/api/test_tools_contract.py tests/search/test_query_rewrite.py tests/search/test_context_service.py tests/e2e/test_phase_b_connectors_flow.py tests/test_app_composition.py` -> 150 passed; `uv lock --check`; `rg -n "numpy\|import numpy\|from numpy\|np\\." pyproject.toml requirements.txt api core environments fetching indexing search storage tests scripts` -> no matches |
| Post-remediation verification 14 | completed | Reran deterministic functional smoke and full local verification after pass 14 fixes. | `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_functional_e2e.sh` -> 272 passed; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_all.sh` -> Ruff passed, mypy passed, Bandit passed, 452 non-live tests passed with 85.61% coverage, functional gate 272 passed |
| Review pass 15 | completed/actionable | Fresh five-reviewer pass had four clean reviewers and one actionable security finding: optional query rewrite prompt redaction missed JSON-style quoted secret keys such as `{"api_key":"..."}` before external LLM egress. | Reviewers: Dewey, Pauli, Erdos, Peirce, Lovelace |
| Review remediation 15 | completed | Extended shared query redaction to handle quoted sensitive keys before `:` or `=`, and added a prompt-regression that verifies quoted secrets are removed while benign hyphenated technical identifiers are preserved. | `uv run --locked pytest -q tests/search/test_query_rewrite.py::test_query_rewriter_redacts_quoted_key_secrets_before_llm_prompt` -> first failed as expected, then passed after fix; `uv run --locked pytest -q tests/search/test_query_rewrite.py` -> 5 passed; `python -m compileall search/debug_redaction.py tests/search/test_query_rewrite.py`; `uv run --locked pytest -q tests/api/test_tools_contract.py tests/search/test_query_rewrite.py tests/search/test_context_service.py tests/e2e/test_phase_b_connectors_flow.py tests/test_app_composition.py` -> 151 passed; `git diff --check` |
| Post-remediation verification 15 | completed | Reran deterministic functional smoke and full local verification after pass 15 fixes. | `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_functional_e2e.sh` -> 272 passed; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_all.sh` -> Ruff passed, mypy passed, Bandit passed, 453 non-live tests passed with 85.61% coverage, functional gate 272 passed |
| Review pass 16 | completed/actionable | Fresh five-reviewer pass had three clean reviewers and two actionable security findings: optional query rewrite redaction still missed multiword quoted secret values and common credential labels such as `cookie`, `jwt`, `pwd`, and `code` before LLM egress. | Reviewers: Harvey, Nietzsche, Socrates, Singer, Godel |
| Review remediation 16 | completed | Aligned query prompt/debug redaction with the broader safe-error credential vocabulary, added a quoted-assignment regex that consumes complete quoted values including spaces and newlines, kept benign natural-language phrases from being redacted as whitespace secrets, and added regressions for the new safety and false-positive cases. | `uv run --locked pytest -q tests/search/test_query_rewrite.py::test_query_rewriter_redacts_complete_multiword_quoted_secret_values tests/search/test_query_rewrite.py::test_query_rewriter_redacts_common_credential_labels_before_llm_prompt` -> first failed as expected, then passed after fix; `uv run --locked pytest -q tests/search/test_query_rewrite.py` -> 8 passed; `python -m compileall search/debug_redaction.py tests/search/test_query_rewrite.py`; `uv run --locked mypy search/debug_redaction.py`; `uv run --locked pytest -q tests/api/test_tools_contract.py tests/search/test_query_rewrite.py tests/search/test_context_service.py tests/e2e/test_phase_b_connectors_flow.py tests/test_app_composition.py` -> 154 passed; `git diff --check` |
| Post-remediation verification 16 | completed | Reran deterministic functional smoke and full local verification after pass 16 fixes. | `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_functional_e2e.sh` -> 272 passed; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_all.sh` -> Ruff passed, mypy passed, Bandit passed, 456 non-live tests passed with 85.69% coverage, functional gate 272 passed |
| Review pass 17 | completed/actionable | Fresh five-reviewer pass found two remaining delivery blockers: optional query rewrite redaction missed Bearer/Basic auth scheme tokens before LLM egress, and `environments/token.py` remained staged despite being a sensitive local config path that reviewers cannot inspect. | Reviewers: Helmholtz, Pascal, Poincare, Mill, Plato |
| Review remediation 17 | completed | Added auth-scheme token redaction for Bearer/Basic values while preserving ordinary non-secret phrases such as `basic examples`, and removed `environments/token.py` from the staged PR set without inspecting its contents so the local one-line sensitive config change remains unstaged only. | `uv run --locked pytest -q tests/search/test_query_rewrite.py::test_query_rewriter_redacts_auth_scheme_tokens_before_llm_prompt` -> first failed as expected, then passed after fix; `uv run --locked pytest -q tests/search/test_query_rewrite.py` -> 9 passed; `python -m compileall search/debug_redaction.py tests/search/test_query_rewrite.py`; `uv run --locked mypy search/debug_redaction.py`; `git restore --staged -- environments/token.py`; `uv run --locked pytest -q tests/api/test_tools_contract.py tests/search/test_query_rewrite.py tests/search/test_context_service.py tests/e2e/test_phase_b_connectors_flow.py tests/test_app_composition.py` -> 155 passed |
| Post-remediation verification 17 | completed | Reran deterministic functional smoke and full local verification after pass 17 fixes; `environments/token.py` remained an unstaged local-only change and is excluded from the PR index. | `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_functional_e2e.sh` -> 272 passed; `CONTEXTWIKI_SEARCH_LLM_ENABLED=true ./scripts/verify_all.sh` -> Ruff passed, mypy passed, Bandit passed, 457 non-live tests passed with 85.72% coverage, functional gate 272 passed |
| Review pass 18 | completed/clean | Final fresh five-reviewer pass reported no actionable findings after pass 17 remediation. | Reviewers: Gibbs, Meitner, Cicero, Epicurus, Ampere |
| Final verification | completed | Final delivery checks confirmed cached whitespace is clean, `environments/token.py` is excluded from the staged PR diff, and only that sensitive local config path remains unstaged. | `git diff --cached --check`; `git diff --cached --name-only \| rg '^environments/token\\.py$' || true`; `git status --short --branch` |
| PR delivery | pending | Commit, push, and create PR after final clean review. | Pending |
