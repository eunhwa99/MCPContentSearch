# Roadmap H/I/J Review

## User request

Review whether these new roadmap phases should be added:

- Phase H: Security, Permissions, and Data Governance
- Phase I: Production Ingestion Hardening
- Phase J: Retrieval and Answer Quality

Also review whether the following items should be reflected in the plan:

- Minimum source-aware chunking before or inside Phase B, especially GitHub line-range chunking.
- Minimum document identity hardening before or inside Phase B: `external_id`, `canonical_url`, `last_seen_at`, `deleted_at`.
- Source-wide stale document/chunk cleanup for documents that disappear from a source sync.

Follow-up request:

- Create the PR for this work.
- Update the repository workflow so final clean `$subagent-review-loop` verification proceeds to PR creation by default.

## Branch preflight result

- Original worktree `/Users/eunhwa/IdeaProjects/MCPContentSearch` was dirty from a prior docs-only branch, so no branch switching, pulling, or cleanup was performed there.
- Ran `git fetch origin main`.
- Created isolated worktree `/private/tmp/MCPContentSearch-roadmap-hij` with fresh branch `feature/review-roadmap-hij` from `origin/main`.
- This worktree was clean before target edits.

## Scope and non-goals

- Scope: docs-only roadmap and planning updates.
- Scope: durable workflow docs for final clean subagent verification to flow into commit, push, and PR delivery by default.
- Non-goals: no runtime code changes, no schema migration implementation, no connector implementation.
- Do not inspect, delete, reset, or migrate user Chroma data.
- Do not store tokens, API keys, or private repo contents in docs.
- Do not watch the PR, respond to GitHub comments, or push follow-up PR changes unless the user delegates that work.

## Acceptance criteria

- Decide whether Phase H/I/J should be added to the ContextWiki roadmap.
- If added, place them in a way that does not hide earlier prerequisites for Phase B GitHub/Web connectors or Phase E remote/API deployment.
- Record minimum Phase B gates for source-aware chunking, document identity hardening, and source-wide stale cleanup.
- Preserve architecture constraints from `.agents/docs/architecture.md`, ADR 0001, and ADR 0002.
- Keep the update docs-only and verify with lightweight docs checks.
- Run `$subagent-review-loop` until all five reviewers in the newest fresh pass report no actionable findings.
- State that final clean `$subagent-review-loop` verification should proceed to commit, push, and PR creation by default, unless the user explicitly opts out or a safety blocker exists.
- Preserve safe staging, no direct commits to `main`, PR base `main`, and no GitHub comment replies without explicit delegation.

## Step breakdown

| Step | Label | Boundary | Acceptance criteria |
| --- | --- | --- | --- |
| 1 | `roadmap-assessment` | Review existing roadmap and current metadata/chunker/ingestion implementation. | Identify which user items are already covered, missing, or phase-gated. |
| 2 | `roadmap-update` | Update `docs/plan/2026-05-20-contextwiki-roadmap.md`. | Phase H/I/J are added and Phase B minimum gates are explicit. |
| 3 | `pr-workflow-update` | Update repo workflow docs and harness skills. | Final clean subagent verification now flows into PR delivery by default. |
| 4 | `verification` | Run docs-only checks after all docs edits. | `rg --files`, `git status --short`, and `git diff --check` pass. |
| 5 | `review-loop` | Run fresh five-reviewer `$subagent-review-loop` after final verification. | All five reviewers in the newest fresh pass report no actionable findings. |
| 6 | `pr-delivery` | Commit, push, and open a `main`-base PR. | PR URL is reported after successful push/create. |

## Files likely to change

- `docs/plan/2026-05-20-contextwiki-roadmap.md`
- `docs/plan/2026-05-22-roadmap-hij-review.md`
- `AGENTS.md`
- `.agents/docs/github-workflow.md`
- `.agents/docs/harness-engineering.md`
- `.agents/skills/harness-engineering/SKILL.md`
- `.agents/skills/harness-implement/SKILL.md`
- `.agents/skills/harness-integrate/SKILL.md`
- `.agents/skills/harness-integrate/agents/openai.yaml`
- `.agents/skills/harness-review/SKILL.md`

