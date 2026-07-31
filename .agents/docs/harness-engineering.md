# Harness Engineering

## Purpose

This document is the default implementation harness contract for `MCPContentSearch`. When the user asks for feature work, fixes, refactors, tests, or other file-changing work, read this document and run the phase skills in order.

The harness makes planning, test-driven implementation, verification, review,
refactoring, and integration repeatable. If verification fails, classify the
failure, update the plan document when one is required, and return to the
relevant implementation or test phase.

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
plan decisions, worker persona design, delegation, result synthesis,
integration, review routing, and final delivery.

All feature and behavior changes use strict Red-Green-Refactor TDD. Before
production code changes, add or update unit, integration, and deterministic
functional E2E coverage, then run the smallest relevant new or changed test to
observe the expected failure. Before production edits, record the exact
command, covered unit/integration/E2E test layers or names, non-zero exit code,
expected failure signature, and why it represents the missing behavior. Make
the minimum production change that turns the focused tests green, refactor only
while they remain green, then run `./scripts/verify_all.sh`. Every required test
and the full repository suite must pass before any review, commit, push, or PR.
Environment or dependency failures block delivery rather than converting the
gate into a partial fallback.

If the feature changes retrieval quality, ranking,
grounding, citation selection, answer quality, or another quality-sensitive
output already modeled by retained local evaluations, it must also add or
update eval coverage and satisfy the matching eval gate after
`./scripts/verify_all.sh` succeeds and before improvement after/delta (or an explicit `n/a` rationale), functional smoke, or review. Prefer
recording the full-suite deterministic quality eval layer evidence when that
layer already executed the matching surface; otherwise run the focused matching
eval command. If the feature falls within retained local eval coverage but no
exact retained eval surface exists yet, it must extend an existing retained
eval surface and satisfy the eval gate only after that full-suite gate. Do not
run matching eval commands during focused GREEN; that lane stops so refactor can
run next.

0. Branch preflight: follow `.agents/docs/github-workflow.md`.
1. Plan decision: create or update
   `docs/plan/YYYY-MM-DD-short-task-name.md` and run
   `.agents/skills/harness-plan/SKILL.md`, unless the task is plan-exempt.
   Docs/instruction-only work and truly trivial atomic low-risk changes are
   plan-exempt under the criteria below.
2. Subagent orchestration design: choose bounded implementation, testing,
   documentation, or integration worker personas when delegation is available;
   use `.agents/skills/harness-multitask/SKILL.md` when work needs splitting.
   For improvement-scoped work, the plan (or plan-exempt task evidence) must
   already declare the metrics to measure; see Improvement Performance Delta.
3. Improvement performance baseline: when work improves or claims to improve an
   existing measurable capability, capture the declared baseline metrics before
   production/code edits (alongside or just before the TDD red gate) using
   temporary Chroma/SQLite paths and mocked connectors; never inspect or mutate
   user data without both explicit user approval and a plan. Otherwise record
   `n/a` with a short rationale. Brand-new features with no prior comparable
   surface record `n/a — no prior baseline` with rationale.
4. TDD red gate: for feature/behavior changes, use
   `.agents/skills/harness-test/SKILL.md` to add or update unit, integration,
   and deterministic E2E coverage before production code and record the
   auditable expected focused failure. Pure refactor, test-only, or other
   non-behavior code work records RED as `n/a` with a rationale.
5. TDD green implementation: `.agents/skills/harness-implement/SKILL.md`,
   delegated to worker personas when useful and safe.
6. TDD green verification: use `.agents/skills/harness-test/SKILL.md` to pass
   the focused unit, integration, and E2E tests.
7. TDD refactor phase: `.agents/skills/harness-refactor/SKILL.md`; simplify only
   while the focused tests remain green.
8. Post-refactor full-suite gate: rerun affected focused tests, then require
   `./scripts/verify_all.sh` to pass.
9. Eval gate when required by feature scope: after `./scripts/verify_all.sh`
   succeeds, confirm the matching retained eval surface. Prefer recording the
   full-suite deterministic quality eval layer evidence when that layer already
   executed the matching surface; otherwise run the focused matching eval
   command. Never run matching eval during focused GREEN.
