# ContextWiki

[![CI](https://github.com/eunhwa99/MCPContentSearch/actions/workflows/ci.yml/badge.svg)](https://github.com/eunhwa99/MCPContentSearch/actions/workflows/ci.yml)

A **private knowledge retrieval MCP server** for LLM clients. It syncs
configured sources from Notion, Tistory, GitHub, and Obsidian into local
vector and metadata stores, then returns verified, citation-backed context.

---

## Architecture

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

## MCP Tools

| Tool | Description |
|------|-------------|
| `list_sources()` | List configured sources. |
| `sync_source(source_id)` | Sync a specific source. |
| `sync_all()` | Sync all configured sources in one pass. |
| `get_sync_status(source_id="")` | Get source and sync job status. |
| `search_context(query, ...)` | Semantic search with SQLite-validated chunks. |
| `search_documents(query, ...)` | Search results grouped by document. |
| `fetch_context(document_id="", chunk_id="")` | Fetch a specific document or chunk directly; provide at least one input. |
| `answer_with_citations(question, ...)` | Evidence-aware answer preview for preview/debug/eval use. |

> In production, use `search_context` to gather grounded evidence and use
> `search_documents` for document discovery, then let a downstream LLM generate
> the final answer.

When validating `answer_with_citations`, inspect `citations`, `used_chunks`,
and, when the request reaches a configured answer-service path with
`include_debug=True`, `debug` or `debug_markdown`.

---

## Quick Start

**Prerequisites:** Python `3.13`, [`uv`](https://docs.astral.sh/uv/)

Use this path if you want to run ContextWiki directly with local Python and
`uv`.

```bash
uv sync --locked
cp .env.example .env
uv run --locked python main.py
```

**Syntax check only (no external services):**

```bash
python -m compileall api core environments fetching indexing search storage main.py
```

### Docker

For Docker-only usage, you need Docker Desktop or Docker Engine instead of the
local Python + `uv` setup above.

The command below is the minimum Docker run example. It is enough if you are
not using the Obsidian source.

If your `.env` already contains a host path such as
`CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/absolute/path/to/your/vault`, remove it or
override it before using this minimum Docker path unless you are also mounting
that vault into the container.

```bash
docker build -t contextwiki .
cp .env.example .env
docker run --rm -i \
  --env-file .env \
  -v contextwiki_data:/home/appuser/.mcp_content_search \
  contextwiki
```

Because ContextWiki is a stdio MCP server rather than a long-running HTTP API,
`docker run -d ...` is not the normal integration path.

If you also want `source_obsidian` inside Docker, add a vault mount and point
`CONTEXTWIKI_OBSIDIAN_VAULT_PATH` at the container path:

```bash
docker run --rm -i \
  --env-file .env \
  -v contextwiki_data:/home/appuser/.mcp_content_search \
  -v "/absolute/path/to/your/vault:/vault:ro" \
  contextwiki
```

Then set this in `.env` for the Docker run:

```bash
CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/vault
```

---

## Configuration

### Source Connectors

| Source | Source ID | Required Env Var |
|--------|-----------|------------------|
| Notion | `source_notion` | `NOTION_API_KEY` |
| Tistory | `source_tistory` | `TISTORY_BLOG_NAME` |
| GitHub | `source_github` | `CONTEXTWIKI_GITHUB_REPOSITORIES` |
| Obsidian | `source_obsidian` | `CONTEXTWIKI_OBSIDIAN_VAULT_PATH` |

**GitHub example**

```bash
CONTEXTWIKI_GITHUB_REPOSITORIES=eunhwa99/MCPContentSearch@main
CONTEXTWIKI_GITHUB_DEFAULT_REF=main
CONTEXTWIKI_GITHUB_MAX_FILES=200
CONTEXTWIKI_GITHUB_MAX_FILE_BYTES=512000
CONTEXTWIKI_GITHUB_USER_AGENT=ContextWikiBot/0.1 (+https://github.com/eunhwa99/MCPContentSearch)
GITHUB_TOKEN=...
```

Important GitHub notes:

- Use `owner/repo` or `owner/repo@ref`.
- Do not wrap `CONTEXTWIKI_GITHUB_REPOSITORIES` in quotes in `.env`.
- `GITHUB_TOKEN` is optional, but unauthenticated access is more rate-limited.

**Tistory example**

```bash
TISTORY_BLOG_NAME=devlog
```

Use the blog subdomain only, not the full URL. For example, use `devlog`, not
`https://devlog.tistory.com`.

**Obsidian example**

```bash
CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/absolute/path/to/your/vault
CONTEXTWIKI_OBSIDIAN_MAX_FILES=2000
CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES=512000
```

Use the vault root path, not an individual `.md` file path.

### Search Query Rewrite (Optional)

Rewrites weak queries via an external LLM. Disabled by default.

```bash
CONTEXTWIKI_SEARCH_LLM_ENABLED=true
CONTEXTWIKI_SEARCH_LLM_PROVIDER=openai
CONTEXTWIKI_SEARCH_LLM_MODEL=gpt-4.1-mini
OPENAI_API_KEY=...
```

> The default embedding path also usually requires `OPENAI_API_KEY` for real
> sync and search runs.

---

## Usage

ContextWiki runs as a local stdio MCP server. Your MCP client spawns the
server process and communicates with it over stdin/stdout.

### Recommended: Claude Desktop via local `uv`

This is the easiest setup path on macOS because:

- Claude Desktop can spawn the server directly.
- ContextWiki can load `.env` at startup when launched from the repo root or
  via `uv --directory ...`.
- Obsidian can use your real host vault path directly.
- You do not need Docker mounts for the vault.

Do this in order:

1. Create `.env` in your repo root.
2. Put your real values there.
3. Add the MCP entry below to Claude Desktop.
4. Fully restart Claude Desktop.
5. In a fresh Claude Desktop chat, ask it to call `list_sources()`.

Example `.env`:

```bash
OPENAI_API_KEY=...
NOTION_API_KEY=...
TISTORY_BLOG_NAME=devlog
CONTEXTWIKI_GITHUB_REPOSITORIES=eunhwa99/MCPContentSearch
CONTEXTWIKI_GITHUB_DEFAULT_REF=main
CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/absolute/path/to/your/vault
```

On macOS, add this to
`~/Library/Application Support/Claude/claude_desktop_config.json`:

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

On macOS, find your `uv` path with:

```bash
which uv
```

You should not need to start `uv run python main.py` manually first. Claude
Desktop should launch the configured command automatically when it needs the
MCP server.

### Claude Desktop via Docker

Use this only if you specifically want the server to run inside Docker.
Build the image first so the `contextwiki:latest` tag exists locally:

```bash
docker build -t contextwiki .
```

If you are not using Obsidian, you can omit the vault mount below.
If you are using Obsidian in Docker, keep both the mount and the `/vault`
environment setting shown after the JSON example.
If your `.env` still contains a host-only
`CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/absolute/path/to/your/vault` from the local
`uv` setup, remove it or override it before using the no-Obsidian Docker
client path below.
On macOS, prefer the absolute Docker binary path from `which docker` if Claude
Desktop cannot find the `docker` command from its GUI environment.

```json
{
  "mcpServers": {
    "content-search-server": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "--env-file",
        "/absolute/path/to/MCPContentSearch/.env",
        "-v",
        "contextwiki_data:/home/appuser/.mcp_content_search",
        "contextwiki:latest"
      ]
    }
  }
}
```

If you want Obsidian in the Docker-spawned client path, add this mount to the
JSON `args` list before `contextwiki:latest`:

```json
"-v",
"/absolute/path/to/your/vault:/vault:ro"
```

Then set this in `.env`:

```bash
CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/vault
```

Why both the mount and the env var matter:

- `-v /absolute/path/to/your/vault:/vault:ro` exposes your host vault inside
  the container.
- `CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/vault` tells the app where that vault
  lives inside the container.

If you leave `CONTEXTWIKI_OBSIDIAN_VAULT_PATH` set to a host path like
`/Users/...` while using Docker, the container will not be able to read it.

### Cursor

Add to `.cursor/mcp.json` in your project root:

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

### After connecting

1. Ask the AI to call `sync_all()` or `sync_source("source_notion")`.
2. Once synced, ask the AI to use `search_context()` or `search_documents()`
   against your indexed sources.

### Claude Desktop Client Workflow Example

Example prompt:

```text
find my projects about DynamoDB and organize it with STAR method. Answer in English
```

This screenshot shows a Claude Desktop client workflow that uses ContextWiki as
the retrieval backend. The final STAR-form prose is Claude output built on top
of retrieved notes, not a direct server-side answer format.

![Claude Desktop using ContextWiki MCP as a retrieval backend before Claude composes the final STAR-style response](docs/images/claude-desktop-dynamodb-star-example.png)

### Troubleshooting

**If Claude cannot discover the MCP server**

- Recheck your Claude Desktop MCP config file. On macOS, the default path is
  `~/Library/Application Support/Claude/claude_desktop_config.json`.
- Fully restart Claude Desktop after any config change.
- If needed, run the configured `command` and `args` manually outside Claude to
  verify they start successfully.

**If Claude only works after you start the server manually**

- Claude Desktop is probably failing to launch the configured command.
- Test the exact `command` and `args` outside Claude first.

**If GitHub source startup fails with `Invalid GitHub repository spec`**

- Remove quotes from `CONTEXTWIKI_GITHUB_REPOSITORIES` in `.env`.
- Use `owner/repo` or `owner/repo@ref`, not a full GitHub URL.

**If Obsidian works locally but not in Docker**

- Make sure you both mounted the host vault and set
  `CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/vault`.
- The Docker mount path and the app env path are intentionally different.

**If a source still appears disabled after config changes**

- Fully restart the MCP client after updating config.
- In Claude Desktop, a chat refresh alone is often not enough.

**If `sync_all()` takes too long**

- Sync sources individually with `sync_source("source_notion")`,
  `sync_source("source_github")`, and so on.
- GitHub sync can be slower for larger repositories.

---

## Demo

Run the full retained flow against the bundled sample vault:

```bash
./scripts/demo.sh
```

- Uses temporary SQLite and Chroma state
- Uses `MockEmbedding`
- Requires no Notion, Tistory, GitHub, or Obsidian credentials

The default demo transcript is the canonical portfolio path: the same question
is used for retrieval and helper answer preview.

The keyless bundled-sample-vault path above is the safer first-run path for reviewers, and it is the only documented
path here that is intentionally keyless.

```bash
./scripts/demo.sh --query "Why does ContextWiki validate citations through SQLite?"
./scripts/demo.sh --json
```

If you override both `--query` and `--question` with different values, treat the
output as separate probes rather than one validated end-to-end chain. The
default transcript is intentionally aligned so reviewers do not over-read a
split-input run as a stronger product guarantee than it is.

---

## Verification

Verification layers are intentionally split:

- Public MCP contract layer
  Real `FastMCP.call_tool(...)` payload checks for retained public tools.
- Deterministic functional E2E layer
  Retained sync/search/fetch/answer flow checks over temp or local state.
- Deterministic quality eval layer
  Retrieval and answer quality checks through `tests/evals` and
  `scripts/run_contextwiki_eval.py`.
- Manual live smoke layer
  Optional local configured-runtime diagnostics only.

No retained automated pytest currently uses the `live` marker.
`tests/scripts/test_live_query_smoke.py` only verifies the CLI contract for the
manual smoke script, not live external source behavior.

If you only want a quick product-flow check, run the aligned same-input smoke
path:

```bash
./scripts/demo.sh
```

This is the aligned same-input smoke path, and the transcript explicitly labels the output as
separate probes so reviewers do not mistake it for one validated chain.

If you changed code and want the main local verification gate, run:

```bash
./scripts/verify_all.sh
```

All live-smoke output should be treated as local diagnostic data, not as a
retained deterministic test artifact.

---

## Project Structure

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

## Additional Docs

- [Architecture](.agents/docs/architecture.md)
