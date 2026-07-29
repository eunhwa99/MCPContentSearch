# Background sync-all and query-rewrite removal

## User request

- Make `sync_all()` launch per-source synchronization in the background, like
  `sync_source()`.
- Remove the optional LLM query-rewrite feature completely.
- Update code, tests, README, maintained architecture, configuration examples,
  scripts, and other in-scope documentation to match.
- Keep the work local: do not commit, stage, push, or update a PR.

## Branch preflight result

- Continue the user-selected isolated worktree
  `/Users/eunhwa/IdeaProjects/MCPContentSearch-ai-portfolio-hardening`.
- Current branch: `feature/ai-portfolio-hardening`.
- The worktree is intentionally dirty with existing matched-context, README,
  evaluation, and semantic-benchmark work. Do not switch branches, pull,
  delete branches, or overwrite unrelated changes.
- This is an explicit continuation of the current branch. No network access is
  required for implementation or verification.

## Scope

### Background `sync_all`

- Reuse the existing per-source background launch and SQLite running-job guard
  used by `sync_source`.
- Launch configured sources concurrently and return without waiting for
  connector fetching/indexing to finish.
- Preserve `sync_source`'s current public job payload.
- Replace completion-oriented bulk fields with launch-oriented fields:
  - per-source `launch_outcome`: `started`, `already_running`, `skipped`, or
    `failed`;
  - top-level `status`: `accepted`, `partial`, or `failed`;
  - summary counts for each launch outcome plus request timestamp;
  - the safe source and job payloads needed for later polling.
- Treat `accepted` as “the launch request was processed,” never as evidence
  that indexing completed.
- Use `get_sync_status(source_id)` to observe each source until its latest job
  reaches a terminal state.
- Keep existing background task finalization, heartbeat, orphan recovery,
  stale-cleanup safety, and source-level overlap guards.

### Query-rewrite removal

- Delete the OpenAI query-rewriter implementation and all runtime construction.
- Remove search-LLM environment/config fields and validation.
- Remove conditional rewrite branches, policies, rewrite-specific debug fields,
  answer-debug rendering, live-smoke controls, and dead redaction helpers used
  only by rewrite prompts.
- Keep deterministic query-term extraction, retrieval query variants, Chroma
  retrieval, SQLite validation, metadata fallback, ranking, intent handling,
  citations, and query redaction.
- `search_context` and `search_documents` use the same non-rewrite retrieval
  pipeline; `search_documents` still returns required full `matched_context`
  without `preview`.

## Non-goals

- Do not change source connectors, document identity, chunking, indexing,
  tombstone behavior, SQLite schema, Chroma schema, embedding provider, or
  embedding quality.
- Do not add a batch-job database table or a new MCP tool; per-source jobs and
  `get_sync_status` remain the status mechanism.
- Do not inspect or mutate user SQLite/Chroma data.
- Do not call live Notion, Tistory, GitHub, OpenAI, Codex, or user vaults.
- Do not modify unrelated semantic-benchmark/Codex-ranker work.

## Acceptance criteria

1. `sync_all()` returns promptly after per-source background launches and does
   not wait for connector completion.
2. Each bulk result truthfully distinguishes newly started, already-running,
   disabled/skipped, and failed launch attempts.
3. Bulk callers can poll returned source IDs with `get_sync_status`.
4. Existing `sync_source` background behavior and source-level overlap guards
   remain correct.
5. No production or public documentation/config surface references optional
   query rewrite or `CONTEXTWIKI_SEARCH_LLM_*`.
6. Search performs one deterministic retrieval path with SQLite validation,
   metadata fallback, ranking, and existing citation/document contracts.
7. Focused sync, MCP contract, search, answer, configuration, script, and E2E
   tests pass using fake or temporary data.
8. README and architecture clearly distinguish launch acceptance from sync
   completion and explain polling before search.

## Worker ownership

