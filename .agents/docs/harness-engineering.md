# Harness Engineering

## Purpose

This document is the default implementation harness contract for `MCPContentSearch`. When the user asks for feature work, fixes, refactors, tests, or other file-changing work, read this document and run the phase skills in order.

The harness makes planning, implementation, focused verification, review, refactoring, and integration repeatable. If verification fails, classify the failure, update the plan document, and return to the relevant implementation or test phase.

## Applies To

Always apply this harness for:

- Implementation, addition, fix, refactor, or test-writing requests.
- MCP tool contract changes.
- Search, indexing, fetcher, configuration, or persistence behavior changes.
- Multi-task work where parallel ownership, separate branches, or stacked PRs may be useful.
- Requests that mention phase-based, planner-first, review-gated, or subagent-assisted work.

For read-only explanations, command-output checks, or code reviews, use only the relevant parts.

## Phase and Gate Order

Run phases in this order. The main agent is the harness orchestrator: it owns
the plan, worker persona design, delegation, result synthesis, integration,
review routing, and final delivery. `harness-implement` and `harness-test` may
run as parallel lanes when tool policy and file ownership allow it.
Code-changing work must always pass through the test lane before any review
gate.

0. Branch preflight: follow `.agents/docs/github-workflow.md`.
1. Plan document: create or update `docs/plan/YYYY-MM-DD-short-task-name.md`.
2. `.agents/skills/harness-plan/SKILL.md`
3. Subagent orchestration design: choose bounded implementation, testing,
   documentation, or integration worker personas when delegation is available;
   use `.agents/skills/harness-multitask/SKILL.md` when work needs splitting.
4. Implementation lane: `.agents/skills/harness-implement/SKILL.md`, delegated
   to worker personas when useful and safe.
5. Test lane: `.agents/skills/harness-test/SKILL.md`, delegated to a distinct
   verification persona when useful and safe.
6. Middle review gate: `.agents/skills/harness-review/SKILL.md`, which must invoke `$subagent-review-loop`
7. Refactor phase: `.agents/skills/harness-refactor/SKILL.md`
8. Integration phase: `.agents/skills/harness-integrate/SKILL.md`
9. Final review gate: `.agents/skills/harness-review/SKILL.md`, which must invoke `$subagent-review-loop`
10. PR delivery: after the final clean `$subagent-review-loop` pass, stage only relevant files, commit, push, and create a `main`-base PR by default unless the user explicitly asks for local-only work or a safety blocker prevents delivery.

`.agents/skills/harness-engineering/SKILL.md` is the orchestrator for the full loop.

## Branch Preflight

Before editing target files:

1. Run `git status --short`.
2. Run `git branch --show-current`.
3. Run `git branch -vv` and `git worktree list` so local branch cleanup is safe and linked worktrees are visible.
4. If the current worktree is dirty, do not switch branches, pull, or delete branches there. If network is available, run `git fetch origin main`, then create an isolated worktree with a fresh `feature/...` branch from `origin/main`. If network or isolation is unavailable, record the blocker and ask the user before changing branch state.
5. If the current worktree is clean, switch to `main`.
6. If network is available, run `git fetch origin main` and `git pull --ff-only origin main` after reaching `main`. If network is restricted, record that freshness was not checked.
7. Delete only safe existing local non-`main` work branches before creating the new task branch, or from the isolated worktree after creating its fresh task branch when the original worktree is dirty. Delete only local refs, never remote branches. Prefer `git branch -d`; use `git branch -D` only after confirming there are no local-only commits or the user explicitly approves discarding them. Do not delete branches checked out in linked worktrees without resolving or reporting the worktree state.
8. Do not edit target files on `main`.
9. Create a fresh `feature/...` branch from updated `main` for every new task.
10. Reuse an existing `feature/...` branch only when the user explicitly asks to continue that branch.

Never run destructive cleanup such as `git reset --hard`, `git checkout -- <file>`, deleting local Chroma data, deleting local SQLite metadata, removing caches, or resetting local credentials unless the user explicitly asks.

