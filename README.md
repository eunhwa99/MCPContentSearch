# ContextWiki

ContextWiki is an MCP-first backend for source-grounded retrieval and citation-based answers.
It indexes configured sources, stores chunk-level evidence metadata, and answers using only retrieved context.

## What This Project Does

- Multi-source ingestion and search: Notion, Tistory, GitHub, and web/docs URLs
- Source-level sync metadata with SQLite and indexed evidence metadata in Chroma/LlamaIndex
- Citation-aware retrieval and answer APIs (`answer_with_citations`)
- Source-grounded Local Web Console for inspection and debugging
- Deterministic local verification paths (compile checks, functional E2E, smoke tests, evals)

## MCP Tools

### Legacy Tools
- `search_content(query, n_results=10)`
- `search_notion(query, n_results=10)`
- `search_tistory(query, n_results=10)`
- `search_github(query, n_results=10)`
- `trigger_index_all_content()`
- `get_index_status()`

### ContextWiki Tools
- `list_sources()`
- `sync_source(source_id)`
- `get_sync_status(source_id="")`
- `search_context(query, filters=None, top_k=10)`
- `fetch_context(document_id="", chunk_id="")`
- `answer_with_citations(question, filters=None, top_k=5)`
- `generate_wiki_page(topic, filters=None, top_k=8)`

## Quick Start (3 Minutes)

```bash
# 1) Install dependencies
uv sync --python 3.13

# 2) Start MCP server
uv run --python 3.13 python main.py

# 3) Start local web console
uv run --python 3.13 uvicorn web_console.app:create_default_app --factory \
  --host 127.0.0.1 --port 8765
```

Open: `http://127.0.0.1:8765`

## Daily Workflow Checklist

1. Check health: `GET /api/health`
2. In web console, verify sync state in `Sources`
3. Sync a source: `POST /api/sources/{source_id}/sync`
4. Ask a question: `POST /api/answer` or via console
5. Confirm evidence in response: citations, sources, used chunks

## Core Environment Variables

### Source Configuration
- `CONTEXTWIKI_GITHUB_REPOSITORIES`: Comma-separated `owner/repo` entries, optional `@ref`
- `GITHUB_TOKEN`: GitHub API auth (recommended)
- `CONTEXTWIKI_WEB_URLS`: Seed URLs for website/docs crawling
- `CONTEXTWIKI_AUTO_SYNC_SOURCES`: Source IDs to auto-sync at startup

### Feature Toggles
- `CONTEXTWIKI_SEARCH_LLM_ENABLED`: Enable search-quality LLM enhancement
- `CONTEXTWIKI_WIKI_LLM_ENABLED`: Enable LLM-assisted wiki synthesis (default: off)
- `CONTEXTWIKI_WIKI_LLM_PROVIDER`, `CONTEXTWIKI_WIKI_LLM_MODEL`
- `OPENAI_API_KEY`: Required for configured provider usage

## Common API Endpoints

- `GET /api/health`
- `GET /api/sources`, `GET /api/sources/{source_id}/sync-status`
- `POST /api/sources/{source_id}/sync`
- `POST /api/targets/sync`
- `POST /api/answer`, `POST /api/answer/codex`

## Verification Commands

```bash
# full local gate
./scripts/verify_all.sh

# core functional checks
./scripts/verify_functional_e2e.sh
uv run pytest -q
uv run pytest -m "not live"

# deterministic evals and smoke
PYTHONPATH=. python scripts/run_contextwiki_eval.py
python scripts/smoke_generate_wiki_page.py --mode fake
python scripts/smoke_web_console_playwright.py
```

## Project Structure

- `core/`: Shared models and utilities
- `fetching/`: Source connectors (Notion, Tistory, GitHub, web/docs)
- `indexing/`: Parsing, chunking, incremental indexing
- `search/`: Retrieval, ranking, citation answer services
- `storage/`: SQLite metadata (sources, jobs, documents, chunks)
- `wiki/`: Auto wiki generation
- `api/`, `web_console/`, `scripts/`, `tests/`

## Demo

<img width="800" height="1000" alt="Image" src="https://github.com/user-attachments/assets/b256eb1e-9126-4778-94a8-dda4ff807e0f" />

### Enough local results in DB (`found 3 results in local DB`)

<img width="1000" height="140" alt="Image" src="https://github.com/user-attachments/assets/79c20cf1-daaa-4954-b1b0-a47aecff7125" />

### Web fallback path (`Insufficient results (2/3), searching web...`)

<img width="1232" height="194" alt="Image" src="https://github.com/user-attachments/assets/aa6f0291-a572-4488-9d7a-119dccdc52c3" />

<img width="1352" height="118" alt="Image" src="https://github.com/user-attachments/assets/ec54b53e-126f-4241-b979-04938aeaae7f" />

## Additional Docs

- [ContextWiki Core Understanding](docs/contextwiki-core-understanding.md)
- [Harness and Workflow](.agents/docs/harness-engineering.md)
- [Architecture](.agents/docs/architecture.md)
- [Plan log](docs/plan/)
- [GitHub workflow policy](.agents/docs/github-workflow.md)
- [ADRs](.agents/docs/adr/)
