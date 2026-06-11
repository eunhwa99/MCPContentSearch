# ContextWiki

[![CI](https://github.com/eunhwa99/MCPContentSearch/actions/workflows/ci.yml/badge.svg)](https://github.com/eunhwa99/MCPContentSearch/actions/workflows/ci.yml)

ContextWiki is a focused MCP retrieval server for private or project-specific
knowledge. It syncs a small set of configured sources into local Chroma vectors,
keeps citation and lifecycle metadata in SQLite, and exposes MCP tools that let
an LLM client retrieve grounded context without reading local databases directly.

The current scope is intentionally small:

- Source connectors: Notion, Tistory, GitHub repositories, and local Obsidian
  vaults.
- Retrieval tools: source listing/status, source sync, context search, context
  fetch, and citation-backed answers.
- Local persistence: Chroma for semantic candidate retrieval, SQLite for source,
  job, document, chunk, and tombstone metadata.

The current slim MCP server does not include the former Web Console, Auto Wiki,
generic website/docs crawler, dynamic web fallback, or legacy live search/index
tools. Those features are historical and superseded for the current project
scope by [ADR 0006](.agents/docs/adr/0006-slim-mcp-core-scope.md).

## Why SQLite Plus Chroma

Chroma answers "which chunks are semantically close to this query?" SQLite
answers "is this chunk still active, citation-ready, and attached to the right
source?"

ContextWiki only returns citeable evidence after Chroma candidates are hydrated
and validated through SQLite. Successful full syncs refresh `last_seen` metadata
and tombstone missing documents for cleanup-capable sources. If a stale vector
entry remains in Chroma, the SQLite active chunk/document gate filters it before
`search_context` or `answer_with_citations` can expose it.

This split keeps retrieval fast while making citation safety explicit.

## MCP Tool Surface

The retained MCP tools are:

| Tool | Purpose |
| --- | --- |
| `list_sources()` | List configured Notion, Tistory, GitHub, and Obsidian sources. |
| `sync_source(source_id)` | Sync one configured source into SQLite metadata and Chroma vectors. |
| `get_sync_status(source_id="")` | Read latest source and sync-job state. |
| `search_context(query, filters=None, top_k=10)` | Return structured, SQLite-validated evidence chunks. |
| `fetch_context(document_id="", chunk_id="")` | Fetch a document or chunk directly from SQLite metadata. |
| `answer_with_citations(question, filters=None, top_k=5)` | Build an evidence-gated answer with citations and used chunks. |

Tool handlers live in `api/tools.py`; business behavior stays in `indexing/`,
`search/`, `fetching/`, and `storage/`.

## Optional Search Query Rewrite

`search_context` can optionally ask an external LLM to produce short query
rewrites when initial local vector results look weak. This is disabled by
default. Enabling it may send the user's search query and normalized query terms
to the configured provider before local Chroma retrieval.

```bash
CONTEXTWIKI_SEARCH_LLM_ENABLED=true
CONTEXTWIKI_SEARCH_LLM_PROVIDER=openai
CONTEXTWIKI_SEARCH_LLM_MODEL=gpt-4.1-mini
OPENAI_API_KEY=...
```

The rewrite path is not a dynamic web fallback, does not fetch external source
content, and does not mutate SQLite or Chroma. Keep it disabled to avoid
query-rewrite egress. Fully local operation also depends on using a local or
otherwise non-egress embedding configuration for LlamaIndex/Chroma.

## Source Connectors

| Source | Source id | Configuration | Notes |
| --- | --- | --- | --- |
| Notion | `source_notion` | `NOTION_API_KEY` | Syncs pages/documents through the Notion fetcher. |
| Tistory | `source_tistory` | `TISTORY_BLOG_NAME` | Syncs blog posts through the Tistory fetcher. |
| GitHub | `source_github` | `CONTEXTWIKI_GITHUB_REPOSITORIES`, optional `GITHUB_TOKEN` | Syncs bounded text/code/Markdown files from configured repositories. |
| Obsidian | `source_obsidian` | `CONTEXTWIKI_OBSIDIAN_VAULT_PATH`, `CONTEXTWIKI_OBSIDIAN_MAX_FILES`, `CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES` | Syncs bounded Markdown notes from a configured local vault. |

GitHub repositories are configured as comma-separated `owner/repo` entries with
an optional `@ref`, for example:

```bash
CONTEXTWIKI_GITHUB_REPOSITORIES="eunhwa99/MCPContentSearch@main"
```

Obsidian is a local-vault configured source. It reads Markdown files from the
configured vault path, skips hidden/Obsidian metadata directories, and uses
`obsidian://open` canonical URLs for citations. It does not require a live
Obsidian app, plugin, or API server. The max file count and max file byte
bounds are positive integers; if a configured vault exceeds either bound, the
sync fails as an incomplete snapshot and stale cleanup stays disabled.

```bash
CONTEXTWIKI_OBSIDIAN_VAULT_PATH="/path/to/temp-or-real-vault"
CONTEXTWIKI_OBSIDIAN_MAX_FILES=2000
CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES=512000
```

Raw secrets are read at runtime from environment variables. They are not stored
in SQLite, committed to docs/tests, or printed by verification commands.

## Minimal Setup

```bash
uv sync --locked --python 3.13 --dev
uv run --locked python main.py
```

For a plain Python syntax check without contacting external services:

```bash
python -m compileall api core environments fetching indexing search storage main.py
```

## Verification

The full local gate is:

```bash
./scripts/verify_all.sh
```

It performs retained-runtime syntax checks, Ruff, scoped mypy, scoped Bandit,
non-live pytest with coverage over retained packages, and the slim functional
E2E gate.

The functional E2E gate is:

```bash
./scripts/verify_functional_e2e.sh
```

It uses non-live tests and temporary test storage for retained MCP flows:
source registry, source sync, context search, context fetch, citation answers,
metadata lifecycle, and Chroma/SQLite citation-safety behavior. It does not run
browser checks, Playwright, Web Console tests, wiki generation smoke, live
external APIs, or LLM calls.
Obsidian verification must use a temporary vault unless the user explicitly
approves a real vault path.

Useful focused checks:

```bash
uv run --locked pytest -q tests/fetching/test_connectors.py
uv run --locked pytest -q tests/api/test_tools_contract.py
uv run --locked pytest -q tests/e2e/test_contextwiki_flow.py
uv run --locked pytest -q tests/search/test_context_service.py tests/search/test_answer_service.py
uv run --locked pytest -q tests/storage/test_metadata_store.py tests/indexing/test_ingestion_service.py
uv run --locked pytest -q tests/scripts/test_demo_public_flow.py
```

Do not treat these commands as evidence unless they were run in the current
branch. This README lists the intended verification surface; PR descriptions and
handoffs should report the actual commands and results from the run.

## Project Map

- `main.py`: FastMCP server composition.
- `api/`: MCP-facing tool contracts and response formatting.
- `core/`: shared models, exceptions, and utilities.
- `environments/`: runtime configuration and secret/environment access.
- `fetching/`: Notion, Tistory, GitHub, and Obsidian source connectors/fetchers.
- `indexing/`: chunking, deduplication, lifecycle coordination, and Chroma writes.
- `search/`: ContextWiki retrieval, ranking, active metadata gates, and answers.
- `storage/`: SQLite source/job/document/chunk lifecycle metadata.
- `tests/`, `scripts/`: non-live verification harnesses.

## One-command Public Demo

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

1. uses `sample_vault/` as a bounded public Obsidian source
2. syncs it through the retained `source_obsidian` connector
3. runs `search_context`
4. runs `answer_with_citations`
5. prints the sync, search, and citation-backed answer payloads

This is a reviewer-facing local transcript runner. It exercises the retained
tool-handler and service path directly without requiring a separate MCP client
setup.

This demo is fixture-backed, normalized, and non-live:

- it uses temporary SQLite and Chroma storage
- it uses `MockEmbedding` instead of a live embedding provider
- it does not require Notion, Tistory, GitHub, or Obsidian credentials
- it forces `CONTEXTWIKI_SEARCH_LLM_ENABLED=false` even if your shell sets it
- it normalizes generated ids and timestamps in the printed payload so the transcript stays stable

Optional flags:

```bash
./scripts/demo.sh --json
./scripts/demo.sh --query "sqlite active evidence gate" --question "Why does ContextWiki validate citations through SQLite?"
```

Expected transcript highlights:

```text
ContextWiki Public Demo
1. Sync retained source
...
"source_id": "source_obsidian"
"status": "succeeded"
...
3. Search query: stale citations
...
"title": "Citation Safety"
...
4. Grounded question: How does ContextWiki prevent stale citations?
...
"evidence_status": "grounded"
```

## Additional Docs

- [ContextWiki Core Understanding](docs/contextwiki-core-understanding.md)
- [Architecture](.agents/docs/architecture.md)
- [ADRs](.agents/docs/adr/)
- [Harness and workflow](.agents/docs/harness-engineering.md)
- [GitHub workflow policy](.agents/docs/github-workflow.md)
- [Plan log](docs/plan/)
