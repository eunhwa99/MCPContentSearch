# GitHub Fetch Parallelization

## User request
- Make GitHub blob content fetch faster by parallelizing fetch during sync, while keeping stale-skip and progress/error semantics intact.
- The user asks that work be done from `main`-derived branch flow.

## Branch preflight
- Current branch after preflight: `feature/github-parallel-fetch` (created from updated `main` before edits).
- `main` was checked out, fast-forwarded, and feature branch created.
- Worktree is clean and no unsafe edits happened on `main`.

## Scope and non-goals
- In scope:
  - `fetching/github.py`: fetch scheduling inside `GitHubRepositoryFetcher.fetch_documents`.
  - Unit/integration/e2e tests covering changed behavior.
- Out of scope:
  - MCP tool contracts.
  - Public config schema changes (for this pass we will use existing connection/pool settings).

## Acceptance criteria
- Unchanged files keep `supports_stale_cleanup` behavior and tombstone snapshot completeness semantics.
- Blob fetches for changed entries can be run with bounded concurrency instead of strict sequential loop.
- `fetch_documents` preserves existing progress event counts and order-sensitive `current_page`/`total_pages` values.
- Sync completion remains stable in deterministic tests and E2E flows.
- No user data mutation in temporary paths used by tests.

## Files likely to change
- `fetching/github.py`
- `tests/fetching/test_github.py`
- `tests/fetching/test_github_fetch_skip_integration.py` (if needed)
- `tests/e2e/test_github_fetch_skip_flow.py` (if needed)

## TDD RED evidence
- Command:
  - `pytest tests/fetching/test_github.py -k "bounded_concurrency or keeps_progress_order" -q`
- Tests:
  - `pytest tests/fetching/test_github.py -k parallel`
  - `pytest tests/e2e/test_github_fetch_skip_flow.py -k unchanged` (if test additions are made)
- Non-zero was observed before implementation (`2 failed`) because fetches were sequential.
- Expected failure signatures: missing parallelized blob-fetch scheduling, wrong progress sequence, or changed ordering/skip behavior.
- Missing behavior: GitHub blob fetch currently runs in one awaited pass, serially.

## Improvement metrics
- Metric: GitHub fetch wall-clock time for mocked multi-blob fixture in deterministic test scope.
- Unit: `github_blob_fetch_wall_time_seconds`.
- Method: wall-clock timer around `fetch_documents` in red/green tests.
- Direction: expected equal-or-improved (lower is better) with unchanged semantic outputs.

- Baseline: captured before production edits using existing mocked multi-blob HTTP fixtures (no external credentials).

  - Command: inline `python` benchmark in workspace (`DelayBlobHTTP`, 12 blobs, 50ms each)
  - Result: `elapsed=0.6164835` seconds, 12 documents, 14 requests

## TDD GREEN evidence (planned)
- Focused unit:
  - `pytest tests/fetching/test_github.py -k "bounded_concurrency or keeps_progress_order" -q`
  - `pytest tests/fetching/test_github.py` (targeted verification for fetcher behavior)
- Focused integration:
  - `pytest tests/fetching/test_github_fetch_skip_integration.py`
- Focused E2E:
  - `pytest tests/e2e/test_github_fetch_skip_flow.py`

## Full-suite gate
- `./scripts/verify_all.sh`

## Matching eval
- Not required for this behavior change (no retained quality-eval surface touched).

## Functional smoke matrix
- GitHub unchanged-first-then-reused-blob flow.
- GitHub multi-repo repository plan still resolves all repos and updates progress totals.
- Sync status and indexing path remain stable using fake metadata store.

## Architecture constraints
- Keep connector contract unchanged.
- Do not change `SourceConnector` interface or public MCP payloads.
- Bound concurrency to avoid unbounded API request spikes.

## Risks and rollback
- Risk: progress order mismatch if concurrent completion order differs from tree order.
  - Mitigation: store page index with each entry and emit progress with explicit counters.
- Risk: over-parallelism under strict API limits.
  - Mitigation: bounded semaphore around blob fetches.
- Rollback: revert `fetch_documents` blob loop to sequential mode.

## Progress

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created `feature/github-parallel-fetch` from updated `main`. | `git status`, `git pull --ff-only origin main`, `git checkout -b feature/github-parallel-fetch` |
| Improvement delta declare | completed | Declared metric and method for mocked end-to-end timing comparison. | Plan |
| Improvement baseline | completed | Baseline benchmark with 12 delayed blobs, 50ms each. | `python` timing snippet before implementation: `0.6164835` sec |
| TDD RED | completed | Added two unit tests and confirmed pre-implementation failure. | `pytest tests/fetching/test_github.py -k "bounded_concurrency or keeps_progress_order" -q` |
| Focused unit GREEN | completed | Focused parallel tests and full GitHub unit suite passed. | `pytest tests/fetching/test_github.py -k "bounded_concurrency or keeps_progress_order" -q` and `pytest tests/fetching/test_github.py` |
| Focused integration GREEN | completed | Integration skip/fetch reuse flows passed with concurrency changes. | `pytest tests/fetching/test_github_fetch_skip_integration.py` |
| Focused E2E GREEN | completed | Deterministic GitHub skip-flow E2E passed. | `pytest tests/e2e/test_github_fetch_skip_flow.py` |
| Full suite GREEN | completed | `./scripts/verify_all.sh` 전체 통과 (1381 passed, + eval/full suite/e2e green). | `./scripts/verify_all.sh` |
| Matching eval | not required | Not applicable. | n/a |
| Improvement after/delta | completed | `github_blob_fetch_wall_time_seconds` 0.15365s로 0.61648s 대비 **-75.2%** 개선 확인. | `python` timing snippet after implementation |
| Functional smoke | completed | Deterministic functional E2E(`59 passed`) 포함하여 smoke 경로 통과. | `./scripts/verify_all.sh` |

### Improvement delta

| Metric | Unit | Before | After | Absolute | Relative | Command | Interpretation |
| --- | --- | --- | --- | --- | --- | --- |
| `github_blob_fetch_wall_time_seconds` | seconds | 0.61648 | 0.15365 | -0.46284 | -75.2% | `python` timing snippet (12 blobs, 50ms delay) | Mocked wall time dropped significantly with bounded parallel fetches. |
