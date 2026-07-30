# Notion Fetch Skip Unchanged Pages

## User Request

Skip Notion block-content fetch for pages whose `last_edited_time` matches the
already indexed document `modified_at`, so incremental syncs do not re-download
unchanged page bodies.

## Branch Preflight Result

- Repo: `/Users/eunhwa/IdeaProjects/MCPContentSearch`
- Starting worktree: clean `main` at `72e7f84` (PR #89 merged).
- Freshness: fetched and fast-forwarded `origin/main`; ahead/behind `0 0`.
- Task branch: `feature/notion-fetch-skip-unchanged-edited` created from
  `origin/main`.

## Scope and Non-Goals

### Scope

- During Notion search→page fetch, reuse stored document content when the
  page's `last_edited_time` canonically matches the stored active document's
  `modified_at`, content is present, and the document is not tombstoned.
- Emit progress so skipped pages still advance `upstream_fetched_pages`.
- Wire the Notion connector to the metadata store through app composition.
- Cover unit, integration, and deterministic E2E before production code.
- Update `.agents/docs/architecture.md` for the fetch-before-index skip rule.

### Non-Goals

- Do not add unbounded parallel Notion fetches in this change.
- Do not change MCP tool contracts or public response shapes.
- Do not call live Notion or mutate user Chroma/SQLite outside temp tests.
- Do not change Tistory/GitHub/Obsidian connectors in this change.

## Acceptance Criteria

- Unchanged Notion pages (matching canonical `last_edited_time` /
  `modified_at`) do not call `fetch_block_content`.
- Changed, missing, tombstoned, or empty-content stored pages still fetch.
- Skipped pages remain visible to stale-cleanup (documents are still returned).
- Progress reports skipped pages as completed upstream pages.
- Focused unit/integration/E2E pass, then `./scripts/verify_all.sh`, smoke, and
  clean three-reviewer review before PR.

## Worker Ownership

| Worker | Owned files | Acceptance |
| --- | --- | --- |
| tests | `tests/fetching/test_notion.py`, `tests/fetching/test_connectors.py` or connector tests, `tests/indexing/test_ingestion_service.py` (if progress), `tests/e2e/test_*notion*` or durable/contextwiki Notion path | RED first; unit+integration+E2E assert skip vs refetch |
| notion-fetch | `fetching/notion.py`, `fetching/connectors.py`, `app_runtime.py` | Implement lookup + skip; preserve stale cleanup |
| docs | `.agents/docs/architecture.md`, this plan | Document fetch-before skip |

## TDD RED (to record before production)

- Command:
  `uv run --locked pytest -q tests/fetching/test_notion.py::test_fetch_notion_pages_skips_block_fetch_for_unchanged_existing_document tests/fetching/test_notion.py::test_fetch_notion_pages_fetches_when_modified_at_differs tests/fetching/test_notion.py::test_fetch_notion_pages_fetches_when_existing_document_is_deleted tests/fetching/test_notion.py::test_fetch_notion_pages_fetches_when_existing_content_is_empty tests/fetching/test_notion.py::test_fetch_notion_pages_fetches_when_existing_document_is_missing tests/fetching/test_notion.py::test_fetch_notion_pages_skips_when_created_time_fallback_matches tests/fetching/test_notion.py::test_fetch_notion_pages_skip_progress_includes_page_counters tests/fetching/test_notion_fetch_skip_integration.py tests/e2e/test_notion_fetch_skip_flow.py --tb=line`
- Layers: unit (`test_notion` skip/refetch/progress), integration
  (`test_notion_fetch_skip_integration` connector+store+runtime wiring), E2E
  (`test_notion_fetch_skip_flow` second sync skip)
- Exit code: `1`
- Failure signatures:
  - unit: `TypeError: fetch_notion_pages() got an unexpected keyword argument 'existing_documents'`
  - integration/E2E: `TypeError: NotionSourceConnector.__init__() got an unexpected keyword argument 'metadata_store'`
  - wiring: `AttributeError: 'NotionSourceConnector' object has no attribute 'metadata_store'`
- Missing behavior: `existing_documents` skip path, connector metadata_store
  lookup, and app_runtime wiring are not implemented yet (11 failed)

## Verification

- Focused GREEN, refactor while green, `./scripts/verify_all.sh`
- Eval: n/a (no retrieval quality change)
- Functional smoke: fake/temp Notion sync path only; live Notion blocked/gated

## Risks

- Timestamp canonicalization mismatch causes false refetch (safe) or false skip
  (unsafe). Prefer strict canonical equality and refetch when ambiguous.
- Empty stored content must never skip.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Fresh feature branch from main | `feature/notion-fetch-skip-unchanged-edited` |
| Plan | completed | This document | `docs/plan/2026-07-30-notion-fetch-skip-unchanged.md` |
| TDD RED | completed | Focused unit+integration+E2E fail before production skip | exit `1`; `existing_documents` TypeError (7 unit); `metadata_store` TypeError/AttributeError (3 integration + 1 E2E); 11 failed |
| TDD GREEN | completed | Minimum production skip path + wiring | `uv run --locked pytest -q` focused 7 unit + 3 integration + 1 E2E → **11 passed** in 1.26s; `existing_documents` skip via canonical timestamps; connector `metadata_store` list+get hydrate; app_runtime wires store; `page_fetch_skipped` advances upstream pages |
| Harness review fix RED | completed | Loader-after-search + progress/tombstone coverage fail first | exit `0` with 5 failed / 2 passed; signatures: `unexpected keyword argument 'existing_documents_loader'`; `no attribute '_load_existing_documents_for_page_ids'`; `assert {} is None` (preloaded existing_documents); integration `must not browse full corpus via list_documents`; ingestion progress + E2E tombstone already green (coverage-only) |
| Harness review fix GREEN | completed | Post-search id-only loader; no list_documents hydrate | focused new+previous skip suite **17 passed** in 1.23s (`test_notion` loader+7 skip, connector loader/progress, 4 integration, ingestion `page_fetch_skipped`, 2 E2E including peer-disappear last_seen) |
| PR #91 review fix preflight | completed | Continue existing PR branch after review findings | worktree `/Users/eunhwa/IdeaProjects/MCPContentSearch` on `feature/notion-fetch-skip-unchanged-edited` @ `93e4a9f`; clean; continue branch per user request to fix PR #91 |
| PR #91 review fix plan | completed | Align skip/persist timestamps + loader page_id keying | Scope below; N+1 batch hydrate deferred |
| PR #91 review fix RED | completed | Timestamp persist + loader page_id key tests fail first | exit `1`; 3 failed: `updated_at … input_value=None` / `assert '' == created_time` / `page-kept` missing from loader keys keyed by mismatched `external_id` |
| PR #91 review fix GREEN | completed | Align build_document + loader page_id keying | `build_document` uses `_page_remote_modified_at`; loader `existing[page_id]=full`; focused skip suite **17 passed** in 1.31s |
| PR #91 review fix refactor | completed | No further simplification needed | production diffs are 2+2 lines; architecture already matches created_time fallback wording |
| PR #91 review fix verify_all | completed | Full suite green outside sandbox | `./scripts/verify_all.sh` exit 0; 1236 pytest items; functional E2E 51 passed; quality eval layer included in full suite (n/a scope for this fix) |
| PR #91 review fix functional smoke | completed | Fake/temp Notion skip path | Matrix below |
| PR #91 review fix harness review | completed | Fresh three-lens pass clean | R1 bugs/contracts, R2 security, R3 reliability: all `NO ACTIONABLE FINDINGS` |
| PR #91 batch+public-ts plan | completed | Scope batch hydrate + public timestamp helper | continue PR branch; deferred yellows now in scope |
| PR #91 batch+public-ts RED | completed | 11 focused tests fail before production | exit `1`; missing `canonical_document_timestamp` / `get_documents_for_fetch_reuse`; loader still N×`get_document`; skip still private helper |
| PR #91 batch+public-ts GREEN | completed | Public timestamp + batch hydrate wired | `canonical_document_timestamp`; `get_documents_for_fetch_reuse`; connector batch once; notion uses public helper; Bandit-safe placeholder SQL; focused 19 passed |
| PR #91 batch+public-ts verify_all | completed | Full suite green | `./scripts/verify_all.sh` exit 0; 1243 pytest; E2E 51; eval layer n/a for this fix |
| PR #91 batch+public-ts smoke | completed | Fake/temp Notion skip + store batch | Matrix below |
| PR #91 batch+public-ts harness review | completed | Fresh three-lens pass clean | R1/R2/R3: all `NO ACTIONABLE FINDINGS` |

### Functional smoke matrix (batch hydrate + public timestamp)

| Feature | Caller | Data mode | Expected | Evidence | Result |
| --- | --- | --- | --- | --- | --- |
| Batch fetch-reuse hydrate | MetadataStore + connector | temp SQLite | one IN-query; no N× get_document | unit+integration focused suite | pass |
| Public timestamp equality | notion skip | fake pages | skip uses `canonical_document_timestamp` | unit monkeypatch tests | pass |
| Second-sync skip + stale cleanup | E2E | temp Chroma/SQLite | skip + tombstone peer | `test_notion_fetch_skip_flow` | pass |
| Live Notion | MCP sync | live | n/a | blocked/gated | blocked/gated |

### Functional smoke matrix (PR #91 review fix)

| Feature | Caller | Data mode | Expected | Command / evidence | Result |
| --- | --- | --- | --- | --- | --- |
| Notion skip + timestamp persist | unit/integration fetch | fake pages + temp SQLite | skip keeps created-time `modified_at`; loader keys by page_id | focused 17-test skip suite | pass |
| Notion second-sync skip + stale cleanup | E2E | temp Chroma/SQLite | second sync skips body fetch; peer disappear tombstones | `tests/e2e/test_notion_fetch_skip_flow.py` via verify_all E2E | pass |
| Sync progress `page_fetch_skipped` | ingestion service | temp store | upstream_fetched_pages advances | `test_handle_source_fetch_progress_page_fetch_skipped_*` | pass |
| Live Notion sync | MCP `sync_source` | live credentials | n/a | blocked/gated — no live approval | blocked/gated |

## PR #91 Review Fix Scope (2026-07-30)

### Findings to fix now

1. **Timestamp persist mismatch:** `_should_skip_notion_block_fetch` uses
   `_page_remote_modified_at` (`last_edited_time or created_time`), but
   `build_document` uses `page.get("last_edited_time", created_time)` which does
   not fall back when the key exists as `None`/`""`. After a created-time
   fallback skip, persisted `modified_at` becomes empty and later skips die.
2. **Loader keying:** `_load_existing_documents_for_page_ids` indexes by
   `external_id or document_id` after `get_document(page_id)`. Lookup uses
   `page_id`, so mismatched `external_id` silently disables skip.

### Deferred

- N+1 full-document hydrate batching (scale follow-up).
- Extracting a shared public timestamp helper beyond the minimal fix (optional
  if a thin public alias is trivial during GREEN).

### Acceptance

- Skip path with `last_edited_time=None` keeps `modified_at` equal to the
  canonical created-time fallback used for skip equality.
- Loader returns docs keyed by searched `page_id` even when stored
  `external_id` differs.
- Unit + integration (+ E2E touch if needed) cover both before production.

### Worker Ownership (review fix)

| Worker | Owned files | Acceptance |
| --- | --- | --- |
| tests | `tests/fetching/test_notion.py`, `tests/fetching/test_connectors.py`, optionally `tests/fetching/test_notion_fetch_skip_integration.py` | RED assertions for persist timestamp + page_id loader key |
| notion-fetch | `fetching/notion.py`, `fetching/connectors.py` | Align `build_document` remote timestamp; key loader by `page_id` |
| docs | this plan; architecture only if wording drifts | Record progress |

### TDD RED (review fix — record before production)

- Command (expected): focused new/changed tests in
  `tests/fetching/test_notion.py` and `tests/fetching/test_connectors.py`
- Expected failure: skipped fallback doc has empty/`None`-derived `modified_at`
  instead of created-time; loader map misses `page_id` when `external_id` differs

## PR #91 Follow-up: Batch Hydrate + Public Timestamp (2026-07-30)

### User Request

Address the remaining review findings on PR #91:
1. Replace per-id `get_document` N+1 hydrate with a batched id→skip fields read.
2. Stop fetch-layer calls to private `MetadataStore._canonical_document_timestamp`;
   expose a public helper.

### Branch Preflight

- Continue `feature/notion-fetch-skip-unchanged-edited` (user asked to fix PR #91).
- Worktree clean at `2d37801`; fetched `origin/main` + feature branch.

### Scope

- Add public `MetadataStore.canonical_document_timestamp` (private method may
  delegate) and switch `fetching/notion.py` skip equality to it.
- Add batched store read for Notion fetch-reuse fields
  (`document_id`, `content`, `modified_at`, `content_hash`, `deleted_at`) keyed
  by searched page ids; connector loader uses that instead of N×`get_document`.
- Cover unit (store + notion/connectors), integration (connector+store skip
  path), and keep deterministic E2E Notion skip flow green.
- Update architecture wording if hydrate semantics change.

### Non-Goals

- Live Notion; MCP contract changes; other connectors; unbounded parallel fetch.

### Worker Ownership

| Worker | Owned files | Acceptance |
| --- | --- | --- |
| tests | `tests/storage/test_metadata_store.py`, `tests/fetching/test_connectors.py`, `tests/fetching/test_notion.py`, `tests/fetching/test_notion_fetch_skip_integration.py`, optionally `tests/e2e/test_notion_fetch_skip_flow.py` | RED: public timestamp API; batch hydrate once; no N× get_document |
| store-fetch | `storage/metadata_store.py`, `fetching/connectors.py`, `fetching/notion.py` | Public helper + batch API + loader wiring |
| docs | this plan; `.agents/docs/architecture.md` if needed | Record progress / hydrate wording |

### Deferred (still)

None from the prior yellow list after this follow-up.
