# Obsidian Vault Source Connector

## User Request

Add Obsidian vault support to the ContextWiki RAG pipeline so that local `.md`
files in an Obsidian vault can be indexed and searched alongside Notion,
Tistory, GitHub, and website/docs sources.

## Branch Preflight Result

- Repository: `/Users/eunhwa/IdeaProjects/MCPContentSearch`
- Worktree created: `.claude/worktrees/obsidian-connector` on branch
  `worktree-obsidian-connector` from current HEAD.
- Target edits happen in the isolated worktree only.
- `origin/main` freshness: not checked (offline session). Changes are based on
  local `main` HEAD at `174d4a4`.

## Scope and Non-goals

**In scope:**
- New `SourceType.OBSIDIAN` enum value in `core/models.py`.
- New `CONTEXTWIKI_OBSIDIAN_VAULT_PATH` config field in `environments/config.py`.
- New `fetching/obsidian.py` with `fetch_obsidian_documents()` — filesystem
  walk, frontmatter title extraction, Obsidian URI generation, content hash.
- New `ObsidianSourceConnector` in `fetching/connectors.py` registered via
  `build_source_registry()`.
- E2E tests in `tests/e2e/test_obsidian_connector_flow.py`.
- Update `tests/fetching/test_connectors.py` to include `source_obsidian`.
- `.env` updated with `CONTEXTWIKI_OBSIDIAN_VAULT_PATH` pointing to user vault.

**Non-goals:**
- No new MCP tool — existing `sync_source("source_obsidian")` covers it.
- No Obsidian graph/backlink metadata extraction.
- No ADR required — this follows the established Phase B connector pattern
  (ADR 0004) without changing its contracts.
- No changes to legacy search tools, Chroma structure, or citation answer shape.

## Acceptance Criteria

- `SourceType.OBSIDIAN` exists and is valid.
- `AppConfig.obsidian_vault_path` reads from `CONTEXTWIKI_OBSIDIAN_VAULT_PATH`.
- `ObsidianSourceConnector` is returned by `build_source_registry()` with
  `source_id="source_obsidian"`.
- Disabled gracefully when vault path is unset or not a directory.
- Markdown files are indexed with correct `document_id`, `canonical_url`
  (`obsidian://open?vault=...&file=...`), title from frontmatter or filename.
- `.obsidian/` and `.trash/` system dirs are skipped.
- Stale cleanup tombstones documents whose `.md` file was deleted.
- 971 tests pass (`uv run pytest -q -m "not live"`).
- `./scripts/verify_functional_e2e.sh` passes.

## Files Changed

| File | Change |
|------|--------|
| `core/models.py` | Add `OBSIDIAN = "obsidian"` to `SourceType` |
| `environments/config.py` | Add `obsidian_vault_path: Path \| None` field |
| `fetching/obsidian.py` | New — filesystem fetcher |
| `fetching/connectors.py` | Add `ObsidianSourceConnector`; register in `build_source_registry()` |
| `tests/e2e/test_obsidian_connector_flow.py` | New — 8 E2E tests |
| `tests/fetching/test_connectors.py` | Add `source_obsidian` to registry set assertion |
| `.env` | Add `CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/Users/eunhwa/Obsidian/myVault` |

## Test and Verification Plan

1. `uv run python -m compileall api core environments fetching indexing search storage wiki web_console main.py`
2. `uv run pytest -q -m "not live"` — must pass 971 tests.
3. `./scripts/verify_functional_e2e.sh` — full functional E2E gate.

## Functional Smoke Matrix

| Feature | Caller Surface | Data Mode | Expected Result | Action/Command | Result | Evidence | Skip Reason |
|---------|---------------|-----------|----------------|----------------|--------|----------|-------------|
| Obsidian sync (configured source) | `sync_source("source_obsidian")` via IngestionService | Temp SQLite + RecordingIndexer in E2E test | SUCCEEDED, docs indexed | `pytest tests/e2e/test_obsidian_connector_flow.py` | passed | 8/8 E2E tests pass | — |
| Obsidian sync disabled when no vault | `ObsidianSourceConnector(config)` with `None` path | No filesystem | `enabled=False`, empty fetch | E2E test | passed | `test_obsidian_connector_disabled_when_vault_path_not_set` | — |
| Stale cleanup tombstone | Second sync after file deletion | Temp SQLite | `deleted_at != ""` set on removed doc | E2E test | passed | `test_obsidian_stale_cleanup_removes_deleted_file` | — |
| Obsidian URI canonical_url | Chunk URL field | Temp SQLite | Starts with `obsidian://open?vault=` | E2E test | passed | `test_obsidian_connector_canonical_url_uses_obsidian_scheme` | — |
| MCP tool contract (`sync_source`) | Not changed | — | Unchanged | Not re-tested | not affected | No change to `api/tools.py` | — |
| Auto Wiki / answer / search | Not changed | — | Unchanged | Not re-tested | not affected | No change to search/wiki/answer | — |
| Web Console UI | Not changed | — | Unchanged | Not re-tested | not affected | No Web Console changes | — |
| Live Obsidian vault sync | MCP client + real vault | User vault at `/Users/eunhwa/Obsidian/myVault` | Indexes real .md files | Manual — not run | blocked/gated | Requires live MCP session; `.env` updated | User can run manually |

## Architecture / ADR Constraints

- Follows ADR 0001 layered boundaries: new connector lives in `fetching/`,
  registered through `SourceRegistry`, no shortcut in `api/tools.py`.
- Follows ADR 0002: `DocumentModel` fields (`external_id`, `canonical_url`,
  `content_hash`, `updated_at`, `source_id`) all populated.
- Follows ADR 0003: stable `external_id` = vault-relative path, content hash
  for change detection, `supports_stale_cleanup = True`.
- Follows ADR 0004: new connector added as a `fetching/` responsibility,
  registered via `SourceRegistry`, configuration is env-driven through
  `AppConfig`, no new MCP tools, no secret storage.
- No new ADR needed: this is a straightforward extension of the established
  Phase B connector pattern.

## Risks and Rollback Notes

- **Risk**: Large vaults with many files may be slow — no page cap implemented
  yet (unlike GitHub/Web connectors). Acceptable for now; a future
  `obsidian_max_files` limit can be added in a follow-up.
- **Risk**: Binary or non-UTF-8 `.md` files silently skipped — consistent with
  GitHub connector behavior.
- **Rollback**: Remove `ObsidianSourceConnector` from `build_source_registry()`
  and remove `CONTEXTWIKI_OBSIDIAN_VAULT_PATH` from `.env`. No Chroma or SQLite
  migration needed for rollback since vault data is re-indexable.

## Progress Log

| Phase | Status | Summary | Evidence |
|-------|--------|---------|---------|
| Branch preflight | completed | Isolated worktree `obsidian-connector` created. | `git worktree list` |
| Implementation | completed | 5 files changed, 1 new connector file, 1 new test file. | Worktree diff |
| Focused tests | completed | 971 tests pass (`uv run pytest -q -m "not live"`). | pytest output |
| Plan document | completed | This file. | `docs/plan/2026-06-10-obsidian-connector.md` |
| Functional E2E gate | pending | `./scripts/verify_functional_e2e.sh` | Pending |
| Middle review gate | pending | `$subagent-review-loop` ×5 | Pending |
| Refactor | pending | Apply review findings if any. | Pending |
| Final review gate | pending | `$subagent-review-loop` ×5 | Pending |
| PR delivery | pending | Commit, push, create PR. | Pending |
