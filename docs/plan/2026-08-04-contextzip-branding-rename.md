# ContextZip Branding Rename

## User request

Rename the mixed repository/project branding from `MCPContentSearch`,
`ContextWiki`, old `contextwiki`/`mcp_content_search` paths, and
`CONTEXTWIKI_*` environment names to the new ContextZip naming contract, then
verify that the project still works before reporting back.

## Branch preflight result

- Started from clean `/Users/eunhwa/IdeaProjects/MCPContentSearch` on `main`.
- Existing local `feature/career-evidence-retrieval` was preserved because it
  was not safe to delete (`2 3` ahead/behind versus `origin/main`).
- Fetched and fast-forward checked `origin/main`, then created
  `feature/contextzip-branding-rename`.
- Current task branch: `feature/contextzip-branding-rename`.

## Scope and non-goals

Scope:

- Rename public branding to `ContextZip`.
- Use `context-zip` for package/image/path-style external names.
- Use `context_zip` for Python identifiers, metadata keys, generated filenames,
  and test fixture identifiers that cannot contain a hyphen.
- Replace current public `CONTEXTWIKI_*` configuration names with
  `CONTEXTZIP_*` names.
- Update docs, deploy templates, scripts, tests, eval labels, Docker metadata,
  and runtime log/debug strings that describe the project.
- Preserve source IDs and MCP tool names because the user asked for project
  rename, not a source/tool contract redesign.

Non-goals:

- Do not rename the GitHub repository settings remotely in this task.
- Do not inspect or mutate user Chroma/SQLite data.
- Do not change retrieval, sync, chunking, citation, or ranking behavior beyond
  naming/configuration strings required by the rename.

## Acceptance criteria

- Repository-visible project branding no longer exposes active old project
  names; active target names use `ContextZip`, `context-zip`, `context_zip`, or
  `CONTEXTZIP_*` according to surface.
- Runtime-facing logs, Docker labels, LaunchAgent labels/templates, README, and
  current architecture docs say `ContextZip`.
- New environment variables use `CONTEXTZIP_*`; tests prove config reads the new
  names.
- Python syntax remains valid after identifier renames.
- Focused config/app/script/e2e tests pass.
- `./scripts/verify_all.sh` passes before review, commit, push, or PR.
- Functional smoke covers app composition plus deterministic MCP flow through
  fake/temp data.

## Step breakdown

1. Inventory current names and classify each occurrence as branding, path,
   config env, Python identifier, historical plan evidence, or test fixture.
2. Add/update tests that expect `ContextZip` names and `CONTEXTZIP_*` settings,
   then run a focused RED command before production edits.
3. Implement scoped rename across code, scripts, deploy templates, docs, and
   tests while avoiding user-data access.
4. Run focused unit/integration/E2E checks, then full verification.
5. Run functional smoke through deterministic caller surfaces.
6. Run the three-reviewer harness loop and fix any actionable findings.
7. Stage only relevant files, commit, push, and create a PR unless blocked.

## Files likely to change

- `README.md`, `AGENTS.md`, `.agents/docs/architecture.md`
- `pyproject.toml`, `Dockerfile`
- `main.py`, `environments/config.py`, `fetching/`, `indexing/`, `search/`,
  `storage/`
- `scripts/*context_zip*` names and script internals
- `deploy/launchd/*context_zip*`
- `tests/**`, `evals/**`, `sample_vault/**`
- Current plan docs only as needed for active references; older historical plan
  evidence may be left as history if changing it would reduce audit value.

## TDD RED evidence

- Command:
  `uv run pytest -q tests/environments/test_config.py tests/test_app_composition.py tests/fetching/test_connectors.py::test_build_source_registry_includes_core_sources tests/fetching/test_connectors.py::test_github_connector_uses_validated_custom_token_env_ref tests/fetching/test_connectors.py::test_obsidian_connector_is_enabled_for_temp_vault tests/indexing/test_sync_worker.py::test_max_concurrent_jobs_env_default_is_two tests/indexing/test_sync_worker.py::test_create_worker_wires_env_max_concurrent_to_worker_and_store tests/scripts/test_sync_worker_launch_agent.py::test_render_only_creates_valid_absolute_secret_free_plist tests/e2e/test_context_zip_flow.py::test_context_zip_fake_e2e_sync_search_fetch_and_answer`
