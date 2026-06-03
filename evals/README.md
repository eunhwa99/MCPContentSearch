# ContextWiki Local Evaluations

This directory contains deterministic evaluation scaffolding for ContextWiki.
Phase D1 now covers two local-first layers:

- payload-level answer grounding checks
- fixture-based retrieval and answer evaluation over temporary local SQLite
  state

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

Run the D1 fixture runner with:

```bash
PYTHONPATH=. python scripts/run_contextwiki_eval.py
```

This seeds temporary fixture documents into temp SQLite, swaps in a local
fixture `VectorIndexRetriever`, executes the normal `search_context` indexer
path plus `answer_with_citations`, and returns a JSON summary without live LLM
rewrite.

Phase split used by the roadmap:

- `D1`: local retrieval/answer eval foundation
- `D2`: observability expansion
- `J1`: deterministic non-LLM retrieval quality
- `J2`: LLM-assisted answer quality
