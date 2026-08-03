# Career Evidence Retrieval Service

## User request

Turn MCPContentSearch into a reusable, measured evidence-retrieval service for
a separate Application OS. Add local career-document ingestion for PDF, DOCX,
Markdown, and text; safe incremental indexing; the additive `search_evidence`
MCP contract; deterministic evaluation, regression gates, observability, and
integration documentation. Keep Application OS logic out of this repository.

## Branch preflight result

- Starting worktree: clean `main` at `9a2d39c`, equal to `origin/main`
  (`git rev-list --left-right --count HEAD...origin/main` -> `0 0`).
- Freshness: `git fetch origin main` and `git pull --ff-only origin main`
  succeeded on 2026-08-03.
- Task branch: `feature/career-evidence-retrieval`.
- No local-only or linked non-main branches required cleanup.
- User Chroma/SQLite data and secrets have not been inspected or mutated.

## Scope and non-goals

### In scope

- Explicit-manifest local career source with safe PDF, DOCX, Markdown, and text
  parsing; typed parsing failures; section-aware normalized metadata.
- Separate career evidence source taxonomy and the fixed experience taxonomy;
  existing connector `SourceType` remains unchanged.
- Additive SQLite schema evolution for document/chunk evidence metadata and
  document-version identity; current SQLite active/tombstone gate remains
  authoritative.
- Incremental indexing counters for parsed/skipped/updated documents,
  created/updated/skipped chunks, generated/reused embeddings, parse failures,
  and latency. Existing unrelated-document and unchanged-document skip behavior
  remains.
- Additive `search_evidence` MCP tool returning stored quotes only, with
  validated filters, deterministic exact/near deduplication, stable IDs,
  scores, and sanitized typed errors.
- Deterministic public retrieval dataset, metrics, JSON/Markdown reports,
  baseline comparison, failure analysis, CI thresholds, and an optional manual
  evaluation workflow that never uploads private text.
- Application OS integration documentation matching executed behavior.

### Non-goals

- Job-description parsing, fit scoring, resume advice or generation, outreach,
  job tracking, or any Application OS UI/code.
- LLM generation/paraphrasing of evidence.
- Live provider evaluation, live source sync, real-vault mutation, or paid API
  calls. Deterministic temp/fake substitutes are used.
- Neo4j, vector-store replacement, or an unmeasured reranker.
- Inferring `professional` from project text. Missing experience metadata is
  `unknown`; manifest values are authoritative.
- Historical version retention beyond a stable `document_version_id` for the
  active version. Existing current-row lifecycle stays additive and compatible.

## Acceptance criteria

1. Explicit local manifest entries accept only PDF, DOCX, Markdown, and text,
   require a supported career source type, validate the fixed experience type,
   stay inside an approved manifest root, and produce typed parse errors.
2. Parsed/indexed chunks preserve all available required normalized fields,
   exact stored quote text, section hierarchy, career metadata, stable logical
   document identity, and deterministic version/chunk identity.
3. Unchanged retry produces no duplicate active documents/chunks and no new
   embeddings; one changed document does not reindex unrelated documents;
   replacement/deletion uses existing complete-snapshot/tombstone safety.
4. `search_evidence(query, source_types, experience_types, document_ids,
   top_k)` validates inputs, applies SQLite-authoritative filters, returns
   extractive structured evidence, removes exact and near duplicates, and
   returns `[]` for no relevant evidence.
5. Existing eight MCP tools and contracts stay compatible; tool inventory adds
   only `search_evidence`.
6. Public deterministic evaluation covers all requested query categories and
   declares `label_source=deterministic_fixture`; private local datasets and
   reports are ignored.
7. Reports contain configuration, dataset/label metadata, Git/tree identity,
   timestamp, requested retrieval/filter/citation/latency metrics, failures,
   and the mandatory fixture disclaimer.
8. CI comparison enforces reviewed tolerances for Recall@5, MRR, citation
   validity, duplicates, filters, empty results, p95 latency, and MCP contract.
9. Failure analysis contains only executed failed cases. If the selected final
   fixture strategy has no failures, it records the executed baseline/variant
   failures that were actually observed, without invented examples.
10. Focused unit, integration, E2E, full `./scripts/verify_all.sh`, post-suite
    eval, functional smoke, and fresh three-reviewer gates pass before delivery.

## Ordered steps and worker ownership

1. `models-storage-contract` — one test/contract worker owns model/schema RED
   coverage and then model/storage implementation handoff. Files:
   `core/models.py`, `core/exceptions.py`, `storage/metadata_store.py`, focused
   model/storage tests. No connector, retrieval, or docs edits.
2. `parsing-local-source` — after shared contracts settle, one worker owns
   parser, local connector, section-aware chunk metadata, dependencies, and
   focused parser/connector tests. It must not edit retrieval/eval/docs files.
3. `evidence-retrieval-eval` — sequential single owner for `search_evidence`,
   MCP contract tests, deterministic E2E, evaluation runner/metrics/baseline,
   and CI threshold behavior. Shared schema changes are consumed, not replaced.
4. `docs-integration` — after executed results exist, a docs worker owns README,
   maintained architecture, integration guide, failure analysis, and dataset
   guidance. It must not claim unexecuted or private results.
5. Main agent integrates worker outputs, resolves overlap, runs all gates,
   performs main-only safety checks, routes review findings, and delivers PR.

Workers share the task branch only with disjoint ownership. Workers do not
commit, push, open PRs, inspect secrets, inspect/mutate user Chroma/SQLite, or
revert other changes.

## Files likely to change

- Models/errors/storage: `core/models.py`, `core/exceptions.py`,
  `storage/metadata_store.py`.
- Parsing/source/indexing: new `parsing/`, new `fetching/career_files.py`,
  `fetching/connectors.py`, `indexing/chunker.py`, `indexing/converter.py`,
  `indexing/indexer.py`, `indexing/ingestion_service.py`, `app_runtime.py`,
  `environments/config.py`, `pyproject.toml`, `uv.lock`.
- Retrieval/MCP: new `search/evidence_service.py`, `api/tools.py`, `main.py`.
- Evaluation/CI: new `evaluation/`, `.gitignore`, `.github/workflows/ci.yml`,
  optional manual workflow, verification scripts, reviewed baseline reports.
- Tests: focused unit/integration/E2E/contract/eval/CI tests under `tests/`.
- Docs: `README.md`, `.agents/docs/architecture.md`,
  `docs/application_os_integration.md`,
  `docs/retrieval_failure_analysis.md`, and this plan.

## TDD RED evidence