- Unit/integration/E2E test names or layers: config env loading, app
  composition/source auth refs, fetching source registry, sync-worker env
  wiring, LaunchAgent render smoke, and retained deterministic MCP E2E naming
  fixtures.
- Non-zero exit code: `1`.
- Expected failure signature: `13 failed, 28 passed`; production still ignored
  `CONTEXTZIP_GITHUB_REPOSITORIES`, did not validate/load `CONTEXTZIP_*`
  integer env vars, defaulted paths under `.context-zip`, emitted
  `env:CONTEXTZIP_OBSIDIAN_VAULT_PATH`, and ignored
  `CONTEXTZIP_SYNC_WORKER_MAX_CONCURRENT`.
- Missing-behavior explanation: production naming/config surfaces still use the
  old project names while tests now expect ContextZip.
- Predates production edits: yes; recorded before production Python changes.

## TDD GREEN evidence

- Focused unit: `uv run pytest -q tests/environments/test_config.py
  tests/test_app_composition.py tests/fetching/test_connectors.py
  tests/indexing/test_sync_worker.py tests/scripts/test_sync_worker_launch_agent.py
  tests/e2e/test_context_zip_flow.py tests/scripts/test_run_context_zip_eval.py
  tests/scripts/test_verification_architecture.py tests/evals` -> `237 passed`.
- Focused integration: same command covered app composition, source registry,
  sync-worker env wiring, LaunchAgent render checks, CI verification
  architecture, and eval runner integration -> `237 passed`.
- Focused E2E: same command included retained deterministic
  `tests/e2e/test_context_zip_flow.py` -> passed.

## Full-suite evidence

- `./scripts/verify_all.sh`: passed after review fixes and generated cache/temp
  isolation for disk and pytest basetemp headroom.
  - Static verification: compileall, Ruff, mypy, Bandit passed.
  - Public MCP contract layer: `40 passed`.
  - Broad non-live regression layer: `1371 passed`, coverage `87.93%`.
  - Deterministic quality eval layer: passed; retrieval `14/14`, document sort
    `2/2`, answer `9/9`; artifacts written to `artifacts/context-zip-evals`.
  - Deterministic functional E2E layer: `58 passed`.

## Matching eval gate

`n/a` -- branding/config rename does not change retrieval quality, ranking,
grounding, citation selection, or answer quality.

## Improvement performance delta

`n/a` -- this is a rename/configuration consistency task and makes no
performance or quality improvement claim.

## Functional smoke matrix

| Surface | Mode | Expected | Status | Evidence |
| --- | --- | --- | --- | --- |
| App composition | fake sources/temp paths | Registers retained tools and ContextZip source auth refs | passed | `tests/test_app_composition.py`; full `./scripts/verify_all.sh` public contract layer `40 passed` |
| Config loading | monkeypatched env | Reads `CONTEXTZIP_*` names and defaults under `.context-zip` | passed | `tests/environments/test_config.py`; focused suite `237 passed`; latest full regression `1371 passed` |
| Deterministic MCP flow | fake/temp SQLite and Chroma | Sync/search/fetch/answer flow still passes | passed | `tests/e2e/test_context_zip_flow.py`; `./scripts/verify_functional_e2e.sh` -> `58 passed`; full `./scripts/verify_all.sh` functional layer -> `58 passed` |
| Worker/deploy scripts | dry/static tests | LaunchAgent/container naming uses ContextZip/context-zip | passed | `bash -n` changed shell scripts passed; `tests/scripts/test_sync_worker_launch_agent.py`; latest full regression `1371 passed` |
| GitHub connector fixture flow | mocked HTTP/temp SQLite | Document identity and cleanup prefixes use `context-zip` | passed | `tests/fetching/test_github.py` and retained connector E2E; latest full regression `1371 passed` |

