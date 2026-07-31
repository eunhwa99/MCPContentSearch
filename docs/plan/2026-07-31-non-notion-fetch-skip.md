# Non-Notion Fetch Skip Unchanged Documents

## User Request

Notion already skips expensive block-content fetch when stored
`modified_at` matches remote edit time. Other sources store change tokens
(`modified_at` / `version_id`) for browse/filters but still always download
bodies. Extend the same fetch-before-index skip pattern to sources that have a
cheap remote/local change token before content fetch.

## Branch Preflight Result

- Starting worktree: dirty primary repo (`?? .codex-conflict-worktrees/`,
  `?? .worktrees/`); did not switch/pull/delete there.
- Freshness: `git fetch origin main` succeeded.
- Isolated worktree:
  `/Users/eunhwa/IdeaProjects/MCPContentSearch/.worktrees/non-notion-fetch-skip`
- Task branch: `feature/non-notion-fetch-skip` initially from `origin/main`
  (`ff45c8b`).
- Pre-implementation refresh (2026-07-31): stashed local RED WIP, fast-forwarded
  to `origin/main` (`5a7efab`, includes PR #93 concurrency + PR #94 harness
  perf-delta docs), restored WIP cleanly. HEAD matches `origin/main`.

## Scope and Non-Goals

### Scope

- **Obsidian:** After vault listing/stat, skip reading note bytes when an active
  stored document has non-empty content and canonical `modified_at` matches
  filesystem mtime; reuse stored body/`content_hash`; emit
  `page_fetch_skipped`; keep skipped notes in the snapshot for `last_seen` /
  stale cleanup.
- **GitHub:** After tree planning, skip `_fetch_blob_text` when an active stored
  document has non-empty content and `version_id` equals the tree blob SHA;
  reuse stored body/`content_hash`; emit `page_fetch_skipped`; keep skipped
  blobs in the snapshot.
- Wire Obsidian/GitHub connectors to `metadata_store` via app composition
  (generalize Notion-only wiring).
- Extend `get_documents_for_fetch_reuse` to include `version_id` (and keep
  existing skip/reuse fields); batch-load only planned ids.
- Unit + integration + deterministic E2E coverage before production edits.
- Update `.agents/docs/architecture.md` identity table for Obsidian/GitHub skip.

### Non-Goals

- **Tistory:** no cheap remote change token before HTML fetch (only
  `published_at` after body download). Leave unchanged; document rationale.
- Do not change MCP tool contracts or public response shapes.
- Do not call live Notion/Tistory/GitHub or mutate user Chroma/SQLite outside
  temp tests.
- Do not change Notion skip semantics beyond shared loader field additions.

## Acceptance Criteria

- Unchanged Obsidian notes (matching canonical mtime/`modified_at`) do not read
  note bytes.
- Unchanged GitHub blobs (matching stored `version_id` to tree SHA) do not call
  blob download.
- Changed, missing, tombstoned, or empty-content stored docs still fetch.
- Skipped docs remain in fetch snapshots for stale cleanup / `last_seen`.
- Progress reports skipped items via `page_fetch_skipped` (upstream_done).
- Focused unit/integration/E2E pass, then `./scripts/verify_all.sh`, smoke, and
  clean three-reviewer review before PR.

## Worker Ownership

| Worker | Owned files | Acceptance |
| --- | --- | --- |
| tests | `tests/fetching/test_obsidian.py`, `tests/fetching/test_github.py`, `tests/fetching/test_connectors.py`, `tests/storage/test_metadata_store.py` (reuse fields), new integration/E2E skip tests under `tests/fetching/` / `tests/e2e/` | RED first; unit+integration+E2E assert skip vs refetch |
| storage | `storage/metadata_store.py` | `version_id` in fetch-reuse batch; docstring source-neutral |
| obsidian-fetch | `fetching/obsidian.py` | mtime skip + progress; loader hook |
| github-fetch | `fetching/github.py` | SHA/`version_id` skip + progress; loader hook |
| wiring | `fetching/connectors.py`, `app_runtime.py` | metadata_store on Obsidian/GitHub; batch loaders |
| docs | `.agents/docs/architecture.md`, this plan | Document skip rules; Tistory non-goal |

