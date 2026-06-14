# Issue 32 Mixed Query Evals and CI Artifacts

## User request

Implement Issue `#32` from the latest `main`.

Issue summary:

- extend deterministic ContextWiki evals with mixed-language and mixed-format
  query coverage
- add reviewer-visible metrics, including latency summaries
- preserve deterministic non-live defaults
- publish eval outputs as CI artifacts or equivalent reviewer-visible evidence

## Branch preflight result

- Starting worktree: `/Users/eunhwa/IdeaProjects/MCPContentSearch`
- Starting branch: `feature/readme-rewrite-cleanup`
- Starting state: dirty
- Safety action: preserved the dirty worktree, fetched `origin/main`, and
  created isolated worktree `/private/tmp/MCPContentSearch-issue32-evals`
- Task branch: `feature/issue-32-mixed-query-evals`
- Task branch base: `origin/main` at `4e63398`

## Scope

- Extend local eval cases to cover mixed Korean, English, code, Markdown, and
  Obsidian-style retrieval/answer scenarios using deterministic fixture data.
- Add additive eval metrics so retrieval and answer suites report per-group
  totals plus latency summaries.
- Add a stable artifact-writing path for the eval runner.
- Upload eval summaries as CI artifacts and document how to read them.

## Non-goals

- No live LLM-as-judge or external API calls.
- No custom observability platform or dashboard.
- No MCP tool contract changes.
- No mutation, inspection, deletion, or migration of user SQLite or Chroma
  data.

## Acceptance criteria

- Eval fixtures include mixed-query cases that exercise multilingual and
  mixed-format retrieval/answer expectations.
- `run_contextwiki_eval` returns reviewer-meaningful suite summaries with
  group-level metrics and latency summaries while staying deterministic.
- The eval runner can write a stable artifact bundle to a caller-specified
  output directory.
- CI uploads eval outputs as artifacts on verification runs.
- README and eval docs explain the new metrics, artifacts, and intended usage.

## Step breakdown

1. Expand fixture documents and retrieval/answer case files for mixed query
   scenarios.
2. Add failing eval tests for grouped metrics, latency summaries, and artifact
   emission.
3. Implement additive eval summary and artifact-writing support in `evals/` and
   `scripts/run_contextwiki_eval.py`.
4. Wire CI to run the eval runner and upload the generated artifacts.
5. Update README, eval docs, and ContextWiki understanding note as needed.
6. Run focused verification, retained functional smoke, and required review
   gates before commit/PR delivery.

## Worker orchestration note

- This is not truly atomic work because it spans eval logic, fixtures/tests,
  CI workflow, and docs.
- The repository harness wants bounded worker delegation before target edits.
- User-approved exception for this session: main agent may implement directly.
- User-approved review exception for this session: final review may use three
  fresh reviewer subagents instead of the repository default five-reviewer
  loop.

## Likely changed files

- `evals/contextwiki_eval.py`
- `evals/retrieval_quality.py`
- `evals/answer_quality.py`
- `evals/contextwiki_fixture_documents.json`
- `evals/retrieval_quality_cases.json`
- `evals/contextwiki_answer_quality_cases.json`
- `evals/README.md`
- `scripts/run_contextwiki_eval.py`
- `tests/evals/test_retrieval_quality.py`
- possibly new focused eval tests under `tests/evals/`
- `.github/workflows/ci.yml`
- `README.md`
- `docs/contextwiki-core-understanding.md`

## Test and verification plan

- RED/GREEN focused tests:
  - `PYTHONPATH=. uv run pytest -q tests/evals/test_retrieval_quality.py`
  - `PYTHONPATH=. uv run pytest -q tests/evals`
- Focused runner checks:
  - `PYTHONPATH=. python scripts/run_contextwiki_eval.py`
  - `python -m compileall evals scripts tests/evals`
- Broader repo checks:
  - `python -m compileall api core environments fetching indexing search storage main.py`
  - `./scripts/verify_functional_e2e.sh`

## Functional smoke matrix

| Feature row | Caller surface | Data mode | Planned check | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| Deterministic eval runner | local CLI | temp SQLite + fixture retriever | run eval script and confirm mixed-query metrics plus artifact files | passed | `PYTHONPATH=. uv run --locked python scripts/run_contextwiki_eval.py --output-dir artifacts/contextwiki-evals --include-latency`; same-dir rerun without latency removes `runtime_metrics.json` |
| Retained retrieval flow unchanged | repo functional E2E | temp storage/non-live | `./scripts/verify_functional_e2e.sh` | passed | `344 passed`; unchanged retained MCP flow |
| CI artifact path | local workflow-equivalent check | local filesystem only | run artifact output mode and inspect expected JSON files | passed | `summary.json`, `retrieval_suite.json`, `answer_suite.json`, optional `runtime_metrics.json`; `artifacts/` ignored |
| Live source sync or live rewrite | not needed | real user data/external APIs | not run | not affected | issue scope is deterministic eval only |