- One implementation worker owns the complete code/test change to avoid shared
  contract overlap across `api/tools.py`, ingestion, search, scripts, and
  tests. The worker must not modify README, architecture, or this plan.
- The main agent owns README, architecture, configuration-document integration,
  diff inspection, verification, functional smoke, review routing, and final
  local-only handoff.
- A separate read-only verification worker may audit coverage after the
  implementation worker finishes.

## Expected files

- `indexing/ingestion_service.py`
- `api/tools.py`
- `search/context_service.py`
- `search/retrieval_pipeline.py`
- `search/answer_service.py`
- `search/query_rewrite.py` (delete)
- `search/debug_redaction.py`
- `environments/config.py`
- `scripts/demo.sh`
- `scripts/demo_public_flow.py`
- `scripts/live_query_smoke.py`
- `scripts/verify_all.sh`
- focused tests under `tests/indexing`, `tests/api`, `tests/contracts`,
  `tests/search`, `tests/scripts`, `tests/environments`, `tests/e2e`, and
  retained eval tests only where imports/contracts require cleanup
- `README.md`
- `.agents/docs/architecture.md`
- configuration example documentation when safely accessible
- this plan

Changes outside this list require a plan update before editing.

## Verification plan

Focused checks:

```bash
uv run --locked pytest -q \
  tests/indexing/test_ingestion_service.py \
  tests/api/test_tools_contract.py \
  tests/contracts/test_public_mcp_contracts.py \
  tests/search/test_context_service.py \
  tests/search/test_answer_service.py \
  tests/scripts/test_live_query_smoke.py \
  tests/scripts/test_demo_public_flow.py \
  tests/environments/test_config.py \
  tests/e2e/test_contextwiki_flow.py
uv run --locked ruff check api indexing search environments scripts tests
python -m compileall api core environments fetching indexing search storage main.py
./scripts/verify_functional_e2e.sh
git diff --check
```

Run `./scripts/verify_all.sh` after focused and functional checks if the
workspace remains healthy.

## Functional smoke matrix

| Feature | Caller surface | Data mode | Expected result | Command | Result | Evidence | Blocker/substitute |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `sync_all` background launch | FastMCP/public contract | Two event-blocked fake connectors + temp SQLite | FastMCP returns two `started` jobs before connectors finish | Focused contract/E2E tests | passed | Focused suite `426 passed`; retained FastMCP E2E calls real `IngestionService.sync_all` | N/A |
| Per-source completion | MCP status flow | Fake connectors + temp SQLite | `get_sync_status` exposes running then terminal job | Functional E2E | passed | `./scripts/verify_functional_e2e.sh` -> `26 passed` | N/A |
| `sync_source` regression | FastMCP/public contract | Fake connector + temp SQLite | Existing background contract unchanged | Focused + functional E2E | passed | Focused suite and functional E2E passed | N/A |
| `search_context` without rewrite | MCP/public search | Fake retriever + temp SQLite | One deterministic retrieval path and no rewrite fields | Focused + functional E2E | passed | Focused suite and full non-live suite passed | N/A |
| `search_documents` | MCP/public search | Fake retriever + temp SQLite | Required full `matched_context`, no preview | Focused + functional E2E | passed | Focused suite and functional E2E passed | N/A |
| `fetch_context` | MCP/public fetch | Temp SQLite | Existing document/chunk hydration unchanged | Functional E2E | passed | `./scripts/verify_functional_e2e.sh` -> `26 passed` | N/A |
| Live sources/user stores | Live MCP calls | External/user data | No live call or mutation | Not run | blocked/gated | No live call | Requires explicit approval; fake/temp tests are substitute |

## Architecture constraints

- SQLite remains authoritative for job ownership, lifecycle, active documents,
  chunks, and citations.
- Chroma remains a candidate-retrieval accelerator.
- Background launch must keep strong task references and existing completion
  callbacks so failures are persisted.
