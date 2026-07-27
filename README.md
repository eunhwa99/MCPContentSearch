# ContextWiki

[![CI](https://github.com/eunaverse/MCPContentSearch/actions/workflows/ci.yml/badge.svg)](https://github.com/eunaverse/MCPContentSearch/actions/workflows/ci.yml)

**A private, multi-source RAG retrieval server that gives MCP clients
citation-ready evidence without trusting stale vector hits.**

ContextWiki syncs Notion, Tistory, GitHub, and Obsidian into a retrieval layer
built with FastMCP, LlamaIndex, ChromaDB, and SQLite. Chroma finds semantically
similar candidates; SQLite remains the lifecycle source of truth and removes
documents or chunks that are no longer active before evidence reaches the
caller.

## Why this project exists

A vector database can retain an old embedding after its source document has
changed or disappeared. Returning that hit produces a plausible but stale
citation. ContextWiki separates the two responsibilities:

- **Chroma is the retrieval accelerator.** It proposes semantic candidates.
- **SQLite is the active-evidence gate.** It validates document and chunk
  lifecycle state before a result can be returned or cited.
- **MCP is the caller contract.** Seven focused tools expose source sync,
  lifecycle status, chunk search, document search, and direct evidence fetch.

The result is an applied RAG backend with explicit document identity,
deterministic chunking, incremental indexing, tombstone safety, inspectable
ranking, and citation metadata.

## Reproduce the portfolio path

Prerequisites: Python `3.13` and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --locked --python 3.13 --dev
./scripts/demo.sh
```

`./scripts/demo.sh` is the default reviewer path: it syncs the bundled sample vault into temporary storage and needs no credentials. It disables LLM query
rewrite, uses a mock embedding model, and keeps retrieval plus helper preview on the same input by default without reading or mutating the default user ChromaDB
or SQLite database.

Run the deterministic evaluation and write reviewer-readable artifacts:

```bash
uv run --locked python scripts/run_contextwiki_eval.py \
  --output-dir artifacts/contextwiki-evals
```

## Deterministic evaluation snapshot

The checked-in fixture suite currently passes **13/13 retrieval cases** and
**9/9 answer cases**. The generated report also presents ranking,
citation, status, and insufficient-status metrics with their scorable
denominators.

| Surface | Current fixture result | What it checks |
| --- | ---: | --- |
| Retrieval cases | 13/13 passed | top result, required/forbidden chunks, source, mixed-language and negative queries |
| Answer cases | 9/9 passed | evidence status, required terms, citation linkage, unsupported terms, secret-like output |
| Retrieval ranking | hit rate 1.0000; MRR 1.0000; recall 1.0000; nDCG 1.0000 | 11 positively labeled cases at each case's `top_k` |
| Grounding status | 1.0000 (9/9) | expected `grounded` or `insufficient` status |
| Required-citation recall | 1.0000 (9/9 labels) | 8 cases with required chunk citations |
| Citation coverage | 1.0000 (12/12 used chunks) | 8 cases that used evidence chunks |
| Insufficient-status accuracy | 1.0000 (1/1) | status-only check for the labeled insufficient-evidence case |

This is a **small deterministic regression suite**, not a production-quality
benchmark. It uses 13 labeled retrieval queries, 9 answer cases, temporary
SQLite state, a deterministic lexical stand-in for `VectorIndexRetriever`,
deterministic answer rendering, and no live LLM, source API, user Chroma data,
or user SQLite data. All seeded fixture records are active, so this run executes
the normal SQLite validation path but does not test inactive or tombstoned
candidate suppression. The results demonstrate repeatable behavior on retained
fixtures; they do not establish general RAG quality or live-provider latency.
Two negative retrieval cases are pass/fail regressions but are intentionally
excluded from positive ranking denominators.

See [Evaluation methodology](docs/evaluation.md) for metric definitions,
denominators, limitations, and the next benchmark steps.

## Architecture

```text
Notion / Tistory / GitHub / Obsidian
                  |
                  v
        normalized source documents
                  |
                  v
     stable identity + deterministic chunks
             /                 \
            v                   v
   Chroma / LlamaIndex       SQLite
   semantic candidates       lifecycle truth
             \                 /
              v               v
          active-evidence validation
                    |
                    v
    MCP search / fetch / citation helper
```

The active-evidence boundary is intentional: vector deletion is best effort,
while SQLite tombstones and active lifecycle records can still suppress a stale
candidate. Cleanup is allowed only after a complete successful source snapshot;
failed or bounded partial syncs do not infer that missing documents were
deleted.

For the maintained data flow and contract details, see
[Architecture](.agents/docs/architecture.md).

## MCP tools

| Tool | Description |
| --- | --- |
| `list_sources()` | List configured sources |
| `sync_source(source_id)` | Start sync for one configured source |
| `sync_all()` | Start sync across configured sources |
| `get_sync_status(source_id="")` | Read source state and sync-job progress |
| `search_context(query, ...)` | Search chunk evidence with SQLite validation |
| `search_documents(query, ...)` | Search and group results by document |
| `fetch_context(document_id="", chunk_id="")` | Fetch one active document or chunk |

In normal use, an MCP client calls `search_context` or `search_documents` to
collect validated evidence, then a downstream LLM composes the final response.
`CitationAnswerService` is an internal debug/evaluation helper, not an eighth
public MCP tool.

## Quick start

```bash
uv sync --locked --python 3.13
cp .env.example .env
uv run --locked python main.py
```

Docker:

```bash
docker build -t contextwiki .
cp .env.example .env
docker run --rm -i \
  --env-file .env \
  -v contextwiki_data:/home/appuser/.mcp_content_search \
  contextwiki
```

For Obsidian in Docker, add
`-v "/path/to/vault:/vault:ro"` and set
`CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/vault`.

## Configuration

### Source activation

| Source | Source ID | Environment variable |
| --- | --- | --- |
| Notion | `source_notion` | `NOTION_API_KEY` |
| Tistory | `source_tistory` | `TISTORY_BLOG_NAME` |
| GitHub | `source_github` | `CONTEXTWIKI_GITHUB_REPOSITORIES` |
| Obsidian | `source_obsidian` | `CONTEXTWIKI_OBSIDIAN_VAULT_PATH` |

Set only the sources you plan to use. A source remains disabled when its
enabling configuration is missing or empty. Bad credentials can fail later
during refresh or sync; malformed target configuration can fail earlier during
startup.

With the default embedding setup, successful indexing requires
`OPENAI_API_KEY`. Disabling query rewrite alone does not make embedding and
search fully local. The current application startup has no supported
environment-variable switch for local embeddings; a local or non-egress model
requires code-level LlamaIndex composition.

Example:

```bash
OPENAI_API_KEY=...              # default embedding path

NOTION_API_KEY=...
TISTORY_BLOG_NAME=devlog        # subdomain only, not a full URL

CONTEXTWIKI_GITHUB_REPOSITORIES=eunaverse/MCPContentSearch@main
GITHUB_TOKEN=...                # private repos or higher rate limits

CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/absolute/path/to/vault
CONTEXTWIKI_OBSIDIAN_MAX_FILES=2000
CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES=512000
```

GitHub repository specs use `owner/repo` or `owner/repo@ref`. Do not wrap
`CONTEXTWIKI_GITHUB_REPOSITORIES` in quotes in `.env`.

### Optional query rewrite

Query rewrite is disabled by default. Enabling it sends a redacted form of the
user query and normalized terms to the configured provider:

```bash
CONTEXTWIKI_SEARCH_LLM_ENABLED=true
CONTEXTWIKI_SEARCH_LLM_PROVIDER=openai
CONTEXTWIKI_SEARCH_LLM_MODEL=gpt-4.1-mini
```

Review [Security and data flow](SECURITY.md) before enabling external
providers with private knowledge.

## MCP client setup

### Claude Desktop: local uv

1. Create `.env` in the repository root.
2. Add only the source and provider values you need.
3. Add the server configuration below to Claude Desktop.
4. Fully restart Claude Desktop.
5. In a fresh chat, ask it to call `list_sources()`.

On macOS, add this to
`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "content-search-server": {
      "command": "/absolute/path/to/uv",
      "args": [
        "--directory", "/absolute/path/to/MCPContentSearch",
        "run", "--python", "3.13", "python", "main.py"
      ]
    }
  }
}
```

Run `which uv` to locate the executable. ContextWiki loads the repository-local
`.env`, but already-set parent-process environment variables take precedence.
Fully restart the client after changing either location.

### Claude Desktop: Docker

```json
{
  "mcpServers": {
    "content-search-server": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "--env-file", "/absolute/path/to/MCPContentSearch/.env",
        "-v", "contextwiki_data:/home/appuser/.mcp_content_search",
        "contextwiki:latest"
      ]
    }
  }
}
```

### Cursor

Add the same local uv MCP configuration to `.cursor/mcp.json`.

After connecting:

1. Call `sync_all()` or `sync_source("source_obsidian")`.
2. Poll `get_sync_status()` until the job reaches a terminal state.
3. Call `search_context()` or `search_documents()`.

Example prompt:

```text
Find my projects about DynamoDB and organize them with the STAR method.
```

![Claude Desktop using ContextWiki as a retrieval backend before composing a STAR-style response](docs/images/claude-desktop-dynamodb-star-example.png)

## Engineering trade-offs

| Decision | Benefit | Cost |
| --- | --- | --- |
| Chroma candidates + SQLite active gate | Fast semantic recall without treating vector state as deletion truth | Two stores must remain lifecycle-aligned |
| Stable source-aware document IDs | Incremental sync and predictable reactivation | Connector identity rules require care |
| Deterministic source-aware chunking | Repeatable citations and comparable eval fixtures | Less adaptive than model-driven chunking |
| Complete-snapshot cleanup gate | Partial fetches cannot silently tombstone valid content | Stale cleanup may be deferred after incomplete sync |
| Optional LLM query rewrite | Can recover vocabulary mismatch | Adds latency, cost, nondeterminism, and query egress |
| Fixture-first evaluation | Fast, credential-free regression evidence | Does not measure live embeddings, LLMs, or production corpora |

## Verification

```bash
./scripts/demo.sh
./scripts/demo.sh --query "your question"
uv run --locked python scripts/run_contextwiki_eval.py \
  --output-dir artifacts/contextwiki-evals
