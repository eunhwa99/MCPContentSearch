# AI Portfolio Evaluation and Documentation Plan

## User Request

Update from the latest `main`, execute the project, and improve both code and
documentation so ContextWiki is stronger evidence for AI-related job
applications.

## Branch Preflight Result

- Original worktree:
  `/Users/eunhwa/IdeaProjects/MCPContentSearch`
- Original branch: `main`
- Original state: dirty, with nine modified files; local `main` was ten commits
  behind `origin/main`.
- Safety decision: the original worktree was not switched, pulled, cleaned, or
  edited.
- Freshness check: `git fetch origin main` completed successfully.
- Isolated worktree:
  `/Users/eunhwa/IdeaProjects/MCPContentSearch-ai-portfolio`
- Task branch: `feature/ai-portfolio-evals`
- Base: `origin/main` at `9495adab87dc0ac7c236ce4606d122ab0f80fc78`
- Divergence at creation: `0 0` against `origin/main`.
- Local branch cleanup: no existing branch was deleted. Many non-`main`
  branches are linked to retained worktrees or may contain local-only work, so
  preserving them is safer than broad cleanup.

## Scope

1. Make the deterministic evaluation output communicate standard,
   reviewer-readable retrieval and grounding metrics without overstating what
   the fixture suite proves.
2. Produce a stable Markdown evaluation artifact alongside the existing JSON
   artifacts so CI output can be read without manually interpreting raw JSON.
3. Add focused tests for metric semantics, empty/negative cases, and generated
   artifact behavior.
4. Rewrite the public README opening and evaluation sections around the
   problem, architecture decision, reproducible demo, measured evidence, and
   limitations.
5. Add maintained documentation for evaluation methodology and privacy/data
   egress.
6. Keep the retained MCP tool surface and runtime architecture unchanged.

## Non-Goals

- No new MCP tools, source connectors, model providers, vector stores, or web
  application.
- No live Notion, Tistory, GitHub, OpenAI, or private-data validation.
- No inspection or mutation of user ChromaDB or SQLite data.
- No claim that deterministic fixture metrics represent production model
  quality.
- No GitHub repository settings change, release publication, or license choice;
  those require separate owner decisions.
- No reuse of the dirty original worktree changes.

## User-Directed Review Override

After the first final review pass, the user explicitly requested that future
review passes use two reviewers instead of five. This overrides the repository
default for the remaining work in this task. The post-fix final review pass
will therefore use exactly two fresh read-only reviewers, and the delivery
report will state this exception rather than claiming a five-reviewer final
pass.

## Acceptance Criteria

- Retrieval eval summary includes clearly named ranking metrics derived from
  explicit relevance labels, including hit rate, mean reciprocal rank, recall,
  and nDCG at the case `top_k`.
- Answer eval summary includes status accuracy, required-citation recall,
  citation coverage, and insufficient-status accuracy with
  denominators or scorable-case counts.
- Metrics handle empty suites, negative retrieval cases, and insufficient
  answer cases without division errors or misleading perfect scores.
- `scripts/run_contextwiki_eval.py --output-dir <temp>` writes the existing JSON
  artifacts plus a deterministic human-readable Markdown report.
- Existing JSON keys and pass/fail semantics remain compatible.
- Focused tests prove the new metrics and artifact contract.
- README leads with a concise portfolio narrative, reproducible demo, current
  deterministic snapshot, explicit limitations, architecture trade-offs, and
  links to maintained evaluation/privacy docs.
- Documentation states that query rewrite and default embeddings can cause
  external egress and distinguishes the fully deterministic local eval/demo
  from live-provider behavior.
- Architecture documentation stays aligned if the maintained eval or privacy
  explanation layer changes.
- Full verification and functional smoke pass before review.
- Per the later user-directed override, exactly two fresh reviewers in the
  post-fix final pass report no actionable findings.

## Ordered Steps and Worker Ownership

1. **Evaluation implementation worker**
   - Owns: `evals/retrieval_quality.py`, `evals/answer_quality.py`,
     `evals/contextwiki_eval.py`, `scripts/run_contextwiki_eval.py`.
   - May add a small dedicated report module under `evals/` when that reduces
     coupling.
   - Must preserve existing public JSON fields and deterministic output.
2. **Evaluation test worker**
   - Owns: `tests/evals/` and `tests/scripts/test_run_contextwiki_eval.py`.
   - Adds focused metric and report artifact coverage without touching
     production/eval implementation files.
3. **Portfolio documentation worker**
   - Owns: `README.md`, `evals/README.md`, new `docs/evaluation.md`, new
     `SECURITY.md`, and `.agents/docs/architecture.md` only if required for
     maintained-doc alignment.
   - Must use only verified repository behavior and clearly label fixture
     metrics and live-check limits.
4. **Main-agent integration**
   - Inspects all worker diffs, resolves interface mismatches, updates this
     plan, runs verification/smoke, routes review findings, and owns delivery.
