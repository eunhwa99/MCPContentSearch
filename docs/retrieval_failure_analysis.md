# Retrieval failure analysis

**TEST FIXTURE — NOT PRODUCT PERFORMANCE**

This analysis uses only failures and metrics saved by measured synthetic
fixture runs. Checked proxy and actual-service reports store exact
commit/head/worktree-tree provenance and measurement timestamps. Dataset label
source is `deterministic_fixture`; all quoted text below is synthetic. Every run
made zero external API calls with estimated API cost `$0.00`.

Evidence:

- [keyword baseline](../evaluation/reports/experiments/baseline_keyword/report.json)
- [metadata-filter experiment](../evaluation/reports/experiments/metadata_filters/report.json)
- [exact-dedup experiment](../evaluation/reports/experiments/exact_dedup/report.json)
- [exact-plus-near-dedup experiment](../evaluation/reports/experiments/near_dedup/report.json)
- [hybrid RRF experiment](../evaluation/reports/experiments/hybrid_rrf/report.json)
- [selected production analog](../evaluation/reports/retrieval_fixture_baseline.json)

Only observed categories are included. The saved reports did not show stale
document-version, chunk-size, parsing, or citation failures, so none are
invented here.

## 1. Wrong source and experience type

- Query: `q-005` — “professional database migration experience”
- Expected: top five contains `resume-professional-migration`; every result is
  `source_type=resume` and `experience_type=professional`.
- Actual keyword-baseline behavior: the expected resume chunk ranked first,
  but incompatible results were also returned.

| Returned chunk | Source / experience | Synthetic exact quote |
| --- | --- | --- |
| `resume-professional-migration` | `resume` / `professional` | “Led a staged professional database migration with rollback verification.” |
| `project-prototype-migration` | `project` / `prototype` | “Built a prototype schema migration tool against generated test databases.” |
| `previous-resume-recovery` | `previous_resume` / `professional` | “Cut recovery time after service failures by standardizing rollback checks.” |

The saved baseline classified nine cases as `wrong_source_type` and six as
`wrong_experience_type`. The root-cause hypothesis is that lexical matching
ranked shared words such as “migration” without enforcing request metadata.
The returned `project/prototype` and `previous_resume` rows directly support
that hypothesis.

Fix attempted: apply source- and experience-type filtering before candidate
selection, then retain that filtering in the selected production analog.

| Metric | Keyword baseline | Selected production analog |
| --- | ---: | ---: |
| Source-type filter accuracy | 0.473684 | 1.000000 |
| Experience-type filter accuracy | 0.763158 | 1.000000 |
| Recall@5 | 0.958333 | 1.000000 |
| Failed cases | 10 | 0 |

Latency delta is `n/a`: keyword baseline used direct offline fixture scorer,
while selected run routed candidates through actual context/evidence services.
Their standalone p95 values remain in the checked reports and are not
subtracted. Same-path regression comparison recorded baseline/current p95
separately and passed with zero violations. API cost stayed `$0.00`. Remaining
limitation: fixture does
not measure provider-backed vector retrieval, production I/O, or a larger and
more varied metadata distribution.

## 2. Exact and near-duplicate results

- Query: `q-001` — “Kubernetes readiness probe incident reduction”
- Expected: one unique primary evidence chunk,
  `resume-professional-reliability`.
- Actual keyword-baseline behavior: three versions of the same evidence filled
  the first three ranks.

| Returned chunk | Synthetic exact quote |
| --- | --- |
| `resume-professional-reliability` | “Improved Kubernetes readiness probes and reduced incident recovery time for a synthetic service.” |
| `resume-professional-reliability-copy` | “Improved Kubernetes readiness probes and reduced incident recovery time for a synthetic service.” |
| `resume-professional-reliability-near-copy` | “Improved Kubernetes readiness probes and reduced incident recovery time for the synthetic service.” |

The baseline measured four exact and three near duplicates among 38 returned
results: exact rate `0.105263`, near rate `0.078947`, combined rate `0.184211`.
Different chunk/document IDs prevented identity-only deduplication, while the
quotes show content equality or near equality.

Fix attempted in two measured steps: exact normalized-quote deduplication,
then token-Jaccard near-duplicate removal at threshold `0.8`. The selected
production analog keeps both.

