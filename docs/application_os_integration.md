# Application OS integration

MCPContentSearch is the retrieval boundary. A separate Application OS may call
it through MCP, but Application OS code and decisions do not belong in this
repository.

## Responsibility boundary

| MCPContentSearch owns | Application OS owns |
| --- | --- |
| Source connectors and local file parsing | Job-description parsing |
| Document/chunk identity, versions, hashes, and lifecycle | Job-fit evaluation and ranking |
| Section-aware chunking and embeddings | Resume advice or material generation |
| SQLite metadata, Chroma candidates, active/tombstone gates | Outreach, referrals, and application tracking |
| Extractive evidence retrieval and exact stored quotes | Product UI and user workflows |
| Retrieval tests, fixture validation, metrics helpers, and CI checks | How retrieved evidence is interpreted or presented |

`search_evidence` never asks an LLM to generate or paraphrase evidence.

## Install and start

Requirements: Python 3.13 and `uv`.

```bash
git clone <MCPContentSearch repository URL>
cd MCPContentSearch
uv sync --locked --python 3.13
cp .env.example .env
uv run --locked python main.py
```

`main.py` runs FastMCP over its default **stdio** transport. Keep stdout owned
by MCP framing; application logs use the configured logging stream. Source sync
is queued in SQLite and requires a second process:

```bash
uv run --locked python -m indexing.sync_worker
```

On macOS, `./scripts/install_sync_worker_launch_agent.sh` installs the same
worker as a LaunchAgent. Docker setup is documented in the project README.
For career files in Docker, mount the manifest and its root read-only and set
the environment variable to the absolute **container** path, for example
`-v "/host/career:/career:ro"` with
`CONTEXTWIKI_CAREER_MANIFEST_PATH=/career/career-manifest.json`.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Default embedding provider used by normal indexing and semantic search |
| `CONTEXTWIKI_CAREER_MANIFEST_PATH` | Absolute path to the explicit career JSON manifest |
| `CONTEXTWIKI_CAREER_MAX_FILE_BYTES` | Positive per-file byte limit; default `10000000` |
| `CONTEXTWIKI_CAREER_MAX_FILES` | Maximum manifest entries; default `100`, hard maximum `1000` |
| `CONTEXTWIKI_CAREER_MAX_TOTAL_RAW_BYTES` | Aggregate listed-file byte limit; default `50000000` |
| `CONTEXTWIKI_CAREER_MAX_TOTAL_EXTRACTED_TEXT_BYTES` | Aggregate extracted UTF-8 text limit; default `100000000` |
| `CONTEXTWIKI_SYNC_WORKER_MAX_CONCURRENT` | Worker source concurrency, integer `1`–`8`; default `2` |
| `NOTION_API_KEY` | Optional Notion connector |
| `TISTORY_BLOG_NAME` | Optional Tistory connector |
| `CONTEXTWIKI_GITHUB_REPOSITORIES` | Optional GitHub targets |
| `GITHUB_TOKEN` | Optional private GitHub access or higher rate limits |
| `CONTEXTWIKI_OBSIDIAN_VAULT_PATH` | Optional Obsidian vault root |

Both the MCP server and sync worker snapshot configuration at startup. Restart
both after `.env` or manifest-path changes.

### Embedding privacy

The default runtime has no environment switch for a local embedding provider.
Normal indexing can send chunk text to OpenAI, and semantic search can send the
query, when the default embedding path is used. This matters for private career
documents. Deterministic tests inject fake or mock embeddings and do not prove
that the production embedding path is local. Do not index private material
until that egress is acceptable or the runtime is explicitly composed with an
approved local embedding implementation.

### Private persistence preflight

Configuring `CONTEXTWIKI_CAREER_MANIFEST_PATH` makes storage privacy a startup
requirement, even if the manifest is temporarily missing or invalid and the
source begins disabled. The connector can refresh later, so its stores are
protected before composition. The SQLite parent directory and Chroma directory
must be owned by the current user with mode `0700`; an existing SQLite file
must be a regular owner-owned file with mode `0600`. New final directories and
the SQLite file are created with those modes, and Chroma initialization runs
with an owner-only creation mask.