- Status: completed before production edits.
- Parser/model unit RED at `2026-08-02T23:23:06Z`:
  `uv run --locked pytest -q
  tests/parsing/test_career_documents.py::test_career_taxonomies_are_fixed_and_separate_from_connector_type`
  exited `4` with expected signature
  `ImportError: cannot import name 'ParsingError' from 'core.exceptions'`.
- MCP inventory RED:
  `uv run --locked pytest -q
  tests/test_app_composition.py::test_create_app_registers_slim_mcp_tools_and_core_sources`
  exited `1` because `search_evidence` was the missing expected tool.
- Search unit/API/E2E collection RED:
  `uv run --locked pytest -q tests/search/test_evidence_service.py
  tests/api/test_search_evidence_contract.py tests/e2e/test_career_evidence_flow.py`
  exited `2` with expected missing symbols `EvidenceRetrievalError`,
  `EvidenceChunk`, and `search.evidence_service`.
- Evaluation unit RED at `2026-08-03T08:23:09+09:00`:
  `uv run --locked pytest -q
  tests/evaluation/test_metrics.py::test_retrieval_metrics_use_unique_relevance_and_explicit_denominators`
  exited `4` with expected signature
  `ModuleNotFoundError: No module named 'evaluation.metrics'`.
- DOCX hardening RED: the focused entity-expansion and compressed-small /
  decompressed-large DOCX tests exited `1`; both expected `ParsingError` but
  reported `DID NOT RAISE`. The implementation then moved to `defusedxml` and
  bounded decompressed `word/document.xml` reads.
- Storage migration regression RED: the first post-implementation
  `./scripts/verify_all.sh` exited `1` with `1 failed, 1457 passed` because
  `ensure_schema()` rewrote an unrelated current-schema legacy chunk during a
  scoped worker run. The exact failing test was
  `test_scoped_worker_recovers_stale_legacy_source_and_completes_retained_job`.
  A focused current-schema idempotency test also failed before the migration
  backfill was limited to newly added columns.
- Functional-gate inventory RED:
  `tests/scripts/test_verification_architecture.py::test_functional_gate_includes_career_evidence_mcp_flow`
  exited `1` because `tests/e2e/test_career_evidence_flow.py` was absent from
  `verify_functional_e2e.sh`.
- Middle-review remediation RED:
  `uv run --locked pytest -q --tb=short
  tests/search/test_evidence_service.py::test_search_evidence_restricts_candidates_to_career_source_before_cap
  tests/search/test_evidence_service.py::test_default_near_duplicate_threshold_matches_selected_measured_config
  tests/evaluation/test_runner_retrieval.py::test_selected_evaluation_executes_real_context_and_evidence_services
  tests/evaluation/test_dataset_fixture.py::test_no_answer_case_uses_filters_that_match_unrelated_indexed_chunks
  tests/evaluation/test_ci_wiring.py::test_optional_workflow_has_explicit_local_only_private_live_and_larger_modes`
  exited `1` with `5 failed`. Expected signatures were
  `['unrelated-0'] != ['career-target']`, `0.9 == 0.8`, actual service call
  count `0 == 13`, empty matching-unrelated fixture `assert []`, and missing
  workflow input `type: choice`.
- Service-path quality calibration RED:
  `uv run --locked pytest -q --tb=short
  tests/evaluation/test_runner_retrieval.py::test_selected_evaluation_executes_real_context_and_evidence_services`
  exited `1` with expected signature
  `assert 0.9583333333333334 == 1.0`; the first actual-service adapter passed
  weak deterministic proxy scores directly into the production relevance gate
  and lost the labeled leadership/mentoring result.
- Service-path report transparency RED:
  `uv run --locked pytest -q --tb=short
  tests/evaluation/test_runner_retrieval.py::test_runner_cli_executes_and_writes_json_and_markdown`
  exited `1` with expected signature
  `assert '## Execution path' in markdown`; JSON recorded the actual services,
  but rendered Markdown did not expose them.
- Fresh three-reviewer remediation REDs:
  - Retrieval/API/storage selector exited `1` with `16 failed, 2 passed`:
    FastMCP validation bypassed `[invalid_request]`, request limits were absent,
    provider limits were `[3, 6, 12, 24]` instead of one bounded `[96]` call,
    batch hydration count was `0`, and
    `get_active_evidence_snapshots` was missing. A strict-type follow-up also
    exited `1` because string/float/bool `top_k` values were coerced.
  - Parser hardening selectors exited `1`: intermediate symlink swap did not
    raise `ParsingError`; PDF page/character/UTF-8 limits were missing; delayed
    fetch added about `250 ms` to the mislabeled indexing timer. The all-format
    E2E selector initially exited `4` because the required test did not exist.
  - Evaluation/security selector exited `1` with `10 failures`: missing section
    metadata still counted as a valid citation, private outputs were `0644` and
    non-atomic/symlink-unsafe, workflow trust controls were absent, batch fixture
    hydration was missing, and cross-path latency was presented as comparable.
  - Poisoned career environment connector selector exited `1` because an
    unexpected `source_career` entered the registry.
- The first final full-suite attempt exited `1` with `1 failed, 1498 passed`:
    a new legacy-schema test leaked its raw SQLite fixture connection. The
    poisoned ordering reproduced `1 failed, 6 passed`; `contextlib.closing`
    fixed the fixture without warning suppression, then passed five repeats.
- The post-storage-hardening full-suite attempt exited `1` with `1 failed,
  1624 passed`: the provider-timeout cleanup test could expire before its child
  process wrote a PID file. The test now captures the successful `Popen` PID
  immediately, preserves the 50 ms timeout and process-reaping assertion, and
  passed 50 consecutive executions. Production timeout behavior was unchanged.
- Final-review remediation RED reproduced three defects: a configured but
  initially missing manifest could later enable against stores that skipped
  private preflight; intermediate symlink ancestors were accepted by storage
  path setup; and an unchanged document taxonomy update changed SQLite while
  leaving Chroma prefilter metadata stale. Focused transition/ancestry tests
  failed in four cases, while taxonomy integration/E2E failed with
  `processed=0`, `skipped=1`, and zero metadata updates. The fixes preflight any
  configured career source, descriptor-walk full ancestry with no-follow
  semantics, and update managed Chroma metadata in place while reusing stable
  embeddings.
