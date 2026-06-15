## User request

- Fix the stuck Notion sync problem caused while running the MCP server from Claude Desktop with Docker.
- Do both:
  - recover the current stuck local sync state
  - patch the code so Docker restarts do not keep stale running jobs alive
- Clarify whether two running Docker containers mean one was started by Claude and whether the user still needs to run one manually.

## Branch preflight result

- Date: `2026-06-15`
- Starting worktree: `/Users/eunhwa/.codex/worktrees/53e0/MCPContentSearch`
- Initial state: clean detached HEAD at `25ce3b5`
- Freshness check: `git fetch origin main` succeeded
- Task branch: `feature/notion-docker-owner-recovery` created from `origin/main`
- Safety: target edits will not happen on `main`

## Scope

- Add regression coverage for Docker-style sync owner persistence where multiple container generations record `process_id=1`.
- Patch `storage/metadata_store.py` so startup and runtime recovery can distinguish the current owner from stale previous owners even when PID liveness checks are not enough in containers.
- Safely mark the currently stuck local `running` sync jobs as failed in SQLite metadata only, without touching user document/chunk content or Chroma vectors.
- Confirm that no leftover `running` sync rows remain in metadata after the
  bounded local repair.

## Non-goals

- Do not change MCP tool contracts.
- Do not inspect or mutate user document/chunk contents.
- Do not delete or reset local Chroma data.
- Do not change Notion connector request logic unless testing proves it is needed.

## Acceptance criteria

- A failing test first demonstrates that a previous-owner running job can be recovered even when the stored PID remains alive in a Docker-style `process_id=1` scenario.
- `MetadataStore.recover_orphaned_running_jobs()` and/or follow-on `begin_sync_job()` no longer treat stale previous-owner rows as live solely because PID `1` exists in the new container.
- Focused storage tests pass.
- Syntax verification for repo Python modules passes.
- The bounded local metadata repair only changes sync-job/source status fields.
- After the metadata repair, the post-repair readback shows no leftover
  `running` sync rows in local sync metadata.

## Step breakdown

1. `storage-owner-regression`
   - Read current `MetadataStore` owner recovery flow and nearby tests.
   - Add a regression that models Docker container restart behavior with old and new owners both using PID `1`.
   - Verify the new test fails before implementation.
2. `storage-owner-fix`
   - Patch owner liveness/recovery logic in `storage/metadata_store.py` with the smallest boundary that preserves current non-Docker behavior.
   - Keep the fix inside storage metadata ownership rules; do not spread Docker-specific logic into API/fetching layers.
3. `focused-verification`
   - Run the targeted storage tests.
   - Run compile verification.
4. `local-metadata-repair`
   - Update only stuck local sync job/source status rows to a recovery failure message.
   - Re-read the rows to confirm the post-repair metadata state.

## Files likely to change

- `storage/metadata_store.py`
- `tests/storage/test_metadata_store.py`
- `docs/contextwiki-core-understanding.md`
- `docs/plan/2026-06-15-notion-docker-owner-recovery.md`

## Test and verification plan

- Red: targeted failing storage regression
  - `uv run --locked pytest -q tests/storage/test_metadata_store.py -k "docker or previous_owner"`
- Green:
  - `uv run --locked pytest -q tests/storage/test_metadata_store.py`
- Syntax:
  - `python -m compileall api core environments fetching indexing search storage main.py`

## Functional smoke matrix plan

| Surface | Scenario | Safe mode | Planned status |
| --- | --- | --- | --- |
| SQLite sync metadata | Recover stale Docker-owner running job | Temp test DB | passed |
| Local metadata cleanup | Confirm post-repair metadata no longer contains leftover `running` sync rows | Existing local metadata only; no content reads | passed |
| Functional E2E gate | Retained MCP caller-surface smoke via repo script | Local deterministic suite | passed |

## Architecture/ADR constraints

- Keep the fix in `storage/metadata_store.py` per ADR 0001 layered boundaries.
- Preserve SQLite as the operational source of truth beside Chroma per ADR 0002.
- Do not broaden MCP scope or change retained tool contracts per ADR 0006.
- Raw secrets must not be logged or persisted.

## Risks and rollback notes

- Risk: over-recovering a genuinely live sync job.
  - Mitigation: make the recovery rule narrow to previous-owner rows that cannot be the current store owner, and cover it with regression tests.
- Risk: local metadata repair could touch user data.
  - Mitigation: limit the repair to `sync_jobs` and `sources` status/error fields only; no document/chunk/chroma mutation.
- Rollback:
  - Revert the code diff.
  - If the local metadata repair proves wrong, statuses can be re-created by rerunning syncs; no indexed content rows or vectors are deleted in this plan.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Verified clean detached worktree, fetched `origin/main`, created `feature/notion-docker-owner-recovery`. | `git status --short --branch`; `git fetch origin main`; `git switch -c feature/notion-docker-owner-recovery origin/main` |
| Planning | completed | Wrote plan for storage-owner fix plus bounded local metadata repair. | `docs/plan/2026-06-15-notion-docker-owner-recovery.md` |
| Regression test | completed | Added a failing regression for stale previous-owner recovery when PID liveness is a Docker false positive. | `uv run --locked pytest -q tests/storage/test_metadata_store.py -k "stale_previous_owner_even_if_pid_looks_alive"` failed with `assert 0 == 1` |
| Implementation | completed | Refreshed owner heartbeats during running-job activity, then tightened same-PID previous-owner recovery to use running-job staleness instead of the short unowned grace after review findings. | `storage/metadata_store.py`; `tests/storage/test_metadata_store.py` |
| Focused verification | completed | Re-ran targeted storage tests, full storage test file, compile verification, and functional E2E after the review-driven fix. | `uv run --locked pytest -q tests/storage/test_metadata_store.py -k "same_pid_previous_owner or stale_previous_owner_even_if_pid_looks_alive or live_previous_owner or previous_owner or begin_sync_job_preserves_same_pid_previous_owner or begin_sync_job_recovers_stale_same_pid_previous_owner"`; `uv run --locked pytest -q tests/storage/test_metadata_store.py`; `python -m compileall api core environments fetching indexing search storage main.py`; `./scripts/verify_functional_e2e.sh` |
| Local metadata repair | completed | Confirmed the post-repair metadata readback had no remaining `running` sync rows. | readback query showed zero remaining `sync_jobs.status = running` rows |
