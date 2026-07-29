# GitHub Owner Target Sync

## User Request

Allow `CONTEXTWIKI_GITHUB_REPOSITORIES` entries to express either:

- a GitHub owner such as `eunaverse`, which discovers and syncs every repository
  owned by that account and visible to the configured GitHub credentials; or
- an exact repository such as `eunaverse/example` or
  `eunaverse/example@ref`, which syncs only that repository.

## Branch Preflight

- The original worktree at
  `/Users/eunhwa/IdeaProjects/MCPContentSearch` is dirty on
  `feature/wait-for-sync-all`; its existing tracked and untracked changes are
  preserved without switching, pulling, cleanup, or target edits.
- `git fetch origin main` completed successfully.
- This work runs in the isolated worktree
  `/Users/eunhwa/IdeaProjects/MCPContentSearch-github-owner-targets` on fresh
  branch `feature/github-owner-targets`, created from `origin/main`.
- At creation, `git rev-list --left-right --count HEAD...origin/main` returned
  `0 0`.
- Existing local branches and linked/prunable worktrees are left untouched
  because their local-only and ownership state is outside this task.

## Scope

- Accept owner-only targets in the existing comma/newline-separated
  `CONTEXTWIKI_GITHUB_REPOSITORIES` setting.
- Resolve owner targets during the GitHub source fetch using the existing
  authenticated/public GitHub repository discovery boundary.
- Preserve exact repository target behavior, including explicit refs and the
  configured default ref.
- Keep repository identity, document identity, indexing, MCP tool parameters,
  and public response shapes stable.
- Derive GitHub stale-cleanup prefixes from the repositories actually resolved
  for the completed fetch.
- Document owner-versus-repository behavior and restart/sync expectations.

## Non-Goals

- Do not add a new MCP tool or change existing tool signatures.
- Do not add a Web Console, interactive repository picker, or persisted dynamic
  source configuration.
- Do not inspect, delete, migrate, or rewrite user Chroma/SQLite data.
- Do not run live GitHub, embedding-provider, Notion, Tistory, or Obsidian
  validation.
- Do not change historical document cleanup outside repository prefixes safely
  resolved by the current successful GitHub snapshot.

## Acceptance Criteria

1. A bare owner target such as `eunaverse` discovers all owned repositories
   visible through the configured GitHub access and fetches each repository at
   its API-reported default branch.
2. An exact target such as `eunaverse/example` or
   `eunaverse/example@release` bypasses owner-list discovery and fetches only
   that repository with the expected ref.
3. Mixed owner and exact targets resolve deterministically and reject
   case-insensitive duplicate repository identities rather than fetching the
   same repository ambiguously.
4. Invalid owner/repository targets and GitHub failures remain redacted and
   cause a truthful failed sync; incomplete snapshots never enable stale
   cleanup.
5. After a complete successful owner sync, cleanup is limited to exact
   repository prefixes resolved for that snapshot. Repositories outside those
   prefixes, including historical/private repositories absent from the current
   discovery result, are not tombstoned.
6. Existing exact-repository configuration and connector tests remain green.
7. A retained fake/temp MCP E2E scenario proves that
   `sync_source("source_github")` with an owner target discovers multiple
   repositories, reaches terminal success, and persists searchable/citeable
   repository documents without live credentials or user data.
8. README and maintained architecture documentation explain the new target
   semantics, credential visibility, default-branch behavior, safety bounds,
   and restart requirement.

## Ordered Steps

1. `github-owner-runtime`
   - Read `fetching/github.py`, `fetching/connectors.py`, and existing GitHub
     parser/discovery tests.
   - Integrate owner discovery into the configured source fetch path.
   - Preserve exact-repository off-network resolution and update cleanup
     prefixes only from the resolved fetch scope.
