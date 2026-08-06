# Indexer Safe Traceback Diagnostics

## User Request

After investigating the `Indexing failed: 'bool' object is not subscriptable` sync failure, add safer diagnostics so the next indexing failure identifies whether the exception came from existing-metadata filtering, embedding, LlamaIndex insert/build, or Chroma write internals without logging secrets or user document content.

## Branch Preflight Result

- Starting worktree: `/Users/eunhwa/IdeaProjects/MCPContentSearch`
- Starting state: clean `main`, already at `origin/main` merge commit for PR #98.
- Commands: `git status --short --branch`, `git branch -vv`, `git worktree list`, `git fetch origin main`, `git pull --ff-only origin main`.
- Task branch: `feature/indexer-safe-traceback-diagnostics`.

## Scope and Non-Goals

Scope:

- Add sanitized indexing traceback/stage diagnostics in `indexing/indexer.py`.
- Keep exception messages redacted via existing `safe_error_message`.
- Add tests proving diagnostic output includes stage/frame hints but not secret payloads.

Non-goals:

- Do not run live Notion/OpenAI/Chroma mutation against user data.
- Do not print document content, API keys, or full local user paths in logs.
- Do not change MCP response contracts or sync failure terminal semantics.

## Acceptance Criteria

- When `_filter_documents`, `VectorStoreIndex.from_documents`, or `index.insert` raises, logs include an indexing operation/stage and sanitized traceback frame summary.
- Logged diagnostics do not include secret-like exception text or document content.
- Existing failure wrapping still raises `IndexingError("Indexing failed: <sanitized>")`.
- Focused tests and `./scripts/verify_all.sh` pass.

## Worker Orchestration

Main-agent direct implementation is allowed: expected production change is atomic and localized to `indexing/indexer.py` logging diagnostics plus focused tests. Three read-only reviewer subagents remain required after verification.

## TDD RED Evidence

- Command: `uv run pytest -q tests/indexing/test_indexer_redaction.py::test_content_indexer_logs_sanitized_failure_stage_and_trace_frames tests/indexing/test_indexer_redaction.py::test_chroma_worker_logs_sanitized_operation_and_trace_frames`
- Test layers/names: unit; sanitized indexer diagnostics for top-level indexing stage and Chroma worker operation.
- Non-zero exit code: `1`
- Expected failure signature: first test log only has `Indexing error: filter failed token=<redacted>` without `indexing_stage=` or `trace_frames=`; second test fails because `_run_chroma_in_thread()` forwards `operation=` to the worker callable instead of using it as diagnostic context.
- Missing-behavior explanation: indexing failure logs currently preserve redacted message only, so they do not identify the failed stage or safe traceback frames needed to distinguish embedding vs Chroma write failures.
- Predates production edits: yes; only tests and this plan were edited before the RED run.

## TDD GREEN Evidence

- Focused RED-to-GREEN command: `uv run pytest -q tests/indexing/test_indexer_redaction.py::test_content_indexer_logs_sanitized_failure_stage_and_trace_frames tests/indexing/test_indexer_redaction.py::test_chroma_worker_logs_sanitized_operation_and_trace_frames`
- Result: `2 passed in 0.78s`
- Affected unit file command: `uv run pytest -q tests/indexing/test_indexer_redaction.py`
- Result: initial GREEN `7 passed in 0.90s`; after review-finding duplicate-log coverage, latest `8 passed in 1.18s`
- Syntax check: `python -m compileall api core environments fetching indexing search storage main.py`
- Result: passed.

## Full-Suite Evidence

Command: `./scripts/verify_all.sh`

Result: passed.

- Static verification layer: passed.
- Public MCP contract layer: `40 passed in 1.19s`.
- Broad non-live regression layer: latest `1378 passed in 104.22s`.
- Coverage: required `70.0%`, actual `87.96%`.
- Deterministic quality eval layer: passed; retrieval `14/14`, document sort `2/2`, answer `9/9`.
- Deterministic functional E2E layer: `59 passed in 3.89s`.

## Matching Eval Gate

`n/a` for scope requirement. Logging diagnostics do not change retrieval, ranking, grounding, citation selection, or answer quality. The full-suite deterministic quality eval layer still passed as part of `./scripts/verify_all.sh`.

## Improvement Performance Delta

