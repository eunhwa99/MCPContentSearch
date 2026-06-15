# User request

Improve sync status so Claude and other MCP clients do not mistake long-running
Notion upstream fetch work for a stuck sync just because `processed_documents`
remains `0`.

## Branch preflight result

- Current worktree: `/Users/eunhwa/.codex/worktrees/eab9/MCPContentSearch`
- Current branch: `feature/fix-dotenv-loading`
- Worktree state: already dirty from the dotenv-loading and Notion observability
  work in progress.
- Safety note: continuing in the same approved dirty worktree because the user
  explicitly asked to keep improving this active work item.

## Scope and non-goals

### Scope

- Add running-job status metadata that makes upstream fetch progress visible via
  `get_sync_status`.
- Include phase/progress/message hints that let clients distinguish upstream
  fetch from indexing.
- Update focused MCP contract tests for the new `latest_job` surface.

### Non-goals

- Do not redesign the source-level `sync_status` enum.
- Do not expose raw page content, local file paths, or secret values in the new
  fields.
- Do not change `search_*` or answer tool contracts.

## Acceptance criteria

- `get_sync_status(...).latest_job` can expose:
  - `phase`
  - `upstream_total_pages`
  - `upstream_fetched_pages`
  - `last_progress_at`
  - `status_message`
- A running Notion fetch with `processed_documents=0` still looks alive from
  the MCP contract because the upstream fields advance.
- Focused contract and ingestion tests fail before the change and pass after it.

## Files likely to change

- `.agents/docs/architecture.md`
- `docs/plan/2026-06-15-sync-status-running-phase.md`
- `core/models.py`
- `fetching/connectors.py`
- `fetching/notion.py`
- `storage/metadata_store.py`
- `indexing/ingestion_service.py`
- `api/tools.py`
- `tests/api/test_tools_contract.py`
- `tests/fetching/test_connectors.py`
- `tests/fetching/test_notion.py`
- `tests/indexing/test_ingestion_service.py`
- `tests/storage/test_metadata_store.py`

## Test and verification plan

- Red:
  - focused pytest on MCP contract and ingestion progress tests
- Green:
  - `uv run pytest tests/api/test_tools_contract.py tests/fetching/test_connectors.py tests/fetching/test_notion.py tests/indexing/test_ingestion_service.py tests/storage/test_metadata_store.py -q`
  - `python -m compileall api core environments fetching indexing search storage main.py`
  - `./scripts/verify_functional_e2e.sh`

## Functional smoke matrix

| Surface | Scenario | Safe mode | Expected result | Status | Evidence |
| --- | --- | --- | --- | --- | --- |
| `get_sync_status` | running Notion upstream fetch | local fake/temp only | `latest_job.phase` and upstream counters show active fetch even when processed docs are 0 | completed | `uv run pytest tests/api/test_tools_contract.py tests/indexing/test_ingestion_service.py -q` |
| `sync_source` re-entry | existing running Notion job | local fake/temp only | job reuse still works, and callers can re-poll `get_sync_status` to observe running-phase hints instead of only `processed_documents=0` | completed | `./scripts/verify_functional_e2e.sh` |

## Architecture constraints

- Keep persistent sync-job truth in `storage/metadata_store.py`.
- Keep MCP payload formatting in `api/tools.py`.
- Keep fetch-phase interpretation inside ingestion/orchestration, not tool
  handlers.

## Risks and rollback notes

