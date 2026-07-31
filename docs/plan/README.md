# Plan Documents

`docs/plan/` contains implementation plan documents for non-exempt
file-changing work.

## When to Write a Plan

Create a plan document after branch preflight and before `harness-plan`,
`harness-implement`, `harness-test`, or any non-plan target file edit unless
the task is plan-exempt.

Plan-exempt work includes:

- Docs- or instruction-only changes that do not authorize or initiate live API
  access, user-data/destructive action, or a substantive runtime security,
  public-contract, or maintained-architecture change (including a substantive
  update to `.agents/docs/architecture.md`). Process/testing/review
  documentation alone remains exempt. Non-substantive corrections to that
  architecture document (typos, links, or non-design wording) remain
  plan-exempt.
- Truly trivial atomic changes that are localized, low risk, easy to revert,
  and do not add a feature or change a public/MCP contract,
  persistence/schema behavior, dependency, security boundary, user-data
  handling, or maintained architecture.
- Read-only questions, code review requests, command-output checks, and
  explanations.

If any exemption criterion is uncertain, write a plan. Plan-exempt work skips
the plan document and `harness-plan` phase, but it does not skip applicable
branch, TDD, verification, functional-smoke, review, or delivery gates. Record
the exemption reason in task updates, reviewer context, and the final handoff.

Feature work, non-trivial bug fixes, non-trivial refactoring or test work, MCP
contract changes, indexing/search behavior changes, non-trivial or
runtime-affecting configuration changes, and production/runtime architecture
changes require a plan. A docs-only edit to `.agents/docs/architecture.md`
remains plan-exempt only when it is a non-substantive correction (for example
typos, broken links, or wording that does not change maintained design
assumptions). A substantive update to that maintained architecture document is
a maintained-architecture change and requires a plan, matching the
docs/instruction exemption criteria above.

## File Naming

Use this format:

```text
YYYY-MM-DD-short-task-name.md
```

Keep the name stable for the work item. If the plan changes during retry loops or review fixes, update the same file instead of creating a new one.

## Required Sections

Each plan document must include:

- User request
- Branch preflight result
- Scope and non-goals
- Acceptance criteria
- Step breakdown, if the work needs multiple ordered steps
- Files likely to change
- For feature/behavior changes, TDD RED evidence fields: command,
  unit/integration/E2E test names or layers, non-zero exit code, expected
  failure signature, missing-behavior explanation, and confirmation that the
  evidence predates production edits
- For pure refactor, test-only, or other non-behavior code work, a TDD RED
  `n/a` rationale instead of a manufactured missing-behavior failure
- TDD GREEN evidence fields: focused unit, integration, and E2E commands/results
- Full-suite evidence for `./scripts/verify_all.sh`
- Matching eval gate evidence when required by feature scope
- Improvement performance delta fields when the work improves or claims to
  improve an existing measurable capability:
  - Declared metrics: name(s), unit, measurement command or method, expected
    improvement direction
  - Baseline before production/code edits using temporary Chroma/SQLite paths
    and mocked connectors; never inspect or mutate user data without both
    explicit user approval and a plan
  - After measurement: take one after measurement after the latest applicable
    gate — after `./scripts/verify_all.sh` and matching eval when those gates
    apply; otherwise after focused GREEN and post-refactor — using temporary
    Chroma/SQLite paths and mocked connectors; never inspect or mutate user
    data without both explicit user approval and a plan. Do not require one
    remeasure per phase
  - Delta table: metric, unit, before, after, absolute delta, relative delta
    (% when meaningful), command/method, environment notes (no secrets,
    credentials, PII, user content, or real user-data paths), one-line
    interpretation
  - Quality claims use retained eval scores as delta-table metrics;
    latency/throughput use runtime metrics and remain informational for
    quality gates (do not treat `runtime_metrics` as deterministic quality
    evidence)
  - Brand-new features with no prior comparable surface:
    `n/a — no prior baseline` with rationale (optional after-only baseline
    using temporary Chroma/SQLite paths and mocked connectors; never inspect
    or mutate user data without both explicit user approval and a plan)
  - Non-improvement work: `n/a` with a short rationale
  - Do not invent fake before numbers; keep deterministic quality-eval
    artifacts separate from optional runtime/latency metrics
- Functional smoke matrix or planned matrix rows before review
- Three-reviewer evidence rows for bugs/correctness, security/data safety, and
  performance/reliability
- Architecture constraints
- Risks and rollback notes
- Progress log

## Step Design

When a work item needs multiple ordered steps, write steps that can be executed and reviewed without relying on hidden conversation context.

- Keep each step focused on one module, contract, or behavior slice.
- Include files to read, required prior outputs, and the exact boundary of the work.
- Prefer interface-level direction over transcription. Name expected functions, classes, contracts, and invariants, then follow local patterns.
- Make acceptance criteria executable with commands or concrete smoke scenarios.
- Write constraints as specific rules. Use "Do not change X because Y" instead of broad warnings.
- Use short kebab-case labels such as `mcp-tool-contract`, `indexing-dedup`, or `fetcher-timeout`.

## Progress Tracking

Use this shape unless a smaller log is clearly enough:

```markdown
| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created `feature/example`. | `git status --short` |
| Improvement delta declare | pending | Metric names, units, command/method, expected direction; or `n/a` rationale. | Pending |
| Improvement baseline | pending | Capture before production edits when improvement-scoped (temp Chroma/SQLite + mocked connectors; no user-data access). | Pending |
| TDD RED | pending | Run the smallest new/changed test before production edits, or record non-behavior `n/a`. | Command, test/layer, non-zero exit, expected signature, or `n/a` rationale |
| Focused unit GREEN | pending | Run focused unit coverage. | Pending |
| Focused integration GREEN | pending | Run focused integration coverage. | Pending |
| Focused E2E GREEN | pending | Run retained deterministic E2E coverage. | Pending |
| Full suite GREEN | pending | Run `./scripts/verify_all.sh`. | Pending |
| Matching eval | pending | Record full-suite quality-eval evidence or focused matching eval when required by feature scope; else `n/a`. | Pending |
| Improvement after/delta | pending | One after measurement after the latest applicable gate (temp Chroma/SQLite + mocked connectors; no user-data access); record delta table, or keep `n/a`. | Pending |
| Functional smoke | pending | Exercise task-relevant inventory through safest caller surfaces, or record blocked/gated. | Pending |
```

Status values:

- `pending`: not started
- `in_progress`: currently being worked
- `completed`: finished and verified at the planned level
- `blocked`: waiting on user input, credentials, local services, permissions, or external systems
- `error`: attempted and failed after local retry

When implementation, testing, review, or integration discovers new information, update the same progress table before continuing.

## Execution Rule

After writing the plan, run harness phases according to that plan. If implementation, testing, integration, or review discovers new information, update the plan first and then continue.
