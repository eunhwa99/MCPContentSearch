# Issue 33 Reproducibility

## User request

Implement Issue `#33` only from the latest `main`. The user clarified on
2026-06-14 that Issue `#43` phases are already complete and only the parallel
support-track reproducibility work should proceed.

## Branch preflight result

- Source worktree at `/Users/eunhwa/IdeaProjects/MCPContentSearch` was dirty on
  branch `feature/readme-rewrite-cleanup`; it was preserved without branch
  switching or cleanup.
- Fetched `origin/main` and observed latest remote `main` at `a9f4439`.
- Created isolated worktree
  `/private/tmp/MCPContentSearch-issue33-reproducibility` on fresh branch
  `feature/issue-33-reproducibility` from `origin/main`.
- Worker orchestration bypass: user explicitly approved direct main-agent
  implementation for this scoped reproducibility task, including the later
  runtime-script/test remediations needed to make the documented Docker and
  demo paths actually execute.

## Scope

- Add a slim-core `Dockerfile` suitable for reviewer/repro launch paths.
- Add a safe `.env.example` aligned to current runtime config and supported
  source connectors.
- Update `README.md` so fresh-machine local launch, container launch, demo
  launch, and live smoke launch are documented in one consistent flow.
- Add small support files only if they directly improve reproducibility
  (`.dockerignore` is allowed).

## Non-goals

- No production deployment automation.
- No `docker-compose`, multi-service orchestration, or extra infra files.
- No MCP contract changes, source-sync behavior changes, or runtime feature
  additions beyond packaging/docs plus the minimal demo runtime-script
  remediations required to make the documented reproducibility paths execute.
- No real secrets in docs or examples.

## Acceptance criteria

1. A first-time reviewer can identify supported launch paths from `README.md`
   without reading source files.
2. `.env.example` includes safe placeholders for the current slim-core config
   and clearly distinguishes optional from required settings.
3. `Dockerfile` documents or encodes the supported runtime entrypoint for the
   slim MCP core, and any local Docker-build blocker is recorded explicitly if
   the build cannot be exercised in this environment.
4. README local launch, container launch, demo launch, and live smoke commands
   match actual repository commands and current config names.

## Step breakdown

1. Inspect current reproducibility gaps across config, launch paths, and docs.
2. Add container packaging files for the slim MCP core.
3. Add safe environment template.
4. Rewrite README launch/repro sections around reviewer-first flows.
5. Run focused verification: diff checks, compile baseline sanity, Docker build
   and a minimal container startup smoke when the local Docker environment is
   available, and otherwise record the blocker explicitly.

## Files likely to change

- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `.env.example`
- Modify: `README.md`
- Modify: `environments/config.py`
- Modify: `environments/token.py`
- Modify: `scripts/demo_public_flow.py`
- Modify: `scripts/live_query_smoke.py`
- Modify: `tests/environments/test_config.py`
- Modify: `tests/environments/test_token.py`
- Modify: `tests/scripts/test_demo_public_flow.py`
- Modify: `tests/scripts/test_live_query_smoke.py`
- Modify: `docs/plan/2026-06-14-issue-33-reproducibility.md`

## Test and verification plan

- `git diff --check`
- `python -m compileall api core environments fetching indexing search storage main.py`
- `docker build -t contextwiki-issue33 .`
  or, when Docker Desktop's credential helper blocks anonymous pulls on this
  machine, `docker --config /tmp/docker-nocreds... build -t contextwiki-issue33 .`
- Minimal container startup smoke using the documented reviewer path:
  `docker run --rm --env-file .env -v contextwiki_data:/home/appuser/.mcp_content_search contextwiki-issue33`
  or the closest safe command if the server blocks indefinitely and needs a
  bounded timeout wrapper.
- Recheck README commands against repository files after edits.

## Functional smoke matrix

