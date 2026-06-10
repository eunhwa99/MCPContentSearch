# Obsidian Vault Source Connector

## User Request

Add Obsidian vault support to the ContextWiki RAG pipeline so that local `.md`
files in an Obsidian vault can be indexed and searched alongside Notion,
Tistory, GitHub, and website/docs sources. After review findings surfaced,
fix the PR, rerun verification/review until clean, then push the PR updates.

## Branch Preflight Result

- Repository: `/Users/eunhwa/.codex/worktrees/1442/MCPContentSearch`
- Current checkout: detached `HEAD` at `bd7744c`, matching PR branch
  `origin/worktree-obsidian-connector`.
- Worktree is clean at fix-up start, so continuing the current PR branch state
  is safe and matches the user’s explicit request to update the current PR.
- `origin/main` freshness was not refreshed in this worktree because the user
  asked to continue the already-open PR rather than start a new branch.

## Scope and Non-goals

**In scope:**
- Keep `SourceType.OBSIDIAN` and `source_obsidian` registry wiring.
- Fix Obsidian connector partial-snapshot behavior so failed/incomplete fetches
  do not trigger stale cleanup.
- Preserve correct indexed content and source line numbers when stripping
  frontmatter.
- Expand user-friendly Obsidian vault paths such as `~/Vault`.
- Expose Obsidian as a Web Console filter source type.
- Extend targeted regression coverage and rerun repo verification/review gates.

**Non-goals:**
- No new MCP tool — existing `sync_source("source_obsidian")` remains the path.
- No Obsidian graph/backlink metadata extraction.
- No change to the stable identity choice in this PR beyond documenting current
  behavior; rename-stable note identity is a separate design problem.
- No change to legacy search tools, answer payload shapes, or Chroma schema.

## Acceptance Criteria

- `SourceType.OBSIDIAN` remains valid and source registry wiring is preserved.
- `AppConfig.obsidian_vault_path` safely accepts absolute paths and
  `~/...`-style paths.
- Disabled gracefully when the configured vault path is unset or unavailable.
- Partial or failed Obsidian snapshots do not tombstone still-existing notes.
- Markdown files are indexed with correct `document_id`, `canonical_url`
  (`obsidian://open?vault=...&file=...`), title from frontmatter or filename,
  and body content that excludes frontmatter metadata while preserving the
  original post-frontmatter body whitespace.
- Chunk/citation line numbers remain aligned to the original file lines even
  when frontmatter is present.
- `.obsidian/`, `.trash/`, and dot-prefixed paths are skipped.
- Stale cleanup still tombstones documents whose `.md` file was actually
  deleted after a successful full snapshot.
- Web Console filters expose Obsidian as a selectable source type.
- `python -m compileall ...`, targeted pytest, `./scripts/verify_functional_e2e.sh`,
  and `git diff --check` pass.
- A fresh final five-reviewer `$subagent-review-loop` pass reports no
  actionable findings before push.

## Files Changed

| File | Change |
|------|--------|
| `docs/plan/2026-06-10-obsidian-connector.md` | Update plan for review-fix loop |
| `environments/config.py` | Expand and validate vault path safely |
| `fetching/obsidian.py` | Fix fetch failure handling and frontmatter/line metadata |
| `fetching/connectors.py` | Keep stale-cleanup behavior aligned with fetch outcomes |
| `storage/metadata_store.py` | Preserve operational sync errors while clearing stale disabled errors on source recovery |
| `core/public_payloads.py` | Share source/job payload redaction across MCP and Web Console without cross-layer imports |
| `indexing/chunker.py` | Preserve citation-accurate line offsets while keeping stored Obsidian bodies frontmatter-free |
| `indexing/ingestion_service.py` | Freeze per-sync stale-cleanup decision before mutable source refreshes |
| `tests/e2e/test_obsidian_connector_flow.py` | Add regression tests for partial snapshots/frontmatter/path behavior |
| `tests/fetching/test_connectors.py` | Extend connector/config regression coverage if needed |
| `tests/api/test_tools_contract.py` | Cover MCP `list_sources()` refresh contract |
| `web/index.html` | Add Obsidian source filter option |
| `web/app.js` | Keep filter/source helper wiring aligned with Obsidian |
| `api/tools.py` | Refresh registered sources before MCP `list_sources()` response |
| `web_console/app.py` | Refresh registered sources before Web Console source listing |
| `web_console/payloads.py` | Keep source-id mapping aligned with Web Console filters |
| `scripts/smoke_web_console_playwright.py` | Verify browser-visible source refresh recovers Obsidian availability |
| `docs/contextwiki-core-understanding.md` | Document Obsidian source behavior and tombstone safety |

## Worker Orchestration

- Worker A ownership: `fetching/obsidian.py`, `fetching/connectors.py`,
  `environments/config.py`, `tests/e2e/test_obsidian_connector_flow.py`,
  `tests/fetching/test_connectors.py`
  - Goal: fix partial-snapshot, frontmatter, line-range, and path parsing
    findings without changing MCP contracts.
- Worker B ownership: `web/index.html`, `web/app.js`, `web_console/payloads.py`
  - Goal: expose Obsidian consistently in Web Console filter/source-type wiring.
