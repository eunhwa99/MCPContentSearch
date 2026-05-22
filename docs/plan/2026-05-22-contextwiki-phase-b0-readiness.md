# ContextWiki Phase B-0 Readiness

## User Request

Implement the Phase B-0 GitHub/Web connector readiness foundation:

- Add or update ADR/plan first.
- Implement document identity lifecycle:
  - `external_id`
  - `canonical_url`
  - `last_seen_at`
  - `last_seen_sync_id`
  - `deleted_at`
- Implement source-wide stale document cleanup for documents missing from a successful full source sync.
- Implement minimum source-aware chunking:
  - Markdown: heading-based chunks.
  - Code: line-range chunks.
  - Plain text: keep the current character chunker.
- Add tests, run verification, run `$subagent-review-loop`, then commit, push, and create a PR.

## Branch Preflight Result

- Original worktree `/Users/eunhwa/IdeaProjects/MCPContentSearch` was dirty on `feature/update-branch-workflow-docs`, so no switch, pull, branch deletion, or edits were performed there.
- Ran `git fetch origin main`.
- Created isolated worktree `/private/tmp/MCPContentSearch-phase-b0` with branch `feature/contextwiki-phase-b0-readiness` from latest `origin/main`.
- `origin/main` included PR #3 at `b4829e1`.
- Local non-`main` branches are checked out in linked worktrees, so no branch cleanup was attempted.

## Scope and Non-goals

- Scope:
  - Metadata model and SQLite schema extensions for stable document identity and lifecycle.
  - Source sync handling for `last_seen_at`, `deleted_at`, unchanged document refresh, reappearing deleted documents, and source-wide stale cleanup after successful sync only.
  - Minimum source-aware chunking in `indexing/`.
  - Search/citation filtering so tombstoned documents do not produce active chunk results.
  - ADR and focused tests.
- Non-goals:
  - Do not implement real GitHub or Web connectors in this slice.
  - Do not implement worker queues, retry/backoff, fingerprint deduplication, ACLs, audit logs, reranking, query rewriting, or LLM generation.
  - Do not inspect, delete, reset, or migrate local user Chroma data.
  - Do not run live Notion/Tistory/GitHub/Web validation.

## Acceptance Criteria

- ADR records the Phase B-0 identity, tombstone, and chunking contract.
- `DocumentModel` can carry stable identity and lifecycle fields without breaking existing fetchers.
- SQLite schema is backward-compatible with existing metadata DBs by adding missing columns on `ensure_schema`.
- `external_id` controls stable `document_id` when provided, while version metadata stays separate.
- `canonical_url` is preserved and used for chunk citation URLs when available.
- Every fetched document in a successful sync receives `last_seen_at`, receives a job-scoped `last_seen_sync_id`, and clears `deleted_at`.
- Documents absent from a successful full source sync are tombstoned and their active chunk metadata/vector ids are removed from retrieval.
- Partial fetch/index/metadata failures do not tombstone missing documents.
- Reappearing tombstoned documents are reindexed even if content hash is unchanged.
- Markdown ATX and setext heading chunks preserve heading-scoped line ranges.
- Code chunks preserve file path and line ranges; function/class-aware chunking can wait.
- Plain text documents keep the current character chunking behavior.
- Tests cover identity lifecycle, stale cleanup, partial failure safety, and chunking strategies.

## Step Breakdown

| Step | Label | Boundary | Acceptance Criteria |
| --- | --- | --- | --- |
| 1 | `adr-contract` | Add ADR 0003 and index it. | Persistence/chunking contract is accepted and aligned with ADR 0001/0002. |
| 2 | `red-tests` | Add focused failing tests first. | Tests fail for missing lifecycle/chunking behavior. |
| 3 | `metadata-lifecycle` | Update `core.models` and `storage.metadata_store`. | Identity/lifecycle fields persist, tombstone helpers exist, active chunks exclude deleted docs. |
| 4 | `source-aware-chunking` | Update `indexing.chunker` and converter metadata. | Markdown/code/plain text strategies produce expected chunks. |
| 5 | `ingestion-cleanup` | Update `indexing.ingestion_service`. | Successful full sync refreshes seen docs and tombstones missing docs; failures do not cleanup. |
| 6 | `verification` | Run focused and broad checks. | Focused tests, compile check, and repo verification pass. |
| 7 | `review-and-pr` | Run fresh five-reviewer `$subagent-review-loop`, commit, push, PR. | All five reviewers in newest pass report no actionable findings; PR URL is reported. |

## Files Likely to Change

- `.agents/docs/adr/README.md`
- `.agents/docs/adr/0003-contextwiki-phase-b0-identity-and-chunking.md`
- `core/models.py`
- `storage/metadata_store.py`
- `indexing/chunker.py`
- `indexing/converter.py`
- `indexing/ingestion_service.py`
- `main.py`
- `search/answer_service.py`
- `search/service.py`
- `api/tools.py`
- `fetching/connectors.py`
- `fetching/notion.py`
- `tests/api/test_tools_contract.py`
- `tests/fetching/test_notion.py`
- `tests/storage/test_metadata_store.py`
- `tests/indexing/test_chunker.py`
- `tests/indexing/test_ingestion_service.py`
- `tests/search/test_answer_service.py`
- `tests/search/test_context_service.py`
- `tests/search/test_service.py`
- `docs/plan/2026-05-22-contextwiki-phase-b0-readiness.md`