## Plan Document

File-changing work must create or update a plan document after branch preflight and before non-plan target edits.

- File name: `YYYY-MM-DD-short-task-name.md`
- Required sections are listed in `docs/plan/README.md`.
- Include branch preflight result, scope/non-goals, acceptance criteria, expected files, verification plan, architecture/ADR constraints, risk/rollback notes, and progress log.
- If verification or review changes the plan, update the same plan document before continuing.
- Final reports should include the plan document path.

## Architecture and ADR

Planning must read:

- `.agents/docs/architecture.md`
- `.agents/docs/adr/README.md`
- Directly relevant accepted ADRs only

Review gates must check that the diff does not violate architecture docs or accepted ADRs. If a change intentionally changes long-term architecture, add or update an ADR in the same work item.

## Multi-task Orchestration

When the user provides multiple tasks, or a single task naturally splits into
independent implementation, testing, documentation, or integration ownership:

- Split by independent behavior, module ownership, and PR boundary.
- Do not treat tasks as independent if they change the same MCP tool contract, shared config, Chroma/indexing behavior, SQLite lifecycle/tombstone metadata, external source connector contract, or the same public module interface.
- Assign independent tasks to disjoint file ownership when subagent or parallel work is allowed.
- Use stacked PR planning when tasks have contract or ordering dependencies.
- The main agent owns integration, conflict resolution, final verification, and final report.

## Main-Agent Orchestration

The main agent should minimize human intervention while keeping safety gates
explicit. After branch preflight and plan creation, the main agent should:

- Inspect the plan and update it when the requested work, repo state, or
  verification results change.
- Define task-specific worker personas for implementation, tests,
  documentation, refactor, or integration. Each persona needs a bounded goal,
  owned files or modules, required context, non-goals, acceptance criteria, and
  expected verification.
- Delegate work to subagents when the active tool policy allows it and file
  ownership is clear. Tell workers not to commit, push, open PRs, change
  unrelated files, inspect secrets, mutate local Chroma/SQLite data, or bypass
  repo-local harness rules unless explicitly authorized.
- Collect worker outputs, inspect the diff directly, resolve conflicts, and
  decide whether more implementation, test, docs, or integration work is
  needed before review.
- Route actionable issues to the responsible worker persona, or to a fresh
  replacement worker with the same ownership boundary if the original worker is
  unavailable. Update the plan before retrying.
- Ask the user only for unsafe ambiguity, credentials, destructive operations,
  local data mutation, unavailable delegation/review tools, or external
  approval. Do not ask for routine implementation choices that can be decided
  from repo docs, architecture, ADRs, and the plan.

Implementation/execution worker subagents and `$subagent-review-loop` reviewer
subagents are separate roles. Workers may edit within their assigned boundary
when delegated. Reviewers inspect only and must not edit files.

## Retry Loop

Use this control loop:

```text
read repository instructions
read harness and GitHub workflow
read architecture and relevant ADRs
run branch preflight with GitHub workflow dirty/clean worktree safeguards
write or update docs/plan plan
run planning phase
design worker personas and task ownership
if needed, run multitask phase
repeat:
  delegate implementation/test/docs/integration work to bounded worker personas where possible
  collect worker results, inspect the diff, and synthesize the main-agent result
  run middle review gate using $subagent-review-loop
  if main-agent review or subagent review finds actionable issues:
    update plan
    route each issue to the responsible worker persona or a fresh replacement
    rerun affected verification
    continue the loop
  run refactor phase
  rerun focused verification
  run integration verification
  run final review gate using $subagent-review-loop
  if final review finds actionable issues:
    update plan
    route each issue to the responsible worker persona or a fresh replacement
    rerun affected verification
    continue the loop
  after the final clean review pass, commit, push, and create a PR
until complete or blocked
```

## Review Gates

Review gates use `$subagent-review-loop` and code-review stance. Each review pass must use exactly five newly spawned reviewer subagents, and the loop continues until all five reviewers in the newest pass report no actionable findings. Findings are prioritized by correctness, regressions, missing tests, data loss, security, MCP contract mismatch, async/concurrency issues, architecture/ADR violations, and change size.