- Final pass-2 remediation RED reproduced four more issues: automatic career
  IDs changed with taxonomy; metadata-only Chroma refresh was sequential and
  not compensating on partial/SQLite failure; 1,200 chunks caused 1,200 GETs
  plus 1,200 UPDATEs; and evaluation provenance accepted imprecise dirty-tree
  identifiers. Focused tests failed on changed IDs/re-embedding, four rollback
  and operation-count cases, one real-Chroma rollback E2E, and eight provenance
  assertions. Fixes made path identity taxonomy-independent, added 500-chunk
  vector snapshot/update batches with compensation and missing-vector cleanup,
  and introduced a precise report-self-reference-free virtual worktree tree.
- Final pass-3 remediation RED found unbatched stale-vector cleanup, no durable
  retry after a failed vector delete, and a private-output owner check that
  accepted foreign-owned directories already at mode `0700`. Six cleanup unit
  cases plus fail-once unchanged-sync integration/E2E failed, and two secure
  output cases wrote instead of rejecting. Fixes added one SQLite active
  snapshot, 500-ID off-loop Chroma deletes, additive tombstone cleanup
  acknowledgement with bounded retry on later successful syncs, and an
  unconditional final-parent owner check before/after mode handling.
- Final pass-4 remediation RED found a 5,000-item backlog ceiling, undurable
  cleanup for never-committed vector IDs, metadata-only career changes counted
  as skips, untrusted writable/foreign storage ancestors, and private report
  output accepted under Git-trackable repository paths. Large cleanup,
  commit/delete-failure, metadata-counter, ancestry, and output-allowlist tests
  failed before fixes. The sync now drains new deletions immediately, then up
  to four 5,000-ID old-backlog pages per successful sync and defers any
  remainder; it records uncommitted cleanup durably, counts authoritative career
  metadata changes as updates with embedding reuse, validates every storage
  ancestor FD, and restricts in-repo private reports to ignored private roots.
- Final pass-5 storage/ingestion remediation RED ran before production edits:
  `uv run --locked pytest -vv --tb=short
  tests/storage/test_evidence_metadata.py::test_vector_write_intent_survives_until_chunk_commit_resolves_it_atomically
  tests/storage/test_evidence_metadata.py::test_active_tombstone_history_is_acknowledged_and_pending_scan_is_indexed
  tests/storage/test_metadata_store.py::test_successful_sync_terminal_transaction_persists_all_ingestion_metrics`
  exited `1` with three expected failures: missing
  `record_vector_write_intents`, active tombstone history retained an empty
  cleanup acknowledgement, and `complete_successful_sync` rejected the new
  terminal metric arguments. The broader new unit/integration/E2E selector
  also failed before production edits, including stale terminal counters,
  missing bounded-retry constant, on-loop SQLite work, and missing durable
  crash intent behavior.
- Final pass-5 retrieval deadline RED exited `1` with five expected failures:
  `ContextSearchService` did not accept bounded retrieval deadline/concurrency
  inputs, a deliberately blocking retriever stalled the caller loop, and a
  timed-out cross-loop retry showed a leaked loop-bound slot. The fix adds one
  service-scoped bounded executor with a cross-loop-safe semaphore, a total
  queue/execution deadline, sanitized typed timeout behavior, and slot release
  only after the blocking worker actually exits.
- Final pass-5 private-output/workflow RED exited `1` with seven expected
  failures: report, private-review, and local-dataset writers accepted an
  outside-CWD or nested/untrusted Git destination, and the `live_provider`
  workflow selected the deterministic offline runner. The trusted repository
  root now comes from the installed module location (with explicit injection
  retained), other Git repositories are rejected, and `live_provider` fails
  closed with exit `3` until a reviewed adapter exists.
- Final pass-6 failed-terminal-metrics RED ran before production edits:
  `uv run --locked pytest -vv --tb=short
  tests/indexing/test_ingestion_service.py::test_failed_sync_terminal_transaction_persists_metrics_when_progress_write_fails`
  exited `1`; after an injected best-effort progress-write failure, the
  terminal `FAILED` row retained `total_documents=0`, `parsed_documents=0`,
  `parsing_failures=0`, and `indexing_latency_ms=0.0` instead of the final
  in-memory values. The failure confirms `complete_failed_sync` did not own the
  required counters and latency in its terminal transaction.
- Final pass-6 failed-terminal-metrics GREEN moved every available ingestion
  counter plus terminal elapsed indexing latency into `complete_failed_sync`'s
  `BEGIN IMMEDIATE` terminal update while retaining optional arguments for
  existing callers. The focused integration/storage/E2E selector passed `3`;
  the affected ingestion, career-ingestion, metadata-store, and deterministic
  career E2E files passed `347`; focused Ruff, production mypy, and
  `git diff --check` passed.
- Final pass-6 authoritative-hydration RED exited `1` with four expected
  failures: the bounded executor had no reusable absolute deadline/`run_until`
  API, blocking SQLite hydration delayed an event-loop heartbeat, and MCP
  hydration timeout tests did not raise. Candidate retrieval and authoritative
  hydration now share one absolute request deadline and bounded executor;
  cancellation retains the slot until the blocking worker exits. The targeted
  regressions passed `4`, all search tests passed `265`, and the affected
  API/career E2E selector passed `38`.
- Final pass-6 public-evaluation-boundary RED first failed five tests at import
  because no explicit public fixture authorization existed. A later integration
  RED passed the arbitrary-substitution cases but failed all seven reviewed
  variant configurations. Public report authorization now requires the exact
  canonical reviewed dataset and corpus plus one of eight explicit checked-in
  configurations, descriptor-held no-follow snapshots, and an explicit writer
  authorization. Copied, renamed, substituted, and symlinked inputs fail closed;
  all evaluation tests passed `134` and the retained E2E passed `5`.
- Final pass-7 deadline-compatibility RED showed a `**kwargs` context did not
  receive the absolute deadline and a legacy context ignored the configured
  `20 ms` deadline until an outer `300 ms` timeout. `**kwargs` is now treated
  as deadline-capable; legacy awaits use the remaining absolute deadline and
  map expiry to the same sanitized typed timeout. New unit/integration/E2E
  regressions passed `3`; the affected search/API/E2E selector passed `73`.
- Final pass-7 workflow-pin RED failed because the approved private job checkout
  had no immutable `ref`. It now checks out `${{ github.sha }}` while retaining
  the branch, approval, self-hosted-runner, and privacy gates; workflow/structure
  selectors passed `20`, YAML parsing and actionlint passed.
- Final pass-7 workload-identity RED exited `1` with nine expected failures:
  comparison accepted changed corpus/config/execution paths and reports lacked
  content identities. Report schema v2 now carries SHA-256 digests of the exact
  descriptor-held dataset/corpus/config snapshots and a versioned execution-path
  identity; comparison fails closed on missing/version/mismatch. Evaluation/E2E
  passed `149`. The intentional v1-to-v2 migration mismatch was observed, then
  all eight checked reports were regenerated as v2 and the same-workload v2
  comparison passed.
