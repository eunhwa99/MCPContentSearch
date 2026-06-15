## User request

- Verify each MCP tool's documented/commented description against code and fix the mismatches.
- Current scoped action: narrow docs/comment alignment for confirmed mismatches only, including one Python docstring update in `api/tools.py`.
- Follow-up after refreshing to the latest `origin/main`: preserve the latest mainline doc structure, resolve conflicts conservatively, and keep wording fixes without resurrecting files intentionally removed on `main`.

## Branch preflight result

- Starting worktree: repository root checkout
- Starting branch: `main`
- Starting worktree state: dirty (`.env.example` modified), so no branch switching or edits there.
- Freshness check: ran `git fetch origin main`.
- Isolated worktree: `.worktrees/search-tool-doc-alignment`
- Task branch: `feature/search-tool-doc-alignment` from `origin/main`

## Scope and non-goals

### Scope

- Align MCP tool behavior wording with current implementation for confirmed mismatches.
- Keep the change non-behavioral and minimal.

### Non-goals

- No MCP contract changes.
- No runtime behavior changes.
- No search/indexing/storage behavior changes.
- No edits unrelated to the confirmed wording mismatches.

## Acceptance criteria

- The retained MCP tool surface audit is recorded end-to-end: `list_sources`, `sync_source`, `sync_all`, `get_sync_status`, `search_context`, `search_documents`, `fetch_context`, and `answer_with_citations` were checked against current code.
- `search_context` docs no longer claim an unconditional `debug` key when the service is unconfigured.
- `answer_with_citations` wording makes clear that debug data is opt-in rather than always present.
- Changed docs remain consistent with current `api/tools.py` behavior.

## Retained MCP tool audit

| Tool | Inspected doc/comment surface | Verified code path | Audit result |
| --- | --- | --- | --- |
| `list_sources` | `README.md` retained tools table; `api/tools.py` wrapper docstring | `api/tools.py` `list_sources` wrapper | Already aligned; no wording change needed |
| `sync_source` | `README.md` retained tools table; `api/tools.py` wrapper docstring | `api/tools.py` `sync_source` wrapper | Already aligned; no wording change needed |
| `sync_all` | `.agents/docs/architecture.md` contract intent; `api/tools.py` wrapper docstring | `api/tools.py` `sync_all` wrapper; `indexing/ingestion_service.py` bulk outcome/status helpers | Required wording narrowing: `skipped` is scoped to disabled-source failures, not generic unconfigured sources |
| `get_sync_status` | `README.md` retained tools table; `api/tools.py` wrapper docstring | `api/tools.py` `get_sync_status` wrapper | Already aligned; no wording change needed |
| `search_context` | `.agents/docs/architecture.md` contract intent; `api/tools.py` wrapper docstring | `api/tools.py` `search_context`; `search/context_service.py` fallback paths | Required wording narrowing: no unconditional `debug` key on service-unconfigured fallback |
| `search_documents` | `README.md` production guidance; `api/tools.py` wrapper docstring | `api/tools.py` `search_documents`; `search/context_service.py` document grouping | Required wording narrowing: keep it positioned as document discovery rather than chunk-level evidence gathering |
| `fetch_context` | `README.md` retained tools table; `api/tools.py` wrapper docstring | `api/tools.py` `fetch_context` wrapper | Required wording narrowing: the public table should reflect that `document_id` and `chunk_id` are optional inputs and at least one must be provided |
| `answer_with_citations` | `README.md` guidance; `.agents/docs/architecture.md` contract intent; `api/tools.py` wrapper docstring | `api/tools.py` `answer_with_citations`; `search/answer_service.py` | Required wording narrowing: helper payload can be grounded or insufficient, and debug is only forwarded on configured answer-service paths |

## Step breakdown

1. Confirm the exact mismatches between tool docs/comments and `api/tools.py`.
2. Update only the minimal docs/comments that overstate current behavior.
3. Run focused verification plus the required lightweight MCP contract and functional smoke checks for the Python docstring touch.
4. Attempt required review-gate handling or record the blocker transparently if delegation is not authorized.

## Files likely to change

- `README.md`
- `.agents/docs/architecture.md`
- `api/tools.py`
- `docs/plan/2026-06-15-search-tool-doc-alignment.md`

## Test and verification plan

- `python -m compileall api core environments fetching indexing search storage main.py`
- `python -m pytest -q tests/contracts/test_public_mcp_contracts.py`
- `./scripts/verify_functional_e2e.sh`
- `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`
- `git status --short --branch`
- `git diff --check`
- Stage relevant files, then run `git diff --cached --check`

## Functional smoke matrix

