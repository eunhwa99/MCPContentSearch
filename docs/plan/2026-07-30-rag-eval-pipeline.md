# Plan: RAG evaluation dataset, metrics, benchmark, and report

## User request

Implement a four-phase RAG evaluation pipeline for MCPContentSearch:

1. Public synthetic RAG evaluation dataset with train/dev/test splits.
2. Standard retrieval/citation quality metrics with numerator/denominator and N/A handling.
3. Retrieval benchmark runner comparing lexical baseline vs live embedding/vector (and hybrid when available), gated behind `--live`.
4. RAG evaluation report with CI artifact retention, without claiming fixture results as production performance.

Always develop with strict TDD. Do not inspect or mutate user Chroma/SQLite or personal documents. Do not cherry-pick `feature/ai-portfolio-evals`; use it only as reference.

## Branch preflight result

- Worktree was clean on `main`.
- Freshness: `git fetch origin main` and `git pull --ff-only origin main` succeeded (`62a392f` -> `4b1948c`).
- Safe local cleanup: deleted merged `feature/remove-sync-wait-timeout`; other non-`main` branches are linked to worktrees or unmerged and were preserved.
- Task branch: `feature/rag-eval-pipeline` from updated `main` (`0 0` vs `origin/main`).
- Safety: no user Chroma/SQLite inspection or mutation.

## Current eval structure analysis

Existing deterministic quality layer:

| Surface | Role | Gap vs request |
| --- | --- | --- |
| `evals/contextwiki_fixture_documents.json` | Flat active fixture chunks | No README/ADR/runbook KB; no inactive docs; personal-ish repo names; no train/dev/test |
| `evals/retrieval_quality_cases.json` | Pass/fail checks (`expected_top_chunk_id`, required/forbidden) | No document IDs, no `no_answer`, no split, no Hit/MRR/Recall/nDCG |
| `evals/retrieval_quality.py` | Binary check scoring (`average_score`) | Missing standard RAG ranking metrics and N/A/scorable counts |
| `evals/answer_quality.py` | Citation/status checks | Needs citation precision/recall and insufficient accuracy as explicit metrics |
| `evals/contextwiki_eval.py` | Temp SQLite + lexical `FixtureVectorIndexRetriever` | Always active seeding; no inactive injection; no live embedding path |
| `scripts/run_contextwiki_eval.py` | JSON artifacts | No Markdown report; no benchmark modes; no CSV |
| CI `run_contextwiki_eval` | Uploads JSON artifacts | Needs Markdown report + clearer fixture-vs-live separation |

Reference branch `origin/feature/ai-portfolio-evals` already drafts metric reporting and `docs/evaluation.md`, but current `main` differs. Reimplement against current contracts; do not cherry-pick.

## Dataset design (Phase 1)

### Synthetic project knowledge base

Public, fictional product: **Aurora Relay** (synthetic event-routing service). No company/personal content.

Document inventory (version `rag_v1`):

| Doc ID | Type | Language | Active | Role |
| --- | --- | --- | --- | --- |
| `aurora:readme` | README | EN | yes | Product overview, setup, MCP search responsibility boundary |
| `aurora:adr-001-sqlite-gate` | ADR | EN | yes | SQLite as lifecycle authority over vector store |
| `aurora:adr-002-no-llm-rewrite` | ADR | EN/KO mixed | yes | Deterministic query normalization only |
| `aurora:runbook-incident-queue-lag` | runbook | EN | yes | Queue lag triage steps |
| `aurora:runbook-reindex-chroma` | runbook | KO/EN mixed | yes | Safe reindex procedure using temp paths |
| `aurora:guide-citation-contract` | guide | EN | yes | Citation fields and insufficient behavior |
| `aurora:hardneg-vector-is-source-of-truth` | hard-negative | EN | yes | Plausible but wrong claim (vector store is authority) |
| `aurora:hardneg-llm-rewrite-required` | hard-negative | KO/EN | yes | Wrong claim that LLM rewrite is required |
| `aurora:legacy-webhook-v1` | stale/inactive | EN | **inactive** | Old webhook runbook; seeded then tombstoned |
| `aurora:no-match-padding` | distractor | EN | yes | Unrelated billing notes to dilute lexical hits |

Inactive document seeding: upsert then tombstone via MetadataStore APIs so `get_chunk` returns `None` while the fixture retriever can still inject the tombstoned chunk as a fake vector candidate. This measures SQLite active-gate blocking without touching user data.

### Case label schema

Each question case defines:

```json
{
  "case_id": "test-runbook-queue-lag",
  "split": "test",
  "group": "runbook",
  "query": "How do I triage Aurora Relay queue lag?",
  "relevant_document_ids": ["aurora:runbook-incident-queue-lag"],
  "relevant_chunk_ids": ["aurora-runbook-queue-lag-chunk"],
  "forbidden_chunk_ids": [
    "aurora-hardneg-vector-authority-chunk",
    "aurora-legacy-webhook-v1-chunk"
  ],
  "expected_source_id": "source_aurora_docs",
  "no_answer": false,
  "top_k": 5,
  "notes": "Positive runbook retrieval"
}
```

Rules:

- `no_answer: true` cases have empty relevant IDs; ranking metrics are N/A; pass/fail checks empty/forbidden behavior.
- `forbidden_chunk_ids` includes hard-negatives and inactive chunks that must not appear.
- `top_k` is per-case evaluation depth (default 5 for new suite; existing ContextWiki cases keep their own `top_k`).
- Splits: **train** (fixture development only), **dev** (local iteration), **test** (CI/report only). Retrieval logic must not tune against test labels.
- Default deterministic runner continues to support existing ContextWiki fixtures; new RAG suite is additive under `evals/datasets/rag_v1/`.

### Split sizes (target)

| Split | Positive | No-answer | Stale-block | Hard-negative focus | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| train | 4 | 1 | 1 | 1 | ~7 |
| dev | 4 | 1 | 1 | 1 | ~7 |
| test | 6 | 2 | 2 | 2 | ~12 |

Exact IDs finalized during implementation; counts may adjust slightly while preserving coverage of all required document types.

### Layout

```text
evals/datasets/rag_v1/
  VERSION                 # "rag_v1"
  manifest.json           # version, policy, counts, non-goals
  documents.jsonl         # synthetic docs/chunks + active flag
  cases/
    train.jsonl
    dev.jsonl
    test.jsonl
  README.md               # public dataset card
```

Compatibility: keep existing `evals/contextwiki_fixture_documents.json` and current runners working. New dataset is consumed by extended metrics/benchmark/report paths.

## Metrics design (Phase 2)

Additive `quality_metrics` on suite JSON (do not remove existing keys):

| Metric | Numerator | Denominator | N/A when |
| --- | --- | --- | --- |
| Hit@K | cases with ≥1 relevant chunk in top K | scorable positive cases | no relevant labels or `no_answer` |
| MRR@K | sum of `1/rank` of first relevant (0 if miss) | scorable positive cases | same |
| Recall@K | sum of \|hit relevants\| / \|labeled relevants\| | scorable positive cases | same |
| nDCG@K | sum of binary DCG/IDCG | scorable positive cases | same |
| citation precision | cited chunks ∩ relevant / cited | cases with citations labeled or produced per case rules | no citation labels and no citations to score |
| citation recall | cited ∩ required / required | cases with required citation labels | no required labels |
| insufficient accuracy | correct insufficient status | labeled insufficient cases | none labeled |
| stale/inactive block rate | cases where forbidden inactive chunks absent | cases with inactive forbidden labels | none labeled |

Also emit: `scorable_case_count`, per-metric numerator/denominator/value (`null` = N/A), group breakdowns, failed case IDs + failure reasons. Markdown + JSON.

## Benchmark design (Phase 3)

`scripts/run_retrieval_benchmark.py`:

- Modes: `lexical` (default), `vector` / `hybrid` only with `--live`.
- Same **test** split for all executed modes.
- Metrics: Hit@5, MRR@5, Recall@5, nDCG@5, citation recall, mean/P95 latency, per-query embedding cost estimate.
- `--max-budget` aborts live runs before exceeding budget; do not score unrun providers as 0.
- Separate `provider_error` vs `quality_failure`.
- Outputs: JSON, CSV, Markdown under output dir.
- Never print or persist API keys.

## Report design (Phase 4)

`evals/reporting.py` + CI:

- Dataset version, retrieval config, overall/group metrics, citation metrics, stale block, latency, failures, baseline delta (optional prior artifact), limitations.
- Separate tables: fixture/lexical vs live embedding.
- Explicit disclaimer: fixture ≠ production performance.
- CI: run deterministic eval, upload JSON + Markdown artifacts; update `evals/README.md` and architecture notes if maintained assumptions change.

## Scope and non-goals

**In scope:** synthetic dataset, metrics, benchmark runner, report, tests, CI artifact wiring, architecture/eval docs alignment.

**Non-goals:** Agent execution, LLM cost product features, MCP contract changes, live Notion/Tistory/GitHub sync, user-data mutation, cherry-picking portfolio branch, claiming fixture scores as production quality.

## Acceptance criteria