- Final pass-8 career-root RED ran five new unit/integration/E2E cases and all
  failed: intermediate symlink ancestors and post-precheck manifest/root swaps
  redirected reads, and the E2E sync succeeded rather than failing. Manifest
  and root ancestry now use descriptor walks with no-follow/ownership/write
  trust; parsing binds and revalidates root device/inode for each read. The
  focused security set passed `63`, broad affected coverage passed `357`, and
  functional E2E passed `74`.
- Final pass-8 indexer-scaling RED reproduced one unfiltered full Chroma scan,
  1,200 singleton deletes, a blocked heartbeat, and unstable managed/raw ID
  fallback. The indexer now uses source/managed-scoped filtered batches of at
  most 500 IDs, all Chroma GET/DELETE work is off-loop, and stable `chunk_id`
  with `doc_id` fallback preserves idempotency. The synthetic 1,200-update delta
  was full scans `1 -> 0`, delete calls `1,200 -> 3` (`-99.75%`); `152`
  affected tests passed.
- Final pass-8 evaluation/workflow security RED could not collect because the
  reviewed-content loader and private workflow wrapper did not exist. Public
  evaluation now verifies the held canonical input snapshots against a reviewed
  SHA-256 manifest before any public output; CI uploads the raw report only
  after success. Private evaluation uses a validated non-symlink temp ancestry,
  unpredictable `0700` run directory, and exclusive no-follow `0600` logs.
  All evaluation tests passed `151`; synthetic public/private smokes and static
  workflow/security checks passed.
- Final pass-9 manifest-identity RED ran seven unit/integration/E2E cases and
  all failed: hardlinks/case aliases produced duplicate physical documents, and
  parent/root swaps after manifest read replaced the ingested snapshot. The
  connector now retains the manifest-parent descriptor, opens the relative root
  from the same chain, passes a bound root device/inode to the parser, verifies
  identity before/after every read, and rejects duplicate file identities before
  parsing/ID generation. Focused `7`, broad career `97`, and functional `79`
  tests passed.
- Final pass-9 retained-reorder RED failed four tests: positional/version vector
  fields were absent, `updated_chunks=0`, and no metadata refresh/rollback
  occurred. Safe non-content lifecycle metadata now refreshes in bounded
  compensating batches while embeddings are reused and SQLite stays authority;
  focused `4`, affected `56`, and broad indexing/storage/career E2E `571`
  passed.
- Final pass-9 warm-write RED showed 1,200 `index.insert` calls/thread handoffs
  and measured `389.055 ms`. Cold and warm vector writes now use bounded bulk
  batches `[500, 500, 200]`, three handoffs (`-99.75%`) and no fixed sleep;
  the synthetic run measured `36.228 ms` including injected per-batch delay,
  with stable IDs/metadata and a zero-write unchanged rerun in real temp Chroma.
- Final pass-10 chunking-scaling RED measured 30k→60k timing ratios of `3.782`
  for plain text and `3.032` for Markdown, while deterministic line-calculation
  work ratios were `4.000` for both paths. Integration and career E2E heartbeat
  tests observed zero ticks during large allowed-document chunking. Newline
  offsets are now computed once, each document chunks off the event loop, and
  cancellation cooperatively stops and joins the worker. Focused unit,
  integration, and E2E coverage passed `5`; broad affected coverage passed
  `156`; functional E2E passed `82`; Ruff and focused mypy passed.
- Final pass-10 pre-chunk identity RED failed four tests: two logical large
  passages became 57 Chroma vectors, configured size transformations still ran,
  and generated-embedding counters remained two. Managed already-chunked
  passages now bypass transformations in cold and warm 500-bounded batches and
  use stable `chunk_id` node IDs; raw unmanaged inputs retain legacy transforms.
  Focused `4`, broad affected `150`, and an additional `31` identity/idempotency
  regressions passed.
- Final pass-10 partial-parse metrics RED could not import the required typed
  progress error. `CareerManifestParsingError` now carries only non-sensitive
  attempted/completed/latency counters; failed finalization atomically persists
  them with `parsing_failures=1` while indexing remains all-or-nothing. Failure
  positions, progress-write failure, retry, repaired success, and idempotency
  coverage passed in a `422`-test affected selector.
- Final pass-11 chunk-cancellation RED ran before production edits:
  `uv run --locked pytest -vv --tb=short` with the new streaming-line,
  plain/Markdown model-build, ingestion cancellation, and career E2E selectors
  exited `1` with five expected failures. Line-oriented paths invoked bulk
  `splitlines()`, both model-building comprehensions ignored a newly requested
  stop, and real ingestion built up to 2,000 models after cancellation instead
  of observing the cooperative stop. The fixtures use synthetic newline-dense
  text and temp stores only; the first pre-production run exposed missing test
  fields and was corrected before this auditable behavior RED.
- Final pass-11 chunk-memory baseline before production edits used
  `tracemalloc` around `_has_markdown_heading("x\n" * 200_000)`:
  400,000 input bytes produced 3,248,796 peak additional traced bytes because
  `splitlines()` materialized every line. Cancellation-during-build baseline
  was 2,000 models constructed after the stop request in the career E2E
  fixture; both metrics should decrease.
- Final pass-11 chunk-cancellation GREEN replaced bulk line materialization
  with a `splitlines()`-compatible streaming iterator, flushed Markdown
  sections as boundaries were found, retained only constant setext-detection
  state, and polled the stop before each plain/Markdown model build. The five
  RED selectors passed, randomized split-line equivalence passed `1000/1000`,
  the broad affected chunker/ingestion/career E2E selector passed `153`, and
  Ruff plus focused mypy passed. The same 200,000-line `tracemalloc` method
  measured 1,820 peak additional bytes, a reduction of 3,246,976 bytes
  (`-99.94%`); deterministic build cancellation stops at four models rather
  than completing the 875-model synthetic output, and integration/E2E require
  fewer than ten builds after the asynchronous stop.
- Final pass-11 Darwin helper RED showed a poisoned `PATH/ps` executed and the
  launcher still ran when the trusted helper was unavailable. PDF RSS sampling
  now validates and invokes only root-owned, regular, executable, non-writable
  `/bin/ps`; failure returns no sample. Focused `2`, all parser `46`, and PDF
  E2E `1` passed with Ruff, mypy, Bandit, and compile checks.