10. Improvement performance after/delta: when improvement-scoped, take one
    after measurement after the latest applicable gate — after
    `./scripts/verify_all.sh` and matching eval when those gates apply;
    otherwise after focused GREEN and post-refactor — using temporary
    Chroma/SQLite paths and mocked connectors; never inspect or mutate user
    data without both explicit user approval and a plan. Do not require one
    remeasure per phase. Always record the delta table. Keep earlier `n/a`
    rationales for non-improvement or no-prior-baseline work.
11. Functional smoke gate: `.agents/skills/harness-functional-smoke/SKILL.md`,
   which exercises the task-relevant feature inventory once through the safest
   real caller surfaces or records a safety-gated skip.
12. Middle review gate: `.agents/skills/harness-review/SKILL.md`, which runs the
   three-reviewer harness loop.
13. Integration phase: `.agents/skills/harness-integrate/SKILL.md`
14. Final review gate: `.agents/skills/harness-review/SKILL.md`, which runs the
   three-reviewer harness loop.
15. PR delivery: after the final clean three-reviewer pass, stage only relevant files, commit, push, and create a `main`-base PR by default unless the user explicitly asks for local-only work or a safety blocker prevents delivery. When the task is tied to a real GitHub issue, include a dedicated PR-body closing-keyword line such as `closes #59`. If no real issue exists, omit closing keywords instead of inventing one. Include the improvement performance delta summary (or `n/a` rationale) in the PR body and final handoff.

Docs/instruction-only work skips the code-specific TDD red, green
implementation, full-suite, and functional-smoke gates. It uses the docs-only
verification commands and retains the review and delivery gates. Record
improvement performance delta as `n/a` with a docs-only rationale. A trivial
plan-exempt code change does not skip TDD or the full-suite gate.

`.agents/skills/harness-engineering/SKILL.md` is the orchestrator for the full loop.

## Branch Preflight

Before editing target files:

1. Run `git status --short`.
2. Run `git branch --show-current`.
3. Run `git branch -vv` and `git worktree list` so local branch cleanup is safe and linked worktrees are visible.
4. If the current worktree is dirty, do not switch branches, pull, or delete branches there. If network is available, run `git fetch origin main`, then create an isolated worktree with a fresh `feature/...` branch from `origin/main`. If network or isolation is unavailable, record the blocker and ask the user before changing branch state.
5. If the current worktree is clean, switch to `main`.
6. If network is available, run `git fetch origin main` and `git pull --ff-only origin main` after reaching `main`. If network is restricted, record that freshness was not checked.
7. Delete only safe existing local non-`main` work branches before creating the new task branch, or from the isolated worktree after creating its fresh task branch when the original worktree is dirty. Delete only local refs, never remote branches. Prefer `git branch -d`; use `git branch -D` only after confirming there are no local-only commits, or after both a plan and explicit user approval authorize discarding them. Removing a branch checked out in a linked worktree likewise requires a plan and explicit approval.
8. Do not edit target files on `main`.
9. Create a fresh `feature/...` branch from updated `main` for every new task.
10. Reuse an existing `feature/...` branch only when the user explicitly asks to continue that branch.

Never run destructive cleanup such as `git reset --hard`,
`git checkout -- <file>`, deleting local Chroma data, deleting local SQLite
metadata, removing caches, or resetting local credentials without both a plan
and explicit user approval. Plan-exempt work must reclassify or keep the action
blocked.

## Plan Document

Non-exempt file-changing work must create or update a plan document after
branch preflight and before non-plan target edits.

Do not create a plan document or run `harness-plan` for:

- Docs- or instruction-only changes that do not authorize or initiate live API
  access, user-data/destructive action, or a substantive runtime security,
  public-contract, or maintained-architecture change. Process/testing/review
  documentation alone remains exempt.
- Truly trivial atomic changes that are localized, low risk, easy to revert,
  and do not add a feature or change a public/MCP contract,
  persistence/schema behavior, dependency, security boundary, user-data
  handling, or maintained architecture.

If any exemption criterion is uncertain, write a plan. Plan-exempt work must
record the exemption reason in task updates, reviewer context, and the final
handoff. It still runs applicable branch, TDD, verification, functional-smoke,
review, and delivery gates.

