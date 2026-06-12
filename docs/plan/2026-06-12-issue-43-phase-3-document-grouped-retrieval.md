# Issue 43 Phase 3 Document-Grouped Retrieval

## User request

Implement Issue `#43` Phase 3, which maps to Issue `#38`:
add a document-grouped retrieval surface for search results.

## Branch preflight result

- Starting worktree: `/Users/eunhwa/.codex/worktrees/c298/MCPContentSearch`
- Initial state: dirty on `feature/slim-mcp-core` with local modifications in
  `environments/token.py`
- Safety action: did not switch, pull, or delete branches in the dirty worktree
- Freshness check: `git fetch origin main` succeeded from the dirty worktree
- Isolated worktree: created
  `/Users/eunhwa/.codex/worktrees/issue43-phase3/MCPContentSearch`
  on `feature/issue-43-phase-3` from `origin/main`
- Current safe branch before edits:
  `## feature/issue-43-phase-3...origin/main`

## Scope and non-goals

In scope:

- Add grouped retrieval service/model support so callers can browse unique
  matching documents without repeated chunk rows dominating the list.
- Expose the additive MCP tool `search_documents(query, filters=None, top_k=10)`
  over that grouped retrieval behavior.
- Preserve existing `search_context` chunk-level behavior for citation and
  evidence workflows.
- Pick one representative chunk per returned document and expose enough
  document metadata for follow-up `fetch_context(document_id=...)`.
- Add focused service tests, contract tests, app-composition coverage, fake/temp
  e2e coverage, and maintained docs updates for the new retrieval surface.

Non-goals:

- No removal or behavioral regression of `search_context`
- No migration, deletion, or inspection of user Chroma/SQLite data
- No answer-generation policy change for `answer_with_citations`
- No live external source validation beyond deterministic local/fake tests

## Desired behavior and acceptance criteria

- A caller can request grouped document-level retrieval through a dedicated MCP
  tool without using chunk-level result parsing.
- Results collapse repeated chunks from the same document and keep only the
  best representative chunk per returned document.
- The returned document rows include `document_id`, `source_id`, `source_type`,
  `title`, `url`, `path`, `score`, `preview`, and representative `chunk_id`.
- Existing `search_context` callers keep the same contract and result shape.
- Tests cover same-document chunk collapse and retained-source filtering.

## Contract choice

Recommended implementation:

- Add a new MCP tool `search_documents(query, filters=None, top_k=10)` instead
  of overloading `search_context`.
- Implement document grouping inside `ContextSearchService` using the existing
  retrieval/reranking pipeline, then choose the highest-ranked chunk as the
  representative row for each document.

Reasoning:

- This keeps the chunk-level `search_context` contract stable.
- It avoids a polymorphic `search_context` payload keyed by hidden filters.
- It makes document browsing explicit in docs, tests, and MCP tool discovery.

## Module boundaries and likely changed files

- `core/models.py`
  - Add the grouped document search DTO.
- `search/context_service.py`
  - Add grouped retrieval behavior and document-aware candidate expansion.
- `search/retrieval_pipeline.py`
  - Keep grouped retrieval off the optional query-rewrite path.
- `api/tools.py`
  - Register the new MCP tool and sanitize the new payload through a document
    result allowlist plus the same retained-source filter path.
- `tests/search/test_context_service.py`
  - Add grouped-retrieval service coverage, including duplicate-heavy
    candidate expansion.
- `tests/api/test_tools_contract.py`
  - Add MCP contract coverage for registration, retained-source filtering, and
    payload shape.
- `tests/e2e/test_contextwiki_flow.py`
  - Add fake/temp functional coverage for the new caller surface.
- `tests/test_app_composition.py`
  - Ensure the composed FastMCP app advertises the new tool.
- `README.md`
  - Document the new tool in the retained MCP surface.
- `.agents/docs/architecture.md`
  - Update the retained tool list and retrieval flow description.
- `.agents/docs/adr/0006-slim-mcp-core-scope.md`
  - Align the accepted retained tool surface with the additive MCP tool.