- Risk: new fields could break strict contract consumers.
- Mitigation: add fields only, keep existing names and semantics unchanged.
- Rollback: remove the extra SyncJobModel fields and their formatting/tests.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Planning | completed | Wrote a dedicated plan for running-phase status metadata. | `docs/plan/2026-06-15-sync-status-running-phase.md` |
| Focused test design | completed | Added red expectations for running-phase metadata on MCP status payloads, public-source gating, and ingestion progress persistence. | `tests/api/test_tools_contract.py`; `tests/indexing/test_ingestion_service.py` |
| Implementation | completed | Added persistent sync-job phase/progress fields, preserved private/public payload boundaries, and wired Notion discovery/page/block stop handling plus observer-safe cancellation into the fetch path. | `core/models.py`; `fetching/connectors.py`; `fetching/notion.py`; `storage/metadata_store.py`; `indexing/ingestion_service.py` |
| Focused verification | completed | After the latest reviewer findings, reran the full focused five-file pytest set again and extended coverage for `_await_request_with_stop()` stop-vs-error races, `get_sync_status()` / `sync_all()` error-shape preservation, fully blocked `sync_all()` aggregate status handling, hidden-only filtered-result behavior with raw hidden rows present, unsupported public-filtering contract paths, and true empty-result upstream failures that still report `total_sources`. The newest rerun stayed green, and the documented syntax check now matches the repo-standard compile surface including `environments/`, `search/`, and `main.py`. | `uv run pytest tests/api/test_tools_contract.py tests/fetching/test_connectors.py tests/fetching/test_notion.py tests/indexing/test_ingestion_service.py tests/storage/test_metadata_store.py -q` (`237 passed`); `python -m compileall api core environments fetching indexing search storage main.py` |
| Functional smoke | completed | Repo functional E2E gate passed again after the latest `sync_all()` contract/status fixes. | `./scripts/verify_functional_e2e.sh` (`25 passed`) |
| Review gate | completed | First fresh five-reviewer pass found two actionable issues, the second fresh five-reviewer pass found a heartbeat follow-up, the third fresh five-reviewer pass found terminal-status, progress-stop-signal, redaction, and progress-wording follow-ups, and the fourth fresh five-reviewer pass found explicit-sentinel, callback-isolation, terminal-race, block-fetch cancellation, discovery-stop propagation, awaitable observer, discovery-checker propagation, contract-boundary, raw-error-redaction, terminal-surface, evidence-alignment, discovery-counter, public-source-gating, and final-batch cancellation follow-ups. Later fresh passes found conditional legacy `sync_all` compatibility expectations, retry/backoff and in-flight Notion stop propagation gaps, same-timestamp latest-job ordering, visible long-fetch heartbeat freshness, callback-only observer stop handling, observer-cancel replay on the public background-start path, and verification evidence drift. After merging `origin/main`, resolved the README conflict, narrowed callback-only observer cancellation to explicit sentinel use, preserved `_StopRequested` through the Notion progress path and composed observer wrapper, aligned generic background-cancel replay expectations with the public launcher contract, added a tool-layer `sync_source` replay regression, and reran the full focused five-file pytest set plus the functional E2E gate. A newer fresh pass then found active running-job tie-break inconsistency, unconditional `sync_all()` signature introspection on the no-filter path, and README wording drift around `OPENAI_API_KEY` / preexisting env precedence. Applied those fixes, limited replay caching to observer-cancelled failures only, added a running-job tie-break regression plus a no-filter `sync_all()` contract regression, refreshed README env guidance, and reran focused verification plus functional smoke. The next fresh pass then found source snapshot tie-break drift, stop-checker `_StopRequested` swallowing, and missing pre-event stop checks around local Notion progress transitions. Applied those fixes, added focused regressions, and reran focused verification plus functional smoke. A later fresh pass then found nested observer stop-checker `_StopRequested` swallowing, a discovery-batch stop gap before `search_page_batch_completed`, bulk-sync preflight work outside the stable error-shaping path, and README wording drift on the default OpenAI embedding requirement. Applied those fixes, added focused regressions, and reran focused verification plus functional smoke. The latest passes then found an `_await_request_with_stop()` stop-vs-error cleanup race, duplicate README wording, `get_sync_status()`/`list_sources()` refresh work outside stable error shaping, observer-cancel replay gaps in `sync_all()`, lifecycle-stop vs observer-stop conflation, documentation/evidence drift around source activation wording and compile coverage, `sync_all()` error-shape drift on both preflight and formatting failures, all-blocked aggregate status misreporting, GitHub startup wording drift for malformed repository specs, empty-result fallback-status drift, remaining early-return shape gaps in `sync_all()`, and hidden-only-vs-true-empty failure ambiguity in the regression fixtures. Applied those fixes, corrected the unsupported-filter regression fixture, converted the hidden-only test to use a raw hidden row that gets filtered out, added a true empty-result failure regression that still reports `total_sources`, reran focused verification to `237 passed`, retained the repo-standard compile pass, retained the functional E2E gate at `25 passed`, and the final fresh five-reviewer pass reported no actionable findings. | Reviewer pass 1 findings; reviewer pass 2 findings; reviewer pass 3 findings; reviewer pass 4 findings; later reviewer findings on terminal-surface/evidence alignment/discovery counters/public-source-gating/final-batch cancellation; newer reviewer findings on conditional bulk-sync filtering, in-flight stop propagation, latest-job ordering, visible heartbeat freshness, callback-only observer stops, public replay semantics, tool-layer coverage, active-running tie-break consistency, no-filter signature introspection, README env wording, source snapshot tie-breaks, stop-checker `_StopRequested`, pre-event progress stop checks, nested observer stop-checkers, discovery-batch stop gaps, bulk-sync preflight error shaping, request-drain stop-vs-error races, duplicate README wording, status-refresh error shaping, observer-cancel replay gaps in `sync_all()`, lifecycle-stop vs observer-stop conflation, source-activation wording drift, compile-surface evidence drift, sync-all formatting-shape drift, all-blocked aggregate-status drift, malformed-GitHub-spec wording drift, empty-result fallback-status drift, remaining sync-all early-return shape drift, hidden-only-vs-true-empty regression ambiguity, and final clean five-reviewer pass; `uv run pytest tests/api/test_tools_contract.py tests/fetching/test_connectors.py tests/fetching/test_notion.py tests/indexing/test_ingestion_service.py tests/storage/test_metadata_store.py -q`; `python -m compileall api core environments fetching indexing search storage main.py`; `./scripts/verify_functional_e2e.sh` |