- Final pass-11 latency-semantics RED observed partial parse durations `12.5 ms`
  and `1.536... ms` stored as indexing latency. Pre-index failures now persist
  `indexing_latency_ms=0.0`, while attempted/completed/parsing-failure counters
  remain atomic. Focused `3` and broad affected `448` passed.
- Final pass-12 interrupted-metadata RED ran
  `uv run pytest -q tests/indexing/test_career_ingestion.py::test_interrupted_taxonomy_refresh_repairs_vector_metadata_on_unchanged_retry`
  and exited `1`: a hard interruption left one taxonomy write and the
  authoritative unchanged retry did not repair Chroma. SQLite now records a
  durable vector-metadata-refresh intent before Chroma mutation, replays pending
  intents from active authoritative chunks before unchanged skip, and clears
  them with chunk commit/cleanup. Focused temp-SQLite/real-temp-Chroma coverage
  passed `3`; affected ingestion/storage/E2E passed `136`, and the full
  MetadataStore suite passed `239`.
- Final pass-12 parser-operability RED used six focused synthetic selectors.
  The first command exited `1` with four expected failures (bulk line
  materialization, 12 RSS subprocess samples, Markdown first build after 100%
  input consumption, and five synchronous SQLite touches); the multiline
  Setext selector separately exited `1`. The fix retains 50 ms in-memory
  cancellation polling, throttles durable status/heartbeat and Darwin RSS
  sampling to 0.5 seconds, moves SQLite touch off-loop, streams title
  validation, and incrementally flushes oversized Markdown sections. Focused
  GREEN passed `6`; affected suites passed `223`. Durable touches measured
  `5 -> 1`, RSS samples `12 -> 1`, first Markdown build moved from `100%` to
  `0.339%` input consumed, and title-validation peak traced allocation fell
  from `7,202,797` to `2,103` bytes.
- Final pass-12 snapshot-consistency RED ran parser, manifest, and deterministic
  ingestion-E2E mutation-during-read selectors and exited `1` with three
  expected missing-error failures. Complete manifest and listed-file reads now
  compare pre/post descriptor `fstat` identity, size, nanosecond mtime, and
  nanosecond ctime; drift aborts the whole typed snapshot before indexing or
  cleanup. Focused GREEN passed `3`, affected parser/fetching/E2E passed `106`,
  and Ruff, mypy, Bandit, compile, and diff checks passed.
- Final pass-13 reactivation RED reproduced three successful-sync stale-vector
  paths: tombstoned same-hash reactivation, pre-commit orphan retry with changed
  taxonomy, and real-temp-Chroma filtered retrieval. Each exited `1` because
  stable vector reuse skipped metadata refresh. SQLite-new career chunks now
  enter the existing durable metadata-refresh/rollback transaction whenever an
  index batch reuses a vector; generated-only cold writes avoid the extra
  update. Focused GREEN passed `3`, career ingestion passed `18`, and affected
  ingestion/storage/real-Chroma coverage passed `362`.
- Final pass-13 scaling/cancellation RED exited `1` with five failures. A
  30k-to-60k single-line Markdown doubling cost ratio was `3.959`; title and
  section APIs lacked cancellation, DOCX extraction did not stop, and E2E
  incremental XML parsing did not begin. Chunking now uses one joined buffer and
  monotonic windows; 30k/60k times fell from `0.0837545/0.3315760 s` to
  `0.002141958/0.004344791 s`, ratio `2.028`. Text scanning polls every 256
  codepoints; defused DOCX iterparse uses at most 64 KiB per read and polls XML
  events/text nodes. Focused GREEN passed `5`, affected coverage passed `232`,
  and connector cancellation joined in `1.543 ms`.
- Final pass-13 accepted-snapshot/path RED exited `1` with seven late-mutation,
  NUL/control, raw-error, and E2E failures; a separate trim-control selector
  also exited `1`. Before success the loader now securely reopens and
  revalidates the manifest and every listed file against retained `fstat`
  snapshots. NUL/non-printable path components fail before filesystem calls and
  `ValueError` stays inside the sanitized typed boundary. New selectors passed
  `7` plus `3`; broad parser/fetching/E2E passed `119` with static gates clean.
- The first post-pass-11 full gate exposed one existing LaunchAgent test race
  (`dd` writer helper exit `71` instead of worker exit `17`). The exact test
  passed ten consecutive isolated repeats without code changes; the identical
  full gate then passed. This is recorded as a non-product test flake, not hidden
  as a successful first attempt.
- The first post-pass-13 full gate exposed another timing-sensitive assertion
  in the existing LaunchAgent suite: startup-log compaction observed `79`
  synthetic `dd` calls where the test requires `>100`; all `1808` other tests
  passed. No product code changed. The exact test passed ten consecutive
  repeats, then the identical poisoned-env full gate passed `1809/1809`.
- Tests cover unit, temp-SQLite integration, MCP contract, deterministic
  functional E2E, metrics/reporting/comparison, and CI wiring. Failures were
  missing behavior, not fixture/dependency/environment failures. Production
  files were unchanged when every RED command ran. Focused test syntax, Ruff,
  and `git diff --check` passed.

## TDD GREEN and verification plan

- Focused unit: parser formats/errors, taxonomy/input validation, metric math,
  section/dedup logic, report/comparison threshold behavior.
- Focused integration: temp SQLite schema/round-trip/filter/version lifecycle,
  fake local connector snapshot, unchanged/update/retry embedding counters.
- Focused deterministic E2E: FastMCP `search_evidence` after temp career-file
  ingestion; required fields, filters, exact quote, empty result, duplicates,
  existing tool compatibility.
- Post-refactor affected tests: rerun all focused unit/integration/E2E commands.
- Full suite: `./scripts/verify_all.sh` must pass.
- Matching eval after full suite: run the new deterministic fixture runner and
  baseline comparison; full-suite eval evidence may satisfy this only if it
  executes the exact new dataset and thresholds.
- Startup/tool-registration smoke: local fake/temp composition, no live
  credentials and no user stores.
- Final pass-5 storage/ingestion focused GREEN: the 10 new
  unit/integration/E2E regressions passed; the broader storage, ingestion,
  career-ingestion, and career E2E selector passed `353` tests; neighboring
  indexer/durable-worker cleanup coverage passed `30` tests. Ruff, focused
  mypy, and `git diff --check` passed. Full-suite and functional gates remain
  orchestrator-owned before the next review pass.

## Improvement performance delta

This work adds a brand-new `search_evidence` surface and also claims improved
retrieval measurement/dedup/filter quality. Existing retrieval remains a
comparable safety baseline; the new surface receives an after-only benchmark
where no prior contract exists.

