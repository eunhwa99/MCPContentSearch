# Issue 58 Query Redaction

## User request

- 최신 `main` 기준으로 GitHub issue #58 작업

## Branch preflight result

- Original worktree: `/Users/eunhwa/IdeaProjects/MCPContentSearch`
- Original branch/state: `feature/readme-rewrite-cleanup`, dirty
- Safety action: fetched `origin/main` to `ea76c97` and created isolated worktree `/private/tmp/MCPContentSearch-issue58`
- Working branch: `feature/issue-58-query-redaction`
- Atomic self-implementation rationale: the scoped fix is limited to one MCP tool response path plus targeted contract tests; no safe multi-worker boundary is needed, and subagent workers are not authorized unless explicitly requested by the user

## Scope and non-goals

### Scope

- Align `search_documents()` public query echo with existing MCP public-output redaction policy
- Cover both empty-result/no-public-source path and normal result path
- Add focused MCP contract regression tests

### Non-goals

- Do not change retrieval, ranking, grouping, or source-filter behavior
- Do not change `search_context()` or `answer_with_citations()` semantics except via shared existing helper reuse
- Do not inspect or mutate user Chroma/SQLite data

## Acceptance criteria

- `search_documents()` returns redacted `query` text on the service-missing path
- `search_documents()` returns redacted `query` text on the no-public-source path
- `search_documents()` returns redacted `query` text on the normal result path
- Focused tests use a secret-like query string and assert the raw secret is absent from the public payload
- No unrelated behavior change to grouped document results or filter sanitization

## Step breakdown

1. Add failing MCP contract tests for `search_documents()` query redaction paths
2. Run focused pytest to confirm red failure
3. Apply minimal `api/tools.py` fix using the existing public query redaction helper
4. Re-run focused pytest and compile check
5. Run task-relevant functional smoke and record matrix
6. Attempt harness review gate or record authorization blocker

## Files likely to change

- `api/tools.py`
- `tests/api/test_tools_contract.py`
- `docs/plan/2026-06-14-issue-58-query-redaction.md`

## Test and verification plan

- Focused pytest:
  - `uv run --locked pytest tests/api/test_tools_contract.py -q`
  - If needed, narrower selectors while iterating on TDD
- Syntax/import safety:
  - `python -m compileall api core environments fetching indexing search storage main.py`
- Functional gate:
  - `./scripts/verify_functional_e2e.sh`

## Functional smoke matrix

| Feature or workflow | Caller surface | Safest data mode | Expected visible result | Command | Result | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `search_documents` normal public payload | MCP contract pytest | Fake context search | Returned `query` is redacted, results preserved | `uv run --locked pytest tests/api/test_tools_contract.py -q` | passed | New regression `test_search_documents_redacts_query_text_in_normal_public_payload`; full contract file `34 passed` |
| `search_documents` no-public-source payload | MCP contract pytest | Fake registry/filter sanitization | Returned `query` is redacted, results empty | `uv run --locked pytest tests/api/test_tools_contract.py -q` | passed | New regressions for service-missing and no-public-source paths; targeted TDD ended `3 passed` |
| Neighbor MCP contract stability | Functional E2E suite | Temp/fake retained harness | Retained MCP flows still pass | `./scripts/verify_functional_e2e.sh` | passed | `355 passed in 18.75s` |
| Live external sources | Real MCP/live sync | Requires credentials and user data | Not needed for this privacy fix | Not run | not affected | Local fake/temp coverage only |

## Architecture and ADR constraints

- Keep MCP contract formatting in `api/tools.py` per architecture and ADR 0001
- Preserve slim retained MCP tool scope and public contract shape per ADR 0006
- Avoid any Chroma/SQLite behavior changes per architecture and ADR 0002

## Risks and rollback notes

- Risk: missing one `search_documents()` return path would keep public payloads inconsistent
- Risk mitigation: cover service-missing, filtered-empty, and normal result paths in tests
- Rollback: revert the narrow `search_documents()` query-field redaction and associated tests only

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Fetched `origin/main` and created isolated `feature/issue-58-query-redaction` worktree from `ea76c97`. | `git fetch origin main`; `git worktree add /private/tmp/MCPContentSearch-issue58 -b feature/issue-58-query-redaction origin/main` |
| Planning | completed | Scoped fix to `search_documents()` public payload redaction and targeted contract tests. | Issue #58 body; code search in `api/tools.py` and `tests/api/test_tools_contract.py` |
| Implementation | completed | Added failing contract regressions for three `search_documents()` response paths, then applied minimal MCP-boundary redaction reuse in `api/tools.py`. | Targeted TDD cycle on `tests/api/test_tools_contract.py` and `api/tools.py` |
| Focused verification | completed | Confirmed initial red failure, then reran targeted tests green, full contract file, and compile check. | `uv run --locked pytest -q tests/api/test_tools_contract.py::test_search_documents_redacts_query_text_when_service_is_missing tests/api/test_tools_contract.py::test_search_documents_redacts_query_text_when_no_public_source_matches tests/api/test_tools_contract.py::test_search_documents_redacts_query_text_in_normal_public_payload` -> 3 failed, then 3 passed; `uv run --locked pytest tests/api/test_tools_contract.py -q` -> 34 passed; `python -m compileall api core environments fetching indexing search storage main.py` |
| Functional smoke | completed | Retained deterministic functional E2E suite passed after the privacy fix. | `./scripts/verify_functional_e2e.sh` -> 355 passed |
| Review gate | completed | Fresh five-reviewer subagent pass reported no actionable findings. | Reviewers `Singer`, `Hubble`, `Feynman`, `Copernicus`, `Pascal` all reported no actionable findings |
