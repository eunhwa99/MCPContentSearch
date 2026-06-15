## User request

`ci 실패해`

## Branch preflight result

- Starting worktree: `/Users/eunhwa/.codex/worktrees/4561/MCPContentSearch`
- Initial state: clean detached HEAD at `1669152`
- Preflight evidence gathered: `git status --short --branch`, `git branch -vv`, `git worktree list`
- Network freshness: `git fetch origin main` succeeded on 2026-06-15
- Branch safety note: `main` is checked out in another linked worktree, so this clean detached worktree was moved directly onto fresh branch `feature/fix-ci-readme-demo-contract` from `origin/main` instead of switching this worktree onto `main`
- Current task branch: `feature/fix-ci-readme-demo-contract`

## Scope and non-goals

- Scope:
  - Fix the current CI failure caused by README/demo contract drift.
  - Reduce CI log noise by preventing the eval artifact upload step from failing when earlier steps stop artifact generation.
  - Keep the change limited to README/workflow/test contract surfaces.
- Non-goals:
  - No retrieval, indexing, storage, or MCP contract behavior changes.
  - No live-source sync, local SQLite inspection, or Chroma mutation.
  - No broad README rewrite beyond the contract phrases required for CI clarity.

## Acceptance criteria

- `tests/scripts/test_demo_public_flow.py::test_readme_keeps_demo_and_live_smoke_contract_phrases` passes.
- CI workflow still runs eval generation and upload, but the upload step no longer creates a secondary failure when no artifacts exist because an earlier step already failed.
- Targeted verification for README/workflow contract surfaces passes locally.
- After syncing the branch with latest `origin/main`, the public MCP contract layer also passes so PR merge CI is not blocked by stale-base failures.

## Step breakdown

1. `root-cause-confirmation`
   - Read the failing GitHub Actions log, README contract test, and current README wording.
   - Confirm whether the failure is documentation drift, workflow drift, or both.
2. `contract-test-red`
   - Add or update the smallest tests that describe the intended README and workflow contract.
   - Run the targeted test selection and confirm the expected failure before implementation.
3. `minimal-fix`
   - Restore the required README wording and tighten the CI artifact upload guard with the narrowest workflow change.
4. `verification`
   - Run targeted tests and a workflow/docs sanity pass.
5. `merge-ref-repair`
   - If PR CI fails only on the GitHub merge ref, fast-forward or merge `origin/main` into the branch, reproduce the failure locally, and fix only the branch-related regression.

## Files likely to change

- `README.md`
- `.github/workflows/ci.yml`
- `tests/scripts/test_verification_architecture.py`
- `docs/plan/2026-06-15-fix-ci-readme-demo-contract.md`

## Test and verification plan

- `uv run --locked pytest -q tests/scripts/test_demo_public_flow.py tests/scripts/test_verification_architecture.py`
- `git diff --check`
- If available locally: `actionlint .github/workflows/ci.yml`

## Functional smoke matrix

| Feature or workflow | Caller surface | Safe data mode | Expected result | Command/action | Planned result | Evidence | Blocker / substitute |
| --- | --- | --- | --- | --- | --- | --- | --- |
| README demo contract | README docs text | docs-only | required demo/live-smoke phrases remain present | targeted pytest README contract test | pending | `tests/scripts/test_demo_public_flow.py` | n/a |
| CI eval artifact upload guard | workflow YAML | local file inspection only | upload step still targets `artifacts/contextwiki-evals` without failing on absent files after earlier failure | targeted pytest workflow contract test | pending | `tests/scripts/test_verification_architecture.py` | `actionlint` if installed |

## Architecture constraints

- No architecture doc update is expected because this work does not change retained source connectors, indexing, retrieval, citation behavior, or storage semantics.
- Keep CI/workflow behavior aligned with the retained verification story already documented in `scripts/verify_all.sh` and workflow contract tests.

## Risks and rollback notes

- Risk: README wording could satisfy the test while becoming misleading. Mitigation: restore the exact reviewer-facing intent described by the contract test rather than paraphrasing loosely.
- Risk: workflow guard could hide a real eval artifact regression. Mitigation: keep the eval generation step intact and only stop the upload step from becoming a secondary failure when artifacts are absent.
- Rollback: revert the README/workflow/test contract edits on this branch before any commit if the targeted verification shows the guard weakens the intended CI signal.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Confirmed clean detached worktree, fetched `origin/main`, and created fresh `feature/fix-ci-readme-demo-contract` from `origin/main`. | `git status --short --branch`; `git branch -vv`; `git worktree list`; `git fetch origin main`; `git switch -c feature/fix-ci-readme-demo-contract origin/main` |
| Root-cause investigation | completed | Confirmed CI run `27546871532` failed first on README/demo contract drift in `tests/scripts/test_demo_public_flow.py`, with a secondary artifact-upload failure caused by missing `artifacts/contextwiki-evals` after the earlier stop. | `gh run view 27546871532 --log-failed`; `tests/scripts/test_demo_public_flow.py`; `.github/workflows/ci.yml`; `README.md` |
| Worker orchestration decision | completed | Treated this as atomic because the fix is a narrow README/workflow/test contract slice with one owner and no safe parallel write boundary; self-implementation is lower risk than forced delegation. | Current plan scope and file list |
| Contract test red | completed | Added a workflow contract test for guarded eval artifact upload and confirmed the targeted README/workflow suite failed on the expected missing README text and missing workflow guard. | `uv run --locked pytest -q tests/scripts/test_demo_public_flow.py tests/scripts/test_verification_architecture.py` -> `2 failed, 18 passed` |
| Minimal fix | completed | Kept the README to the single reviewer-path bullet the user wanted, trimmed the README contract test to match that smaller doc surface, and guarded the eval artifact upload step so absent artifacts after earlier failures do not create a second CI failure. | `README.md`; `tests/scripts/test_demo_public_flow.py`; `.github/workflows/ci.yml` |
| Verification | completed | Targeted README/workflow contract tests still passed after removing the extra bullets, and the diff is whitespace-clean. | `uv run --locked pytest -q tests/scripts/test_demo_public_flow.py tests/scripts/test_verification_architecture.py` -> `20 passed`; `git diff --check` |
| Review gate bypass | completed | The user explicitly approved bypassing the required five-reviewer subagent loop for this PR delivery. Proceeding with local verification evidence only. | User message: `2번` |
| PR CI root-cause investigation | completed | Confirmed PR #79 failed on the merge ref because latest `origin/main` was already red in the public MCP contract layer; the branch itself passed locally before syncing with main. | `gh pr checks 79`; `gh run view 27547827309 --log-failed`; `gh run view 27547716266 --log-failed`; `uv run --locked pytest -q tests/contracts/test_public_mcp_contracts.py tests/test_app_composition.py` before merge -> `11 passed`; `git rev-list --left-right --count HEAD...origin/main` -> `1 3` |
| Merge-ref repair | completed | Merged latest `origin/main` into the branch to reproduce the merge-ref state locally, then restored the public `search_context` debug key contract at the API boundary while preserving the workflow upload guard from this branch. | `git merge --no-edit origin/main`; `api/tools.py`; `.github/workflows/ci.yml` |
| Merge-ref verification | completed | The public contract layer plus README/workflow contract checks all passed on the merged branch state. | `uv run --locked pytest -q tests/contracts/test_public_mcp_contracts.py tests/test_app_composition.py tests/scripts/test_demo_public_flow.py tests/scripts/test_verification_architecture.py` -> `31 passed`; `git diff --check` |