Shared-file rule: storage → wiring → source fetchers sequentially after RED;
docs after GREEN. Main agent integrates; workers must not revert peers.

## TDD RED (record before production)

- Command (exact focused suite to be finalized by tests worker; must cover all
  three layers):
  `uv run --locked pytest -q tests/fetching/test_obsidian.py tests/fetching/test_github.py tests/fetching/test_connectors.py tests/storage/test_metadata_store.py -k 'fetch_reuse or skip_unchanged or fetch_skip' tests/e2e/test_*fetch_skip* --tb=line`
- Layers: unit (obsidian mtime skip / github sha skip / store version_id),
  integration (connector+store wiring), E2E (second sync skips body fetch)
- Exit code: expected `1`
- Failure signatures: missing skip helpers / loader args / `version_id` in
  reuse payload / connectors lack `metadata_store`
- Missing behavior: Obsidian/GitHub fetch-before-index skip not implemented

## Verification

- Focused GREEN, refactor while green, `./scripts/verify_all.sh`
- Eval: n/a (no retrieval quality change)
- Functional smoke: fake/temp Obsidian vault + fake GitHub HTTP only; live
  providers blocked/gated

## Functional Smoke Matrix (planned)

| Feature | Caller | Data mode | Expected |
| --- | --- | --- | --- |
| Obsidian unchanged skip | connector/E2E temp vault | temp SQLite + vault files | second sync emits skip; no byte re-read |
| GitHub unchanged skip | connector/E2E fake HTTP | temp SQLite + fake tree/blob | second sync skips blob GET |
| Notion skip regression | existing Notion skip tests | fake Notion | still skips blocks |
| Tistory | n/a | — | unchanged; blocked as non-goal |
| Live sources | — | live | blocked/gated |

## Risks

- False skip if mtime/SHA comparison is loose → prefer strict equality; refetch
  when empty/ambiguous.
- Obsidian skip without re-reading frontmatter may lose title updates that
  somehow change without mtime change (impossible on normal FS) — use stored
  title from reuse row when skipping.
- GitHub documents historically omit fetch-time `content_hash`; reuse store hash
  after first index.

## Architecture Constraints

- GitHub change token is `version_id` (blob SHA), not `modified_at`.
- Obsidian change token is filesystem mtime → `modified_at`.
- Batched id-only hydrate; no full-corpus `list_documents` browse.
- Skipped docs still returned in snapshot.

## Improvement Performance Delta

### Declared metrics

| Metric | Unit | Method | Expected direction |
| --- | --- | --- | --- |
| Unchanged Obsidian note byte reads on second sync | count | E2E/integration `_track_note_byte_reads` / focused skip suite | decrease (→ 0) |
| Unchanged GitHub blob GETs on second sync | count | E2E/unit `_github_blob_urls` / focused skip suite | decrease (→ 0) |

Environment: local temp vault/SQLite + mocked GitHub HTTP; no user data.

### Baseline (pre-improvement behavior)

Before this change, connectors always read note bytes / download blobs even when
stored change tokens matched. Evidence: RED suite (32 failures for missing skip)
and architecture stating only Notion had fetch-before-index skip.

| Metric | Before |
| --- | --- |
| Unchanged Obsidian note byte reads on second sync | 1 per unchanged note |
| Unchanged GitHub blob GETs on second sync | 1 per unchanged blob |

### After / delta

Measured after `./scripts/verify_all.sh` (exit 0) via focused unit/E2E skip suite
(`assert read_calls == []` / `assert _github_blob_urls(client) == []` on
unchanged second-pass paths).

| Metric | Unit | Before | After | Absolute Δ | Relative Δ | Method | Interpretation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Unchanged Obsidian note byte reads on second sync | count/item | 1 | 0 | −1 | −100% | `_track_note_byte_reads` + E2E second sync | unchanged notes no longer re-read |
| Unchanged GitHub blob GETs on second sync | count/item | 1 | 0 | −1 | −100% | `_github_blob_urls` + E2E second sync | unchanged blobs no longer downloaded |

## Functional Smoke Matrix (results)