- File name: `YYYY-MM-DD-short-task-name.md`
- Required sections are listed in `docs/plan/README.md`.
- Include branch preflight result, scope/non-goals, acceptance criteria, expected files, verification plan, architecture constraints, risk/rollback notes, and progress log.
- For improvement-scoped work, declare metrics early and reserve progress-log rows for baseline, after, and delta evidence; see Improvement Performance Delta and `docs/plan/README.md`.
- If verification or review changes the plan, update the same plan document before continuing.
- Final reports should include the plan document path, or the plan-exempt reason.

## Improvement Performance Delta

Whenever work improves or claims to improve an existing capability on a
measurable axis (latency, throughput, resource use, ranking/retrieval/answer
quality, sync speed, or similar), record a before/after performance delta.
Agents must document what improved and by how much; do not invent fake before
numbers.

### When it applies

- **Improvement-scoped**: the request or implementation claims a measurable
  improvement to an existing comparable surface. Full baseline → after → delta
  recording is mandatory.
- **Brand-new feature with no prior comparable surface**: record
  `n/a — no prior baseline` with rationale. Optionally record an after-only
  baseline for future comparisons using temporary Chroma/SQLite paths and
  mocked connectors; never inspect or mutate user data without both explicit
  user approval and a plan. Do not invent fake before numbers.
- **Non-improvement work** (pure docs, unrelated bug fix with no improvement
  claim, and similar): record `n/a` with a short rationale. Do not force fake
  benchmarks.

Plan-exempt improvement-scoped work still records the same evidence in task,
review, and final handoff context.

### What to record

1. **Declare metrics early** in the plan when one exists; otherwise in
   task/review evidence: metric name(s), unit, measurement command or method,
   and expected improvement direction.
2. **Capture a baseline BEFORE production/code edits** (or before the
   improvement lands) using the safest local-first surfaces: focused
   benchmarks, retained evals under `tests/evals`, fixture runners, temporary
   Chroma/SQLite paths, and mocked connectors. Never inspect or mutate user
   data without both explicit user approval and a plan.
3. **Capture the same metric(s) AFTER** with one after measurement after the
   latest applicable gate — after `./scripts/verify_all.sh` and the matching
   eval gate when those gates apply; otherwise after focused GREEN and
   post-refactor — using temporary Chroma/SQLite paths and mocked connectors;
   never inspect or mutate user data without both explicit user approval and a
   plan. Do not require one remeasure per phase.
4. **Always record a delta table** with at least: metric, unit, before, after,
   absolute delta, relative delta (% when meaningful), command/method,
   environment notes, and a one-line interpretation (what improved and by how
   much). If a metric did not improve or regressed, say so explicitly.
   Environment notes must not include secrets, credentials, PII, user content,
   or real user-data paths.

Quality claims use retained eval scores as delta-table metrics.
Latency/throughput claims use runtime metrics and remain informational for
quality gates; do not treat `runtime_metrics` (or similar) as deterministic
quality evidence. Keep deterministic quality-eval artifacts separate from
optional runtime/latency metrics. Deterministic retained eval outputs stay the
source of truth for quality gates.

### Where to record

- Plan document progress log and dedicated improvement-delta fields when a plan
  exists (`docs/plan/README.md`).
- Plan-exempt task updates, reviewer context, and final handoff otherwise.
- Final handoff and PR body: include the delta summary or the `n/a` rationale
  alongside verification, matching-eval, functional-smoke, and review evidence.
  Delta environment notes, handoff text, and PR text must not include secrets,
  credentials, PII, user content, or real user-data paths.

### Review enforcement

Reviewer 3 (performance/reliability) must treat missing or incoherent
before/after/delta evidence for improvement-scoped work as an actionable
finding. Coherent `n/a` rationales for brand-new-without-baseline or
non-improvement work are acceptable.

## Architecture

Planning must read:

- `.agents/docs/architecture.md`

Review gates must check that the diff does not violate the maintained
architecture doc. If a change intentionally changes long-term architecture,
update `.agents/docs/architecture.md` in the same work item.

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
explicit. After branch preflight and the plan decision, the main agent should:

- Inspect the plan when one is required and update it when the requested work,
  repo state, or verification results change.
- Define task-specific worker personas for implementation, tests,
  documentation, refactor, or integration. Each persona needs a bounded goal,
  owned files or modules, required context, non-goals, acceptance criteria, and
  expected verification.
- Delegate work to subagents when the active tool policy allows it and file
  ownership is clear. Tell workers not to commit, push, open PRs, change
  unrelated files, inspect secret values, inspect or mutate user
  Chroma/SQLite data, or bypass repo-local harness rules. Secret values and
  user-data operations are never delegable; an approved planned operation stays
  with the main agent.
- Collect worker outputs, inspect the diff directly, resolve conflicts, and
  decide whether more implementation, test, docs, or integration work is
  needed before review.
- Route actionable issues to the responsible worker persona, or to a fresh
  replacement worker with the same ownership boundary if the original worker is
  unavailable. Update the plan before retrying when one is required.
- Ask the user only for unsafe ambiguity, credentials, destructive operations,
  local data mutation, unavailable delegation/review tools, or external
  approval. Do not ask for routine implementation choices that can be decided
  from repo docs, architecture, and the task context.

Implementation/execution worker subagents and harness reviewer subagents are
separate roles. Workers may edit within their assigned boundary when
delegated. Reviewers inspect only and must not edit files.

## Retry Loop

Use this control loop:

