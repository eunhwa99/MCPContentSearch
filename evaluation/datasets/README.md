# Retrieval evaluation datasets

`retrieval_gold.example.jsonl` is a public, deterministic, synthetic fixture.
It contains no resume, account, repository, or other private user content.
`career_corpus.example.jsonl` contains the matching sanitized evidence chunks.
Reports produced from it must state:

```text
TEST FIXTURE — NOT PRODUCT PERFORMANCE
```

Create private candidate labels only in
`retrieval_gold.local.jsonl`. That path and `evaluation/reports/private/` are
Git-ignored. Private source text, exact quotes, and raw results must never be
uploaded as CI artifacts.

Each JSONL object contains:

- `query_id`, `query`, and one supported `query_category`
- binary `expected_chunk_ids` and `expected_document_ids`
- optional `graded_relevance` values used by nDCG
- optional `allowed_source_types` and `allowed_experience_types`
- `should_return_empty`
- one truthful `label_source`
- optional reviewer `notes`

`expected_chunk_ids` drive chunk Recall/Precision/MRR/nDCG. Independent
`expected_document_ids` drive Document Recall@1/3/5 and missing-document
failed-case analysis, including document-only labels.

Supported query categories are `exact_keyword`, `semantic_paraphrase`,
`technology`, `scale_or_metric`, `professional_only`,
`personal_project_only`, `section_specific`, `ambiguous`, and `no_answer`.

Supported label sources are `deterministic_fixture`,
`ai_generated_unreviewed`, `ai_generated_reviewed`, and `human_reviewed`.
AI-generated labels remain AI-generated until a human explicitly reviews them.
Private candidate reports therefore use:

```text
AI-LABELED PRIVATE BENCHMARK — REQUIRES HUMAN REVIEW
```

Run the public deterministic evaluation and regression comparison:

```bash
eval_git_identifier="$(bash scripts/evaluation_git_identifier.sh)"
uv run --locked python -m evaluation.runner \
  --dataset evaluation/datasets/retrieval_gold.example.jsonl \
  --corpus evaluation/datasets/career_corpus.example.jsonl \
  --configuration evaluation/configs/deterministic_fixture.json \
  --output-dir artifacts/career-retrieval-evaluation \
  --git-identifier "${eval_git_identifier}" \
  --public-only
uv run --locked python -m evaluation.compare \
  --baseline evaluation/reports/retrieval_fixture_baseline.json \
  --current artifacts/career-retrieval-evaluation/report.json \
  --thresholds evaluation/reports/ci_thresholds.json
```

`--public-only` is fail-closed: it accepts only the exact reviewed dataset and
corpus paths shown above from this checkout, paired with one checked-in public
configuration: `deterministic_fixture.json`, `baseline_keyword.json`,
`candidate_tuning.json`, `exact_dedup.json`, `hybrid_rrf.json`,
`metadata_filters.json`, `near_dedup.json`, or `query_normalization.json`.
Every input path must have no symlinked component. A copied, renamed,
substituted, or symlinked input must use the private execution path and an
owner-restricted private output destination even if its records claim
`deterministic_fixture` provenance. Public report writing is enabled only after
that complete input triplet passes the boundary check; label text alone never
authorizes a public artifact. The reviewed SHA-256 allowlist is checked in at
`evaluation/public_fixture_manifest.json`. The runner opens the manifest and
all three inputs through no-follow descriptor traversal, verifies the captured
bytes against that allowlist, and parses the same descriptor-held snapshots.
An in-place content substitution at a canonical path fails before any public
artifact is written. Reports record the verified SHA-256 digests for dataset,
corpus, and configuration. They also record a versioned identity for
the selected execution path. Regression comparison rejects missing or changed
workload identity, so a v1 checked baseline must be regenerated with this runner
instead of being compared to a v2 report.

Selected public CI evaluation executes `ContextSearchService` and
`EvidenceSearchService` with a deterministic offline candidate provider. Proxy
score calibration, provider omission, and zero external API calls stay explicit
in the report.

Measured execution requires exact Git provenance:
`commit=<40-hex>;head_tree=<40-hex>;worktree_tree=<40-hex>;state=clean|dirty`.
`scripts/evaluation_git_identifier.sh` creates the worktree tree in an isolated
temporary Git index/object directory from tracked and non-ignored untracked
implementation inputs. It restores generated `evaluation/reports/` outputs to
their HEAD state, then reapplies the maintained `README.md` and
`ci_thresholds.json` inputs. Code, configs, datasets, workflows, tests, and docs
remain in scope except mutable harness progress under `docs/plan/`, which is
also restored to HEAD. Maintained architecture and integration docs remain in
scope. The script never stages files in the real index or writes objects into
the repository object store. Clean runs have equal head/worktree trees; dirty
runs retain the exact virtual implementation tree. Repeated report generation
and plan bookkeeping therefore keep the identifier stable when implementation
inputs do not change.