- `docs/contextwiki-core-understanding.md`
  - Record the document-browsing surface and representative-chunk behavior.

## Worker orchestration plan

This work is not atomic because it spans service/model behavior, MCP contract,
tests, ADR/doc alignment, and repeated review-fix loops.

Planned workers:

1. `grouped-retrieval-worker`
   - Ownership: `core/models.py`, `search/context_service.py`,
     `search/retrieval_pipeline.py`, `tests/search/test_context_service.py`
   - Goal: implement grouped retrieval behavior, candidate expansion, rewrite
     boundary, and service-level regression coverage
2. `mcp-contract-worker`
   - Ownership: `api/tools.py`, `tests/api/test_tools_contract.py`,
     `tests/e2e/test_contextwiki_flow.py`, `tests/test_app_composition.py`
   - Goal: expose the new tool safely and cover contract/e2e expectations
3. `docs-worker`
   - Ownership: `README.md`, `.agents/docs/architecture.md`,
    `.agents/docs/adr/0006-slim-mcp-core-scope.md`,
    `docs/contextwiki-core-understanding.md`, this plan file updates
   - Goal: align docs with the new retained retrieval surface after code lands

Workers must preserve other user/agent edits, avoid secrets, and not inspect or
mutate local user Chroma/SQLite data outside temp-test fixtures.

## Verification plan

Focused verification:

- `uv run pytest tests/api/test_tools_contract.py -k search_documents`
- `uv run pytest tests/e2e/test_contextwiki_flow.py -k search_documents`
- `uv run pytest tests/test_app_composition.py -k slim_mcp_tools`
- `python -m compileall api core environments fetching indexing search storage main.py`

Broader verification after integration:

- `uv run pytest tests/search/test_context_service.py tests/api/test_tools_contract.py tests/e2e/test_contextwiki_flow.py tests/test_app_composition.py`
- `./scripts/verify_functional_e2e.sh`

Fallback if `uv run ...` is unavailable:

- run the closest dependency-free check and record the blocker

## Functional smoke matrix

| Feature or workflow | Caller surface | Safe data mode | Expected result | Command/action | Planned result |
| --- | --- | --- | --- | --- | --- |
| Chunk retrieval remains stable | MCP `search_context` | fake/temp local data | existing chunk-level results still return unchanged | `uv run pytest tests/api/test_tools_contract.py tests/e2e/test_contextwiki_flow.py tests/test_app_composition.py`; `./scripts/verify_functional_e2e.sh` | passed |
| Document browsing retrieval | MCP `search_documents` | fake/temp local data | one result per document, representative chunk preserved | `uv run pytest tests/api/test_tools_contract.py -k search_documents`; `uv run pytest tests/e2e/test_contextwiki_flow.py -k search_documents`; `./scripts/verify_functional_e2e.sh` | passed |
| Follow-up context fetch | MCP `fetch_context(document_id=...)` after grouped search | fake/temp local data | returned `document_id` can hydrate document/chunks | `uv run pytest tests/e2e/test_contextwiki_flow.py -k search_documents`; `./scripts/verify_functional_e2e.sh` | passed |

## Architecture and ADR constraints

- Stay inside ADR `0006` slim MCP core scope; no reintroduction of removed web,
  console, or wiki surfaces.
- Keep layered boundaries from `.agents/docs/architecture.md`; grouping logic
  belongs in `search/`, not `api/`.
- Preserve ADR `0002` SQLite citation/lifecycle authority; grouped retrieval may
  expose document summaries, but it must still derive from SQLite-hydrated chunk
  records and must not bypass managed active-result gating.
- Retained-source filtering and public payload sanitization in `api/tools.py`
  must apply to the new tool as well.

## Risks, assumptions, and rollback

- Assumption: an additive MCP tool is acceptable for Phase 3 because Issue `#38`
  explicitly allows a new tool.
- Risk: grouped retrieval could return fewer than `top_k` unique documents when
  retrieval candidates are dominated by one document. Mitigation: reuse the
  existing retrieval pipeline candidate expansion and group after reranking.
- Risk: docs drift across README, architecture, and understanding note.
  Mitigation: dedicate a docs worker after code integration.
