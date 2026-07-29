# Date Filter, Sort, and Document Listing Tools

## User Request

Implement the complete date-filtering and sorting design for ContextWiki MCP
tools. Use test-driven development and add retained end-to-end coverage.

## Branch Preflight Result

- Starting worktree: clean `main` at `12043e6b48e0fd307bcf10d39b144052793d5744`.
- Freshness: `git fetch origin main` and `git pull --ff-only origin main`
  completed; local `main` already matched `origin/main`.
- Safe cleanup: deleted only the unlinked, merged local branch
  `feature/wait-for-sync-all`. Linked-worktree and local-only branches were
  preserved.
- Task branch: `feature/date-filter-sort-tools`.
- Main refresh during integration: on 2026-07-30, fetched `origin/main` at
  `c5227ff`, preserved the complete dirty feature diff and untracked files in
  `stash@{0}`, fast-forwarded this feature branch by 17 upstream commits, and
  reapplied the preserved diff without textual conflicts. The stash remains as
  a recovery point until delivery completes.
- User Chroma and SQLite data will not be inspected or mutated. Tests use fake
  services and temporary SQLite/Chroma paths.

## Scope

1. Add normalized document time metadata without reinterpreting legacy
   `date`/`updated_at` fields:
   - `published_at`
   - `modified_at`
   - `indexed_at`
   - `date_provenance`
2. Populate normalized fields where source semantics are trustworthy:
   - Notion creation/edit timestamps.
   - Tistory publication timestamp when available.
   - Obsidian filesystem modification timestamp.
   - GitHub blob SHA remains revision metadata; it must not be exposed as a
     modification timestamp.
3. Add typed MCP search filters with inclusive UTC date/time bounds.
4. Extend `search_context` with normalized date filtering while retaining
   relevance ordering.
5. Extend `search_documents` with normalized date filtering plus explicit
   relevance/date sort controls.
6. Add query-less `list_documents` with active-document filtering, deterministic
   date ordering, bounded page size, and opaque cursor pagination.
7. Return stable structured time metadata in document/chunk search and listing
   payloads.
8. Update maintained README and architecture documentation.
9. Develop through TDD: focused and retained E2E tests are written and observed
   failing before production implementation, then made green and refactored.

## Non-Goals

- No live Notion, Tistory, GitHub, or Obsidian sync.
- No inspection, migration run, reset, or reindex of the user's local SQLite or
  Chroma data.
- No LLM-based date parsing or query rewriting.
- No separate `filter_by_date` or `sort_by_date` tools.
- No attempt to infer per-file GitHub modification time from blob SHA.
- No unrelated retrieval/ranking redesign.

## Acceptance Criteria

1. FastMCP exposes typed, documented date filter fields instead of an opaque
   date-filter convention hidden inside `dict`.
2. Existing source-id filters continue to work through backward-compatible
   normalization.
3. Invalid ranges, unsupported sort values, invalid cursor values, and unsafe
   page sizes fail with deterministic caller-visible validation errors.
4. Date filters are applied through SQLite-authoritative active document
   metadata before result truncation. Filtered semantic search can retrieve
   additional candidates up to the existing bounded retrieval ceiling when
   early candidates do not match.
5. `search_context` remains relevance ordered.
6. `search_documents` defaults to relevance and supports ascending/descending
   sort by `published_at`, `modified_at`, or `indexed_at`, with deterministic
   document-id tie breaking and null timestamps after timestamped rows.
7. `list_documents` does not require a semantic query, returns active public
   documents only, supports the same typed date filters and deterministic date
   sorts, and returns `next_cursor` when another page exists.
8. Notion, Tistory, and Obsidian normalized timestamps are covered by mocked or
   temporary-source tests; GitHub explicitly keeps blob SHA out of normalized
   time fields.
9. SQLite schema upgrades are additive and operation-scoped. Existing databases
   receive new columns through the repository's current additive-column
   mechanism, with no destructive migration or reindex requirement.
10. Public results do not expose content beyond the existing search/list
    contract and do not leak secrets or local filesystem paths.