`n/a`. This is an observability/reliability diagnostics fix with no performance-improvement claim.

## Functional Smoke Matrix

| Feature or workflow | Caller surface | Data mode | Expected result | Command | Result | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Indexing diagnostic log | Focused unit test | Fake collection/index failure | Stage/frame hint logged; secret payload redacted | `uv run pytest -q tests/indexing/test_indexer_redaction.py` | passed | Latest `8 passed in 1.18s` |
| Indexing failure wrapping | Focused unit test | Fake failure | `IndexingError` contract preserved | `uv run pytest -q tests/indexing/test_indexer_redaction.py` | passed | Latest `8 passed in 1.18s` |
| Retained functional E2E | Repo script | Temporary/fake fixtures | Existing MCP/source-sync/search contracts still pass | `./scripts/verify_all.sh` | passed | Latest functional E2E `59 passed in 3.94s` |
| Live Notion sync reproduction | MCP live source | User data/live credentials | Blocked unless explicitly approved | Not run | blocked/gated | Nearest substitute: fake/temp tests |

## Architecture Constraints

- SQLite lifecycle semantics and Chroma vector role remain unchanged.
- Diagnostics must not inspect or mutate user Chroma/SQLite data.
- Logs must be useful operationally without exposing secrets.

## Risks and Rollback Notes

- Risk: traceback logs leak sensitive exception strings. Mitigation: format traceback frames manually and log sanitized error text only.
- Risk: noisy logs. Mitigation: one concise error log per indexing failure; worker-level diagnostics mark the exception so the outer wrapper does not log the same failure twice.
- Rollback: revert the localized logging helper and tests.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created `feature/indexer-safe-traceback-diagnostics` from clean updated `main`. | `git fetch origin main`; `git pull --ff-only origin main`; `git switch -c feature/indexer-safe-traceback-diagnostics` |
| Plan | completed | Non-exempt diagnostics plan created. | `docs/plan/2026-08-06-indexer-safe-traceback-diagnostics.md` |
| Improvement delta declare | completed | Observability/reliability diagnostics fix; no performance claim. | `n/a` |
| TDD RED | completed | Added focused diagnostics tests and confirmed current logging lacks stage/frame context. | RED command -> exit `1`; expected missing `indexing_stage=`/`trace_frames=` |
| Focused GREEN | completed | New diagnostics tests and affected indexer tests passed. | Focused `2 passed in 0.78s`; affected file `7 passed in 0.90s`; compileall passed |
| Full suite GREEN | completed | Full verification wrapper passed and was rerun after review-finding fix. | Latest `./scripts/verify_all.sh`: public MCP `40 passed`; broad regression `1378 passed`; functional E2E `59 passed` |
| Matching eval | completed | Scope `n/a`; full-suite quality eval still passed. | `./scripts/verify_all.sh`: retrieval `14/14`, document sort `2/2`, answer `9/9` |
| Improvement after/delta | completed | Observability/reliability diagnostics fix with no performance-improvement claim. | `n/a` |
| Functional smoke | completed | Retained fake/temp test surfaces and functional E2E passed; live sync gated. | Focused indexer tests passed; full functional E2E `59 passed in 3.89s` |
| Harness review pass 1 | retry | Reviewer 3 found duplicate worker plus outer indexing logs for the same batch failure. | RED `uv run pytest -q tests/indexing/test_indexer_redaction.py::test_batch_index_failure_logs_single_sanitized_diagnostic` -> exit `1`, expected `2 == 1` indexing error logs |
| Review finding fix | completed | Worker-level diagnostics now mark already-logged exceptions so outer `IndexingError` wrapping preserves status without duplicate error logs. | Focused `3 passed in 0.86s`; latest affected file `8 passed in 1.18s`; compileall passed |
| Post-fix full suite | completed | Required full verification rerun after behavior-changing review fix passed. | `./scripts/verify_all.sh`: `1378 passed`, coverage `87.96%`, quality eval passed, functional E2E `59 passed in 3.94s` |
| Harness review pass 2 | completed | Reviewer 1 and 2 reported no actionable findings; Reviewer 3 performance/reliability/operability pass found no actionable findings after duplicate-log fix. | Read-only review evidence; no live Chroma/SQLite or user-data access |
| Delivery | pending | Commit, push, PR after clean review unless blocked. | Pending |
