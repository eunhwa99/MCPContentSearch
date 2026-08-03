# Retrieval evaluation reports

All checked-in results use the sanitized deterministic fixture and state
`TEST FIXTURE — NOT PRODUCT PERFORMANCE`. They contain no private career data
and made zero external API calls.

- `experiments/baseline_keyword/`: pre-optimization keyword baseline.
- `experiments/query_normalization/`: normalization only.
- `experiments/metadata_filters/`: source/experience filters only.
- `experiments/exact_dedup/`: exact duplicate removal only.
- `experiments/near_dedup/`: exact plus near duplicate removal.
- `experiments/hybrid_rrf/`: offline RRF experiment only; not the production
  default because the production service has no explicit RRF mode.
- `experiments/candidate_tuning/`: expanded production-analog candidate pool.
- `retrieval_fixture_baseline.{json,md}`: selected production analog and CI
  regression baseline.

The production analog maps query normalization/candidate behavior to the
existing `ContextSearchService` path and maps metadata filtering, exact/near
deduplication, and refill to `EvidenceSearchService`. Its similarity score is
still a local lexical/character proxy. It does not reproduce configured
provider embeddings or production vector scores. Its latency is fixture-only.
Selected config sets production `candidate_multiplier=3`; with fixture
`top_k=5`, the deterministic provider prefilters source/experience taxonomy
before proposing at most 15 candidates. `ContextSearchService` and
`EvidenceSearchService` revalidate authoritative filters and retain the hard
global cap before relevance filtering/dedup/refill.
Proxy variants time the direct offline fixture scorer. Selected baseline times
the `ContextSearchService` + `EvidenceSearchService` path. These execution paths
are not latency-comparable; cross-path latency delta is `n/a`. Selected service
timing remains fixture-only, and CI comparisons use that same service path.
Checked historical service-path report stores its timestamp and fixture-only
p95. The checked comparison stores same-path baseline/current values separately.
Each verification run generates its own current runtime value in `artifacts/`;
documentation does not hardcode a transient run as “latest.”
Checked reports store exact
`commit=<40-hex>;head_tree=<40-hex>;worktree_tree=<40-hex>;state=clean|dirty`
provenance. The deterministic worktree tree covers tracked and non-ignored
untracked implementation inputs using an isolated temporary Git index/object
directory. Generated report outputs are restored to HEAD for hashing, while
this `README.md` and `ci_thresholds.json` remain inputs. The script does not
stage real files or write repository objects. Mutable `docs/plan/` harness
progress is likewise restored to HEAD; maintained architecture/integration
docs, code, configs, datasets, workflows, and tests remain fingerprinted.
Repeated report generation and plan bookkeeping thus retain one implementation
identifier instead of hashing metadata output.

## CI thresholds

- Recall@5 and MRR may drop by at most 0.05. This catches material ranking
  regressions while avoiding equality checks on future fixture changes.
- Document Recall@5 must remain 1.0. Document-only labels and chunk labels with
  expected documents affect both metrics and failed-case analysis.
- Citation validity must remain 1.0 because every fixture result must resolve
  to its stored chunk, document, exact quote, and section metadata.
- Duplicate-result rate must remain 0.0 because the fixture deliberately
  includes exact and near duplicates.
- Empty-result accuracy must remain at least 0.95 to catch false evidence for
  no-answer queries while allowing a larger future fixture one miss per 20.
- Source-type and experience-type filter accuracy must remain 1.0 because
  violating explicit filters is a contract error.
- Fixture p95 latency must remain below 1000 ms. This is a generous deterministic
  ceiling for a tiny in-process corpus, not a production latency SLO.

The comparison fails closed before metric comparison on unknown threshold
metrics, unsupported rule keys, empty/invalid rule shapes, non-finite values,
negative `max_drop`, or inverted `min`/`max`. It also fails on missing metrics,
missing max-drop baselines, dataset mismatch, or exceeded valid thresholds.
Report schema v2 records SHA-256 digests for the exact descriptor-held dataset,
corpus, and configuration snapshots plus a versioned execution-path identity.
Comparison also fails closed when either report lacks that identity or when any
digest or execution-path field differs. Older v1 baselines must be regenerated
with the reviewed runner before they can serve as CI comparison baselines.
