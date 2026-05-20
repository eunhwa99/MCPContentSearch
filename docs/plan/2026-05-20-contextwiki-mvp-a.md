# ContextWiki MVP A Implementation Plan

## User Request

Implement the first ContextWiki slice:

> Notion/Tistory existing functionality stabilization, source/job/status models, incremental ingestion, and citation-based `answer_with_citations`.

The user also asked for the complete product plan, which is captured in `docs/plan/2026-05-20-contextwiki-roadmap.md`.

## Branch Preflight Result

- Repository: `/Users/eunhwa/IdeaProjects/MCPContentSearch`
- Latest main checked with `git fetch origin main`.
- Latest remote main: `origin/main` at `f9b3157`.
- Work started from: `origin/main`.
- Working branch: `feature/contextwiki-mvp-planning`.
- Worktree state before writing this plan: clean.
- Current branch is not `main`, satisfying the repository branch policy.

## Scope and Non-goals

In scope for MVP A:

- Preserve existing `main.py` composition-root style.
- Add a metadata persistence strategy for sources, sync jobs, documents, and chunks.
- Register built-in Notion and Tistory sources without storing raw secrets.
- Add per-source sync job lifecycle and retry status.
- Extend incremental ingestion from document-only hash comparison to source/job/document/chunk metadata.
- Store citation-ready chunk metadata.
- Add source-filtered context search.
- Add context fetch by document id or chunk id.
- Add citation-grounded answer generation.
- Return an insufficient-evidence response when retrieved context is too weak.
- Update MCP tool documentation and README.
- Add required verification infrastructure so unit, integration, and fake E2E tests run together before completion.
- Keep live Notion/Tistory credentials and network smoke tests optional and opt-in only.

Non-goals for MVP A:

- Do not implement GitHub repo ingestion.
- Do not implement generic website/docs URL ingestion.
- Do not implement Auto Wiki generation.
- Do not implement golden-set evaluation or observability dashboards.
- Do not implement remote HTTP deployment.
- Do not implement Resume/JD assistant tools.
- Do not implement Markdown/PDF uploads.
- Do not delete, reset, inspect, or migrate existing local ChromaDB data without explicit approval.
- Do not store API tokens in SQLite, logs, test fixtures, or docs.
- Do not commit, push, or create a PR unless explicitly requested.

## Acceptance Criteria

- `list_sources()` returns built-in Notion/Tistory source records with status fields.
- `sync_source(source_id)` creates a sync job, runs the relevant connector, records status, and indexes only new or changed content.
- `get_sync_status(source_id: str | None = None)` returns source-level and job-level status without relying only on the old global `IndexStatusModel`.
- Failed sync attempts record `failed` status and `last_error`, and can be retried by calling `sync_source` again.
- Chunk metadata is persisted with source, document, title, URL, path, chunk index, optional line range, updated time, and content hash.
- `search_context(query, filters=None, top_k=10)` returns structured results with chunk ids and citation metadata.
- `fetch_context(document_id=None, chunk_id=None)` returns full stored context metadata and text for the requested document or chunk.
- `answer_with_citations(question, filters=None, top_k=5)` answers only from retrieved chunks and includes the citations used.
- `answer_with_citations` returns an insufficient-evidence result when retrieved chunks do not meet the configured evidence threshold.
- Existing tools remain available or receive a documented compatibility path unless explicitly removed in a later plan.
- Python modules pass compile verification.
- `scripts/verify_all.sh` exists and runs the required compile check plus `uv run pytest -m "not live"`.
- pytest markers distinguish `unit`, `integration`, `e2e`, and `live`.
- Required tests do not require live Notion/Tistory credentials.
- A fake-source E2E test covers source registration, sync, status, context search, context fetch, citation answer, and insufficient-evidence behavior.
- Live external API smoke tests, if added, are marked `live` and run only with an explicit opt-in such as `RUN_LIVE_E2E=1 uv run pytest -m live`.

## Current Implementation Baseline

- `main.py` creates config, Chroma collection, `ContentIndexer`, `SearchService`, `WebSearcher`, `DynamicSearchService`, and registers MCP tools.
- `api/tools.py` currently exposes:
  - `search_content`
  - `search_notion`
  - `search_tistory`
  - `trigger_index_all_content`
  - `get_index_status`
