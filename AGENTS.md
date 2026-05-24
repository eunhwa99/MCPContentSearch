# Repository Instructions

## Project Harness

- When the user asks to implement, add, fix, refactor, or test behavior, first read `.agents/docs/harness-engineering.md`, then follow `.agents/skills/harness-engineering/SKILL.md`.
- Harness planning and review must read `.agents/docs/architecture.md` and `.agents/docs/adr/README.md`. Read only accepted ADRs that directly affect the requested change.
- Harness phase skills live under `.agents/skills/`: `harness-plan`, `harness-multitask`, `harness-implement`, `harness-test`, `harness-review`, `harness-refactor`, and `harness-integrate`.
- Branch, commit, push, PR, and PR-watch policy is defined in `.agents/docs/github-workflow.md`.
- File-changing work starts with branch preflight from the latest `main`: if the worktree is clean, switch to `main`, fast-forward it from `origin/main` when network is available, delete only safe local non-`main` work branches using `.agents/docs/github-workflow.md` safeguards, then create a fresh `feature/...` branch before target edits.
- If the starting worktree is dirty, do not switch, pull, or delete branches there. Fetch `origin/main` when network is available, then create an isolated worktree with a fresh `feature/...` branch from `origin/main`; if fetch or isolation is unavailable, ask or report the blocker. Preserve local-only commits and linked-worktree branches unless the user explicitly approves cleanup. Do not edit target files on `main`.
- After branch preflight and before non-plan target edits, create or update a plan document under `docs/plan/`.
- After planning, the main agent should minimize human intervention by acting as the harness orchestrator: define task-specific implementation, testing, documentation, or integration subagent personas; delegate bounded work with clear file ownership and acceptance criteria; collect the results; review and integrate them; and only ask the user when safety, credentials, destructive actions, unavailable delegation/review tools, or genuinely unclear requirements require human judgment.
- Keep `docs/contextwiki-core-understanding.md` updated when changes affect ContextWiki source connectors, source sync, document identity, chunking, tombstones, retrieval, citation metadata, or answer behavior. This note is the maintained human explanation layer and should not drift behind README, architecture docs, ADRs, or implementation.
- After code-changing work, always run the relevant test or verification command before review. Use the repo-local commands in this file and `.agents/docs/harness-engineering.md`, not plugin-default paths such as `docs/superpowers/...`.
- After verification and before PR delivery for any code, configuration, documentation, or skill change, run `$subagent-review-loop`: spawn exactly five fresh reviewer subagents per pass and repeat until all five reviewers in the newest pass report no actionable findings. If subagent review is unavailable, stop and report the blocker instead of silently replacing it with self-review.
- If the main agent's synthesis or `$subagent-review-loop` reports actionable findings, update the plan, assign the fix back to the responsible worker persona or a fresh replacement with the same ownership boundary, rerun affected verification, and repeat the loop before delivery.
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
- `fetching/`: Notion, Tistory, GitHub, website/docs, and unified document fetching/searching.
- `indexing/`: document conversion, chunking, dedup/update detection, and vector indexing.
- `search/`: local Chroma/LlamaIndex search, dynamic local-to-web fallback, ContextWiki retrieval, and citation answer scaffolding.
- `storage/`: SQLite source/job/document/chunk lifecycle metadata and active retrieval checks.
- `docs/contextwiki-core-understanding.md`: maintained learning note for the current ContextWiki data flow, source connector behavior, lifecycle metadata, retrieval gate, and limitations.
- `docs/plan/`: plan documents written before file-changing harness work.
- `.agents/`: local harness docs, phase skills, and ADRs.

## Development Commands

- `python main.py`: start the MCP server in the current environment.
- `python -m compileall api core environments fetching indexing search storage main.py`: syntax-check project modules without contacting external services.
- `uv run python -m compileall api core environments fetching indexing search storage main.py`: same check through uv when the uv environment is healthy.
- `uv run pytest`: preferred test command once tests exist.

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
- For MCP tool contract changes, add or update tests when feasible and run an import or startup smoke that does not require real Notion/Tistory/GitHub/Web credentials.
- For indexing/search/storage changes, verify local-only behavior without touching user data when possible. Avoid deleting or resetting local Chroma state or SQLite metadata without explicit user approval.
- For fetcher changes, prefer mocked HTTP/API tests over live credentials. Live Notion/Tistory/GitHub/Web checks require user approval and should not expose tokens.
- Verification must happen before `$subagent-review-loop`; if review findings require edits, rerun the affected verification before starting a fresh five-reviewer pass.

## Security and Configuration

- Do not commit secrets, local database files, Chroma data, cache directories, or `.env` contents.
- External APIs include Notion, Tistory, GitHub, and configured website/docs sources. Network-dependent validation is optional unless the user explicitly requests it.
- Local ChromaDB data and SQLite metadata may contain indexed user content. Do not inspect, delete, or migrate them without explicit user approval, a plan, and user-visible rationale.
