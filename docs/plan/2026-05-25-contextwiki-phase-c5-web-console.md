# ContextWiki Phase C.5 Local Web Test Console

## User Request

Add a Phase C.5 local web test console after Phase C and before Phase D. The
console is not a final product UI; it is a local browser surface for manually
exercising ContextWiki E2E behavior through a thin HTTP wrapper over existing
services.

Requested capabilities:

- Ask a question and inspect answer, citations, backlinks, and used chunks.
- Generate a wiki page by calling `generate_wiki_page(topic)`.
- Download generated Markdown and JSON result files.
- Select source filters such as GitHub, Notion, PDF, and docs/source ids.
- Run smoke checks such as fake smoke and optional GitHub live smoke.

## Branch Preflight Result

- Primary worktree: `/Users/eunhwa/IdeaProjects/MCPContentSearch`.
- Primary branch: `feature/contextwiki-phase-b-connectors`.
- Primary worktree state: dirty with existing user/agent changes, so no branch
  switching, pulling, or cleanup was performed there.
- Freshness check: `git fetch origin main` succeeded and updated `origin/main`
  to `8a1bc41`.
- Isolated worktree: `/private/tmp/MCPContentSearch-phase-c5`.
- Task branch: `feature/contextwiki-phase-c5-web-console`, created from
  `origin/main`.

## Scope and Non-Goals

Scope:

- Add a local-only FastAPI development server that composes existing
  ContextWiki services and exposes HTTP endpoints for the console.
- Add a minimal browser UI under `web/` that calls the HTTP endpoints and shows
  answer/wiki payloads, citations, backlinks, and used chunks.
- Add Markdown and JSON download support in the browser without server-side
  persistence.
- Add a deterministic fake smoke endpoint/button and an optional GitHub live
  smoke endpoint/button that delegates to the existing smoke script behavior.
- Add focused tests for the HTTP wrapper contracts without live credentials or
  user Chroma/SQLite data.
- Document how to run the local console.

Non-goals:

- No product authentication, deployment, multi-user behavior, or hosted UI.
- No Phase D metrics dashboard yet; latency/cost/citation correctness can be
  surfaced later when Phase D exists.
- No MCP tool contract change.
- No ChromaDB or SQLite reset, migration, deletion, or inspection.
- No live external validation as a required test gate.
- No persistent wiki page store.

## Acceptance Criteria

- `GET /api/health` returns a local-console health payload.
- `POST /api/answer` calls the existing citation answer service and returns the
  stable answer payload.
- `POST /api/wiki/generate` calls the existing wiki service and returns the
  stable wiki payload.
- `GET /api/sources` returns registered sources when metadata is configured and
  an empty list when not configured.
- `POST /api/smoke/fake` runs the deterministic fake wiki smoke path and returns
  a structured result without live credentials.
- `POST /api/smoke/github` skips gracefully without configured source/network
  and does not print secrets.
- Browser UI allows question/wiki input, source type and source id filters,
  payload inspection, and Markdown/JSON download.
- Tests cover endpoint behavior with fake services and do not touch persistent
  user Chroma/SQLite paths.
- README documents the local-only nature, run command, endpoint map, and skipped
  live smoke behavior.

## Step Breakdown

| Step | Label | Work | Acceptance |
| --- | --- | --- | --- |
| 1 | `http-contract` | Add failing tests for local console endpoint contracts using fake services. | Tests fail because the wrapper does not exist yet. |
| 2 | `http-wrapper` | Add `web_console/` FastAPI app factory and route handlers over answer/wiki/source/smoke services. | Focused tests pass without live services. |
| 3 | `browser-console` | Add minimal local browser UI under `web/` and serve it from the app. | UI can call answer/wiki/smoke endpoints and download Markdown/JSON. |
| 4 | `docs` | Document commands and local-only boundaries. | README reflects the Phase C.5 console without implying production UI. |
| 5 | `verification-review` | Run focused tests, compile/full non-live verification, then `$subagent-review-loop`. | Verification and review results are recorded before PR delivery. |

## Worker Ownership

