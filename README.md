# 🔍 ContextWiki

[![CI](https://github.com/eunaverse/MCPContentSearch/actions/workflows/ci.yml/badge.svg)](https://github.com/eunaverse/MCPContentSearch/actions/workflows/ci.yml)

**A self-hosted knowledge retrieval MCP server for LLM clients.**
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

For detailed data flows and design constraints, see
[Architecture](.agents/docs/architecture.md).

---

## 🛠️ MCP Tools

| Tool | What it does | When the LLM uses it |
|------|--------------|----------------------|
| `list_sources()` | Lists configured sources and their current state | When the user asks which sources are connected or available |
| `sync_source(source_id)` | Starts a sync for one source, or returns its already-running sync job | When the user asks to import or refresh one specific source |
| `sync_all()` | Starts all configured source syncs in the background and immediately reports each launch result | When the user asks to import or refresh every source |
| `wait_for_sync_all()` | Starts or reuses all public configured-source syncs and waits for their final results within a bounded wait | When the user wants one final all-source report without polling each source |
| `get_sync_status(source_id="")` | Shows source and sync-job status for one source, or all sources when `source_id` is omitted | When the user asks whether a sync has finished or why it failed |
| `search_context(query, ...)` | Finds relevant chunks and returns citation-ready context after SQLite validation | When the LLM needs focused evidence to answer the user's question |
| `search_documents(query, ...)` | Returns one result per document with the full best-matching chunk text in `matched_context` | When the user asks for relevant documents and the LLM needs one representative passage from each document |
| `fetch_context(document_id="", chunk_id="")` | Fetches stored document content and its chunks, or one known chunk, directly by ID | As an optional drill-down when the LLM already has an ID and needs more stored content than the search result provides |

`matched_context` is specific to `search_documents`. The separate preview
behavior of `search_context` is unchanged.

### `source_id` values

| Source | `source_id` | Example |
|--------|-------------|---------|
| Notion | `source_notion` | `sync_source("source_notion")` |
| Tistory | `source_tistory` | `sync_source("source_tistory")` |
| GitHub | `source_github` | `sync_source("source_github")` |
| Obsidian | `source_obsidian` | `sync_source("source_obsidian")` |

### Example LLM tool selection

| User request | Tool the LLM may call |
|--------------|-----------------------|
| “Which sources are connected?” | `list_sources()` |
| “Refresh my Notion content.” | `sync_source("source_notion")` |
| “Start refreshing all of my connected sources in the background.” | `sync_all()` |
| “Refresh all of my connected sources and tell me their final results.” | `wait_for_sync_all()` |
| “Has the Notion sync finished?” | `get_sync_status("source_notion")` |
| “Find evidence about how this project prevents stale citations.” | `search_context(...)` |
| “Show me each relevant document about SQLite with its most relevant passage.” | `search_documents(...)` |
| “Retrieve the stored content and chunks for the document you just found.” | `fetch_context(document_id="...")` |

> 💡 In production, use `search_context` / `search_documents` to gather grounded evidence, then let a downstream LLM generate the final answer.

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

| Source | Source ID | Env var to enable |
|--------|-----------|-------------------|
| Notion | `source_notion` | `NOTION_API_KEY` |
| Tistory | `source_tistory` | `TISTORY_BLOG_NAME` |
| GitHub | `source_github` | `CONTEXTWIKI_GITHUB_REPOSITORIES` |
| Obsidian | `source_obsidian` | `CONTEXTWIKI_OBSIDIAN_VAULT_PATH` |

Set only the sources you plan to use. A source stays disabled when its enabling
config is missing or empty. Bad credentials can still fail later during refresh
or sync. Some invalid target values can fail earlier during startup or source
refresh; for example, a malformed `CONTEXTWIKI_GITHUB_REPOSITORIES` value can
prevent the server from starting cleanly.

Source-specific env vars only register and enable each source. With the default
embedding setup, indexing and search also require `OPENAI_API_KEY` unless you
change the LlamaIndex embedding setup in code.

By default, LlamaIndex uses OpenAI embeddings, so indexing may send document
chunks and search may send queries to OpenAI.

### `.env` Example

```bash
OPENAI_API_KEY=...              # required for default indexing/search embeddings

NOTION_API_KEY=...
# Use the blog subdomain only, not the full URL.
# For example, use devlog, not https://devlog.tistory.com.
TISTORY_BLOG_NAME=devlog

# Use owner/repo or owner/repo@ref.
CONTEXTWIKI_GITHUB_REPOSITORIES=eunaverse/MCPContentSearch@main
GITHUB_TOKEN=...                # needed for private repos or higher rate limits

CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/absolute/path/to/vault
```

---

## 🚀 Usage

### Claude Desktop — local uv (Recommended)

This is the easiest setup path on macOS because:

- Claude Desktop can spawn the server directly.
- ContextWiki loads the repository-local `.env` at startup, so Claude Desktop
  does not need plaintext env entries in `claude_desktop_config.json`.
- Obsidian can use your real host vault path directly.
- You do not need Docker mounts for the vault.

Important:

- Repository `.env` values do not override env vars already set by Claude
  Desktop or your shell.
- If you previously set `OPENAI_API_KEY`, `NOTION_API_KEY`, `GITHUB_TOKEN`, or
  other source env vars in `claude_desktop_config.json` or a parent shell, clear
  the stale values there too.

Do this in order:

1. Create `.env` in your repo root.
2. Put your real values there.
3. Add the MCP entry below to Claude Desktop.
4. Fully restart Claude Desktop.
5. In a fresh Claude Desktop chat, ask it to call `list_sources()`.

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

1. Refresh content:
   - For one source, call `sync_source("source_notion")` and inspect its
     top-level `status`. If it is `running`, call
     `get_sync_status("source_notion")` until `latest_job.status` becomes
     `succeeded` or `failed`. Continue only after `succeeded`. An immediate
     `failed` response uses `error_message`, while an `error` response uses
     `message`; after polling ends in `failed`, inspect
     `latest_job.error_message` or `source.latest_failure_reason`.
   - For one completion report covering all public configured sources, call
     `wait_for_sync_all()`. It starts new jobs or reuses already-running jobs,
     then waits within a bounded request for their final per-source results.
     The result can contain a mix of success, failure, skipped launch, and
     timeout outcomes. A timeout ends only this wait; it does not cancel the
     background sync. Call `get_sync_status(source_id)` later to observe a
     timed-out job's eventual completion.
   - To launch all sources without waiting, call `sync_all()`. It remains
     launch-only and returns after launch decisions, without waiting for the
     syncs to finish. Check each `results[].launch_outcome`: `started` means a
     new job was launched, `already_running` means the existing job was reused,
     and `skipped` or `failed` means that source did not start. For every
     started or already running source, poll `get_sync_status(source_id)` until
     `latest_job.status` becomes `succeeded` or `failed`. The top-level status
     summarizes launch acceptance: `accepted`, `partial`, or `failed`.
2. Search successfully refreshed sources with `search_context()` or
   `search_documents()`.

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
| `Invalid GitHub repository spec` | Use `owner/repo` or `owner/repo@ref`; separate multiple repositories with commas |
| Obsidian not working in Docker | Set both the volume mount and `CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/vault` |
| Source still disabled after config change | Fully restart the MCP client — a chat refresh is not enough |
| A sync failed | Call `get_sync_status("source_notion")`, replacing `source_notion` with the failed source ID shown above |

---

## ✅ Verification

```bash
./scripts/demo.sh                           # Run the local sample flow
./scripts/demo.sh --query "your question"   # Run it with a custom query
./scripts/verify_all.sh                     # Run the full developer checks
```

`demo.sh` needs no credentials. It uses the bundled Obsidian sample vault,
temporary SQLite and Chroma storage, and mock embeddings. `verify_all.sh` runs
the full checks used after code changes.

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
