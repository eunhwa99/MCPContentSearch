# Restore PR #25 Obsidian Connector Plan

## User Request

Restore the feature from PR #25 on top of the current `main`, because PR #26's
slim MCP core merge removed the Obsidian connector entirely.

Target PR #25:

- `https://github.com/eunhwa99/MCPContentSearch/pull/25`
- merged commit: `01447e9`
- source branch head: `db3cef9`

## Branch Preflight Result

- Starting worktree:
  `/Users/eunhwa/.codex/worktrees/c298/MCPContentSearch`.
- Starting state: dirty `feature/slim-mcp-core` with an unstaged
  `environments/token.py` change. This file is sensitive and was not read,
  switched, reset, or modified.
- Ran `git fetch origin main`; `origin/main` advanced from PR #25's merge commit
  `01447e9` to PR #26's merge commit `226d25d`.
- Created isolated worktree and branch from the updated `origin/main`:
  `/Users/eunhwa/.codex/worktrees/restore-obsidian-pr25/MCPContentSearch` on
  `feature/restore-obsidian-pr25`.
- Current state before non-plan target edits:
  `## feature/restore-obsidian-pr25...origin/main`.

## Scope and Non-Goals

In scope:

- Reintroduce the Obsidian source connector from PR #25 as a retained slim MCP
  source:
  - `source_obsidian`
  - `CONTEXTWIKI_OBSIDIAN_VAULT_PATH`
  - local `.md` vault crawling with frontmatter title parsing
  - `obsidian://open` canonical URLs
  - symlink-defensive note reads
  - incomplete-snapshot protection so stale cleanup does not tombstone active
    documents after unreadable notes or traversal errors
- Preserve PR #26's slim product direction:
  - no generic website/docs crawler
  - no Auto Wiki
  - no Web Console
  - no legacy live-search/indexing MCP tools
- Restore or adapt deterministic Obsidian tests using temporary vaults only.
- Update README, architecture docs, ADRs, and
  `docs/contextwiki-core-understanding.md` so Obsidian is documented as a
  retained source beside GitHub, Notion, and Tistory.
- Keep existing MCP tool names and parameters stable:
  `list_sources`, `sync_source`, `get_sync_status`, `search_context`,
  `fetch_context`, and `answer_with_citations`.

Non-goals:

- No live Obsidian app integration, file writes into a user's vault, or user
  vault indexing during verification.
- No deletion, reset, migration, or inspection of local user ChromaDB or SQLite
  metadata.
- No restoration of website/docs, dynamic web fallback, wiki generation,
  browser UI, web console tests, or browser smoke scripts.
- No live Notion, Tistory, GitHub, Obsidian, or LLM validation.

## Acceptance Criteria

- The production source registry lists GitHub, Notion, Tistory, and Obsidian.
- `SourceType.OBSIDIAN` exists and SQLite source rows for `source_obsidian` can
  be loaded without being filtered as unsupported.
- When `CONTEXTWIKI_OBSIDIAN_VAULT_PATH` is unset or invalid,
  `source_obsidian` appears disabled with a public, non-secret reason.
- When a temp vault is configured, `sync_source("source_obsidian")` indexes only
  bounded `.md` notes, skips hidden/Obsidian metadata directories, preserves
  frontmatter-derived titles, and emits `obsidian://` canonical URLs.
- Obsidian sync supports stale cleanup only after a complete snapshot. If any
  note cannot be safely read, a traversal/root safety check fails, or the
  configured max file count/byte bounds are exceeded, sync fails and does not
  tombstone missing active documents.
- `CONTEXTWIKI_OBSIDIAN_MAX_FILES` and
  `CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES` load from environment/default config and
  must be positive integers.
- MCP public payloads redact unsafe auth/error text and expose only approved
  config-style Obsidian errors.
- Functional smoke covers retained MCP sync/list/status/search/fetch/answer
  behavior and includes an Obsidian temp-vault path.
- ADR 0006 and architecture docs make clear that the slim core now retains four
  configured sources: GitHub, Notion, Tistory, and Obsidian.

## Worker Ownership Plan

Delegation is available through `multi_agent_v1`. This restoration is not
atomic, so role-specific workers must run before non-plan target edits. Workers
must not commit, push, open PRs, inspect secrets, mutate local Chroma/SQLite
data, or revert other user/agent changes.