1. Public `rag_v1` synthetic KB with README/ADR/runbook/inactive/hard-negative/mixed-language docs and train/dev/test cases with full labels.
2. Metrics include Hit/MRR/Recall/nDCG@K, citation precision/recall, insufficient accuracy, stale block rate with num/den, N/A, scorable counts, group + failure detail, JSON+Markdown.
3. Existing JSON suite keys and MCP response contracts remain compatible.
4. Benchmark runner defaults to no external API; `--live` + budget required for embeddings; provider vs quality failures separated; unrun modes not zero-scored; JSON/CSV/Markdown out.
5. Report + CI artifacts; fixture and live tables separated; limitations stated.
6. Unit, integration, and deterministic E2E coverage added under TDD; focused tests and `./scripts/verify_all.sh` pass; temp SQLite/fixtures only.

## Ordered steps and worker ownership

Stacked on one branch (shared contracts). Sequential handoff, not independent PRs.

1. **Dataset worker** — `evals/datasets/rag_v1/**`, dataset loader helpers under `evals/rag_dataset.py` if needed. Acceptance: loadable docs/cases; inactive seeding contract documented.
2. **Metrics worker** — `evals/retrieval_quality.py`, `evals/answer_quality.py`, shared metric helpers; preserve existing JSON fields. Acceptance: metric unit tests RED then GREEN.
3. **Runner/benchmark worker** — `evals/contextwiki_eval.py` extensions, `scripts/run_retrieval_benchmark.py`, optional report module wiring for lexical path.
4. **Report/CI/docs worker** — `evals/reporting.py`, `scripts/run_contextwiki_eval.py`, `.github/workflows/ci.yml`, `evals/README.md`, `docs/evaluation.md`, architecture touch if needed.
5. **Test worker** — `tests/evals/**`, `tests/scripts/**`, deterministic E2E coverage for runner artifacts.
6. **Main-agent integration** — synthesize, verify, smoke, three-reviewer loop, PR delivery.

Workers must not commit/push/open PRs, inspect secrets, or touch user Chroma/SQLite.

## Files likely to change

- `evals/datasets/rag_v1/**` (new)
- `evals/rag_dataset.py` (new)
- `evals/retrieval_quality.py`
- `evals/answer_quality.py`
- `evals/metrics.py` (new, shared formulas)
- `evals/reporting.py` (new)
- `evals/contextwiki_eval.py`
- `evals/README.md`
- `scripts/run_contextwiki_eval.py`
- `scripts/run_retrieval_benchmark.py` (new)
- `tests/evals/**`
- `tests/scripts/**`
- `.github/workflows/ci.yml`
- `docs/evaluation.md` (new)
- `.agents/docs/architecture.md` (eval layer note if needed)
- this plan

## TDD RED evidence

- Status: completed (before production edits)
- Timestamp: 2026-07-30 local; recorded before any `evals/` / `scripts/` production modules existed
- Commands:
  - `uv run --locked pytest -q tests/evals/test_rag_dataset.py tests/evals/test_rag_metrics.py tests/evals/test_rag_reporting.py --tb=line`
  - `uv run --locked pytest -q tests/scripts/test_run_retrieval_benchmark.py::test_run_retrieval_benchmark_help_documents_live_budget_and_outputs --tb=line`
- Layers/tests: unit (`test_rag_dataset`, `test_rag_metrics`, `test_rag_reporting`); integration (`test_run_retrieval_benchmark` help); e2e file added (`test_rag_eval_pipeline`) pending first run
- Non-zero exit: collection ERROR / FAILED (exit non-zero)
- Expected failure signatures:
  - `ModuleNotFoundError: No module named 'evals.rag_dataset'`
  - `ModuleNotFoundError: No module named 'evals.metrics'`
  - `ModuleNotFoundError: No module named 'evals.reporting'`
  - `AssertionError: ... can't open file '.../scripts/run_retrieval_benchmark.py': [Errno 2] No such file or directory` (returncode 2)
- Missing-behavior explanation: RAG dataset loader, metrics module, reporting helper, and retrieval benchmark CLI do not exist yet
- Note: prior RED test subagent hit API limit; main agent wrote RED tests directly

## Functional smoke matrix

