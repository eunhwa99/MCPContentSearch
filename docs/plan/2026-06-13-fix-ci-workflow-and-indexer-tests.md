# Fix CI Workflow And Indexer Tests

## User request

Fix the failing CI workflow after identifying why GitHub Actions was failing without useful logs.

## Branch preflight result

- Starting worktree: `/Users/eunhwa/.codex/worktrees/e1f4/MCPContentSearch`
- Initial state: clean detached HEAD at `223a3d9`
- Network was available; fetched `origin/main`
- Local `main` is already checked out in another linked worktree, so this worktree could not safely switch to `main`
- Created fresh branch `feature/fix-ci-workflow-and-indexer-tests` from `origin/main` in the current clean worktree
- Follow-up PR delivery used a clean isolated worktree at `/private/tmp/MCPContentSearch-ci-fix-clean` because the Codex worktree Git pointer was stale and the root repo contained unrelated local changes
- Current PR branch: `feature/fix-ci-workflow-and-indexer-tests-pr-clean`

## Scope and non-goals

In scope:

- Fix the GitHub Actions workflow parse failure that prevents jobs from starting
- Fix the reproduced local test mismatch around `ContentIndexer.delete_documents_by_ids`
- Fix the CI-only non-live test failures caused by default embedding resolution without `OPENAI_API_KEY`
- Re-run the relevant local verification to confirm the CI path is healthy

Non-goals:

- No MCP contract changes
- No search, indexing, or storage behavior redesign
- No local data inspection, deletion, or migration

## Acceptance criteria

- GitHub Actions workflow YAML no longer uses the invalid `runner.temp` expression placement that caused the parse failure
- `tests/indexing/test_index_manager.py` passes against the current async `ContentIndexer.delete_documents_by_ids` contract
- `./scripts/verify_all.sh` passes locally, or any remaining blocker is documented with exact failing output
- The CI non-live pytest step and functional E2E step no longer require a real `OPENAI_API_KEY` to resolve the default embed model path

## Step breakdown

1. Confirm the root cause from local reproduction and remote Actions annotations
2. Patch `.github/workflows/ci.yml` to remove the invalid job-level cache-path override and keep `setup-uv` cache handling intact
3. Patch `tests/indexing/test_index_manager.py` to exercise the async delete method correctly
4. Patch the CI workflow test caller surfaces so non-live pytest and functional E2E resolve mock embeddings without `OPENAI_API_KEY`
5. Run focused verification, then the repo verification script
6. Run the required five-reviewer subagent review loop before PR delivery

## Files likely to change

- `.github/workflows/ci.yml`
- `tests/indexing/test_index_manager.py`
- `docs/plan/2026-06-13-fix-ci-workflow-and-indexer-tests.md`

## Test and verification plan

- `uv run --locked pytest -q tests/indexing/test_index_manager.py`
- `OPENAI_API_KEY='' IS_TESTING=1 uv run --locked pytest -q tests/e2e/test_contextwiki_flow.py::test_contextwiki_temp_chroma_e2e_sync_search_fetch_and_answer tests/scripts/test_demo_public_flow.py`
- `OPENAI_API_KEY='' IS_TESTING=1 ./scripts/verify_functional_e2e.sh`
- `./scripts/verify_all.sh`
- `actionlint .github/workflows/ci.yml`

## Functional smoke matrix

| Feature or workflow | Caller surface | Safe data mode | Expected result | Command/action | Planned result |
| --- | --- | --- | --- | --- | --- |
| GitHub Actions parse validity | workflow YAML | local repo only | workflow is parse-safe and no longer references invalid job-env context | `actionlint .github/workflows/ci.yml` plus workflow diff inspection | passed: removed invalid job-level `runner.temp` usage, left `setup-uv` action-managed caching intact, and `actionlint` returned clean |
| CI non-live embedding fallback | pytest with CI-like env | local repo only | non-live tests use mock embeddings without a real OpenAI key | `OPENAI_API_KEY='' IS_TESTING=1 uv run --locked pytest -q tests/e2e/test_contextwiki_flow.py::test_contextwiki_temp_chroma_e2e_sync_search_fetch_and_answer tests/scripts/test_demo_public_flow.py` | passed: 4 tests |
| CI functional E2E embedding fallback | retained functional E2E script with CI-like env | local repo only | retained functional E2E path uses mock embeddings without a real OpenAI key | `OPENAI_API_KEY='' IS_TESTING=1 ./scripts/verify_functional_e2e.sh` | passed: 325 tests |
| Managed/raw vector cleanup contract | pytest | fake collection only | async delete path records the expected Chroma filters | `uv run --locked pytest -q tests/indexing/test_index_manager.py` | passed: 6 tests |
| Full retained verification | repo script | local repo and temp test data | compile, lint, type, tests, and functional E2E pass | `OPENAI_API_KEY='' IS_TESTING=1 ./scripts/verify_all.sh` | passed: compile, Ruff, mypy, Bandit, 533 non-live tests with 86.35% coverage, and 325 retained functional tests |

## Architecture and ADR constraints