Startup uses no-follow descriptor traversal and rejects symlinks anywhere in
the configured path ancestry without reading store contents. Final private
directories/files reject all group/world access. Every existing ancestor
descriptor must be owned by the current user or root and must not be
group/world-writable. The
only writable-ancestor exception is a standard root-owned sticky temporary
root such as `/tmp` or `/var/tmp`; the next component must still satisfy the
normal ownership and write restrictions. It never silently changes existing
permissions. Follow the filename-only error guidance to move or recreate the
store, or apply the suggested `chmod` manually, then restart both processes.

## Configure local career documents

The connector reads only files explicitly listed in one manifest. Supported
formats are `.pdf`, `.docx`, `.md`, `.markdown`, and UTF-8 `.txt`.

1. Copy [the sanitized manifest example](examples/career_manifest.example.json)
   to a private location.
2. Create the manifest `root` directory and listed files.
3. Set an absolute manifest path in `.env`:

```bash
CONTEXTWIKI_CAREER_MANIFEST_PATH=/absolute/private/path/career-manifest.json
CONTEXTWIKI_CAREER_MAX_FILE_BYTES=10000000
CONTEXTWIKI_CAREER_MAX_FILES=100
CONTEXTWIKI_CAREER_MAX_TOTAL_RAW_BYTES=50000000
CONTEXTWIKI_CAREER_MAX_TOTAL_EXTRACTED_TEXT_BYTES=100000000
```

The manifest root may be absolute or relative to the manifest. It must be an
existing non-symlink directory. Document paths must be unique, relative to that
root, stay inside it, and must not traverse `..` or symlinks. The manifest is
limited to 1 MB. File count, per-file bytes, aggregate raw bytes, and aggregate
extracted UTF-8 text are bounded before a snapshot is accepted. Unknown entry
fields, oversized metadata, and duplicate document IDs are rejected. PDF
extraction runs in a disposable subprocess with a deadline and page/text bounds.

All manifest, root, nested listed-file directory, and listed-file components
must be owned by the current user or root and must not be group/world-writable.
An exact standard root-owned sticky temp root is the only writable-directory
exception. Reads use descriptor-relative no-follow traversal, and each parser
binds the approved root device/inode for the full manifest snapshot. Symlinked
intermediate ancestors or a root/ancestor replacement after initialization
fail closed instead of redirecting reads. Existing permissions are never
changed automatically.

Each document entry requires `path` and `source_type`. `experience_type`
defaults to `unknown`. Optional fields are `document_id`, `document_title`,
`company`, `role`, `project`, `start_date`, and `end_date`.
Without an explicit `document_id`, identity is derived only from the normalized
manifest-relative path. Changing evidence or experience taxonomy therefore
updates metadata without replacing the document or its unchanged chunks.

The valid evidence source types are:

```text
resume
previous_resume
project
github_readme
behavioral_story
career_note
skills_inventory
```

The valid experience types are:

```text
professional
academic
personal_project
prototype
unknown
```

Manifest values are authoritative. The parser does not infer professional or
production experience from prose.

### Career source state and sync

- If `CONTEXTWIKI_CAREER_MANIFEST_PATH` is unset, `source_career` is not added
  to the runtime registry and does not appear in `list_sources`.
- If it is set but relative, missing, or a symlink, `source_career` is listed
  as disabled with a sanitized reason; sync does not read files.
- A valid absolute manifest enables `source_career`.

After restarting the server and worker:

```json
{"name":"sync_source","arguments":{"source_id":"source_career"}}
```

Keep the returned `source_id` and `job_id`, then poll that exact job:

```json
{
  "name": "get_sync_status",
  "arguments": {
    "source_id": "source_career",
    "job_id": "<returned job_id>"
  }
}
```

A valid manifest is a complete snapshot. Removing an entry can tombstone that
document only after a complete successful sync. A missing, unsafe, or partially
parsed manifest disables stale cleanup for that failed run.