| Feature | Caller Surface | Data Mode | Expected Result | Action/Command | Result | Evidence | Skip Reason / Substitute |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ContextWiki eval CLI + Markdown | CLI | temp SQLite fixtures | passed + `rag_report.md` | `uv run --locked python scripts/run_contextwiki_eval.py --output-dir /tmp/cw-eval-smoke` | passed | passed=True; report exists | N/A |
| RAG benchmark offline | CLI | rag_v1 temp SQLite | lexical executed; vector/hybrid skipped; citation_precision < 1.0 when distractors rank | `uv run --locked python scripts/run_retrieval_benchmark.py --split test --output-dir /tmp/rag-eval-smoke` | passed | Hit@5=1.0 (8/8); citation_precision≈0.24; vector skipped | N/A |
| MCP list/sync/search/fetch | Functional E2E | fake/temp | unchanged contracts | `./scripts/verify_functional_e2e.sh` via verify_all | passed | 38 passed | N/A |
| Live embedding `--live` | CLI | would call provider | not run | not run | blocked/gated | requires credentials/budget approval | Offline lexical substitute passed |
| User Chroma/SQLite mutation | n/a | user data | never touched | not run | blocked/gated | policy | temp SQLite only |

## TDD GREEN evidence

- Focused: `uv run --locked pytest -q tests/evals tests/scripts/test_run_retrieval_benchmark.py tests/scripts/test_run_contextwiki_eval.py tests/scripts/test_verification_architecture.py tests/e2e/test_rag_eval_pipeline.py` → 48 passed
- Full suite: `./scripts/verify_all.sh` → exit 0
- Eval gate: included in verify_all (ContextWiki + RAG benchmark)

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Created `feature/rag-eval-pipeline` from updated `main`. | `0 0` vs `origin/main` |
| Plan + dataset design | completed | Analysis and `rag_v1` design recorded. | this document |
| Multitask ownership | completed | Main agent implemented after RED worker API-limit failure. | subagent error; main-agent continuation |
| TDD RED | completed | Missing modules/script failures recorded before production edits. | ModuleNotFoundError / missing script |
| Focused GREEN | completed | Dataset/metrics/reporting/benchmark/E2E tests green. | `48 passed` focused suite |
| Refactor | completed | Hard-neg forbidden labels softened; inactive injection retained. | lexical suite 12/12 |
| Full suite | completed | `./scripts/verify_all.sh` passed. | exit 0; functional E2E 38 passed |
| Eval gate | completed | ContextWiki + RAG benchmark in verify_all/CI. | artifacts under `artifacts/` |
| Functional smoke | completed | Eval CLIs + E2E inventory | matrix above |
| Middle review pass 1 | completed | Actionable findings fixed | budget gate; single retrieve; hard_neg labels; report tables; quality gate |
| Middle review pass 2 | completed | Findings fixed (metrics N/A, suite overwrite, hardneg ordering, corpus budget) | verify_all green |
| Middle review pass 3 | completed | Perf findings fixed; security clean | no_answer status; ContextWiki asyncio batch |
| Middle review pass 4 | completed | Actionable bugs findings fixed | citation = full ranked top-k; unique no-answer tokens across splits; evidence_status uses relevant_hits |
| Pass-4 regression lock | completed | Added citation-precision distractor unit test; no-answer split uniqueness already covered | `test_citation_precision_penalizes_distractors_in_cited_list` green |
| Middle review pass 5 | completed | Bugs: document_sort report gap; perf: quality-gate fail-open, crash artifacts, char-sized budget | security clean |
| Pass-5 bugs fix | completed | Include document_sort_suite failures in `_write_artifacts` | RED/GREEN documented above |
| Pass-5 perf fixes | completed | Fail-closed quality gate; error artifacts on crash; 1k-char live budget estimate | RED: 3 focused tests failed; GREEN after production edits |
| Pass-5/6 follow-up fixes | completed | stale min_result_count=0; E2E+CSV citation_precision; ContextWiki p95 latency | RED then GREEN on focused tests |
| Pass-7 follow-up fixes | completed | Redact crash artifacts via `safe_error_message`; integer unit budget compare | RED then GREEN |
| Middle review pass 9 | completed | All three lenses clean | bugs/security/perf: no actionable findings |
| Integration | completed | verify_all evidence current after last code change; branch restored to `feature/rag-eval-pipeline`; smoke matrix unchanged | verify_all exit 0; offline CLIs smoke |
| Final review pass 1 | completed | Bugs: enforce no_answer empty-results check | security/perf clean |
| Final bugs fix | completed | `no_answer_empty_results` in evaluate_search_payload + unit test | RED then GREEN |
| Final review pass 2 | completed | Bugs: default min_result_count=0 for no_answer | security/perf clean |
| Final min_result fix | completed | from_mapping defaults min_result_count=0 when no_answer | RED then GREEN |
| Final review pass 3 | completed | All three lenses clean | bugs/security/perf: no actionable findings |
| PR delivery | completed | Pushed `feature/rag-eval-pipeline`; opened main-base PR | https://github.com/eunaverse/MCPContentSearch/pull/90 |