## Test and Verification Plan

Red/green focused tests:

```bash
uv run pytest tests/api/test_tools_contract.py tests/fetching/test_notion.py tests/storage/test_metadata_store.py tests/indexing/test_chunker.py tests/indexing/test_ingestion_service.py tests/search/test_answer_service.py tests/search/test_context_service.py tests/search/test_service.py
```

Fallback if `uv` is unavailable:

```bash
python -m pytest tests/api/test_tools_contract.py tests/fetching/test_notion.py tests/storage/test_metadata_store.py tests/indexing/test_chunker.py tests/indexing/test_ingestion_service.py tests/search/test_answer_service.py tests/search/test_context_service.py tests/search/test_service.py
```

Broader deterministic verification:

```bash
python -m compileall api core environments fetching indexing search main.py
uv run pytest
```

If `uv run ...` fails because the local workspace is not healthy, report it and run the closest dependency-free or direct Python fallback. Live external tests are out of scope.

## Architecture/ADR Constraints

- ADR 0001 keeps connectors in `fetching/`, chunking/sync/vector writes in `indexing/`, retrieval in `search/`, and shared contracts in `core/`.
- ADR 0002 keeps Chroma responsible for vector retrieval and SQLite responsible for operational source/job/document/chunk metadata.
- This slice changes metadata persistence contracts, so it adds ADR 0003 before runtime implementation.
- Chroma deletion must be limited to managed chunk ids returned by metadata cleanup; no raw local Chroma inspection or reset is allowed.
- Existing MCP tool response shapes remain stable.

## Risks and Rollback Notes

- Risk: Tombstoning after partial source failure could hide valid documents.
  - Mitigation: cleanup only for connectors that opt into complete snapshots; Notion full-sync block fetch failures are surfaced as sync failures, and Tistory remains cleanup-disabled.
- Risk: Vector cleanup failures could leave stale managed hits in Chroma.
  - Mitigation: SQLite is the active retrieval gate; structured and legacy search filter managed chunk hits through SQLite, legacy search expands past stale managed hits, and vector deletion is best-effort after metadata commits.
- Risk: Sync finalization failure after stale cleanup could hide valid documents.
  - Mitigation: successful sync finalization atomically commits optional tombstones, source status, and job status before best-effort vector cleanup.
- Risk: timestamp-only stale cleanup could miss deleted documents when two full syncs share the same `last_seen_at`.
  - Mitigation: cleanup uses a job-scoped `last_seen_sync_id` marker while preserving `last_seen_at` as observation timestamp metadata.
- Risk: overlapping same-source syncs could use competing lifecycle markers.
  - Mitigation: sync start is guarded by SQLite with a write transaction and returns the existing running job instead of starting a second full sync.
- Risk: a killed process could leave a source permanently stuck behind a stale `RUNNING` job.
  - Mitigation: running jobs use an internal heartbeat and conservative timeout; stale running jobs are failed before a replacement sync starts, and finalization ignores jobs that are no longer active.
- Risk: a superseded sync could resume after an awaited vector write and publish stale metadata.
  - Mitigation: document/chunk metadata commits refresh and verify the owning job in the same SQLite transaction; inactive jobs return without writing active chunks or deleting old vectors.
- Risk: unguarded metadata-store APIs or cross-source connector output could bypass source lifecycle isolation.
  - Mitigation: `update_sync_job` cannot start or finish jobs outside guarded lifecycle APIs; guarded commit/finalization validates the active source/job boundary; ingestion normalizes fetched docs to the registry source id; cross-source document-id collisions are reserved/rejected before vector writes or metadata overwrite; stale claims from expired jobs are recoverable; active chunk reads and stale cleanup require source equality with the owning document.
- Risk: Source-aware chunking could change chunk ids for unchanged documents.
  - Mitigation: unchanged-content sync compares generated chunk ids with stored chunk ids and reindexes when the chunk set changes.
- Risk: Existing SQLite files may lack new columns.
  - Mitigation: `ensure_schema` adds missing columns without dropping tables or data.
- Risk: Deleted chunk vectors may remain if the indexer lacks delete support.
  - Mitigation: metadata retrieval excludes tombstoned documents, and vector deletion is attempted only when `delete_documents_by_ids` exists.
- Risk: Source-aware chunking could alter existing plain text behavior.
  - Mitigation: keep current character chunker for plain text and add tests.