| Feature | Caller Surface | Data Mode | Expected Result | Action/Command | Result | Evidence | Skip Reason / Substitute |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MCP tool docs alignment | Real FastMCP `call_tool` contract test plus static diff review | Fake/temp contract harness | Public wording matches current tool behavior and no contract wording overstates outputs | `python -m pytest -q tests/contracts/test_public_mcp_contracts.py` | passed | Contract smoke passed; targeted code/doc comparison completed | n/a |
| Retained MCP workflow smoke | Functional E2E script | Temp/local deterministic harness | Retained MCP sync/search/fetch/answer flows still pass after the doc/comment alignment change | `./scripts/verify_functional_e2e.sh` | passed | Functional E2E smoke passed | n/a |

## Architecture/ADR constraints

- `.agents/docs/architecture.md`: retained MCP tools and current public contract intent must stay stable.
- `.agents/docs/adr/0006-slim-mcp-core-scope.md`: no reintroduction of removed web fallback or non-retained surfaces.
- Keep changes at the docs/comment layer only, and respect latest-`main` file removals unless restoring the file is clearly required.

## Risks and rollback notes

- Risk: narrowing wording too much and dropping useful behavior context.
- Mitigation: adjust only the statements proven false by current code.
- Risk: reviving a file intentionally removed on latest `main`.
- Mitigation: preserve the removal and migrate any still-needed wording into surviving docs instead of silently restoring the deleted file.
- Rollback: revert the docs/comment wording changes in this branch only.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Audit baseline | completed | Checked the retained MCP tool descriptions in `README.md`, architecture docs, and tool docstrings against current code, then recorded the per-tool result table in this plan. | `api/tools.py`; `README.md`; `.agents/docs/architecture.md`; `docs/plan/2026-06-15-search-tool-doc-alignment.md` |
| Branch preflight | completed | Dirty start preserved; fetched `origin/main`; created isolated worktree and fresh feature branch. | `git status --short --branch`; `git fetch origin main`; `git worktree add -b feature/search-tool-doc-alignment .worktrees/search-tool-doc-alignment origin/main` |
| Planning | completed | Scoped a non-behavioral docs-plus-docstring alignment change with no contract edits. | `docs/plan/2026-06-15-search-tool-doc-alignment.md` |
| Implementation | completed | Narrowed wording in README, architecture, and the `answer_with_citations` tool docstring to match current behavior. | `README.md`; `.agents/docs/architecture.md`; `api/tools.py` |
| Verification pass 1 | completed | Ran lightweight checks before review and staged the current change set. | `rg --files AGENTS.md README.md docs .agents/docs .agents/skills`; `git status --short --branch`; `git diff --check`; `git diff --cached --check` |
| Review pass 1 | completed/actionable | Five fresh reviewers reported plan accuracy, staging drift, path-leak, and docstring precision issues. | Reviewer agents `Laplace`, `Aristotle`, `Popper`, `Kuhn`, `Beauvoir` |
| Review pass 1 remediation | completed | Fixed plan accuracy, removed local path leakage, tightened the MCP docstring to mention `include_debug=True`, and refreshed the staged snapshot. | `docs/plan/2026-06-15-search-tool-doc-alignment.md`; `api/tools.py` |
| Verification pass 2 | completed | Re-ran Python syntax safety plus diff checks after review-pass-1 fixes. | `python -m compileall api core environments fetching indexing search storage main.py`; `git diff --check`; `git diff --cached --check` |
| Review pass 2 | completed/actionable | Fresh reviewers found one remaining actionable wording issue in the `answer_with_citations` docstring on fallback-path guarantees; other repeated findings came from reviewers who inspected the top-level checkout instead of the isolated worktree. | Reviewer agents `Banach`, `Copernicus`, `Kierkegaard`, `Faraday`, `Ohm` |
| Review pass 2 remediation | completed | Narrowed the `answer_with_citations` docstring further and completed another verification rerun. | `api/tools.py`; `docs/plan/2026-06-15-search-tool-doc-alignment.md`; `python -m compileall api core environments fetching indexing search storage main.py`; `git diff --check`; `git diff --cached --check` |
| Verification pass 3 | completed | Re-ran Python syntax safety plus diff checks after review-pass-2 fixes. | `python -m compileall api core environments fetching indexing search storage main.py`; `git diff --check`; `git diff --cached --check` |
| Review pass 3 | completed/actionable | Fresh reviewers found remaining wording issues in the `answer_with_citations` wrapper docstring, `evidence_status` documentation, and plan freshness. | Reviewer agents `Huygens`, `Ramanujan`, `Turing`, `Russell`, `McClintock` |
| Review pass 3 remediation | completed | Tightened the wrapper docstring to pass-through semantics, narrowed `evidence_status` docs to current values, and refreshed the plan log. | `api/tools.py`; `docs/plan/2026-06-15-search-tool-doc-alignment.md` |
| Verification pass 4 | completed | Re-ran Python syntax safety plus diff checks after review-pass-3 fixes. | `python -m compileall api core environments fetching indexing search storage main.py`; `git diff --check`; `git diff --cached --check` |
| Review pass 4 | completed/actionable | Fresh reviewers found remaining helper-surface wording gaps for `debug`, `debug_markdown`, and one plan wording inconsistency. | Reviewer agents `Pauli`, `Hypatia`, `Singer`, `Newton`, `Dirac` |
| Review pass 4 remediation | completed | Qualified debug-only helper fields more precisely, narrowed the plan wording, and added a real FastMCP contract smoke result to the matrix. | `README.md`; `docs/plan/2026-06-15-search-tool-doc-alignment.md` |
| Verification pass 5 | completed | Re-ran Python syntax safety, public MCP contract smoke, and diff checks after review-pass-4 fixes. | `python -m compileall api core environments fetching indexing search storage main.py`; `python -m pytest -q tests/contracts/test_public_mcp_contracts.py`; `git diff --check`; `git diff --cached --check` |
| Review pass 5 | completed/actionable | Four reviewers reported no actionable findings; one reviewer correctly noted the missing functional smoke trace for the Python file touch. | Reviewer agents `Noether`, `Poincare`, `Pascal`, `Herschel`, `Bacon` |
| Review pass 5 remediation | completed | Ran the repo-required functional E2E smoke gate and updated the plan trace to record the code-touch verification path fully. | `./scripts/verify_functional_e2e.sh`; `docs/plan/2026-06-15-search-tool-doc-alignment.md` |
| Verification pass 6 | completed | Re-ran syntax, FastMCP contract, functional E2E smoke, and diff checks with the final plan updates. | `python -m compileall api core environments fetching indexing search storage main.py`; `python -m pytest -q tests/contracts/test_public_mcp_contracts.py`; `./scripts/verify_functional_e2e.sh`; `git diff --check`; `git diff --cached --check` |
| Review pass 6 | superseded | A fresh final pass was about to start, but latest-`main` refresh work intervened before that pre-refresh snapshot could clear the gate. | `docs/plan/2026-06-15-search-tool-doc-alignment.md` |
| Latest-main refresh | completed/actionable | Fast-forwarded the worktree to latest `origin/main`; stash reapply produced conflicts in `README.md`, `.agents/docs/architecture.md`, and the removed `docs/contextwiki-core-understanding.md`. | `git fetch origin main`; `git merge --ff-only origin/main`; `git stash pop` |
| Conflict preservation | completed | Recorded the conservative resolution decision in this plan, kept latest-main deletion of `docs/contextwiki-core-understanding.md`, and reapplied wording fixes onto surviving docs only. | `docs/plan/2026-06-15-search-tool-doc-alignment.md`; `README.md`; `.agents/docs/architecture.md` |
| Verification pass 7 | completed | Re-ran syntax, FastMCP contract smoke, functional E2E smoke, and diff checks against the latest-`main` refreshed tree before the new review pass. | `python -m compileall api core environments fetching indexing search storage main.py`; `python -m pytest -q tests/contracts/test_public_mcp_contracts.py`; `./scripts/verify_functional_e2e.sh`; `git diff --check`; `git diff --cached --check` |
| Review pass 7 | completed/actionable | Fresh reviewers found remaining README overstatement around evidence/debug availability plus stale post-refresh plan state and leftover conflict-artifact hygiene. | Reviewer agents `Anscombe`, `Wegener`, `Gauss`, `Hegel`, `Euler` |
| Review pass 7 remediation | completed | Tightened README helper-surface wording, refreshed the post-refresh plan log, and removed the temporary `.codex-conflict-preserver/` artifact directory from the worktree. | `README.md`; `docs/plan/2026-06-15-search-tool-doc-alignment.md`; `git status --short --branch` |
| Verification pass 8 | completed | Re-ran syntax, FastMCP contract smoke, functional E2E smoke, and diff checks after the review-pass-7 remediation. | `python -m compileall api core environments fetching indexing search storage main.py`; `python -m pytest -q tests/contracts/test_public_mcp_contracts.py`; `./scripts/verify_functional_e2e.sh`; `git diff --check`; `git diff --cached --check` |
| Review pass 8 | completed/actionable | Fresh reviewers found one remaining `answer_with_citations` docstring overstatement plus missing audit-trace wording for the full retained MCP tool check. | Reviewer agents `Maxwell`, `Nietzsche`, `Lorentz`, `Franklin`, `Lagrange` |
| Review pass 8 remediation | completed | Narrowed the `answer_with_citations` docstring to payload-level semantics on fallback paths and recorded the full retained-tool audit result in the plan. | `api/tools.py`; `docs/plan/2026-06-15-search-tool-doc-alignment.md` |
| Verification pass 9 | completed | Re-ran syntax, FastMCP contract smoke, functional E2E smoke, and diff checks after the review-pass-8 remediation. | `python -m compileall api core environments fetching indexing search storage main.py`; `python -m pytest -q tests/contracts/test_public_mcp_contracts.py`; `./scripts/verify_functional_e2e.sh`; `git diff --check`; `git diff --cached --check` |
| Review pass 9 | completed/actionable | Fresh reviewers found one remaining `sync_all` wording overstatement in the architecture doc plus durable traceability gaps in the plan. | Reviewer agents `Leibniz`, `Hume`, `Volta`, `Peirce`, `Linnaeus` |
| Review pass 9 remediation | completed | Narrowed the `sync_all` contract wording to disabled-source `skipped` behavior only, expanded the per-tool audit trace, and replaced deleted-path conflict evidence with durable plan/file references. | `.agents/docs/architecture.md`; `docs/plan/2026-06-15-search-tool-doc-alignment.md` |
| Verification pass 10 | completed | Re-ran syntax, FastMCP contract smoke, functional E2E smoke, and diff checks after the review-pass-9 remediation. | `python -m compileall api core environments fetching indexing search storage main.py`; `python -m pytest -q tests/contracts/test_public_mcp_contracts.py`; `./scripts/verify_functional_e2e.sh`; `git diff --check`; `git diff --cached --check` |
| Review pass 10 | completed/actionable | Fresh reviewers found one remaining plan-trace gap around per-tool docstring audit evidence and the missing final review-state row after verification pass 10. | Reviewer agents `Rawls`, `Arendt`, `Schrodinger`, `Ptolemy`, `Boyle` |
| Review pass 10 remediation | completed | Expanded the per-tool audit table to include each retained wrapper docstring surface and recorded this review pass explicitly in the progress log. | `docs/plan/2026-06-15-search-tool-doc-alignment.md` |
| Review pass 11 | superseded | A fresh clean pass started after the plan-trace-only remediation, but `origin/main` advanced again and forced another latest-main refresh before that pass could be accepted as final. | Reviewer agents `Rawls`, `Arendt`, `Schrodinger`, `Ptolemy`, `Boyle` |
| Latest-main refresh 2 | completed | Fetched current `origin/main`, stashed the local docs-only diff, fast-forwarded from `3354a76` to `cf00665`, and reapplied the scoped MCP wording changes without conflicts. | `git fetch origin main`; `git stash push --include-untracked`; `git merge --ff-only origin/main`; `git stash pop` |
| Review follow-up | completed/actionable | A late reviewer notification after the second refresh identified one remaining retained-table wording mismatch for `fetch_context` optional parameters. | Reviewer agent `Pasteur` |
| Review follow-up remediation | completed | Narrowed the `fetch_context` README signature to show optional `document_id` and `chunk_id` inputs, and updated the retained-tool audit row to match. | `README.md`; `docs/plan/2026-06-15-search-tool-doc-alignment.md` |
| Verification pass 12 | completed | Re-ran syntax, FastMCP contract smoke, functional E2E smoke, and diff checks after the second latest-main refresh plus the `fetch_context` wording fix. | `python -m compileall api core environments fetching indexing search storage main.py`; `python -m pytest -q tests/contracts/test_public_mcp_contracts.py`; `./scripts/verify_functional_e2e.sh`; `git diff --check`; `git diff --cached --check` |
| Review pass 12 | completed/actionable | Fresh reviewers found one remaining README precision gap: `fetch_context` still needed the “at least one input” requirement called out explicitly. | Reviewer agents `Mencius`, `Helmholtz`, `Feynman`, `Goodall`, `Mill` |
| Review pass 12 remediation | completed | Narrowed the `fetch_context` README row so the public table states that at least one of `document_id` or `chunk_id` must be provided, and refreshed the plan trace accordingly. | `README.md`; `docs/plan/2026-06-15-search-tool-doc-alignment.md` |
| Verification pass 13 | completed | Re-ran docs-only diff checks after the final `fetch_context` precision fix and plan refresh. | `git diff --check`; `git diff --cached --check`; `git status --short --branch` |
| Review pass 13 | completed/clean | Fresh five-reviewer pass on the latest `origin/main` snapshot reported no actionable findings after the final `fetch_context` precision fix and post-remediation verification trace update. | Reviewer agents `Hooke`, `Kant`, `Fermat`, `Zeno`, `Halley` |