## MCP connection and discovery

Example Claude Desktop configuration for local `uv`:

```json
{
  "mcpServers": {
    "content-search-server": {
      "command": "/absolute/path/to/uv",
      "args": [
        "--directory",
        "/absolute/path/to/MCPContentSearch",
        "run",
        "--python",
        "3.13",
        "python",
        "main.py"
      ]
    }
  }
}
```

Use the MCP `tools/list` request after connecting. The public inventory is:

```text
list_sources
sync_source
sync_all
get_sync_status
search_context
search_documents
list_documents
fetch_context
search_evidence
```

## `search_evidence` request contract

The generated input schema has exactly five properties (descriptions/titles
omitted here; constraints and enums are exact):

```json
{
  "type": "object",
  "required": ["query"],
  "properties": {
    "query": {"type": "string", "minLength": 1, "maxLength": 4096},
    "source_types": {
      "anyOf": [
        {
          "type": "array",
          "maxItems": 32,
          "items": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "enum": [
              "resume",
              "previous_resume",
              "project",
              "github_readme",
              "behavioral_story",
              "career_note",
              "skills_inventory"
            ]
          }
        },
        {"type": "null"}
      ],
      "default": null
    },
    "experience_types": {
      "anyOf": [
        {
          "type": "array",
          "maxItems": 32,
          "items": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "enum": [
              "professional",
              "academic",
              "personal_project",
              "prototype",
              "unknown"
            ]
          }
        },
        {"type": "null"}
      ],
      "default": null
    },
    "document_ids": {
      "anyOf": [
        {
          "type": "array",
          "maxItems": 100,
          "items": {"type": "string", "minLength": 1, "maxLength": 512}
        },
        {"type": "null"}
      ],
      "default": null
    },
    "top_k": {
      "type": "integer",
      "minimum": 1,
      "maximum": 50,
      "default": 5
    }
  }
}
```

- `source_types`, `experience_types`, and `document_ids` are optional or null.
- Source and experience values must use the fixed taxonomies above.
- Document IDs are trimmed, non-empty, and de-duplicated.
- `top_k` defaults to `5` and must be between `1` and `50`.
- Multiple filter families are combined, not unioned.

## Response contract

The tool intentionally has no FastMCP `outputSchema`: MCP structured content
requires an object wrapper, while this contract requires a bare list. It
returns one MCP text content block containing a UTF-8 JSON **array**, not an
object wrapper. Each item has this shape:

```json
{
  "chunk_id": "string",
  "document_id": "string",
  "document_version_id": "string or null",
  "source_type": "resume",
  "document_title": "string or null",
  "section_title": "string or null",
  "parent_section_title": "string or null",
  "exact_quote": "stored source passage",
  "retrieval_score": 0.94,
  "experience_type": "professional or null",
  "file_name": "string or null",
  "metadata": {}
}
```

`metadata` may include available `company`, `role`, `project`, `start_date`,
and `end_date`. Results are hydrated from active SQLite chunk/document rows;
candidate text does not replace `exact_quote`. Exact and token-Jaccard
near-duplicates are removed while retaining the higher-ranked passage.
`retrieval_score` is either `null` or a finite JSON number. `NaN`, positive
infinity, and negative infinity are never returned.

No relevant active evidence is a successful response containing exactly:

```json
[]
```

Do not treat an empty list as an internal failure or invent evidence in the
Application OS.

## Working request and response

MCP tool call:

```json
{
  "name": "search_evidence",
  "arguments": {
    "query": "Kubernetes reliability improvement",
    "source_types": ["resume"],
    "experience_types": ["professional"],
    "document_ids": ["doc-1"],
    "top_k": 3
  }
}
```

Representative synthetic response shape verified by the MCP contract tests:

```json
[
  {
    "chunk_id": "chunk-1",
    "document_id": "doc-1",
    "document_version_id": "version-1",
    "source_type": "resume",
    "document_title": "Backend Resume",
    "section_title": "Reliability",
    "parent_section_title": "Experience",
    "exact_quote": "Reduced deployment failures by 40%.",
    "retrieval_score": 0.94,
    "experience_type": "professional",
    "file_name": "resume.md",
    "metadata": {
      "company": "Example Systems"
    }
  }
]
```

The values above are synthetic contract examples, not a product benchmark.

## Errors and timeouts

Invalid enum filters, blank document IDs, blank/whitespace queries, and invalid
`top_k` values fail validation. Caller-visible messages are sanitized and do
not echo rejected private values. Handler-level validation uses the label
`invalid_request`; internal failures use:

```text
[internal_error] Evidence retrieval failed
```

Server-side retrieval deadline failures use:

```text
[timeout] Evidence search failed
```

MCP clients receive these as tool execution errors. They must not depend on a
Python exception class crossing the transport.

`search_evidence` has one server-side queue-plus-execution deadline covering
candidate retrieval through final authoritative SQLite hydration. The default
is `AppConfig.request_timeout=10.0` seconds, with at most
`AppConfig.connection_limit=10` synchronous retrieval workers per composed
`ContextSearchService`; embedded compositions may pass narrower constructor
overrides. Synchronous SQLite hydration also runs on this shared executor, not
the FastMCP event loop. Set the client deadline slightly above the server
deadline so the typed timeout can cross MCP. Remote embedding latency can vary.
A timed-out or cancelled request cannot forcibly stop an already-running
worker thread; its slot remains occupied until it exits, preventing unbounded
replacement work.
Retry with backoff rather than immediately multiplying timed-out calls.
Retrying the same search does not intentionally change indexed content,
although its MCP annotation is not read-only/idempotent because startup/read
paths may initialize persistence.
Sync calls are asynchronous queue operations: a client timeout does not replace
exact-job status polling and must not be interpreted as cancellation.

## Retrieval behavior

`search_evidence` reuses `ContextSearchService`'s deterministic query handling,
keyword/vector candidate path, ranking, and SQLite active gate. Career vector
metadata includes document identity, non-PII evidence-source and experience
taxonomies, and non-content position/version/lifecycle fields. All are excluded
from embedding serialization, and SQLite remains authoritative. Retrieval then:

1. applies requested evidence-source, experience, and document-ID metadata
   filters before the fixed `3 * top_k` candidate cap;
2. hydrates stored chunk and document metadata;
3. drops candidates below the evidence relevance threshold;
4. authoritatively reapplies all filters against active SQLite rows;
5. preserves the stored quote and stable IDs;
6. removes exact and near-duplicate quotes.

SQLite remains authoritative for active/tombstoned evidence. Chroma proposes
candidates and performs only the pre-cap narrowing; stale or metadata-mismatched
vector hits are not valid citations.

## Evaluation, tests, and CI

Run all repository checks:

```bash
./scripts/verify_all.sh
```

Run the focused career layers:

```bash
uv run --locked pytest -q \
  tests/parsing/test_career_documents.py \
  tests/storage/test_evidence_metadata.py \
  tests/indexing/test_career_ingestion.py \
  tests/search/test_evidence_service.py \
  tests/api/test_search_evidence_contract.py \
  tests/e2e/test_career_evidence_flow.py
```

Validate the public synthetic dataset and configuration:

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
  --thresholds evaluation/reports/ci_thresholds.json \
  --output artifacts/career-retrieval-evaluation/comparison.json
