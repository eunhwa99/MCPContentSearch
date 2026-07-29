# ContextWiki

[![CI](https://github.com/eunaverse/MCPContentSearch/actions/workflows/ci.yml/badge.svg)](https://github.com/eunaverse/MCPContentSearch/actions/workflows/ci.yml)

ContextWiki is a local-first MCP server that makes content from Notion,
Tistory, GitHub, and Obsidian searchable from an LLM client. It syncs and
chunks documents, retrieves relevant evidence, and returns citation-ready results.

## How it works

```text
Notion / Tistory / GitHub / Obsidian
                  |
            fetch and normalize
                  |
          deterministic chunks
             /          \
            v            v
   Chroma candidates   SQLite lifecycle state
             \          /
              v        v
       active, citation-ready results
                  |
              MCP client
```

Chroma finds likely matches. SQLite tracks which documents and chunks are
currently active, so stale vector results are filtered before they reach the
client.

## Features

- four source connectors: Notion, Tistory, GitHub, and Obsidian;
- incremental sync with stable document identity and tombstone cleanup;
- chunk-level and document-level retrieval with citation metadata;
- observable source and sync-job status;
- optional OpenAI query rewrite for low-confidence searches;
- stdio MCP transport for clients such as Claude Desktop and Cursor;
- deterministic local tests and a safe demo.

## Quick start

Requires Python 3.13 and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/eunaverse/MCPContentSearch.git
cd MCPContentSearch
uv sync --locked --python 3.13 --dev
./scripts/demo.sh
```

The bundled sample vault demo needs no credentials. It runs the local Obsidian
connector against `sample_vault/`, uses temporary SQLite and Chroma storage,
and uses `MockEmbedding` instead of an external model. It runs retrieval plus
helper preview on the same input by default:

```text
sync_source -> get_sync_status -> search_context -> citation helper preview
```

This local workflow smoke shows that the bundled-vault Obsidian path, source
sync, status reporting, search, and citation wiring work together. It does
**not** test the remote Notion, Tistory, or GitHub connectors, user-configured
sources, an external MCP client, or production embedding quality. Temporary
demo data is deleted when the process exits.

## Configure real sources

Create a local environment file and enable only the sources you want to read:

```bash
cp .env.example .env
# Edit .env
uv run --locked python main.py
```

| Source | Source ID | Configuration |
| --- | --- | --- |
| Notion | `source_notion` | `NOTION_API_KEY` |
| Tistory | `source_tistory` | `TISTORY_BLOG_NAME` |
| GitHub | `source_github` | `CONTEXTWIKI_GITHUB_REPOSITORIES` |
| Obsidian | `source_obsidian` | `CONTEXTWIKI_OBSIDIAN_VAULT_PATH` |

The default real indexing path also requires `OPENAI_API_KEY` for embeddings.
GitHub repositories use `owner/repo` or `owner/repo@ref`. Obsidian requires an
absolute vault path. Existing process environment values take precedence over
the repository `.env`.

### Privacy checkpoint

Application data is stored locally under `~/.mcp_content_search` by default,
but the default runtime is not fully local. Indexing and search can send source
chunks and queries to the configured embedding provider. Optional OpenAI query
rewrite is disabled by default and is a separate form of network egress.

Do not sync sensitive content until you understand these boundaries.

## Connect an MCP client

ContextWiki uses FastMCP's stdio transport. For Claude Desktop on macOS, add a
server entry like this after creating `.env`:

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

Run `which uv` to find the executable path. Use the same server entry in
`.cursor/mcp.json` for Cursor, and fully restart the client after changing its
configuration.

## MCP tools

| Tool | Purpose |
| --- | --- |
| `list_sources()` | List configured sources and their current state. |
| `sync_source(source_id)` | Start or reuse a sync job for one source. |
| `sync_all()` | Start sync for all configured sources. |
| `get_sync_status(source_id="")` | Read source and sync-job status. |
| `search_context(query, ...)` | Return active chunk-level evidence. |
| `search_documents(query, ...)` | Return one representative result per document. |
| `fetch_context(document_id="", chunk_id="")` | Fetch a known active document or chunk. |

Use `search_context` or `search_documents` to retrieve evidence, then let the
MCP client or another downstream LLM compose the final answer.

## Docker

```bash
docker build -t contextwiki .
docker run --rm -i \
  --env-file /absolute/path/to/MCPContentSearch/.env \
  -v contextwiki_data:/home/appuser/.mcp_content_search \
  contextwiki
```

For Obsidian, also mount the vault read-only at `/vault` and set
`CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/vault`.

## Verification

```bash
# Full local gate
./scripts/verify_all.sh

# MCP, sync, search, citation, indexing, and storage flows with temp data
./scripts/verify_functional_e2e.sh
```

## Current limitations

- The default embedding path can use an external provider; a fully local
  embedding model is not an environment-only option today.
- The server assumes a trusted, single-user stdio client. It does not provide
  HTTP authentication, multi-tenancy, quotas, or rate limiting.
- SQLite and Chroma data are not encrypted by the application.
- Automated connector tests use mocks; live provider checks are opt-in.
- No software license has been selected.

More detail:

- [Architecture](.agents/docs/architecture.md)
