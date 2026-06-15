# Repository Instructions

## Project Harness

- When the user asks to implement, add, fix, refactor, or test behavior, first read `.agents/docs/harness-engineering.md`, then follow `.agents/skills/harness-engineering/SKILL.md`.
- Harness planning and review must read `.agents/docs/architecture.md`.
- Harness phase skills live under `.agents/skills/`: `harness-plan`, `harness-multitask`, `harness-implement`, `harness-test`, `harness-functional-smoke`, `harness-review`, `harness-refactor`, and `harness-integrate`.
- Branch, commit, push, PR, and PR-watch policy is defined in `.agents/docs/github-workflow.md`.
- File-changing work starts with branch preflight from the latest `main`: if the worktree is clean, switch to `main`, fast-forward it from `origin/main` when network is available, delete only safe local non-`main` work branches using `.agents/docs/github-workflow.md` safeguards, then create a fresh `feature/...` branch before target edits.
- If the starting worktree is dirty, do not switch, pull, or delete branches there. Fetch `origin/main` when network is available, then create an isolated worktree with a fresh `feature/...` branch from `origin/main`; if fetch or isolation is unavailable, ask or report the blocker. Preserve local-only commits and linked-worktree branches unless the user explicitly approves cleanup. Do not edit target files on `main`.
- After branch preflight and before non-plan target edits, create or update a plan document under `docs/plan/`.
- After planning, the main agent is the CEO/orchestrator for file-changing harness work, not the default implementer. Before non-plan target edits, discover available subagent/delegation tools unless an equivalent callable subagent tool is already available in the active tool list.
- For any work that is not truly atomic, spawn role-specific implementation, testing, documentation, or integration workers before implementation begins. Assign each worker a bounded ownership area, expected files or modules, acceptance criteria, verification expectations, and an instruction to preserve other user/agent changes instead of reverting them.
- The main agent may implement directly only when the change is truly atomic. Record the reason in the plan progress log before editing target files. Shared-file overlap is not a reason to bypass workers: for non-atomic work, use a single-owner worker or sequential worker handoff instead of parallel edits. If subagent tools are unavailable for non-atomic work, or no safe worker boundary can be created, stop before target edits and ask the user for explicit approval before bypassing worker orchestration. Do not silently collapse worker orchestration into self-implementation.
- Worker subagents and reviewer subagents are different roles. Workers may edit only inside their assigned boundary and must not commit, push, open PRs, inspect secrets, print secret values, inspect local Chroma/SQLite data, or mutate user data. Local Chroma/SQLite inspection or user-data mutation requires explicit user approval plus plan rationale, bounded instructions, and rollback/safety notes; secret values are not delegable. `$subagent-review-loop` reviewers are read-only and run only after verification and functional smoke.
- The main agent owns integration: collect worker outputs, inspect diffs, resolve conflicts, update the plan, run verification, route actionable findings back to the responsible worker persona or a fresh replacement with the same ownership boundary, and minimize human intervention. Ask the user only when safety, credentials, destructive actions, unavailable delegation/review tools, or genuinely unclear requirements require human judgment.
- Keep `.agents/docs/architecture.md` updated when changes affect ContextWiki source connectors, source sync, document identity, chunking, tombstones, retrieval, citation metadata, answer behavior, or other maintained design assumptions. This document is the maintained human explanation layer and should not drift behind README or implementation.
- After code-changing work, always run the relevant test or verification command before review. Use the repo-local commands in this file and `.agents/docs/harness-engineering.md`, not plugin-default paths such as `docs/superpowers/...`.
- After implementation and focused tests, run the functional smoke gate before `$subagent-review-loop`: use `.agents/skills/harness-functional-smoke/SKILL.md` to exercise the task-relevant MCP/source-sync/user-visible feature inventory once through the safest real caller surfaces, not only unit-test the changed files. Record explicit safety blockers, approval needed, and nearest fake/temp substitutes in the plan.
- After verification and before PR delivery for any code, configuration, documentation, or skill change, run `$subagent-review-loop`: spawn exactly five fresh reviewer subagents per pass and repeat until all five reviewers in the newest pass report no actionable findings. If subagent review is unavailable, stop and report the blocker instead of silently replacing it with self-review.
- If the main agent's synthesis or `$subagent-review-loop` reports actionable findings, update the plan, assign the fix back to the responsible worker persona or a fresh replacement with the same ownership boundary, rerun affected verification and affected functional smoke entries, and repeat the loop before delivery.
- After the final clean `$subagent-review-loop` pass, proceed to commit, push, and create a `main`-base PR by default. This is the standing repository workflow unless the user explicitly asks for local-only work or a safety blocker prevents PR delivery.
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
- `search/`: ContextWiki retrieval, ranking, SQLite-backed active gates, query rewrite, and citation answer scaffolding.
- `storage/`: SQLite source/job/document/chunk lifecycle metadata and active retrieval checks.
- `docs/plan/`: plan documents written before file-changing harness work.
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
- For Python code changes, run the smallest relevant test or import/syntax check first, then broaden to `uv run pytest` when tests exist.
- For code-changing work, before review run `./scripts/verify_functional_e2e.sh` (or `./scripts/verify_all.sh`, which includes it) so end-to-end feature workflows are exercised at least once.
- The functional E2E gate is local-first and deterministic in behavior.
- For MCP tool contract changes, add or update tests when feasible and run an import or startup smoke that does not require real Notion/Tistory/GitHub credentials.
- For source sync changes, the functional smoke matrix must cover MCP `sync_source`, `list_sources`, and `get_sync_status` with fake/temp dependencies whenever possible; live configured source sync needs explicit user approval and must not mutate user Chroma/SQLite without a plan.
- For indexing/search/storage changes, verify local-only behavior without touching user data when possible. Avoid deleting or resetting local Chroma state or SQLite metadata without explicit user approval.
- For fetcher changes, prefer mocked HTTP/API tests over live credentials. Live Notion/Tistory/GitHub checks require user approval and should not expose tokens.
- Verification and functional smoke must happen before `$subagent-review-loop`; if review findings require edits, rerun the affected verification and affected functional smoke entries before starting a fresh five-reviewer pass.

## Security and Configuration

- Do not commit secrets, local database files, Chroma data, cache directories, or `.env` contents.
- External APIs include Notion, Tistory, and GitHub sources. Network-dependent validation is optional unless the user explicitly requests it.
- Local ChromaDB data and SQLite metadata may contain indexed user content. Do not inspect, delete, or migrate them without explicit user approval, a plan, and user-visible rationale.
