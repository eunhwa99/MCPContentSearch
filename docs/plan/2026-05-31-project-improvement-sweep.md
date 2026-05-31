# Project Improvement Sweep

## User Request

Implement all eight improvement items identified in the project review:

1. Fix the default test entrypoint/package import behavior.
2. Align dependency manifests.
3. Prevent `.idea` IDE files from polluting version control.
4. Split `web_console/app.py` into maintainable modules.
5. Split `fetching/web_docs.py` helper concerns into maintainable modules.
6. Improve background indexing task status visibility.
7. Add evaluation/answer-quality groundwork for future LLM answer generation.
8. Update project documentation so the current architecture and limitations are in sync.

## Branch Preflight Result

- Starting checkout: `/Users/eunhwa/IdeaProjects/MCPContentSearch` on `main`.
- Starting worktree state: dirty due to staged/modified/untracked `.idea` files. No target files were edited there.
- Safety action: fetched `origin main` and created isolated worktree `/private/tmp/MCPContentSearch-project-improvements`.
- Task branch: `feature/project-improvement-sweep` tracking `origin/main`.
- Current isolated branch state: clean at `02a0eba`.
- Cleanup: no local branch or linked worktree cleanup performed because existing linked worktrees are outside this task and may contain user/agent work.

## Scope and Non-goals

Scope:

- Keep MCP tool names and existing response shapes stable unless adding additive status details.
- Make `uv run pytest` work from the repository root without manually setting `PYTHONPATH`.
- Make dependency declarations consistent and remove stale placeholder metadata.
- Ignore personal IDE files going forward without deleting user files from the original dirty checkout.
- Extract bounded helper modules from `web_console/app.py` and `fetching/web_docs.py` while preserving behavior.
- Add caller-visible status for legacy background indexing tasks without mutating user Chroma or SQLite data.
- Add deterministic, local evaluation scaffolding for answer quality/grounding that can run in default verification.
- Update README and `docs/contextwiki-core-understanding.md` to describe the current Phase C/C.5 state and the new verification defaults.

Non-goals:

- Do not inspect or mutate local user ChromaDB or SQLite metadata.
- Do not run live Notion, Tistory, GitHub, Web, or LLM validation.
- Do not implement full LLM answer generation in this sweep; add groundwork and documentation only.
- Do not delete or alter `.idea` files in the original dirty checkout.
- Do not change external connector public contracts beyond internal refactoring.

## Acceptance Criteria

- `uv run pytest -q` passes from repository root without `PYTHONPATH=.`.
- `./scripts/verify_all.sh` passes and no longer needs to compensate for broken package import behavior beyond normal environment setup.
- `pyproject.toml`, `requirements.txt`, and README dependency/install guidance agree on project name, Python version, and direct runtime dependencies.
- `.gitignore` ignores `.idea/`.
- `web_console/app.py` is smaller and delegates Codex CLI, security/payload helpers, and/or target sync behavior to dedicated modules with focused tests still passing.
- `fetching/web_docs.py` delegates URL safety/redaction and/or media classification helper behavior to dedicated modules with focused tests still passing.
- Background indexing tasks are tracked in memory with observable status and errors for MCP callers, while keeping existing tool response compatibility.
- Evaluation scaffolding exists under an `evals/` or test-backed equivalent path and has deterministic tests that do not call live external APIs.
- `README.md` and `docs/contextwiki-core-understanding.md` describe current Auto Wiki, Web Console, evaluation, background-task status, and known limitations accurately.
- No secret values are added to docs, logs, tests, or generated files.

## Step Breakdown and Worker Boundaries

### package-manifest-worker

- Owned files: `pyproject.toml`, `requirements.txt`, `.gitignore`, README install/test sections, package/import tests if needed.
- Goal: fix test import behavior and dependency drift.
- Acceptance: `uv run pytest -q` can collect tests without `PYTHONPATH`; dependency manifests are consistent.

### web-console-worker

- Owned files: `web_console/app.py`, new `web_console/*.py` helper modules, `tests/web_console/test_app.py`.
- Goal: split large web console concerns while preserving HTTP behavior and safety checks.
- Acceptance: focused web console tests pass; route contracts remain stable.

### web-docs-worker

- Owned files: `fetching/web_docs.py`, new `fetching/web_*.py` helper modules, `tests/fetching/test_web_docs.py`, connector e2e tests if affected.
- Goal: split crawler helper concerns without changing crawl safety behavior.
- Acceptance: focused web docs tests pass; no live network validation required.

### background-status-worker

- Owned files: `api/tools.py`, `search/dynamic_search.py`, new `indexing/background_tasks.py` or equivalent, `tests/api/test_tools_contract.py`, related search tests.
- Goal: make background indexing task status visible to MCP clients.
- Acceptance: additive status can be queried through existing status tooling; failures are captured without leaking secrets.

### eval-docs-worker