### Declared metrics

- Quality: Recall@1/3/5, Precision@3/5, MRR, nDCG@5, duplicate rate,
  citation-validity rate, source/experience-filter accuracy, empty-result
  accuracy. Higher is better except duplicate rate.
- Runtime: mean/p50/p95 retrieval latency in milliseconds. Lower is better;
  informational except the generous deterministic CI ceiling.
- Ingestion: parse success, unchanged skip, unnecessary re-embedding, full and
  incremental latency. Parse/skip quality higher; re-embedding/latency lower.
- Method: deterministic synthetic fixtures, fake/temp SQLite and Chroma,
  `MockEmbedding` or deterministic retriever; no live API or user data.

### Pre-edit baseline captured

- Command: `uv run --locked python scripts/run_contextwiki_eval.py
  --output-dir /tmp/mcp-career-baseline.4BQHfy --include-latency`.
- Git: `9a2d39c`; dataset: existing 14-case deterministic fixture.
- Result: retrieval `14/14`, document sort `2/2`, answer `9/9`; retrieval mean
  `10.526 ms`, max `22.989 ms`. This is fixture regression evidence, not
  product performance. Existing runner does not calculate requested Recall,
  Precision, MRR, nDCG, p50, or p95, so those baseline cells are `n/a` rather
  than invented.
- Baseline repository gate: `./scripts/verify_all.sh` passed with `1356 passed`,
  coverage `87.91%`, deterministic eval `14/14`, and functional E2E `58 passed`.
- New `search_evidence`: `n/a — no prior contract or comparable executable
  surface` before this work.

### After/delta

Measured public fixture: 13 queries / 14 synthetic chunks,
`label_source=deterministic_fixture`, zero external calls and `$0.00` estimated
API cost. This is `TEST FIXTURE — NOT PRODUCT PERFORMANCE`.

| Metric | Keyword baseline | Selected production analog | Absolute delta |
| --- | ---: | ---: | ---: |
| Recall@5 | 0.958333 | 1.000000 | +0.041667 |
| MRR | 1.000000 | 1.000000 | 0.000000 |
| nDCG@5 | 0.944779 | 0.979276 | +0.034497 |
| Duplicate-result rate | 0.184211 | 0.000000 | -0.184211 |
| Citation validity | 1.000000 | 1.000000 | 0.000000 |
| Source filter accuracy | 0.473684 | 1.000000 | +0.526316 |
| Experience filter accuracy | 0.763158 | 1.000000 | +0.236842 |
| Empty-result accuracy | 1.000000 | 1.000000 | 0.000000 |
| p95 fixture latency | 0.096125 ms | 2.008041 ms | n/a (different execution paths) |

The checked selected report preserved all quality thresholds while executing
real context/evidence services and measured p95 `2.008041 ms`; the final full
gate rerun measured `2.029625 ms`. A separately generated v2 same-workload
report measured `2.019083 ms`; comparison passed with no violation. The prior
v1 report was intentionally not treated as comparable after the schema-v2
workload-identity gate.
Keyword-variant latency came from the direct proxy scorer, so no cross-path
latency delta is claimed. Each standalone timing remains fixture-only.
Ingestion counters were executed with temp/fake stores: first
two-document sync generated three embeddings; unchanged retry skipped both
documents/three chunks, generated zero, and reused three; one-section update
generated one and reused two. Retrieval-only report ingestion-rate/latency
fields stay `null` with denominator `0` rather than inventing measurements.

Chunking scaling used the same synthetic newline-dense 30k/60k-character
documents, `max_chars=64`, seven post-edit median runs (three baseline runs),
and no user data. Plain 30k/60k latency changed from `13.224/50.012 ms` to
`1.666/4.064 ms` (`-87.40%/-91.87%`). Markdown changed from
`22.724/68.905 ms` to `11.917/23.712 ms` (`-47.56%/-65.59%`), with its observed
doubling ratio improving from `3.032` to `1.990`. Sub-5 ms plain timing remains
informational and scheduling-sensitive; the deterministic instrumented
line-calculation work is the scaling gate and now doubles exactly with doubled
input instead of quadrupling.

Pass-11 cancellation/memory used the same synthetic inputs and no user data:

| Metric | Before | After | Delta |
| --- | ---: | ---: | ---: |
| Peak extra traced memory, 200k newline-dense heading scan | 3,248,796 bytes | 1,820 bytes | -3,246,976 bytes (-99.94%) |
| Models built before deterministic cooperative stop | 875 | 4 | -871 (-99.54%) |

The memory measurement uses `tracemalloc` around
`_has_markdown_heading("x\n" * 200_000)`. The model-build measurement uses
the real plain and Markdown-section paths with `max_chars=16`; asynchronous
integration/E2E additionally enforce fewer than ten builds after cancellation.

## Planned functional smoke matrix

| Feature | Caller surface | Safe data mode | Expected result | Status |
| --- | --- | --- | --- | --- |
| Local career sync | Career connector + `IngestionService`; generic MCP sync/status E2E | temp manifest/files/SQLite + fake indexer | parsed/indexed terminal job with counters; unchanged/update/tombstone behavior | passed |
| Source/status inventory | MCP `list_sources`, `sync_all`, `get_sync_status` | fake/temp runtime | existing inventory/status contracts remain valid; configured career connector tested separately | passed |
| Evidence retrieval | real FastMCP `search_evidence` | temp indexed public fixtures | exact quotes, IDs, metadata, filters, dedup | passed |
| Existing retrieval | MCP `search_context`, `search_documents`, `list_documents`, `fetch_context` | retained temp fixture suite | compatible responses | passed |
| Citation/empty behavior | eval runner + MCP tool | deterministic fixture | citation validity `1.0`; truthful empty list; comparison passed | passed |
| Live/private source | configured provider/user stores | not run | explicit approval required; deterministic substitute above | blocked/gated |

## Architecture constraints

- Existing connector `SourceType` and new evidence taxonomy stay separate.
- SQLite remains lifecycle, filter, and citation authority; Chroma only proposes
  candidates and accelerates metadata.
- Local career files enter through `fetching/`, parsing through `parsing/`,
  chunking/indexing through `indexing/`, retrieval through `search/`, and MCP
  formatting through `api/`.
- All schema changes are additive. No reset, destructive migration, or user
  store access.
- Exact quotes are stored passages. No LLM synthesis or paraphrase.
- Default embeddings may egress unless a local/mock embedding is composed;
  docs must state this accurately.

## Risks and rollback