11. Focused unit/contract/storage/search tests, retained E2E, retained eval
    coverage, compile, full non-live verification, and functional smoke pass
    before review.
12. Per the user's latest explicit override, exactly three fresh read-only
    reviewers report no actionable findings in the
    newest final review pass before commit, push, and PR creation.

## Ordered Steps

### 1. `tdd-red-contract-and-e2e`

- Read current tests under `tests/api`, `tests/contracts`, `tests/search`,
  `tests/storage`, `tests/fetching`, `tests/e2e`, and `tests/evals`.
- Add focused tests for normalized connector/model/storage behavior, typed MCP
  schemas, filter/sort semantics, pagination, and validation.
- Extend `tests/e2e/test_contextwiki_flow.py` with real FastMCP caller coverage
  for filtered/sorted search, query-less listing, and pagination using temporary
  data.
- Extend the retained retrieval eval surface with deterministic date-filter
  selection coverage.
- Run only the new tests and record the expected production-code failures.
- Test worker owns tests only and must not edit production code.

### 2. `normalized-time-storage`

- Add shared time/filter/list models in `core/`.
- Extend connector normalization in `fetching/`.
- Extend additive SQLite schema, row hydration, upsert, active filtering, and
  cursor listing in `storage/metadata_store.py`.
- Preserve legacy fields and all document identity/tombstone invariants.

### 3. `search-and-mcp-contract`

- Extend `search/` filtering, bounded candidate expansion, deterministic sort,
  and result metadata.
- Extend `api/tools.py` with typed filters/sort parameters and `list_documents`.
- Preserve public-source sanitation and redaction.
- Add honest non-destructive annotations where supported by the current
  FastMCP dependency. Because metadata reads can initialize additive schema and
  refresh sync-owner heartbeat metadata, do not advertise them as read-only or
  idempotent; preserve the external-provider `openWorldHint` distinction.

### 4. `docs-and-integration`

- Update README, `.agents/docs/architecture.md`, and this plan.
- Integrate worker changes, inspect the complete diff, and resolve overlaps
  without reverting other worker/user changes.

### 5. `verify-smoke-review-deliver`

- Run focused tests, retained evals, compile, retained functional E2E, and the
  full verification wrapper.
- Record the functional smoke matrix.
- Run middle and final three-reviewer loops, routing any findings back to the
  responsible worker boundary and rerunning affected verification/smoke.
- Commit, push, and create a `main`-base PR.

## Worker Ownership

| Worker | Owned files/modules | Acceptance boundary |
| --- | --- | --- |
| TDD test worker | `tests/api`, `tests/contracts`, `tests/search`, `tests/storage`, `tests/fetching`, `tests/e2e`, `tests/evals` | Writes failing tests first; no production edits |
| Time/storage worker | `core/`, `fetching/`, `storage/metadata_store.py` | Normalized timestamps, additive schema, active filtering/list pagination |
| Search/API worker | `search/`, `api/tools.py` | Search filter/sort behavior, bounded refill, typed MCP contract, new list tool |
| Docs/integration worker | `README.md`, `.agents/docs/architecture.md` | Client-facing contract and maintained architecture alignment |

Workers share the task branch but have disjoint ownership. They must preserve
concurrent changes, must not commit/push/open PRs, inspect secrets, inspect or
mutate user data, or make destructive changes.

## Files Likely To Change

- `core/models.py` and possibly a focused new `core/` date/filter helper module.
- `fetching/notion.py`
- `fetching/tistory.py`
- `fetching/github.py`
- `fetching/obsidian.py`
- `storage/metadata_store.py`
- `search/context_service.py`
- `search/retrieval_pipeline.py` only if needed for bounded candidate refill.
- `api/tools.py`
- Focused tests in the ownership table.
- `tests/e2e/test_contextwiki_flow.py`
- A retained retrieval eval test/fixture.
- `README.md`
- `.agents/docs/architecture.md`
- This plan document.

## TDD and Verification Plan