- Owned files: `evals/**`, `tests/evals/**`, README, `docs/contextwiki-core-understanding.md`.
- Goal: add deterministic answer-quality eval groundwork and synchronize docs.
- Acceptance: eval tests run locally without live APIs; docs reflect current implementation and future limits.

The main agent owns integration, cross-file conflict resolution, verification, review gates, commit, push, and PR delivery.

## Files Likely to Change

- `.gitignore`
- `pyproject.toml`
- `requirements.txt`
- `README.md`
- `docs/contextwiki-core-understanding.md`
- `api/tools.py`
- `search/dynamic_search.py`
- `web_console/app.py`
- new `web_console/*.py`
- `fetching/web_docs.py`
- new `fetching/web_*.py`
- new `indexing/background_tasks.py`
- new `evals/**`
- `tests/**`

## Test and Verification Plan

Focused checks:

```bash
uv run pytest -q tests/api/test_tools_contract.py
uv run pytest -q tests/web_console/test_app.py
uv run pytest -q tests/fetching/test_web_docs.py tests/e2e/test_phase_b_connectors_flow.py
uv run pytest -q tests/evals
node --check web/app.js
```

Integration checks:

```bash
python -m compileall api core environments fetching indexing search storage wiki web_console main.py
uv run pytest -q
./scripts/verify_all.sh
python scripts/smoke_generate_wiki_page.py --mode fake
git diff --check
```

No live external smoke is planned because the request does not approve using real Notion/Tistory/GitHub/Web/LLM credentials.

## Architecture/ADR Constraints

- ADR 0001: preserve layered boundaries; keep tool handlers thin and delegate behavior to services.
- ADR 0002/0003: do not inspect or mutate user SQLite/Chroma data; tests use fakes or temporary state.
- ADR 0004: keep GitHub/Web connector behavior bounded, robots-aware, and secret-safe.
- ADR 0005: keep LLM synthesis opt-in; answer-quality groundwork must not send evidence to external models.

## Risks and Rollback Notes