Use review lenses that fit the change:

- MCP contract lens: tool names, parameters, return shapes, async behavior, error messages, and README/client documentation.
- Indexing/vector-store/storage lens: deduplication, content hashes, Chroma
  mutations, LlamaIndex usage, SQLite lifecycle/citation metadata, tombstones,
  local data safety, and rollback risk.
- Fetching/network lens: external source connector behavior, timeouts, retries,
  rate limits, credential handling, partial snapshots, and partial failure
  handling.
- Async/background lens: `asyncio.create_task`, status reporting, swallowed exceptions, and caller-visible completion semantics.
- Config/secrets lens: environment variables, token handling, `.env`, local data paths, and logging.
- Test-quality lens: focused tests, compile/import checks, mocked external APIs, and smoke checks.
- Docs-only lens: path references, skill names, phase order, whitespace checks,
  and staged diff checks.

When subagent review is unavailable due to tool policy or the user did not authorize delegation, do not pretend it ran. Stop and report the blocker. Continue with local self-review only if the user explicitly approves bypassing `$subagent-review-loop`.

## Failure Classification

Classify failures before retrying:

- `implementation bug`: code does not satisfy requested behavior.
- `test bug`: test setup or expectation is wrong.
- `environment blocker`: local services, credentials, network, permissions, or tools are missing.
- `dependency issue`: uv/pip dependency resolution, Python version, or package import failure.
- `unclear requirement`: behavior cannot be inferred safely.

Local, fixable failures return to implementation/test after updating the plan. Real blockers or unsafe ambiguity should be reported to the user.

## Verification Standards

Docs-only changes limited to `AGENTS.md`, `README.md`, `.agents/`, and
`docs/**/*.md` use:

```bash
rg --files AGENTS.md README.md docs .agents/docs .agents/skills
git status --short --branch
git diff --check
git diff --cached --check
```

Stage the relevant docs-only files before running `git diff --cached --check`;
otherwise the cached check does not cover new untracked docs or plan files.

Python code changes use the smallest useful check first:

```bash
python -m compileall api core environments fetching indexing search storage wiki main.py
```

Prefer uv when the local uv workspace is healthy:

```bash
uv run python -m compileall api core environments fetching indexing search storage wiki main.py
uv run pytest
```

MCP tool changes should include an import/startup smoke when it can run without
real credentials or without mutating user Chroma data or SQLite metadata.
External live checks against Notion, Tistory, GitHub, or configured website/docs
sources require user approval.

MCP/wiki generation changes should include the safe FastMCP wiki smoke whenever
appropriate:

```bash
python scripts/smoke_generate_wiki_page.py --mode fake
```

For PR validation, always consider whether live wiki smoke is appropriate. Run
the optional GitHub live smoke only when network access is available, the user
has approved the source, and an appropriate repository is configured. The live
smoke must use temporary Chroma/SQLite paths, write Markdown under
`/private/tmp` or a caller-provided output directory, skip gracefully when the
source or network is unavailable, and avoid printing raw secrets or tokens:

```bash
python scripts/smoke_generate_wiki_page.py \
  --mode github \
  --github-repository owner/repo@main \
  --topic README \
  --require-generated
```

Verification must precede `$subagent-review-loop`. If review findings require changes, rerun the affected verification before starting the next fresh five-reviewer subagent review pass.

## Delivery

Final reports include:

- Plan document path
- Changed files
- Verification commands and results
- Review status and any subagent-review limitation
- Known blockers or skipped checks
- Commit, push, and PR status, including the PR URL after successful delivery

After the final clean `$subagent-review-loop` pass, do not stop at local completion. Use `.agents/docs/github-workflow.md` to stage only relevant files, commit, push the `feature/...` branch, and create a `main`-base PR by default. If the user explicitly requested local-only work, or if auth, permissions, network, branch safety, or review availability blocks PR delivery, report that blocker instead of silently skipping the PR.
