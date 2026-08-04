# Repository Instructions

## Project Harness

- When the user asks to implement, add, fix, refactor, or test behavior, first read `.agents/docs/harness-engineering.md`, then follow `.agents/skills/harness-engineering/SKILL.md`.
- Harness planning and review must read `.agents/docs/architecture.md`.
- Harness phase skills live under `.agents/skills/`: `harness-plan`, `harness-multitask`, `harness-implement`, `harness-test`, `harness-functional-smoke`, `harness-review`, `harness-refactor`, and `harness-integrate`.
- Branch, commit, push, PR, and PR-watch policy is defined in `.agents/docs/github-workflow.md`.
- File-changing work starts with branch preflight from the latest `main`: if the worktree is clean, switch to `main`, fast-forward it from `origin/main` when network is available, delete only safe local non-`main` work branches using `.agents/docs/github-workflow.md` safeguards, then create a fresh `feature/...` branch before target edits.
- If the starting worktree is dirty, do not switch, pull, or delete branches
  there. Fetch `origin/main` when network is available, then create an isolated
  worktree with a fresh `feature/...` branch from `origin/main`; if fetch or
  isolation is unavailable, ask or report the blocker. Preserve local-only
  commits and linked-worktree branches; discarding commits or removing linked
  worktrees requires both explicit user approval and a plan. Do not edit target
  files on `main`.
- After branch preflight and before non-plan target edits, create or update a
  plan document under `docs/plan/` unless the task is plan-exempt.
  Docs/instruction-only changes and truly trivial atomic changes are
  plan-exempt. A docs/instruction change stops being exempt only when it
  authorizes or initiates live API access, user-data/destructive action, or a
  substantive runtime security, public-contract, or maintained-architecture
  change (including a substantive update to `.agents/docs/architecture.md`);
  process/testing/review documentation alone remains exempt. Non-substantive
  corrections to that architecture document (typos, links, or non-design
  wording) remain plan-exempt. A trivial atomic change must be localized, low
  risk, easy to revert, and must not add a feature or change a public/MCP
  contract, persistence/schema behavior, dependency, security boundary,
  user-data handling, or maintained architecture. If any criterion is
  uncertain, write a plan.
- Plan-exempt work skips both the plan document and `harness-plan` phase; record the exemption reason in the task update, review context, and final handoff. All other branch, TDD, verification, functional-smoke, review, and delivery gates still apply when relevant.
- For planned work, the main agent is the CEO/orchestrator for file-changing harness work, not the default implementer. Before non-plan target edits, discover available subagent/delegation tools unless an equivalent callable subagent tool is already available in the active tool list.
- For any work that is not truly atomic, spawn role-specific implementation, testing, documentation, or integration workers before implementation begins. Assign each worker a bounded ownership area, expected files or modules, acceptance criteria, verification expectations, and an instruction to preserve other user/agent changes instead of reverting them.
- The main agent may implement directly only when the change is truly atomic. Record the reason in the plan progress log, or in the task update for plan-exempt work, before editing target files. Shared-file overlap is not a reason to bypass workers: for non-atomic work, use a single-owner worker or sequential worker handoff instead of parallel edits. If subagent tools are unavailable for non-atomic work, or no safe worker boundary can be created, stop before target edits and ask the user for explicit approval before bypassing worker orchestration. Do not silently collapse worker orchestration into self-implementation.
- Worker subagents and reviewer subagents are different roles. Workers may edit only inside their assigned boundary and must never commit, push, open PRs, inspect or print secret values, inspect or mutate local Chroma/SQLite or other user data, or perform destructive actions. Secrets, user-data access or mutation, and destructive actions are non-delegable. When explicitly approved user-data work is necessary, it remains main-agent-only and requires a plan rationale, bounded instructions, and rollback/safety notes. Harness reviewers are read-only and run only after verification, matching eval when in scope, improvement after/delta when improvement-scoped, and functional smoke.
- The main agent owns integration: collect worker outputs, inspect diffs, resolve conflicts, update the plan when one is required, run verification, route actionable findings back to the responsible worker persona or a fresh replacement with the same ownership boundary, and minimize human intervention. Ask the user only when safety, credentials, destructive actions, unavailable delegation/review tools, or genuinely unclear requirements require human judgment.
- Keep `.agents/docs/architecture.md` updated when changes affect ContextZip source connectors, source sync, document identity, chunking, tombstones, retrieval, citation metadata, answer behavior, or other maintained design assumptions. This document is the maintained human explanation layer and should not drift behind README or implementation.
- All feature and behavior changes must use strict test-driven development: add or update automated tests before production code, run the smallest relevant new or changed test to observe the expected failure, and record the command, covered test layers/names, non-zero exit code, expected failure signature, and missing-behavior explanation before production edits. Make the minimum implementation pass it, and refactor only while tests stay green.
- Every feature or behavior change must add or update coverage at all three levels: unit, integration, and deterministic functional E2E. Use mocks, fakes, and temporary Chroma/SQLite paths rather than live credentials or user data.
- After implementation, pass the focused unit, integration, and E2E tests,
  refactor while they stay green, rerun affected focused tests, then run
  `./scripts/verify_all.sh`. Do not proceed to functional smoke, review, commit,
  push, or PR delivery unless every required test and the full repository suite
  pass. An environment or dependency failure is a blocker, not permission to
  claim completion from a partial fallback.