- Rollback: revert the new tool, grouped DTO/service logic, and matching docs;
  no user data migration is involved.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Preserved the dirty starting worktree, fetched `origin/main`, and created isolated branch `feature/issue-43-phase-3`. | `git status --short --branch`; `git fetch origin main`; `git worktree add -b feature/issue-43-phase-3 ... origin/main` |
| Planning | completed | Read harness docs, workflow docs, architecture, ADR `0002`/`0006`, roadmap issue `#43`, and implementation target issue `#38`; selected the additive `search_documents` contract. | `.agents/docs/harness-engineering.md`; `.agents/docs/github-workflow.md`; `.agents/docs/architecture.md`; `.agents/docs/adr/README.md`; `.agents/docs/adr/0002-contextwiki-metadata-and-citation-store.md`; `.agents/docs/adr/0006-slim-mcp-core-scope.md`; `gh issue view 43`; `gh issue view 38` |
| Worker orchestration design | completed | Spawned three bounded workers for service/model, MCP contract/app/e2e, and docs alignment; each preserved unrelated edits and stayed inside its owned files. | worker ids `019eb9ab-2c4d-7361-9625-a0238e5d6cc9`, `019eb9b0-28d4-7c42-86e1-7ae2d18c96ec`, `019eb9b0-5813-7162-ad05-b448bb1f2370` |
| Implementation | completed | Added grouped retrieval DTO/service logic, the additive `search_documents` MCP tool, contract/e2e/app coverage, and matching maintained docs while preserving existing `search_context` and `answer_with_citations` contracts. | `core/models.py`; `search/context_service.py`; `api/tools.py`; `tests/search/test_context_service.py`; `tests/api/test_tools_contract.py`; `tests/e2e/test_contextwiki_flow.py`; `tests/test_app_composition.py`; `README.md`; `.agents/docs/architecture.md`; `docs/contextwiki-core-understanding.md` |
| Initial focused verification | completed | Followed TDD at both service and MCP contract layers: confirmed grouped retrieval was missing, then reran focused tests plus compile checks after implementation. | `uv run pytest tests/search/test_context_service.py -k search_documents` failed with `AttributeError: 'ContextSearchService' object has no attribute 'search_documents'`; rerun passed; `uv run pytest tests/api/test_tools_contract.py -k search_documents` failed with `KeyError: 'search_documents'`; rerun passed; `uv run pytest tests/test_app_composition.py -k slim_mcp_tools`; `uv run pytest tests/e2e/test_contextwiki_flow.py -k search_documents`; `python -m compileall api core environments fetching indexing search storage main.py` |
| Functional smoke | completed | Ran retained MCP functional smoke through fake/temp caller surfaces after focused verification. Grouped document search, unchanged chunk retrieval, fetch, and citation flows all stayed green. | `uv run pytest tests/search/test_context_service.py tests/api/test_tools_contract.py tests/e2e/test_contextwiki_flow.py tests/test_app_composition.py` -> `152 passed`; `./scripts/verify_functional_e2e.sh` -> `314 passed` |
| Initial docs alignment | completed | Updated README, architecture, and the maintained ContextWiki understanding note so the retained tool surface and grouped browsing semantics match the implementation. | `README.md`; `.agents/docs/architecture.md`; `docs/contextwiki-core-understanding.md`; `git diff --check`; `git diff --cached --check` |
| Review pass 1 | completed/actionable | Fresh user-requested 3-reviewer pass found one correctness gap and two documentation drift issues: `search_documents` under-filled unique documents because grouping happened after chunk truncation; ADR `0006` was missing the additive retained tool; and the plan scope drifted from the actual touched files. | reviewers `019eb9ba-fa87-7413-a3ed-1a52d04586b5`, `019eb9bb-24e8-7f51-a284-71856292167c`, `019eb9bb-4e98-7e22-9c40-8e3368b00665` |
| Review pass 1 fixes | completed | Added document-aware candidate expansion plus regression coverage, updated ADR `0006` to include `sync_all`/`search_documents`, and corrected the plan scope/module sections to match the actual work. | `search/context_service.py`; `tests/search/test_context_service.py`; `.agents/docs/adr/0006-slim-mcp-core-scope.md`; this file |
| Review pass 2 | completed/actionable | Fresh user-requested 3-reviewer pass found two remaining implementation issues: `search_documents` incorrectly inherited the optional query-rewrite egress path, and representative chunk selection could drift from actual reranked order on tie-like candidate sequences. | reviewers `019eb9c1-26f6-7012-816f-39b5a7c8d172`, `019eb9c1-5358-7e83-bfa0-f795b159aaf1`, `019eb9c1-84e3-7c30-adbb-0d241bcea17b` |
| Review pass 2 fixes | completed | Disabled query rewrite for `search_documents`, made grouped representative selection preserve first reranked occurrence per document, and added regression coverage for both behaviors before rerunning focused and broader verification. | `search/retrieval_pipeline.py`; `search/context_service.py`; `tests/search/test_context_service.py`; `uv run pytest tests/search/test_context_service.py -k search_documents`; `uv run pytest tests/search/test_context_service.py tests/api/test_tools_contract.py tests/e2e/test_contextwiki_flow.py tests/test_app_composition.py`; `./scripts/verify_functional_e2e.sh` |
| Review pass 3 | completed/actionable | Fresh user-requested 3-reviewer pass found remaining low-severity contract/docs issues: the query-rewrite regression test did not cover the rewrite gate path, README's functional E2E description omitted grouped document browsing, and the plan still needed small consistency cleanup. | reviewers `019eb9c8-a39b-7162-9917-6f6ee0baf011`, `019eb9c8-d1e2-7493-bd4c-31e877e52272`, `019eb9c9-006d-7fa2-a365-5f0b211f8bb1` |
| Review pass 3 fixes | completed | Tightened the rewrite regression to assert `allow_query_rewrite=False`, updated README functional-smoke wording to include grouped browsing, and clarified plan history labels before rerunning targeted checks. | `tests/search/test_context_service.py`; `README.md`; this file; `uv run pytest tests/search/test_context_service.py -k search_documents`; `git diff --check` |
| Review pass 4 | completed/actionable | Fresh user-requested 3-reviewer pass found two final low-severity cleanup items: the public `search_documents` payload still exposed extra representative-chunk metadata, and the plan/evidence bookkeeping still needed final reconciliation. | reviewers `019eb9cd-3461-71f3-925e-18a96e3ce9b0`, `019eb9cd-6694-7363-a121-52c09091aa2e`, `019eb9cd-95e8-7413-9281-74c7acf18067` |
| Review pass 4 fixes | completed | Narrowed the public `search_documents` payload allowlist, extended contract tests with negative field assertions, and split the review-history bookkeeping into the real pass boundaries. | `api/tools.py`; `tests/api/test_tools_contract.py`; this file; `uv run pytest tests/api/test_tools_contract.py -k search_documents`; `uv run pytest tests/search/test_context_service.py -k search_documents`; `git diff --check` |
| Review pass 5 | completed | Fresh user-requested 3-reviewer pass reported no actionable findings after the final payload and plan-history cleanup. | reviewers `019eb9dd-f793-7dd2-b410-f3c5f50cf179`, `019eb9de-29e5-7ae1-8e78-092dcaf8c55b`, `019eb9de-5f1b-78c2-8945-9e99c94ad0a4` |
| Final reverification | completed | Reran compile, focused changed-surface tests, and the retained functional smoke gate after the clean reviewer pass so PR evidence reflects the final worktree state. | `python -m compileall api core environments fetching indexing search storage main.py`; `uv run pytest tests/search/test_context_service.py tests/api/test_tools_contract.py tests/e2e/test_contextwiki_flow.py tests/test_app_composition.py` -> `153 passed`; `./scripts/verify_functional_e2e.sh` -> `315 passed`; `git diff --check` |
| PR delivery | in_progress | Stage the tracked code/docs plus the new plan file, commit on `feature/issue-43-phase-3`, push, and open a `main`-base PR. | pending |