- ADR 0001: keep layered boundaries intact; this fix must not move indexing behavior across modules
- ADR 0006: keep CI and verification focused on the slim retained MCP core; do not reintroduce removed surfaces

## Risks and rollback notes

- If GitHub-hosted runners later need an explicit uv cache override, use a `setup-uv`-supported cache configuration rather than reintroducing the invalid job-level `runner.temp` expression
- The CI-only embedding fallback should stay scoped to the CI test/e2e workflow steps so production runtime defaults do not silently change
- The indexer/test fix must preserve the current production contract, because `IngestionService` already supports both sync and async delete implementations
- No user-data rollback concerns apply because verification uses fake collections and temporary test storage

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created a clean isolated PR worktree from `origin/main` to avoid stale Git metadata and unrelated local changes. | `git worktree add -b feature/fix-ci-workflow-and-indexer-tests-pr-clean ... origin/main` |
| Root-cause investigation | completed | Confirmed the remote workflow parse failure and the local async test mismatch. | GitHub Actions annotation for `.github/workflows/ci.yml`; `./scripts/verify_all.sh` failing at `tests/indexing/test_index_manager.py` |
| Post-PR CI investigation | completed | Confirmed the follow-up CI failure now happens inside the non-live pytest step because some tests touch `Settings.embed_model` under a CI environment with no `OPENAI_API_KEY`. | GitHub Actions run `27453970243`; failures in `tests/e2e/test_contextwiki_flow.py` and `tests/scripts/test_demo_public_flow.py` |
| Worker orchestration decision | completed | Treated this as atomic because the change is limited to one workflow file and one focused test file with no shared multi-owner implementation slice; self-implementation is lower risk than artificial worker overhead. | Current plan scope and file list |
| Implementation | completed | Removed the invalid job-level `runner.temp` usage without adding a later cache-path override, and updated the two indexer tests to execute the async delete method with `asyncio.run(...)`. | `.github/workflows/ci.yml`; `tests/indexing/test_index_manager.py` |
| Focused verification | completed | The targeted indexer test file passed in the clean PR worktree. | `uv run --locked pytest -q tests/indexing/test_index_manager.py` |
| Full verification | completed | The repo verification script passed end to end in the clean PR worktree. | `./scripts/verify_all.sh` |
| Review gate pass 1 | completed | Fresh five-reviewer pass found one actionable plan-status drift issue and no code or workflow correctness findings. | Reviewer agents `019ebebf-4e1e-7a11-904c-d64967836551`, `019ebebf-6e1d-74d0-ba8f-f945169a3d2e`, `019ebebf-8fd5-7980-b9a4-11fde60f10ca`, `019ebebf-b281-7db0-a44a-b04ec9db596b`, `019ebebf-db20-7440-bad4-116bdcd6476d` |
| Review fix verification | completed | Updated the plan to reflect the actual verification and review state; docs-only diff remains clean. | `git diff --check` |
| Review gate pass 2 | completed | Fresh five-reviewer pass found additional plan-traceability issues and pointed out that the late `UV_CACHE_DIR` export could bypass `setup-uv` cache reuse. No code or async-contract correctness issues were reported. | Reviewer agents `019ebec1-9dfb-7ed2-9471-97d75ce67bf6`, `019ebec1-c37c-7782-a568-44f3e9a92b2d`, `019ebec1-e8db-7841-ad3e-bc395bfdbb45`, `019ebec2-0b19-7e30-996f-a739a114b8cf`, `019ebec2-2fc9-7d81-af25-0d4e7704db7e` |
| Review fix verification 2 | completed | Removed the current-pass pending placeholder, removed the late cache-path override step, and validated the new plan file with staged cached-diff checks. | `git diff --check`; `git add docs/plan/2026-06-13-fix-ci-workflow-and-indexer-tests.md`; `git diff --cached --check` |
| Review gate pass 3 | completed | Fresh five-reviewer pass found remaining plan-traceability drift: the step breakdown and rollback note still described the abandoned cache-path-relocation approach, and the progress log still lacked a subsequent clean pass after pass 2. The workflow fix and async test change themselves received no actionable correctness findings. | Reviewer agents `019ebec5-8f1d-7482-8881-314ca96c2d0e`, `019ebec5-b5db-7a11-82e5-a4b7d694d3af`, `019ebec5-da75-7ca0-8395-ad797298fa67`, `019ebec5-ff94-7811-9607-c8c986ae479d`, `019ebec6-2b63-7d92-859e-917408a2df11` |
| Review fix verification 3 | completed | Updated the plan so the implementation narrative matches the shipped workflow diff and prepared for a fresh clean reviewer pass. | plan step breakdown and rollback note updated to match `.github/workflows/ci.yml` |
| Review gate pass 4 | completed | Fresh five-reviewer pass found one remaining PR-readiness traceability issue: the plan still did not record a final clean five-reviewer pass after pass 3. No actionable workflow or async-test correctness findings were reported. | Reviewer agents `019ebec9-0511-7a53-8e5b-d0f047659df9`, `019ebec9-07d8-7c93-9a12-034f3a0e8416`, `019ebec9-0a84-7d21-a8cc-6b6cf488a014`, `019ebec9-0e71-7933-8f1e-84b7b900c96c`, `019ebec9-132f-7673-96bf-384963e5d4e3` |
| Review fix verification 4 | completed | Recorded pass 4 so the next reviewer pass can validate the full trace with no pending review-state drift. | progress log updated with pass 4 reviewer evidence |
| Review gate pass 5 | completed | Fresh five-reviewer pass repeated the temporal traceability concern that the current pass was not yet recorded in the plan, and one reviewer also requested direct workflow-level validation rather than relying only on local Python/test verification. No code-correctness issues were reported in the workflow or async test changes. | Reviewer agents `019ebecb-5d1f-7241-8673-e489ca3b3c85`, `019ebecb-648c-7092-84b1-1190d33faafc`, `019ebecb-68cf-74a1-8d94-9083a705f4bf`, `019ebecb-6f2f-7f63-b017-52bd8dbec1da`, `019ebecb-7c0e-7e83-99b9-4b4c151ceefe` |
| Review fix verification 5 | completed | Added workflow-level parser validation with `actionlint` and documented that future reviewer prompts should not treat the in-flight pass's own absence from the progress log as a standalone defect, because that is resolved only after the pass completes. | `actionlint .github/workflows/ci.yml` |
| Review gate pass 6 | completed | Fresh five-reviewer pass, instructed to ignore the self-referential in-flight-pass bookkeeping artifact, reported no actionable findings. Reviewers agreed the workflow fix, async test updates, verification history, and plan trace are PR-ready once this clean pass is recorded. | Reviewer agents `019ebed0-4e7b-72e0-89b5-f8fcff5ee30f`, `019ebed0-5141-7561-846a-4d3a6ab29057`, `019ebed0-54b8-75b3-98ff-ee95b6047896`, `019ebed0-5859-7de3-bde4-d91b40deb43b`, `019ebed0-60fd-7de0-97d5-9500a208d924` |
| Follow-up implementation | completed | Scoped the CI-only embedding fallback to both GitHub Actions test caller surfaces by setting `IS_TESTING=1` in the non-live pytest step and the functional E2E step, so CI runners without `OPENAI_API_KEY` resolve `MockEmbedding` instead of OpenAI defaults. | `.github/workflows/ci.yml` |
| Follow-up verification | completed | Reproduced the CI failure mode locally with an empty OpenAI key and confirmed the guarded pytest step, guarded functional E2E step, targeted index-manager tests, `actionlint`, the full CI-like non-live pytest command, and the full `verify_all.sh` flow all pass. | `OPENAI_API_KEY='' IS_TESTING=1 uv run --locked pytest -q tests/e2e/test_contextwiki_flow.py::test_contextwiki_temp_chroma_e2e_sync_search_fetch_and_answer tests/scripts/test_demo_public_flow.py`; `OPENAI_API_KEY='' IS_TESTING=1 ./scripts/verify_functional_e2e.sh`; `uv run --locked pytest -q tests/indexing/test_index_manager.py`; `actionlint .github/workflows/ci.yml`; `OPENAI_API_KEY='' IS_TESTING=1 uv run --locked pytest -m \"not live\" --cov=api --cov=core --cov=environments --cov=fetching --cov=indexing --cov=search --cov=storage --cov-report=term-missing`; `OPENAI_API_KEY='' IS_TESTING=1 ./scripts/verify_all.sh` |
| Review-directed follow-up | completed | Confirmed the functional E2E gate still hit the same no-key embed-model path, then extended the CI-only `IS_TESTING=1` guard to that workflow step as well and revalidated the exact CI-like fallback path end to end. | `OPENAI_API_KEY='' ./scripts/verify_functional_e2e.sh` failing locally before the step env change; `OPENAI_API_KEY='' IS_TESTING=1 ./scripts/verify_functional_e2e.sh` passing after it |
| Review gate pass 7 | completed | Fresh five-reviewer pass found no remaining workflow correctness issue; it only reported plan traceability drift because the latest follow-up and re-verification were not yet followed by a fresh clean recorded pass. | Reviewer agents `019ebedf-97ea-7ff2-bfcc-80aa1d5557db`, `019ebedf-9cf9-78d3-befa-0bf7db64e590`, `019ebedf-a462-7fb0-8264-dd6194213b53`, `019ebedf-adfd-7830-8d53-de88d3ff26ea`, `019ebedf-b7e3-7d62-86bf-bca9e5f69bd9` |
| Review gate pass 8 | completed | Fresh five-reviewer pass, instructed to ignore the self-referential in-flight-pass bookkeeping artifact, reported no actionable findings. Reviewers agreed the CI workflow fix, follow-up keyless-embedding guard, CI-like verification, and updated plan trace are PR-ready once this clean pass is recorded. | Reviewer agents `019ebee3-99d9-7c12-b631-b645421d7953`, `019ebee3-a211-7d21-8868-b7bd8427521b`, `019ebee3-a6b0-70d0-82c9-fe8ff51d6de0`, `019ebee3-afdf-7430-95b8-44e3f8f79b43`, `019ebee3-bc01-7da0-b44c-67dd869c2b9c` |
