# Retrieval experiment summary

**TEST FIXTURE — NOT PRODUCT PERFORMANCE**

Checked proxy variants and selected actual-service path were refreshed at
`2026-08-03T07:34:36Z` with exact commit/head/worktree-tree provenance in every
checked report. All use 13
`deterministic_fixture` cases, sanitized 14-chunk corpus, zero external API
calls, and estimated API cost `$0.00`.

All eight checked reports use schema v2 and record canonical dataset, corpus,
and configuration SHA-256 digests plus an execution-path identity. The retained
v1 selected baseline correctly failed closed against a v2 current report with
`invalid_baseline_report_version`, `missing_baseline_input_digests`, and
`missing_baseline_execution_path_identity`. After replacing it, a separately
generated same-workload v2 current report passed comparison with no violations.

| Variant | Recall@5 | MRR | nDCG@5 | Duplicate rate | Source filter | Experience filter | Empty accuracy | Failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Keyword baseline | 0.9583 | 1.0000 | 0.9448 | 0.1842 | 0.4737 | 0.7632 | 1.0000 | 10 |
| Query normalization | 0.9583 | 1.0000 | 0.9448 | 0.2368 | 0.4737 | 0.7632 | 1.0000 | 10 |
| Metadata filters | 0.9583 | 1.0000 | 0.9379 | 0.2222 | 1.0000 | 1.0000 | 1.0000 | 1 |
| Exact dedup | 0.9583 | 1.0000 | 0.9448 | 0.1143 | 0.4857 | 0.7429 | 1.0000 | 10 |
| Exact + near dedup | 0.9583 | 1.0000 | 0.9448 | 0.0000 | 0.4839 | 0.7097 | 1.0000 | 10 |
| Hybrid RRF experiment | 1.0000 | 1.0000 | 0.9702 | 0.1385 | 0.3077 | 0.5692 | 0.9231 | 13 |
| Candidate tuning | 1.0000 | 1.0000 | 0.9702 | 0.1692 | 0.3077 | 0.5692 | 0.9231 | 13 |
| **Selected production analog** | **1.0000** | **1.0000** | **0.9793** | **0.0000** | **1.0000** | **1.0000** | **1.0000** | **0** |

Selected production analog routes deterministic offline candidates through
real `ContextSearchService` and `EvidenceSearchService`, including explicit
source/metadata filtering, relevance rejection, exact/near deduplication, and
refill. Relative to historical keyword proxy, Recall@5 increased by `0.0417`,
duplicate rate fell by `0.1842`, and both filter accuracies reached `1.0`.

## Latency evidence

Proxy variants time `direct_offline_fixture_scorer`; selected baseline times
`ContextSearchService` + `EvidenceSearchService`. Cross-path latency delta: `n/a`
— execution paths differ, so subtracting their p95 values would be
misleading. Proxy keyword p95 was `0.0961 ms`; selected service-path p95 was
`2.0080 ms`. Both remain standalone fixture measurements, not production SLOs.
Same-path service comparison (v2) recorded baseline p95 `2.0080 ms` and
current p95 `2.0191 ms` separately; comparison passed with zero violations.

Selected service path also measured Document Recall@5 `1.0` across 12 scorable
cases. Direct proxy reports do not emit a document-recall measurement; no proxy
document metric is backfilled or invented.

RRF remains experiment-only. The production service has no explicit RRF mode,
and the isolated RRF run produced a no-answer false positive. The selected
offline candidate provider still uses lexical/character scores with declared
deterministic calibration; provider vectors and production I/O remain
unmeasured.