2. `github-owner-tests`
   - Add focused unit/connector coverage for owner-only, exact-only, mixed,
     duplicates, credentials, default branches, failures, and cleanup scope.
   - Add or extend retained FastMCP E2E coverage using fake GitHub responses,
     temp SQLite, and test-local embeddings/index storage.
3. `github-owner-docs`
   - Update README configuration/examples/troubleshooting.
   - Update `.agents/docs/architecture.md` so owner discovery and cleanup
     constraints remain the maintained design.
4. `integration`
   - Inspect and reconcile worker diffs.
   - Run focused tests, compile checks, functional smoke, and full verification.
   - Route actionable findings back to the responsible worker boundary.
5. `review-delivery`
   - Run fresh five-reviewer passes until the newest pass is fully clean.
   - Rerun affected verification after fixes.
   - Commit, push, and create a `main`-base PR.

## Worker Ownership

| Worker | Owned files/modules | Acceptance and verification |
| --- | --- | --- |
| Runtime implementation | `fetching/github.py`, `fetching/connectors.py` | Implement owner/exact resolution and safe dynamic cleanup scope; run focused import or existing GitHub unit tests without editing tests/docs. |
| Test implementation | `tests/fetching/test_github.py`, `tests/fetching/test_connectors.py`, task-relevant `tests/e2e/` files | Add deterministic fake/temp coverage, including a retained MCP caller path; do not edit runtime/docs. |
| Documentation | `README.md`, `.agents/docs/architecture.md` | Explain behavior and safety constraints; run `git diff --check` for owned docs. |

Workers share the task branch but have disjoint ownership. They must preserve
other user/agent changes, must not commit/push/open PRs, inspect secrets, call
live providers, inspect or mutate user Chroma/SQLite data, or revert changes
outside their boundary.

## Files Likely to Change

- `fetching/github.py`
- `fetching/connectors.py`
- `tests/fetching/test_github.py`
- `tests/fetching/test_connectors.py`
- `tests/e2e/test_phase_b_connectors_flow.py`
- `README.md`
- `.agents/docs/architecture.md`
- `docs/plan/2026-07-29-github-owner-target-sync.md`

`environments/config.py` should remain unchanged unless implementation proves a
bounded configuration requirement cannot be satisfied inside the existing
target parsing/fetching boundary.

## Test and Verification Plan

Focused checks:

```bash
python -m compileall fetching environments indexing api core search storage main.py
uv run --locked pytest -q \
  tests/fetching/test_github.py \
  tests/fetching/test_connectors.py \
  tests/e2e/test_phase_b_connectors_flow.py
```

Broader gates:

```bash
./scripts/verify_functional_e2e.sh
./scripts/verify_all.sh
git diff --check
```

No retrieval-quality evaluation is required because this change affects source
target discovery/fetch configuration rather than ranking, grounding, citation
selection, or answer quality.

## Functional Smoke Matrix