## Worker orchestration

- Main agent owns integrated rename and final conflict resolution because the
  change spans shared names and tests.
- Worker 1: test/config boundary, owned files `tests/environments/`,
  `tests/test_app_composition.py`, `tests/fetching/`, and relevant production
  config expectations if delegated.
- Worker 2: docs/scripts/deploy boundary, owned files `README.md`, `Dockerfile`,
  `scripts/`, `deploy/launchd/`, `tests/scripts/` if delegated.
- Workers must preserve unrelated changes, never commit/push/PR, never inspect
  secrets, and never inspect or mutate user Chroma/SQLite data.

## Architecture constraints

- SQLite remains authoritative for active documents and Chroma remains a
  candidate store.
- No behavior change to sync/job lifecycle, tombstones, retrieval gates, MCP
  tool names, public response shapes, or source IDs.
- Local user data paths may change only by default path string in config; this
  task must not migrate, inspect, or delete existing data.

## Risks and rollback notes

- Broad string replacement can break Python identifiers; use `context_zip`
  inside Python and verify with compile/tests.
- Renaming environment variables can break existing local `.env` users; README
  must clearly show the new names.
- GitHub remote repo remains `eunaverse/MCPContentSearch` until the repository
  is renamed outside this branch.
- Rollback point: task branch can be abandoned without touching user data.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created `feature/contextzip-branding-rename` from updated `main`; preserved unsafe local feature branch. | `git status --short --branch`; `git fetch origin main`; `git pull --ff-only origin main`; `git switch -c feature/contextzip-branding-rename` |
| Plan | completed | Created non-exempt rename plan and recorded boundaries. | `docs/plan/2026-08-04-contextzip-branding-rename.md` |
| Improvement delta declare | completed | Non-improvement rename; delta `n/a`. | Plan section |
| Improvement baseline | completed | Non-improvement rename; no baseline required. | Plan section |
| TDD RED | completed | Updated unit/integration/E2E naming expectations before production edits and observed expected failure. | `uv run pytest -q tests/environments/test_config.py ... tests/e2e/test_context_zip_flow.py::test_context_zip_fake_e2e_sync_search_fetch_and_answer` -> `13 failed, 28 passed`, exit 1 |
| Focused unit GREEN | completed | Focused naming/config/script/eval unit coverage passed after implementation. | `uv run pytest -q tests/environments/test_config.py ... tests/evals` -> `237 passed` |
| Focused integration GREEN | completed | App composition, source registry, worker env, LaunchAgent render, CI verification architecture, and eval-runner integration passed. | Same focused command -> `237 passed` |
| Focused E2E GREEN | completed | Deterministic MCP flow passed with ContextZip fixtures and `context_zip_managed` metadata. | Same focused command including `tests/e2e/test_context_zip_flow.py` -> `237 passed` |
| Full suite GREEN | completed | Full static, contract, regression, eval, and functional E2E wrapper passed. | `./scripts/verify_all.sh` -> static passed; `40 passed`; latest full regression `1371 passed`; eval passed (`14/14`, `2/2`, `9/9`); functional E2E `58 passed` |
| Matching eval | completed | `n/a`; no retrieval/answer quality behavior change. | Plan section |
| Improvement after/delta | completed | `n/a`; no improvement claim. | Plan section |
| Functional smoke | completed | Exercised app composition, config, mocked connector, deterministic MCP flow, eval runner, CI verification architecture, and LaunchAgent/script surfaces through fake/temp/dry checks. | Focused suite `237 passed`; `./scripts/verify_functional_e2e.sh` -> `58 passed`; full `./scripts/verify_all.sh` functional layer -> `58 passed` |
| Review loop | completed | Repeated three-reviewer passes; all actionable code/config findings through pass 8 were fixed and reverified, and the final clean pass had no actionable findings. | Final clean pass reviewer 1/2/3: no actionable findings |
| Review pass 1 | completed | Three read-only reviewers found actionable gaps: `.env.example` old env names, active harness skills pointing to deleted eval runner, old LaunchAgent label migration risk, and legacy Chroma managed-vector cleanup gap. | Reviewer 1/2/3 findings on `.env.example`, `.agents/skills/**`, `scripts/*sync_worker_launch_agent*.sh`, `indexing/manager.py`, `indexing/indexer.py` |
| Review pass 1 fixes | completed | Updated `.env.example` mechanically to `CONTEXTZIP_*`; refreshed active harness skill docs; added old LaunchAgent label migration/removal; added legacy Chroma managed-key cleanup compatibility with tests. | `uv run pytest -q tests/indexing/test_index_manager.py` -> RED before fix (`6 failed, 1 passed`), then GREEN `7 passed`; LaunchAgent migration tests RED (`2 failed`), then GREEN with affected checks `9 passed`; `uv run pytest -q tests/scripts/test_sync_worker_launch_agent.py` -> `89 passed`; broader affected checks `139 passed`; active old-token scan -> 0 |
| Post-review full suite | completed | Re-ran full verification after review fixes. An earlier attempt failed because disk was exhausted and coverage could not create its SQLite data file; after cleaning generated pytest/npm cache only, the full suite passed. | `./scripts/verify_all.sh` -> static passed; `40 passed`; broad non-live `1359 passed`; eval passed (`14/14`, `2/2`, `9/9`); functional E2E `58 passed` |
| Review pass 2 | completed | Three fresh read-only reviewers found install legacy cleanup was not transaction-safe; reviewer 3 also found uninstall dry-run omitted legacy cleanup. | Reviewer 1/2/3 findings on `scripts/install_sync_worker_launch_agent.sh` and `scripts/uninstall_sync_worker_launch_agent.sh` |
| Review pass 2 fixes RED | completed | Added regression tests proving install failure preserved the old label plist and uninstall dry-run reported legacy cleanup. | `uv run pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_install_preserves_legacy_launch_agent_when_new_install_fails tests/scripts/test_sync_worker_launch_agent.py::test_uninstall_dry_run_reports_legacy_launch_agent_cleanup` -> `2 failed`, exit 1 |
| Review pass 2 fixes GREEN | completed | Deferred old-label cleanup until after new install commit and added dry-run legacy output. | `bash -n scripts/install_sync_worker_launch_agent.sh scripts/uninstall_sync_worker_launch_agent.sh && uv run pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_install_preserves_legacy_launch_agent_when_new_install_fails tests/scripts/test_sync_worker_launch_agent.py::test_uninstall_dry_run_reports_legacy_launch_agent_cleanup` -> `2 passed`; `uv run pytest -q tests/scripts/test_sync_worker_launch_agent.py tests/indexing/test_index_manager.py` -> `98 passed` |
| Post-review-pass-2 full suite | completed | Re-ran full verification after transaction-safety fixes. One earlier full-suite attempt hit a single SIGINT timing failure in an existing LaunchAgent success-output test; the isolated case passed 3 times, the full LaunchAgent suite passed, and the full wrapper passed on rerun. | Isolated existing signal test x3 -> passed; `uv run pytest -q tests/scripts/test_sync_worker_launch_agent.py` -> `91 passed`; `./scripts/verify_all.sh` -> static passed; `40 passed`; broad non-live `1361 passed`; eval passed (`14/14`, `2/2`, `9/9`); functional E2E `58 passed` |
| Review pass 3 | completed | Three fresh reviewers reported legacy retrieval visibility and identical-loaded LaunchAgent cleanup concerns. Current code already accepts legacy managed metadata in vector filters/post-filter and cleans the old LaunchAgent label in the identical-loaded path; strengthened tests confirmed both. | Reviewer 1/2/3 findings on `search/retrieval_pipeline.py`, `tests/search/test_context_service.py`, and `scripts/install_sync_worker_launch_agent.sh` |
| Review pass 3 checks | completed | Strengthened the legacy managed metadata filter assertion and confirmed identical-loaded old LaunchAgent cleanup. A combined run once hit a missing pytest basetemp after an environment-level temp cleanup anomaly; clean rerun passed. | `uv run pytest -q tests/search/test_context_service.py::test_vector_search_accepts_legacy_managed_metadata_for_existing_chunks tests/scripts/test_sync_worker_launch_agent.py::test_install_cleans_legacy_launch_agent_when_new_service_already_loaded tests/scripts/test_sync_worker_launch_agent.py::test_install_preserves_legacy_launch_agent_when_new_install_fails tests/scripts/test_sync_worker_launch_agent.py::test_uninstall_dry_run_reports_legacy_launch_agent_cleanup` -> `4 passed`; `uv run pytest -q tests/search/test_context_service.py tests/scripts/test_sync_worker_launch_agent.py tests/indexing/test_index_manager.py` -> `253 passed` |
| Post-review-pass-3 full suite | completed | Re-ran full verification after pass-3 checks. One attempt exited `143` during signal-heavy LaunchAgent tests; rerun passed with no code changes. | `./scripts/verify_all.sh` -> static passed; `40 passed`; broad non-live `1363 passed`; eval passed (`14/14`, `2/2`, `9/9`); functional E2E `58 passed` |
| Review pass 4 | completed | Three fresh reviewers; reviewers 1/2 found no actionable findings, reviewer 3 asked for install dry-run legacy cleanup visibility and real temp-Chroma evidence for legacy-only managed metadata. | Reviewer 3 findings on `scripts/install_sync_worker_launch_agent.sh` and Chroma-backed retrieval behavior |
| Review pass 4 fixes | completed | Added install dry-run legacy cleanup output/test; added temp Chroma E2E that rewrites a managed vector to legacy-only metadata before `search_context`; fixed fake LaunchAgent old-label matching for uninstall tests. | `bash -n scripts/install_sync_worker_launch_agent.sh && uv run pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_install_dry_run_reports_legacy_launch_agent_cleanup tests/e2e/test_context_zip_flow.py::test_context_zip_temp_chroma_e2e_sync_search_fetch_and_answer` -> `2 passed`; targeted uninstall checks -> `4 passed`; `uv run pytest -q tests/e2e/test_context_zip_flow.py tests/search/test_context_service.py tests/scripts/test_sync_worker_launch_agent.py tests/indexing/test_index_manager.py` -> `273 passed` |
| Post-review-pass-4 full suite | completed | Re-ran full verification after pass-4 fixes. | `./scripts/verify_all.sh` -> static passed; `40 passed`; broad non-live `1367 passed`; eval passed (`14/14`, `2/2`, `9/9`); functional E2E `58 passed` |
| Review pass 5 | completed | Three fresh reviewers after post-review-pass-4 full verification; reviewers 1/3 found no actionable findings, reviewer 2 found old-label LaunchAgent services could remain loaded when the old plist was already missing. | Reviewer 2 finding on install/uninstall legacy service cleanup |
| Review pass 5 fixes RED | completed | Added install/uninstall regressions for a loaded old-label LaunchAgent service with no old plist present. | `uv run pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_install_stops_legacy_launch_agent_when_old_plist_is_missing tests/scripts/test_sync_worker_launch_agent.py::test_uninstall_stops_legacy_launch_agent_when_old_plist_is_missing` -> `2 failed`, exit 1 |
| Review pass 5 fixes GREEN | completed | Made install/uninstall probe and boot out the old label independently of old plist existence; updated label-aware test fakes and changed expectations for new legacy-service safety probes. | `bash -n scripts/install_sync_worker_launch_agent.sh scripts/uninstall_sync_worker_launch_agent.sh && uv run pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_install_stops_legacy_launch_agent_when_old_plist_is_missing tests/scripts/test_sync_worker_launch_agent.py::test_uninstall_stops_legacy_launch_agent_when_old_plist_is_missing` -> `2 passed`; `TMPDIR=/private/var/folders/18/8wsb4dlx0yx6hnj5_dnl1dq00000gn/T/contextzip-verify-tmp uv run pytest -q tests/scripts/test_sync_worker_launch_agent.py tests/search/test_context_service.py tests/indexing/test_index_manager.py -x` -> `259 passed` |
| Post-review-pass-5 full suite | completed | Re-ran full verification after pass-5 fixes. | `TMPDIR=/private/var/folders/18/8wsb4dlx0yx6hnj5_dnl1dq00000gn/T/contextzip-verify-tmp ./scripts/verify_all.sh` -> static passed; `40 passed`; broad non-live `1369 passed`; eval passed (`14/14`, `2/2`, `9/9`); functional E2E `58 passed` |
| Review pass 6 | completed | Three fresh reviewers after post-review-pass-5 full verification; reviewer 2 found no actionable findings, reviewer 1 found plan-only stale wording, and reviewer 3 found dry-run under-reported loaded legacy service cleanup when the old plist was missing. | Reviewer 1/3 findings on plan wording and dry-run LaunchAgent output |
| Review pass 6 fixes RED | completed | Added install/uninstall dry-run regressions requiring explicit live-state limitation text while preserving no-launchctl dry-run behavior. | `TMPDIR=/private/var/folders/18/8wsb4dlx0yx6hnj5_dnl1dq00000gn/T/contextzip-verify-tmp uv run pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_install_dry_run_warns_legacy_service_state_is_not_queried tests/scripts/test_sync_worker_launch_agent.py::test_uninstall_dry_run_warns_legacy_service_state_is_not_queried` -> `2 failed`, exit 1 |
| Review pass 6 fixes GREEN | completed | Added dry-run output documenting that dry-run does not query loaded LaunchAgent state and actual install/uninstall also stop the loaded old label. | `bash -n scripts/install_sync_worker_launch_agent.sh scripts/uninstall_sync_worker_launch_agent.sh && TMPDIR=/private/var/folders/18/8wsb4dlx0yx6hnj5_dnl1dq00000gn/T/contextzip-verify-tmp uv run pytest -q tests/scripts/test_sync_worker_launch_agent.py::test_install_dry_run_warns_legacy_service_state_is_not_queried tests/scripts/test_sync_worker_launch_agent.py::test_uninstall_dry_run_warns_legacy_service_state_is_not_queried` -> `2 passed`; `TMPDIR=/private/var/folders/18/8wsb4dlx0yx6hnj5_dnl1dq00000gn/T/contextzip-verify-tmp uv run pytest -q tests/scripts/test_sync_worker_launch_agent.py tests/search/test_context_service.py tests/indexing/test_index_manager.py -x` -> `261 passed` |
| Post-review-pass-6 full suite | completed | Re-ran full verification after pass-6 fixes. | `TMPDIR=/private/var/folders/18/8wsb4dlx0yx6hnj5_dnl1dq00000gn/T/contextzip-verify-tmp ./scripts/verify_all.sh` -> static passed; `40 passed`; broad non-live `1371 passed`; eval passed (`14/14`, `2/2`, `9/9`); functional E2E `58 passed` |
| Review pass 7 | completed | Three fresh reviewers after post-review-pass-6 full verification; two reviewers found only stale plan wording and one reviewer timed out before completion. | Reviewer 1/3 plan-only findings; reviewer 2 closed while still running |
| Review pass 7 fixes | completed | Updated review-loop summary wording; no code/config change. | `git diff --check -- docs/plan/2026-08-04-contextzip-branding-rename.md scripts/install_sync_worker_launch_agent.sh scripts/uninstall_sync_worker_launch_agent.sh tests/scripts/test_sync_worker_launch_agent.py` passed |
| Review pass 8 | completed | Three fresh reviewers after pass-7 docs-only fixes; all three found only stale review-loop summary wording and no code/security/operability findings. | Reviewer 1/2/3 plan-only finding on review-loop summary |
| Review pass 8 fixes | completed | Updated review-loop summary to avoid stale pass-specific wording; no code/config change. | `git diff --check` passed; stale review-loop pattern scan returned no matches |
| Final review pass | completed | Three fresh read-only reviewers after final docs-only fix reported no actionable findings. | Reviewer 1 correctness/contracts/tests clean; reviewer 2 security/privacy/data safety clean; reviewer 3 reliability/operability clean |