### Red

- Before production edits, the test worker ran this exact command:

  ```bash
  uv run pytest -q \
    tests/storage/test_metadata_store.py::test_metadata_store_persists_normalized_document_times_and_adds_legacy_columns \
    tests/storage/test_metadata_store.py::test_list_documents_filters_active_rows_sorts_dates_and_paginates_with_cursor \
    tests/search/test_context_service.py::test_search_context_refills_candidates_before_applying_inclusive_date_filter \
    tests/search/test_context_service.py::test_search_documents_sorts_matching_documents_by_normalized_date \
    tests/contracts/test_public_mcp_contracts.py::test_date_filters_and_document_listing_have_typed_real_fastmcp_schemas \
    tests/contracts/test_public_mcp_contracts.py::test_typed_date_filters_and_list_documents_use_real_fastmcp_calls \
    tests/e2e/test_contextwiki_flow.py::test_contextwiki_fastmcp_e2e_date_filter_sort_and_list_pagination
  ```

- Layers: two storage integration tests, two search integration tests, two real
  FastMCP public-contract integration tests, and one retained FastMCP E2E.
- Historical limitation: the initial RED did not include a separate unit-level
  selector. This work began before the 2026-07-30 `main` harness update made
  unit, integration, and deterministic E2E coverage mandatory in the initial
  RED. That chronology cannot be retroactively changed, so this plan records
  the gap instead of claiming full compliance. Unit coverage is present and
  green in the final suite, and the later precision review fix did include a
  genuine pre-production unit RED as recorded below.
- Result: exit code `1`, `7 failed in 1.67s`; collection and syntax succeeded.
- Representative missing-behavior signatures:
  - `AttributeError: 'DocumentModel' object has no attribute 'published_at'`
  - `ImportError: cannot import name 'SearchFilters' from 'core.models'`
  - `AssertionError: assert 'list_documents' in {...}`
  - `ToolError: Unknown tool: list_documents`
- These failures represented the absent normalized model/storage fields,
  filtering/sorting service contracts, typed FastMCP schemas, and
  `list_documents` tool rather than fixture or environment errors.

The post-main precision review fix also returned to RED before its production
edit:

```bash
uv run pytest -q \
  tests/search/test_context_service_date_sort_unit.py \
  tests/search/test_context_service.py::test_search_documents_preserves_microsecond_precision_for_date_sort \
  tests/e2e/test_contextwiki_flow.py::test_contextwiki_fastmcp_e2e_search_documents_preserves_date_microseconds
```

This unit, search-integration, and retained FastMCP E2E selection exited `1`
with `4 failed`. Its representative signature was
`assert 253402300800.0 < 253402300800.0`, proving that float epoch conversion
collapsed adjacent valid microseconds before the UTC-aware datetime fix.

The later annotation review fix also returned to RED before its production
edit:

```bash
uv run pytest -q \
  tests/contracts/test_public_mcp_contracts.py::test_real_fastmcp_annotations_match_metadata_store_write_behavior
```

This real FastMCP public-contract integration test exited `1` with `1 failed`;
its representative signature was
`assert annotations.readOnlyHint is False` while the actual value was `True`.
It proved that the query/list/fetch tools advertised read-only behavior even
though their shared `MetadataStore.ensure_schema()` path can persist additive
schema and sync-owner heartbeat metadata.

### Green

- Run new focused tests until they pass.
- Run affected existing suites:
  - `uv run pytest -q tests/fetching tests/storage/test_metadata_store.py`
  - `uv run pytest -q tests/search/test_context_service.py`
  - `uv run pytest -q tests/api/test_tools_contract.py`
  - `uv run pytest -q tests/contracts/test_public_mcp_contracts.py`
  - `uv run pytest -q tests/e2e/test_contextwiki_flow.py`
  - `uv run pytest -q tests/evals`
  - `PYTHONPATH=. python scripts/run_contextwiki_eval.py`

### Refactor and Integration

