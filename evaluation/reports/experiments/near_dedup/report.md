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

- `configuration_sha256`: `068b1713ece89e196bfbf3e11cee1bda3ac564a35adbdba0e115b2e7d8e1290b`
- `corpus_sha256`: `0bff529524068586730a648b7bd957af6200cc8c8b545073c365037c2776857a`
- `dataset_sha256`: `d193117a73adb4d52c73d7f51145a6a04cc4c83f687213f58cafafd2b23b8715`

## Configuration

- `candidate_multiplier`: `1`
- `exact_duplicate_removal`: `True`
- `metadata_filtering`: `False`
- `name`: `exact-plus-near-dedup-v1`
- `near_duplicate_removal`: `True`
- `near_duplicate_threshold`: `0.8`
- `query_normalization`: `False`
- `retrieval_mode`: `keyword`
- `top_k`: `5`

## Execution path

- `identity`: `dependency-free-retrieve-evidence:keyword:v1`
- `provider`: `deterministic_offline_retrieval_function`
- `retrieval_function`: `evaluation.retrieval.retrieve_evidence`

## Metrics

- Recall@1: `0.9583333333333334`
- Recall@3: `0.9583333333333334`
- Recall@5: `0.9583333333333334`
- Document Recall@1: `0.9583333333333334`
- Document Recall@3: `0.9583333333333334`
- Document Recall@5: `0.9583333333333334`
- Precision@3: `0.3333333333333333`
- Precision@5: `0.20000000000000004`
- MRR: `1.0`
- nDCG@5: `0.9447793310592014`
- Duplicate-result rate: `0.0`
- Citation-validity rate: `1.0`
- Source-type filter accuracy: `0.4838709677419355`
- Experience-type filter accuracy: `0.7096774193548387`
- Empty-result accuracy: `1.0`
- Mean latency (ms): `0.08769553768126151`
- p50 latency (ms): `0.08279200119432062`
- p95 latency (ms): `0.12570800026878715`
- exact_duplicate_count: `0`
- exact_duplicate_result_rate: `0.0`
- invalid_citations: `[]`
- latency_sample_count: `13`
- near_duplicate_count: `0`
- near_duplicate_result_rate: `0.0`
- total_count: `31`
- total_result_count: `31`
- valid_count: `31`

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

- None recorded

## Failed cases

- `q-001`: wrong_source_type
  - Returned chunk IDs: `resume-professional-reliability, behavioral-story-mentoring`
- `q-002`: wrong_source_type
  - Returned chunk IDs: `previous-resume-recovery, resume-professional-reliability, resume-professional-migration`
- `q-004`: wrong_source_type
  - Returned chunk IDs: `behavioral-story-alerts, previous-resume-recovery, resume-professional-reliability`
- `q-005`: wrong_source_type,wrong_experience_type
  - Returned chunk IDs: `resume-professional-migration, project-prototype-migration, previous-resume-recovery, resume-professional-reliability`
- `q-006`: wrong_source_type,wrong_experience_type
  - Returned chunk IDs: `project-tracing, github-readme-kafka, project-prototype-migration, career-note-learning-goals`
- `q-008`: expected_chunk_not_in_top_5,expected_document_not_in_top_5
  - Returned chunk IDs: `career-note-leadership`
- `q-010`: wrong_source_type,wrong_experience_type
  - Returned chunk IDs: `skills-inventory-coursework, resume-professional-migration`
- `q-011`: wrong_source_type,wrong_experience_type
  - Returned chunk IDs: `project-prototype-migration, resume-professional-migration`
- `q-012`: wrong_source_type,wrong_experience_type
  - Returned chunk IDs: `career-note-learning-goals, career-note-leadership, skills-inventory-coursework, resume-professional-migration, resume-professional-reliability`
- `q-013`: wrong_source_type,wrong_experience_type
  - Returned chunk IDs: `github-readme-kafka, project-tracing, project-prototype-migration`