```

The selected `career-evidence-production-analog-v1` configuration was chosen
from executed public-fixture variants: keyword baseline, exact dedup, exact +
near dedup, metadata filtering, query normalization, hybrid RRF, candidate
tuning, and the combined production analog. Selected CI execution now routes
offline lexical/character candidates through real `ContextSearchService` and
`EvidenceSearchService`. Those services own source scoping, metadata filters,
the `0.8` exact/near-dedup threshold, fixed candidate budget, and relevance rejection. Selected
`candidate_multiplier=3`; `top_k=5` requests a bounded 15 candidates on actual
service path after taxonomy filtering. The deterministic offline provider uses
the same pre-cap filter mapping as production vectors. Candidate
scores use declared deterministic fixture calibration; this does not reproduce
provider embeddings or vector scores. RRF was measured but not selected:
production has no explicit RRF mode, and the isolated fixture variant produced
a no-answer false positive.

Checked historical measured fixture baseline (timestamp and latency in the
checked report):

| Metric | Value |
| --- | ---: |
| Recall@1 / @3 / @5 | `0.9583` / `1.0` / `1.0` |
| Document Recall@1 / @3 / @5 | `0.9583` / `1.0` / `1.0` |
| Precision@3 / @5 | `0.3611` / `0.2167` |
| MRR / nDCG@5 | `1.0` / `0.9793` |
| Citation validity | `1.0` |
| Duplicate-result rate | `0.0` |
| Source / experience filter accuracy | `1.0` / `1.0` |
| Empty-result accuracy | `1.0` |
| Mean / p50 / p95 latency | Fixture-only; see checked report |

These 13 deterministic synthetic cases have
`label_source=deterministic_fixture`. The report states
`TEST FIXTURE — NOT PRODUCT PERFORMANCE`. Latency is an in-process offline
proxy, not production SQLite/Chroma/provider latency. The retrieval-only run
did not measure ingestion rates or latency; those fields are null with zero
denominators.
Cross-path proxy/service latency delta remains `n/a`. The checked same-path
comparison records baseline/current p95 separately and passed with zero
violations. Each verification run writes its own current runtime result under
`artifacts/`; this document does not hardcode a transient run as “latest.”
Measured reports store exact `commit`, `head_tree`, deterministic current
`worktree_tree`, and `clean|dirty` state. The implementation tree includes
tracked and non-ignored untracked code, configs, datasets, workflows, tests, and
docs through a temporary Git index/object directory. Generated report outputs
are restored to HEAD while maintained report docs/thresholds remain in scope.
Mutable harness progress under `docs/plan/` is also restored to HEAD; maintained
architecture/integration docs stay in scope. The script modifies neither real
index nor repository object store. Repeated report generation and plan updates
therefore produce the same identifier for unchanged implementation inputs.

PR CI also runs lockfile, compile, Ruff, mypy, Bandit, public MCP contracts,
non-live tests with coverage, the existing ContextWiki deterministic eval,
the measured career evaluation + threshold comparison, and functional E2E. PR
CI uploads the raw public synthetic career report only after the threshold
comparison succeeds; a failed comparison cannot publish that raw directory.
The manual retrieval
workflow intentionally uploads no artifacts. Its default `public_fixture` mode
runs on GitHub-hosted infrastructure. `private_local`, `larger_local`, and
`live_provider` require `confirm_local_only=true`, the repository default ref,
approval through protected environment `retrieval-evaluation-private`, and a
runner tagged `self-hosted` + `retrieval-evaluation` + `ephemeral` + `isolated`.
Environment branch policy must allow only the default branch and require an
authorized reviewer. The private job checks out the immutable `github.sha`
selected when the default-branch dispatch was created, so an approval delay
cannot advance execution to a newer default-branch commit. Runner must handle
one job and be reset afterward.

Configure non-public dataset/corpus/config paths only in that isolated runner's
local environment. Workflow calls fixed reviewed module `evaluation.runner`;
it does not execute a runner-provided shell command. `live_provider` fails
closed as explicitly unavailable because no reviewed provider adapter is
implemented. It cannot run the offline evaluator while claiming a live run.
Any future live network adapter needs a separate reviewed entrypoint, tests,
and environment approval. Do not bridge private inputs through workflow
secrets, enable xtrace, or upload reports.

Non-public job uses `umask 077`. Its reviewed wrapper validates `RUNNER_TEMP`
with no-follow descriptor traversal, creates a fresh unpredictable owner-only
mode-`0700` run directory, opens process stdout/stderr logs exclusively with
no-follow flags at mode `0600`, and exposes only generic status to Actions logs. Private
report and dataset writers use descriptor-relative atomic no-follow writes,
reject symlink targets/components, and keep files at `0600`. Approved
in-repository output directories are created at `0700`, or an existing
current-user-owned final directory is restricted to `0700`. External output
directories must already be `0700`; unsafe destinations fail closed.
Non-fixture measured and validate-only reports inside the checkout may use only
ignored `evaluation/reports/private/` or `artifacts/private-evaluation/`.
Git-trackable destinations such as `docs/` fail closed. Owner-safe external
destinations remain allowed only when they are not inside another Git
repository; the private workflow writes beneath mode-`0700` runner-local
`RUNNER_TEMP`, outside the checkout. Repository classification comes from the
installed module location, not the caller's current working directory, unless
an explicit trusted repository root is injected by the caller.
Evaluation config and CI threshold schemas are strict allowlists;
unknown/mode-incompatible fields and invalid threshold shapes fail closed.
Public-only input content is separately pinned by
`evaluation/public_fixture_manifest.json`: manifest and input bytes are opened
without following symlink components, checked as descriptor-held SHA-256
snapshots, and rejected before report writing on any mismatch.
Both public and non-public jobs set `CONTEXTWIKI_DISABLE_DOTENV=1`; repository
`.env` values are not loaded by evaluation jobs.
Every third-party action in the private self-hosted job is pinned to a reviewed
full 40-character commit SHA. Updates require verifying the official upstream
tag, reviewing the upstream action diff, recording the release/SHA in the
workflow comment, and rerunning workflow contract tests plus `actionlint` before
protected-branch review. Mutable action tags are not allowed in that job.

Private datasets belong in the ignored
`evaluation/datasets/retrieval_gold.local.jsonl`; private review outputs belong
under ignored `evaluation/reports/private/`. To generate 30–50 private
candidates later, supply an explicit private corpus and provider adapter:

```bash
uv run --locked python -m evaluation.label_assistant \
  --private-corpus /absolute/private/career_corpus.local.jsonl \
  --provider-command "python /absolute/private/provider_adapter.py" \
  --provider-env-var OPENAI_API_KEY \
  --candidate-count 36 \
  --output evaluation/reports/private/candidate-review.jsonl \
  --dataset-output evaluation/datasets/retrieval_gold.local.jsonl