```text
read repository instructions
read harness and GitHub workflow
read architecture
run branch preflight with GitHub workflow dirty/clean worktree safeguards
decide whether the task is plan-exempt
if non-exempt, write or update docs/plan plan and run planning phase
if exempt, record the reason without creating a plan
design worker personas and task ownership
if needed, run multitask phase
  declare improvement metrics early when improvement-scoped; otherwise record
  performance-delta n/a with rationale
if docs/instruction-only:
  run docs-only path/status/unstaged/cached verification
  record functional smoke as n/a
  record improvement performance delta as n/a with docs-only rationale
  repeat:
    run middle three-reviewer harness gate
    if no actionable findings:
      exit the middle-review loop
    fix docs and rerun docs-only verification
    continue directly to a fresh middle-review pass
  until clean or blocked
  if blocked:
    record the review blocker and stop
  run docs-only integration verification
  repeat:
    run final three-reviewer harness gate
    if no actionable findings:
      exit the final-review loop
    fix docs and rerun docs-only verification
    refresh docs-only integration evidence from that verification
    continue directly to a fresh final-review pass
  until clean or blocked
  if blocked:
    record the final-review blocker and stop
  after the final clean review pass, commit, push, and create a PR
else:
  if improvement-scoped:
    capture declared metric baseline before production/code edits using safest
    local-first surfaces (temporary Chroma/SQLite paths and mocked connectors);
    never invent fake before numbers; never inspect or mutate user data without
    both explicit user approval and a plan
  else if brand-new with no prior comparable surface:
    record n/a — no prior baseline with rationale; optionally after-only
    baseline using temporary Chroma/SQLite paths and mocked connectors;
    never inspect or mutate user data without both explicit user approval
    and a plan
  else:
    record performance-delta n/a with short non-improvement rationale
  if feature/behavior-changing:
    write/update unit, integration, and E2E tests first
    run the smallest relevant new or changed test and capture expected RED
    before production edits, record command, layers/tests, non-zero exit code,
    expected failure signature, and missing-behavior explanation
  else:
    record RED as n/a with the pure-refactor/test-only/non-behavior rationale
    record the relevant focused GREEN baseline
  delegate implementation/integration work to bounded worker personas where possible
  collect worker results, inspect the diff, and synthesize the main-agent result
  run focused unit, integration, and E2E tests until GREEN
  stop focused GREEN without running matching eval commands
  run refactor phase while focused tests remain GREEN
  rerun affected focused tests
  run ./scripts/verify_all.sh for the current diff and require the full suite to pass
  satisfy any matching eval required by feature scope only after verify_all
  (record full-suite eval-layer evidence when it already covered the matching
  surface; otherwise run the focused matching eval command)
  if improvement-scoped:
    take one after measurement after the latest applicable gate (after
    verify_all and matching eval when those gates apply; otherwise after
    focused GREEN and post-refactor) using temporary Chroma/SQLite paths and
    mocked connectors; never inspect or mutate user data without both
    explicit user approval and a plan; do not require one remeasure per phase;
    record the delta table (metric, unit, before, after, absolute/relative
    delta, command/method, environment notes without secrets/credentials/PII/
    user content/real user-data paths, one-line interpretation); state
    explicitly if a metric did not improve or regressed
  run functional smoke gate using harness-functional-smoke
  repeat:
    run middle three-reviewer harness gate
    if no actionable findings:
      exit the middle-review loop
    update plan when one is required
    route each issue to the responsible worker persona or a fresh replacement
    if a finding needs a behavior-changing code/config change:
      return to unit/integration/E2E RED before production edits
      record auditable RED evidence
      implement GREEN, refactor, rerun affected tests, and run verify_all
      satisfy any matching eval required by feature scope only after verify_all
      (record full-suite eval evidence when already covered; else rerun matching eval)
      refresh improvement after/delta when improvement-scoped using temporary
      Chroma/SQLite paths and mocked connectors; never inspect or mutate user
      data without both explicit user approval and a plan
      rerun affected functional smoke entries
    else if a finding needs a non-behavior code/config/test change:
      record RED as n/a without manufacturing a failure
      rerun affected focused tests and run verify_all
      satisfy any matching eval required by feature scope only after verify_all
      (record full-suite eval evidence when already covered; else rerun matching eval)
      refresh improvement after/delta when improvement-scoped using temporary
      Chroma/SQLite paths and mocked connectors; never inspect or mutate user
      data without both explicit user approval and a plan
      rerun affected functional smoke entries
    else:
      rerun docs-only verification without fake RED
    continue directly to a fresh middle-review pass
  until clean or blocked
  if blocked:
    record the review blocker and stop
  run integration verification
  if improvement-scoped:
    refresh improvement after/delta after the latest applicable post-fix gate
    using temporary Chroma/SQLite paths and mocked connectors; never inspect
    or mutate user data without both explicit user approval and a plan
  rerun or refresh functional smoke entries affected by integration
  repeat:
    run final three-reviewer harness gate
    if no actionable findings:
      exit the final-review loop
    update plan when one is required
    route each issue to the responsible worker persona or a fresh replacement
    if a finding needs a behavior-changing code/config change:
      return to unit/integration/E2E RED before production edits
      record auditable RED evidence
      implement GREEN, refactor, rerun affected tests, and run verify_all
      satisfy any matching eval required by feature scope only after verify_all
      (record full-suite eval evidence when already covered; else rerun matching eval)
      refresh improvement after/delta when improvement-scoped using temporary
      Chroma/SQLite paths and mocked connectors; never inspect or mutate user
      data without both explicit user approval and a plan
      rerun affected functional smoke entries
    else if a finding needs a non-behavior code/config/test change:
      record RED as n/a without manufacturing a failure
      rerun affected focused tests and run verify_all
      satisfy any matching eval required by feature scope only after verify_all
      (record full-suite eval evidence when already covered; else rerun matching eval)
      refresh improvement after/delta when improvement-scoped using temporary
      Chroma/SQLite paths and mocked connectors; never inspect or mutate user
      data without both explicit user approval and a plan
      rerun affected functional smoke entries
    else:
      rerun docs-only verification without fake RED
    refresh integration evidence
    continue directly to a fresh final-review pass
  until clean or blocked
  if blocked:
    record the final-review blocker and stop
  after the final clean review pass, commit, push, and create a PR
```

## Review Gates

Review gates use `.agents/skills/harness-review/SKILL.md`. Each pass must spawn
exactly three fresh read-only reviewer subagents with different primary lenses:

1. Bugs and correctness: regressions, API/MCP contracts, TDD chronology,
   unit/integration/E2E quality, error handling, and architecture correctness.
2. Security and data safety: secrets, authentication/authorization boundaries,
   privacy, input validation, dependency risk, local Chroma/SQLite safety,
   destructive behavior, and external-service exposure.
