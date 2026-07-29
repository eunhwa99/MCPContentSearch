# Search documents matched context

## User request

Return useful matched content from `search_documents`, rather than forcing an
LLM to rely only on a short preview or immediately call `fetch_context`.
Update code, tests, README, and maintained architecture documentation.

Follow-up README collaboration:

- Apply the agreed cleanup from `Troubleshooting` onward.
- Keep the README focused on what the project is and how to use it.
- Audit `.agents/docs/architecture.md` against the current implementation and
  correct factual drift without changing runtime behavior.

## Branch preflight result

- Continue the user-selected isolated worktree
  `/Users/eunhwa/IdeaProjects/MCPContentSearch-ai-portfolio-hardening`.
- Current branch: `feature/ai-portfolio-hardening`, one commit ahead of
  `origin/main`.
- The worktree is intentionally dirty with separate evaluation/benchmark work
  and the in-progress README collaboration. Do not switch branches, pull,
  delete branches, or overwrite unrelated changes.
- The user explicitly requested local-only iteration: do not commit or push.

## Scope and non-goals

### In scope

- Return bounded matched chunk content from each `search_documents` result.
- Use an explicit `matched_context` field populated from the best matching
  chunk already selected for that document.
- Remove `preview` from the document-oriented result model and public
  `search_documents` payload; the user explicitly does not require backward
  compatibility for this contract.
- Keep the separate chunk-oriented `search_context` preview behavior unchanged.
- Update the public MCP payload allowlist, models, focused tests, retained E2E
  assertions, README, and architecture explanation.
- Clarify that `fetch_context(document_id)` remains an optional drill-down for
  stored full-document content and chunks.
- Simplify troubleshooting and verification language for normal users.
- Remove recruiter/reviewer-oriented README wording.
- Verify architecture claims against MCP registration, source registration,
  ingestion, search, storage, configuration, and verification code.

### Non-goals

- Do not change ranking, grouping, query rewrite, source filters, or `top_k`
  semantics.
- Do not remove or rename `fetch_context` or its parameters in this change.
- Do not inspect or mutate user Chroma/SQLite data.
- Do not run live Notion, Tistory, GitHub, OpenAI, or Codex checks.
- Do not modify the separate evaluation or semantic benchmark implementation.
- Do not change production code or public MCP behavior during the docs audit.
- Do not commit, push, or update the existing PR.

## Acceptance criteria

1. Every `search_documents` result includes `matched_context` containing the
   selected representative chunk's text.
2. `search_documents` results do not contain `preview`; `search_context`
   remains unchanged.
3. The public FastMCP response includes `matched_context` but still excludes
   internal ranking fields.
4. `matched_context` is required; internal document-search results that omit
   it, use a non-string value, or use an unsupported payload type fail clearly
   without returning or logging payload content.
5. README explains when an LLM chooses `search_context`,
   `search_documents`, and optional `fetch_context`.
6. Focused unit, MCP contract, and retained E2E tests pass using fake or
   temporary data.
7. README troubleshooting guidance matches the accepted configuration formats.
8. README verification language distinguishes the credential-free demo from
   developer verification without reviewer-facing phrasing.
9. Architecture statements inspected in this follow-up match the current code,
   and any unsupported or stale claims are corrected.

## Steps

1. Update the document search result model and grouping path.
2. Update the public MCP payload and contract tests.
3. Update README and architecture documentation.
4. Run focused verification, functional E2E, and local-only review.
5. Apply README cleanup and run a code-backed architecture documentation audit.

## Files likely to change

- `core/models.py`
- `search/context_service.py`
- `api/tools.py`
- `tests/search/test_context_service.py`
- `tests/api/test_tools_contract.py`
- `tests/contracts/test_public_mcp_contracts.py`
- `tests/e2e/test_contextwiki_flow.py`
- `README.md`
- `.agents/docs/architecture.md`

## Test and verification plan

- Focused pytest for context search, MCP tool contracts, public FastMCP
  contracts, and affected E2E flow.
- `python -m compileall api core search`
- `./scripts/verify_functional_e2e.sh`
- `git diff --check`
- No live or user-data verification.
- For this docs-only follow-up: `rg` path/content checks and `git diff --check`;
  no runtime test is required unless the audit reveals a code/doc ambiguity.

