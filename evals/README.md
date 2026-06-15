# ContextWiki Local Evaluations

This directory contains deterministic evaluation scaffolding for ContextWiki.
Inside this deterministic quality eval layer, Phase D currently covers two
local-first check families:

- payload-level answer grounding checks
- fixture-based retrieval and answer evaluation over temporary local SQLite
  state

Within the broader repository verification architecture, this directory is the
deterministic quality eval layer. It is narrower than the full functional E2E
gate: it does not exercise live APIs, user Chroma data, user SQLite data, or
the manual live smoke path.

These checks do not call live APIs, user Chroma data, user SQLite data, or
LLMs.

Run the focused eval tests with:

```bash
uv run pytest -q tests/evals
```

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

The fixture suites now also report:

- group-level breakdowns for repo-specific, generic-behavior, code-format,
  markdown-format, obsidian-format, and mixed-language queries
- deterministic reviewer-visible JSON artifacts when the runner is given an
  output directory
- optional non-deterministic runtime latency summaries for retrieval and answer
  passes

Run the D1 fixture runner with:

```bash
uv run --locked python scripts/run_contextwiki_eval.py
```

Write reviewer-visible artifacts with:

```bash
uv run --locked python scripts/run_contextwiki_eval.py --output-dir artifacts/contextwiki-evals
uv run --locked python scripts/run_contextwiki_eval.py --output-dir artifacts/contextwiki-evals --include-latency
```

This seeds temporary fixture documents into temp SQLite, swaps in a local
fixture `VectorIndexRetriever`, executes retained retrieval plus answer-eval
coverage without the normal live indexing/vector setup, and returns a JSON
summary without live LLM rewrite.
When `--output-dir` is supplied, the runner writes:

- `summary.json`
- `retrieval_suite.json`
- `answer_suite.json`
- optional `runtime_metrics.json` when `--include-latency` is supplied

The first three files are deterministic CI reviewer evidence for Issue `#32`.
`runtime_metrics.json` is informational and may vary across runs because it
captures wall-clock timing.

Phase split used by the roadmap:

- `D1`: local retrieval/answer eval foundation
- `D2`: mixed-query metrics, latency summaries, and reviewer-visible artifacts
- `J1`: deterministic non-LLM retrieval quality
- `J2`: LLM-assisted answer quality