- `fetching/notion.py` and `fetching/tistory.py` already return `DocumentModel` instances.
- `indexing/manager.py` compares `doc_id` and `content_hash` from Chroma metadata.
- `search/service.py` returns markdown-formatted results, not structured citation context.
- There is no SQLite metadata store, source registry, sync job store, or citation answer service.

## Architecture and ADR Constraints

- Follow `.agents/docs/architecture.md` and ADR 0001.
- Keep `main.py` as dependency composition only.
- Keep MCP handler formatting and tool contracts in `api/`.
- Keep external Notion/Tistory retrieval in `fetching/`.
- Keep vector writes and deduplication behavior in `indexing/`.
- Keep search orchestration and structured search in `search/`.
- Keep shared data models in `core/`.
- Adding SQLite metadata and citation-oriented persistence is a new persistence strategy. Add `ADR 0002` before implementation or as the first implementation task.
- Do not inspect or mutate existing user Chroma data directly in tests.

## Proposed Module Boundary

Likely new or modified modules:

- Create: `.agents/docs/adr/0002-contextwiki-metadata-and-citation-store.md`
- Modify: `.agents/docs/adr/README.md`
- Modify: `environments/config.py`
- Modify: `core/models.py`
- Modify: `core/exceptions.py`
- Modify: `core/utils.py`
- Create: `storage/__init__.py`
- Create: `storage/metadata_store.py`
- Create: `scripts/verify_all.sh`
- Create: `fetching/connectors.py`
- Create: `indexing/chunker.py`
- Modify: `indexing/converter.py`
- Modify: `indexing/indexer.py`
- Create: `indexing/ingestion_service.py`
- Modify: `search/service.py`
- Create: `search/context_service.py`
- Create: `search/answer_service.py`
- Modify: `api/tools.py`
- Modify: `main.py`
- Modify: `pyproject.toml` or create `pytest.ini`
- Modify: `README.md`
- Add tests under `tests/` for storage, chunking, ingestion decisions, search formatting, answer grounding, and fake E2E flow.

The exact file list can be reduced during implementation if a smaller change preserves the same contracts.

## Step Breakdown

| Step | Label | Boundary | Acceptance Criteria |
| --- | --- | --- | --- |
| 1 | `adr-persistence` | Document SQLite metadata and citation-store decision. | ADR 0002 exists and ADR index links it. |
| 2 | `metadata-models` | Add source, job, document, chunk, context result, and citation answer models. | Models validate expected fields and avoid raw secret storage. |
| 3 | `metadata-store` | Add SQLite schema and repository methods. | Temporary SQLite tests can create, upsert, query, and update status records. |
| 4 | `test-harness` | Add pytest markers and the required all-tests command. | `scripts/verify_all.sh` runs compile checks and `uv run pytest -m "not live"`; `live` tests are excluded by default. |
| 5 | `source-registry` | Register Notion/Tistory sources from env-backed config. | `list_sources` can return source status without live network calls. |
| 6 | `chunking` | Add deterministic text chunking and metadata generation. | Chunk ids are stable for same document/chunk index/content hash. |
| 7 | `ingestion-service` | Add per-source sync lifecycle around existing fetchers and indexer. | Changed documents are indexed; unchanged documents are skipped; failures are recorded. |
| 8 | `indexer-integration` | Persist chunk metadata while writing Chroma documents. | Chroma metadata and SQLite chunk metadata share ids needed for citations. |
| 9 | `context-search` | Add structured search over existing hybrid retrieval. | Search returns chunk ids, scores, text previews, and citation metadata. |
| 10 | `context-fetch` | Fetch document or chunk context from metadata store. | Fetch returns text plus source/document/chunk metadata. |
| 11 | `citation-answer` | Add answer service with evidence threshold and citation output. | Insufficient evidence returns a refusal-style response; sufficient evidence includes citations. |
| 12 | `mcp-tools` | Register MVP tools in `api/tools.py`. | MCP exposes `list_sources`, `sync_source`, `get_sync_status`, `search_context`, `fetch_context`, `answer_with_citations`. |
| 13 | `fake-e2e` | Add deterministic E2E coverage using fake sources and temporary persistence. | E2E covers sync, status, search, fetch, citation answer, and insufficient evidence without live credentials. |
| 14 | `compat-docs` | Preserve or document old tool compatibility and update README. | README describes ContextWiki MVP, required verification, and optional live smoke usage. |
| 15 | `verification-review` | Run verification and review loop. | `scripts/verify_all.sh` passes or blockers are reported; subagent review loop runs if available. |

