# ContextWiki Product Roadmap

## User Request

Define the full ContextWiki plan, not only the first MVP slice. ContextWiki is an MCP-based knowledge backend that searches private documents, code, Notion, Tistory, and web/docs content, then answers with citations. The system should read as a production-grade AI knowledge system built by a backend engineer, not a generic RAG chatbot.

## Branch Preflight Result

- Repository: `/Users/eunhwa/IdeaProjects/MCPContentSearch`
- Latest main checked with `git fetch origin main`.
- Latest remote main: `origin/main` at `f9b3157`.
- Work started from: `origin/main`.
- Working branch: `feature/contextwiki-mvp-planning`.
- Worktree state before writing this plan: clean.
- No runtime code has been changed for this roadmap.
- 2026-05-22 update: reviewed from isolated branch `feature/review-roadmap-hij` in `/private/tmp/MCPContentSearch-roadmap-hij` because the primary worktree had unrelated docs changes. This update is docs-only.

## Current Capability Check

| Capability | Status | Notes |
| --- | --- | --- |
| FastMCP knowledge server | checked | `main.py` creates `FastMCP("content-search-server")`. |
| Notion connector | checked | Existing Notion fetch/search implementation exists under `fetching/notion.py`. |
| Tistory connector | checked | Existing Tistory crawl/search implementation exists under `fetching/tistory.py`. |
| Chroma vector index | checked | `environments/config.py` configures persistent Chroma. |
| LlamaIndex search | checked | `search/service.py` retrieves through LlamaIndex. |
| Hybrid search mode | checked | Existing retriever uses `vector_store_query_mode="hybrid"`. |
| Local-first fallback search | checked | `DynamicSearchService` falls back from local search to web search. |
| Full indexing trigger | checked | Existing MCP tool can start background full indexing. |
| Basic index status | checked | Existing `IndexStatusModel` reports one global indexing state. |
| Document content hash detection | checked | Existing metadata and indexing paths compare content hashes for changed-document decisions. |
| Source/job/status persistence | checked | MVP A added SQLite source and sync job metadata. |
| Chunk metadata persistence | checked | MVP A added citation-ready chunk metadata and chunk ids. |
| Source-aware chunking | missing | Needed before GitHub connector work can produce useful code citations. |
| Document identity lifecycle | partial | Current IDs/content hashes cover updates, but connector-heavy sync needs `external_id`, `canonical_url`, `last_seen_at`, and `deleted_at`. |
| Source-wide stale document cleanup | partial | Changed-document stale chunks are handled in MVP A, but disappeared documents from a successful source sync still need tombstone/cleanup handling. |
| Citation-grounded answers | checked | MVP A added `answer_with_citations` with citation validation and insufficient-evidence behavior. |
| Evaluation/observability | missing | Planned after MVP A. |
| Auto Wiki | missing | Planned after source/search/citation core stabilizes. |
| Resume/JD assistant | missing | Planned as an application layer on top of ContextWiki. |

## Scope and Non-goals

This roadmap covers the complete ContextWiki direction. It is not itself the detailed implementation plan for every phase. The detailed first implementation plan is `docs/plan/2026-05-20-contextwiki-mvp-a.md`.

Non-goals for this roadmap:

- Do not implement runtime code in this document.
- Do not store API tokens or local Chroma content in docs.
- Do not inspect or migrate existing local Chroma data.
- After the 2026-05-22 workflow update, final clean `$subagent-review-loop` verification proceeds to commit, push, and PR creation by default unless the user explicitly asks for local-only work or a safety blocker prevents delivery.

## Files Likely to Change

Current docs-only planning files:

- `docs/plan/2026-05-20-contextwiki-roadmap.md`
- `docs/plan/2026-05-20-contextwiki-mvp-a.md`
- `docs/plan/2026-05-22-roadmap-hij-review.md`

Likely future implementation areas by phase:

- Phase A: `.agents/docs/adr/`, `core/`, `storage/`, `fetching/`, `indexing/`, `search/`, `api/`, `main.py`, `README.md`, and `tests/`.
- Phase B: `fetching/`, `indexing/`, `search/`, `api/`, `environments/`, and connector-focused tests.
- Phase C: new wiki-generation service modules, `search/`, `indexing/`, `api/`, and README/client docs.
- Phase D: evaluation/observability modules, test fixtures, report docs, and optional CLI/API surfaces.
- Phase E: API/deployment modules, runtime configuration, health checks, and deployment docs.
- Phase F: resume/JD assistant application-layer modules and MCP tools.
- Phase G: upload/file parsing modules and ingestion tests.
- Phase H: storage/search/API security metadata, ACL filters, audit logging, secret-reference handling, and deletion/tombstone policy tests.
- Phase I: ingestion worker/queue modules, retry/backoff/idempotency helpers, document identity hardening, source-aware chunking, stale cleanup, and connector load tests.
- Phase J: reranking/query-rewriting modules, grounded answer generation, citation verification, eval fixtures, and quality-tuning reports.