- Disabled/incomplete sources must not gain unsafe stale-cleanup behavior.
- Removing query rewrite must not remove local query normalization, metadata
  fallback, ranking, or debug information unrelated to rewrite.

## Risks and rollback

- Public bulk contract break: callers expecting completion-oriented
  `sync_outcome` must switch to `launch_outcome` and poll source status.
- Process lifetime: background work only continues while the MCP process stays
  alive; SQLite orphan recovery remains the restart safety mechanism.
- Search recall may change for users who explicitly enabled rewrite; the
  feature was disabled by default and its removal also removes that external
  chat-completions egress/cost.
- Rollback restores the old awaited `sync_all` aggregation and query-rewrite
  modules/config/debug fields; no data migration or reindex is required.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Continued the existing dirty isolated feature worktree by explicit user request. | `git status`, `git branch -vv`, `git worktree list` |
| Planning | completed | Defined background bulk launch contract and complete rewrite removal boundaries. | This plan |
| Implementation | completed | Reworked bulk sync into concurrent background launches and removed the LLM rewrite module, runtime/config/debug branches, scripts, and obsolete test expectations. | Production diff plus focused tests |
| Maintained docs | completed | Updated README, maintained architecture, repository harness wording, config example, and ADR history for background `sync_all` plus deterministic retrieval without LLM rewrite. | `README.md`; `.agents/docs/architecture.md`; `AGENTS.md`; `.agents/skills/harness-plan/SKILL.md`; `.env.example`; ADR 0008 |
| Focused verification | completed | Focused contract/sync/search/config/E2E suite, retained-source Ruff scope, compile, and diff check passed. A broader ad hoc Ruff invocation including all tests found one pre-existing unused import in untouched `tests/environments/test_runtime_env.py`; the repository verification scope does not lint tests. | `426 passed`; retained Ruff passed; compile passed; `git diff --check` passed |
| Full verification | completed | Full static, public contract, non-live coverage, deterministic eval, lexical semantic baseline, and functional E2E gate passed. | `./scripts/verify_all.sh`: public contracts `12 passed`; non-live `779 passed`, 87.98% coverage; functional E2E `25 passed` |
| Functional smoke | completed | Exercised retained fake/temp MCP source-sync, bulk launch/status polling, search, document, fetch, and answer paths without live services or user stores. | Latest `./scripts/verify_functional_e2e.sh` -> `26 passed` |
| Review pass 1 | completed/actionable | Two fresh reviewers found stale removed `SECURITY.md`, an underspecified MCP `sync_all` description, and internally inconsistent historical ADR edits. | Reviewers 1 and 2 |
| Review remediation 1 | completed | Deleted `SECURITY.md`, documented immediate bulk launch plus polling in the FastMCP schema, restored historical ADR decision text, and reran API contracts and functional E2E. | API contracts `71 passed`; functional E2E `25 passed`; diff check passed |
| Review pass 2 | completed/actionable | Two fresh reviewers found one remaining coverage gap: no retained E2E connected real `IngestionService.sync_all` to FastMCP and per-source status polling. | Reviewer 1 finding; reviewer 2 clean |
| Review remediation 2 | completed | Added a two-source event-blocked FastMCP E2E proving immediate bulk return, running status, release, and terminal success through `get_sync_status`; reran focused and functional gates. | Focused `426 passed`; functional E2E `26 passed` |
| Review pass 3 | completed/clean | Exactly two fresh read-only reviewers independently reported no actionable findings after the new FastMCP bulk-sync E2E and all prior remediations. | Two clean final reviewers |
| Final config check | completed | Removed the five obsolete `CONTEXTWIKI_SEARCH_LLM_*` template entries without reading or exposing any example values, then reran configuration/verification-architecture tests and whitespace checks. | `37 passed`; `git diff --check` passed |
| Delivery boundary | completed | Local files only; no staging, commit, push, or PR update. | User instruction |
