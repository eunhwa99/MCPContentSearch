# ContextWiki

[![CI](https://github.com/eunhwa99/MCPContentSearch/actions/workflows/ci.yml/badge.svg)](https://github.com/eunhwa99/MCPContentSearch/actions/workflows/ci.yml)

ContextWiki is a focused MCP retrieval server for private or project-specific
knowledge. It syncs a small set of configured sources into local Chroma vectors,
stores lifecycle and citation metadata in SQLite, and exposes MCP tools that let
an LLM client retrieve grounded context without reading local databases
directly.

## 🚀 Key Features

- 🔌 **Multi-source connectors**: Notion, Tistory, GitHub, and local Obsidian
- ⚖️ **Hybrid retrieval architecture**: Chroma for semantic search, SQLite for
  lifecycle and citation validation
- 🛠️ **Practical MCP tools**: source listing, sync, status, search, fetch, and
  citation-backed answers
- 🛡️ **Citation safety**: only SQLite-validated chunks are returned as evidence
- 🔒 **Local-first by default**: optional query rewrite stays off unless you
  enable it explicitly, but fully non-egress operation still depends on your
  embedding provider choice

## 🏗️ Architecture Overview

```text
[ Sources ]
 Notion / Tistory / GitHub / Obsidian
                |
                v
         [ Ingestion Service ]
             /            \
            v              v
      [ Chroma ]      [ SQLite ]
    semantic hits    metadata gate
            \              /
             \            /
              v          v
         [ Verified Context ]
```

## 🛠️ MCP Tools

Tool handlers live in `api/tools.py`. Business logic stays in `fetching/`,
`indexing/`, `search/`, and `storage/`.

| Tool | Purpose |
| --- | --- |
| `list_sources()` | List configured Notion, Tistory, GitHub, and Obsidian sources. |
| `sync_source(source_id)` | Sync one configured source into SQLite metadata and Chroma vectors. |
| `sync_all()` | Sync all retained sources concurrently and return aggregate results. |
| `get_sync_status(source_id="")` | Read latest source and sync-job state. |
| `search_context(query, filters=None, top_k=10, include_debug=False)` | Return structured evidence chunks after Chroma retrieval, metadata fallback when needed, and SQLite validation. |
| `search_documents(query, filters=None, top_k=10)` | Return one representative, retrieval-ranked row per matching document. |
| `fetch_context(document_id="", chunk_id="")` | Fetch a document or chunk directly from SQLite metadata. |
| `answer_with_citations(question, filters=None, top_k=5, include_debug=False)` | Build an evidence-gated answer with citations and used chunks by reusing the `search_context` retrieval path. |

At a glance:

- `sync_all()` syncs all retained sources in one pass.
- `search_context(...)` always returns a `debug` key. On the default normal
  path, `include_debug=False` returns `debug={}`, while `include_debug=True`
  returns populated structured debug details.
- `answer_with_citations(..., include_debug=True)` exposes debug details through
  the same retrieval path.
- Today `search_context` also returns a small populated `debug` object when
  `include_debug=False` if the public source filter leaves no matching sources,
  including `debug.rewrite_skipped_reason=no_matching_sources`. In other words,
  the normal default-path `debug` payload is empty, and only that fast path
  returns populated debug data without `include_debug=True`.
  `answer_with_citations` does not have that exception and exposes debug only
  when `include_debug=True`.

## ⚙️ Configuration

### 1. Source connectors

| Source | Source id | Configuration | Notes |
| --- | --- | --- | --- |
| Notion | `source_notion` | `NOTION_API_KEY` | Syncs pages/documents through the Notion fetcher. |
| Tistory | `source_tistory` | `TISTORY_BLOG_NAME` | Syncs blog posts through the Tistory fetcher. |
| GitHub | `source_github` | `CONTEXTWIKI_GITHUB_REPOSITORIES`, `CONTEXTWIKI_GITHUB_DEFAULT_REF`, `CONTEXTWIKI_GITHUB_MAX_FILES`, `CONTEXTWIKI_GITHUB_MAX_FILE_BYTES`, optional `GITHUB_TOKEN`, optional `CONTEXTWIKI_GITHUB_USER_AGENT` | Syncs bounded text/code/Markdown files from configured repositories. |
| Obsidian | `source_obsidian` | `CONTEXTWIKI_OBSIDIAN_VAULT_PATH`, `CONTEXTWIKI_OBSIDIAN_MAX_FILES`, `CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES` | Syncs bounded Markdown notes from a configured local vault. |

GitHub repositories are configured as comma-separated `owner/repo` entries with
an optional `@ref`. If `@ref` is omitted, ContextWiki uses
`CONTEXTWIKI_GITHUB_DEFAULT_REF` and defaults that env var to `main`.

```bash
CONTEXTWIKI_GITHUB_REPOSITORIES="eunhwa99/MCPContentSearch@main"
CONTEXTWIKI_GITHUB_DEFAULT_REF=main
CONTEXTWIKI_GITHUB_MAX_FILES=200
CONTEXTWIKI_GITHUB_MAX_FILE_BYTES=512000
CONTEXTWIKI_GITHUB_USER_AGENT="ContextWikiBot/0.1 (+https://github.com/eunhwa99/MCPContentSearch)"
GITHUB_TOKEN=...
```

Notes:

- `GITHUB_TOKEN` is optional. Unauthenticated GitHub API access depends on the
  target repository being visible without auth and is subject to lower rate
  limits.
- `CONTEXTWIKI_GITHUB_MAX_FILES` and `CONTEXTWIKI_GITHUB_MAX_FILE_BYTES`
  control fetch completeness. Exceeding those bounds means the connector does
  not claim a complete repository snapshot for stale cleanup.
- `CONTEXTWIKI_GITHUB_USER_AGENT` is the HTTP `User-Agent` header knob used by
  the GitHub fetcher.

```bash
CONTEXTWIKI_OBSIDIAN_VAULT_PATH="/path/to/temp-or-real-vault"
CONTEXTWIKI_OBSIDIAN_MAX_FILES=2000
CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES=512000
```

Raw secrets are read at runtime from environment variables. They are not stored
in SQLite, committed to docs/tests, or printed by verification commands.

### 2. Optional search query rewrite

`search_context` can optionally ask an external LLM for short query rewrites
when initial local retrieval looks weak. `answer_with_citations` inherits the
same rewrite egress because its answer flow calls the `search_context`
retrieval path. This is disabled by default.

```bash
CONTEXTWIKI_SEARCH_LLM_ENABLED=true
CONTEXTWIKI_SEARCH_LLM_PROVIDER=openai
CONTEXTWIKI_SEARCH_LLM_MODEL=gpt-4.1-mini
OPENAI_API_KEY=...
```

The default is `off`. When enabled, the search query and normalized query term
groups may be sent to an external LLM. This setting does not fetch source
content or mutate SQLite/Chroma.

## ⚡ Reproducible Launch Paths

### 1. Fresh-machine local launch

Prerequisites:

- Python `3.13`
- [`uv`](https://docs.astral.sh/uv/)

Install dependencies, create a local env file, and start the slim MCP core:

```bash
uv sync --locked --python 3.13 --dev
cp .env.example .env
uv run --locked python main.py
```

Notes:

- `environments/token.py` loads `.env`, so `cp .env.example .env` is the
  intended local setup path.
- Leaving optional source env vars blank is valid; those sources stay disabled
  for future syncs until configured.
- A disabled source does not automatically hide already indexed content from
  retrieval. Existing SQLite-active documents remain retrievable until a later
  cleanup or metadata change removes them.
- The packaged runtime is not keyless today. For non-demo sync/search runs, the
  default embedding path typically requires `OPENAI_API_KEY` even if query
  rewrite stays disabled.
- If you want a public GitHub example after first launch, set
  `CONTEXTWIKI_GITHUB_REPOSITORIES=eunhwa99/MCPContentSearch@main` manually in
  `.env`.
- Default local SQLite/Chroma state is created under
  `~/.mcp_content_search/`.

For a plain syntax check without contacting external services:

```bash
python -m compileall api core environments fetching indexing search storage main.py
```

### 2. Container launch

Build the image:

```bash
docker build -t contextwiki .
```

Prepare the runtime env file before starting the container:

```bash
cp .env.example .env
```

For a real sync/search run, edit `.env` and set an embedding-provider key first.
With the current default runtime, that usually means setting `OPENAI_API_KEY`.

Start the MCP server in a container:

```bash
docker run --rm -it \
  --env-file .env \
  -v contextwiki_data:/home/appuser/.mcp_content_search \
  contextwiki
```

This named volume avoids first-run host-permission issues for reviewers.

If you want to expose a real Obsidian vault to the container, mount it
read-only and point `CONTEXTWIKI_OBSIDIAN_VAULT_PATH` at the container path:

```bash
docker run --rm -it \
  --env-file .env \
  -v contextwiki_data:/home/appuser/.mcp_content_search \
  -v "/absolute/path/to/vault:/vault:ro" \
  -e CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/vault \
  contextwiki
```

Supported limitations:

- This image runs the retained slim MCP core only.
- It does not add deployment automation, multi-service orchestration, or secret
  management beyond `--env-file`.
- Persisted runtime data lives inside `/home/appuser/.mcp_content_search` unless you
  mount that path.

## 🚦 Verification

### 1. Full gate

```bash
./scripts/verify_all.sh
```

Includes syntax checks, Ruff, mypy, Bandit, pytest, and the functional E2E
gate.

### 2. Functional E2E gate

```bash
./scripts/verify_functional_e2e.sh
```

This covers retained MCP flows with non-live tests and temporary storage.

### 3. Focused checks

```bash
uv run --locked pytest -q tests/fetching/test_connectors.py
uv run --locked pytest -q tests/api/test_tools_contract.py
uv run --locked pytest -q tests/e2e/test_contextwiki_flow.py
uv run --locked pytest -q tests/search/test_context_service.py tests/search/test_answer_service.py
uv run --locked pytest -q tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py
uv run --locked pytest -q tests/scripts/test_demo_public_flow.py
uv run --locked pytest -q tests/scripts/test_live_query_smoke.py
uv run --locked pytest -q tests/evals
uv run --locked python scripts/run_contextwiki_eval.py --output-dir artifacts/contextwiki-evals
uv run --locked python scripts/run_contextwiki_eval.py --output-dir artifacts/contextwiki-evals --include-latency
```

`scripts/run_contextwiki_eval.py` is the deterministic reviewer-evidence runner.
It seeds temporary SQLite fixture data, exercises normal retrieval and
grounded-answer flows, and writes deterministic JSON artifacts with:

- group-level mixed-query metrics
- suite pass/fail summaries

When `--include-latency` is supplied, it also writes an informational
`runtime_metrics.json` file with retrieval/answer latency summaries. CI uploads
the deterministic JSON files plus that optional timing file as the
`contextwiki-evals` artifact.

## 🎬 One-command Demo

Run the retained slim tool-handler and service flow against the bundled public
sample vault:

```bash
./scripts/demo.sh
```

If you have not installed dependencies yet:

```bash
uv sync --locked --python 3.13 --dev
./scripts/demo.sh
```

What it does:

1. Uses `sample_vault/` as a bounded public Obsidian source.
2. Syncs it through the retained `source_obsidian` connector.
3. Runs `search_context`.
4. Runs `answer_with_citations`.
5. Prints sync, search, and citation-backed answer payloads.

This demo is non-live and:

- it uses temporary SQLite and Chroma storage
- it uses `MockEmbedding` instead of a live embedding provider
- it does not require Notion, Tistory, GitHub, or Obsidian credentials
- it forces `CONTEXTWIKI_SEARCH_LLM_ENABLED=false` even if your shell sets it
- it normalizes generated ids and timestamps so the transcript stays stable

Optional flags:

```bash
./scripts/demo.sh --json
./scripts/demo.sh --query "sqlite active evidence gate" --question "Why does ContextWiki validate citations through SQLite?"
```

## 🔎 Live Query Smoke

Run a real local retrieval check against your configured ContextWiki runtime:

```bash
uv run --locked python scripts/live_query_smoke.py --query "aws startup"
```

Useful options:

```bash
uv run --locked python scripts/live_query_smoke.py --query "aws startup" --question "How do I start EC2?"
uv run --locked python scripts/live_query_smoke.py --query "github sync" --source-id source_github --top-k 3
uv run --locked python scripts/live_query_smoke.py --query "obsidian citation" --rewrite off
uv run --locked python scripts/live_query_smoke.py --query "obsidian citation" --json
```

Use this only after you have configured real sources in `.env`. The public demo
above is the safer first-run path for reviewers, and it is the only documented
path here that is intentionally keyless.

`--json` prints partially redacted payloads for local debugging. It removes raw
chunk text, previews, and direct `path`/`url` fields, but titles, identifiers,
and synthesized answer text may still reflect local source content. Treat the
output as local diagnostic data rather than public sample content.

## 🗺️ Project Map

- `main.py`: FastMCP server composition
- `api/`: MCP-facing tool contracts and response formatting
- `core/`: shared models, exceptions, and utilities
- `environments/`: runtime configuration and secret/environment access
- `fetching/`: Notion, Tistory, GitHub, and Obsidian connectors/fetchers
- `indexing/`: chunking, deduplication, lifecycle coordination, and Chroma writes
- `search/`: ContextWiki retrieval, ranking, active metadata gates, and answers
- `storage/`: SQLite source/job/document/chunk lifecycle metadata
- `tests/`, `scripts/`: non-live verification harnesses and reviewer utilities

## 📖 Additional Docs

- [ContextWiki Core Understanding](docs/contextwiki-core-understanding.md)
- [Architecture](.agents/docs/architecture.md)
- [ADRs](.agents/docs/adr/)
- [Harness engineering](.agents/docs/harness-engineering.md)
- [GitHub workflow policy](.agents/docs/github-workflow.md)
- [Plan log](docs/plan/)
