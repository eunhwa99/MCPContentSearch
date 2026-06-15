# 🔍 ContextWiki

[![CI](https://github.com/eunhwa99/MCPContentSearch/actions/workflows/ci.yml/badge.svg)](https://github.com/eunhwa99/MCPContentSearch/actions/workflows/ci.yml)

**A private knowledge retrieval MCP server for LLM clients.**
Syncs Notion · Tistory · GitHub · Obsidian into vector + metadata stores and returns citation-backed context.

---

## 🏗️ Architecture

```text
[ Sources ]
 Notion / Tistory / GitHub / Obsidian
                |
                v
         [ Ingestion Service ]
             /            \
            v              v
      [ Chroma ]      [ SQLite ]
    semantic search   metadata gate
            \              /
             v            v
         [ Verified Context ]
```

---

## 🛠️ MCP Tools

| Tool | Description |
|------|-------------|
| `list_sources()` | List configured sources |
| `sync_source(source_id)` | Sync a specific source |
| `sync_all()` | Sync all sources at once |
| `get_sync_status(source_id="")` | Get source and sync job status |
| `search_context(query, ...)` | Semantic search with SQLite validation |
| `search_documents(query, ...)` | Search results grouped by document |
| `fetch_context(document_id="", chunk_id="")` | Fetch a specific document or chunk directly |

> 💡 In production, use `search_context` / `search_documents` to gather grounded evidence, then let a downstream LLM generate the final answer.
>
> Internal demo/eval flows may still use `CitationAnswerService.answer_with_citations(...)` as a helper preview, but it is no longer a public MCP tool.

---

## ⚡ Quick Start

**Prerequisites:** Python `3.13`, [`uv`](https://docs.astral.sh/uv/)

```bash
uv sync --locked
cp .env.example .env
uv run --locked python main.py
```

**Docker:**

```bash
docker build -t contextwiki .
cp .env.example .env
docker run --rm -i \
  --env-file .env \
  -v contextwiki_data:/home/appuser/.mcp_content_search \
  contextwiki
```

> For Obsidian in Docker, add `-v "/path/to/vault:/vault:ro"` and set `CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/vault`.

---

## ⚙️ Configuration

### Source Activation

| Source | Source ID | Required env var |
|--------|-----------|-----------------|
| Notion | `source_notion` | `NOTION_API_KEY` |
| Tistory | `source_tistory` | `TISTORY_BLOG_NAME` |
| GitHub | `source_github` | `CONTEXTWIKI_GITHUB_REPOSITORIES` |
| Obsidian | `source_obsidian` | `CONTEXTWIKI_OBSIDIAN_VAULT_PATH` |

A source is automatically disabled when its env var is missing or invalid.

### `.env` Example

```bash
OPENAI_API_KEY=...              # required for embeddings

NOTION_API_KEY=...
TISTORY_BLOG_NAME=devlog        # subdomain only, not the full URL

CONTEXTWIKI_GITHUB_REPOSITORIES=eunhwa99/MCPContentSearch@main
GITHUB_TOKEN=...                # needed for private repos or higher rate limits

CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/absolute/path/to/vault
```

> ⚠️ Do not wrap `CONTEXTWIKI_GITHUB_REPOSITORIES` in quotes. Format: `owner/repo` or `owner/repo@ref`.

### Search Query Rewrite (Optional)

```bash
CONTEXTWIKI_SEARCH_LLM_ENABLED=true
CONTEXTWIKI_SEARCH_LLM_PROVIDER=openai
CONTEXTWIKI_SEARCH_LLM_MODEL=gpt-4.1-mini
```

---

## 🚀 Usage

### Claude Desktop — local uv (Recommended)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

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

> Run `which uv` to find the path. **Fully restart Claude Desktop** after any config change.

### Claude Desktop — Docker

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

Add the same local uv config above to `.cursor/mcp.json`.

### After Connecting

1. Call `sync_all()` or `sync_source("source_notion")`
2. Search with `search_context()` or `search_documents()`

**Example prompt:**
```text
find my projects about DynamoDB and organize it with STAR method. Answer in English
```

![Claude Desktop using ContextWiki MCP as a retrieval backend before Claude composes the final STAR-style response](docs/images/claude-desktop-dynamodb-star-example.png)

---

## 🔧 Troubleshooting

| Symptom | Fix |
|---------|-----|
| MCP server not discovered | Recheck config path and fully restart the client |
| Only works after manual start | Run `command` + `args` directly in terminal to see errors |
| `Invalid GitHub repository spec` | Remove quotes from `.env`; use `owner/repo`, not a full URL |
| Obsidian not working in Docker | Set both the volume mount and `CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/vault` |
| Source still disabled after config change | Fully restart the MCP client — a chat refresh is not enough |
| `sync_all()` too slow | Sync individually: `sync_source("source_github")`, etc. |

---

## ✅ Verification

```bash
./scripts/demo.sh                           # Quick flow check with sample vault (no credentials needed)
./scripts/verify_all.sh                     # Full verification after code changes
./scripts/demo.sh --query "your question"   # Custom query
```

The default demo transcript is the canonical portfolio path: the same question
is used for retrieval and helper answer preview.

That path above is the safer first-run path for reviewers, and it is the only documented
path here that is intentionally keyless.

If you override both `--query` and `--question` with different values, read the
output as separate probes rather than one validated end-to-end chain.

This is the aligned same-input smoke path for reviewer-friendly transcripts.
When `--query` and `--question` diverge, the transcript explicitly labels the output as
separate probes so reviewers do not mistake it for one validated chain.

All live-smoke output should be treated as local diagnostic data.

---

## 📁 Project Structure

```text
main.py          FastMCP server entry point
api/             MCP tool handlers
core/            Shared models, exceptions, utilities
environments/    Env var and secret loading
fetching/        Source connectors (Notion, Tistory, GitHub, Obsidian)
indexing/        Chunking, deduplication, Chroma indexing
search/          Search, ranking, metadata gate, citation answer support
storage/         SQLite lifecycle management
tests/, scripts/ Verification harnesses and utilities
```

---

## 📖 Additional Docs

- [Architecture](.agents/docs/architecture.md)
