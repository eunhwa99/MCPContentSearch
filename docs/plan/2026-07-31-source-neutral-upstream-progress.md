# Plan: Source-neutral upstream progress hints

## User request

Rename Notion-centric `upstream_total_pages` / `upstream_fetched_pages` to
source-neutral names and wire progress for sources that already have a list
phase (GitHub tree, Obsidian walk), not only Notion.

## Branch preflight

- Primary worktree was on `feature/parallel-sync-worker-concurrency` with only
  untracked `.worktrees/` noise; did not switch/pull there.
- Fetched `origin/main` and created isolated worktree
  `.worktrees/upstream-progress` on
  `feature/source-neutral-upstream-progress` @ `9d68643`.

## Scope

- Public running-job hints: `upstream_total`, `upstream_done` (replace
  `upstream_total_pages`, `upstream_fetched_pages` in MCP/model payloads).
- SQLite: additive `upstream_total` / `upstream_done` columns; dual-write old
  page columns for existing DBs; reads prefer new then fall back to old.
- Emit progress from Notion (mapped), GitHub, Obsidian; Tistory best-effort
  scan progress (`max_id` / completed).
- Update architecture docs for the rename and multi-source meaning.
- Unit + integration + deterministic E2E/contract coverage.

## Non-goals

- Changing tombstone/cleanup rules.
- Live API validation.
- Renaming historical plan docs.

## Acceptance

1. Running `get_sync_status` job hints expose `upstream_total` / `upstream_done`,
   not the old `*_pages` keys.
2. Notion progress still updates the counters with page semantics.
3. GitHub/Obsidian update counters from tree/walk totals and per-item completion.
4. Focused tests + `./scripts/verify_all.sh` pass.
5. Architecture documents the neutral fields.

## TDD

- RED before production edits (focused contract + ingestion/fetcher tests).
- GREEN minimum implementation; refactor while green; full suite; smoke; review.

## Workers

| Worker | Owns |
|--------|------|
| Test worker | `tests/**` for this feature |
| Impl worker | `core/models.py`, `storage/metadata_store.py`, `api/tools.py`, `indexing/ingestion_service.py`, `fetching/{github,obsidian,tistory,notion,connectors}.py` as needed |
| Docs worker | `.agents/docs/architecture.md` (and README only if status fields mentioned) |

## Risks

- MCP contract change for hint field names — document in architecture; no silent
  dual-expose of old keys in public payload (replace, with SQLite dual-write only).
- Rollback: revert branch / PR.

## Progress log

| Phase | Status | Summary | Evidence |
|-------|--------|---------|----------|
| Branch preflight | done | Isolated worktree from origin/main | `.worktrees/upstream-progress` |
| Plan | done | This file | |
| RED | done | 14 focused failures for new fields + multi-source progress | [RED tests](daa4b59e-e795-42cf-bc36-7fcf0f24e181); `EXIT:1` KeyError/AttributeError/TypeError |
| GREEN | done | Neutral fields + multi-source progress emitters | [GREEN impl](221746e8-ee68-45a2-b070-f0d43194ca6b); 15 focused passed |
| verify_all | done | Full suite re-passed after ops/correctness follow-ups | `./scripts/verify_all.sh` exit 0 (1267 pytest + eval + e2e) |
| Functional smoke | done | Fake/temp MCP status + Obsidian progress→status | see matrix below |
| Ops review fix | done | InactiveJobStop re-raise; throttle; neutral status | [Fix ops](a561ca08-e647-4c2f-9f06-485543b4b0ec); RED 14→GREEN 37 focused |
| Review follow-up (TEST) | done | Storage dual-write/prefer/migrate + Obsidian E2E | [Add tests](a4124b26-6b39-4a65-8af4-b6fb77a50ef9); 10 focused passed |
| Review pass 2 | blocked→retry | First trio failed (usage limit); retrying composer-2.5-fast cavecrew | failed: e7a22c65 / a19c1d7d / d96ca91d; retry: c1cd87c3 / 3e872a9c / 37446155 |
| Ops review follow-up (cancel+liveness) | done | Tistory InactiveJobStop cancel cleanup; coalesce page_fetch touches; advance last_progress_at on liveness; read-only inactive check when skipping writes | RED exit 1: 3 failed (`cancelled_before_session_exit` empty; `last_progress_writes` delta 0; `touch_count == 12`). GREEN: 5 exact + 8 related passed |
| Correctness fix (`_prefer`+GH stop) | done | primary zeros kept; mid-discovery stop between repos | [Fix R1](74876fe9-45e5-41ca-af2f-7c129daf39f4); verify_all exit 0 (1271) |
| Review pass 6 | done | All three clean | R1 5c7cb9d2 / R2 41f33b53 / R3 0d4ed1af — NO ACTIONABLE FINDINGS |
| PR | in_progress | Commit, push, main-base PR | |

## Functional smoke matrix

| Surface | Mode | Result |
|---------|------|--------|
| `get_sync_status` running hints (`upstream_total`/`upstream_done`) | pytest contract/temp | pass (verify_all) |
| Obsidian connector progress → `get_sync_status` | temp vault E2E | pass (`test_obsidian_connector_progress_updates_upstream_counters_via_get_sync_status`) |
| Notion/GitHub/Obsidian/Tistory progress emit + cancel | unit/integration fakes | pass |
| Live source sync | blocked/gated | needs explicit user approval + plan; substitute: fake/temp above |
