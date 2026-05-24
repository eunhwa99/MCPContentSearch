# ContextWiki Core Understanding Note

## User Request

Add the user's ContextWiki core understanding note to a durable repository
location so it can keep being updated as the architecture evolves.

## Branch Preflight Result

- Continuing the existing Phase B PR branch at the user's request after PR #6
  was opened.
- Worktree: `/private/tmp/MCPContentSearch-phase-b`
- Branch: `feature/contextwiki-phase-b-connectors`
- Starting status: clean, tracking `origin/feature/contextwiki-phase-b-connectors`
- Linked worktrees inspected; `main` remains checked out at
  `/Users/eunhwa/IdeaProjects/MCPContentSearch`.

## Scope and Non-goals

Scope:

- Add a durable long-form understanding note under `docs/`.
- Update README so humans can discover the note.
- Update repository instructions so future architecture/source behavior changes
  also update the note.
- Keep repo-local harness verification guidance consistent with the broader
  maintained-doc scope.
- Keep the required architecture overview consistent with Phase B source
  coverage and ContextWiki tool/data flow.
- Keep the note aligned with Phase B, including GitHub and Web/docs connectors.

Non-goals:

- No Python behavior changes.
- No MCP tool contract changes.
- No new ADR because this records learning/documentation, not a new
  architecture decision.

## Acceptance Criteria

- `docs/contextwiki-core-understanding.md` exists and reflects Phase B, not only
  Phase B-0.
- README links to the note as a maintained learning/architecture companion.
- AGENTS instructions mention updating the note when ContextWiki source,
  ingestion, retrieval, citation, or lifecycle behavior changes.
- Harness docs-only verification guidance includes the maintained docs touched
  by this change.
- GitHub workflow and harness phase skills use the same docs-only and syntax
  verification commands.
- The required architecture doc no longer describes the source layer as
  Notion/Tistory-only.
- Docs verification passes.

## Step Breakdown

1. Add the understanding note under `docs/contextwiki-core-understanding.md`.
2. Add README discovery link.
3. Add AGENTS durable update instruction and project-structure entry.
4. Align harness docs-only verification guidance.
5. Align architecture overview source/tool/data-flow wording.
6. Run lightweight docs verification.
7. Run a fresh five-reviewer docs review gate before commit/push.

## Files Likely to Change

- `docs/contextwiki-core-understanding.md`
- `README.md`
- `AGENTS.md`
- `.agents/docs/architecture.md`
- `.agents/docs/harness-engineering.md`
- `.agents/docs/github-workflow.md`
- `.agents/skills/harness-engineering/SKILL.md`
- `.agents/skills/harness-plan/SKILL.md`
- `.agents/skills/harness-implement/SKILL.md`
- `.agents/skills/harness-test/SKILL.md`
- `.agents/skills/harness-integrate/SKILL.md`
- `.agents/skills/harness-multitask/SKILL.md`
- `.agents/skills/harness-review/SKILL.md`
- `.agents/skills/harness-review/agents/openai.yaml`
- `docs/plan/2026-05-24-contextwiki-core-understanding-note.md`

## Test and Verification Plan

Docs-only verification:

```bash
rg --files AGENTS.md README.md docs .agents/docs .agents/skills
git status --short --branch
git diff --check
git diff --cached --check
```

## Architecture/ADR Constraints

- `.agents/docs/architecture.md` keeps module ownership boundaries.
- ADR 0002 keeps SQLite as metadata/citation source of truth and Chroma as
  vector retrieval.
- ADR 0003 defines identity lifecycle, tombstones, version metadata, and
  source-aware chunking.
- ADR 0004 defines Phase B GitHub and website/docs connector boundaries.

## Risks and Rollback Notes

- Risk: the note could drift if future feature work updates only plan/ADR docs.
  Mitigation: add AGENTS instruction to update the note for relevant behavior
  changes.
- Rollback: revert this docs-only commit; no runtime state or user data is
  affected.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Continued existing Phase B PR branch with clean worktree. | `git status --short`; `git branch -vv`; `git worktree list` |