## Functional smoke matrix

| Feature | Caller Surface | Data Mode | Expected Result | Action/Command | Result | Evidence | Skip Reason / Substitute |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `search_documents` contract | FastMCP/public contract test | Fake search service | Each document includes required `matched_context` and omits `preview` | Focused contract pytest | passed | Real FastMCP/API contract included in 223 passed | N/A |
| Document grouping | Search service test | Temporary SQLite/fake retriever | Best matched chunk text becomes `matched_context` | Focused search pytest | passed | Search service suite included in 223 passed | N/A |
| Neighboring `search_context` | Retained functional E2E | Temporary/fake data | Chunk-level evidence remains unchanged | Functional E2E gate | passed | 25 passed | N/A |
| Optional `fetch_context` | Retained functional E2E | Temporary SQLite | Existing ID lookup remains compatible | Functional E2E gate | passed | 25 passed | N/A |
| Source sync/status tools | Retained functional E2E | Fake/temp data | Existing source sync and status flows still pass | Functional E2E gate | passed | 25 passed | N/A |
| Live connectors/user stores | Live MCP/source calls | User/external data | No live mutation or egress | Not run | blocked/gated | No live call made | Requires explicit approval; the passing fake/temp E2E is the substitute |
| README usage guidance | Markdown documentation | Repository files only | Troubleshooting and verification match current behavior | Code-backed docs audit plus `git diff --check` | passed | Duplicate `.env` removed; troubleshooting and verification wording aligned | N/A |
| Architecture accuracy | Maintained architecture documentation | Read-only source inspection | Tool, sync, retrieval, storage, and verification descriptions match code | `rg`/source inspection plus docs diff check | passed | Corrected sync return/status semantics, query rewrite/provider, embeddings, and excluded local-only benchmark material | No live/user-data execution needed |

## Architecture constraints

- Chroma/retriever output remains candidate evidence; SQLite remains the
  authoritative active-document gate.
- Document grouping must continue to choose one representative result per
  document using existing ranking behavior.
- Public tool payloads must not expose `vector_score` or
  `metadata_priority`.
- Returned matched content must come from already validated active evidence,
  not from a new live fetch.

## Risks and rollback notes

- Larger MCP responses: one matched chunk per document is bounded by existing
  chunking and is safer than returning every full document.
- Public contract break: existing `search_documents` clients that read
  `preview` must switch to `matched_context`; the user explicitly accepted
  dropping compatibility.
- Rollback requires restoring the document-search `preview` field and removing
  `matched_context` from that result contract and documentation; no schema
  migration or reindexing is required.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Continued the existing dirty isolated feature worktree by explicit user request. | `git status`, `git branch -vv`, `git worktree list` |
| Planning | completed | Replaced document-search `preview` with `matched_context` as an explicitly approved breaking contract. | This plan and user follow-up |
| Implementation | completed | Removed document-search `preview` from model, service, public payload, tests, README, and architecture while preserving `search_context.preview`. | Follow-up workers completed |
| Focused verification | completed | Compile, Ruff, and focused contract/search/E2E tests passed after exception-boundary enforcement. | `223 passed` |
| Functional smoke | completed | Deterministic fake/temp full functional E2E passed after removal. | `25 passed` |
| Review | completed | Final fresh two-reviewer pass reported no actionable findings after exception-boundary fixes. | `review_exception_final1`, `review_exception_final2` |
| Delivery boundary | completed | The user requires local-only file edits; no commit, push, or PR update is permitted. | User instruction |
| README iteration | completed | At this intermediate step, removed only the duplicate Usage `.env` block and reviewed `Troubleshooting` onward before the later follow-up edits. | `git diff --check -- README.md docs/plan/2026-07-29-search-documents-matched-context.md` |
| README and architecture follow-up | completed | Applied the agreed README cleanup and integrated the read-only architecture audit against current code. | `architecture_docs_audit`; `git diff --check` |
| Follow-up review | completed | Final fresh two-reviewer pass reported no actionable findings after the sync vocabulary and embedding requirement fixes. | `readme_arch_review_pass8_1`, `readme_arch_review_pass8_2` |

### Review findings