- `python -m compileall api core environments fetching indexing search storage main.py`
- `./scripts/verify_functional_e2e.sh`
- `./scripts/verify_all.sh`
- `git diff --check`
- FastMCP registration/call smoke through retained public contract and E2E
  tests, without live credentials or user data.

## Planned Functional Smoke Matrix

| Workflow | Caller surface | Safe data mode | Expected result | Action/evidence | Result |
| --- | --- | --- | --- | --- | --- |
| `list_sources` | retained FastMCP E2E | fake registry/temp SQLite | retained public sources returned | `./scripts/verify_functional_e2e.sh` | passed |
| `sync_source` | retained FastMCP E2E | fake connector/temp SQLite/Chroma | background lifecycle remains truthful | retained E2E within functional wrapper | passed |
| `sync_all` / `wait_for_sync_all` | retained E2E | fake services/temp metadata | aggregate contracts unchanged | retained E2E within functional wrapper | passed |
| `get_sync_status` | retained FastMCP E2E | temp SQLite | source/job status returned | retained E2E within functional wrapper | passed |
| `search_context` default | FastMCP E2E | mock retriever/temp SQLite | relevance contract unchanged | post-main focused overlap `476 passed`; final full non-live regression `843 passed`; functional wrapper `36 passed` | passed |
| `search_context` date filter | new FastMCP E2E | mock retriever/temp SQLite | only active in-range evidence returned after bounded refill | `test_search_context_refills_candidates_before_applying_inclusive_date_filter`; FastMCP E2E; retained eval date-filter selector | passed |
| `search_documents` filter/sort | new FastMCP E2E | mock retriever/temp SQLite | grouped rows in deterministic requested order | same new retained FastMCP E2E | passed |
| `list_documents` pagination | new FastMCP E2E | temp SQLite | query-less active rows and opaque next cursor | same new retained FastMCP E2E plus storage keyset tests | passed |
| `fetch_context` | retained FastMCP E2E | temp SQLite | exact active document/chunk hydration works | functional wrapper | passed |
| citation answer helper | retained E2E/eval | deterministic fixtures | existing grounding behavior unchanged | eval retrieval `14/14`, answer `9/9`; functional wrapper | passed |
| Notion/Tistory/GitHub connectors | mocked parser/API tests | mocked responses/temp data | normalized fields follow source semantics, including latest owner-scoped GitHub discovery | post-main focused overlap `476 passed`; final full non-live regression `843 passed` | passed |
| Notion/Tistory/GitHub live sync | live external APIs | user credentials/data | not executed without explicit approval | blocked by live credential/user-data approval; mocked connector tests are nearest substitute | blocked/gated |
| Obsidian temp-vault sync | retained connector tests | temporary vault | filesystem modified timestamp/provenance | connector test with temp file mtime | passed |
| Obsidian real-vault sync | live local source | user vault | not executed without explicit approval | blocked by real-vault/user-data approval; temp-vault test is nearest substitute | blocked/gated |

For blocked/gated live rows, mocked connector tests and temporary E2E fixtures
are the nearest substitutes.

## Architecture Constraints

- SQLite remains the authoritative active-document and date-filter gate.
- Chroma remains a candidate accelerator; this change does not require
  reindexing existing vectors.
- Date filtering cannot be applied only after `top_k` truncation.
- `search_context` remains chunk-level; `search_documents` remains grouped
  semantic browsing; `list_documents` is deterministic query-less browsing.
- Source ids, document ids, tombstones, content hashes, chunk ids, and citation
  metadata remain stable.
- GitHub blob SHA is `version_id`/legacy revision metadata, never a normalized
  timestamp.
- All DB tests use temporary SQLite; embedding/vector tests use fakes or
  temporary Chroma with mock embeddings.

## Risks and Rollback

- Risk: additive schema drift on existing SQLite files. Mitigation: use current
  additive-column initialization and compatibility tests; no destructive
  migration.
- Risk: time-zone and inclusive-boundary ambiguity. Mitigation: normalize
  filter bounds to UTC and document inclusive behavior.
