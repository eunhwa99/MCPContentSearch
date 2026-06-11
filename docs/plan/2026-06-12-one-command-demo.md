## User request

- Start issue `#28`: add a one-command public demo path for the slim ContextWiki core.
- Set the local OpenAI environment variable to the provided key for later LLM verification.

## Branch preflight result

- Started from dirty worktree `/Users/eunhwa/.codex/worktrees/c298/MCPContentSearch` on `feature/slim-mcp-core`; did not switch or pull there.
- Fetched `origin/main` and created isolated worktree `/Users/eunhwa/.codex/worktrees/one-command-demo/MCPContentSearch`.
- Created fresh branch `feature/one-command-demo` from `origin/main` at `9dbd6b0`.

## Scope

- Add a deterministic, public, one-command demo path for the retained slim MCP retrieval flow.
- Provide safe sample data and a short documented reviewer workflow for sync -> ask -> citation.
- Add local-only environment setup needed to verify optional OpenAI-backed behavior without committing secrets.

## Non-goals

- Reintroducing the removed web console or any browser-only UI
- Live external source sync
- Broad reproducibility work such as Docker or `.env.example`
- `sync_all`, explainability, or eval expansion beyond what is strictly needed for the demo

## Acceptance criteria

- A reviewer can run one documented command and see the slim retained demo flow end to end.
- The demo path does not require real Notion/Tistory/GitHub/Obsidian credentials.
- Demo output is deterministic and secret-safe.
- README includes a concise walkthrough and expected behavior.
- The demo path has focused automated verification.

## Step breakdown

1. Inspect the current repo for the best existing deterministic search/answer fixture path to reuse.
2. Design the one-command demo around retained MCP flows and temporary storage.
3. Implement demo fixture assets and the runner script.
4. Document the reviewer workflow in README.
5. Add focused verification for the demo path.

## Files likely to change

- `README.md`
- `scripts/demo.sh` or similar
- `sample_sources/` or `sample_vault/`
- focused tests or smoke scripts
- local ignored `.env` for `OPENAI_API_KEY`

## Test and verification plan

- Focused local demo runner execution
- `python -m compileall api core environments fetching indexing search storage main.py`
- Focused pytest for any added demo script/tests
- `./scripts/verify_functional_e2e.sh` if the implementation touches retained end-to-end behavior broadly enough

## Functional smoke matrix

| Surface | Status | Notes |
| --- | --- | --- |
| Demo fixture setup | pending | Must use public sample data only. |
| Demo sync flow | passed | Demo uses retained Obsidian source sync with temporary SQLite and Chroma storage. |
| Demo ask -> citation flow | passed | Demo shows grounded answer plus citations from bundled sample vault. |
| Optional OpenAI verification | blocked/gated | `.env` exists locally for later LLM work, but issue `#28` demo intentionally hard-disables query rewrite and does not call OpenAI. |

## Architecture/ADR constraints

- Stay within slim MCP core scope from ADR 0006.
- Do not reintroduce removed website/docs crawler, web console, or legacy tools.
- Do not inspect or mutate user-local Chroma/SQLite data; use temporary demo storage.
- Keep MCP contracts stable unless a demo-specific surface clearly belongs outside MCP.
- Do not commit `.env` or secret values.

## Risks and rollback notes

- Risk: demo flow accidentally depends on live credentials or local user data.
  - Mitigation: use deterministic public fixtures and temporary storage paths.
- Risk: demo docs drift from actual runnable commands.
  - Mitigation: verify the exact documented command before handoff.
- Risk: repo harness expects worker orchestration for non-atomic work.
  - Mitigation: record the blocker and request explicit user approval to proceed without worker delegation if policy prevents subagent use.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created isolated worktree and branch from `origin/main`. | `git fetch origin main`; `git worktree add -b feature/one-command-demo ... origin/main` |
| Architecture/ADR read | completed | Read architecture, ADR index, and ADR 0006 for slim-scope constraints. | `.agents/docs/architecture.md`; `.agents/docs/adr/README.md`; `.agents/docs/adr/0006-slim-mcp-core-scope.md` |
| Plan document | completed | Wrote initial execution plan for issue `#28`. | `docs/plan/2026-06-12-one-command-demo.md` |
| Worker orchestration decision | completed | User explicitly approved single-agent execution for this work item. | User reply: `1번` |
| Local secret setup | completed | Wrote ignored local `.env` with `OPENAI_API_KEY` for later verification only. | `.env` written in isolated worktree and ignored by Git |
| Implementation | completed | Added bundled sample vault, one-command demo runner, shell entrypoint, focused tests, and README demo docs. | `sample_vault/`; `scripts/demo_public_flow.py`; `scripts/demo.sh`; `tests/scripts/test_demo_public_flow.py`; `README.md` |
| Focused verification | completed | Verified compile safety, focused demo tests, and direct demo script execution. | `python -m compileall ... scripts/demo_public_flow.py`; `uv run --locked pytest -q tests/scripts/test_demo_public_flow.py`; `./scripts/demo.sh`; `./scripts/demo.sh --json` |
| Review pass 1 remediation | completed | Tightened demo determinism, hardened shell fallback messaging, forced rewrite-off config, and moved focused wrapper coverage onto `./scripts/demo.sh`. | `uv run --locked pytest -q tests/scripts/test_demo_public_flow.py`; `./scripts/demo.sh --json`; `./scripts/verify_functional_e2e.sh` |
| Review pass 2 remediation | completed | Normalized search-result timestamps and narrowed README/demo wording from MCP caller flow to retained tool-handler/service flow. | `uv run --locked pytest -q tests/scripts/test_demo_public_flow.py`; `./scripts/demo.sh --json`; `./scripts/verify_functional_e2e.sh` |
| Review pass 3 remediation | completed | Aligned sample vault wording with the documented tool-handler/service boundary. | `uv run --locked pytest -q tests/scripts/test_demo_public_flow.py`; `./scripts/demo.sh --json` |
| Final review pass | completed | Fresh five-reviewer pass reported no actionable findings. | Pass 4 reviewers: Bernoulli, Faraday, Confucius, Kant, Jason all `no actionable findings` |