## Acceptance Criteria

- The full ContextWiki roadmap is visible in `docs/plan/`.
- The roadmap distinguishes already checked capabilities, MVP A, later MVP phases, and application-layer features.
- The roadmap explains why MVP A is first.
- The roadmap defines a shared verification policy: unit, integration, and fake E2E tests are mandatory; live external API smoke tests are optional and opt-in.
- The roadmap preserves the repository architecture constraints from `.agents/docs/architecture.md` and ADR 0001.
- The roadmap identifies when a new ADR is needed.
- The roadmap records which Phase H/I/J items are true later hardening and which are Phase B prerequisites.

## Roadmap Overview

### Phase A: ContextWiki Core MVP

Goal: Turn the current MCP content search server into a citation-grounded knowledge backend for Notion and Tistory.

Included:

- Stabilize existing Notion/Tistory ingestion and search paths.
- Add source, sync job, document, and chunk metadata persistence.
- Strengthen incremental ingestion with per-source sync jobs, content hashes, status, and retry state.
- Store chunk metadata required for citations.
- Add source-filtered `search_context`.
- Add `fetch_context`.
- Add `answer_with_citations`.
- Return "insufficient evidence" instead of unsupported answers.
- Update README and MCP tool documentation.

Detailed plan: `docs/plan/2026-05-20-contextwiki-mvp-a.md`.

### Phase B: Code and Website Connectors

Goal: Expand ContextWiki from personal documents/blog posts into codebase and docs intelligence.

Included:

- GitHub repository ingestion.
- Commit or blob SHA based change detection stored separately from stable document identity.
- Path and line-range exact citations for code files.
- Minimum source-aware chunking:
  - Markdown: heading-based chunks.
  - Code: line-range chunks suitable for file/line citations.
  - Plain text: continue using the current character chunker.
- Generic website/docs URL ingestion.
- Sitemap or bounded crawler support.
- Robots/rate-limit safety and source-level crawl configuration.
- Minimum document identity hardening:
  - `external_id`: connector-native stable id across revisions, such as GitHub `owner/repo:path` plus branch/ref when needed.
  - `canonical_url`: stable citation URL for the document or file.
  - `last_seen_at`: updated during successful source syncs.
  - `deleted_at`: soft-delete/tombstone marker for documents that disappeared from a successful source sync.
  - Version metadata, such as commit SHA or blob SHA, should be stored separately from `external_id` for change detection and citation precision.
- Source-wide stale cleanup:
  - After a successful full source sync, mark documents not seen in that sync as deleted or remove their active chunks from retrieval.
  - Continue deleting stale old chunks for changed documents.
  - Do not tombstone documents after partial connector failures or incomplete crawls.

Expected impact:

- Makes ContextWiki credible for backend/codebase Q&A.
- Enables architecture and implementation evidence from real repositories.
- Prevents deleted/moved GitHub files from continuing to appear in search results.

Minimum Phase B gate:

- GitHub connector work should not start with only character chunking. The first GitHub slice must include line-range code chunks even if function/class-aware code chunking waits for a later hardening phase.
- GitHub/Web connector work should not ship without `last_seen_at`/`deleted_at` lifecycle support, because source deletion/move behavior is common and stale chunks become immediately user-visible.
- Fingerprint-level duplicate detection can wait; connector-native identity plus canonical URL and tombstones are the required minimum.

### Phase C: Auto Wiki

Goal: Move from "search my content" to "generate and maintain a living wiki."

Included:

- Repo summary pages.
- Project architecture pages.
- Topic pages across Notion/Tistory/GitHub/docs.
- Automatic backlinks between related documents.
- Stale document detection.
- Wiki page generation with citations back to source chunks.

Expected impact:

- Reframes the product as a knowledge system, not only a retrieval API.

### Phase D: Evaluation and Observability

Goal: Demonstrate production-grade AI system operation.

Included:

- Golden question set.
- Retrieval hit rate.
- Citation correctness checks.
- Hallucination checks.
- Latency tracking.
- Token and estimated cost tracking.
- Failed query logs.
- Evaluation report MCP/API endpoint or CLI command.
- Required verification command includes unit, integration, and fake E2E tests.
- Live API smoke tests remain opt-in and separate from required CI.

Expected impact:

- Strong portfolio differentiator for US backend/AI infrastructure roles.

### Phase E: Remote MCP and API Server

Goal: Make ContextWiki usable by remote agents and clients.

Included:

- HTTP API around source, sync, search, answer, and status operations.
- Optional remote MCP deployment.
- Auth strategy for personal deployment.
- Docker or deployment profile.
- Health checks and operational docs.