## Architecture and ADR constraints

- ADR `0001`: keep MCP contracts in `api`, retrieval behavior in `search`, and
  eval scaffolding in `evals`/`scripts` without cross-layer shortcuts.
- ADR `0002`: use temporary/local persistence for deterministic verification and
  do not touch user SQLite or Chroma data.
- ADR `0006`: stay inside the slim MCP core; this work should strengthen the
  retained eval story, not reintroduce removed product surfaces.

## Risks and rollback notes

- Risk: latency summaries could become flaky if they rely on wall-clock
  thresholds. Mitigation: report measurements without asserting brittle timing
  budgets in deterministic tests.
- Risk: artifact format could drift without documentation. Mitigation: keep the
  artifact schema small, JSON-based, and covered by focused tests/docs.
- Rollback point: revert additive eval/CI/docs changes from this task branch
  only; no persisted data migration is involved.

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Preserved dirty source worktree and created isolated clean worktree from `origin/main`. | `git fetch origin main`; `git worktree add -b feature/issue-32-mixed-query-evals /private/tmp/MCPContentSearch-issue32-evals origin/main` |
| Planning | completed | Read harness docs, workflow docs, architecture, ADR `0001`/`0002`/`0006`, current eval code, CI workflow, and issue `#32`; wrote this plan. | `.agents/docs/harness-engineering.md`; `.agents/docs/github-workflow.md`; `.agents/docs/architecture.md`; `.agents/docs/adr/README.md`; relevant ADRs; `gh issue view 32`; eval/CI source files |
| Delegation gate | completed | User approved direct main-agent implementation and a three-reviewer subagent review exception for this task. | User message in this thread |
| Implementation | completed | Expanded fixture documents/cases, added grouped deterministic eval summaries plus optional runtime metrics, wired CI artifact upload, hardened CLI failure behavior, and aligned docs. | `evals/contextwiki_eval.py`; `evals/retrieval_quality.py`; `evals/answer_quality.py`; fixture/case JSON; `scripts/run_contextwiki_eval.py`; `.github/workflows/ci.yml`; docs |
| Focused verification | completed | RED/GREEN eval coverage, CLI failure path, deterministic artifact rerun checks, and compile checks passed. | `PYTHONPATH=. uv run pytest -q tests/evals/test_retrieval_quality.py` -> `12 passed`; `PYTHONPATH=. uv run pytest -q tests/evals` -> `22 passed`; `python -m compileall evals scripts tests/evals` |
| Broader verification | completed | Broader compile and retained functional smoke stayed green for unchanged MCP/search flows. | `python -m compileall api core environments fetching indexing search storage main.py`; `./scripts/verify_functional_e2e.sh` -> `344 passed` |
| Review pass 1 | completed/actionable | Initial 3-reviewer exception pass found deterministic artifact drift, unignored artifact output, and weak mixed-format answer coverage. | Reviewer findings in thread from Ramanujan, Poincare, Lovelace |
| Review pass 1 remediation | completed | Split deterministic artifacts from optional runtime metrics, ignored `artifacts/`, strengthened answer expectations, and added code-format answer coverage. | `evals/contextwiki_eval.py`; `.gitignore`; answer cases/tests; docs |
| Review pass 2 | completed/actionable | Fresh 3-reviewer exception pass found stale `runtime_metrics.json` on same-dir reruns and an eval-doc overstatement. | Reviewer findings in thread from Rawls and Euclid |
| Review pass 2 remediation | completed | Removed stale runtime-metrics files on deterministic reruns, added regression coverage, and corrected eval README wording. | `evals/contextwiki_eval.py`; `tests/evals/test_retrieval_quality.py`; `evals/README.md` |
| Review pass 3 | completed/actionable | Fresh 3-reviewer exception pass found CLI non-zero exit missing for failed evals, mixed-language answer duplication, and stale plan status. | Reviewer findings in thread from Peirce and Cicero |
| Review pass 3 remediation | completed | Made CLI fail on `passed=false`, made mixed-language answer coverage distinct, and synchronized this plan’s smoke matrix/progress log with actual evidence. | `scripts/run_contextwiki_eval.py`; answer cases/tests; this plan |