## Detailed Implementation Notes

### Source and Secret Handling

- Store source auth as an `auth_ref`, such as `env:NOTION_API_KEY`, not the raw token.
- Tistory source config may store the blog name because it is not a secret.
- Source records should include:
  - `source_id`
  - `source_type`
  - `name`
  - `enabled`
  - `auth_ref`
  - `sync_status`
  - `last_synced_at`
  - `last_error`
  - `created_at`
  - `updated_at`

### Sync Jobs

- A sync job should include:
  - `job_id`
  - `source_id`
  - `status`: `queued`, `running`, `succeeded`, `failed`
  - `started_at`
  - `finished_at`
  - `total_documents`
  - `processed_documents`
  - `indexed_chunks`
  - `skipped_documents`
  - `error_message`
- `sync_source` should be safe to call again after failure.
- Background sync is acceptable only if `get_sync_status` remains truthful.

### Documents and Chunks

- Document identity should preserve current ids where possible:
  - Notion: `notion_<page_id>`
  - Tistory: `tistory_<post_id>`
- Chunk identity should be deterministic:
  - `<document_id>:chunk:<chunk_index>:<short_content_hash>`
- Chunk line ranges are best-effort for Notion/Tistory text because current extractors do not preserve exact source file line numbers. Code ingestion in Phase B will provide exact file line ranges.

### Citation Search

`search_context` should return structured data similar to:

```json
{
  "query": "string",
  "results": [
    {
      "chunk_id": "string",
      "document_id": "string",
      "source_id": "string",
      "source_type": "notion",
      "title": "string",
      "url": "string",
      "path": "string",
      "score": 0.91,
      "preview": "string",
      "line_start": null,
      "line_end": null,
      "updated_at": "string"
    }
  ]
}
```

### Citation Answers

`answer_with_citations` should use only retrieved chunks. The MVP can support two execution modes:

- If an LLM provider is configured, ask it to answer using only supplied evidence and cite chunk ids.
- If no LLM provider is configured, return a grounded evidence summary with citations rather than hallucinating.

The tool must not invent citations. If evidence is below threshold, return a response with:

- `answer`: a short insufficient-evidence message.
- `citations`: empty or the weak retrieved evidence marked as unused.
- `evidence_status`: `insufficient`.

## Test and Verification Plan

Required test categories:

- `unit`: pure logic, models, content hashing, chunk id generation, and citation validation.
- `integration`: service boundaries with temporary SQLite, temporary Chroma or fake vector store, fake connectors, and mocked external APIs.
- `e2e`: deterministic product flow using fake sources and temporary persistence.
- `live`: optional external API smoke tests. These are excluded from required verification.

Focused tests to add:

- `tests/storage/test_metadata_store.py`
  - Initializes temporary SQLite DB.
  - Upserts sources.
  - Creates and updates sync jobs.
  - Upserts documents and chunks.
  - Fetches by document id and chunk id.
- `tests/indexing/test_chunker.py`
  - Splits text deterministically.
  - Preserves title/source/url metadata.
  - Produces stable chunk ids.
- `tests/indexing/test_ingestion_service.py`
  - Uses fake connector and fake indexer.
  - Indexes new documents.
  - Skips unchanged documents.
  - Records failed sync job on connector error.
- `tests/search/test_context_service.py`
  - Uses fake retrieval nodes.
  - Applies source filters.
  - Returns citation-ready structured results.
- `tests/search/test_answer_service.py`
  - Returns insufficient evidence for low/empty retrieval.
  - Includes citations for sufficient mocked evidence.
- `tests/e2e/test_contextwiki_flow.py`
  - Registers fake Notion/Tistory-style sources without credentials.
  - Runs `sync_source`.
  - Confirms `get_sync_status` reports success.
  - Runs `search_context` with a source filter.
  - Fetches a returned chunk with `fetch_context`.
  - Runs `answer_with_citations` and verifies citations reference real chunk ids.
  - Asks an unsupported question and verifies `evidence_status` is `insufficient`.

Required verification command:

```bash
scripts/verify_all.sh
```

The script should run:

```bash
python -m compileall api core environments fetching indexing search storage main.py
uv run --with pytest pytest -m "not live"
```

Required CI should use `scripts/verify_all.sh`, so unit, integration, and fake E2E tests all run before completion.

Optional live smoke command:

```bash
RUN_LIVE_E2E=1 uv run --with pytest pytest -m live
```