| Feature | Caller | Data mode | Result | Evidence |
| --- | --- | --- | --- | --- |
| Obsidian unchanged skip | E2E temp vault | temp SQLite + vault | passed | `test_obsidian_fetch_skip_flow` in functional E2E (57 passed) |
| GitHub unchanged skip | E2E fake HTTP | temp SQLite + fake API | passed | `test_github_fetch_skip_flow` in functional E2E |
| Notion skip regression | retained E2E | fake Notion | passed | `test_notion_fetch_skip_flow` |
| Neighboring Obsidian connector flow | retained E2E | temp vault | passed | `test_obsidian_connector_flow` |
| Tistory | — | — | not affected | non-goal; no code change |
| Live Notion/GitHub/Obsidian | — | live | blocked/gated | needs explicit approval; nearest substitute = fake/temp E2E above |

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Isolated worktree from origin/main | `feature/non-notion-fetch-skip` @ `ff45c8b` |
| Main refresh | completed | FF to latest origin/main before impl; RED WIP restored | HEAD was `5a7efab` (= origin/main); stash pop clean |
| Plan | completed | This document | `docs/plan/2026-07-31-non-notion-fetch-skip.md` |
| Improvement delta declare | completed | Body-fetch counts on second sync for unchanged docs | metrics table above |
| Improvement baseline | completed | Pre-skip always-fetch counts from RED/architecture | 1 read/GET per unchanged item |
| TDD RED | completed | Obsidian/GitHub unit+integration+E2E fail for missing skip | exit `1`; **32 failed**, 3 passed (existing Notion reuse) |
| TDD GREEN | completed | Storage + Obsidian/GitHub skip + wiring + architecture | focused suite **41 passed** |
| Refactor | completed | No further simplification; skip helpers mirror Notion intentionally | focused suite rerun **41 passed** |
| Neighboring mock fix | completed | Existing Obsidian connector fakes accept loader kwargs | 2 previously-red mocks fixed; focused green |
| verify_all | completed | Full suite green | `./scripts/verify_all.sh` exit `0`; functional E2E later extended to 57 |
| Eval | n/a | no retrieval quality change; full-suite eval layer ran inside verify_all | verify_all deterministic quality eval layer |
| Improvement after/delta | completed | Body fetches → 0 on unchanged second sync | delta table above (−100% per unchanged item) |
| Functional smoke | completed | Fake/temp Obsidian+GitHub skip + Notion regression | matrix above; `./scripts/verify_functional_e2e.sh` **57 passed** |
| Harness review pass 1 | completed | R2+R3 clean; R1 High title/line_start | R1 actionable; R2/R3 `NO ACTIONABLE FINDINGS` |
| Review fix RED | completed | title + line_start survive Obsidian skip | exit `1`; **5 failed**; reuse `title=""`; skip→stem; E2E frontmatter wipe |
| Review fix GREEN | completed | reuse SELECT title + chunk MIN(line_start) | focused 5 passed; skip/reuse suite **44 passed** |
| Review fix mypy | completed | typed `line_start` helper for mypy | `uv run --locked mypy` clean |
| Review fix verify_all | completed | Full suite green after title/line_start fix | `./scripts/verify_all.sh` exit `0`; functional E2E **58 passed** |
| Eval | n/a | no retrieval quality change; full-suite eval in verify_all | verify_all quality eval layer |
| Improvement after/delta | completed | unchanged (body-fetch 1→0 still holds) | same delta table; title/line_start fidelity fix |
| Functional smoke (post-fix) | completed | Obsidian frontmatter title/line_start E2E + skip flows | `verify_functional_e2e.sh` **58 passed** |
| Harness review pass 2 | completed | R2 clean; R1 tests + R3 query fixed | R3 index+JOIN; R1 unit/integration line_start GREEN |
| Pass2 fix verify | completed | Focused + verify_all after R1/R3 fixes | verify_all exit `0`; E2E **58**; mypy clean |
| Improvement after/delta | completed | unchanged (body-fetch 1→0) | delta table still valid post-fix |
| Functional smoke (post-fix) | completed | skip flows + frontmatter E2E | functional E2E **58 passed** |
| Harness review pass 3 | completed | All three lenses clean | R1/R2/R3 `NO ACTIONABLE FINDINGS` |
| PR delivery | completed | commit, push, main-base PR | `a03254e`; https://github.com/eunaverse/MCPContentSearch/pull/95 |