| Surface | Scenario | Safe mode | Status | Notes |
| --- | --- | --- | --- | --- |
| local launch docs | `uv sync` + `uv run --locked python main.py` path is documented | docs-only | completed | `uv run --locked python -c "from main import create_app; create_app(); print('create_app ok')"` |
| demo launch docs | `./scripts/demo.sh` path is documented and still matches script behavior | local script | completed | `./scripts/demo.sh --json` |
| live smoke docs | `scripts/live_query_smoke.py` commands match current CLI flags | docs-only | completed | `uv run --locked python scripts/live_query_smoke.py --help` |
| container launch | image builds and starts the slim core entrypoint | local container | completed | `docker --config /tmp/docker-nocreds... build -t contextwiki-issue33 .`; `docker run --rm -i --env-file /tmp/contextwiki-issue33.env -v contextwiki_data_issue33:/home/appuser/.mcp_content_search contextwiki-issue33` logged startup successfully |
| container demo script | in-container `scripts/demo_public_flow.py --json` path runs directly from the built image | local container | completed | `docker run --rm --env-file /tmp/contextwiki-issue33.env -v contextwiki_data_issue33:/home/appuser/.mcp_content_search contextwiki-issue33 /app/.venv/bin/python scripts/demo_public_flow.py --json` |

## Architecture/ADR constraints

- ADR `0006`: stay inside the slim MCP core; do not introduce removed web/wiki
  surfaces or multi-service runtime complexity.
- ADR `0002`: do not encourage inspecting or mutating user SQLite/Chroma data;
  docs should prefer temporary/demo or explicit configured runtime paths.
- `.agents/docs/architecture.md`: keep retained MCP tool surface and current
  config names accurate.

## Risks and rollback notes

- Risk: README or `.env.example` can drift from actual config names. Mitigation:
  derive values directly from `environments/config.py`, `main.py`, and scripts.
- Risk: container path may suggest a live runtime without clear local-state
  implications. Mitigation: document persisted paths, optional mounts, and safe
  placeholder env values.
- Rollback: revert the packaging/docs changes plus the scoped demo runtime-script
  and test remediations from this branch; no local user data migration is
  involved.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Preserved dirty source worktree, fetched latest `origin/main`, and created isolated worktree/branch for Issue `#33`. | `git status --short --branch`; `git fetch origin main`; `git worktree add /private/tmp/MCPContentSearch-issue33-reproducibility -b feature/issue-33-reproducibility origin/main` |