| Metric | Baseline | Exact only | Exact + near | Selected |
| --- | ---: | ---: | ---: | ---: |
| Exact duplicate rate | 0.105263 | 0.000000 | 0.000000 | 0.000000 |
| Near duplicate rate | 0.078947 | 0.114286 | 0.000000 | 0.000000 |
| Combined duplicate rate | 0.184211 | 0.114286 | 0.000000 | 0.000000 |

The near rate rose after exact-only removal because the denominator and retained
result set changed; its count was still four. Adding near dedup reduced both
duplicate counts to zero. API cost stayed `$0.00`. Remaining limitation: the
`0.8` threshold is proven only on the synthetic duplicate pair; false-positive
deduplication on longer or domain-specific career evidence remains unmeasured.
Proxy-path p95 values for baseline, exact-only, and exact-plus-near runs remain
in their checked reports. Selected service-path timing is a different path;
cross-path latency delta is `n/a`.

## 3. Second relevant item missed from top five

- Query: `q-008` — “leadership evidence”
- Expected: both `career-note-leadership` and
  `behavioral-story-mentoring` in the top five.
- Actual metadata-filter behavior: only `career-note-leadership` was returned,
  with quote “Recorded leadership goals for clearer technical decisions and
  team support.” `behavioral-story-mentoring` was recorded in
  `missing_chunk_ids`.

The same miss exists in the keyword baseline. The root-cause hypothesis is a
lexical vocabulary gap: the missing synthetic evidence says “Mentored two
synthetic teammates through an incident-review practice” and does not contain
the query term “leadership.” Metadata filtering correctly constrained types
but could not create semantic recall.

Fix attempted: retain filters while adding the production-analog normalized
query/candidate similarity and refill behavior. The selected report has no
failed cases and a complete Recall@5 denominator of 12 scorable non-empty
queries.

| Metric | Metadata filters only | Selected production analog |
| --- | ---: | ---: |
| Recall@5 | 0.958333 | 1.000000 |
| nDCG@5 | 0.937889 | 0.979276 |
| Failed cases | 1 | 0 |

Cross-path latency delta is `n/a`; both standalone p95 values remain in their
checked reports. API cost stayed `$0.00`. Remaining limitation: offline
character/lexical similarity is only a proxy and does not
establish semantic recall for configured production embedding provider.

## 4. No-answer false positive in the RRF experiment

- Query: `q-009` — “COBOL mainframe production deployment”
- Expected: an empty list because the synthetic corpus has no supporting
  evidence.
- Actual hybrid-RRF behavior: five unrelated chunks were returned, including:

| Returned chunk | Synthetic exact quote |
| --- | --- |
| `project-prototype-migration` | “Built a prototype schema migration tool against generated test databases.” |
| `behavioral-story-alerts` | “Consolidated duplicate alerts and reduced alert noise by 35 percent.” |
| `project-tracing` | “Implemented distributed tracing in a personal queue-processing project.” |

The report records `empty_result_false_positive`, `wrong_source_type`, and
`wrong_experience_type`. Empty-result accuracy was `0.923077` (12/13 cases).
The root-cause hypothesis is that RRF fused weak positive character/keyword
ranks without a relevance cutoff or compatible metadata gate. The very small
returned scores (`0.016393`, `0.016129`, and `0.015625` for the rows above)
support the weak-match explanation.

Fix attempted: do not select RRF as default; use production analog with
explicit metadata filtering and relevance rejection. Case now allows
`career_note/unknown`, and two indexed synthetic chunks match those metadata
filters but contain no COBOL/mainframe evidence. Their weak calibrated scores
remain below evidence relevance threshold, so selected actual-service path
returns empty for relevance—not because filter combination is impossible.

| Metric | Hybrid RRF experiment | Selected production analog |
| --- | ---: | ---: |
| Empty-result accuracy | 0.923077 | 1.000000 |
| Source-type filter accuracy | 0.307692 | 1.000000 |
| Experience-type filter accuracy | 0.569231 | 1.000000 |
| Failed cases | 13 | 0 |

Selected configuration improved correctness; API cost remained `$0.00`.
Standalone RRF proxy and selected service-path p95 values remain in their
checked reports; cross-path latency delta is `n/a`. RRF remains an experiment only.
Remaining limitation: no-answer calibration against a provider-backed index or
real private career corpus was run, so the public fixture result cannot be
presented as product performance.
