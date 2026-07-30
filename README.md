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
| `get_sync_status(source_id="", job_id="")` | Shows one exact job when both IDs are supplied, the latest job for one source when only `source_id` is supplied, or all sources when both are omitted | When the user asks whether a sync has finished or why it failed |
| `search_context(query, ...)` | Finds relevant chunks and returns citation-ready context after SQLite validation | When the LLM needs focused evidence to answer the user's question |
| `search_documents(query, ...)` | Returns one result per matching document and can sort the semantic candidates by relevance or normalized dates | When the user asks for relevant documents and the LLM needs one representative passage from each document |
| `list_documents(...)` | Browses all active public documents without a semantic query, with deterministic date sorting and cursor pagination | When the user asks for recent, oldest, or date-bounded documents rather than topic matches |
| `fetch_context(document_id="", chunk_id="")` | Fetches stored document content and its chunks, or one known chunk, directly by ID | As an optional drill-down when the LLM already has an ID and needs more stored content than the search result provides |

`matched_context` is specific to `search_documents`. The separate preview
behavior of `search_context` is unchanged.

### Date filters and sorting

`search_context`, `search_documents`, and `list_documents` accept a typed
`filters` object with these fields:

```json
{
  "source_ids": ["source_notion", "source_tistory"],
  "published_from": "2026-07-01T00:00:00Z",
  "published_to": "2026-07-31T23:59:59Z",
  "modified_from": "",
  "modified_to": "",
  "indexed_from": "",
  "indexed_to": ""
}
```

Use `source_id` for one source or `source_ids` for several sources. They are
alternatives in normal calls. If both are supplied, ContextWiki uses the
deduplicated union of `source_id` and `source_ids`; it does not intersect them.
Unknown filter keys are rejected by the MCP input schema.

All date bounds are inclusive and normalized to UTC. ISO 8601 timestamps
without an offset are treated as UTC; a date-only value starts at midnight UTC.
An empty field means that bound is not applied. Results include
`published_at`, `modified_at`, `indexed_at`, and `date_provenance` when known.

- `search_context(query, filters=..., top_k=10)` keeps relevance ordering while
  applying source and normalized-date filters through SQLite before truncation.
- `search_documents(query, filters=..., sort_by="relevance",
  sort_order="desc", top_k=10)` supports `relevance`, `published_at`,
  `modified_at`, or `indexed_at`. Date sorting orders only documents found in
  the bounded semantic candidate set; use `list_documents` for a global date
  view.
- `list_documents(filters=..., sort_by="indexed_at", sort_order="desc",
  page_size=20, cursor=null)` supports the three normalized date fields,
  requires no query, and returns `{"documents": [...], "next_cursor": ...}`.
  `page_size` must be between 1 and 50. Pass the opaque `next_cursor` back
  unchanged with the same filters and sort settings; null dates sort last.

Normalized date provenance is source-aware: Notion supplies creation and edit
times, Tistory supplies publication time when present, and Obsidian supplies
filesystem modification time. A GitHub blob SHA remains revision metadata
(`version_id`) and is never interpreted as a modification timestamp.
`indexed_at` records when SQLite stores the document. The additive SQLite
columns are populated by normal future sync/indexing; existing Chroma vectors
do not need to be rebuilt.

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
| “Refresh all of my connected sources and tell me their final results.” | `sync_all()`, then make paced exact-job `get_sync_status(source_id=..., job_id=...)` calls |
| “What is the latest Notion sync status?” | `get_sync_status("source_notion")` |
| “Find evidence about how this project prevents stale citations.” | `search_context(...)` |
| “Show me each relevant document about SQLite with its most relevant passage.” | `search_documents(...)` |
| “Show my newest documents from July, regardless of topic.” | `list_documents(filters={"indexed_from": "2026-07-01"}, sort_by="indexed_at", sort_order="desc")` |
| “Retrieve the stored content and chunks for the document you just found.” | `fetch_context(document_id="...")` |

> 💡 In production, use `search_context` / `search_documents` to gather grounded
> evidence, `list_documents` for query-less date browsing, then let a downstream
> LLM generate the final answer.

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

# Use a bare owner to discover all owned repositories visible to GITHUB_TOKEN.
CONTEXTWIKI_GITHUB_REPOSITORIES=eunaverse
# Or sync only exact repositories; separate targets with commas or newlines.
# CONTEXTWIKI_GITHUB_REPOSITORIES=eunaverse/MCPContentSearch,eunaverse/website@main
GITHUB_TOKEN=...                # needed for private repos or higher rate limits

CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/absolute/path/to/vault
```

GitHub targets are resolved when each sync runs:

- `owner` syncs repositories owned by that account and visible to the optional
  `GITHUB_TOKEN`, using each repository's default branch.
- `owner/repo` syncs one repository using `CONTEXTWIKI_GITHUB_DEFAULT_REF`
  (default: `main`).
- `owner/repo@ref` syncs one repository at the specified ref.
- Separate multiple targets with commas or newlines. Do not combine an owner
  target with one of its repositories, because overlapping targets are rejected.

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
     top-level `status`. If it is `running`, retain its `source_id` and
     `job_id`, then call `get_sync_status(source_id=..., job_id=...)` and read
     the exact `job.status` until it becomes `succeeded` or `failed`. Continue
     only after `succeeded`, and use the same paced, bounded observation policy
     described below even for this single target. An immediate `failed`
     response uses `error_message`, while an `error` response uses `message`;
     after exact-job observation ends in `failed`, inspect
     `job.error_message`.
   - For all public configured sources, call `sync_all()` once. It starts new
     jobs or reuses already-running jobs, then returns after the launch
     decisions without waiting for ingestion to finish. Check each
     `results[].launch_outcome`: `started` means a new job was launched,
     `already_running` means an existing job was reused, and `skipped` or
     `failed` means that source did not start. Retain the pair
     `{source_id, job_id}` from `results[].source_id` and
     `results[].job.job_id` only for `started` and `already_running` results.
     Report `skipped` and `failed` launches immediately and do not poll them.
     If a retained launch has no job ID, report that it cannot be observed
     exactly; do not fall back to a newer latest job.
   - For each retained target, the MCP client or agent must make a short,
     separate `get_sync_status(source_id=..., job_id=...)` request and read the
     exact `job`, never `latest_job`. Stop that target when `job.status` is
     `succeeded` or `failed`. Start with a 2-second interval and use capped
     backoff such as 2, 4, 8, then at most 10 seconds between observation
     rounds. Use one overall 5-minute observation deadline for the batch,
     measured from the start of completion observation after `sync_all()`
     returns.
   - Stop observing a target after three consecutive status errors or responses
     with no exact `job`; a successful exact-job response resets that target's
     consecutive error count. Report the observation problem without
     substituting the source's newer `latest_job`. At the 5-minute deadline,
     report every still-running `{source_id, job_id}` without marking it failed
     or cancelling it. The background sync continues, and observation can
     resume later with the same exact IDs.
   - ContextWiki does not automatically schedule later status calls or push a
     completion notification. The `sync_all()` top-level status describes
     launch acceptance (`accepted`, `partial`, or `failed`), not sync
     completion. Calling `get_sync_status(source_id)` without `job_id` still
     returns that source's latest job, and omitting both arguments still returns
     all sources; those modes are for current-state inspection, not exact
     attribution to the `sync_all()` launch.
2. Search successfully refreshed sources with `search_context()` or
   `search_documents()`, or browse them without a query using
   `list_documents()`.

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
| `Invalid GitHub target` or `Invalid GitHub repository spec` | Use a bare `owner`, `owner/repo`, or `owner/repo@ref`; separate multiple targets with commas or newlines |
| `Duplicate GitHub repository spec` | Remove overlapping targets. Do not combine an owner with an exact target for a repository that owner discovery returns; an exact ref does not override the discovered ref |
| GitHub target changes are not reflected in Claude Desktop | Fully quit and restart Claude Desktop after editing its environment or `.env`, run `sync_source("source_github")`, retain its returned `source_id` and `job_id`, then poll the exact job with `get_sync_status(source_id=..., job_id=...)` using the bounded policy above |
| Obsidian not working in Docker | Set both the volume mount and `CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/vault` |
| Source still disabled after config change | Fully restart the MCP client — a chat refresh is not enough |
| A sync failed | For current latest-source inspection, call `get_sync_status("source_notion")`, replacing the source ID as needed. For a job launched in this conversation, use its retained `source_id` and `job_id` with exact-job status instead |

---

## ✅ Verification

```bash
./scripts/demo.sh                           # Run the local sample flow
./scripts/demo.sh --query "your question"   # Run it with a custom query
./scripts/verify_all.sh                     # Run the full developer checks
```

`demo.sh` needs no credentials. It uses the bundled Obsidian sample vault,
temporary SQLite and Chroma storage, and mock embeddings. `verify_all.sh` runs
the full checks used after code changes. Automated verification does not run a
live GitHub owner sync; it uses fake GitHub responses and temporary stores.

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