| Worker | Role | Owned Files | Non-Goals | Acceptance and Verification |
| --- | --- | --- | --- | --- |
| Main agent | CEO/orchestrator plus critical-path HTTP/test integration. | `web_console/`, `tests/web_console/`, `pyproject.toml`, `uv.lock`, `scripts/verify_all.sh`, `README.md`, plan doc. | Do not edit worker-owned static UI except integration fixes after worker completion; do not commit/push before clean review. | TDD endpoint regressions, focused tests, full non-live verification, browser DOM smoke, review routing. |
| Peirce | Static UI worker. | `web/index.html`, `web/styles.css`, `web/app.js`. | No Python, dependency, docs, plan, or test edits. | `node --check web/app.js`, trailing-whitespace check, UI diff reviewed by main agent. |
| Bohr | Read-only instruction explorer. | No file edits. | No implementation or repo changes. | Proposed AGENTS/harness wording for mandatory worker orchestration; final answer should relay it. |
| Reviewers | Fresh five-reviewer `$subagent-review-loop` passes. | No file edits. | Do not replace verification or PR delivery. | Findings-first review; all actionable findings fixed before next fresh pass. |

## Files Likely To Change

- `pyproject.toml`
- `uv.lock`
- `web_console/__init__.py`
- `web_console/app.py`
- `web/index.html`
- `web/styles.css`
- `web/app.js`
- `tests/web_console/test_app.py`
- `README.md`
- `docs/plan/2026-05-25-contextwiki-phase-c5-web-console.md`

## Test and Verification Plan

Red/focused test:

```bash
PYTHONPATH=. uv run pytest tests/web_console/test_app.py
```

Syntax/import check:

```bash
python -m compileall api core environments fetching indexing search storage wiki web_console main.py
```

Full non-live verification:

```bash
./scripts/verify_all.sh
```

Static diff checks:

```bash
git diff --check
git diff --cached --check
```

If `uv run ...` cannot resolve new FastAPI dependencies in the local workspace,
record the dependency blocker and run the closest dependency-free checks that
remain meaningful.

## Architecture/ADR Constraints

- ADR 0001 keeps composition separate from business behavior. The HTTP wrapper
  should be thin and local-only, delegating to existing services instead of
  duplicating ContextWiki logic.
- ADR 0002 keeps SQLite metadata authoritative for citation-ready chunks. The
  console must use existing search/answer/wiki services and must not inspect raw
  Chroma data.
- ADR 0005 keeps LLM wiki synthesis opt-in. The console must not enable or
  invoke external LLM behavior unless existing runtime configuration explicitly
  does so.
- No new ADR is required because this adds a local developer/test surface and
  does not change persistence, MCP contracts, connector contracts, or LLM
  policy.

## Risks and Rollback Notes

- Risk: The UI may look like a production dashboard. Mitigation: document and
  label it as a local test console and avoid auth/deployment work.
- Risk: Smoke endpoints could leak logs or secrets. Mitigation: return
  structured status only and delegate to existing smoke script safeguards.
- Risk: Adding FastAPI could broaden dependency surface. Mitigation: use it only
  for local dev wrapper and keep endpoint tests small.
- Rollback: remove `web_console/`, `web/`, tests/docs, and the FastAPI
  dependency entries.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created isolated C.5 worktree from latest `origin/main` because the primary checkout was dirty. | `git fetch origin main`; `git worktree add -b feature/contextwiki-phase-c5-web-console /private/tmp/MCPContentSearch-phase-c5 origin/main` |