Expected impact:

- Shows deployment and operations readiness.

### Phase F: Resume/JD Assistant

Goal: Build a career-memory application layer on top of ContextWiki.

Included:

- `collect_resume_evidence(jd)`.
- `tailor_resume_for_jd(jd, resume)`.
- `find_experience_gaps(jd)`.
- JD skill/responsibility extraction.
- Evidence-backed resume bullet generation.
- ATS keyword alignment.
- Claim verification against stored evidence.

Expected impact:

- Converts ContextWiki into a practical job-search backend without creating a separate RAG stack.

### Phase G: Markdown/PDF Uploads

Goal: Support uploaded documents after the connector/indexing model is stable.

Included:

- Markdown upload.
- PDF upload and text extraction.
- File content hash detection.
- File-level source status.
- Citation metadata for uploaded files.

Expected impact:

- Broadens data coverage while reusing the same source/job/chunk model.

### Phase H: Security, Permissions, and Data Governance

Goal: Make ContextWiki safe for private, multi-source, and eventually remote use.

Included:

- ACL-aware retrieval.
- Tenant/source isolation.
- Token and secret handling.
- Audit logs for source sync, search, fetch, answer, and deletion/tombstone events.
- Deletion and tombstone policy.

Expected impact:

- Makes private-source retrieval defensible before broad remote/API use.
- Keeps source isolation explicit as the system grows beyond a personal local MCP server.

Dependency note:

- Full ACL and tenant models can wait until remote/multi-user deployment work, but source isolation and auth-reference-only secret handling remain mandatory from Phase A/B onward.
- Phase E remote/API work should not become a shared or multi-user deployment without the relevant Phase H controls.

### Phase I: Production Ingestion Hardening

Goal: Make connector sync reliable under real source churn and repeated runs.

Included:

- Worker queue.
- Retry/backoff.
- Idempotency.
- Document identity hardening.
- Source-aware chunking.
- Stale document cleanup.

Expected impact:

- Converts connector ingestion from a demo sync path into a production-style pipeline.
- Makes GitHub/Web/PDF connectors safer when documents move, disappear, or change frequently.

Dependency note:

- Phase B must include minimum document identity, line-range code chunking, and source-wide stale cleanup.
- Phase I can deepen those foundations with queueing, backoff policy, fingerprint dedup, function/class-aware code chunking, and larger-scale cleanup jobs.

### Phase J: Retrieval and Answer Quality

Goal: Improve answer precision, citation trust, and measurable retrieval quality after the connector base is stable.

Included:

- Reranking.
- Query rewriting.
- Citation verification.
- LLM grounded answer generation.
- Eval-driven tuning.

Expected impact:

- Moves ContextWiki from "retrieves relevant chunks" to "reliably answers with verifiable evidence."
- Uses Phase D evaluation foundations to tune retrieval and answer behavior instead of guessing.

Dependency note:

- Phase J should build on Phase D evaluation and observability. Reranking/query rewriting should be measured against golden questions, citation correctness, and insufficient-evidence behavior.

## Architecture/ADR Constraints

- Preserve ADR 0001 layered boundaries:
  - `main.py`: dependency composition and server startup.
  - `api/`: MCP tool contracts and delegation.
  - `search/`: search orchestration and result contracts.
  - `indexing/`: conversion, deduplication, chunking, status, and vector writes.
  - `fetching/`: Notion/Tistory/external source retrieval.
  - `core/`: shared models, exceptions, utilities.
  - `environments/`: configuration and secret access.
- Phase A added ADR 0002 for SQLite metadata persistence and citation contracts, satisfying the roadmap requirement to document that long-term persistence decision.
- Adding `external_id`, `canonical_url`, `last_seen_at`, `deleted_at`, version metadata, ACL metadata, or audit-log tables changes long-term metadata and governance contracts. The implementation plan should extend ADR 0002 or add a new ADR before runtime schema changes.
- Local ChromaDB data must not be deleted, reset, or inspected without explicit approval.
- API tokens should be referenced by environment variable names, not stored in SQLite or docs.

## Migration and Legacy Cleanup Policy

ContextWiki MCP tools are the canonical public contract for the MVP demo and evaluation story:

- `list_sources`
- `sync_source`
- `get_sync_status`
- `search_context`
- `fetch_context`
- `answer_with_citations`

Existing MCP tools should be treated as legacy compatibility during early phases:

- `search_content`
- `search_notion`
- `search_tistory`
- `trigger_index_all_content`
- `get_index_status`

Legacy tool policy:

- Keep legacy tools during Phase A to avoid breaking the existing server while the ContextWiki layer is introduced.
- Phase B or C should either adapt legacy tools to delegate to ContextWiki services or explicitly mark them deprecated in README and demo docs.
- Removing or renaming legacy tools is an MCP contract change and needs a dedicated implementation plan, README update, and verification pass.
- The portfolio/demo path should use the ContextWiki tools, not the legacy tools.