Live smoke tests are useful before demos, releases, or connector-heavy changes, but they are not required for normal CI or completion.

Fallback verification commands:

```bash
python -m compileall api core environments fetching indexing search storage main.py
python -m pytest -m "not live"
```

If `uv run --with pytest pytest -m "not live"` is blocked by local dependency or workspace issues, report the blocker and run `python -m pytest -m "not live"` as the fallback. Do not replace required verification with plain `uv run pytest`, because that can include opt-in `live` smoke tests.

Docs-only changes before runtime implementation can use:

```bash
rg --files AGENTS.md .agents/docs .agents/skills docs/plan
git status --short
git diff --check
```

## MCP Tool Contract Plan

New MVP tools:

- `list_sources() -> dict`
- `sync_source(source_id: str) -> dict`
- `get_sync_status(source_id: str | None = None) -> dict`
- `search_context(query: str, filters: dict | None = None, top_k: int = 10) -> dict`
- `fetch_context(document_id: str | None = None, chunk_id: str | None = None) -> dict`
- `answer_with_citations(question: str, filters: dict | None = None, top_k: int = 5) -> dict`

Compatibility tools:

- Keep existing tools initially:
  - `search_content`
  - `search_notion`
  - `search_tistory`
  - `trigger_index_all_content`
  - `get_index_status`
- They can delegate to new services where practical.
- Any removal or rename should be a later explicit contract-change plan.

## Multi-task and PR Boundary

MVP A has dependent shared contracts, so it should not be split into independent PRs at first. Suggested internal sequence:

- PR or branch 1: ADR, metadata models, SQLite store.
- PR or branch 2: source registry and ingestion service.
- PR or branch 3: context search/fetch and citation answer.
- PR or branch 4: docs, compatibility polish, tests, review fixes.

Because these slices share models and MCP contracts, stacked PRs would be safer than independent PRs if PRs are requested later.

## Risks and Rollback Notes

- Risk: SQLite metadata and Chroma can get out of sync.
  - Mitigation: idempotent upserts, deterministic chunk ids, sync job status, and tests around changed/unchanged documents.
- Risk: Background tasks can hide critical sync failures.
  - Mitigation: every sync job writes status and error messages; `sync_source` return value includes job id.
- Risk: Citation answer may hallucinate if the prompt is too permissive.
  - Mitigation: answer service must pass only retrieved evidence and validate citations before returning.
- Risk: Existing user Chroma data may not have new chunk ids.
  - Mitigation: do not mutate existing data silently. New metadata applies to future syncs. README should explain that a fresh sync may be needed for citation features.