| Plan document | completed | Added Phase C.5 local console plan. | This file |
| Planning | completed | Read architecture, ADR 0001/0002/0005, Phase C plan, and current answer/wiki service contracts. | `.agents/docs/architecture.md`; ADR 0001, 0002, 0005; `wiki/service.py`; `search/answer_service.py` |
| Worker orchestration | completed | Spawned a UI worker for `web/` and a read-only explorer for mandatory worker-orchestration instruction wording after the user noted the CEO/subagent expectation. | `multi_agent_v1.spawn_agent` -> Peirce (UI worker), Bohr (instruction explorer) |
| Implementation | completed | Added failing HTTP console tests, confirmed RED, implemented `web_console` FastAPI wrapper, integrated worker-built static UI, documented run path, and fixed smoke count rendering after browser verification. | RED `ModuleNotFoundError: web_console`; `web_console/app.py`; `web/index.html`; `web/app.js`; `web/styles.css`; `README.md` |
| Focused verification | completed | Endpoint/MCP/wiki focused tests, JS syntax, Python compile, and diff whitespace checks pass. | `PYTHONPATH=. uv run pytest tests/web_console/test_app.py tests/api/test_tools_contract.py tests/wiki/test_wiki_service.py` -> 35 passed; `node --check web/app.js`; `python -m compileall api core environments fetching indexing search storage wiki web_console main.py`; `git diff --check` |
| Browser verification | completed | Opened fake local console server at `127.0.0.1:8765`; verified health/source render, answer result, wiki Markdown, and fake smoke summary. Screenshot capture timed out in Browser CDP, so DOM verification was used. | Browser DOM snapshots; answer/wiki/fake smoke completed |
| Integration verification | completed | Full non-live verification passed after UI fix; uv health probe fell back to `python -m pytest`. | `./scripts/verify_all.sh` -> fallback `python -m pytest -m "not live"` -> 628 passed |
| Review pass 1 | completed | Fresh five-reviewer pass found actionable issues: loopback enforcement, smoke output persistence/doc mismatch, direct uvicorn dependency, source-type filter broadening, smoke failure shape, not-configured tests, JS verification, top_k/UI filter alignment, tab a11y, stale plan rows, worker detail, and LLM/live-call wording. | Reviewers: Ohm, Hume, Kuhn, Poincare, Rawls |
| Review pass 1 remediation | completed | Added regressions and fixes for loopback enforcement, source-type normalization, unmatched filters, structured smoke failures, unconfigured branches, direct `uvicorn`, JS verification, top_k/UI source labels, tab ARIA, temporary smoke output cleanup, README local/external wording, and worker ownership detail. | `PYTHONPATH=. uv run pytest tests/web_console/test_app.py` -> 12 passed; focused suite -> 42 passed |
| Integration verification after remediation | completed | Full non-live verification passed after review fixes; `verify_all.sh` now includes `node --check web/app.js`; uv health probe still fell back to `python -m pytest`. | `./scripts/verify_all.sh` -> 635 passed |
| Browser verification after remediation | completed | Reopened fake local console server, verified render shows Web/docs filter and `top_k=8`, and clicked fake smoke successfully. Answer/wiki input fill was blocked by Browser virtual clipboard after reload, but the same paths are covered by focused tests and earlier browser verification. | Browser DOM snapshot; fake smoke completed |
| Review pass 2 | completed | Fresh five-reviewer pass found actionable issues: Host/Origin hardening, smoke failure log redaction, required Node check, failed-smoke visible summary, ARIA tab keyboard behavior, and delayed object URL revocation. | Reviewers: Galileo, Plato, Hilbert, Ramanujan, Turing |
| Review pass 2 remediation | completed | Added Host/Origin regression tests, smoke log redaction assertion, Host/Origin guard, generic smoke error logging, failed-smoke summary display, ARIA tab keyboard behavior, delayed URL revocation, and hard `node --check` requirement. | `PYTHONPATH=. uv run pytest tests/web_console/test_app.py` -> 14 passed; focused suite -> 44 passed; `./scripts/verify_all.sh` -> 637 passed |
| Review pass 3 | completed | Fresh five-reviewer pass found actionable issues: staged diff gate, broader source/answer/wiki exception redaction, remote override guard behavior, failed smoke status text, and answer default `top_k` drift. | Reviewers: Confucius, Archimedes, Faraday, Hypatia, McClintock |
| Review pass 3 remediation | completed | Added regressions and fixes for remote override Origin enforcement, structured source/answer/wiki failures with secret-suppressed logs, failed-action status text, blank UI `top_k` default, staged all intended files, and passed staged whitespace check. | `PYTHONPATH=. uv run pytest tests/web_console/test_app.py` -> 18 passed; focused suite -> 48 passed; `./scripts/verify_all.sh` -> 641 passed; `git diff --cached --check` |
| Review pass 4 | completed | Fresh five-reviewer pass found actionable issues: wiki default `top_k` drift for direct API calls, filter-building failures outside structured redaction, Host/Origin authority parsing gaps, remote override wording, and undocumented Node.js verification prerequisite. | Reviewers: Leibniz, Huygens, Kepler, Pauli, Boole |
| Review pass 4 remediation | completed | Added regressions and fixes for optional route-level `top_k` defaults, filter metadata failure redaction, strict Host/Origin authority parsing including bracketed IPv6 loopback, precise remote override docs, and Node.js verification docs. | `PYTHONPATH=. uv run pytest tests/web_console/test_app.py` -> 26 passed; focused suite -> 56 passed; `./scripts/verify_all.sh` -> 649 passed |
| Review pass 5 | completed | Fresh five-reviewer pass reported no actionable findings. | Reviewers: Cicero, Raman, Bernoulli, Dirac, Helmholtz |
| PR delivery | completed | Committed, pushed, and opened a main-base PR after clean final review. | PR #9 |