- Add a chunk longer than the preview limit and assert that `preview` is
  truncated while `matched_context` preserves the full representative chunk.
- Align the LLM-visible `search_context`, `search_documents`, and
  `fetch_context` Tool descriptions with the README roles, and assert the
  actual FastMCP Tool schema descriptions.
- The initial findings were addressed; focused verification now has 223
  passing tests and the 25-test functional E2E gate passed again.
- Follow-up decision: backward compatibility is no longer required, so
  `preview` will be removed from `search_documents` while remaining unchanged
  on `search_context`.
- The earlier preview-truncation review item is superseded for
  `search_documents`: the long-content regression now asserts full
  `matched_context` and complete absence of `preview`.
- Remove the legacy missing-field fallback: `matched_context` must be required
  in both the result model and public payload normalization so stale
  preview-only internal results fail visibly.
- The strict-field finding was addressed: missing values raise `ValueError`,
  non-string values raise `TypeError`, and explicit string values (including
  `""`) remain valid without leaking prior payload content.
- Reject unsupported raw result types instead of returning them unchanged;
  regression coverage must prove strings, lists, and preview-only plain objects
  cannot bypass the public allowlist or leak their values through errors.
- The fail-closed finding was addressed: only mappings (or model dumps that
  return mappings) are accepted, and unsupported payloads fail with a generic
  content-free `TypeError`.
- Normalize exceptions raised by `model_dump()` or `Mapping.items()` to the
  same generic `TypeError` with no exception chaining or payload content.
- The exception-boundary finding was addressed and covered with secret-bearing
  failing model-dump and mapping regressions.

### README and architecture audit findings

- `sync_all` runs selected source syncs concurrently and waits for their
  outcomes; it is not the immediate background-launch contract used by
  `sync_source`.
- A fully blocked bulk run returns `blocked`, while a normally accepted
  `sync_source` returns a running job and exceptional/overlapping paths may
  return an existing or failed terminal job.
- `search_documents` reuses validated candidate retrieval but explicitly
  disables optional query rewrite.
- Query rewrite currently supports OpenAI only. Embedding behavior is inherited
  from LlamaIndex runtime/default configuration rather than a separate
  `AppConfig` embedding-provider switch.
- Semantic-benchmark and Codex-ranker material remains outside this delivery
  scope and was removed from the maintained architecture description.
- First follow-up review found that query rewrite must be shown after initial
  local retrieval rather than before all retrieval, the default OpenAI
  embedding egress boundary needs to be explicit, and the generic sync-failure
  example should not look GitHub-specific.
- Those findings were addressed: the retrieval diagram now shows initial
  search, confidence evaluation, conditional rewrite, second retrieval, and
  result-set selection; embedding-dependent egress and `MockEmbedding`
  substitutes are explicit; the sync-status example tells readers to replace
  the sample with the failed source ID.
- Second follow-up review clarified that SQLite active-hit validation and
  metadata fallback happen inside each original/rewritten retrieval stage,
  before the result sets are compared. It also corrected an intermediate
  progress-log sentence; the reported duplicate `""` sentence was not present.
- Third follow-up review corrected stale module ownership: `fetch_context` is
  an API adapter over direct `MetadataStore` document/chunk hydration, not a
  `search/` service responsibility.
- Fourth follow-up review replaced the README's `private` claim with
  `self-hosted` because default OpenAI embeddings may transmit document chunks
  and search queries externally.
- Fifth follow-up review added the missing wait step after background
  `sync_source`: callers now check `get_sync_status` for `succeeded` before
  searching, while `sync_all` already waits for its aggregate outcomes.
- Sixth follow-up review made the Usage flow outcome-aware: `sync_source`
  polling stops at `succeeded` or `failed`, `sync_all` callers verify each
  desired source's `sync_outcome`, and README now states that default OpenAI
  embeddings may receive indexed chunks and search queries.
- Seventh follow-up review separated immediate `sync_source` errors from
  polled status errors, separated `sync_all.status` from
  `results[].sync_outcome`, and clarified that default indexing and search
  embeddings both require `OPENAI_API_KEY`.
- Final fresh pass 8: both read-only reviewers reported no actionable
  findings. Commit, push, staging, and PR updates remain intentionally skipped
  under the user's local-only instruction.