./scripts/verify_functional_e2e.sh
./scripts/verify_all.sh
```

Live source validation is intentionally separate because it can call external
services and read or mutate configured user storage.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| MCP server not discovered | Recheck executable/repository paths and fully restart the client |
| Only works after manual start | Run the configured `command` and `args` in a terminal |
| `Invalid GitHub repository spec` | Remove quotes; use `owner/repo`, not a full URL |
| Obsidian fails in Docker | Set both the read-only vault mount and `CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/vault` |
| Source remains disabled | Fully restart the MCP process after configuration changes |
| `sync_all()` is too slow | Start one source with `sync_source(...)` and poll its status |

## Project structure

```text
main.py          FastMCP composition and server startup
api/             Stable MCP tool handlers
core/            Shared models, exceptions, and utilities
environments/    Runtime configuration and secret loading
fetching/        Notion, Tistory, GitHub, and Obsidian connectors
indexing/        Chunking, incremental indexing, and lifecycle coordination
search/          Retrieval, ranking, active gate, and citation support
storage/         SQLite source/job/document/chunk lifecycle state
evals/           Deterministic retrieval and answer-quality fixtures
tests/, scripts/ Verification, demo, and evaluation entrypoints
```

## Further documentation

- [Evaluation methodology](docs/evaluation.md)
- [Security and data flow](SECURITY.md)
- [Architecture](.agents/docs/architecture.md)
- [Evaluation runner reference](evals/README.md)
