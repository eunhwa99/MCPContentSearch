# ContextWiki Local Evaluations

This directory contains deterministic evaluation scaffolding for ContextWiki
answer quality and grounding. The current checks evaluate already-produced
`answer_with_citations`-style payloads and do not call live APIs, embedding
providers, Chroma, SQLite, or LLMs.

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

Full LLM answer generation and LLM-as-judge grading are future work. Keep those
opt-in because generated answers may send retrieved source evidence to an
external model.