- Main agent ownership: plan updates, integration, verification, review-loop
  orchestration, commit, and push.

## Test and Verification Plan

1. `python -m compileall api core environments fetching indexing search storage wiki web_console main.py scripts/smoke_web_console_playwright.py`
2. `uv run pytest tests/fetching/test_connectors.py tests/e2e/test_obsidian_connector_flow.py tests/web_console/test_app.py tests/api/test_tools_contract.py`
3. `./scripts/verify_functional_e2e.sh`
4. `git diff --check`

## Functional Smoke Matrix

| Feature | Caller Surface | Data Mode | Expected Result | Action/Command | Result | Evidence | Skip Reason |
|---------|---------------|-----------|----------------|----------------|--------|----------|-------------|
| Obsidian sync success | `sync_source("source_obsidian")` via `IngestionService` | Temp SQLite + fake vault | SUCCEEDED, docs indexed | targeted pytest | completed | `uv run pytest ...` 184 passed | — |
| Obsidian partial snapshot safety | `sync_source("source_obsidian")` via `IngestionService` | Temp SQLite + unreadable/missing vault cases | Failed/incomplete snapshot does not tombstone existing docs | targeted pytest | completed | `uv run pytest ...` 184 passed | — |
| Frontmatter stripping + line ranges | Chunk metadata in temp SQLite | Temp SQLite + frontmatter fixtures | Title/body preserved, line numbers match original file | targeted pytest | completed | `uv run pytest ...` 184 passed | — |
| MCP/Web source refresh consistency | MCP `list_sources()` + `get_sync_status()` + Local Web Console UI | Deterministic fake sources + temp SQLite | Obsidian disabled reason updates correctly, refresh failures fall back, persisted source/job errors stay redacted, and recovery clears stale disabled/incomplete-snapshot errors only when appropriate | targeted pytest + Playwright smoke | completed | 184 targeted tests, Playwright source refresh smoke passed | — |
| Existing configured source sync flows | Local Web Console + fake/local sources | Existing deterministic smoke data | No regression to current source sync/filter/download flows | `./scripts/verify_functional_e2e.sh` | completed | 205 functional tests + Playwright smoke passed | — |

## Architecture / ADR Constraints

- Follows ADR 0001 layered boundaries: connector logic stays in `fetching/`,
  config logic in `environments/`, and Web Console exposure in `web/` +
  `web_console/`.
- Follows ADR 0003: failed or partial syncs must not tombstone missing
  documents; chunk line metadata should remain citation-accurate.
- Follows ADR 0004: connector fetches must fail or otherwise suppress stale
  cleanup when the bounded snapshot is incomplete.
- No MCP tool contract change, no secret exposure, no local user-data reset.

## Risks and Rollback Notes

- **Risk**: Correcting frontmatter line offsets can change chunk metadata for
  affected notes. This is intentional because it restores ADR-aligned citation
  accuracy.
- **Risk**: Treating unreadable vault content as sync failure may surface more
  visible errors for malformed local notes. This is preferable to silent stale
  tombstoning.
- **Rollback**: Revert the Obsidian connector files and Web Console exposure
  changes from this PR branch only. No schema rollback is required.

## Progress Log

| Phase | Status | Summary | Evidence |
|-------|--------|---------|---------|
| Branch preflight | completed | Continuing the existing PR checkout by explicit user request. | `git status --short --branch`, `git worktree list` |
| Plan document | completed | Updated for review-fix loop and push request. | This file |
| Worker orchestration | completed | Split work into backend correctness and Web Console exposure boundaries. | Current run log |
| Middle review gate | completed | Fresh five-reviewer pass `019eb0d5-12b9`, `019eb0d5-c4ed`, `019eb0d5-c775`, `019eb0d5-c9f7`, `019eb0d5-cc8e` surfaced actionable fixes around malformed frontmatter stripping, refresh fallback/status freshness, leading-blank-line hashing, and review evidence coverage. | Reviewer pass `019eb0d5-*` in current run log |
| Refactor | completed | Applied follow-up review findings for skipping symlinked notes/directories while disabling symlinked vault roots, current disabled-reason refresh precedence, frontmatter body-whitespace preservation, and shared source/job payload sanitization in a core-owned helper. | Current worktree diff |
| Focused tests | completed | `python -m compileall ...`, `uv run pytest tests/fetching/test_connectors.py tests/e2e/test_obsidian_connector_flow.py tests/web_console/test_app.py tests/api/test_tools_contract.py`, and `git diff --check` passed after the latest fixes. | Current run log (`184 passed, 1 warning`) |
| Functional E2E gate | completed | `./scripts/verify_functional_e2e.sh` passed after the latest fixes, including Playwright Web Console refresh, answer, download, wiki, and sync smoke. | Current run log (`205 passed, 1 warning`) |
| Final review gate | pending | Latest fresh five-reviewer pass before the current rerun found follow-up issues; those fixes have been applied and re-verified, and a new clean five-reviewer pass is still required before push. | Reviewer pass history in current run log |
| PR delivery | pending | Commit and push updates to `worktree-obsidian-connector`. | Pending |
