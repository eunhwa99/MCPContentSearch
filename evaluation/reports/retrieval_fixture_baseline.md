# Retrieval Evaluation Report

**TEST FIXTURE — NOT PRODUCT PERFORMANCE**

## Provenance

- Dataset: `retrieval_gold.example.jsonl`
- Label source: `deterministic_fixture`
- Dataset size: `13`
- Git/tree identifier: `commit=9a2d39cbcb6a28f90d47ff0b62e76000edec627c;head_tree=fdfe77e949bf3384382acf1bf7ab34d99d10a75b;worktree_tree=8f4e7602b4bb3c579d242634f77729f987fbdecb;state=dirty`
- Timestamp: `2026-08-03T07:34:36Z`
- Status: `measured`

## Input content digests

- `configuration_sha256`: `8053a8a703053abe57ed425509c65713ab733fe0dd87f12c8fc96a43c5820409`
- `corpus_sha256`: `0bff529524068586730a648b7bd957af6200cc8c8b545073c365037c2776857a`
- `dataset_sha256`: `d193117a73adb4d52c73d7f51145a6a04cc4c83f687213f58cafafd2b23b8715`

## Configuration

- `candidate_multiplier`: `3`
- `candidate_score_calibration_floor`: `0.01`
- `candidate_score_scale`: `16.0`
- `exact_duplicate_removal`: `True`
- `keyword_weight`: `0.7`
- `metadata_filtering`: `True`
- `name`: `career-evidence-production-analog-v1`
- `near_duplicate_removal`: `True`
- `near_duplicate_threshold`: `0.8`
- `notes`: `Selected from executed deterministic public-fixture experiments. No external provider or private data.`
- `production_mapping`: `Deterministic offline candidates prefilter source/experience taxonomy before the bounded candidate cap, then pass through ContextSearchService source scoping/reranking and EvidenceSearchService authoritative metadata revalidation, relevance filtering, exact/near deduplication, and refill (candidate multiplier 3; top_k 5 gives at most 15 candidates).`
- `proxy_limitation`: `Offline lexical and character-similarity candidate provider with deterministic score calibration only; does not reproduce embedding-provider vector scores.`
- `query_normalization`: `True`
- `retrieval_mode`: `production_analog`
- `service_execution`: `True`
- `status`: `measured_configuration`
- `top_k`: `5`

## Execution path

- `candidate_budget_per_query`: `15`
- `candidate_multiplier`: `3`
- `context_service`: `ContextSearchService`
- `evidence_service`: `EvidenceSearchService`
- `identity`: `context-search-service+evidence-search-service+deterministic-offline-candidate-provider:v1`
- `provider`: `deterministic_offline_candidate_provider`

## Metrics

- Recall@1: `0.9583333333333334`
- Recall@3: `1.0`
- Recall@5: `1.0`
- Document Recall@1: `0.9583333333333334`
- Document Recall@3: `1.0`
- Document Recall@5: `1.0`
- Precision@3: `0.3611111111111111`
- Precision@5: `0.21666666666666667`
- MRR: `1.0`
- nDCG@5: `0.9792758870925922`
- Duplicate-result rate: `0.0`
- Citation-validity rate: `1.0`
- Source-type filter accuracy: `1.0`
- Experience-type filter accuracy: `1.0`
- Empty-result accuracy: `1.0`
- Mean latency (ms): `0.671551078154992`
- p50 latency (ms): `0.5339579947758466`
- p95 latency (ms): `2.0080410031368956`
- exact_duplicate_count: `0`
- exact_duplicate_result_rate: `0.0`
- invalid_citations: `[]`
- latency_sample_count: `13`
- near_duplicate_count: `0`
- near_duplicate_result_rate: `0.0`
- total_count: `20`
- total_result_count: `20`
- valid_count: `20`

## Ingestion metrics

- `full_ingestion_latency_ms`: `None`
- `incremental_update_latency_ms`: `None`
- `metric_denominators`: `{"full_ingestion_latency_ms": 0, "incremental_update_latency_ms": 0, "parsing_success_rate": 0, "unchanged_document_skip_rate": 0, "unnecessary_reembedding_rate": 0}`
- `parsing_success_rate`: `None`
- `unchanged_document_skip_rate`: `None`
- `unnecessary_reembedding_rate`: `None`
- Note: No ingestion run was executed by this retrieval-only fixture; values remain undefined with zero denominators.

## Resource and API cost

- `estimated_cost_usd`: `0.0`
- `external_api_calls`: `0`

## Limitations

- Executes ContextSearchService and EvidenceSearchService with an offline lexical and character-similarity candidate provider; it does not execute the configured embedding provider or reproduce provider vector scores.
- Latency measures the in-process sanitized fixture only, not production I/O.

## Failed cases

- None