5. **CI evidence integration worker**
   - Owns: `.github/workflows/ci.yml` and
     `tests/scripts/test_verification_architecture.py`.
   - Publishes the generated deterministic Markdown report to the GitHub
     Actions step summary without weakening artifact upload or verification.

Workers share the task branch but have disjoint ownership. They must preserve
other user/agent changes, must not commit, push, open PRs, inspect secrets,
inspect local Chroma/SQLite contents, mutate user data, or run live external
source checks.

## Likely Changed Files

- `evals/retrieval_quality.py`
- `evals/answer_quality.py`
- `evals/contextwiki_eval.py`
- optional `evals/reporting.py`
- `scripts/run_contextwiki_eval.py`
- `tests/evals/test_retrieval_quality.py`
- `tests/evals/test_answer_quality.py`
- `tests/scripts/test_run_contextwiki_eval.py`
- `.github/workflows/ci.yml`
- `tests/scripts/test_verification_architecture.py`
- `README.md`
- `evals/README.md`
- `docs/evaluation.md`
- `SECURITY.md`
- `.agents/docs/architecture.md` if maintained assumptions need clarification
- this plan document

## Test and Verification Plan

Focused:

```bash
uv run --locked pytest -q tests/evals tests/scripts/test_run_contextwiki_eval.py
uv run --locked python scripts/run_contextwiki_eval.py --output-dir <temp>
python -m compileall evals scripts/run_contextwiki_eval.py
git diff --check
```

Broader:

```bash
./scripts/verify_functional_e2e.sh
./scripts/demo.sh
./scripts/verify_all.sh
```

No live smoke will run because it can use configured credentials, external
providers, and user storage. Deterministic fixtures and temporary storage are
the nearest safe substitutes.

## Functional Smoke Matrix

| Feature | Caller Surface | Data Mode | Expected Result | Action/Command | Result | Evidence | Skip Reason / Substitute |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Eval CLI and artifacts | CLI | Temporary directory and fixture SQLite | JSON plus Markdown artifacts; passing metrics | `uv run --locked python scripts/run_contextwiki_eval.py --output-dir <temp>` | passed | Post-review rerun PASS; 13/13 retrieval, 9/9 answer; report generated under `/tmp/contextwiki-portfolio-eval-fix.o5UJrj` | N/A |
| Public demo | CLI script | Bundled sample vault, temp storage | Sync, status, search, and grounded helper preview complete | `./scripts/demo.sh` | passed | Source sync succeeded; three active documents; stale-citation query returned grounded citation | N/A |
| MCP `list_sources` and `get_sync_status` | Functional E2E | Fake/temp metadata | Stable source/status payloads | `./scripts/verify_functional_e2e.sh` | passed | Post-review retained functional E2E: 25 passed | N/A |
| MCP `sync_source` and `sync_all` | Functional E2E | Fake connector and temp storage | Stable running/aggregate behavior | `./scripts/verify_functional_e2e.sh` | passed | Post-review retained functional E2E: 25 passed | N/A |
| MCP `search_context`, `search_documents`, `fetch_context` | Functional E2E | Fixture/temp storage | Validated retrieval and direct fetch remain stable | `./scripts/verify_functional_e2e.sh` | passed | Post-review retained functional E2E: 25 passed | N/A |
| Internal grounded answer | Demo and eval CLI | Fixture/temp storage | Grounded and insufficient behavior is measured truthfully | `./scripts/demo.sh`; eval CLI | passed | Post-review demo grounded citation; eval status 9/9 and insufficient-status 1/1 | N/A |
| Live source sync and live LLM/embedding | Configured external caller | Would use external services/user data | Not executed without explicit source and data approval | Not run | blocked/gated | No live call or user-store access performed | Requires credentials and user-data mutation; deterministic fake/temp flows passed as the substitute |

## Architecture Constraints

- SQLite remains the authoritative active-document/citation gate.
- Chroma/LlamaIndex remains the retrieval accelerator.
- The retained seven-tool MCP surface and response contracts do not change.
- The internal citation answer helper remains an evaluation/demo surface, not
  a newly public MCP answer tool.
- Stable deterministic artifacts remain separate from optional latency output.
- Query-rewrite egress and embedding-provider egress must be described
  separately and accurately.

## Risks and Rollback

- **Metric ambiguity:** standard names can overclaim quality if relevance labels
  are incomplete. Mitigation: document label semantics, denominators, fixture
  scope, and negative/insufficient cases.
- **README drift:** hard-coded numbers can become stale. Mitigation: generate a
  canonical report and add contract tests or wording that identifies the
  checked-in snapshot scope.
- **Artifact compatibility:** consumers may rely on existing JSON keys.
  Mitigation: add fields and files; do not remove or rename existing fields.
- **Concurrent worker overlap:** workers use disjoint ownership and must not
  revert each other. Main agent owns integration.
- Rollback point is the clean feature branch base `9495ada`; no user-data
  migration or runtime contract change is planned.