- Docs/instruction-only changes do not invent code tests or run the code TDD cycle; use the docs-only verification commands below.
- After focused tests, refactor, affected-test reruns, and a successful
  `./scripts/verify_all.sh`, satisfy any matching eval gate required by feature
  scope only after that full-suite gate (prefer recording the full-suite
  deterministic quality eval layer evidence when it already covered the
  matching surface; otherwise run the focused matching eval command), then
  record improvement after/delta when improvement-scoped using temporary
  Chroma/SQLite paths and mocked connectors (never inspect or mutate user data
  without both explicit user approval and a plan), then run the
  functional smoke gate before the harness review loop: use
  `.agents/skills/harness-functional-smoke/SKILL.md` to
  exercise the task-relevant MCP/source-sync/user-visible feature inventory
  once through the safest real caller surfaces, not only unit-test the changed
  files. Record explicit safety blockers, approval needed, and nearest
  fake/temp substitutes in the plan, or in the review/final evidence for
  plan-exempt work.
- When work improves or claims to improve an existing measurable capability
  (latency, throughput, resource use, ranking/retrieval/answer quality, sync
  speed, or similar), record before/after performance delta evidence: declare
  metrics early; capture a local-first baseline before production edits using
  temporary Chroma/SQLite paths and mocked connectors (never inspect or mutate
  user data without both explicit user approval and a plan); take one after
  measurement after the latest applicable gate — after `./scripts/verify_all.sh`
  and matching eval when those gates apply, otherwise after focused GREEN and
  post-refactor — also using temporary Chroma/SQLite paths and mocked
  connectors (never inspect or mutate user data without both explicit user
  approval and a plan; do not require one remeasure per phase); and always
  record a delta table (or an explicit `n/a` rationale for brand-new surfaces
  without a prior baseline or for non-improvement work). Do not invent fake
  before numbers. Delta-table environment notes, handoff, and PR text must not
  include secrets, credentials, PII, user content, or real user-data paths.
  Final handoff and PR body must include the delta summary or `n/a` rationale;
  reviewer 3 treats missing or incoherent improvement-scoped delta evidence as
  actionable.
- After verification and before PR delivery for any code, configuration, documentation, or skill change, run the harness review loop with exactly three fresh read-only reviewer subagents per pass. Prompt reviewer 1 for bugs/correctness/API contracts/tests, reviewer 2 for security/privacy/data safety/secrets, and reviewer 3 for performance/reliability/async/concurrency/operability plus improvement-scoped before/after/delta coherence. Repeat until all three reviewers in the newest pass report no actionable findings. If subagent review is unavailable, stop and report the blocker instead of silently replacing it with self-review.
- If the main agent's synthesis or harness review reports an actionable
  behavior-changing code/config finding, return to the TDD RED gate before
  production edits, then rerun GREEN, refactor, affected tests,
  `./scripts/verify_all.sh`, any matching eval gate required by feature scope
  only after that full-suite gate (record full-suite eval evidence when already
  covered; otherwise rerun the matching eval), refresh improvement after/delta
  when improvement-scoped using temporary Chroma/SQLite paths and mocked
  connectors (never inspect or mutate user data without both explicit user
  approval and a plan), and functional smoke before a fresh three-reviewer
  pass. For a non-behavior code/config/test finding, record RED as `n/a`
  without manufacturing a failure, then rerun affected focused tests,
  `./scripts/verify_all.sh`, any matching eval gate required by feature scope
  only after that full-suite gate (record full-suite eval evidence when already
  covered; otherwise rerun the matching eval), refresh improvement after/delta
  when improvement-scoped using temporary Chroma/SQLite paths and mocked
  connectors (never inspect or mutate user data without both explicit user
  approval and a plan), and affected functional smoke.
  For a docs-only finding, rerun the lightweight docs verification without fake
  RED. Update the plan when one is required and route the fix to the responsible
  worker persona or a fresh replacement with the same ownership boundary.
- After the final clean three-reviewer pass, proceed to commit, push, and create a `main`-base PR by default. This is the standing repository workflow unless the user explicitly asks for local-only work or a safety blocker prevents PR delivery.
- If the user gives multiple independent tasks, split them during planning. Use separate worker ownership and branch/worktree boundaries when parallel work is allowed.
- Do not reply on GitHub, watch PRs, or push follow-up PR changes unless the user explicitly delegates that work.

## Project Structure

