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