| Inventory row | Caller surface | Safe data mode | Expected result | Planned evidence | Status |
| --- | --- | --- | --- | --- | --- |
| Owner target GitHub sync | Real `FastMCP.call_tool("list_sources")`, `sync_source`, `sync_all`, `wait_for_sync_all`, and terminal `get_sync_status` | Fake GitHub HTTP, temp SQLite/index, local recording indexer | Multiple discovered repos sync successfully with stable repo document ids; bulk wait reuses the exact launched GitHub job | Retained FastMCP owner flows; focused suite `157 passed`; functional wrapper `34 passed` | passed |
| Owner target search/citation | Real FastMCP `search_context` and `fetch_context`, plus retained citation helper | Fake multi-repo GitHub documents and temp stores | Both repos are searchable/fetchable and grounded answer cites the selected chunk after direct and bulk/wait sync paths | Retained owner E2E in `tests/e2e/test_phase_b_connectors_flow.py`; focused suite `143 passed` | passed |
| Exact repository sync compatibility | Connector plus retained MCP regression path | Fake GitHub HTTP and temp state | Exact repo bypasses owner-list endpoint and preserves configured/default ref | Focused connector/E2E suite `137 passed` | passed |
| Authenticated owner visibility | Discovery connector unit path | Fake public and authenticated GitHub list responses | Public plus token-visible target-owned repositories included even when the target differs from the token user; other-owner repos and token text excluded; overlapping observations deduplicate | Authenticated owner discovery/fetch regressions; focused suite `137 passed` | passed |
| Owner with empty and populated repositories | Connector plus retained FastMCP path | Fake discovery metadata, empty repo, populated repo, temp stores | Empty repo is a complete zero-document scope; populated repo sync/search succeeds; exact empty prefix may clean stale data | Retained FastMCP empty/populated owner regression; focused suite `137 passed` | passed |
| Multi-page and valid mixed targets | Discovery/fetcher unit path | Fake page-1-full/page-2-partial responses and non-overlapping owner+exact targets | All pages resolve; valid mixed targets fetch once each with API default/explicit refs | Pagination and mixed-target regressions; focused suite `137 passed` | passed |
| Mixed/duplicate target safety | Connector unit path | Fake GitHub HTTP | Case-insensitive duplicate fails truthfully and cleanup remains disabled | Focused suite `137 passed` | passed |
| GitHub failure/incomplete/pagination snapshot | Connector and ingestion tests | Failing, truncated, ambiguous-empty, and 100-full-page fake responses | Failed or incomplete discovery/fetch cannot enable cleanup or claim completeness | Focused suite `137 passed` | passed |
| `sync_all` compatibility and source status | Retained FastMCP E2E/tests | Existing fake/temp sources and metadata, including real owner connector with fake GitHub | Bulk launch/wait/status and source health behavior remains green; owner discovery composes through the exact launched GitHub job | Functional wrapper `34 passed`; full non-live suite `756 passed` | passed |
| `search_documents` and other retained retrieval | Retained functional tests and deterministic eval | Existing fake/temp indexed fixtures | Existing grouped document search, fetch, and answer behavior remains green | Functional wrapper `34 passed`; retrieval eval `13/13`; answer eval `9/9` | passed |
| Other retained source tools | Functional E2E wrapper | Existing fake/temp Notion, Tistory, Obsidian paths | Existing sync/search/fetch/status behavior remains green | Functional wrapper `34 passed` | passed |
| Storage lifecycle and cleanup scope | Temp SQLite/index connector E2E and focused tests | Temporary stores only | Resolved repositories persist; exact prefixes are exposed; incomplete or metadata-conflicted snapshots do not tombstone | Focused suite `143 passed`; full non-live suite `742 passed` | passed |
| Owner cleanup two-cycle lifecycle | Temp `IngestionService`/SQLite/index | Resolved populated + empty repo, absent historical/private repo, incomplete follow-up | Resolved stale/empty repo docs tombstone only after complete sync; absent/incomplete-snapshot docs remain active | Retained temp-store lifecycle regression; focused suite `137 passed` | passed |
| Live configured GitHub owner sync | Live GitHub and real local user stores | Approval-gated external/user-data mode | Not required for delivery | Blocked: no explicit live mutation approval; nearest substitute passed through fake/temp FastMCP owner E2E | blocked/gated |

## Architecture Constraints

- GitHub target parsing and discovery remain in `fetching`; `api/tools.py` and
  MCP contracts should not acquire GitHub-specific shortcuts.
- Document ids remain `github:<normalized-owner>/<normalized-repo>:<path>`.
- Stale cleanup remains repository-prefix scoped and runs only after a complete
  successful snapshot.
- Public/private repository visibility follows GitHub API access through the
  optional token, without logging or persisting token values.
- Sync/status truthfulness and existing background-job behavior remain
  unchanged.
- Tests use fake GitHub responses and temporary stores; no user data is read or
  mutated.

## Risks and Rollback