- Rollback: revert this branch; no local user Chroma data is reset or migrated.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created fresh isolated branch from latest `origin/main`. | `git fetch origin main`; `git worktree add -b feature/contextwiki-phase-b0-readiness /private/tmp/MCPContentSearch-phase-b0 origin/main` |
| Planning | completed | Read architecture, ADR 0001/0002, metadata store, chunker, ingestion service, and current tests. | `.agents/docs/architecture.md`; ADR 0001/0002; `storage/metadata_store.py`; `indexing/chunker.py`; `indexing/ingestion_service.py`; tests |
| ADR contract | completed | Added ADR 0003 for identity lifecycle, tombstones, and source-aware chunking. | `.agents/docs/adr/0003-contextwiki-phase-b0-identity-and-chunking.md` |
| Red tests | completed | Added focused tests and confirmed expected failures for missing lifecycle/chunking behavior. | `python -m pytest tests/storage/test_metadata_store.py tests/indexing/test_chunker.py tests/indexing/test_ingestion_service.py tests/search/test_context_service.py` -> 7 failed, 17 passed |
| Implementation | completed | Implemented metadata lifecycle, source-aware chunking, and successful-sync stale cleanup. | `core/models.py`; `storage/metadata_store.py`; `indexing/chunker.py`; `indexing/converter.py`; `indexing/ingestion_service.py` |
| Focused verification | completed | Focused lifecycle/chunking/search tests passed. | `python -m pytest tests/storage/test_metadata_store.py tests/indexing/test_chunker.py tests/indexing/test_ingestion_service.py tests/search/test_context_service.py` -> 24 passed |
| Integration verification | completed | Compile, full tests, and repo verification script passed. `uv` pytest was unavailable in this environment and the script used its Python fallback. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 32 passed; `scripts/verify_all.sh` -> 32 passed |
| Review remediation | completed | Fixed cleanup opt-in for partial connectors, metadata-first best-effort vector cleanup, unchanged-content citation metadata refresh, external-id precedence, tombstoned `fetch_context`, and leading/trailing blank line ranges. | `python -m pytest tests/api/test_tools_contract.py tests/indexing/test_chunker.py tests/indexing/test_ingestion_service.py` -> 25 passed |
| Post-remediation verification | completed | Re-ran deterministic verification after all review fixes and plan updates. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 39 passed; `scripts/verify_all.sh` -> 39 passed; `git diff --check` |
| Fresh review pass 1 | completed | Five fresh reviewers found actionable gaps around rechunk reindexing, Notion partial-content failures, metadata-before-vector ordering, answer line citations, fenced Markdown headings, and legacy managed tombstone filtering. | Reviewers `019e4e25-11c8`, `019e4e25-dd85`, `019e4e25-dd37`, `019e4e25-dde6`, `019e4e25-de31` |
| Review pass 1 remediation | completed | Added regressions and fixed rechunk reindexing, post-commit best-effort stale vector cleanup, strict Notion full-sync block fetches, answer citation line metadata, fenced Markdown headings, and legacy search filtering for inactive managed chunks. | `python -m pytest tests/indexing/test_ingestion_service.py tests/indexing/test_chunker.py tests/search/test_answer_service.py tests/fetching/test_notion.py tests/search/test_service.py` -> 28 passed |
| Post-review-remediation verification | completed | Re-ran deterministic verification after review pass 1 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 44 passed; `scripts/verify_all.sh` -> 44 passed; `git diff --check` |
| Fresh review pass 2 | completed | Five fresh reviewers found actionable gaps around exact Markdown fence marker tracking, legacy search expansion/SQLite hydration, orphan chunk filtering, and atomic successful-sync finalization. One reviewer had no actionable findings. | Reviewers `019e4e31-9475`, `019e4e31-94cb`, `019e4e31-9665`, `019e4e31-96a7`, `019e4e31-972c` |
| Review pass 2 remediation | completed | Added regressions and fixed marker-aware fenced Markdown handling, legacy search stale-window expansion and SQLite hydration, orphan chunk inactivity, and atomic sync success finalization with rollback before vector cleanup. | `python -m pytest tests/indexing/test_chunker.py tests/search/test_service.py tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py` -> 34 passed |
| Post-review-pass-2 verification | completed | Re-ran deterministic verification after review pass 2 fixes and context-service seed updates for the orphan-chunk contract. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 49 passed; `scripts/verify_all.sh` -> 49 passed; `git diff --check` |
| Fresh review pass 3 | completed | Five fresh reviewers found actionable gaps around stricter CommonMark fence close handling and legacy managed platform/date hydration. Three reviewers had no actionable findings. | Reviewers `019e4e3d-7b27`, `019e4e3d-7b82`, `019e4e3d-7bd4`, `019e4e3d-7c3f`, `019e4e3d-7c8b` |
| Review pass 3 remediation | completed | Added regressions and fixed CommonMark-aware fence close/indent handling plus legacy managed search platform/date hydration from SQLite chunk metadata. | `python -m pytest tests/indexing/test_chunker.py tests/search/test_service.py` -> 13 passed |
| Post-review-pass-3 verification | completed | Re-ran deterministic verification after review pass 3 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 50 passed; `scripts/verify_all.sh` -> 50 passed; `git diff --check` |
| Fresh review pass 4 | completed | Five fresh reviewers found actionable gaps around stricter ATX heading detection and direct legacy-schema migration coverage. Three reviewers had no actionable findings. | Reviewers `019e4e46-59a8`, `019e4e46-5a14`, `019e4e46-5a5f`, `019e4e46-5b4e`, `019e4e46-5acf` |
| Review pass 4 remediation | completed | Added regressions and fixed CommonMark-like ATX heading detection; added direct legacy `documents` table migration coverage for additive lifecycle columns. | `python -m pytest tests/indexing/test_chunker.py tests/storage/test_metadata_store.py` -> 17 passed |
| Post-review-pass-4 verification | completed | Re-ran deterministic verification after review pass 4 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 52 passed; `scripts/verify_all.sh` -> 52 passed; `git diff --check` |
| Fresh review pass 5 | completed | Five fresh reviewers found actionable gaps around invalid backtick fence openers, source-wide cleanup scalability, and stale managed vector windows beyond the previous expansion cap. Two reviewers had no actionable findings. | Reviewers `019e4e4f-24d5`, `019e4e4f-254f`, `019e4e4f-25a1`, `019e4e4f-2605`, `019e4e4f-2665` |
| Review pass 5 remediation | completed | Added regressions and fixed invalid backtick fence opener handling, marker-based stale cleanup using `last_seen_at` instead of seen-id `NOT IN` lists, and dynamic search expansion bounded by collection count or fallback cap. | `python -m pytest tests/indexing/test_chunker.py tests/indexing/test_ingestion_service.py tests/search/test_service.py tests/search/test_context_service.py` -> 39 passed |
| Post-review-pass-5 verification | completed | Re-ran deterministic verification after review pass 5 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 55 passed; `scripts/verify_all.sh` -> 55 passed; `git diff --check` |
| Fresh review pass 6 | completed | Five fresh reviewers found an actionable same-source overlapping sync race around marker-based stale cleanup. Four reviewers had no actionable findings. | Reviewers `019e4e5a-98ce`, `019e4e5a-9926`, `019e4e5a-9972`, `019e4e5a-99dc`, `019e4e5a-9a34` |
| Review pass 6 remediation | completed | Added a DB-backed per-source start guard so overlapping sync requests return the existing running job instead of starting a second fetch/finalization path. | `python -m pytest tests/indexing/test_ingestion_service.py` -> 19 passed |
| Post-review-pass-6 verification | completed | Re-ran deterministic verification after review pass 6 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 56 passed; `scripts/verify_all.sh` -> 56 passed; `git diff --check` |
| Fresh review pass 7 | completed | Five fresh reviewers found actionable DB-level sync start race and old seen-id cleanup helper exposure. Two reviewers had no actionable findings. | Reviewers `019e4e63-a1fa`, `019e4e63-a24f`, `019e4e63-a2b1`, `019e4e63-a36b`, `019e4e63-a3ec` |
| Review pass 7 remediation | completed | Added `BEGIN IMMEDIATE` sync-start locking, multi-connection running-job coverage, removed old seen-id cleanup helper path, and moved tombstone tests to marker-based finalization. | `python -m pytest tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py tests/search/test_context_service.py` -> 32 passed |
| Post-review-pass-7 verification | completed | Re-ran deterministic verification after review pass 7 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 57 passed; `scripts/verify_all.sh` -> 57 passed; `git diff --check` |
| Fresh review pass 8 | completed | Five fresh reviewers found an actionable race where source re-registration could overwrite `RUNNING` status before the guarded sync start. Four reviewers had no actionable findings. | Reviewers `019e4e70-63c9`, `019e4e70-6434`, `019e4e70-6496`, `019e4e70-6500`, `019e4e70-6570` |
| Review pass 8 remediation | completed | Changed static source registration to update only static fields on existing rows, preserving operational source status in the database, and added stale-read regression coverage. | `python -m pytest tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py` -> 27 passed |
| Post-review-pass-8 verification | completed | Re-ran deterministic verification after review pass 8 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 58 passed; `scripts/verify_all.sh` -> 58 passed; `git diff --check` |
| Fresh review pass 9 | completed | Five fresh reviewers found actionable gaps around disabled-source lock bypass, running-job detection when source status is stale, and crash recovery for stale `RUNNING` jobs. Two reviewers had no actionable findings. | Reviewers `019e4e79-203c`, `019e4e79-2094`, `019e4e79-210d`, `019e4e79-217b`, `019e4e79-2202` |
| Review pass 9 remediation | completed | Moved disabled-source handling behind the DB sync-start guard, made running-job detection check `sync_jobs` directly, added heartbeat timeout recovery for stale running jobs, and prevented inactive jobs from later finalizing cleanup. | `python -m pytest tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py` -> 31 passed |
| Post-review-pass-9 verification | completed | Re-ran deterministic verification after review pass 9 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 62 passed; `scripts/verify_all.sh` -> 62 passed; `git diff --check` |
| Fresh review pass 10 | completed | Five fresh reviewers found actionable gaps around duplicate stale/active running rows and superseded jobs committing metadata after awaited vector writes. One reviewer had no actionable findings. | Reviewers `019e4e86-9674`, `019e4e87-32d1`, `019e4e87-332b`, `019e4e87-3396`, `019e4e87-33e4` |
| Review pass 10 remediation | completed | Changed sync-start recovery to inspect all running rows, fail stale duplicates, keep only one active running job, and commit document/chunk metadata only if the owning job is still running inside the SQLite transaction. | `python -m pytest tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py` -> 34 passed |
| Post-review-pass-10 verification | completed | Re-ran deterministic verification after review pass 10 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 65 passed; `scripts/verify_all.sh` -> 65 passed; `git diff --check` |
| Fresh review pass 11 | completed | Five fresh reviewers found actionable gaps around setext Markdown heading support, cross-source guarded commits, managed-hit fallback without SQLite, and unguarded metadata-store RUNNING/finalization APIs. Two reviewers had no actionable findings. | Reviewers `019e4e92-436e`, `019e4e92-43c6`, `019e4e92-4458`, `019e4e92-44bf`, `019e4e92-4541` |
| Review pass 11 remediation | completed | Added setext heading chunking, made managed vector hits require SQLite hydration, forced ingestion source ids to registry source ids, rejected cross-source guarded commits, blocked unguarded `RUNNING` transitions, and required successful finalization to own the active running job. | `python -m pytest tests/indexing/test_chunker.py tests/search/test_service.py tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py tests/search/test_context_service.py` -> 61 passed |
| Post-review-pass-11 verification | completed | Re-ran deterministic verification after review pass 11 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 71 passed; `scripts/verify_all.sh` -> 71 passed; `git diff --check` |
| Fresh review pass 12 | completed | Five fresh reviewers found actionable gaps around cross-source document-id collisions and multi-line setext heading chunking. Three reviewers had no actionable findings. | Reviewers `019e4e9e-f207`, `019e4e9e-f29c`, `019e4e9e-f30a`, `019e4e9e-f377`, `019e4e9e-f3e5` |
| Review pass 12 remediation | completed | Added cross-source document-id collision rejection, source-scoped chunk replacement deletes, ingestion collision regression coverage, and multi-line setext heading chunking coverage. | `python -m pytest tests/indexing/test_chunker.py tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py` -> 54 passed |
| Post-review-pass-12 verification | completed | Re-ran deterministic verification after review pass 12 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 74 passed; `scripts/verify_all.sh` -> 74 passed; `git diff --check` |
| Fresh review pass 13 | completed | Five fresh reviewers found actionable gaps around setext paragraph boundaries, source-mismatched active chunk hydration, pre-vector collision rejection, failed-sync source mismatch, and self-expired job source reconciliation. | Reviewers `019e4ea9-859c`, `019e4ea9-85f1`, `019e4ea9-8676`, `019e4ea9-871f`, `019e4ea9-879c` |
| Review pass 13 remediation | completed | Restricted setext paragraph backtracking, added pre-vector document collision validation, tightened active chunk joins with source equality, validated legacy chunk replacement sources, rejected failed-sync source mismatches, and reconciled self-expired jobs to failed source status. | `python -m pytest tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py` -> 44 passed |
| Post-review-pass-13 verification | completed | Re-ran deterministic verification after review pass 13 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 78 passed; `scripts/verify_all.sh` -> 78 passed; `git diff --check` |
| Fresh review pass 14 | completed | Five fresh reviewers found actionable gaps around stale cleanup source scoping, unguarded terminal job updates, long-fetch self-expiry, setext block boundaries, and concurrent pre-vector collision reservation. One reviewer had no actionable findings. | Reviewers `019e4eb3-8192`, `019e4eb3-813f`, `019e4eb3-820c`, `019e4eb3-82a1`, `019e4eb3-8302` |
| Review pass 14 remediation | completed | Added SQLite document claims before vector writes, made heartbeat refresh resolve stale leases, blocked unguarded terminal job updates, source-scoped stale cleanup, tightened setext backtracking, and added focused regressions for concurrent collisions and self-expired fetches. | `python -m pytest tests/indexing/test_chunker.py tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py` -> 64 passed |
| Post-review-pass-14 verification | completed | Re-ran deterministic verification after review pass 14 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 84 passed; `scripts/verify_all.sh` -> 84 passed; `git diff --check` |
| Fresh review pass 15 | completed | Five fresh reviewers found one actionable consecutive-setext-section boundary gap. Four reviewers had no actionable findings. | Reviewers `019e4ebf-b825`, `019e4ebf-b8e4`, `019e4ebf-bc7b`, `019e4ebf-b959`, `019e4ebf-bb33` |
| Review pass 15 remediation | completed | Added consecutive setext section regression coverage and prevented prior setext underlines from being treated as heading text for the next setext heading. | `python -m pytest tests/indexing/test_chunker.py` -> 17 passed |
| Post-review-pass-15 verification | completed | Re-ran deterministic verification after review pass 15 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 85 passed; `scripts/verify_all.sh` -> 85 passed; `git diff --check` |
| Fresh review pass 16 | completed | Five fresh reviewers found actionable gaps around stale document claim recovery, sync-status active job reporting, and combined multiline/consecutive setext boundaries. Two reviewers had no actionable findings. | Reviewers `019e4ec9-e2aa`, `019e4ec9-e0f8`, `019e4ec9-e354`, `019e4ec9-e3ef`, `019e4ec9-e481` |
| Review pass 16 remediation | completed | Reworked setext paragraph boundary handling, added stale cross-source claim recovery, and made latest job reporting prefer active running jobs while the source is running. | `python -m pytest tests/indexing/test_chunker.py tests/storage/test_metadata_store.py` -> 44 passed |
| Post-review-pass-16 verification | completed | Re-ran deterministic verification after review pass 16 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 88 passed; `scripts/verify_all.sh` -> 88 passed; `git diff --check` |
| Fresh review pass 17 | completed | Five fresh reviewers found actionable gaps around no-blank multiline setext headings, code blank-line range coverage, source-aware vector dedupe, and unguarded chunk/document metadata validation. One reviewer had no actionable findings. | Reviewers `019e4ed6-93b6`, `019e4ed6-941c`, `019e4ed6-94ec`, `019e4ed6-9612`, `019e4ed6-968c` |
| Review pass 17 remediation | completed | Standardized setext handling around paragraph boundaries, preserved code blank lines in chunk line ranges, keyed existing vector docs by source id, and reused chunk ownership validation for public metadata helpers. | `python -m pytest tests/indexing/test_chunker.py tests/indexing/test_index_manager.py tests/storage/test_metadata_store.py` -> 50 passed |
| Post-review-pass-17 verification | completed | Re-ran deterministic verification after review pass 17 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 94 passed; `scripts/verify_all.sh` -> 94 passed; `git diff --check` |
| Fresh review pass 18 | completed | Five fresh reviewers found actionable gaps around source-scoped managed vector cleanup, stale status recovery, oversized Markdown sections, and untracked new artifacts. One reviewer had no actionable findings. | Reviewers `019e4ee4-74c7`, `019e4ee4-f7c9`, `019e4ee4-f81f`, `019e4ee4-f888`, `019e4ee4-f915` |
| Review pass 18 remediation | completed | Scoped vector deletes to managed rows for the source, made status reads recover stale running jobs, split oversized Markdown sections under `max_chars`, and added new artifacts to the diff-check surface with intent-to-add. | `python -m pytest tests/indexing/test_chunker.py tests/indexing/test_index_manager.py tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py` -> 77 passed |
| Post-review-pass-18 verification | completed | Re-ran deterministic verification after review pass 18 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 97 passed; `scripts/verify_all.sh` -> 97 passed; `git diff --check`; `git ls-files --others --exclude-standard` -> none |
| Fresh review pass 19 | completed | Five fresh reviewers found one actionable oversized single-line code chunk gap. Four reviewers had no actionable findings. | Reviewers `019e4ef0-e9d9`, `019e4ef0-eab2`, `019e4ef0-ea40`, `019e4ef0-eb3f`, `019e4ef0-ebb5` |
| Review pass 19 remediation | completed | Split oversized single code lines into budget-sized chunks while preserving same-line citation ranges. | `python -m pytest tests/indexing/test_chunker.py` -> 22 passed |
| Post-review-pass-19 verification | completed | Re-ran deterministic verification after review pass 19 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 98 passed; `scripts/verify_all.sh` -> 98 passed; `git diff --check`; `git ls-files --others --exclude-standard` -> none |
| Fresh review pass 20 | completed | Five fresh reviewers found actionable gaps around managed/legacy vector collisions, timestamp-only stale cleanup markers, and stale source status in `get_sync_status`. One reviewer had no actionable findings. | Reviewers `019e4efa-557e`, `019e4efa-55ef`, `019e4efa-5667`, `019e4efa-56c3`, `019e4efa-5735` |
| Review pass 20 remediation | completed | Added job-scoped `last_seen_sync_id` cleanup markers, separated managed and legacy vector dedupe/delete keys, and re-read source status after sync-status recovery. | `python -m pytest tests/indexing/test_index_manager.py tests/api/test_tools_contract.py tests/indexing/test_ingestion_service.py tests/storage/test_metadata_store.py` -> 63 passed |
| Post-review-pass-20 verification | completed | Re-ran deterministic verification after review pass 20 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 101 passed; `scripts/verify_all.sh` -> 101 passed; `git diff --check`; `git ls-files --others --exclude-standard` -> none |
| Fresh review pass 21 | completed | Five fresh reviewers found actionable gaps around raw-vector deletes crossing into managed vectors, post-vector metadata failure cleanup, and managed-hit ownership verification. One reviewer had no actionable findings. | Reviewers `019e4f07-cbc5`, `019e4f07-cc2a`, `019e4f07-cc9e`, `019e4f07-cd08`, `019e4f07-cd98` |
| Review pass 21 remediation | completed | Scoped raw vector deletes away from managed rows, cleaned uncommitted vectors on inactive metadata commits or metadata exceptions, and rejected managed vector hits whose source/document metadata disagree with SQLite chunks. | `python -m pytest tests/indexing/test_index_manager.py tests/indexing/test_ingestion_service.py tests/search/test_service.py tests/search/test_context_service.py` -> 41 passed |
| Post-review-pass-21 verification | completed | Re-ran deterministic verification after review pass 21 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 103 passed; `scripts/verify_all.sh` -> 103 passed; `git diff --check`; `git ls-files --others --exclude-standard` -> none |
| Fresh review pass 22 | completed | Five fresh reviewers found actionable gaps around no-source raw deletes, required managed owner metadata, and duplicate managed-hit search suppression. Two reviewers had no actionable findings. | Reviewers `019e4f12-b492`, `019e4f12-b511`, `019e4f12-b58a`, `019e4f12-b60f`, `019e4f12-b68a` |
| Review pass 22 remediation | completed | Scoped no-source raw deletes to raw vectors, required managed vector hits to carry matching source/document metadata, and moved structured-search duplicate tracking after hydration/ownership validation. | `python -m pytest tests/indexing/test_index_manager.py tests/search/test_service.py tests/search/test_context_service.py` -> 21 passed |
| Post-review-pass-22 verification | completed | Re-ran deterministic verification after review pass 22 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 108 passed; `scripts/verify_all.sh` -> 108 passed; `git diff --check`; `git ls-files --others --exclude-standard` -> none |
| Fresh review pass 23 | completed | Five fresh reviewers found actionable gaps around marker-absent raw vector cleanup, public upsert atomicity, and post-retrieval managed marker validation. Two reviewers had no actionable findings. | Reviewers `019e4f1f-4a80`, `019e4f1f-4b01`, `019e4f1f-4c06`, `019e4f1f-4b88`, `019e4f1f-4cea` |
| Review pass 23 remediation | completed | Treated missing `contextwiki_managed` as raw for cleanup while excluding managed rows, locked public metadata upserts with source-conflict guards, required managed hits to keep marker/source/document metadata, and blocked raw fallback for active managed chunk ids. | `python -m pytest tests/indexing/test_index_manager.py tests/search/test_service.py tests/search/test_context_service.py tests/storage/test_metadata_store.py` -> 52 passed |
| Post-review-pass-23 verification | completed | Re-ran deterministic verification after review pass 23 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 110 passed; `scripts/verify_all.sh` -> 110 passed; `git diff --check`; `git ls-files --others --exclude-standard` -> none |
| Fresh review pass 24 | completed | Five fresh reviewers found one actionable legacy raw fallback gap for tombstoned ContextWiki document ids. Three reviewers had no actionable findings. | Reviewers `019e4f2c-079f`, `019e4f2c-0743`, `019e4f2c-0833`, `019e4f2c-08ea`, `019e4f2c-094c` |
| Review pass 24 remediation | completed | Blocked legacy raw fallback for any vector hit that maps to a known SQLite document or active chunk, including tombstoned ContextWiki documents with markerless raw vectors. | `python -m pytest tests/search/test_service.py` -> 8 passed |
| Post-review-pass-24 verification | completed | Re-ran deterministic verification after review pass 24 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 111 passed; `scripts/verify_all.sh` -> 111 passed; `git diff --check`; `git ls-files --others --exclude-standard` -> none |
| Fresh review pass 25 | completed | Five fresh reviewers found actionable gaps around stale-job cleanup deleting replacement active vectors and public chunk replacement deleting source-mismatched inactive rows. Three reviewers had no actionable findings. | Reviewers `019e4f35-76a1`, `019e4f35-7709`, `019e4f35-7766`, `019e4f35-77d0`, `019e4f35-7847` |
| Review pass 25 remediation | completed | Skipped best-effort vector cleanup for chunk ids that are currently active in SQLite and scoped public chunk replacement deletes by source. | `python -m pytest tests/indexing/test_ingestion_service.py tests/storage/test_metadata_store.py` -> 56 passed |
| Post-review-pass-25 verification | completed | Re-ran deterministic verification after review pass 25 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 113 passed; `scripts/verify_all.sh` -> 113 passed; `git diff --check`; `git ls-files --others --exclude-standard` -> none |
| Fresh review pass 26 | completed | Five fresh reviewers found one actionable legacy raw fallback gap for markerless vectors whose `doc_id` is a ContextWiki chunk id. Four reviewers had no actionable findings. | Reviewers `019e4f43-1d9c`, `019e4f43-e379`, `019e4f43-e3e9`, `019e4f43-e442`, `019e4f43-e49e` |
| Review pass 26 remediation | completed | Added regressions for markerless vectors carrying active or tombstoned chunk ids in `doc_id`, made legacy fallback check chunk id candidates across `chunk_id`/`doc_id`/`document_id`, and added a chunk-record existence probe for tombstoned rows. | `python -m pytest tests/search/test_service.py -q` -> 10 passed |
| Post-review-pass-26 verification | completed | Re-ran deterministic verification after review pass 26 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 115 passed; `scripts/verify_all.sh` -> 115 passed; `git diff --check`; `git ls-files --others --exclude-standard` -> none |
| Fresh review pass 27 | completed | Five fresh reviewers found one actionable tombstoned markerless chunk leak after real stale cleanup deletes chunk rows. One reviewer had no actionable findings. | Reviewers `019e4f4a-94ae`, `019e4f4a-950b`, `019e4f4a-954a`, `019e4f4a-95cf`, `019e4f4a-9674` |
| Review pass 27 remediation | completed | Preserved chunk rows behind tombstoned documents so SQLite keeps stale chunk-id provenance while active chunk reads stay hidden by deleted-document joins; added real MetadataStore cleanup regression for markerless `doc_id` chunk ids. | `python -m pytest tests/search/test_service.py tests/storage/test_metadata_store.py -q` -> 41 passed; `python -m pytest tests/indexing/test_ingestion_service.py -q` -> 26 passed |
| Post-review-pass-27 verification | completed | Re-ran deterministic verification after review pass 27 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 116 passed; `scripts/verify_all.sh` -> 116 passed; `git diff --check`; `git ls-files --others --exclude-standard` -> none |
| Fresh review pass 28 | completed | Five fresh reviewers found one actionable connector identity gap where `external_id` was added to the model but not populated by real Notion/Tistory connectors. Four reviewers had no actionable findings. | Reviewers `019e4f51-3351`, `019e4f51-33b4`, `019e4f51-346f`, `019e4f51-34d6`, `019e4f51-35a2` |
| Review pass 28 remediation | completed | Populated native `external_id`/`document_id`/canonical URL fields in Notion and Tistory fetch paths, preserved the values through source connector normalization, and added SQLite persistence regressions for both connectors. | `python -m pytest tests/fetching/test_notion.py tests/fetching/test_tistory.py tests/fetching/test_connectors.py -q` -> 5 passed; `python -m pytest tests/search/test_service.py tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py -q` -> 67 passed |
| Post-review-pass-28 verification | completed | Re-ran deterministic verification after review pass 28 fixes and added new tests to the diff-check surface. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 120 passed; `scripts/verify_all.sh` -> 120 passed; `git diff --check`; `git ls-files --others --exclude-standard` -> none |
| Fresh review pass 29 | completed | Five fresh reviewers found one actionable legacy raw vector gap where pre-B0 Notion/Tistory ids could bypass native external-id matching. Four reviewers had no actionable findings. | Reviewers `019e4f58-f3b2`, `019e4f58-f363`, `019e4f58-f543`, `019e4f58-f5b7`, `019e4f58-f662` |
| Review pass 29 remediation | completed | Added canonical URL lookup and source-specific legacy id aliases for raw fallback suppression, with regressions for old `notion_...`, old `tistory_...`, and canonical-URL-only raw hits. | `python -m pytest tests/search/test_service.py -q` -> 14 passed; `python -m pytest tests/fetching/test_notion.py tests/fetching/test_tistory.py tests/fetching/test_connectors.py tests/storage/test_metadata_store.py -q` -> 35 passed |
| Post-review-pass-29 verification | completed | Re-ran deterministic verification after review pass 29 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 123 passed; `scripts/verify_all.sh` -> 123 passed; `git diff --check`; `git ls-files --others --exclude-standard` -> none |
| Fresh review pass 30 | completed | Five fresh reviewers found actionable gaps around dual URL metadata fallback and old chunk-id provenance after tombstone/reappearance. Three reviewers had no actionable findings. | Reviewers `019e4f60-2da7`, `019e4f60-2d45`, `019e4f60-2e3a`, `019e4f60-2ef0`, `019e4f60-2f78` |
| Review pass 30 remediation | completed | Checked both `canonical_url` and `url` independently for raw fallback suppression, added historical `chunk_tombstones` provenance for replaced chunk ids, and covered reappearing tombstoned documents with stale markerless chunk ids. | `python -m pytest tests/search/test_service.py -q` -> 14 passed; `python -m pytest tests/indexing/test_ingestion_service.py tests/storage/test_metadata_store.py -q` -> 57 passed |
| Post-review-pass-30 verification | completed | Re-ran deterministic verification after review pass 30 fixes. | `python -m compileall api core environments fetching indexing search main.py`; `python -m pytest` -> 124 passed; `scripts/verify_all.sh` -> 124 passed; `git diff --check`; `git ls-files --others --exclude-standard` -> none |
| Fresh review pass 31 | completed | Five fresh reviewers reported no actionable findings after review pass 30 remediation. | Reviewers `019e4f67-da70`, `019e4f67-d9ea`, `019e4f67-daf1`, `019e4f67-dc30`, `019e4f67-db79` |
