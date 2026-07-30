# RAG evaluation methodology

## Purpose

MCPContentSearch evaluates document collection, retrieval ranking, citation
return, and stale/inactive blocking. It does **not** evaluate agent execution or
LLM spend as a product surface.

There are two deterministic evaluation families:

1. **ContextWiki fixture suite** (`scripts/run_contextwiki_eval.py`) — retained
   regression fixtures for search/answer contracts.
2. **Aurora Relay RAG suite** (`evals/datasets/rag_v1` +
   `scripts/run_retrieval_benchmark.py`) — public synthetic knowledge base with
   train/dev/test splits and standard ranking metrics.

Fixture and offline lexical results are **not** production embedding
performance. Live embedding rows appear only when `--live` is explicitly enabled
and are reported in a separate table.

## Dataset (`rag_v1`)

Synthetic project: Aurora Relay.

Document types:

- README, ADR, runbook, citation guide
- hard-negative distractors
- inactive/stale legacy webhook document
- mixed Korean/English documents

Each case labels:

- relevant document IDs and chunk IDs
- forbidden chunk IDs (absolute must-not-appear, typically inactive)
- `forbidden_inactive_chunk_ids` for stale/inactive block-rate scoring
- `hard_negative_chunk_ids` for distractors that may appear in the candidate
  list but must not outrank the labeled relevant chunk
- expected source
- `no_answer`
- evaluation `top_k`

Split policy: **test labels must not be used for retrieval tuning.**

## Metrics

| Metric | Numerator | Denominator | N/A when |
| --- | --- | --- | --- |
| Hit@K | cases with ≥1 relevant chunk in top K | scorable positive cases | no relevant labels / `no_answer` |
| MRR@K | sum of reciprocal rank of first relevant | scorable positive cases | same |
| Recall@K | sum of retrieved-relevant / labeled-relevant | scorable positive cases | same |
| nDCG@K | sum of binary DCG/IDCG | scorable positive cases | same |
| Citation precision | cited ∩ required / cited | cases with citations and required labels | no scorable citation cases |
| Citation recall | cited ∩ required / required | cases with required citation labels | none labeled |
| Insufficient accuracy | correct insufficient status | labeled insufficient cases | none labeled |
| Stale/inactive block rate | cases where inactive forbidden chunks are absent | cases with inactive forbidden labels | none labeled |

Unlabeled cases contribute **N/A** (`value: null`), not zero. Reports show
numerator, denominator, and scorable case counts.

## Benchmark runner

```bash
uv run --locked python scripts/run_retrieval_benchmark.py \
  --split test \
  --output-dir artifacts/rag-evals
```

- Default: offline lexical baseline only (no external API).
- `--live`: attempt vector/hybrid providers; failures are `provider_error`, not
  silent zero scores. Requires a positive `--max-budget`.
- `--max-budget`: mandatory estimated embedding spend ceiling for live runs;
  the estimate includes active corpus indexing units plus per-query units and
  aborts before provider calls when the estimate exceeds the cap.
- Outputs: JSON, CSV, Markdown. API keys are never written to artifacts.

## Limitations

- Lexical fixture retrieval is a deterministic stand-in, not Chroma production
  ranking.
- Relevance labels are incomplete by design; metrics measure labeled contracts.
- Hard-negatives may still appear in the candidate list; pass/fail focuses on
  correct top evidence and inactive blocking.
- Do not interpret CI fixture scores as customer/production search quality.