- Owner accounts can contain many repositories, increasing GitHub requests,
  indexing time, and embedding cost. The implementation must retain existing
  file/byte bounds and fail truthfully rather than claim a complete snapshot
  after partial discovery or fetch.
- Authentication changes the visible repository set. Cleanup must not use a
  broad owner prefix because losing token visibility could otherwise tombstone
  private repository documents.
- Mixed owner and exact targets may overlap. Duplicate repository identity must
  be handled deterministically before indexing.
- A repository may use a non-`main` default branch; owner discovery must retain
  GitHub's `default_branch`, while exact targets retain current default/explicit
  ref semantics.
- Rollback is the feature commit/PR. No schema migration or user-data rewrite is
  part of this work.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Preserved dirty original worktree and created fresh isolated task branch from fetched `origin/main`. | `git status --short --branch`; `git fetch origin main`; `git worktree add`; ahead/behind `0 0` |
| Planning | completed | Defined owner/exact target semantics, worker boundaries, verification, smoke, data safety, and rollback. | This plan |
| Runtime implementation | completed | Bare-owner discovery, exact-target compatibility, pagination completeness, duplicate safety, and dynamic cleanup scope implemented. | Worker `/root/github_owner_runtime`; runtime compile/Ruff/mypy passed |
| Test implementation | completed | Added focused connector coverage and retained FastMCP multi-repository owner E2E. | Worker `/root/github_owner_tests`; targeted suite `127 passed`; Ruff passed |
| Documentation | completed | README and maintained architecture describe owner/exact targets, visibility, restart, cost, and cleanup safety. | Worker `/root/github_owner_docs`; docs diff check passed |
| Integration finding: authenticated visibility | completed | Authenticated discovery now enumerates token-accessible repositories and filters exact target ownership; regression covers target-owned private organization repo and token safety. | Focused GitHub tests `90 passed`; official GitHub REST affiliation semantics |
| Focused verification | completed | Re-ran focused GitHub/connector/FastMCP tests and static checks after remediation and main integration. | `137 passed`; Ruff, mypy, compile, and `git diff --check` passed |
| Functional smoke | completed | Re-ran retained fake/temp MCP and source-sync smoke after remediation and main integration. | `./scripts/verify_functional_e2e.sh` -> `33 passed` |
| Full verification | completed | Static, MCP contract, non-live regression, deterministic evaluation, and functional E2E layers all passed on integrated main. | `20` public contracts; `736` non-live tests; 87.89% coverage; retrieval `13/13`; answer `9/9`; E2E `33 passed` |
| Review pass 1 | completed/actionable | Five fresh read-only reviewers found empty-repository source-wide failure, missing owner cleanup lifecycle/happy pagination/valid mixed tests, and missing README file/byte-limit semantics. Security lens was clean. | Reviewers: fetching, cleanup, security, test/smoke, docs/UX |
| Review remediation 1 | completed | Confirmed-empty repositories are zero-document complete scopes; tests cover empty/populated, cleanup lifecycle, pagination, valid mixed targets, and conservative ambiguous metadata; docs record bounded content/incomplete cleanup semantics. | Focused suite `137 passed`; Ruff, mypy, compile, and diff check passed |
| Main refresh integration | completed | Fast-forwarded onto PR #83 (`wait_for_sync_all`) and restored the owner-target work. Git auto-merged README/architecture without conflict markers; both features remain documented. Added cross-account authenticated owner coverage after integration review. | `git merge --ff-only origin/main`; `git stash pop`; ahead/behind `0 0`; focused suite `137 passed` |
| Review pass 2 | completed/actionable | Five fresh read-only reviewers found no runtime, cleanup, security, or test issues. The docs reviewer found missing overlap/duplicate semantics and the cleanup consequence for confirmed-empty repositories. | Reviewers: runtime, cleanup, security, test/smoke, docs/UX |
| Review remediation 2 | completed | README and architecture now clarify non-overlapping mixed targets, duplicate rejection, and exact-prefix cleanup for confirmed-empty repositories while preserving conservative ambiguous-metadata behavior. No runtime or smoke inventory changed. | `git diff --check -- README.md .agents/docs/architecture.md` passed; affected functional smoke: none |
| Review pass 3 | completed/actionable | Five fresh read-only reviewers found runtime, cleanup, security, and docs clean. The test/smoke reviewer found owner discovery was not yet composed through the new main `sync_all` plus `wait_for_sync_all` path. | Reviewers: runtime, cleanup, security, test/smoke, docs/UX |
| Review remediation 3 | completed | Added a retained fake/temp FastMCP owner E2E through `sync_all`, exact-job `wait_for_sync_all`, status, search, fetch, and citation while retaining direct `sync_source` coverage. | Focused suite `138 passed`; functional wrapper `34 passed`; full suite `737 passed`, retrieval `13/13`, answer `9/9` |
| Review pass 4 | completed/actionable | Five fresh read-only reviewers found a cleanup-safety edge: duplicate public/auth observations could disagree on default branch, and non-empty owner metadata could omit the API default branch. Other lenses were clean. | Reviewers: runtime, cleanup, security, test/smoke, docs/UX |
| Review remediation 4 | completed | Runtime fails closed on conflicting non-empty default branches and missing/invalid non-empty owner default-branch metadata; empty-to-populated duplicates adopt the populated API branch. Tests cover cleanup disablement and exact-target compatibility. | Focused suite `143 passed`; functional wrapper `34 passed`; full non-live suite `742 passed`; retrieval `13/13`; answer `9/9` |
| Review policy override | completed | User explicitly requested that subsequent review gates use three reviewers instead of five. | Current conversation instruction |
| Refactor | completed | Inspected changed runtime/tests against local patterns. No additional refactor reduced real complexity without weakening the explicit metadata-state and lifecycle coverage, so no target edits were made. | `harness-refactor`; focused suite remained `143 passed` |
| Integration | completed | Refreshed `origin/main`, confirmed no drift, rechecked the full diff and functional smoke matrix, and retained the live GitHub/user-store gate. | `git fetch origin main`; ahead/behind `0 0`; `git diff --check`; full verification and smoke passed |
| Three-reviewer pass 1 | completed/actionable | Fresh runtime, test/integration, and security/docs reviewers found malformed list entries were silently discarded and the 100-page-per-endpoint discovery cap was undocumented. Test/integration lens was otherwise clean. | User-overridden three-reviewer gate |
| Review remediation 5 | completed | Runtime fails closed on any malformed repository-list entry; tests prove cleanup remains disabled; README/architecture document the 10,000-item-per-endpoint cap and fail-closed behavior. | Focused suite `144 passed`; functional wrapper `34 passed`; full non-live suite `743 passed`; retrieval `13/13`; answer `9/9` |
| Three-reviewer pass 2 | completed/actionable | Fresh runtime, test/integration, and security/docs reviewers found malformed repository dict identities were still skipped and pages larger than the documented 100-item bound were accepted. | User-overridden three-reviewer gate |
| Review remediation 6 | completed | Runtime validates every repo identity before source-specific owner filtering, rejects public-owner mismatches, preserves authenticated other-owner filtering, and rejects pages over 100 entries; tests cover all fail-closed states. | Focused suite `157 passed`; functional wrapper `34 passed`; full non-live suite `756 passed`; retrieval `13/13`; answer `9/9` |
| Three-reviewer pass 3 | completed/clean | Fresh runtime/cleanup, test/integration, and security/docs reviewers all reported no actionable findings after identity and page-bound remediation. | User-overridden final three-reviewer gate |
| Review | completed | Final fresh three-reviewer pass is fully clean. | Reviewers: runtime/cleanup, test/integration, security/docs |
| Delivery | pending | Commit, push, and `main`-base PR after clean review. | Pending |
