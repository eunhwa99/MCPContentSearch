# Plan: Slim README and diagram-first architecture

## User request

README and architecture docs have too much prose and are hard to scan at a
glance. Prefer a short, visual README and an architecture document led by
diagrams rather than long narrative sections.

## Branch preflight result

- Starting worktree was clean on `feature/notion-fetch-skip-unchanged-edited`.
- `main` is checked out in another worktree
  (`MCPContentSearch-launchd-sync-worker-tdd`), so local `main` could not be
  switched into this worktree.
- Fetched `origin/main` and created
  `feature/slim-readme-architecture` from `origin/main` at `9d68643`.
- Safe local branch cleanup skipped for branches checked out in other
  worktrees; no destructive cleanup performed.
- Worktree safety: clean on the new feature branch before target edits.

## Plan decision

Not plan-exempt. This task substantially rewrites
`.agents/docs/architecture.md`, which is a maintained-architecture document.
Presentation-only condensation still requires a plan under
`docs/plan/README.md`.

## Scope and non-goals

### In scope

- Rewrite `README.md` for scannability: short hero, compact tables, minimal
  setup, diagrams over long prose.
- Rewrite `.agents/docs/architecture.md` as diagram-first while preserving
  maintained design assumptions, MCP contracts, safety rules, and harness
  constraints.
- Keep deep operational detail discoverable, but not in the first viewport /
  first screenful.

### Non-goals

- No runtime, MCP contract, schema, sync, retrieval, or security behavior
  changes.
- No live API, credential, Chroma, or SQLite user-data access.
- No harness process doc rewrites beyond what architecture condensation
  requires for accuracy.
- Do not invent new architecture; compress and visualize the current one.

## Acceptance criteria

1. README is substantially shorter and readable in one pass: what it is, how
   it runs, tools, quick start, config essentials, short sync-worker note,
   troubleshooting table, verification, project structure.
2. README keeps a small architecture diagram and links to
   `.agents/docs/architecture.md` for detail.
3. Architecture opens with Mermaid (or equivalent) diagrams for runtime,
   sync/job ownership, and retrieval/citation gating.
4. Architecture retains these design truths in scannable form:
   - SQLite is lifecycle and citation-gate authority; Chroma is accelerator.
   - Durable sync is enqueue + separate worker claim/heartbeat.
   - Tombstones only after complete successful cleanup-capable snapshots.
   - Tool inventory and contract intent remain accurate.
   - Secrets, local data, and verification strategy remain documented.
5. No design assumption drift vs current main-branch architecture content.
6. Docs-only verification passes; three-reviewer harness is clean; PR opens
   against `main`.

## Step breakdown

1. Documentation worker A: slim `README.md` only.
2. Documentation worker B: rewrite `.agents/docs/architecture.md` only.
3. Main-agent integration: inspect both diffs for consistency and preserved
   contracts; fix cross-links if needed.
4. Docs-only verification, functional-smoke matrix (docs n/a rows),
   three-reviewer loop, commit/push/PR.

## Files likely to change

- `README.md`
- `.agents/docs/architecture.md`
- `docs/plan/2026-07-31-slim-readme-architecture.md`

## Worker ownership

| Worker | Owns | Must not touch |
|--------|------|----------------|
| README docs worker | `README.md` | architecture.md, code, secrets |
| Architecture docs worker | `.agents/docs/architecture.md` | README.md, code, secrets |

Both share this branch. Main agent integrates. Workers must not commit, push,
open PRs, inspect secrets, or touch local Chroma/SQLite user data.

## TDD / verification

- TDD RED: `n/a` — docs-only maintained-architecture presentation rewrite;
  no runtime behavior change.
- GREEN / full suite: docs-only verification commands below, not
  `./scripts/verify_all.sh`.
- Eval gate: `n/a` — no retrieval/answer quality code change.
- Functional smoke: docs-only path listing and link/path sanity; no MCP live
  calls required.

Docs verification:

```bash
rg --files AGENTS.md README.md docs .agents/docs .agents/skills
git status --short --branch
git diff --check
# stage relevant docs, then:
git diff --cached --check
```

## Functional smoke matrix

| Surface | Mode | Result |
|---------|------|--------|
| README path + internal architecture link | docs listing | pass |
| Architecture Mermaid diagrams present | file inspect | pass (4 diagrams) |
| MCP/sync live smoke | blocked/gated | n/a for docs-only |

## Architecture constraints

- Preserve current slim ContextWiki scope: FastMCP + durable sync worker,
  Notion/Tistory/GitHub/Obsidian, SQLite + Chroma, citation-gated retrieval.
- Do not document removed surfaces (Web Console, Auto Wiki, public
  `answer_with_citations` MCP tool) as active.
- Keep auth_ref / sanitizer / tombstone / owner-heartbeat safety rules.
- README and architecture must not disagree on tool names or sync ownership.

## Risks and rollback

- Risk: over-compression drops a harness-critical design rule.
  Mitigation: workers keep a "must retain" checklist; reviewers check
  correctness/contracts and security lenses.
- Rollback: revert the docs commit / close the PR.

## Progress log

| Phase | Status | Summary | Evidence |
|-------|--------|---------|----------|
| Branch preflight | done | Feature branch from `origin/main` | `feature/slim-readme-architecture` @ `9d68643` |
| Plan | done | Plan written | this file |
| README worker | done | Slimmed ~551 → ~252 lines | [Slim README](7e6cd3ee-9ecb-44fb-a792-7004159776e3) |
| Architecture worker | done | Slimmed ~917 → ~275 lines, 4 Mermaid diagrams | [Diagram-first architecture](eb45e104-74a5-46f2-a82d-91e33702c8fc) |
| Integration | done | Tool names, sync ownership, links aligned; no code edits | inspected both files |
| Docs verify | done | path listing, diff --check, cached --check clean | staged README + architecture + plan |
| Review pass 1 | done with findings | Correctness 5🟡, security 4🟡, ops 2🟡 | [Review correctness](430a34fa-613b-46cb-b97e-56b64e9cc44c) / [Review security](313d6265-84b7-49d1-a07b-0eacb3dac76d) / [Review ops](06855da0-a229-4dca-98a8-732df1968d8d) |
| Review-fix | done | Docker log bounds, stale-env clear, LaunchAgent blast radius, sanitizer fail-closed, ADR0009/list_documents/date UTC, SQLite gate diagram, CitationAnswerService edge | stash recovered after ops reviewer `git restore`/`checkout main` |
| Review pass 2 | done with fixes | Security clean; ops 5🟡 + correctness 2🟡 fixed after report | [Pass2 security](1e636197-d897-4628-8d6a-c1019ad66c0e) clean |
| Review pass 3 | done with findings | Security clean; ops + correctness findings fixed | [Pass3 security](58e43206-ffbd-4c54-8b27-39a2432bcdaa) / [Pass3 ops](38287dc0-681a-4668-8d2f-779b57e8fc85) / [Pass3 correctness](f4ef7d86-eee8-4c4a-9030-10710b11ca5a) |
| Review-fix (pass3) | done | Docker recreate; missing-plist; config/Docker wording; tombstone diagram; phase table; ADR deadline clock; list_documents fields; error field contract | README + architecture |
| Review pass 4 | done clean | All three NO ACTIONABLE FINDINGS | [Pass4 correctness](64c0f61d-999a-464e-8fb5-e46d6d10aa55) / [Pass4 security](ded0bebb-56e2-485f-b732-35f5dd7fd8f1) / [Pass4 ops](a3554dd8-7c6f-4b61-a109-1bddc4aa7254) |
| PR delivery | done | Commit `eae7391`, PR opened | https://github.com/eunaverse/MCPContentSearch/pull/92 |
| CI fix | in_progress | Restore README/architecture docs-contract phrases for pytest | isolated worktree `.worktrees/slim-ci-fix` |