```

The label generator imports a dependency-light corpus loader and never loads
repository `.env`. Adapter subprocesses receive only required runtime basics,
`CONTEXTWIKI_DISABLE_DOTENV=1`, and variables explicitly named by repeated
`--provider-env-var NAME` options; unrelated inherited secrets are omitted.
Adapter stdout is capped at 4 MiB, stderr at 64 KiB, and each phase at 600
seconds. Overflow or timeout terminates and, if needed, kills the adapter
process group. Sanitized failures omit adapter output, paths, and environment
values.
The adapter controls any live-provider text egress and cost. The command runs
candidate generation, two labeling passes, and disagreement adjudication. Its
output remains `label_source=ai_generated_unreviewed` until a human explicitly
reviews it. Never upload the corpus, raw results, or private review artifact.

## Known limitations

- Measured career metrics are a sanitized lexical/character-similarity proxy;
  they do not establish production embedding quality or latency.
- PDF extraction depends on the source layout and may lose hierarchy.
- DOCX parsing reads normal paragraph text/headings from `word/document.xml`;
  tables, text boxes, headers, and footers may be incomplete. XML entity
  expansion is disabled and decompressed `document.xml` obeys the file limit.
- Markdown/DOCX headings preserve hierarchy; PDF and plain text have weaker
  section structure.
- Only explicit manifest files and five listed suffixes are supported; no RTF,
  image OCR, directory-wide implicit crawl, or symlink traversal.
- The active version is retained; this does not provide a historical version
  archive.
- Default embeddings can egress private text/query content as described above.
- Near-duplicate removal uses deterministic token Jaccard, not semantic
  duplicate classification.
- No reranker or Neo4j layer is present.