## Test and verification plan

Docs-only verification:

```bash
rg --files AGENTS.md .agents/docs .agents/skills docs/plan
git status --short
git diff --check
```

No Python tests are required unless runtime code changes are introduced.

After the final clean review pass, stage only relevant files, commit with a docs Conventional Commit message, push the feature branch, and create a `main`-base PR.

## Architecture/ADR constraints

- ADR 0001 keeps behavior inside layered runtime boundaries: `fetching/` for connectors, `indexing/` for chunking/sync/vector writes, `search/` for retrieval/answer orchestration, and `api/` for MCP contracts.
- ADR 0002 introduced the SQLite metadata store, currently implemented under `storage/`, and says future GitHub/Web/PDF connectors should reuse the existing source/job/document/chunk schema while keeping auth as environment-variable references.
- Document identity and tombstone fields are persistence-contract changes. Runtime implementation should either extend ADR 0002 or add a new ADR when the schema is changed.
- ACL-aware retrieval and tenant/source isolation affect search filters, storage metadata, and remote/API deployment contracts; they need a dedicated implementation plan before any remote multi-user release.

## Assessment

- Phase H/I/J should be added, but not as a reason to postpone Phase B-critical metadata and chunking work.
- `source-aware chunking` needs a Phase B minimum: Markdown heading chunks, code line-range chunks, plain text character chunks. Function/class-aware code chunking can wait.
- `document identity hardening` needs a Phase B minimum: `external_id`, `canonical_url`, `last_seen_at`, and `deleted_at`; fingerprint dedup can wait.
- `stale chunk cleanup` currently handles changed documents by removing stale old chunks, but source-wide disappeared documents are still weak. Phase B should include source sync cleanup or soft deletion for documents absent from a successful full source sync.

## Risks and rollback notes

- Risk: Treating H/I/J as purely late phases could allow GitHub connector work to ship with poor code citations or stale deleted files.
  - Mitigation: add Phase B prerequisite gates.
- Risk: Tombstone cleanup could delete data after partial connector failures.
  - Mitigation: plan cleanup only after a successful source sync, and prefer soft delete/tombstone first.
- Risk: ACL/tenant language could over-scope the personal/local MVP.
  - Mitigation: make H a production hardening phase, but require minimal source isolation now.
- Rollback: docs-only rollback removes this plan and reverts roadmap edits.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created isolated branch `feature/review-roadmap-hij` from `origin/main` because the primary worktree was dirty. | `git fetch origin main`; `git worktree add -b feature/review-roadmap-hij /private/tmp/MCPContentSearch-roadmap-hij origin/main` |
| Planning | completed | Reviewed roadmap, architecture, ADRs, metadata store, chunker, and ingestion implementation. | `docs/plan/2026-05-20-contextwiki-roadmap.md`; ADR 0001/0002; `storage/metadata_store.py`; `indexing/chunker.py`; `indexing/ingestion_service.py` |
| Implementation | completed | Updated roadmap with H/I/J, Phase B minimum gates, and current capability status. | `docs/plan/2026-05-20-contextwiki-roadmap.md` |
| PR workflow update | completed | Updated durable harness/GitHub workflow so final clean `$subagent-review-loop` verification proceeds to PR creation by default. | `AGENTS.md`; `.agents/docs/github-workflow.md`; `.agents/docs/harness-engineering.md`; `.agents/skills/harness-engineering/SKILL.md`; `.agents/skills/harness-implement/SKILL.md`; `.agents/skills/harness-integrate/SKILL.md`; `.agents/skills/harness-integrate/agents/openai.yaml`; `.agents/skills/harness-review/SKILL.md` |
| Verification | completed | Docs-only checks passed after the PR workflow update and review remediation. | `rg --files AGENTS.md .agents/docs .agents/skills docs/plan`; `git status --short`; `git diff --check` |
| Review findings remediation | completed | Fresh review passes found stale one-reviewer loop wording, no-commit skill metadata, fallback wording, stale roadmap/plan sequencing, and plan bookkeeping gaps; fixes applied. | Fresh reviewers `019e4dfd-*`, `019e4e02-*` |