| Context discovery | completed | Reviewed harness docs, workflow docs, architecture, ADR `0002`/`0006`, current README, runtime config, and launch scripts. | `.agents/docs/harness-engineering.md`; `.agents/docs/github-workflow.md`; `.agents/docs/architecture.md`; `.agents/docs/adr/README.md`; `.agents/docs/adr/0002-contextwiki-metadata-and-citation-store.md`; `.agents/docs/adr/0006-slim-mcp-core-scope.md`; `README.md`; `environments/config.py`; `main.py`; `scripts/demo.sh`; `scripts/live_query_smoke.py` |
| Baseline verification | completed | Confirmed current Python modules compile before packaging/docs edits. | `python -m compileall api core environments fetching indexing search storage main.py` |
| Implementation | completed | Added `Dockerfile`, `.dockerignore`, `.env.example`, rewrote README launch-path docs, and fixed `scripts/live_query_smoke.py` so the documented direct script invocation works from repo root. | `Dockerfile`; `.dockerignore`; `.env.example`; `README.md`; `scripts/live_query_smoke.py`; `tests/scripts/test_live_query_smoke.py` |
| Verification | completed/blocked | `git diff --check`, compileall, script-focused pytest, local `create_app()` startup smoke, `demo.sh`, and `live_query_smoke.py --help` passed. Docker image build verification remains blocked because the local Docker daemon was unavailable. | `git diff --check`; `python -m compileall api core environments fetching indexing search storage main.py scripts/live_query_smoke.py tests/scripts/test_live_query_smoke.py`; `uv run --locked pytest -q tests/scripts/test_live_query_smoke.py tests/scripts/test_demo_public_flow.py`; `uv run --locked python -c "from main import create_app; create_app(); print('create_app ok')"`; `./scripts/demo.sh --json`; `uv run --locked python scripts/live_query_smoke.py --help`; `docker build -t contextwiki-issue33 .` -> `Cannot connect to the Docker daemon ...` |
| Review pass 1 | completed/actionable | Fresh five-reviewer pass found issues in `.env.example` defaults, Docker host-volume ownership, `live_query_smoke.py` debug accuracy/redaction, and stale plan traceability. | Reviewers: Hubble, Tesla, Franklin, Averroes, Maxwell |
| Review remediation 1 | completed | Blank GitHub placeholder defaults, switched container runtime to a non-root user, requested search debug explicitly in live smoke, tightened/redescribed JSON redaction, and refreshed plan traceability. | `.env.example`; `Dockerfile`; `README.md`; `scripts/live_query_smoke.py`; `tests/scripts/test_live_query_smoke.py`; this file |
| Reverification 1 | completed/blocked | Reran affected checks after remediation: diff check, compileall, script-focused pytest, app startup smoke, demo smoke, and live-smoke help passed. Docker build remains blocked by the local daemon state. | `git diff --check`; `python -m compileall api core environments fetching indexing search storage main.py scripts/live_query_smoke.py tests/scripts/test_live_query_smoke.py`; `uv run --locked pytest -q tests/scripts/test_live_query_smoke.py tests/scripts/test_demo_public_flow.py`; `uv run --locked python -c "from main import create_app; create_app(); print('create_app ok')"`; `./scripts/demo.sh --json`; `uv run --locked python scripts/live_query_smoke.py --help`; `docker build -t contextwiki-issue33 .` -> `Cannot connect to the Docker daemon ...` |
| Review pass 2 | completed/actionable | Fresh five-reviewer pass found remaining Docker README mismatches around persistence path and first-run host-volume setup, plus a nested debug-redaction mismatch in `--json` output wording. | Reviewers: Popper, Galileo, Poincare, Socrates, Banach |
| Review remediation 2 | completed | Switched Docker README examples to a named volume, aligned the persisted container path to `/home/appuser/.mcp_content_search`, and stripped nested debug `path`/`url` fields so `--json` wording matches behavior. | `README.md`; `scripts/live_query_smoke.py`; `tests/scripts/test_live_query_smoke.py`; this file |
| Reverification 2 | completed/blocked | Reran affected checks after remediation 2: diff check, compileall, script-focused pytest, app startup smoke, demo smoke, and live-smoke help passed. Docker build remains blocked by the local daemon state. | `git diff --check`; `python -m compileall api core environments fetching indexing search storage main.py scripts/live_query_smoke.py tests/scripts/test_live_query_smoke.py`; `uv run --locked pytest -q tests/scripts/test_live_query_smoke.py tests/scripts/test_demo_public_flow.py`; `uv run --locked python -c "from main import create_app; create_app(); print('create_app ok')"`; `./scripts/demo.sh --json`; `uv run --locked python scripts/live_query_smoke.py --help`; `docker build -t contextwiki-issue33 .` -> `Cannot connect to the Docker daemon ...` |
| Review pass 3 | completed/actionable | Fresh five-reviewer pass found remaining README/tooling/documentation issues: missing `search_documents` in README, missing explicit embedding-key requirement for packaged runtime, missing container subsection `.env` prerequisite, mutable Docker base tag, and stale plan rows. | Reviewers: Cicero, Beauvoir, Carver, Dirac, Hume |
| Review remediation 3 | completed | Added `search_documents` to README, documented the packaged runtime embedding-key requirement and container `.env` prerequisite, pinned the Docker base image patch version, and refreshed the plan trace. | `README.md`; `.env.example`; `Dockerfile`; this file |
| Reverification 3 | completed/blocked | Reran affected checks after remediation 3: diff check, compileall, script-focused pytest, app startup smoke, demo smoke, and live-smoke help passed. Docker build remains blocked by the local daemon state. | `git diff --check`; `python -m compileall api core environments fetching indexing search storage main.py scripts/live_query_smoke.py tests/scripts/test_live_query_smoke.py`; `uv run --locked pytest -q tests/scripts/test_live_query_smoke.py tests/scripts/test_demo_public_flow.py`; `uv run --locked python -c "from main import create_app; create_app(); print('create_app ok')"`; `./scripts/demo.sh --json`; `uv run --locked python scripts/live_query_smoke.py --help`; `docker build -t contextwiki-issue33 .` -> `Cannot connect to the Docker daemon ...` |
| Review pass 4 | completed/actionable | Fresh five-reviewer pass found one remaining runtime/docs mismatch: `TISTORY_BLOG_NAME` still defaulted to a live blog name when env was absent, which conflicted with the safe-blank reviewer template and launch docs. | Reviewers: Plato, Volta, Euclid, Meitner, Singer |
| Review remediation 4 | completed | Made `TISTORY_BLOG_NAME` default to blank and added regression coverage so missing env does not silently enable the retained Tistory source. | `environments/token.py`; `tests/environments/test_token.py`; this file |
| Reverification 4 | completed/blocked | Reran affected checks after remediation 4: diff check, compileall, expanded focused pytest, app startup smoke, demo smoke, and live-smoke help passed. Docker build remains blocked by the local daemon state. | `git diff --check`; `python -m compileall api core environments fetching indexing search storage main.py scripts/live_query_smoke.py tests/scripts/test_live_query_smoke.py tests/environments/test_token.py`; `uv run --locked pytest -q tests/environments/test_token.py tests/fetching/test_connectors.py::test_build_source_registry_disables_tistory_until_blog_is_configured tests/test_app_composition.py::test_create_app_registers_slim_mcp_tools_and_core_sources tests/scripts/test_live_query_smoke.py tests/scripts/test_demo_public_flow.py`; `uv run --locked python -c "from main import create_app; create_app(); print('create_app ok')"`; `./scripts/demo.sh --json`; `uv run --locked python scripts/live_query_smoke.py --help`; `docker build -t contextwiki-issue33 .` -> `Cannot connect to the Docker daemon ...` |
| Review pass 5 | completed/actionable | Fresh reviewer pass found two remaining Docker packaging issues: `.dockerignore` still allowed other `.env*` files into the build context, and the container entrypoint used `uv run`, which can mutate the runtime by installing dev dependencies. | Reviewers: Pauli, Rawls, Carson, Wegener, Newton |
| Review remediation 5 | completed | Ignored `.env*` by default while re-allowing `.env.example`, and switched the container entrypoint to the prebuilt `/app/.venv/bin/python` runtime. | `.dockerignore`; `Dockerfile`; this file |
| Reverification 5 | completed/blocked | Reran affected checks after remediation 5: diff check, compileall, expanded focused pytest, app startup smoke, demo smoke, and live-smoke help passed. Docker build remains blocked by the local daemon state. | `git diff --check`; `python -m compileall api core environments fetching indexing search storage main.py scripts/live_query_smoke.py tests/scripts/test_live_query_smoke.py tests/environments/test_token.py`; `uv run --locked pytest -q tests/environments/test_token.py tests/fetching/test_connectors.py::test_build_source_registry_disables_tistory_until_blog_is_configured tests/test_app_composition.py::test_create_app_registers_slim_mcp_tools_and_core_sources tests/scripts/test_live_query_smoke.py tests/scripts/test_demo_public_flow.py`; `uv run --locked python -c "from main import create_app; create_app(); print('create_app ok')"`; `./scripts/demo.sh --json`; `uv run --locked python scripts/live_query_smoke.py --help`; `docker build -t contextwiki-issue33 .` -> `Cannot connect to the Docker daemon ...` |
| Review pass 6 | completed/actionable | Fresh reviewer pass found remaining Docker/tooling issues: unpinned `uv` installation, `test_token` dotenv leakage, stale container-smoke wording in the plan, and pre-creation of `/home/appuser/.mcp_content_search` for first-run volume safety. | Reviewers: Curie, Kepler, Ptolemy, Gauss, Fermat |
| Review remediation 6 | completed | Pinned `uv`, isolated `tests/environments/test_token.py` from ambient dotenv reloads, created `/home/appuser/.mcp_content_search` before dropping privileges, and refreshed the plan's documented container smoke path. | `Dockerfile`; `tests/environments/test_token.py`; this file |
| Reverification 6 | completed/blocked | Reran affected checks after remediation 6: diff check, compileall, expanded focused pytest, app startup smoke, demo smoke, and live-smoke help passed. Docker build remains blocked by the local daemon state. | `git diff --check`; `python -m compileall api core environments fetching indexing search storage main.py scripts/live_query_smoke.py tests/scripts/test_live_query_smoke.py tests/environments/test_token.py`; `uv run --locked pytest -q tests/environments/test_token.py tests/fetching/test_connectors.py::test_build_source_registry_disables_tistory_until_blog_is_configured tests/test_app_composition.py::test_create_app_registers_slim_mcp_tools_and_core_sources tests/scripts/test_live_query_smoke.py tests/scripts/test_demo_public_flow.py`; `uv run --locked python -c "from main import create_app; create_app(); print('create_app ok')"`; `./scripts/demo.sh --json`; `uv run --locked python scripts/live_query_smoke.py --help`; `docker build -t contextwiki-issue33 .` -> `Cannot connect to the Docker daemon ...` |
| Review pass 7 | completed/actionable | Fresh reviewer pass found one remaining runtime-state gap: `cache_dir` still pointed at repo-local `.llama_cache`, and `.dockerignore` did not exclude that cache. | Reviewers: Herschel, Kuhn, Halley, Raman, Pasteur |
| Review remediation 7 | completed | Moved the default `cache_dir` under `~/.mcp_content_search/llama_cache`, added a regression test for that default, and excluded `.llama_cache/` from the Docker build context. | `environments/config.py`; `tests/environments/test_config.py`; `.dockerignore`; this file |
| Reverification 7 | completed/blocked | Reran affected checks after remediation 7: diff check, compileall, expanded focused pytest, app startup smoke, demo smoke, and live-smoke help passed. Docker build remains blocked by the local daemon state. | `git diff --check`; `python -m compileall api core environments fetching indexing search storage main.py scripts/live_query_smoke.py tests/scripts/test_live_query_smoke.py tests/environments/test_token.py tests/environments/test_config.py`; `uv run --locked pytest -q tests/environments/test_token.py tests/environments/test_config.py::test_cache_dir_defaults_under_contextwiki_home tests/fetching/test_connectors.py::test_build_source_registry_disables_tistory_until_blog_is_configured tests/test_app_composition.py::test_create_app_registers_slim_mcp_tools_and_core_sources tests/scripts/test_live_query_smoke.py tests/scripts/test_demo_public_flow.py`; `uv run --locked python -c "from main import create_app; create_app(); print('create_app ok')"`; `./scripts/demo.sh --json`; `uv run --locked python scripts/live_query_smoke.py --help`; `docker build -t contextwiki-issue33 .` -> `Cannot connect to the Docker daemon ...` |
| Review pass 8 | completed/actionable | Fresh reviewer pass found one remaining test-isolation issue: `tests/environments/test_token.py` reloaded `environments.token` without restoring module state, making the suite order-dependent. | Reviewers: Avicenna, Nietzsche, Epicurus, Chandrasekhar, Dewey |
| Review remediation 8 | completed | Restored `environments.token` after the regression assertion so the dotenv-related test no longer leaks module state into later tests. | `tests/environments/test_token.py`; this file |
| Reverification 8 | completed/blocked | Reran the focused test suite plus diff check after remediation 8; all targeted checks passed. Docker build remains blocked by the local daemon state. | `git diff --check`; `uv run --locked pytest -q tests/environments/test_token.py tests/environments/test_config.py::test_cache_dir_defaults_under_contextwiki_home tests/fetching/test_connectors.py::test_build_source_registry_disables_tistory_until_blog_is_configured tests/test_app_composition.py::test_create_app_registers_slim_mcp_tools_and_core_sources tests/scripts/test_live_query_smoke.py tests/scripts/test_demo_public_flow.py`; `docker build -t contextwiki-issue33 .` -> `Cannot connect to the Docker daemon ...` |
| Single-reviewer finding 9 | completed/actionable | One staged-state reviewer found two PR-readiness traceability gaps: the plan still showed review pass 9 as pending, and the acceptance wording overstated Docker-build validation despite the recorded daemon blocker. | Reviewer: Ohm |
| Remediation 9 | completed | Narrowed the acceptance/verification wording so Docker build/startup validation is explicitly conditional on local daemon availability, added the remaining touched files to the plan inventory, and refreshed the final-review trace before rerunning staged checks. | this file |
| Reverification 9 | completed/blocked | Reran staged diff checks after remediation 9; both working-tree and cached whitespace checks passed. Docker build remains blocked by the local daemon state. | `git diff --check`; `git diff --cached --check`; `docker build -t contextwiki-issue33 .` -> `Cannot connect to the Docker daemon ...` |
| Single-reviewer finding 10 | completed/actionable | One reviewer found one remaining test-isolation issue: the cleanup reload in `tests/environments/test_token.py` could still read an ambient repo-root `.env` after `monkeypatch.undo()`, leaving the suite order-dependent. | Reviewer: Erdos |
| Remediation 10 | completed | Patched the cleanup reload to keep `dotenv.load_dotenv` disabled while restoring `environments.token`, so the test no longer reimports local `.env` state during teardown. | `tests/environments/test_token.py`; this file |
| Reverification 10 | completed/blocked | Reran the focused reproducibility test set after remediation 10; all targeted checks passed, and staged diff checks remained clean. Docker build remains blocked by the local daemon state. | `uv run --locked pytest -q tests/environments/test_token.py tests/environments/test_config.py::test_cache_dir_defaults_under_contextwiki_home tests/fetching/test_connectors.py::test_build_source_registry_disables_tistory_until_blog_is_configured tests/test_app_composition.py::test_create_app_registers_slim_mcp_tools_and_core_sources tests/scripts/test_live_query_smoke.py tests/scripts/test_demo_public_flow.py`; `git diff --check`; `git diff --cached --check`; `docker build -t contextwiki-issue33 .` -> `Cannot connect to the Docker daemon ...` |
| Single-reviewer finding 11 | completed/actionable | One reviewer found one remaining test-collection issue: `tests/environments/test_token.py` still imported `environments.token` at module scope, so pytest collection itself could load a local `.env` before the test's monkeypatching ran. | Reviewer: Huygens |
| Remediation 11 | completed | Removed the module-scope `environments.token` import, imported it only inside the test after stubbing `dotenv.load_dotenv`, and cleared `sys.modules` during setup/teardown so collection no longer loads ambient `.env` state. | `tests/environments/test_token.py`; this file |
| Reverification 11 | completed/blocked | Reran the focused reproducibility test set after remediation 11; all targeted checks passed, and staged diff checks remained clean. Docker build remains blocked by the local daemon state. | `uv run --locked pytest -q tests/environments/test_token.py tests/environments/test_config.py::test_cache_dir_defaults_under_contextwiki_home tests/fetching/test_connectors.py::test_build_source_registry_disables_tistory_until_blog_is_configured tests/test_app_composition.py::test_create_app_registers_slim_mcp_tools_and_core_sources tests/scripts/test_live_query_smoke.py tests/scripts/test_demo_public_flow.py`; `git diff --check`; `git diff --cached --check`; `docker build -t contextwiki-issue33 .` -> `Cannot connect to the Docker daemon ...` |
| Single-reviewer finding 12 | completed/actionable | One reviewer found one remaining docs mismatch: `.env.example` still mentioned bare `python main.py`, while the reproducible local path documented in the README is `uv run --locked python main.py`. | Reviewer: Bernoulli |
| Remediation 12 | completed | Narrowed the `.env.example` guidance comment to the documented `uv run --locked python main.py` local path so the template no longer suggests an unsupported first-run launch command. | `.env.example`; this file |
| Reverification 12 | completed/blocked | Reran staged diff checks after remediation 12; both working-tree and cached whitespace checks passed. Docker build remains blocked by the local daemon state. | `git diff --check`; `git diff --cached --check`; `docker build -t contextwiki-issue33 .` -> `Cannot connect to the Docker daemon ...` |
| Review pass 13 | completed/actionable | Fresh five-reviewer pass found one remaining plan-traceability issue: the prior one-off reviewer findings were labeled as full review passes, and there was not yet a recorded clean five-reviewer pass after the latest remediation. | Reviewers: McClintock, Russell, Noether, Hypatia, Ramanujan |
| Review remediation 13 | completed | Reclassified the one-off reviewer rows as single-reviewer findings instead of formal review passes and prepared a fresh clean five-reviewer gate from the remediated staged state. | this file |
| Review pass 14 | completed/actionable | Fresh five-reviewer pass found one remaining plan-only issue: the pass-14 result had not yet been written back into the plan while review was still in progress, and one reviewer also caught a `contextwiki-issue33` vs `contextwiki` tag mismatch in the plan's Docker smoke example. | Reviewers: Aquinas, Darwin, Mencius, Laplace, Sagan |
| Review remediation 14 | completed | Recorded the actual pass-14 outcome in the plan and aligned the plan's Docker smoke example to the `contextwiki-issue33` image tag used by the documented build command. | this file |
| Reverification 14 | completed/blocked | Reran staged diff checks after remediation 14; both working-tree and cached whitespace checks passed. Docker build remains blocked by the local daemon state. | `git diff --check`; `git diff --cached --check`; `docker build -t contextwiki-issue33 .` -> `Cannot connect to the Docker daemon ...` |
| Review pass 15 | completed/actionable | Fresh diff-local reviewer pass found two remaining issues: the public demo path could still inherit the home-scoped default `Settings.cache_dir`, and the `--json` wording overstated how fully live-smoke payloads were sanitized. | Reviewers: Peirce, Euler, Einstein, Hegel, Bacon |
| Review remediation 15 | completed | Forced the demo harness to use a temp `Settings.cache_dir` and restore it afterward, added regression coverage for that isolation, and narrowed the README/CLI wording for `--json` to describe partial redaction instead of claiming full path-safe sanitization. | `scripts/demo_public_flow.py`; `tests/scripts/test_demo_public_flow.py`; `README.md`; `scripts/live_query_smoke.py`; this file |
| Reverification 15 | completed/blocked | Reran the focused demo/live-smoke reproducibility test set after remediation 15; all targeted checks passed, and staged diff checks remained clean. Docker build remains blocked by the local daemon state. | `uv run --locked pytest -q tests/scripts/test_demo_public_flow.py tests/scripts/test_live_query_smoke.py tests/environments/test_token.py tests/environments/test_config.py::test_cache_dir_defaults_under_contextwiki_home tests/fetching/test_connectors.py::test_build_source_registry_disables_tistory_until_blog_is_configured tests/test_app_composition.py::test_create_app_registers_slim_mcp_tools_and_core_sources`; `git diff --check`; `git diff --cached --check`; `docker build -t contextwiki-issue33 .` -> `Cannot connect to the Docker daemon ...` |
| Review pass 16 | completed/actionable | Fresh diff-local reviewer pass found one remaining plan-traceability issue: the `Files likely to change` inventory still omitted `scripts/demo_public_flow.py` and `tests/scripts/test_demo_public_flow.py` even though remediation 15 changed both files. | Reviewers: Boole, Faraday, Gibbs, Goodall, Zeno |
| Review remediation 16 | completed | Added the missing demo-flow script and test files to the plan inventory so the plan matches the staged diff. | this file |
| Environment debugging | completed | Root-caused the Docker blocker to the local Docker Desktop credential helper path: public image pulls hung with `credsStore: desktop`, but succeeded immediately with a temporary Docker config that omitted the credential helper for anonymous pulls. | `docker pull python:3.13.9-slim` hung; `docker run alpine:3.20 ...` failed with `error getting credentials`; `docker --config /tmp/docker-nocreds... pull --platform linux/arm64 python:3.13.9-slim` succeeded |
| Review pass 17 | completed/actionable | Fresh runtime verification found two container-only issues in the shipped image: `scripts/demo_public_flow.py --json` could not import `api` when run directly inside the container, and the demo flow touched `Settings.embed_model` in a way that required a live OpenAI key before the intended `MockEmbedding` override. | Container runtime verification |
| Review remediation 17 | completed | Added repo-root `sys.path` bootstrapping to `scripts/demo_public_flow.py`, switched demo embed-model preservation to the internal `_embed_model` state so no OpenAI key is needed before `MockEmbedding` is installed, and added direct-script regression coverage plus tolerant JSON parsing for demo-script stdout banners. | `scripts/demo_public_flow.py`; `tests/scripts/test_demo_public_flow.py`; this file |
| Reverification 17 | completed/actionable | Reran focused script tests and the Docker path, which exposed one more teardown-state bug and two plan-traceability mismatches after the initial container success. | `uv run --locked pytest -q tests/scripts/test_demo_public_flow.py tests/scripts/test_live_query_smoke.py`; `docker --config /tmp/docker-nocreds... build -t contextwiki-issue33 .`; `docker run --rm --env-file /tmp/contextwiki-issue33.env -v contextwiki_data_issue33:/home/appuser/.mcp_content_search contextwiki-issue33 /app/.venv/bin/python scripts/demo_public_flow.py --json` |
| Review pass 18 | completed/actionable | Fresh follow-up review found that the demo embed-model restore path still used the property setter when the prior `_embed_model` state was `None`, and the plan wording lagged behind the broadened runtime-script scope plus the `docker --config /tmp/docker-nocreds...` verification fallback. | Reviewers: Schrodinger, Turing, James, Leibniz, Ampere |
| Review remediation 18 | completed | Restored the prior embed-model state by writing `Settings._embed_model` directly, updated the regression test to model the real fresh-process `_embed_model is None` case, and refreshed the plan wording to describe the runtime-script remediation scope plus the Docker credential-helper fallback path. | `scripts/demo_public_flow.py`; `tests/scripts/test_demo_public_flow.py`; this file |
| Reverification 18 | completed | Reran focused script tests, rebuilt the image, and reran the in-container demo JSON path successfully with the latest fixes. | `uv run --locked pytest -q tests/scripts/test_demo_public_flow.py tests/scripts/test_live_query_smoke.py`; `docker --config /tmp/docker-nocreds... build -t contextwiki-issue33 .`; `docker run --rm --env-file /tmp/contextwiki-issue33.env -v contextwiki_data_issue33:/home/appuser/.mcp_content_search contextwiki-issue33 /app/.venv/bin/python scripts/demo_public_flow.py --json` |
