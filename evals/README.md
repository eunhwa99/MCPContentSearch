# ContextWiki deterministic evaluations

This directory contains the retained, local-first regression fixtures for
retrieval and citation-answer behavior. The suite is designed to be repeatable
in CI and review environments without credentials, live APIs, user ChromaDB,
user SQLite data, or an LLM.

It is deliberately narrower than the functional E2E gate and must not be
presented as a production RAG benchmark.

## Run

Focused tests:

```bash
uv run --locked pytest -q tests/evals
```

Fixture runner:

```bash
uv run --locked python scripts/run_contextwiki_eval.py
```

Write deterministic reviewer artifacts:

```bash
uv run --locked python scripts/run_contextwiki_eval.py \
  --output-dir artifacts/contextwiki-evals
```

Add informational wall-clock timing:

```bash
uv run --locked python scripts/run_contextwiki_eval.py \
  --output-dir artifacts/contextwiki-evals \
  --include-latency
```

## What runs

The runner:

1. loads the retained fixture documents and labeled cases;
2. seeds temporary SQLite metadata;
3. installs a deterministic lexical stand-in for `VectorIndexRetriever`;
4. disables LLM query rewrite;
5. runs retrieval through the normal SQLite-gated search service;
6. runs the internal deterministic citation-answer helper; and
7. evaluates the payloads against explicit relevance, citation, status, and
   unsupported-output expectations.

No default user storage is opened. No live source, embedding, or LLM provider
is called. The fixtures seed only active document and chunk records. The runner
therefore executes the normal SQLite validation path but does not evaluate
inactive or tombstoned candidate suppression.

## Coverage

`evals.retrieval_quality` checks:

- minimum result count;
- expected top chunk and source;
- required and forbidden chunks;
- hit rate at each case's `top_k`;
- mean reciprocal rank;
- recall at `top_k`; and
- nDCG at `top_k`.

`evals.answer_quality` checks:

- expected `evidence_status`;
- required and forbidden answer terms;
- minimum citation count;
- required cited chunk ids;
- consistency between `used_chunks` and citations;
- secret-like output leakage;
- status accuracy;
- required-citation recall;
- citation coverage; and
- insufficient-status accuracy.

The retained cases include repository-specific, generic-behavior, code-format,
Markdown-format, Obsidian-format, mixed-language, negative retrieval, and
insufficient-evidence examples.

## Artifacts

When `--output-dir` is supplied, the runner writes stable JSON suite data and a
human-readable Markdown report. The canonical artifact list is:

- `summary.json`
- `retrieval_suite.json`
- `answer_suite.json`
- `portfolio_report.md`
- optional `runtime_metrics.json` with `--include-latency`

The JSON suite artifacts and Markdown report are deterministic for a fixed
fixture and code revision. Runtime timing is intentionally separate because
wall-clock values vary by machine and run.

## Interpretation

A case pass means all expectations declared for that fixture passed. Aggregate
ranking metrics use only cases with explicit positive relevance labels;
negative cases remain valuable regression checks but do not become artificial
perfect ranking observations. Answer metrics expose their denominators so
grounded and insufficient-evidence cases are not conflated.
Insufficient-status accuracy checks only whether the labeled insufficient case
returns the expected status; it does not independently score the quality of an
insufficient-answer explanation.

The current retained snapshot is 13/13 retrieval cases and 9/9 answer cases.
The 11 positively labeled retrieval cases have hit rate, MRR, recall, and nDCG
of 1.0000 at each case's `top_k`. Answer status accuracy is 9/9,
required-citation recall is 9/9 labeled chunk ids, citation coverage is 12/12
used chunks, and insufficient-status accuracy is 1/1. These fixtures are small
and repository-shaped. They do not test real embeddings, live LLM output,
inactive/tombstoned candidate suppression, provider failures, a large private
corpus, or production latency.

See [the maintained evaluation methodology](../docs/evaluation.md) for metric
definitions, limitations, and the proposed path to a larger benchmark.