Manual workflow mode defaults to `public_fixture`. Non-public modes
`private_local`, `larger_local`, and `live_provider` require
`confirm_local_only=true`, dispatch from the repository default branch, and
approval through the protected environment `retrieval-evaluation-private`.
That environment must restrict deployment branches to the default branch and
require an authorized reviewer. After dispatch, the approved private job
checks out immutable `github.sha`, not the mutable default-branch tip, so code
cannot advance while approval is pending. The self-hosted runner must carry
`retrieval-evaluation`, `ephemeral`, and `isolated` tags; use a one-job machine
that is reset after completion and has no unrelated workloads. Dataset, corpus,
and configuration paths exist only in runner-local environment. Workflow uses
the reviewed `evaluation.runner` entrypoint, executes no runner-provided shell
command, uploads no artifacts, and bridges no workflow secrets.

Non-public job applies `umask 077`. A fixed run path is never reused. The
reviewed `evaluation.private_workflow` wrapper validates every `RUNNER_TEMP`
path component with no-follow descriptor traversal, creates an unpredictable
owner-only mode-`0700` child, and opens runner stdout/stderr logs exclusively
with no-follow flags at mode `0600`. Actions logs receive only generic
success/failure status. Private
report and dataset writers use descriptor-relative atomic no-follow writes,
reject symlink targets/components, and keep files at `0600`. Approved
in-repository output directories are created at `0700`, or an existing
current-user-owned final directory is restricted to `0700`. External output
directories must already be `0700`; unsafe destinations fail closed.
Non-fixture measured and validate-only reports inside the repository are allowed
only under ignored `evaluation/reports/private/` or
`artifacts/private-evaluation/`; Git-trackable paths such as `docs/` are
rejected. Owner-safe external output remains allowed only outside every other
Git repository. The trusted checkout root is derived from the evaluation
module location rather than the caller's current working directory unless a
caller explicitly injects it. The private workflow uses that external randomly
named runner-local directory beneath validated `RUNNER_TEMP`.
Inspect restricted logs only on isolated runner, then destroy/reset runner after
job.

Both public and non-public evaluation jobs set
`CONTEXTWIKI_DISABLE_DOTENV=1`; neither job loads repository `.env` values.

Every third-party action in the private self-hosted job is pinned to a reviewed
full 40-character commit SHA. To update one, verify the official upstream tag,
review the upstream action diff from the pinned commit to the candidate commit,
record the release and SHA in the workflow comment, then rerun workflow contract
tests and `actionlint` before protected-branch review. Never replace the pin with
a mutable tag.

Local `actionlint` does not know deployment-specific runner labels
`retrieval-evaluation`, `ephemeral`, and `isolated`. Lint workflow with only its
`runner-label` unknown-label diagnostic ignored (or register those labels in a
local actionlint config); all syntax/expression/ShellCheck diagnostics remain
enabled.

`live_provider` currently fails closed as explicitly unavailable: no reviewed provider
adapter is implemented. It cannot silently run the offline evaluator
and report success as a live run. Adding provider network execution requires a
separate reviewed entrypoint, tests, and environment approval. Do not encode
executable commands in any configuration input.

Evaluation configuration is an explicit mode-specific allowlist. Unknown
fields, mode-incompatible fields, and malformed values fail without echoing
field contents. Threshold configuration likewise rejects unknown metrics and
invalid rule schemas before comparing reports.

Default CI uses only public fixtures and offline deterministic dependencies.
The checked-in baseline is measured from this synthetic corpus. It is a
regression fixture, not evidence of product performance.

## Generate a private candidate set later

Private generation never discovers SQLite, Chroma, or local files. Supply one
explicit private corpus JSONL using the same chunk schema as
`career_corpus.example.jsonl`, plus a provider adapter you control:

```bash
uv run --locked python -m evaluation.label_assistant \
  --private-corpus /absolute/private/career_corpus.local.jsonl \
  --provider-command "python /absolute/private/provider_adapter.py" \
  --provider-env-var OPENAI_API_KEY \
  --candidate-count 36 \
  --output evaluation/reports/private/candidate-review.jsonl \
  --dataset-output evaluation/datasets/retrieval_gold.local.jsonl
```

The label generator uses a dependency-light corpus loader and never loads the
repository `.env`. The adapter child receives only required runtime basics,
`CONTEXTWIKI_DISABLE_DOTENV=1`, and environment variables named by repeated
`--provider-env-var NAME` options. Unlisted inherited variables, including
unrelated secrets, are omitted. Adapter stdout is capped at 4 MiB, stderr at
64 KiB, and each phase at 600 seconds. Overflow or timeout terminates and, if
needed, kills the adapter process group; errors never echo adapter output,
paths, or environment values. The adapter receives the phase as its final
argument and one JSON object on stdin. It must return JSON on stdout. Phases are
`generate_candidates`,
`label_pass_1`, `label_pass_2`, and `adjudicate`. Candidate generation must
return 30–50 queries covering every supported category. Label phases return
objects shaped as `{"query_id": "...", "label": "relevant"}` or
`not_relevant`. Adjudication receives only disagreements. The workflow always
writes `ai_generated_unreviewed`; human review is required before changing
provenance. Whether the adapter calls a live provider, incurs cost, or sends
private text is controlled by the person running that explicit command.