- PDF extraction is layout-dependent; typed per-file failure and deterministic
  fixtures bound the initial support.
- DOCX tables/text boxes may not preserve full reading order; document this
  limitation.
- Schema additions can expose legacy empty values; fall back to connector
  metadata without changing old public tools.
- Near-duplicate thresholds can hide legitimate evidence; use deterministic,
  conservative similarity with tests and measured comparison.
- Physical Chroma cleanup is best effort; SQLite active gate remains safety
  authority. Any new reconciliation must use temp stores only.
- Rollback is branch/PR revert plus additive unused columns; no destructive DB
  rollback or user-data rewrite.

## Three-reviewer evidence

| Pass | Lens | Status | Evidence |
| --- | --- | --- | --- |
| Middle | Bugs/correctness/contracts/tests | actionable, fixed | Typed invalid requests, citation metadata gate, and four-format E2E gaps routed through RED/GREEN remediation. |
| Middle | Security/privacy/data safety | actionable, fixed | Trusted workflow execution, atomic active hydration, private-file permissions, request/parser/PDF bounds remediated. |
| Middle | Performance/reliability/operability/delta | actionable, fixed | Single provider call, batch hydration, env isolation, accurate indexing timer, and cross-path latency `n/a` remediated. |
| Final pass 1 | Bugs/correctness/contracts/tests | actionable, fixed | Taxonomy-only sync left stale Chroma prefilter metadata; fixed through metadata-only refresh with stable IDs and reused embeddings. |
| Final pass 1 | Security/privacy/data safety | actionable, fixed | Configured-disabled source transition and symlinked storage ancestry bypassed private preflight; fixed through configured-source enforcement and full descriptor traversal. |
| Final pass 1 | Performance/reliability/operability/delta | actionable, fixed | Independently identified the configured-disabled transition; same remediation and regression coverage applied. |
| Final pass 2 | Bugs/correctness/contracts/tests | actionable, fixed | Taxonomy-dependent automatic identity, non-atomic metadata refresh, and imprecise report provenance were reproduced and fixed. |
| Final pass 2 | Security/privacy/data safety | clean | No actionable findings; focused security review reported `95 passed`. |
| Final pass 2 | Performance/reliability/operability/delta | actionable, fixed | Sequential per-chunk metadata refresh and cross-store failure consistency fixed through bounded batches and compensating rollback. |
| Final pass 3 | Bugs/correctness/contracts/tests | actionable, fixed | Failed stale-vector deletion was not retried and could poison the bounded candidate budget; durable tombstone retry added. |
| Final pass 3 | Security/privacy/data safety | actionable, fixed | Foreign-owned mode-0700 private output parent was accepted; owner check is now unconditional and revalidated after mode changes. |
| Final pass 3 | Performance/reliability/operability/delta | actionable, fixed | O(n) SQLite/Chroma cleanup blocked the event loop; replaced with one active snapshot and 500-ID off-loop batches. |
| Final pass 4 | Bugs/correctness/contracts/tests | actionable, fixed | Uncommitted cleanup was not durable and metadata-only career changes were counted as skips; ledger and counters corrected. |
| Final pass 4 | Security/privacy/data safety | actionable, fixed | Intermediate ancestor trust and private-report repository destination gaps fixed with per-FD policy and shared allowlist. |
| Final pass 4 | Performance/reliability/operability/delta | actionable, fixed | >5,000 newly deleted IDs drain immediately; old backlog is bounded to four 5,000-ID pages per successful sync and remainder is deferred. |
| Final pass 5 | Bugs/correctness/contracts/tests | actionable, fixed | Crash-window vector writes now have durable pre-write intents resolved atomically with active chunk commit; terminal ingestion metrics persist in the same successful-sync transaction. |
| Final pass 5 | Security/privacy/data safety | actionable, fixed | Private-output trust no longer depends on CWD and rejects other/nested Git repositories; report, review, and local-dataset writers share the policy. |
| Final pass 5 | Performance/reliability/operability/delta | actionable, fixed | Cleanup retry is bounded/indexable/off-loop, retrieval is deadline-bounded off-loop, and unsupported `live_provider` mode fails closed. |
| Final pass 6 | Bugs/correctness/contracts/tests | actionable, fixed | Failed terminal metrics now persist atomically; authoritative hydration shares the total evidence-search deadline off-loop. |
| Final pass 6 | Security/privacy/data safety | actionable, fixed | Public-only evaluation now requires a canonical reviewed dataset/corpus/config triplet and explicit public writer authorization; copied/private/symlinked inputs fail closed. |
| Final pass 6 | Performance/reliability/operability/delta | actionable, fixed | Failed-run counters no longer depend on best-effort hints, and blocking SQLite hydration now uses the bounded executor/deadline/cancellation lifecycle. |
| Final pass 7 | Bugs/correctness/contracts/tests | actionable, fixed | `**kwargs` and truly legacy context services now honor the same absolute evidence-search deadline with typed timeout behavior. |
| Final pass 7 | Security/privacy/data safety | actionable, fixed | The approved private evaluation job checks out the immutable dispatched `${{ github.sha }}` before accessing runner-local data. |
| Final pass 7 | Performance/reliability/operability/delta | actionable, fixed | Report schema v2 binds comparison to dataset/corpus/config content digests and execution-path identity; mismatched workloads fail closed. |
| Final pass 8 | Bugs/correctness/contracts/tests | clean | No actionable findings; focused independent verification passed `103`. |
| Final pass 8 | Security/privacy/data safety | actionable, fixed | Reviewed content-digest authorization, safe CI artifact gating, hardened private temp/log creation, and descriptor-bound manifest/root reads fixed four privacy boundaries. |
| Final pass 8 | Performance/reliability/operability/delta | actionable, fixed | Career indexing full scans/singleton deletes moved to 500-ID off-loop batches; cleanup documentation now matches the 20k retry budget. |
| Final pass 9 | Bugs/correctness/contracts/tests | actionable, fixed | Physical-file aliases are rejected before ID generation; reordered retained chunks refresh lifecycle metadata and update counters without re-embedding. |
| Final pass 9 | Security/privacy/data safety | clean | No actionable findings; focused security verification passed `228`. |
| Final pass 9 | Performance/reliability/operability/delta | actionable, fixed | Manifest/root remain bound to one descriptor snapshot; warm vector writes use three 500-bounded bulk handoffs rather than 1,200 singleton inserts. |
| Final pass 10 | Bugs/correctness/contracts/tests | actionable, fixed | Managed pre-chunked passages now stay one vector per stable chunk; partial manifest progress persists atomically on typed failure. |
| Final pass 10 | Security/privacy/data safety | clean | No actionable findings; independent focused security coverage passed `278`. |
| Final pass 10 | Performance/reliability/operability/delta | actionable, fixed | Chunk line accounting is O(n), off-loop, cooperatively cancellable, and measured across plain/Markdown scaling fixtures. |
| Final pass 11 | Bugs/correctness/contracts/tests | clean | No actionable findings; pass-10 focused verification reran clean. |
| Final pass 11 | Security/privacy/data safety | actionable, fixed | Darwin RSS sampling now pins and validates trusted `/bin/ps`; ambient `PATH` cannot inject a helper. |
| Final pass 11 | Performance/reliability/operability/delta | actionable, fixed | Line processing/section flush/model construction stream with cancellation polling, and pre-index parse failures no longer mislabel parser time as indexing latency. |
| Final pass 12 | Bugs/correctness/contracts/tests | actionable, fixed | Hard interruption after Chroma taxonomy refresh could leave stale prefilter metadata through an unchanged retry; durable SQLite refresh intent and authoritative replay added. |
| Final pass 12 | Security/privacy/data safety | actionable, fixed | Owner-writable manifest/listed files could mutate during descriptor reads; complete reads now reject pre/post `fstat` snapshot drift. |
| Final pass 12 | Performance/reliability/operability/delta | actionable, fixed | Durable parser polling over-wrote SQLite at 20 Hz, long sections/title checks retained whole line lists, and Darwin RSS monitoring spawned `/bin/ps` at 20 Hz; all three paths are now independently throttled or streaming with measured reductions. |
| Final pass 13 | Bugs/correctness/contracts/tests | actionable, fixed | Tombstoned/reactivated and orphan same-hash vectors could reuse stale taxonomy; SQLite-new vector reuse now joins durable metadata refresh before commit. |
| Final pass 13 | Security/privacy/data safety | actionable, fixed | Late post-read snapshot mutation and raw NUL/control filesystem argument errors are rejected by final secure reopen/revalidation and typed path validation. |
| Final pass 13 | Performance/reliability/operability/delta | actionable, fixed | Single-line Markdown suffix copying was quadratic and scan/DOCX cancellation polling was too sparse; monotonic windows and bounded incremental polling restored measured linear/interruptible behavior. |