- Broad refactors can accidentally alter MCP or web console response shapes; protect with existing contract tests.
- Dependency alignment can change lockfile contents; verify with `uv run pytest -q` and `./scripts/verify_all.sh`.
- Background task status is process-local and should be documented as runtime status, not persisted lifecycle state.
- If a refactor becomes too risky, rollback point is the clean branch base `02a0eba`.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Dirty main checkout preserved; isolated feature worktree created from `origin/main`. | `git fetch origin main`; `git worktree add -b feature/project-improvement-sweep /private/tmp/MCPContentSearch-project-improvements origin/main` |
| Plan document | completed | Plan created with all eight requested improvements in scope. | `docs/plan/2026-05-31-project-improvement-sweep.md` |
| Worker orchestration | completed | Spawned bounded workers for package hygiene, web console split, web docs split, background status, and eval/docs. | `tool_search`; workers `019e7d29-7963-7292-ae46-359b96603ffe`, `019e7d29-943c-7003-a49b-da9335c462f7`, `019e7d29-af47-7d61-a441-2ab69fd6c39e`, `019e7d29-c9b3-7920-9e5f-9d83f9e0488a`, `019e7d29-e5ba-7e90-b1fa-e569393d2352` |
| Implementation | completed | Applied package hygiene, web console split, web docs split, background task status, deterministic eval groundwork, and docs sync changes. | New modules under `web_console/`, `fetching/`, `indexing/`, and `evals/`; updated contracts and docs |
| Focused verification | completed | Focused touched-area tests and syntax checks pass. | `uv run pytest -q tests/api/test_tools_contract.py` (10 passed); `uv run pytest -q tests/web_console/test_app.py` (85 passed); `uv run pytest -q tests/fetching/test_web_docs.py` (249 passed); `uv run pytest -q tests/evals` (6 passed); `node --check web/app.js` |
| Full verification | completed | Full local verification and fake wiki smoke pass without live external credentials. | `python -m compileall api core environments fetching indexing search storage wiki web_console evals main.py`; `uv run pytest -q` (747 passed); `./scripts/verify_all.sh` (747 passed); `python scripts/smoke_generate_wiki_page.py --mode fake`; `git diff --check` |
| Review pass 1 | completed | Fresh five-reviewer pass found actionable issues: broader background task error redaction, Web Console browser verification, eval citation validation, active task retention over history cap, and README tree sync. | Reviewers `019e7d34-5b9b-7480-ac34-c9b60c65563c`, `019e7d35-1717-7753-8a0c-973eb2abbe7c`, `019e7d35-194a-7d61-b35b-6f317f5a1446`, `019e7d35-1b53-7ab0-ad03-1404ffae4a1a`, `019e7d35-1dc2-7190-a294-32701c6aa968` |
| Review pass 1 remediation | completed | Added broad secret-token redaction tests/fix for background task errors, preserved active records over the registry history cap, made eval minimum citation count require valid `chunk_id`s, and synchronized the README tree. | `uv run pytest -q tests/indexing/test_background_tasks.py tests/evals tests/api/test_tools_contract.py tests/web_console/test_app.py` -> 104 passed; `python -m compileall indexing evals api search web_console tests/indexing/test_background_tasks.py tests/evals/test_answer_quality.py tests/api/test_tools_contract.py tests/web_console/test_app.py`; `git diff --check` |
| Browser verification | completed | Started a fake in-memory local Web Console at `127.0.0.1:8766` and exercised browser-facing health/source render, empty Answer and Target Sync validation failures, grounded Codex answer success with citations/used chunks/download buttons, and Web URL target placeholder switching. The fake app used no Chroma, SQLite, credentials, live APIs, or LLM calls. | `uv run --python 3.13 uvicorn contextwiki_browser_smoke_app:app --app-dir /private/tmp --host 127.0.0.1 --port 8766`; Browser snapshot/actions; screenshot `/private/tmp/contextwiki-console-browser-smoke.png` |
| Full verification after remediation | completed | Re-ran broad local verification after fixes. | `uv run pytest -q` -> 750 passed; `./scripts/verify_all.sh` -> 750 passed; `python scripts/smoke_generate_wiki_page.py --mode fake`; `node --check web/app.js`; `uv lock --check`; `git diff --check` |
| Review pass 2 | completed | Fresh five-reviewer pass had four clean reviewers and one actionable finding: top-level indexer status/log errors needed the same secret redaction as background task records, and the background status note needed one update. | Reviewers `019e7d41-4e57-7943-a71a-dd16722ab62a`, `019e7d41-5354-7840-be7d-473c86d20cf5`, `019e7d41-5881-7c02-ae41-a9cae8974b07`, `019e7d41-5e4c-70f0-89b5-8ebdfb4a493f`, `019e7d41-64ac-7d33-9ca1-9f4432167004` |
| Review pass 2 remediation | completed | Added redaction regressions for `ContentIndexer` status/log/exception text and top-level `get_index_status` messages, applied the shared redactor to both paths, and updated the background status explanation. | RED `uv run pytest -q tests/indexing/test_indexer_redaction.py tests/api/test_tools_contract.py::test_get_index_status_redacts_top_level_error_message` -> 2 failed; GREEN same command -> 2 passed; `uv run pytest -q tests/indexing/test_indexer_redaction.py tests/indexing/test_background_tasks.py tests/api/test_tools_contract.py tests/evals tests/web_console/test_app.py` -> 106 passed |
| Full verification after review pass 2 remediation | completed | Re-ran broad local verification after the second remediation. | `uv run pytest -q` -> 752 passed; `./scripts/verify_all.sh` -> 752 passed; `python scripts/smoke_generate_wiki_page.py --mode fake`; `node --check web/app.js`; `uv lock --check`; `git diff --check` |
| Review pass 3 | completed | Fresh five-reviewer pass had two clean reviewers and three actionable reports across two issues: sanitized `IndexingError` still chained raw causes into tracebacks, and background tasks needed strong references while pending. | Reviewers `019e7d4a-0fbe-79c0-ac45-6f37567e4915`, `019e7d4a-1278-7d91-bfc6-bed8509ca1a6`, `019e7d4a-1513-7e03-82ac-0a497651f552`, `019e7d4a-1802-7652-96de-615fdecdd604`, `019e7d4a-1a88-7072-93cf-e38577348a37` |
| Review pass 3 remediation | completed | Added traceback/cause redaction regression and strong-reference task retention regression, raised sanitized `IndexingError` without raw exception chaining, and retained pending task handles in the background registry until completion. | RED `uv run pytest -q tests/indexing/test_indexer_redaction.py tests/indexing/test_background_tasks.py` -> 2 failed, 2 passed; GREEN same command -> 4 passed; `uv run pytest -q tests/indexing/test_indexer_redaction.py tests/indexing/test_background_tasks.py tests/api/test_tools_contract.py tests/evals tests/web_console/test_app.py` -> 107 passed |
| Full verification after review pass 3 remediation | completed | Re-ran broad local verification after the third remediation. | `uv run pytest -q` -> 753 passed; `./scripts/verify_all.sh` -> 753 passed; `python scripts/smoke_generate_wiki_page.py --mode fake`; `node --check web/app.js`; `uv lock --check`; `git diff --check` |
| Review pass 4 | completed | Fresh five-reviewer pass reported no actionable findings from all reviewers. | Reviewers `019e7d50-78c1-77d0-a77b-7dacaa01dc5a`, `019e7d50-7bde-7393-9ee5-f74312a2993d`, `019e7d50-7e52-7230-860b-8cb7a0b6d88c`, `019e7d50-811d-74a1-961a-db4b8081e745`, `019e7d50-8425-7f13-be93-269db042515c` |
| Review gate | completed | Required latest five-reviewer pass is clean. | Pass 4: 5/5 no actionable findings |
| PR delivery | completed | Committed, pushed, and opened a `main`-base PR after clean review. | Commit `8b97c54`; branch `feature/project-improvement-sweep`; PR https://github.com/eunhwa99/MCPContentSearch/pull/13 |
