# Repository Instructions

## Project Harness

- When the user asks to implement, add, fix, refactor, or test behavior, first read `.agents/docs/harness-engineering.md`, then follow `.agents/skills/harness-engineering/SKILL.md`.
- Harness planning and review must read `.agents/docs/architecture.md` and `.agents/docs/adr/README.md`. Read only accepted ADRs that directly affect the requested change.
- Harness phase skills live under `.agents/skills/`: `harness-plan`, `harness-multitask`, `harness-implement`, `harness-test`, `harness-review`, `harness-refactor`, and `harness-integrate`.
- Branch, commit, push, PR, and PR-watch policy is defined in `.agents/docs/github-workflow.md`.
- File-changing work starts with branch preflight. Do not edit target files on `main`; create or use a `feature/...` branch or isolated worktree first.
- After branch preflight and before non-plan target edits, create or update a plan document under `docs/plan/`.
- After code-changing work, always run the relevant test or verification command before review. Use the repo-local commands in this file and `.agents/docs/harness-engineering.md`, not plugin-default paths such as `docs/superpowers/...`.
- After verification and before PR delivery for any code, configuration, documentation, or skill change, run `$subagent-review-loop`: spawn exactly five fresh reviewer subagents per pass and repeat until all five reviewers in the newest pass report no actionable findings. If subagent review is unavailable, stop and report the blocker instead of silently replacing it with self-review.
- After the final clean `$subagent-review-loop` pass, proceed to commit, push, and create a `main`-base PR by default. This is the standing repository workflow unless the user explicitly asks for local-only work or a safety blocker prevents PR delivery.
- If the user gives multiple independent tasks, split them during planning. Use separate worker ownership and branch/worktree boundaries when parallel work is allowed.
- Do not reply on GitHub, watch PRs, or push follow-up PR changes unless the user explicitly delegates that work.

## Project Structure

This repository is a Python MCP content search server built around FastMCP, LlamaIndex, and ChromaDB.

- `main.py`: application composition and FastMCP server startup.
- `api/`: MCP tool registration and tool handlers.
- `core/`: shared models, exceptions, and utility code.
- `environments/`: runtime configuration and secret/environment loading.
- `fetching/`: Notion, Tistory, and unified document fetching/searching.
- `indexing/`: document conversion, dedup/update detection, and vector indexing.
- `search/`: local Chroma/LlamaIndex search and dynamic local-to-web fallback.
- `docs/plan/`: plan documents written before file-changing harness work.
- `.agents/`: local harness docs, phase skills, and ADRs.

## Development Commands

- `python main.py`: start the MCP server in the current environment.
- `python -m compileall api core environments fetching indexing search main.py`: syntax-check project modules without contacting external services.
- `uv run python -m compileall api core environments fetching indexing search main.py`: same check through uv when the uv environment is healthy.
- `uv run pytest`: preferred test command once tests exist.

If `uv run ...` fails because the local environment or workspace metadata is not ready, report the failure and run the closest dependency-free check, such as `python -m compileall ...`.

## Coding Style

- Prefer small, focused modules that preserve the current boundaries: API tools, search, fetching, indexing, configuration, and core models.
- Do not move secrets into logs, docs, tests, or plan files. Treat `environments/token.py`, environment variables, API keys, and local Chroma contents as sensitive.
- Keep MCP tool response shapes stable unless the user requested a contract change.
- Use async boundaries deliberately. Do not create background tasks that hide critical failures unless the caller contract explicitly treats the work as background work.
- Add comments only where they explain non-obvious async, indexing, vector-store, or external API behavior.

## Testing and Verification

- For docs/instruction-only changes limited to `AGENTS.md`, `.agents/`, and `docs/plan/`, use lightweight verification: path listing, `git status --short`, and `git diff --check`.
- For Python code changes, run the smallest relevant test or import/syntax check first, then broaden to `uv run pytest` when tests exist.
- For MCP tool contract changes, add or update tests when feasible and run an import or startup smoke that does not require real Notion/Tistory credentials.
- For indexing/search changes, verify local-only behavior without touching user data when possible. Avoid deleting or resetting local Chroma state without explicit user approval.
- For fetcher changes, prefer mocked HTTP/API tests over live credentials. Live Notion/Tistory checks require user approval and should not expose tokens.
- Verification must happen before `$subagent-review-loop`; if review findings require edits, rerun the affected verification before starting a fresh five-reviewer pass.

## Security and Configuration

- Do not commit secrets, local database files, Chroma data, cache directories, or `.env` contents.
- External APIs include Notion and Tistory. Network-dependent validation is optional unless the user explicitly requests it.
- Local ChromaDB data may contain indexed user content. Do not inspect, delete, or migrate it without a plan and user-visible rationale.
