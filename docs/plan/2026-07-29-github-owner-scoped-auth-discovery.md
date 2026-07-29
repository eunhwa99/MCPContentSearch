# Plan: Owner-scoped authenticated GitHub discovery

## User request

Verify and fix: when `GITHUB_TOKEN` is set, owner discovery paginates all of
`/user/repos?visibility=all` and only then filters by the configured owner.
That endpoint can exceed the 10,000-repository cap while the target owner’s
repos were already complete on `/users/{owner}/repos`, so owner-wide sync fails
even for accounts whose owned repositories fit within the documented bounds.

Affected area: `fetching/github.py` `_fetch_owner_repositories` (approx.
600–609). This is a Bugbot finding on PR #84 / branch
`feature/github-owner-targets`.

## Branch preflight result

- Starting worktree: clean.
- Current branch: `feature/github-owner-targets` (open PR #84 against `main`).
- Cloud/task instruction: reuse the current branch; preferred delivery updates
  PR #84 rather than opening a stacked branch.
- `origin/main` fetched; branch remains based on the owner-target feature
  commits (`864d14b`, `60d69f8`). No isolated worktree needed.
- Local non-`main` cleanup skipped because this run continues the existing
  feature branch by explicit cloud/task instruction.

## Scope and non-goals

### In scope

- Replace token-global `/user/repos?visibility=all` owner discovery with
  owner-scoped authenticated listing.
- Prefer `/orgs/{owner}/repos` (type `all`) for organization owners so private
  org repos remain visible to the token without paging unrelated accounts.
- For personal accounts, use `/user/repos?affiliation=owner` only when the
  authenticated login matches the target owner (bounded by that owner’s owned
  repos).
- Treat `/orgs/{owner}/repos` HTTP 404 as “not an organization,” not a hard
  sync failure.
- Update focused fake-HTTP tests and architecture wording for the new
  discovery endpoints.
- Keep fail-closed pagination (100 pages × 100) per endpoint; the cap must
  apply to owner-scoped lists, not the token’s global visibility set.

### Non-goals

- Live GitHub network validation or mutation of user Chroma/SQLite.
- MCP tool contract changes.
- Changing exact `owner/repo` / `owner/repo@ref` target behavior.
- Preserving collaborator-visible private repos owned by a *different* personal
  user when that visibility cannot be obtained from an owner-scoped endpoint
  without global `/user/repos` pagination. Organization private visibility and
  self-owned private visibility remain required.

## Acceptance criteria

1. With a token, owner discovery does not call
   `/user/repos?visibility=all` without an owner-bounding affiliation filter.
2. Organization owner sync still includes token-visible private repositories
   under that owner via `/orgs/{owner}/repos`.
3. When the authenticated user matches the target owner, private self-owned
   repositories remain discoverable via `/user/repos?affiliation=owner`.
4. When `/orgs/{owner}` is not an org (404) and the token user is not the
   target owner, discovery returns the complete `/users/{owner}/repos` public
   list and does not fail due to unrelated global repo volume.
5. Existing fail-closed malformed/oversized/pagination behaviors remain.
6. Focused GitHub tests and functional E2E gate pass with fake/temp data only.
7. Architecture documents owner-scoped authenticated discovery.

## Step breakdown

1. `owner-scoped-runtime` — change `_fetch_owner_repositories` and any minimal
   HTTP 404 helper needed in `fetching/github.py`; update architecture.
2. `owner-scoped-tests` — update fake HTTP fixtures and add regressions proving
   no unfiltered global `/user/repos` paging and org/self owner paths.
3. Verify, functional smoke, review loop, push/update PR #84.

## Files likely to change

- `fetching/github.py`
- `tests/fetching/test_github.py`
- `.agents/docs/architecture.md`
- `docs/plan/2026-07-29-github-owner-scoped-auth-discovery.md`

## Test and verification plan

- Focused: `uv run pytest -q tests/fetching/test_github.py`
- Broader if needed: connector/FastMCP GitHub owner tests already in-tree
- `python -m compileall` / `uv run python -m compileall` on project modules
- `./scripts/verify_functional_e2e.sh` before review
- Docs/architecture whitespace: `git diff --check`

## Functional smoke matrix plan

| Feature / path | Caller surface | Safe data mode | Expected result |
| --- | --- | --- | --- |
| GitHub owner discovery (public) | Fake HTTP unit | Fake responses | `/users/{owner}/repos` complete list |
| GitHub owner discovery (org private) | Fake HTTP unit/connector | Fake `/orgs/{owner}/repos` | Private target-owned repos included |
| GitHub owner discovery (self private) | Fake HTTP unit | Fake `/user` + affiliation=owner | Self private included; no unfiltered visibility=all |
| GitHub owner discovery (other user + token) | Fake HTTP unit | Org 404 + non-matching `/user` | Public-only; no global `/user/repos` exhaustion |
| MCP sync/search retained flows | `./scripts/verify_functional_e2e.sh` | Temp stores / fakes | passed |
| Live GitHub owner sync | N/A | Blocked | blocked/gated — needs explicit approval |

## Architecture constraints

- Fetching owns GitHub connector behavior; keep MCP contracts stable.
- Owner-wide sync remains explicit configuration; bounds remain 100×100 per
  listing endpoint with fail-closed incomplete pages.
- Do not log or persist raw tokens.
- Update `.agents/docs/architecture.md` when discovery endpoints/assumptions
  change.

## Risks and rollback

- Risk: missing collaborator-only private personal repos for another user.
  Accepted to avoid token-global pagination failures; org and self-owned
  private paths remain covered.
- Risk: fake HTTP fixtures must emulate 404 for non-org owners.
- Rollback: revert the fix commit on `feature/github-owner-targets`.

## Functional smoke matrix results

| Feature / path | Caller surface | Safe data mode | Result | Evidence |
| --- | --- | --- | --- | --- |
| GitHub owner discovery (public) | `tests/fetching/test_github.py` | Fake HTTP | passed | Focused suite 121 passed |
| GitHub owner discovery (org private) | Fake HTTP unit/connector | Fake `/orgs/{owner}/repos` | passed | Org path tests + merge connector test |
| GitHub owner discovery (self private) | Fake HTTP unit | Fake `/user` + affiliation=owner | passed | `test_authenticated_self_owned_personal_discovery_uses_affiliation_owner` |
| GitHub owner discovery (other user + token) | Fake HTTP unit | Org 404 + non-matching `/user` | passed | `test_authenticated_non_org_other_user_discovery_stays_on_public_users_list` |
| Retained MCP sync/search/citation E2E | `./scripts/verify_functional_e2e.sh` | Temp stores / fakes | passed | 34 passed |
| Live GitHub owner sync | N/A | Blocked | blocked/gated | Needs explicit approval; nearest substitute is fake-HTTP owner discovery tests |

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Reusing clean `feature/github-owner-targets` for PR #84 Bugbot fix. | `git status --short --branch`; `gh pr view 84` |
| Planning | completed | Confirmed bug at `_fetch_owner_repositories`; designed owner-scoped org/self auth listing. | Issue text; Bugbot thread |
| Implementation | completed | Runtime uses `/orgs/{owner}/repos` and affiliation-scoped self `/user/repos`; tests and architecture updated. | Workers; diff in `fetching/github.py`, `tests/fetching/test_github.py`, architecture |
| Focused verification | completed | Focused GitHub tests and compileall passed. | `119 passed`; `python -m compileall ...` |
| Functional smoke | completed | Retained functional E2E and owner-discovery matrix recorded. | `./scripts/verify_functional_e2e.sh` → `34 passed` |
| Review pass 1 | completed/actionable | Four lenses clean; test-quality found soft self-owned `/user/repos` fixture and missing non-404 org error coverage. | Reviewers: fetching, test, security, architecture, smoke |
| Review remediation 1 | completed | Hardened self-owned `/user/repos` fixture; added non-404 org error propagation tests. | Focused suite `121 passed`; E2E `34 passed` |
| Review pass 2 | completed/clean | All five fresh reviewers reported no actionable findings. | Reviewers: fetching, test, security, architecture, smoke |
| Refactor | completed | No additional refactor reduced complexity without weakening explicit discovery-path coverage. | Inspected `fetching/github.py` helpers and tests |
| Integration | completed | Reconfirmed focused GitHub suite, compileall, and functional E2E after clean review. | `121 passed`; E2E `34 passed`; `git diff --check` |
| Delivery | in_progress | Commit, push, update PR #84. | Pending |