- Worker A, runtime connector restoration:
  - Owns `core/models.py`, `fetching/obsidian.py`, `fetching/connectors.py`,
    `environments/config.py`, `storage/metadata_store.py`,
    `indexing/ingestion_service.py`, and any small compatibility imports.
  - Must preserve PR #26 source filtering/redaction behavior and avoid broad
    reverts.
- Worker B, tests and functional gate:
  - Owns `tests/e2e/test_obsidian_connector_flow.py`,
    `tests/fetching/test_connectors.py`, `tests/api/test_tools_contract.py`,
    `tests/storage/test_metadata_store.py`, `tests/environments/test_config.py`,
    `tests/test_app_composition.py`, and `scripts/verify_functional_e2e.sh`.
  - Must use temporary vault directories and must not inspect or mutate a real
    vault or local Chroma/SQLite data.
- Worker C, documentation and ADR alignment:
  - Owns `README.md`, `.agents/docs/architecture.md`,
    `.agents/docs/adr/README.md`, `.agents/docs/adr/0004-contextwiki-phase-b-connectors.md`,
    `.agents/docs/adr/0006-slim-mcp-core-scope.md`,
    `docs/contextwiki-core-understanding.md`, and this plan progress log.
  - Must keep Obsidian restored while website/docs, Auto Wiki, and Web Console
    remain out of scope.
- Main agent:
  - Owns integration, conflict resolution, final diff inspection, verification,
    functional smoke matrix, five-reviewer loop routing, staging, commit, push,
    and PR creation.

## Test and Verification Plan

Focused commands:

```bash
python -m compileall api core environments fetching indexing search storage main.py
uv run --locked pytest -q tests/environments/test_config.py tests/fetching/test_connectors.py tests/storage/test_metadata_store.py
uv run --locked pytest -q tests/api/test_tools_contract.py tests/test_app_composition.py
uv run --locked pytest -q tests/e2e/test_obsidian_connector_flow.py
```

Broader commands:

```bash
./scripts/verify_functional_e2e.sh
./scripts/verify_all.sh
git diff --check
```

Fallback:

- If `uv` is unavailable or dependency metadata is unhealthy, record the failure
  and run dependency-free compile/import checks where useful.

## Functional Smoke Matrix

| Feature or workflow | Caller surface | Safe data mode | Expected result | Planned result |
| --- | --- | --- | --- | --- |
| MCP app startup/import | `create_app()` / tool contract tests | local repo only, no live credentials | retained tools register with four source ids | passed: `tests/test_app_composition.py` and compile checks passed |
| Source list/status | MCP handlers via tests | temp SQLite / fake registry | GitHub, Notion, Tistory, Obsidian are public retained sources | passed: tool contract and app composition tests assert four retained source ids |
| Obsidian disabled state | `list_sources` / config tests | no vault env | disabled source has public non-secret reason | passed: config, connector, MCP status, and app composition tests cover unset/invalid vault behavior |
| Obsidian source sync | `sync_source("source_obsidian")` / E2E | temporary vault and temp metadata/vector state | `.md` notes index with stable identity and citations | passed: temp-vault Obsidian E2E indexes notes, frontmatter title, line metadata, and `obsidian://` citations |
| Obsidian stale cleanup safety | E2E / storage assertions | temporary vault with incomplete snapshot substitute | failed/incomplete snapshot does not tombstone active docs | passed: incomplete/removed vault E2E keeps active docs/chunks and records failed sync |
| Obsidian bounded snapshot safety | `sync_source("source_obsidian")` / E2E | temporary vault and temp metadata/vector state | file count/byte bounds fail sync before stale cleanup | passed after remediation: file-limit E2E keeps active docs/chunks and records failed sync |
| Obsidian root symlink safety | fetcher unit regression | temporary symlinked root only | root fd open rejects symlink roots instead of following them | passed after remediation: no-follow/lstat-fstat root check raises before reading |
| Context search/fetch/answer | MCP/service tests | temporary metadata/vector state | active Obsidian chunks hydrate and cite through SQLite gates | passed: Obsidian E2E covers `search_context`, `fetch_context`, and `answer_with_citations` |
| Removed website/docs/wiki/web UI | import/docs checks | no user data | removed surfaces stay absent | passed: no website/docs, Auto Wiki, Web Console, dynamic fallback, or legacy tools restored in runtime |
| Live external APIs and real vault | live services / user data | approval required | not run without explicit approval | blocked/gated |
| Local user Chroma/SQLite mutation | user data | approval required | not touched | blocked/gated |