## Progress log

| Phase | Status | Summary | Evidence |
| --- | --- | --- | --- |
| Branch preflight | completed | Fresh feature branch from current `origin/main`. | Commands and SHA above |
| Repository inspection | completed | Read README, maintained architecture, ADRs, tools, connectors, indexing, storage, retrieval, eval, tests, CI. | Read-only inspection; no user data |
| Improvement delta declare | completed | Quality, latency, and ingestion metrics declared. | Metrics section |
| Improvement baseline | completed | Current executable deterministic retrieval and full-suite baseline captured before edits. | Commands/results above |
| TDD RED | completed | Unit/integration/E2E/eval tests added before production code; expected missing contracts observed. | Exact commands/signatures above |
| Integrated E2E coverage RED | completed | Confirmed the manifest-to-MCP integration test did not exist before adding it. | `uv run --locked pytest -q tests/e2e/test_career_evidence_ingestion_flow.py::test_manifest_to_fastmcp_search_evidence_uses_sqlite_authority` -> exit `4`, `file or directory not found` |
| Focused unit GREEN | completed | Parser/models/metrics/retrieval. | Integrated focused selector: `220 passed`; parser/security rerun included |
| Focused integration GREEN | completed | Temp storage/source/indexing. | Career parser/source/storage/ingestion selectors passed; storage regression selector `23 passed` |
| Focused E2E GREEN | completed | FastMCP evidence workflow. | Included in integrated selector and functional wrapper |
| Integrated E2E coverage GREEN | completed | Explicit manifest -> parser -> ingestion/index lifecycle -> SQLite -> real context candidate pipeline -> evidence service -> FastMCP response, using only a deterministic temp embedding adapter. | Focused selector -> `1 passed`; retained by `scripts/verify_functional_e2e.sh` |
| Refactor + affected rerun | completed | Removed redundant evidence getter; privacy-safe document-ID count; migration backfills made idempotent. | Focused affected tests and static checks green |
| Middle-review remediation | completed | Career source scoped before cap; production dedup default aligned to `0.8`; selected eval runs real context/evidence services; no-answer fixture exercises matching unrelated metadata; manual non-public modes require local-only confirmation and tagged self-hosted runner. | RED signatures above; `70 passed` search/eval; `15 passed` API/E2E/composition; Ruff, mypy, compileall, JSON validation, `git diff --check`, and eval comparison green |
| Full suite GREEN | completed | Final poisoned-env `./scripts/verify_all.sh`; the first run's isolated LaunchAgent timing assertion passed ten repeats before the identical full rerun. | `1809 passed`, coverage `85.60%`, ContextWiki eval `14/14`, reviewed-manifest schema-v2 career comparison passed, functional `89 passed`; injected career env vars were cleared by the gate |
| Matching eval | completed | All eight reports regenerated after the final full suite with one reviewed-manifest/schema-v2 workload identity and precise virtual-worktree provenance; an independent same-workload report passed. | Recall@5/MRR/citation/filter/empty `1.0`, duplicates `0.0`, checked p95 `2.008041 ms`, same-workload v2 `2.019083 ms`, full-gate p95 `2.029625 ms`, violations `[]`; `worktree_tree=8f4e7602b4bb3c579d242634f77729f987fbdecb` |
| Improvement after/delta | completed | Actual baseline/selected metrics recorded. | Delta table above; reports under `evaluation/reports/` |
| Functional smoke | completed | Final matrix covers reactivation taxonomy, accepted-snapshot lifetime, linear single-line Markdown, bounded DOCX cancellation, and all prior MCP/source/index/storage behavior. | Poisoned-env functional gate -> `89 passed` |
| Middle review | completed with remediation | Exactly three fresh read-only reviewers found actionable issues; all were routed to bounded workers with RED/GREEN evidence. | Reviewer table and remediation evidence above |
| Integration | completed | Final verification, report provenance, matching eval, measured deltas, and functional smoke refreshed after pass-13 findings. | Final poisoned-env full gate above |
| Final review | completed with remediation | Exactly three fresh pass-13 reviewers were the final requested subagent review. All findings were routed through strict RED/GREEN remediation; no further reviewer pass will be started per the user's explicit instruction. | Reviewer table and pass-13 remediation evidence above |
| Delivery | pending | Commit, push, main-base PR after clean final review. | Pending |
