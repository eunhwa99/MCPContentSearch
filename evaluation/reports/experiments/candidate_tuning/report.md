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

- `configuration_sha256`: `b1add7d050f4b9e6ac8562669a2cca2a92bcbb0e10d978e42094e6a6e1c5bac5`
- `corpus_sha256`: `0bff529524068586730a648b7bd957af6200cc8c8b545073c365037c2776857a`
- `dataset_sha256`: `d193117a73adb4d52c73d7f51145a6a04cc4c83f687213f58cafafd2b23b8715`

## Configuration

- `candidate_multiplier`: `3`
- `exact_duplicate_removal`: `False`
- `keyword_weight`: `0.7`
- `metadata_filtering`: `False`
- `name`: `production-analog-candidate-tuning-v1`
- `near_duplicate_removal`: `False`
- `near_duplicate_threshold`: `0.8`
- `query_normalization`: `True`
- `retrieval_mode`: `production_analog`
- `top_k`: `5`

## Execution path

- `identity`: `dependency-free-retrieve-evidence:production_analog:v1`
- `provider`: `deterministic_offline_retrieval_function`
- `retrieval_function`: `evaluation.retrieval.retrieve_evidence`

## Metrics

- Recall@1: `0.9583333333333334`
- Recall@3: `0.9583333333333334`
- Recall@5: `1.0`
- Document Recall@1: `0.9583333333333334`
- Document Recall@3: `0.9583333333333334`
- Document Recall@5: `1.0`
- Precision@3: `0.3333333333333333`
- Precision@5: `0.21666666666666667`
- MRR: `1.0`
- nDCG@5: `0.9701554117170889`
- Duplicate-result rate: `0.16923076923076924`
- Citation-validity rate: `1.0`
- Source-type filter accuracy: `0.3076923076923077`
- Experience-type filter accuracy: `0.5692307692307692`
- Empty-result accuracy: `0.9230769230769231`
- Mean latency (ms): `0.34158969244274956`
- p50 latency (ms): `0.33720799547154456`
- p95 latency (ms): `0.41649999911896884`
- exact_duplicate_count: `5`
- exact_duplicate_result_rate: `0.07692307692307693`
- invalid_citations: `[]`
- latency_sample_count: `13`
- near_duplicate_count: `6`
- near_duplicate_result_rate: `0.09230769230769231`
- total_count: `65`
- total_result_count: `65`
- valid_count: `65`

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

- Offline lexical and character-similarity proxy; it does not execute ContextSearchService, EvidenceSearchService, or the configured embedding provider.
- Latency measures the in-process sanitized fixture only, not production I/O.

## Failed cases

- `q-001`: wrong_source_type
  - Returned chunk IDs: `resume-professional-reliability, resume-professional-reliability-copy, resume-professional-reliability-near-copy, behavioral-story-mentoring, behavioral-story-alerts`
- `q-002`: wrong_source_type
  - Returned chunk IDs: `previous-resume-recovery, resume-professional-reliability-near-copy, resume-professional-reliability, resume-professional-reliability-copy, resume-professional-migration`
- `q-003`: wrong_source_type,wrong_experience_type
  - Returned chunk IDs: `github-readme-kafka, project-tracing, behavioral-story-mentoring, resume-certifications, skills-inventory-coursework`
- `q-004`: wrong_source_type
  - Returned chunk IDs: `behavioral-story-alerts, resume-professional-reliability, resume-professional-reliability-copy, resume-professional-reliability-near-copy, previous-resume-recovery`
- `q-005`: wrong_source_type,wrong_experience_type
  - Returned chunk IDs: `resume-professional-migration, project-prototype-migration, previous-resume-recovery, resume-professional-reliability, resume-professional-reliability-copy`
- `q-006`: wrong_source_type,wrong_experience_type
  - Returned chunk IDs: `project-tracing, github-readme-kafka, career-note-learning-goals, project-prototype-migration, skills-inventory-coursework`
- `q-007`: wrong_source_type,wrong_experience_type
  - Returned chunk IDs: `resume-certifications, resume-professional-migration, behavioral-story-alerts, project-prototype-migration, career-note-leadership`
- `q-008`: wrong_source_type
  - Returned chunk IDs: `career-note-leadership, resume-professional-reliability, resume-professional-reliability-copy, resume-professional-reliability-near-copy, behavioral-story-mentoring`
- `q-009`: empty_result_false_positive,wrong_source_type,wrong_experience_type
  - Returned chunk IDs: `project-prototype-migration, behavioral-story-alerts, behavioral-story-mentoring, project-tracing, resume-professional-reliability`
- `q-010`: wrong_source_type,wrong_experience_type
  - Returned chunk IDs: `skills-inventory-coursework, career-note-learning-goals, resume-professional-migration, resume-certifications, behavioral-story-alerts`
- `q-011`: wrong_source_type,wrong_experience_type
  - Returned chunk IDs: `project-prototype-migration, resume-professional-migration, behavioral-story-mentoring, behavioral-story-alerts, resume-certifications`
- `q-012`: wrong_source_type,wrong_experience_type
  - Returned chunk IDs: `career-note-learning-goals, career-note-leadership, resume-professional-reliability, resume-professional-reliability-copy, resume-professional-reliability-near-copy`
- `q-013`: wrong_source_type,wrong_experience_type
  - Returned chunk IDs: `github-readme-kafka, project-tracing, project-prototype-migration, resume-professional-migration, career-note-learning-goals`