## Architecture and ADR Constraints

- ADR 0001 still applies: MCP contracts stay in `api`, source fetching in
  `fetching`, indexing in `indexing`, retrieval in `search`, config in
  `environments`, and composition in `main.py`.
- ADR 0002 still applies: SQLite remains the metadata/citation source of truth
  beside Chroma, and raw secrets are not persisted.
- ADR 0003 still applies: document identity, tombstones, source-aware chunking,
  and SQLite active chunk hydration remain intact.
- ADR 0004 remains GitHub-focused but should acknowledge Obsidian as a retained
  local-file connector if docs mention the full current source set.
- ADR 0006 is directly affected: update it to retain `source_obsidian` while
  keeping website/docs, Auto Wiki, Web Console, dynamic web fallback, and legacy
  live-search tools removed.

## Risks and Rollback Notes

- Risk: blindly restoring PR #25 would bring back removed web console/wiki
  references from the pre-slim tree. Mitigation: cherry-pick concepts/files
  selectively and keep PR #26 slim boundaries.
- Risk: disabled/error messages could expose local paths or secret-like values.
  Mitigation: keep current public payload redaction and allow-list only stable
  Obsidian config messages.
- Risk: stale cleanup can delete active metadata after an incomplete filesystem
  snapshot. Mitigation: Obsidian connector must fail incomplete snapshots before
  successful stale cleanup finalization.
- Rollback: revert this branch/PR. No local data migration or destructive
  cleanup is part of this change.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Fetched latest `origin/main`, preserved dirty starting worktree, and created an isolated restoration branch from PR #26 main. | `git fetch origin main`; `git worktree add -b feature/restore-obsidian-pr25 ... origin/main` |
