# Issue 59 Answer Helper Positioning

## User request

- Implement issue #59: reposition `answer_with_citations()` as a helper surface,
  not a core production answer API.

## Branch preflight result

- Dirty source worktree detected at `/Users/eunhwa/IdeaProjects/MCPContentSearch`
  on `feature/readme-rewrite-cleanup`; no branch switching or cleanup was
  performed there.
- Fetched `origin/main` and created isolated worktree
  `/Users/eunhwa/IdeaProjects/MCPContentSearch/.worktrees/issue-59-answer-helper`
  on fresh branch `feature/issue-59-answer-helper` from `origin/main`.
- This isolated worktree started clean.

## Scope and non-goals

### Scope

- Narrow README and maintained docs language so the core MCP story stays
  retrieval plus citation-ready evidence for downstream LLMs.
- Update tool/demo/smoke wording so `answer_with_citations()` is explicitly a
  helper preview/debug/eval surface.
- Add developer-facing validation guidance for `citations`, `used_chunks`,
  `debug`, and `debug_markdown`.

### Non-goals

- No MCP tool rename or alias in this issue.
- No payload shape or retrieval behavior change unless a wording-only helper
  description requires a focused string update.
- No local Chroma or SQLite inspection/mutation outside temporary test data.

## Acceptance criteria

- README and related docs explicitly state that downstream LLMs usually produce
  the final answer in production MCP usage.
- `answer_with_citations()` is described as a helper preview/debug/eval surface,
  not the main production answer path.
- Demo and smoke wording no longer imply that `answer_with_citations()` is the
  production answer API.
- Developer validation guidance tells readers to inspect `citations`,
  `used_chunks`, `debug`, and `debug_markdown`.
- Tool name stays unchanged and docs explicitly narrow its meaning.

## Step breakdown

1. `positioning-scan`
   - Read the README, maintained understanding doc, MCP tool description, and
     demo/smoke scripts that currently frame `answer_with_citations()`.
   - Confirm whether any relevant tests assert old wording.
2. `docs-and-string-update`
   - Update the owned docs/string surfaces without changing MCP payload shape or
     retrieval behavior.
   - Atomic single-owner implementation is acceptable because the work is a
     docs/string alignment slice with tightly coupled phrasing across a small
     set of files; parallel workers would add review/integration overhead
     without reducing risk.
3. `focused-verification`
   - Run targeted tests for any changed script output wording.
   - Run docs-only verification commands and diff checks.
4. `functional-smoke-matrix`
   - Record the affected matrix rows and safest substitutes in this plan.
5. `review-and-delivery`
   - Attempt mandatory review gate; if subagent review cannot run under current
     tool authorization, record the blocker and report it.

## Files likely to change

- `README.md`
- `docs/contextwiki-core-understanding.md`
- `api/tools.py`
- `search/answer_service.py`
- `scripts/demo_public_flow.py`
- `scripts/live_query_smoke.py`
- `tests/scripts/test_demo_public_flow.py`
- `tests/scripts/test_live_query_smoke.py`

## Test and verification plan

- Focused tests:
  - `uv run pytest tests/scripts/test_demo_public_flow.py tests/scripts/test_live_query_smoke.py`
- Docs/string verification:
  - `git status --short --branch`
  - `git diff --check`
  - `python -m compileall api search scripts`
- Stage relevant files before `git diff --cached --check` if preparing review.

## Functional smoke matrix

| Feature or workflow | Caller surface | Safest data mode | Expected result | Command | Result | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| README tool positioning | README spot-check | docs-only | `answer_with_citations()` described as helper preview/debug/eval surface and downstream LLM owns final answer generation | `rg -n "downstream LLM|helper answer|helper answer preview|debug_markdown|used_chunks" README.md` | passed | README lines 79-89, 310-357 now narrow the tool role and validation guidance. |
| Maintained core understanding note | maintained doc spot-check | docs-only | tool table and validation guidance match helper positioning | `rg -n "downstream LLM|helper answer|debug_markdown|used_chunks" docs/contextwiki-core-understanding.md` | passed | Maintained note lines 62-64, 194, 229-258, 284 now match helper positioning. |
| Demo wording | script test | temp SQLite/Chroma + bundled sample vault fixture | demo transcript frames answer step as helper preview, not production final answer | `uv run pytest tests/scripts/test_demo_public_flow.py` | passed | Included in focused suite: 12 script tests passed. |
| Live smoke wording | script test | mocked runtime/test fixture | smoke summary and sanitized payload framing describe helper validation guidance, including inspectable `used_chunks`, `debug`, and `debug_markdown` | `uv run pytest tests/scripts/test_live_query_smoke.py` | passed | Focused suite passed after reviewer-driven smoke fix; JSON mode now preserves `used_chunks` and answer debug fields in redacted form. |
| MCP tool runtime behavior | nearest safe substitute is compile + contract-preserving string review | no behavior change | no MCP contract or payload-shape change; only wording/docstring updates | `python -m compileall api search scripts` | passed | Compile completed for `api`, `search`, and `scripts`; no payload code changed. |

## Architecture/ADR constraints

- ADR 0001: keep tool handler vs service boundaries intact; do not move business
  behavior into `api/tools.py`.
- ADR 0002: do not change citation grounding semantics or payload safety.
- ADR 0006: keep the repo positioned as a slim MCP retrieval core; answers stay
  secondary to retrieval/citation-ready evidence.

## Risks and rollback notes

- Risk: wording drift across README, maintained docs, tool docstrings, and
  reviewer/demo scripts could leave mixed product language.
- Risk: overshooting into contract renaming would create avoidable MCP client
  impact for this issue.