3. Performance and reliability: latency or complexity regressions, resource
   use, async/concurrency, timeouts/retries, lifecycle/cleanup, scalability,
   observability, operational failure modes, and improvement-scoped
   before/after/delta evidence (missing or incoherent delta evidence is
   actionable).

Every reviewer may report issues outside the primary lens, but its prompt must
name the assigned lens and task-relevant checks. The loop continues until all
three reviewers in the newest pass report no actionable findings.

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
- Test-quality lens: auditable pre-production RED evidence, unit/integration/E2E
  coverage, focused GREEN results, full-suite GREEN result, mocked external
  APIs, and smoke checks.
- Functional-smoke lens: task-relevant inventory coverage, caller surfaces,
  safe data modes, result vocabulary, blocked/gated rows, local substitutes, and
  reruns after review fixes.
- Improvement-performance-delta lens: declared metrics, pre-edit baseline,
  one after measurement after the latest applicable gate, coherent delta table
  or explicit `n/a` rationale, quality claims via retained eval scores versus
  informational runtime/latency metrics, and environment notes free of secrets,
  credentials, PII, user content, and real user-data paths.
- Docs-only lens: path references, skill names, phase order, whitespace checks,
  and staged diff checks.

When subagent review is unavailable due to tool policy or the user did not
authorize delegation, do not pretend it ran. Stop and report the blocker.
Continue with local self-review only if the user explicitly approves bypassing
the three-reviewer harness loop.

## Failure Classification

Classify failures before retrying:

- `implementation bug`: code does not satisfy requested behavior.
- `test bug`: test setup or expectation is wrong.
- `environment blocker`: local services, credentials, network, permissions, or tools are missing.
- `dependency issue`: uv/pip dependency resolution, Python version, or package import failure.
- `unclear requirement`: behavior cannot be inferred safely.

Local, fixable failures return to implementation/test after updating the plan
when one is required. Real blockers or unsafe ambiguity should be reported to
the user.

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

Feature and behavior changes must start by adding or updating unit, integration,
and deterministic E2E coverage before production code. Run the smallest
relevant new or changed test and confirm it fails for the expected missing
behavior, not because of a broken fixture or environment. Before production
edits, record the exact command, covered test layers/names, non-zero exit code,
expected failure signature, and missing-behavior explanation in the plan or
plan-exempt task evidence.

Python code changes use the smallest useful check first:

```bash
python -m compileall api core environments fetching indexing search storage main.py
```

Prefer uv when the local uv workspace is healthy:

```bash
uv run python -m compileall api core environments fetching indexing search storage main.py
uv run pytest
```

Feature additions must add or update unit, integration, and retained
deterministic functional E2E coverage for the new behavior. The focused tests
must pass after implementation.
When the added feature changes retrieval quality, ranking, grounding, citation
selection, answer quality, or another quality-sensitive output that is already
modeled by retained local evaluations, they must add or update eval coverage
and satisfy the matching eval gate only after `./scripts/verify_all.sh`
succeeds and before improvement after/delta (or an explicit `n/a` rationale), functional smoke, or review, never during focused GREEN.
Prefer recording full-suite eval-layer evidence when already covered; otherwise
run the focused matching eval command.

Before review for code-changing work, run the repo-wide functional E2E
regression gate that exercises retained end-to-end feature workflows
(ContextWiki MCP flows, connector E2E flows, search, citation answer, indexing,
and storage behavior):

```bash
./scripts/verify_functional_e2e.sh
```

This unconditional gate run is a broader regression check for code-changing
work. It is separate from the narrower rule above about when a work item must
add or update retained functional E2E coverage for a newly added or changed
workflow, and that added or updated retained E2E coverage must itself be
executed before review, normally by extending the retained suite exercised by
`./scripts/verify_functional_e2e.sh`.

After focused GREEN, refactoring, and affected focused-test reruns, every
code-changing work item must run:

```bash
./scripts/verify_all.sh
```

The command must exit successfully before review or delivery. It includes the
functional E2E gate. An environment or dependency failure is a blocker; a
compile-only or partial pytest fallback may help diagnosis but does not satisfy
the all-tests-pass gate.