| Plan | completed | Created this plan with scoped Obsidian restoration, worker boundaries, ADR constraints, and temp-data verification. | `docs/plan/2026-06-11-restore-obsidian-pr25.md` |
| Subagent discovery | completed | Delegation tooling is available for worker orchestration. | `tool_search` exposed `multi_agent_v1` |
| Worker implementation | completed | Worker A restored runtime connector/config/metadata behavior, Worker B restored temp-vault tests and functional gate coverage, and Worker C aligned docs/ADR with Obsidian as a retained slim source. | Worker reports; changed runtime, tests, scripts, and docs |
| Integration | completed | Fixed the ad-hoc sync regression caused by setting `sources.enabled = 1` during successful sync finalization, added direct `pyyaml` runtime dependency, refreshed `uv.lock`, and added symlink regressions. | `tests/indexing/test_ingestion_service.py::test_ingestion_can_skip_source_config_registration_for_ad_hoc_sync` failed then passed; `uv lock --check` passed |
| Focused verification | completed | Compile, focused connector/config/storage/API/app/Obsidian E2E tests, lock check, and whitespace checks passed. | `python -m compileall api core environments fetching indexing search storage main.py`; focused pytest groups -> 82 passed and 21 passed; `uv lock --check`; `git diff --check` |
| Functional smoke | completed | Functional gate now covers Obsidian E2E along with retained MCP/source/search/answer workflows. | `./scripts/verify_functional_e2e.sh` -> 282 passed |
| Full verification | completed | Full verification passed after restoration and integration fixes. | `./scripts/verify_all.sh` -> compile, Ruff, mypy, Bandit, 472 non-live tests with 85.74% coverage, and functional gate 282 passed |
| Review pass 1 | completed/actionable | Five fresh reviewers found bounded-vault and root-symlink-race gaps in Obsidian sync plus a docs/runtime mismatch for Obsidian document identity. Runtime should add max files/bytes limits and no-follow root safety; docs should align identity to the existing relative-path contract. | Reviewers: Wegener, Hilbert, Ptolemy, Copernicus, Harvey |
| Review remediation 1 | completed | Added RED tests for Obsidian max file/file-byte bounds, root no-follow safety, and bounded-snapshot stale-cleanup preservation; implemented config/runtime/docs fixes while keeping raw relative-path Obsidian document identity. | RED `uv run --locked pytest -q tests/environments/test_config.py tests/fetching/test_connectors.py tests/e2e/test_obsidian_connector_flow.py` -> 15 failed, 36 passed; GREEN same command -> 51 passed; `python -m compileall api core environments fetching indexing search storage main.py` passed; `git diff --check` passed |
| Review remediation integration | completed | Added a main-agent fd-level byte-limit recheck after opening note files, with a direct regression, so a file growing between traversal stat and open cannot be read past the configured bound. | `uv run --locked pytest -q tests/environments/test_config.py tests/fetching/test_connectors.py tests/e2e/test_obsidian_connector_flow.py` -> 52 passed |
| Post-remediation verification 1 | completed | Reran compile, dependency lock check, focused tests, whitespace checks, functional smoke, and full verification after remediation and integration follow-up. | `python -m compileall api core environments fetching indexing search storage main.py`; `uv lock --check`; `git diff --check`; `./scripts/verify_functional_e2e.sh` -> 289 passed; `./scripts/verify_all.sh` -> compile, Ruff, mypy, Bandit, 487 non-live tests with 85.61% coverage, functional gate 289 passed |
| Review pass 2 | completed/actionable | Fresh reviewer pass found bounded-read, ambient Obsidian env, and ranking source-intent gaps. | Main remediation assignment for pass 2 |
| Review remediation 2 | completed | Enforced Obsidian byte limits with bounded fd reads, isolated Obsidian env in verification/default-disabled tests, and restored Obsidian source-type ranking terms and canonical id matching. | `uv run --locked pytest -q tests/fetching/test_connectors.py tests/test_app_composition.py tests/search/test_ranking.py` -> 21 passed; `python -m compileall api core environments fetching indexing search storage main.py`; `git diff --check` |
| Post-remediation verification 2 | completed | Reran focused Obsidian/config/ranking tests, compile, lock, whitespace, functional smoke, and full verification after pass-2 remediation. | Focused suite -> 56 passed; `python -m compileall api core environments fetching indexing search storage main.py`; `uv lock --check`; `git diff --check`; `./scripts/verify_functional_e2e.sh` -> 290 passed; `./scripts/verify_all.sh` -> compile, Ruff, mypy, Bandit, 490 non-live tests with 85.67% coverage, functional gate 290 passed |
| Review pass 3 | completed/actionable | Fresh reviewers found that visible symlinked Obsidian notes/directories could be skipped as missing while still allowing successful stale cleanup, which can tombstone active documents. | Reviewers: Planck, Helmholtz, Banach, Plato, Aristotle |
| Review remediation 3 | completed | Added RED regressions for an active note replaced by a symlink plus visible symlinked note/directory snapshots, then marked visible unsafe Obsidian note and directory entries as incomplete snapshots before stale cleanup. | RED targeted symlink tests -> 3 failed as expected; GREEN same tests -> 3 passed; focused Obsidian suite -> 25 passed; `python -m compileall api core environments fetching indexing search storage main.py` passed; `git diff --check` passed |
| Post-remediation verification 3 | completed | Reran focused Obsidian/config/ranking tests, compile, lock, whitespace, functional smoke, and full verification after pass-3 remediation. | Focused suite -> 59 passed; `python -m compileall api core environments fetching indexing search storage main.py`; `uv lock --check`; `git diff --check`; `./scripts/verify_functional_e2e.sh` -> 293 passed; `./scripts/verify_all.sh` -> compile, Ruff, mypy, Bandit, 493 non-live tests with 85.69% coverage, functional gate 293 passed |
| Review pass 4 | completed/actionable | Fresh reviewers found stale MCP source status after vault removal, E2E ambient Obsidian env leakage, symlinked ancestor vault escape, and Korean Obsidian source-intent retrieval gaps. | Reviewers: Curie, Lorentz, Halley, James, Leibniz |
| Review remediation 4 | completed | Fixed stale MCP source status refresh, E2E ambient Obsidian env leakage, symlinked ancestor vault escape, and Korean Obsidian source-intent retrieval. | RED env command -> 2 failed, 23 passed; RED symlink ancestor tests -> 2 failed; GREEN API tests -> 18 passed; GREEN search tests -> 118 passed; GREEN Obsidian env/filesystem tests -> 27 passed; focused suite -> 61 passed |
| Post-remediation verification 4 | completed | Reran compile, mypy, lock, ruff scoped check, whitespace, functional smoke, and full verification after pass-4 remediation. | `python -m compileall api core environments fetching indexing search storage main.py`; `uv run --locked mypy`; `uv lock --check`; `git diff --check`; scoped Ruff passed; `./scripts/verify_functional_e2e.sh` -> 297 passed; `./scripts/verify_all.sh` -> compile, Ruff, mypy, Bandit, 497 non-live tests with 85.90% coverage, functional gate 297 passed |
| Review pass 5 | completed/actionable | First two reviewers found disabled Obsidian refresh could still preserve stale source-level `sync_status=succeeded` after vault removal. | Reviewers: Avicenna, Sagan |
| Review remediation 5 | completed | Disabled runtime source refresh now sets source-level `sync_status=failed` when a public disabled reason is registered, while preserving prior `last_synced_at`; regression covers succeeded source -> disabled refresh. | RED dynamic registry state test failed with stale `succeeded`; GREEN dynamic registry state test -> 1 passed; focused API/storage/Obsidian/search suite -> 210 passed |
| Post-remediation verification 5 | completed | Reran compile, mypy, whitespace, functional smoke, and full verification after pass-5 remediation. | `python -m compileall api core environments fetching indexing search storage main.py`; `uv run --locked mypy`; `git diff --check`; `./scripts/verify_functional_e2e.sh` -> 297 passed; `./scripts/verify_all.sh` -> compile, Ruff, mypy, Bandit, 497 non-live tests with 85.90% coverage, functional gate 297 passed |
| Review pass 6 | completed/actionable | Four reviewers reported no actionable findings; final reviewer found source-intent fallback can be skipped when vector results already fill `top_k`. | Reviewers: Laplace, Tesla, Pascal, Galileo, Peirce |
| Review remediation 6 | completed | Added RED regression for filled-vector Korean Obsidian source-intent fallback and allowed source-intent fallback to run even when vector candidates already fill `top_k`. | RED source-intent test -> 1 failed, 1 passed; GREEN source-intent tests -> 2 passed; focused search suite -> 119 passed |
| Post-remediation verification 6 | completed | Reran compile, mypy, whitespace, functional smoke, and full verification after pass-6 remediation. | `python -m compileall api core environments fetching indexing search storage main.py`; `uv run --locked mypy`; `git diff --check`; `./scripts/verify_functional_e2e.sh` -> 298 passed; `./scripts/verify_all.sh` -> RC=0, 498 non-live tests with 85.90% coverage, functional gate 298 passed |
| Review pass 7 | completed/actionable | First two reviewers ran per the two-at-a-time request; reviewer 1 found that `answer_with_citations("옵시디언")` dropped source-intent Obsidian results because answer grounding did not use source-type aliases. The remaining stale pass was stopped so remediation could happen before a fresh five-reviewer pass. | Reviewers: Linnaeus, Bernoulli |
| Review remediation 7 | completed | Added RED answer-level regression for Korean Obsidian source intent and made answer grounding/debug matched terms source-type-aware using the ranking source alias mapping. | RED answer test -> 1 failed; GREEN answer test -> 1 passed; focused answer/ranking/context suite -> 157 passed |
| Post-remediation verification 7 | completed | Reran compile, mypy, whitespace, functional smoke, and full verification after pass-7 remediation. | `python -m compileall api core environments fetching indexing search storage main.py`; `uv run --locked mypy`; `git diff --check`; `./scripts/verify_functional_e2e.sh` -> 299 passed; `./scripts/verify_all.sh` -> RC=0, 499 non-live tests with 85.94% coverage, functional gate 299 passed |
| Review pass 8 | completed/clean | Five fresh reviewers reported no actionable findings after pass-7 remediation. Reviewers were spawned in batches of at most two concurrent subagents per user request. | Reviewers: Dewey, Raman, Hegel, Pauli, Heisenberg |