- Rollback: revert only the wording/doc updates in this branch if reviewer or
  user prefers different positioning language.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created isolated worktree and fresh `feature/issue-59-answer-helper` from `origin/main` because the source worktree was dirty. | `git fetch origin main`; `git worktree add .worktrees/issue-59-answer-helper -b feature/issue-59-answer-helper origin/main` |
| Planning | completed | Captured scope, acceptance criteria, verification plan, smoke matrix, and atomic single-owner rationale. | `docs/plan/2026-06-14-issue-59-answer-helper-positioning.md` |
| Positioning scan | completed | Read README, maintained doc, tool/docstring surfaces, demo/smoke scripts, and relevant tests. | `sed`; `rg -n "answer_with_citations|debug_markdown|used_chunks"` |
| Docs/string update | completed | Repositioned README, maintained docs, tool/service docstrings, and demo/live-smoke wording around helper preview/debug/eval semantics without changing MCP payloads. | `git diff -- README.md docs/contextwiki-core-understanding.md api/tools.py search/answer_service.py scripts/demo_public_flow.py scripts/live_query_smoke.py` |
| Focused verification | completed | Focused script tests passed; compile and diff checks passed. | `uv run pytest tests/scripts/test_demo_public_flow.py tests/scripts/test_live_query_smoke.py -q` -> 12 passed; `python -m compileall api search scripts`; `git diff --check` |
| Functional smoke | completed | Recorded docs/script validation matrix rows and nearest safe substitute for unchanged MCP runtime behavior. | Matrix rows above; `rg -n "downstream LLM|helper answer|debug_markdown|used_chunks" README.md docs/contextwiki-core-understanding.md scripts/demo_public_flow.py scripts/live_query_smoke.py api/tools.py search/answer_service.py` |
| Review pass 1 | completed/invalid | Five fresh reviewers were spawned after user authorization, but every reviewer inspected the source checkout at `/Users/eunhwa/IdeaProjects/MCPContentSearch` instead of this isolated worktree, so their findings described stale pre-change files and were not actionable against the current diff. | Reviewer paths in notifications referenced `/Users/eunhwa/IdeaProjects/MCPContentSearch/...` instead of `/Users/eunhwa/IdeaProjects/MCPContentSearch/.worktrees/issue-59-answer-helper/...` |
| Review pass 2 | completed/actionable | Five fresh reviewers inspected the correct worktree. All actionable findings converged on one real gap: `scripts/live_query_smoke.py` advertised inspection of `used_chunks`, `debug`, and `debug_markdown`, but answer calls did not request debug and redacted JSON dropped `used_chunks`. | Reviewer notifications from worktree-local paths under `.worktrees/issue-59-answer-helper/...` |
| Review remediation 2 | completed | Changed `live_query_smoke` to request answer debug payloads, preserve redacted `used_chunks` plus answer-side `debug`/`debug_markdown` in JSON mode, added a `--json` inspection tip in text summary, and updated focused tests to guard the new behavior. | `scripts/live_query_smoke.py`; `tests/scripts/test_live_query_smoke.py`; `uv run pytest tests/scripts/test_live_query_smoke.py tests/scripts/test_demo_public_flow.py -q` -> 12 passed; `python -m compileall scripts`; `git diff --check` |
| Review pass 3 | completed/invalid | A clean review sweep was collected after remediation, but only four fresh reviewers were actually spawned because of an orchestration mistake, so this pass does not satisfy the repository's exact five-reviewer stop condition. | Clean reviewer outputs from `Carver`, `Erdos`, `Hilbert`, and `Lovelace`; missing fifth reviewer in spawn log. |
| Review pass 4 | completed/actionable | Five fresh reviewers inspected the correct worktree. Four reviewers reported no actionable findings; one reviewer found that the JSON smoke redactor/test still allowed nested raw `used_chunks.text`, which was broader than the documented raw-text redaction contract. | Reviewers `Aquinas`, `Gauss`, `Lagrange`, `Poincare` clean; reviewer `Ohm` flagged `used_chunks` redaction mismatch. |
| Review remediation 4 | completed | Tightened `used_chunks` JSON redaction to drop nested raw content keys while preserving inspectable ids/metadata, updated the focused test expectation, and reran affected verification. | `scripts/live_query_smoke.py`; `tests/scripts/test_live_query_smoke.py`; `uv run pytest tests/scripts/test_live_query_smoke.py tests/scripts/test_demo_public_flow.py -q` -> 12 passed; `python -m compileall scripts`; `git diff --check` |
| Review pass 5 | completed/actionable | Another fresh five-reviewer pass surfaced one remaining JSON redaction mismatch: answer-side nested `debug.preview` and `debug_markdown` preview lines could still preserve preview content despite the README claiming previews are removed in `--json` mode. | Reviewer `Dirac` flagged nested debug/markdown preview leakage; other findings in this pass were clean or stale relative to the immediate remediation. |
| Review remediation 5 | completed | Tightened nested debug redaction to drop `preview`/`text` keys and redact preview lines inside `debug_markdown`, expanded the focused test fixture to cover both cases, and reran affected verification. | `scripts/live_query_smoke.py`; `tests/scripts/test_live_query_smoke.py`; `uv run pytest tests/scripts/test_live_query_smoke.py tests/scripts/test_demo_public_flow.py -q` -> 12 passed; `python -m compileall scripts`; `git diff --check` |
| Review pass 6 | completed | Exactly five fresh reviewers inspected the latest worktree after the final redaction fix and all reported no actionable findings. | Reviewers `Aristotle`, `Pascal`, `Cicero`, `Hegel`, and `Wegener` all reported clean passes against `.worktrees/issue-59-answer-helper`. |
| Review gate | completed | Final fresh five-reviewer subagent pass had no actionable findings. | Review pass 6 clean; stop condition satisfied. |
