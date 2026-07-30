# ContextWiki Local Evaluations

This directory contains deterministic evaluation scaffolding for ContextWiki
and the public Aurora Relay RAG evaluation dataset.

Inside this deterministic quality eval layer, current local-first check families
include:

- payload-level answer grounding checks
- fixture-based retrieval and answer evaluation over temporary local SQLite
  state
- public synthetic RAG dataset (`evals/datasets/rag_v1`) with train/dev/test
  splits
- standard ranking and citation metrics (Hit/MRR/Recall/nDCG, citation
  precision/recall, insufficient accuracy, stale/inactive block rate)
- offline retrieval benchmark runner comparing lexical baseline vs optional
  `--live` embedding modes

Within the broader repository verification architecture, this directory is the
deterministic quality eval layer. It is narrower than the full functional E2E
gate: it does not exercise live APIs, user Chroma data, user SQLite data, or
the manual live smoke path.

These checks do not call live APIs, user Chroma data, user SQLite data, or
LLMs unless an operator explicitly passes `--live` to the retrieval benchmark.

Run the focused eval tests with:

```bash
uv run pytest -q tests/evals
```

## ContextWiki fixture suite

The first evaluator, `evals.answer_quality`, checks local answer payloads for:

- expected `evidence_status`
- required answer terms
- forbidden unsupported claims
- minimum citation count
- required cited chunk ids
- consistency between `used_chunks` and citation payloads
- obvious secret-like output leakage

`evals.retrieval_quality` checks retrieval ranking expectations such as:

- expected top chunk ids
- expected source ids
- required chunk presence
- forbidden chunk absence
- additive `quality_metrics` with numerator/denominator and N/A handling

Run the ContextWiki fixture runner with:

```bash
uv run --locked python scripts/run_contextwiki_eval.py
uv run --locked python scripts/run_contextwiki_eval.py --output-dir artifacts/contextwiki-evals
```

## Aurora Relay RAG dataset and benchmark

Public synthetic dataset card: `evals/datasets/rag_v1/README.md`.

Policy:

- Use **test** labels only for final scoring and CI. Do not tune retrieval logic
  against the test split.
- Fixture lexical scores are regression evidence, not production embedding
  performance.
- Live embedding/vector/hybrid modes require explicit `--live` and a positive
  `--max-budget` estimated spend ceiling.

```bash
uv run --locked python scripts/run_retrieval_benchmark.py \
  --split test \
  --output-dir artifacts/rag-evals
```

Artifacts:

- `benchmark_summary.json`
- `benchmark_summary.csv`
- `benchmark_report.md` / `rag_report.md`

Unrun live providers are recorded as `skipped`/`not_run` with null metrics, never
as zero-quality scores.

See `docs/evaluation.md` for metric definitions and interpretation limits.