This repository is a Python MCP content search server built around FastMCP,
LlamaIndex, ChromaDB, and SQLite metadata storage.

- `main.py`: application composition and FastMCP server startup.
- `api/`: MCP tool registration and tool handlers.
- `core/`: shared models, exceptions, and utility code.
- `environments/`: runtime configuration and secret/environment loading.
- `fetching/`: Notion, Tistory, GitHub, and Obsidian source connectors.
- `indexing/`: document conversion, chunking, dedup/update detection, and vector indexing.
- `search/`: ContextZip retrieval, ranking, SQLite-backed active gates, and citation answer scaffolding.
- `storage/`: SQLite source/job/document/chunk lifecycle metadata and active retrieval checks.
- `docs/plan/`: plan documents written before non-exempt file-changing harness work.
- `.agents/`: local harness docs and phase skills.

## Development Commands

- `python main.py`: start the MCP server in the current environment.
- `python -m compileall api core environments fetching indexing search storage main.py`: syntax-check project modules without contacting external services.
- `uv run python -m compileall api core environments fetching indexing search storage main.py`: same check through uv when the uv environment is healthy.
- `uv run pytest`: preferred test command once tests exist.
- `./scripts/verify_functional_e2e.sh`: local functional E2E gate for retained source sync, MCP tool contracts, search, citation answer, indexing, and storage paths using tests and temporary data.
- `./scripts/verify_all.sh`: full verification entrypoint; includes compile, Ruff, mypy, Bandit, non-live pytest with coverage, and the functional E2E gate.

If `uv run ...` fails because the local environment or workspace metadata is not ready, report the failure and run the closest dependency-free check, such as `python -m compileall ...`.

## Coding Style

- Prefer small, focused modules that preserve the current boundaries: API tools, search, fetching, indexing, storage, configuration, and core models.
- Do not move secrets into logs, docs, tests, or plan files. Treat `environments/token.py`, environment variables, API keys, local Chroma contents, and local SQLite metadata as sensitive.
- Keep MCP tool response shapes stable unless the user requested a contract change.
- Use async boundaries deliberately. Do not create background tasks that hide critical failures unless the caller contract explicitly treats the work as background work.
- Add comments only where they explain non-obvious async, indexing, vector-store, or external API behavior.

## Testing and Verification

- For docs/instruction-only changes limited to `AGENTS.md`, `README.md`, `.agents/`, and `docs/**/*.md`, use lightweight verification: path listing, `git status --short --branch`, `git diff --check`, then stage the relevant docs-only files and run `git diff --cached --check` so new files are covered before review.
- For feature and behavior changes, follow Red-Green-Refactor: write or update unit, integration, and deterministic E2E coverage before production code and capture an expected failing focused test first.
- For all code-changing work, run focused checks first and then `./scripts/verify_all.sh`; the full suite must pass before review or delivery.
- The functional E2E gate is local-first and deterministic in behavior.
- For MCP tool contract changes, add or update unit, integration, and deterministic E2E contract coverage. Also run an import or startup smoke when it can execute without real Notion/Tistory/GitHub credentials or user-data mutation.
- For source sync changes, the functional smoke matrix must cover MCP
  `sync_source`, `list_sources`, and `get_sync_status` with fake/temp
  dependencies whenever possible. Live configured source sync requires both
  explicit user approval and a plan; plan-exempt work must be reclassified
  before the live check or keep it `blocked/gated`.
- For indexing/search/storage changes, verify local-only behavior without
  touching user data when possible. Do not inspect, mutate, delete, or reset
  local Chroma state or SQLite metadata without both explicit user approval and
  a plan.
- For fetcher changes, prefer mocked HTTP/API tests over live credentials. Live
  Notion/Tistory/GitHub checks require both explicit user approval and a plan;
  plan-exempt work must reclassify or keep the check `blocked/gated`. Never
  expose tokens.
- Verification, any matching eval gate required by feature scope (only after
  `./scripts/verify_all.sh`, prefer recording full-suite quality-eval evidence
  when already covered), improvement after/delta when improvement-scoped, and
  functional smoke must happen before the harness review loop; if review
  findings require edits, rerun the affected verification, matching eval gate
  when in scope, refresh improvement after/delta when improvement-scoped using
  temporary Chroma/SQLite paths and mocked connectors (never inspect or mutate
  user data without both explicit user approval and a plan), and affected
  functional smoke entries before starting a fresh three-reviewer pass.

## Security and Configuration

- Do not commit secrets, local database files, Chroma data, cache directories, or `.env` contents.
- External APIs include Notion, Tistory, and GitHub sources. Network-dependent
  live validation remains blocked unless the user explicitly requests the exact
  action and a plan records its source/data/cost/rollback scope.
- Local ChromaDB data and SQLite metadata may contain indexed user content. Do not inspect, delete, or migrate them without explicit user approval, a plan, and user-visible rationale.