- Rollback: remove new SQLite metadata files and service wiring, then restore `api/tools.py` and `main.py` to previous tool registration. Do not delete local Chroma data as part of rollback.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created `feature/contextwiki-mvp-planning` from latest `origin/main`. | `git fetch origin main`, `git switch -c feature/contextwiki-mvp-planning origin/main` |
| Roadmap alignment | completed | Full ContextWiki roadmap created separately from MVP A. | `docs/plan/2026-05-20-contextwiki-roadmap.md` |
| MVP A plan | completed | Detailed first implementation slice defined with module boundaries and acceptance criteria. | This file |
| Focused verification | completed | Docs-only checks passed, including no-output whitespace checks for untracked plan files. | `rg --files AGENTS.md .agents/docs .agents/skills docs/plan`, `git status --short --branch`, `git diff --check`, `git diff --no-index --check /dev/null docs/plan/2026-05-20-contextwiki-mvp-a.md` |
| Review | completed | Fresh follow-up review completed after the testing-policy update; final reviewer reported no actionable findings. | Prior final pass by subagent `019e4500-3727-7f31-b753-060567ff61ec`; follow-up finding by subagent `019e450a-8a76-7eb3-9c47-ae2b84e80d1d`; final pass by subagent `019e450c-76c9-75d1-b073-70f41170aff0` |
| ADR and metadata implementation | completed | Added ADR 0002, SQLite metadata store, source/job/document/chunk models, source registry, chunker, ingestion service, context search, and citation answer service. | `.agents/docs/adr/0002-contextwiki-metadata-and-citation-store.md`, `storage/metadata_store.py`, `indexing/ingestion_service.py`, `search/context_service.py`, `search/answer_service.py` |
| MCP and composition wiring | completed | Wired ContextWiki services into `main.py` and added MVP MCP tools while preserving existing tools. | `main.py`, `api/tools.py` |
| Required test harness | completed | Added pytest markers, fake E2E coverage, and `scripts/verify_all.sh` with live tests excluded by default. | `pyproject.toml`, `scripts/verify_all.sh`, `tests/e2e/test_contextwiki_flow.py` |
| Runtime review pass 1 | completed | Fresh reviewer found source status persistence, failed-index retry desync, source-filter ordering, and canonical id consistency issues. | Subagent `019e4518-8f58-7f90-8706-5ba11cfe342e` |
| Review fixes | completed | Added regression tests and fixed source registration status preservation, metadata commit ordering, canonical document id use, and pre-limit fake retriever source filtering. | `tests/indexing/test_ingestion_service.py`, `tests/e2e/test_contextwiki_flow.py`, `storage/metadata_store.py`, `indexing/ingestion_service.py`, `indexing/chunker.py`, `search/context_service.py` |
| Runtime review pass 2 | completed | Fresh reviewer found stale chunk deletion, production vector filtering, disabled source handling, and compile coverage issues. | Subagent `019e451e-8a96-7ff3-99d4-d12dd1dffd95` |
| Review fixes 2 | completed | Added regression tests and fixed stale-only vector deletion, disabled source failed jobs, vector metadata filters, and storage compile coverage. | `tests/indexing/test_ingestion_service.py`, `tests/search/test_context_service.py`, `indexing/ingestion_service.py`, `search/context_service.py`, `scripts/verify_all.sh` |
| Runtime review pass 3 | completed | Fresh reviewer found non-atomic document/chunk metadata commit and unknown-source MCP error-contract gaps, plus missing MCP contract test coverage. | Subagent `019e4524-7fff-7863-a195-1c4b91a89545` |
| Review fixes 3 | completed | Added atomic document+chunk metadata commit, structured `sync_source` error response, and MCP tool contract tests. | `storage/metadata_store.py`, `indexing/ingestion_service.py`, `api/tools.py`, `tests/indexing/test_ingestion_service.py`, `tests/api/test_tools_contract.py` |
| Runtime review pass 4 | completed | Fresh reviewer found legacy Chroma result-window, MCP fake E2E contract, and storage transaction rollback test gaps. | Subagent `019e452b-3c6d-7683-a532-ac233f6a4419` |
| Review fixes 4 | completed | Added over-retrieval for metadata-backed chunks, MCP contract-shape tests, and storage transaction rollback regression. | `search/context_service.py`, `tests/search/test_context_service.py`, `tests/api/test_tools_contract.py`, `tests/storage/test_metadata_store.py` |
| Runtime review pass 5 | completed | Fresh reviewer found remaining legacy Chroma filtering, undeclared pytest dependency, and real MCP E2E coverage gaps. | Subagent `019e4531-ef3c-7bf2-862c-5d19d4b50a53` |
| Review fixes 5 | completed | Added `contextwiki_managed` vector metadata filtering for ContextWiki chunk docs only, declared pytest dev dependency and explicit `uv --with pytest` verification path, and routed fake E2E through real MCP handlers. | `indexing/converter.py`, `search/context_service.py`, `pyproject.toml`, `scripts/verify_all.sh`, `tests/e2e/test_contextwiki_flow.py`, `tests/search/test_context_service.py`, `tests/indexing/test_chunker.py` |
| Runtime review pass 6 | completed | Fresh reviewer found singular `source_id` filter handling, temp Chroma E2E coverage, and stale fallback compile-command docs. | Subagent `019e453f-4bb3-7ce2-bfec-0a44386a634d` |
| Review fixes 6 | completed | Normalized singular and plural source filters, added source-filter regressions through search and citation answer, added temp Chroma + MockEmbedding E2E smoke, and updated fallback compile docs. | `search/context_service.py`, `tests/search/test_context_service.py`, `tests/e2e/test_contextwiki_flow.py`, this file |
| Runtime verification | completed | Required verification passed; local `uv` pytest health check is unavailable, so the script used `python -m pytest -m "not live"` fallback while preserving live-test exclusion. | `scripts/verify_all.sh` -> 24 passed |
| Runtime review | completed | Fresh follow-up reviewer reported no actionable findings after the sixth fix set. | Subagent `019e454a-36a1-7871-a3ac-e74881c92187`; `git diff --check`, compileall, `python -m pytest -m "not live"` -> 24 passed |