## Progress Log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Repository instructions | completed | Read harness, architecture, workflow, phase skills, smoke matrix, and review-loop contract. | Skill and doc reads |
| Branch preflight | completed | Preserved dirty original worktree and created fresh isolated feature branch from current `origin/main`. | `git status`; `git rev-list`; worktree creation |
| Plan | completed | Defined bounded code, test, docs, smoke, and review scope. | This document |
| Worker implementation | completed | Added additive ranking/grounding metrics and deterministic Markdown reporting. | `/root/eval_implementation`; 28 focused tests passed |
| Worker tests | completed | Added metric boundary, compatibility, CLI, and report tests. | `/root/eval_tests`; 28 focused tests passed |
| Worker docs | completed | Reframed README and added evaluation/security guidance with explicit limitations. | `/root/portfolio_docs`; demo passed; 29 targeted tests passed |
| CI evidence integration | completed | Publishes the generated Markdown report in GitHub Actions step summary with a contract test. | `/root/ci_eval_evidence`; 5 focused tests passed |
| Focused verification | completed | Focused test union, eval CLI, Ruff, mypy, compile, and diff checks passed. | `33 passed`; eval PASS; Ruff/mypy/compileall clean |
| Functional smoke | completed | Deterministic E2E and public demo passed; live/user-data checks remain gated. | `25 passed`; demo exit 0; matrix above |
| Middle review | completed | Pass 1 findings were fixed and reverified; all five fresh reviewers in pass 2 reported no actionable findings. | Pass 2 reviewers 1-5 clean; focused 69; E2E 25; demo/eval PASS |
| Refactor | completed | Reviewed introduced code for duplication, boundaries, names, and test clarity; no safe refactor justified additional churn. | Main-agent diff inspection after clean middle review |
| Integration | completed | Full repository gate passed after an environment-only disk-space retry; latest `origin/main` was refetched and remains the branch base. | `709 passed`, 87.37% coverage, eval PASS, E2E 25; `HEAD...origin/main` = `0 0`; actionlint clean |
| Final review | completed | Five-reviewer pass 1 found a shared label issue. After the user's two-reviewer override, pass 2 found stale plan wording and README demo duplication; both were fixed, reverified, and both fresh reviewers in pass 3 reported no actionable findings. | Post-fix focused 85; demo PASS; diff check clean; pass 3 reviewers 2/2 clean |
| Delivery | in_progress | Final clean review gate passed; commit, push, and `main`-base PR remain under main-agent ownership. | Ready for explicit staging |

### Middle Review Pass 1 Findings

- `evals/retrieval_quality.py` removes malformed result rows before assigning
  rank, which can promote a later relevant result and overstate MRR/nDCG.
  Preserve original positions and add a regression test.
- README and `SECURITY.md` describe local/non-egress embeddings as configurable
  without clarifying that the current production composition has no supported
  environment switch; document that code-level customization is currently
  required.
- `docs/evaluation.md` and `evals/README.md` must call the fixture retriever a
  deterministic lexical stand-in, not imply semantic vector retrieval.
- `SECURITY.md` needs an actionable private vulnerability-reporting path or a
  clear statement not to send sensitive evidence through public issues.
- Rename the status-only answer metric from `abstention_accuracy` to
  `insufficient_status_accuracy`; the evaluator does not prove semantic
  abstention from every unsupported continuation.
- Add a failing-suite report/CLI regression proving `FAIL`, failed case IDs,
  JSON/Markdown artifacts, and exit code `1`.
- Remove the unsupported claim that the deterministic eval covers inactive
  active-gate regressions; the current eval fixtures seed only active records.
- Remove the `llama_cache/` persistent-storage claim because the current
  `Settings.cache_dir` assignment is not a demonstrated cache backend.
- Canonical repository ownership is verified as `eunaverse`; align the
  remaining source-controlled GitHub user-agent URL in configuration and keep
  the old remote URL only as a redirecting local Git setting.

### Integration Retry Note

The first `./scripts/verify_all.sh` attempt passed static checks, 11 public MCP
contract tests, all 709 non-live tests at 87.37% coverage, and the deterministic
eval. It then failed before launching its final E2E subprocess because the host
volume had only 32 MiB available and Python could not create a temporary file.
This was classified as an environment blocker, not a candidate failure.

Only the isolated task worktree's reproducible 525 MiB `.venv` was removed and
replaced with a symlink to the original project's existing compatible Python
3.13 environment. No user data, shared cache, ChromaDB, SQLite, credentials, or
source files were deleted. The second unchanged `./scripts/verify_all.sh` run
completed successfully: 709 tests passed, coverage was 87.37%, deterministic
eval passed, and the final functional E2E layer passed 25 tests. The verbose
local log is retained at `/tmp/contextwiki-verify-all-portfolio.log`; existing
SQLite `ResourceWarning` noise remains out of this task's scope.

### Final Review Pass 1 Finding

`evals/reporting.py` labels the full nine-case answer suite and answer failures
as `Grounded answer`, although one retained case intentionally expects an
`insufficient` status. Rename the reviewer-facing label to the neutral
`Answer quality` and add report regression coverage, including a failing
insufficient case so the failure section cannot drift back to the inaccurate
label.