- Risk: filtered retrieval underfills results. Mitigation: bounded candidate
  expansion before truncation and regression tests.
- Risk: date sorting of only an early semantic candidate window can imply a
  global ordering guarantee. Mitigation: document `search_documents` as sorting
  matching semantic candidates; use `list_documents` for global deterministic
  date browsing.
- Risk: cursor instability. Mitigation: deterministic `(sort timestamp,
  document_id)` keyset and tests for ties/nulls/invalid cursors.
- Rollback point: revert this feature branch/PR. Legacy columns and tool defaults
  remain compatible; added SQLite columns are inert if the code is rolled back.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Updated clean main, safely removed one unlinked merged branch, created fresh feature branch. | `git fetch`, `git pull --ff-only`, `git switch -c feature/date-filter-sort-tools` |
| Plan | completed | Scope, TDD sequence, worker ownership, E2E and smoke matrix recorded. | This document |
| TDD Red | completed | Added seven storage/search/public-contract/retained-E2E selectors and ran the exact selector command recorded above before production edits. All seven failed for missing requested production behavior with no collection or syntax errors. | exit `1`; `7 failed in 1.67s`; representative `published_at` attribute, `SearchFilters` import, schema registration, and unknown `list_documents` failures |
| Implementation Green | completed | Normalized source times, additive storage, typed filters, pre-truncation gate/refill, sorting, query-less list tool, annotations, and docs implemented. | Seven Red selectors now `7 passed`; storage/fetching `204 passed` |
| Focused verification | completed | Compile and affected suites/evals pass after review pass 9 blank-source compatibility fix. | `616 passed`; retrieval `14/14`; document sort `2/2`; answer `9/9`; `git diff --check`; feature branch confirmed |
| Functional smoke | completed | Retained full inventory exercised with fake/temp data; live/user-data rows explicitly gated. | `./scripts/verify_functional_e2e.sh` -> `32 passed` |
| Middle three-reviewer gate | completed | Fresh pass 10: all three reviewers reported no actionable findings. | MCP, storage, and test/docs reviewers clean |
| Refactor/integration | completed | Bandit B608 was narrowed with a scoped suppression after confirming only enum/internal SQL fragments are interpolated and values remain parameterized. The stale app-composition tool inventory was updated for `list_documents`, then the complete wrapper passed. Review fixes moved source-filter preprocessing inside the safe error boundary, validate cursor anchors against their active filtered rows, and preserve exact microsecond ordering with UTC-aware datetimes instead of float epochs. | static checks passed; post-main non-live regression `843 passed` at `88%` coverage; retrieval `14/14`; document sort `2/2`; answer `9/9`; functional E2E `36 passed` |
| Latest-main reintegration | completed | Preserved the full feature diff in `stash@{0}`, fast-forwarded from `12043e6` to current `origin/main` `c5227ff`, and reapplied without textual conflicts. Automatic merges in README, architecture, and GitHub connector tests retained both owner-scoped discovery changes and normalized-date coverage. | `HEAD...origin/main` -> `0 0`; no unmerged paths; focused overlap `476 passed`; `./scripts/verify_all.sh` -> exit 0, public MCP `42 passed`, non-live `839 passed`, functional E2E `35 passed`; `git diff --check` passed |
| Final three-reviewer gate | completed | Final pass 1 found source-filter redaction and forged-anchor gaps; later passes fixed float-epoch microsecond precision loss, completed the honest TDD audit trail, and aligned annotations with SQLite write behavior. The newest post-fix pass has three distinct read-only reviewer results with correctness, security/data-safety, and performance/reliability lenses; all are clean. The agent-thread cap required the third distinct reviewer to run a fresh post-fix turn in an existing reviewer process rather than creating another node; no self-review substituted for it. | correctness CLEAN with contract/API `101 passed` and focused `37 passed`; security CLEAN with `16 passed`; reliability CLEAN; full wrapper `843 passed`; functional E2E `36 passed` |
| Delivery | in_progress | Stage only task files, commit, push, and create a main-base PR. | Pending |
