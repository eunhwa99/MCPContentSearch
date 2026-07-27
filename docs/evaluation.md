# ContextWiki evaluation methodology

## Purpose

ContextWiki uses deterministic evaluations to catch changes in retrieval
ranking, citation selection, grounded answer status, and insufficient-status
behavior. The goal is repeatable engineering evidence, not a claim of
production model quality.

The current snapshot contains:

- 13 retrieval cases at `top_k=3`;
- 9 citation-answer cases;
- repository-specific, generic, code, Markdown, Obsidian, and mixed-language
  groups;
- 2 negative retrieval cases with forbidden-result expectations; and
- 1 insufficient-evidence answer case.

All current cases pass on the retained deterministic fixture stack.

## Execution boundary

The evaluation runner uses production search and answer service boundaries
where practical, but replaces non-deterministic or private dependencies:

```text
fixture documents
-> temporary SQLite lifecycle data
-> deterministic lexical stand-in for VectorIndexRetriever
-> ContextSearchService with active fixture records
-> CitationAnswerService
-> labeled retrieval and answer evaluators
```

The run does not call Notion, Tistory, GitHub, OpenAI, or another live provider.
It does not open the default ChromaDB or SQLite paths, and it does not inspect
user content. Query rewrite is disabled.

All fixture documents and chunks are seeded as active. The runner traverses the
normal SQLite validation path, but this fixture set does not test rejection of
inactive or tombstoned vector candidates.

Run it with:

```bash
uv run --locked python scripts/run_contextwiki_eval.py \
  --output-dir artifacts/contextwiki-evals
```

The generated `portfolio_report.md` is the reviewer-facing summary.
`summary.json`, `retrieval_suite.json`, and `answer_suite.json` retain the
machine-readable evidence. `runtime_metrics.json` is written only when
`--include-latency` is requested and is not deterministic evidence.

## Retrieval labels and metrics

Each retrieval case declares some combination of:

- `expected_top_chunk_id`;
- `required_chunk_ids`;
- `forbidden_chunk_ids`;
- `expected_source_id`;
- `min_result_count`; and
- `top_k`.

The suite-level ranking metrics are:

| Metric | Definition | Denominator |
| --- | --- | --- |
| Hit rate at `k` | fraction of scorable positive cases with at least one relevant chunk in the first `k` results | cases with one or more explicit positive relevant chunk ids |
| MRR | mean of `1 / rank` for the first relevant result, or 0 when no relevant result appears | positive scorable cases |
| Recall at `k` | mean fraction of each case's relevant chunk ids found in the first `k` results | positive scorable cases |
| nDCG at `k` | mean binary-relevance discounted cumulative gain normalized by the ideal ranking | positive scorable cases |

The current relevance set is derived from the explicit expected top chunk and
required chunks. These are fixture labels, not exhaustive relevance judgments
over a real corpus.

Negative cases can assert zero expected results or forbid known collision
chunks. They contribute to case pass/fail but are excluded from positive
ranking denominators. This prevents an empty result from being counted as a
perfect ranking observation.

## Answer labels and metrics

Each answer case can declare:

- expected evidence status (`grounded` or `insufficient`);
- required and forbidden answer terms;
- minimum citation count; and
- required cited chunk ids.

The generated report summarizes:

| Metric | Definition | Denominator |
| --- | --- | --- |
| Status accuracy | answers whose evidence status matches the label | all answer cases |
| Required-citation recall | required chunk citations returned divided by required chunk citations labeled | labeled required chunk ids |
| Citation coverage | used chunk ids that have a matching citation divided by all used chunk ids | used chunk ids |
| Insufficient-status accuracy | insufficient cases whose returned status is `insufficient` | labeled insufficient cases |

Case pass/fail also checks required and forbidden terms, minimum citations,
used-chunk/citation consistency, and obvious secret-like output.
Insufficient-status accuracy itself measures only the returned status; it does
not grade the prose quality of an insufficient-answer explanation.

## Current result and limitations

The retained snapshot is:

| Suite | Result |
| --- | ---: |
| Retrieval cases | 13/13 passed |
| Answer cases | 9/9 passed |
| Hit rate / MRR / recall / nDCG | 1.0000 / 1.0000 / 1.0000 / 1.0000 on 11 positive cases |
| Status accuracy | 1.0000 (9/9) |
| Required-citation recall | 1.0000 (9/9 required chunk ids across 8 cases) |
| Citation coverage | 1.0000 (12/12 used chunks across 8 cases) |
| Insufficient-status accuracy | 1.0000 (1/1 insufficient case) |

The report generated from the current revision is the canonical source for
ranking and grounding metric values because it keeps each value next to its
scorable denominator.

These results mean the retained fixtures behave as expected. They do **not**
establish:

- production retrieval quality;
- exhaustive relevance over a representative private corpus;
- live embedding or LLM quality;
- inactive or tombstoned candidate suppression;
- resistance to prompt injection or adversarial documents;
- external provider availability, cost, or latency;
- performance at large corpus sizes; or
- universal compatibility with every MCP client.

## Next benchmark steps

The strongest next evaluation expansion would:

1. create separate development and held-out sets with 100–300 queries;
2. include Korean, English, mixed-language, ambiguous, stale-document, and
   unanswerable questions;
3. record multiple relevance judgments per query;
4. compare vector-only, hybrid/reranked, and SQLite-gated variants;
5. measure Recall@5, MRR, nDCG@10, citation precision/recall, and
   insufficient-evidence detection accuracy;
6. report p50/p95 latency, indexing throughput, and provider cost separately;
   and
7. run an explicitly approved live-provider evaluation without mixing its
   nondeterministic results into the retained fixture baseline.