| Planning | completed | Read harness docs, GitHub workflow, architecture, ADR index, and ADR 0002/0003/0004. | `.agents/docs/harness-engineering.md`; `.agents/docs/github-workflow.md`; `.agents/docs/architecture.md`; `.agents/docs/adr/` |
| Implementation | completed | Added the maintained understanding note, README discovery link, AGENTS update rule, architecture overview alignment, and harness docs-only verification alignment. | `docs/contextwiki-core-understanding.md`; `README.md`; `AGENTS.md`; `.agents/docs/architecture.md`; `.agents/docs/harness-engineering.md` |
| Verification | completed | Docs-only checks passed. | `rg --files AGENTS.md README.md docs .agents/docs`; `git status --short`; `git diff --check` |
| Review pass 1 | completed | Five fresh reviewers found docs-only issues: untracked new files, stale cleanup wording, `chunk_tombstones` overstatement, live-smoke wording, and stale AGENTS Phase B/API verification wording. | Reviewers `019e5859-4a4b-7400-a248-2865b7c183da`, `019e5859-4b7d-73f1-9ebe-85a27ca5a3d2`, `019e5859-4d73-7de1-98d6-9017a69329d4`, `019e5859-5016-7810-bfde-8ec32341dabd`, `019e5859-53ae-7500-80b7-612b0ea65992` |
| Review pass 1 remediation | completed | Narrowed stale cleanup and `chunk_tombstones` wording, clarified live smoke tests are opt-in rather than absent, updated AGENTS Phase B API/fetcher/docs verification wording, and prepared to stage the new docs explicitly. | `docs/contextwiki-core-understanding.md`; `AGENTS.md` |
| Post-review-pass-1 verification | completed | Reran docs-only checks after remediation and explicit staging; staged status includes README, AGENTS, the new note, and this plan. | `rg --files AGENTS.md README.md docs .agents/docs`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 2 | completed | Five fresh reviewers reviewed staged remediation. Reviewers found remaining wording issues: `deleted_at` table/section still needed cleanup-capable sync wording, and live smoke checks should be separated from not-implemented limitations. | Reviewers `019e585d-bd2d-79b1-90b5-a76aa8539a0e`, `019e585d-be74-78e2-876d-f4c932eca9d2`, `019e585d-c03e-7461-9d70-41188e8f75e3`, `019e585d-c2d0-71d0-bf94-e33ad771e682`, `019e585d-c554-7bb2-aacb-5d2b7f2edd1c` |
| Review pass 2 remediation | completed | Updated `deleted_at` wording to require cleanup-capable successful syncs and split live smoke tests into an available-but-non-default section. | `docs/contextwiki-core-understanding.md` |
| Post-review-pass-2 verification | completed | Reran docs-only checks after pass 2 remediation. | `rg --files AGENTS.md README.md docs .agents/docs`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 3 | completed | Five fresh reviewers reviewed pass 2 remediation. Reviewers found remaining wording issues: external-id-change examples still implied unconditional tombstoning, live-smoke validation sounded available rather than deferred/non-default, and README still narrowed source metadata to Notion/Tistory. | Reviewers `019e5861-cdab-7152-a251-aab0a1b864aa`, `019e5861-cefe-7b22-bcea-b12168c3f1c9`, `019e5861-d166-7f20-9dd8-e2800c6c13ec`, `019e5861-d53c-7662-b246-3b9500d6f11c`, `019e5861-d827-7bc2-81ba-512b72a54345` |
| Review pass 3 remediation | completed | Qualified external-id-change tombstoning by cleanup-capable source/snapshot, changed live-smoke wording to deferred/non-default validation, and broadened README source metadata wording to include GitHub and website/docs. | `docs/contextwiki-core-understanding.md`; `README.md` |
| Post-review-pass-3 verification | completed | Reran docs-only checks after pass 3 remediation. | `rg --files AGENTS.md README.md docs .agents/docs`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 4 | completed | Five fresh reviewers reviewed pass 3 remediation. Reviewers found remaining docs accuracy issues: chunking happens before skip decisions for chunk-id comparison, `chunk_tombstones` should be pre-replacement provenance, metadata contract mismatches are rejected rather than repaired by reindexing, and harness verification docs needed to match AGENTS. | Reviewers `019e5869-0588-73a1-8e23-96238f3e472d`, `019e5869-072d-7832-9de4-956fc6e900ba`, `019e5869-0a6a-7041-87ea-bd270cc2cec5`, `019e5869-0e25-7b61-8e4f-4f92774b9d40`, `019e5869-1218-7a61-bfa0-1fb8624b08ec` |
| Review pass 4 remediation | completed | Separated deterministic chunking from vector reindexing, clarified skipped metadata refresh, narrowed `chunk_tombstones` wording to pre-replacement provenance, removed contract-mismatch reindex overclaim, and aligned harness docs verification/API wording. | `docs/contextwiki-core-understanding.md`; `.agents/docs/harness-engineering.md`; `docs/plan/2026-05-24-contextwiki-core-understanding-note.md` |
| Post-review-pass-4 verification | completed | Reran docs-only checks after pass 4 remediation. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 5 | completed | Five fresh reviewers reviewed pass 4 remediation. Reviewers found final wording drift: fetching/network review lens still named only Notion/Tistory, and example/summary reindex wording omitted rechunked documents. | Reviewers `019e586e-c9a1-7581-aa8f-74f111900e3f`, `019e586e-cb0b-7cc3-9c26-2f44abf51560`, `019e586e-cdfd-7c92-be25-a94958f5781c`, `019e586e-d12d-7280-9c82-7572509a1301`, `019e586e-d4a2-74d0-89e1-acad143bcde3` |
| Review pass 5 remediation | completed | Generalized the fetching/network review lens to external source connector behavior and broadened example/summary wording to new, changed, reappeared, or rechunked documents. | `.agents/docs/harness-engineering.md`; `docs/contextwiki-core-understanding.md` |
| Post-review-pass-5 verification | completed | Reran docs-only checks after pass 5 remediation. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 6 | completed | Five fresh reviewers were spawned after pass 5 remediation. Completed review findings identified that the required architecture overview still described source coverage as Notion/Tistory-only. | Reviewers `019e5878-719b-7ec2-8b2f-242f0177a93c`, `019e5878-732f-7632-9098-9afc3750a445`, `019e5878-7574-79a0-8b2c-947c96e9f4e9`, `019e5878-78ac-7572-9582-1b9b8ba09e14`, `019e5878-7c50-7853-aecf-d27b68a4763b` |
| Review pass 6 remediation | completed | Updated architecture runtime structure, data flow, module responsibilities, MCP tool list, persistence, and external services for ContextWiki source registry, GitHub/Web connectors, SQLite metadata, and citation retrieval. | `.agents/docs/architecture.md`; `docs/plan/2026-05-24-contextwiki-core-understanding-note.md` |
| Post-review-pass-6 verification | completed | Reran docs-only checks after pass 6 remediation. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 7 | completed | Five fresh reviewers reviewed pass 6 remediation. Reviewers found flow and instruction drift: list/status/fetch tools were overgeneralized, SQLite metadata safety and compile commands were incomplete, README still showed only legacy MCP flow, and Markdown chunking overclaimed heading behavior. | Reviewers `019e587f-8079-7dc2-b761-73e60bc37080`, `019e587f-80f6-7ff0-baba-d57380329300`, `019e587f-818c-7162-a65c-822253ed0fce`, `019e587f-8206-7f92-a798-9f2dc1baec76`, `019e587f-8296-7680-aef3-7176da895c1b` |
| Review pass 7 remediation | completed | Split ContextWiki architecture flows by status, sync, search, answer, and fetch paths; added `storage` to compile checks; broadened SQLite metadata safety wording; added README ContextWiki flow; and clarified headingless Markdown fallback. | `.agents/docs/architecture.md`; `.agents/docs/harness-engineering.md`; `AGENTS.md`; `README.md`; `docs/contextwiki-core-understanding.md`; `docs/plan/2026-05-24-contextwiki-core-understanding-note.md` |
| Post-review-pass-7 verification | completed | Reran docs-only checks after pass 7 remediation. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 8 | completed | Five fresh reviewers reviewed pass 7 remediation. Reviewers found final durable-doc drift: sync flow should show `IngestionService` owning the registry lookup, docs-only instructions needed staged diff checks across workflow/phase skills, README live-smoke wording overclaimed current tests, and README answer wording overclaimed generation. | Reviewers `019e5888-246f-70b0-8742-f19b5dd4454d`, `019e5888-2530-7980-8f3f-259d27661508`, `019e5888-2612-74d1-b642-745c409f39d1`, `019e5888-26d9-7573-8e3b-a455120526fc`, `019e5888-2794-78c3-9c18-180b830737c6` |
| Review pass 8 remediation | completed | Put `IngestionService` before `SourceRegistry` in sync diagrams, added staged diff checks and `storage` compile coverage across workflow/phase skills, tightened README live-smoke wording to future opt-in tests, and renamed README answer generation wording to evidence-gated citation responses. | `.agents/docs/architecture.md`; `.agents/docs/github-workflow.md`; `.agents/docs/harness-engineering.md`; `.agents/skills/harness-test/SKILL.md`; `.agents/skills/harness-integrate/SKILL.md`; `.agents/skills/harness-review/SKILL.md`; `AGENTS.md`; `README.md`; `docs/contextwiki-core-understanding.md`; `docs/plan/2026-05-24-contextwiki-core-understanding-note.md` |
| Post-review-pass-8 verification | completed | Reran docs-only checks after pass 8 remediation. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 9 | completed | Five fresh reviewers reviewed pass 8 remediation. Reviewers found the review skill still had pre-Phase-B review lenses/checklist wording and README still omitted `chunker.py`/`ingestion_service.py` from the indexing module map. | Reviewers `019e5891-84dc-7c80-abe0-591760a8ec5b`, `019e5891-8580-7043-9bd4-1e66bb6df1bf`, `019e5891-8627-78f3-bf89-19c317445833`, `019e5891-86dd-79b2-879f-0e2b118eafdc`, `019e5891-8797-7242-8374-2837b189ae3e` |
| Review pass 9 remediation | completed | Updated harness-review lenses/checklist for external source connectors and SQLite metadata, and added `chunker.py` plus `ingestion_service.py` to README tree/module overview. | `.agents/skills/harness-review/SKILL.md`; `README.md`; `docs/plan/2026-05-24-contextwiki-core-understanding-note.md` |
| Post-review-pass-9 verification | completed | Reran docs-only checks after pass 9 remediation and cleaned up remaining Chroma/Notion/Tistory-only wording in implementation/planning skills. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check`; `.agents/skills/harness-implement/SKILL.md`; `.agents/skills/harness-plan/SKILL.md` |
| Review pass 10 | completed | Five fresh reviewers reviewed pass 9 remediation. Reviewers found planning/review checklist drift for SQLite/GitHub/Web coverage, stale API/converter README descriptions, and raw fallback wording that conflated `search_context` with legacy search suppression. | Reviewers `019e5899-3ca4-7103-87b8-6850113d86d7`, `019e5899-3d40-7f61-9e01-55cf1eaa6222`, `019e5899-3db6-7330-8c39-69497a022692`, `019e5899-3e56-7623-9ca8-f657fc9a4482`, `019e5899-3ee2-7af0-b26e-19b505549b48` |
| Review pass 10 remediation | completed | Broadened planning bullets for SQLite and GitHub/Web, aligned the top-level review lens with storage/tombstone checks, broadened API/converter descriptions, split managed `search_context` from legacy raw-vector suppression wording, and renamed stale `CodeChunker` wording to DocumentChunker code parsing. | `.agents/skills/harness-plan/SKILL.md`; `.agents/docs/harness-engineering.md`; `.agents/docs/architecture.md`; `README.md`; `docs/contextwiki-core-understanding.md`; `docs/plan/2026-05-24-contextwiki-core-understanding-note.md` |
| Post-review-pass-10 verification | completed | Reran docs-only checks after pass 10 remediation. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 11 | completed | Five fresh reviewers reviewed pass 10 remediation. Four reviewers found no actionable issues; one found the architecture/README sync diagram still put connector fetch before the MetadataStore sync job guard. | Reviewers `019e58a1-52f7-78b1-8fab-bc535f730f6e`, `019e58a1-53ab-76a2-b41e-3f7fc3d2c54c`, `019e58a1-5867-7903-8082-6f0d304db69d`, `019e58a1-5911-75d2-a884-13f1b5a84dbd`, `019e58a1-59c8-72b1-9d6c-d63c6e4627c7` |
| Review pass 11 remediation | completed | Moved MetadataStore source registration/sync job guard before connector fetch in architecture and README sync flow diagrams. | `.agents/docs/architecture.md`; `README.md`; `docs/plan/2026-05-24-contextwiki-core-understanding-note.md` |
| Post-review-pass-11 verification | completed | Reran docs-only checks after pass 11 remediation. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 12 | completed | Five fresh reviewers reviewed pass 11 remediation. Four reviewers found no actionable issues; one found the plan's likely-changed file list omitted `harness-plan` and `harness-implement`. | Reviewers `019e58a8-af57-7b81-9d44-8ddd50721831`, `019e58a8-afde-7872-a1fe-80adea8bcd27`, `019e58a8-b0d9-7d23-955d-8d9f431f95ce`, `019e58a8-b1af-7420-acb7-6f9b672129cf`, `019e58a8-b246-7c93-9e94-2e4938c511e8` |
| Review pass 12 remediation | completed | Added `harness-plan` and `harness-implement` to the plan's likely-changed file list. | `docs/plan/2026-05-24-contextwiki-core-understanding-note.md` |
| Post-review-pass-12 verification | completed | Reran docs-only checks after pass 12 remediation. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 13 | completed | Five fresh reviewers reviewed pass 12 remediation. Two reviewers found no actionable issues; three found stale README converter/sync-flow wording, weaker AGENTS data-safety wording, missing tombstone coverage in the review lens, and the missing post-pass-12 verification row. | Reviewers `019e58ae-0c80-7901-ae52-4ad006eabe00`, `019e58ae-0d32-7a92-85fe-bb900b4157a8`, `019e58ae-0e10-7fc0-9a22-c8a3f23442e9`, `019e58ae-0edf-7533-9b17-9317e287aad8`, `019e58ae-0fdc-7722-ba30-512ccbe7d7c6` |
| Review pass 13 remediation | completed | Aligned README converter and source-registration sync-flow wording, strengthened AGENTS Chroma/SQLite safety wording with explicit user approval, added tombstone metadata to the review lens, and recorded post-pass-12 verification. | `README.md`; `AGENTS.md`; `.agents/skills/harness-review/SKILL.md`; `docs/plan/2026-05-24-contextwiki-core-understanding-note.md` |
| Post-review-pass-13 verification | completed | Reran docs-only checks after pass 13 remediation. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 14 | completed | Five fresh reviewers reviewed pass 13 remediation. Reviewers found that the detailed sync diagram still omitted the explicit source registration/begin-sync guard branch, the orchestrator/reviewer prompt still had pre-Phase-B review-lens wording, and the plan log needed post-pass-13 verification. | Reviewers `019e58b5-c40d-76c0-905d-300d06c79b4d`, `019e58b5-c4c3-7c60-8674-fef26e70317a`, `019e58b5-c536-7723-9277-0f9c1825435d`, `019e58b5-c5ea-7d03-8409-ad1ef82ebd84`, `019e58b5-c692-7480-a76b-924393a63d89` |
| Review pass 14 remediation | completed | Updated the detailed sync diagram with `MetadataStore register_source + begin_sync_job guard` and an already-running branch, aligned orchestrator and reviewer prompts with SQLite lifecycle/tombstone metadata and external connector review lenses, and recorded post-pass-13 verification. | `docs/contextwiki-core-understanding.md`; `.agents/skills/harness-engineering/SKILL.md`; `.agents/skills/harness-review/agents/openai.yaml`; `docs/plan/2026-05-24-contextwiki-core-understanding-note.md` |
| Post-review-pass-14 verification | completed | Reran docs-only checks after pass 14 remediation. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 15 | completed | Five fresh reviewers reviewed pass 14 remediation. Four reviewers found only the missing orchestrator skill entry in the likely-changed file list; one also found the multitask split guard still omitted SQLite lifecycle/tombstone metadata and external source connector wording. | Reviewers `019e58bc-825e-7c00-93e2-99718e025dd4`, `019e58bc-82df-75d3-9541-b3b389e3135f`, `019e58bc-838e-7c93-9fb3-1200c772e70b`, `019e58bc-8425-78d0-b09d-d53d8639305b`, `019e58bc-84c8-7cb1-a245-eb28a5389575` |
| Review pass 15 remediation | completed | Added `.agents/skills/harness-engineering/SKILL.md` to the plan's likely-changed file list and updated the multitask split guard for SQLite lifecycle/tombstone metadata plus external source connector contracts. | `docs/plan/2026-05-24-contextwiki-core-understanding-note.md`; `.agents/skills/harness-multitask/SKILL.md` |
| Post-review-pass-15 verification | completed | Reran docs-only checks after pass 15 remediation. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 16 | completed | Five fresh reviewers reviewed pass 15 remediation. Reviewers found `.agents/skills/harness-multitask/SKILL.md` missing from the likely-changed file list; one also found the main harness multitask guard needed the same SQLite lifecycle/tombstone and external source connector wording, and another found the GitHub tombstone example needed the complete cleanup-capable sync qualifier. | Reviewers `019e58c3-7aba-73f2-8523-ec2399858834`, `019e58c3-7b37-7f20-8b16-cba79cc3ff7e`, `019e58c3-7bef-7532-b539-5d2d88ef22b0`, `019e58c3-7ca7-7982-8780-705a83da1716`, `019e58c3-7d56-72e1-91ad-74f4027050d3` |
| Review pass 16 remediation | completed | Added `.agents/skills/harness-multitask/SKILL.md` to the likely-changed file list, aligned the main harness multitask guard with the skill wording, and qualified the GitHub tombstone example as a complete cleanup-capable successful sync. | `docs/plan/2026-05-24-contextwiki-core-understanding-note.md`; `.agents/docs/harness-engineering.md`; `docs/contextwiki-core-understanding.md` |
| Post-review-pass-16 verification | completed | Reran docs-only checks after pass 16 remediation. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