Legacy Chroma data policy:

- Citation-grounded search should only trust ContextWiki-managed chunks, identified by metadata such as `contextwiki_managed=true`.
- Existing raw Chroma documents may stay in place during development because they are excluded from citation search.
- For a clean dev or demo reset, prefer a new Chroma collection/path and a new SQLite metadata DB, then resync sources through ContextWiki ingestion.
- If a reset is required, reset Chroma and SQLite metadata together. Resetting only one side can create SQLite/Chroma drift.
- Do not delete, inspect, or migrate the user's persistent local Chroma data without explicit approval.
- Tests and fake E2E flows must use temporary Chroma and SQLite paths, not the user's persistent local data.

## Test and Verification Plan

This roadmap is a docs-only artifact. Verify with:

```bash
rg --files AGENTS.md .agents/docs .agents/skills docs/plan
git status --short
git diff --check
```

Runtime verification belongs to the detailed phase implementation plans.

All implementation phases should follow this testing policy:

- Required before completion:
  - Python compile/import check.
  - Unit tests for pure logic.
  - Integration tests for service boundaries using temporary SQLite, temporary Chroma, fake vector stores, or mocks.
  - Fake E2E tests that exercise the full product flow without external credentials.
- Optional manual validation:
  - Live external API smoke tests for Notion, GitHub, Tistory, and web/docs connectors.
  - Live smoke tests must require an explicit environment flag such as `RUN_LIVE_E2E=1`.
  - Live smoke tests must never be required for normal CI or ordinary completion because they depend on network, credentials, rate limits, and external service availability.
- Each runtime slice should expose a single required verification command, planned as `scripts/verify_all.sh`, that runs compile checks and `uv run pytest -m "not live"`.
- The `live` pytest marker is reserved for opt-in checks such as `RUN_LIVE_E2E=1 uv run pytest -m live`.

## Risks and Rollback Notes

- Risk: Trying to implement all phases at once would blur MCP contracts, persistence behavior, and connector boundaries.
- Mitigation: Ship Phase A first, then build GitHub/Website, Auto Wiki, and Eval on top of stable source/job/chunk contracts.
- Risk: New SQLite metadata could drift from Chroma state.
- Mitigation: Phase A must define document/chunk identity, idempotent upsert behavior, and retry semantics.
- Risk: GitHub/Web connectors can leave deleted or moved documents searchable.
- Mitigation: Phase B must add `last_seen_at`/`deleted_at` and source-wide stale cleanup or tombstone behavior.
- Risk: Character-only chunks make code citations too weak for backend/codebase Q&A.
- Mitigation: Phase B must include at least code line-range chunks and Markdown heading chunks; function/class-aware chunking can wait for Phase I.
- Risk: Remote/API deployment can expose private source content without governance controls.
- Mitigation: Phase E shared or multi-user deployment requires relevant Phase H controls first.
- Rollback: Docs-only rollback removes this roadmap file. Runtime rollback is phase-specific and must be documented in each implementation plan.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created `feature/contextwiki-mvp-planning` from `origin/main`. | `git fetch origin main`, `git switch -c feature/contextwiki-mvp-planning origin/main` |
| Roadmap planning | completed | Split ContextWiki into full roadmap phases and identified Phase A as the first implementation slice. | This file |
| Legacy cleanup policy | completed | Added roadmap-level policy for legacy MCP tools, ContextWiki canonical tools, and Chroma/SQLite dev reset safety. | `Migration and Legacy Cleanup Policy` |
| Roadmap H/I/J assessment | completed | Added Phase H/I/J and moved minimum source-aware chunking, document identity lifecycle, and stale cleanup gates into Phase B. | `docs/plan/2026-05-22-roadmap-hij-review.md` |
| Roadmap H/I/J verification | completed | Docs-only checks passed for the H/I/J roadmap update. | `rg --files AGENTS.md .agents/docs .agents/skills docs/plan`, `git status --short`, `git diff --check` |
| Focused verification | completed | Docs-only checks passed, including no-output whitespace checks for untracked plan files. | `rg --files AGENTS.md .agents/docs .agents/skills docs/plan`, `git status --short --branch`, `git diff --check`, `git diff --no-index --check /dev/null docs/plan/2026-05-20-contextwiki-roadmap.md` |
| Original roadmap review | completed | Fresh follow-up review completed for the original 2026-05-20 roadmap after the testing-policy update; final reviewer reported no actionable findings. | Prior final pass by subagent `019e4500-3727-7f31-b753-060567ff61ec`; follow-up finding by subagent `019e450a-8a76-7eb3-9c47-ae2b84e80d1d`; final pass by subagent `019e450c-76c9-75d1-b073-70f41170aff0` |