Current local eval paths include deterministic eval tests under
`tests/evals` such as `uv run pytest -q tests/evals` plus the fixture runner at
`PYTHONPATH=. python scripts/run_contextwiki_eval.py`. Satisfy the matching
eval gate only after `./scripts/verify_all.sh` succeeds and before improvement
after/delta (or an explicit `n/a` rationale), functional smoke, or review; do
not run matching eval during focused GREEN. Prefer
recording the full-suite deterministic quality eval layer evidence when that
layer already executed the matching surface; otherwise run the focused matching
eval command. If a feature falls within the repo's retained local eval coverage
but no matching retained eval surface exists yet, treat that as missing required
coverage: extend an existing retained eval surface such as `tests/evals` or the
fixture runner during coverage work and satisfy the eval gate only after the
full-suite gate. Features outside the current retained local eval coverage are
not subject to this eval requirement until the retained eval scope changes.

MCP tool changes should include an import/startup smoke when it can run without
real credentials or without mutating user Chroma data or SQLite metadata.
External live checks against Notion, Tistory, or GitHub sources require both
explicit user approval and a plan. Plan-exempt work must be reclassified before
the live check or keep it `blocked/gated` and use a fake/temp substitute.

After focused GREEN, refactoring, affected-test reruns, a successful
`./scripts/verify_all.sh`, and any matching eval gate required by feature
scope (record full-suite quality-eval evidence when already covered; otherwise
run the focused matching eval command), record improvement after/delta when
improvement-scoped using temporary Chroma/SQLite paths and mocked connectors;
never inspect or mutate user data without both explicit user approval and a
plan, then run the functional smoke gate in
`.agents/skills/harness-functional-smoke/SKILL.md` before any review gate. The
smoke matrix must start from the task-relevant inventory of retained MCP tools,
source-sync paths, status surfaces, search, citation answers, and other
user-visible features. Mark each row `passed`, `failed`, `not affected`, or
`blocked/gated`; do not silently omit unchanged but relevant core workflows.
Use fake services, mocked connectors, and temporary Chroma/SQLite paths before
considering live external checks. A skipped live check is acceptable only when
the matrix records the safety reason, needed user approval, and nearest local
substitute.

Verification, any matching eval gate required by feature scope (only after
`./scripts/verify_all.sh`, prefer recording full-suite quality-eval evidence
when already covered), improvement after/delta when improvement-scoped, and
functional smoke must precede the harness review loop. If review findings
require changes, rerun the affected verification, matching eval gate when in
scope, refresh improvement after/delta evidence when improvement-scoped using
temporary Chroma/SQLite paths and mocked connectors; never inspect or mutate
user data without both explicit user approval and a plan, and affected smoke
entries before starting the next fresh three-reviewer pass.

For improvement-scoped work, keep the delta table in the plan or plan-exempt
evidence. Quality claims use retained eval scores as delta-table metrics;
latency/throughput use runtime metrics and remain informational for quality
gates. Deterministic quality-eval artifacts remain separate from optional
runtime/latency metrics such as `runtime_metrics.json`.

## Delivery

Final reports include:

- Plan document path, or plan-exempt reason
- Changed files
- Verification commands and results
- Matching eval gate evidence when required by feature scope (recorded
  full-suite quality-eval evidence or focused matching command result)
- Improvement performance delta summary (delta table or `n/a` rationale)
- Functional smoke matrix results, including blocked/gated checks and local substitutes
- Review status and any subagent-review limitation
- Known blockers or skipped checks
- Commit, push, and PR status, including the PR URL after successful delivery

After the final clean three-reviewer pass, do not stop at local completion. Use
`.agents/docs/github-workflow.md` to stage only relevant files, commit, push the
`feature/...` branch, and create a `main`-base PR by default. When the task is
tied to a real GitHub issue, include a dedicated PR-body closing-keyword line
such as `closes #59` so the issue closes on merge. If no real issue exists,
omit closing keywords instead of inventing one. The PR body must include the
improvement performance delta summary or `n/a` rationale alongside verification,
smoke, and review evidence. If the user explicitly
requested local-only work, or if auth, permissions, network, branch safety, or
review availability blocks PR delivery, report that blocker instead of
silently skipping the PR.
