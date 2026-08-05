# ContextZip

[![CI](https://github.com/eunaverse/MCPContentSearch/actions/workflows/ci.yml/badge.svg)](https://github.com/eunaverse/MCPContentSearch/actions/workflows/ci.yml)

**Self-hosted knowledge retrieval MCP server.** Syncs Notion · Tistory · GitHub · Obsidian into vector + metadata stores and returns citation-backed context.

---

## Architecture

```text
[ MCP client ]                         [ macOS LaunchAgent / Docker worker ]
       |                                        |
       v                                        v
[ FastMCP server ] -- enqueue --> [ SQLite jobs ] <-- claim -- [ Sync worker ]
       |                                |                         |
       v                                v                         v
 [ Search tools ] <-- SQLite active gate -- [ Chroma candidates + SQLite lifecycle ]
```

Two processes: **FastMCP enqueues jobs**; the **LaunchAgent or Docker worker claims and runs them**. Details: [Architecture](.agents/docs/architecture.md).

---

## MCP Tools

| Tool | Purpose |
|------|---------|
| `list_sources()` | List configured sources and state |
| `sync_source(source_id)` | Enqueue one source job (or return existing queued/running job) |
| `sync_all()` | Enqueue all configured sources; report each launch result immediately |
| `get_sync_status(source_id="", job_id="")` | Exact job (both IDs), latest for one source, or all sources |
| `search_context(query, ...)` | Citation-ready chunks after SQLite active-gate validation |
| `search_documents(query, ...)` | One result per matching document (relevance or date sort) |
| `list_documents(...)` | Browse active docs by date (no semantic query; cursor pagination) |
| `fetch_context(document_id="", chunk_id="")` | Fetch stored document/chunks by ID |

| Source | `source_id` |
|--------|-------------|
| Notion | `source_notion` |
| Tistory | `source_tistory` |
| GitHub | `source_github` |
| Obsidian | `source_obsidian` |

**Example prompts**

| User asks | Tool |
|-----------|------|
| Which sources are connected? | `list_sources()` |
| Refresh Notion | `sync_source("source_notion")` |
| Refresh everything | `sync_all()`, then exact-job `get_sync_status` |
| Find evidence on X | `search_context(...)` |
| Show each matching document about X | `search_documents(...)` |
| Newest docs from July (by index time) | `list_documents(filters={"indexed_from": "2026-07-01"}, sort_by="indexed_at", sort_order="desc")` |
| Show docs by publication date | `list_documents(sort_by="published_at", sort_order="desc")` |
| Open the document / chunk you just found | `fetch_context(document_id="...")` or `fetch_context(chunk_id="...")` |

---

## Quick Start

**Prerequisites:** Python `3.13`, [`uv`](https://docs.astral.sh/uv/)

```bash
uv sync --locked
cp .env.example .env
./scripts/install_sync_worker_launch_agent.sh   # macOS: background sync worker
uv run --locked python main.py
```

This starts the FastMCP process. The LaunchAgent worker claims queued sync jobs. Without a worker, jobs stay `queued`. (Dev alternative: run `uv run --locked python -m indexing.sync_worker` in another terminal — see [Durable sync worker](#durable-sync-worker).)

**Docker** (MCP stdio + separate worker; share `.env` and the same named volume):

```bash
docker build -t context-zip .
cp .env.example .env
docker run --rm -i --env-file .env \
  -v context-zip_data:/home/appuser/.context-zip context-zip

docker run -d --name context-zip-sync-worker --restart unless-stopped \
  --log-driver local --log-opt max-size=5m --log-opt max-file=3 \
  --env-file .env -v context-zip_data:/home/appuser/.context-zip \
  context-zip /app/.venv/bin/python -m indexing.sync_worker
```

For Obsidian in Docker: `-v "/path/to/vault:/vault:ro"` and `CONTEXTZIP_OBSIDIAN_VAULT_PATH=/vault`.

---

## Configuration

| Source | Enable with |
|--------|-------------|
| Notion (`source_notion`) | `NOTION_API_KEY` |
| Tistory (`source_tistory`) | `TISTORY_BLOG_NAME` |
| GitHub (`source_github`) | `CONTEXTZIP_GITHUB_REPOSITORIES` |
| Obsidian (`source_obsidian`) | `CONTEXTZIP_OBSIDIAN_VAULT_PATH` |

Default embeddings need **`OPENAI_API_KEY`** (indexing/search may send text to OpenAI). Empty enable vars leave a source disabled.

```bash
OPENAI_API_KEY=...

NOTION_API_KEY=...
TISTORY_BLOG_NAME=devlog          # subdomain only, not full URL

CONTEXTZIP_GITHUB_REPOSITORIES=eunaverse
# or: eunaverse/context-zip,eunaverse/website@main
GITHUB_TOKEN=...                  # private repos / higher rate limits

CONTEXTZIP_OBSIDIAN_VAULT_PATH=/absolute/path/to/vault
```

GitHub targets (comma/newline separated): `owner` = that account’s visible repos on each default branch; `owner/repo` = one repo at `CONTEXTZIP_GITHUB_DEFAULT_REF` (default `main`); `owner/repo@ref` = one repo at that ref. Do not list an `owner` together with one of its repos — overlapping targets are rejected.

---

## Client setup

### Claude Desktop (local uv)

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "content-search-server": {
      "command": "/absolute/path/to/uv",
      "args": [
        "--directory", "/absolute/path/to/context-zip",
        "run", "--python", "3.13", "python", "main.py"
      ]
    }
  }
}
```

Use `which uv` for the path. Put secrets in the repo `.env` (not plaintext in the config). If you previously set `OPENAI_API_KEY`, `NOTION_API_KEY`, `GITHUB_TOKEN`, or other source env vars in `claude_desktop_config.json` or a parent shell, clear those stale values too — leftover client/shell env bypasses `.env`. Both FastMCP and the durable worker snapshot source configuration at process startup, so fully restart the MCP client and run `./scripts/restart_sync_worker_launch_agent.sh` after `.env` or source-target changes.

### Claude Desktop (Docker)

```json
{
  "mcpServers": {
    "content-search-server": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "--env-file", "/absolute/path/to/context-zip/.env",
        "-v", "context-zip_data:/home/appuser/.context-zip",
        "context-zip:latest"
      ]
    }
  }
}
```

Keep the separate `context-zip-sync-worker` container running (Quick Start). After `.env` edits, restart the MCP client and **recreate** the worker — `--env-file` is applied at `docker run` time, so `docker restart` keeps the old environment:

```bash
docker stop context-zip-sync-worker && docker rm context-zip-sync-worker
docker run -d --name context-zip-sync-worker --restart unless-stopped \
  --log-driver local --log-opt max-size=5m --log-opt max-file=3 \
  --env-file .env -v context-zip_data:/home/appuser/.context-zip \
  context-zip /app/.venv/bin/python -m indexing.sync_worker
```

After an intentional `docker stop` with no `.env` change, `docker start context-zip-sync-worker` is enough (it stays stopped until you start it).

### Cursor

Add the same local uv block to `.cursor/mcp.json`.

---

## After connecting

1. Sync one source (`sync_source`) or all (`sync_all`).
2. Poll `get_sync_status` with the returned `source_id` + `job_id` until it finishes.
3. Search or browse with `search_context`, `search_documents`, or `list_documents`.

**Example prompt:**
```text
find my projects about DynamoDB and organize it with STAR method. Answer in English
```

![Claude Desktop using ContextZip MCP as a retrieval backend before Claude composes the final STAR-style response](docs/images/claude-desktop-dynamodb-star-example.png)

---

## Durable sync worker

`main.py` alone starts the MCP server. Sync jobs still need a **separate worker** that claims and runs them.

**Usual setup on macOS:** install the LaunchAgent once.

```bash
./scripts/install_sync_worker_launch_agent.sh --dry-run   # optional path preview
./scripts/install_sync_worker_launch_agent.sh             # install + start
```

Use the others only when needed:

```bash
./scripts/status_sync_worker_launch_agent.sh              # is it running?
./scripts/restart_sync_worker_launch_agent.sh             # after .env / source-target changes
./scripts/uninstall_sync_worker_launch_agent.sh           # remove it
```

Logs: `~/.context-zip/logs/sync-worker.log` (and `sync-worker-startup.log`).

**Foreground (dev):** run the same worker in a terminal instead of LaunchAgent. Closing the terminal stops it.

```bash
uv run --locked python -m indexing.sync_worker
```

With Docker, the Quick Start `context-zip-sync-worker` container is this worker.

The worker may run up to `N` distinct-source jobs at once. Set
`CONTEXTZIP_SYNC_WORKER_MAX_CONCURRENT` in the repository-local `.env`
(integer `1`–`8`; default `2`). Invalid values fail closed at worker startup.
`1` restores global single-flight. `N` bounds SQLite `RUNNING` claims;
connector fetch can overlap inside one worker, and Chroma mutations are
serialized within that process only. Run one LaunchAgent sync_worker process
per store — extra worker PIDs can oversubscribe writes. Restart the LaunchAgent
worker after changing the value.

Closing the MCP client does not stop an in-flight sync if the worker is still up. Stopping the worker fails every in-flight claimed job; request a fresh sync afterward.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| MCP not discovered | Recheck config path; fully restart the client |
| Only works after manual start | Run `command` + `args` in a terminal to see errors |
| Invalid / duplicate GitHub target | Use `owner`, `owner/repo`, or `owner/repo@ref`; no overlapping owner+repo |
| Sync stays `queued` | LaunchAgent: `./scripts/status_sync_worker_launch_agent.sh` then install/restart. Docker: `docker start` (or recreate if `.env` changed) |
| Worker exits repeatedly | LaunchAgent: check `sync-worker-startup.log`, then `sync-worker.log`; `--dry-run` paths. Docker: `docker logs context-zip-sync-worker` |
| MCP stopped but sync still `running` | Expected — LaunchAgent/Docker worker owns it |
| Source still disabled after `.env` change | Restart MCP client **and** worker. LaunchAgent: restart script. Docker: recreate the worker with the same `docker run ... --env-file` (not `docker restart`) |
| Worker stopped mid-sync | Restart/recreate worker; wait until orphaned job is `failed`; enqueue a fresh sync |
| Sync failed | Exact-job `get_sync_status(source_id, job_id)` for retained IDs; else latest by `source_id` |
| Obsidian in Docker | Mount vault **and** set `CONTEXTZIP_OBSIDIAN_VAULT_PATH=/vault` |

Do not paste `.env`, tokens, or indexed content into diagnostics.

---

## Verification

```bash
./scripts/demo.sh                           # Run the local sample flow
./scripts/demo.sh --query "your question"
./scripts/verify_all.sh                     # full developer checks
```

`demo.sh` needs no credentials. It uses the bundled Obsidian sample vault, temporary SQLite and Chroma storage, and mock embeddings. Automated verification does not run live GitHub owner sync.

---

## Project structure

```text
main.py          FastMCP server entry point
api/             MCP tool handlers
core/            Shared models, exceptions, utilities
deploy/launchd/  macOS LaunchAgent template
environments/    Env var and secret loading
fetching/        Source connectors
indexing/        Chunking, sync worker, dedup, Chroma indexing
search/          Retrieval, ranking, active gate, citations
storage/         SQLite lifecycle metadata
tests/, scripts/ Verification harnesses and utilities
```
