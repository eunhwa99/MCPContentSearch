# ContextWiki

ContextWiki is an MCP-first knowledge backend that indexes personal/work knowledge sources and lets AI agents search, fetch, and answer with citations. It evolves the original MCP Content Search project into a production-oriented backend with source sync state, incremental ingestion, citation metadata, and deterministic verification.

## ✨ Features

- Dynamic auto-fallback search (Local DB ➝ Web ➝ Auto-index)
- Vector-based semantic search via LlamaIndex + ChromaDB
- Real-time web search for Notion & Tistory
- HTML crawling for sites without APIs
- MCP tool exposure for seamless integration with AI clients
- Source metadata for Notion and Tistory
- Incremental source sync with per-job status
- SQLite metadata store for sources, jobs, documents, and citation chunks
- Citation-oriented context search and fetch
- Grounded answer generation that returns insufficient evidence instead of unsupported claims
- GitHub repository ingestion with stable file identities, blob version metadata, and code line citations
- Website/docs ingestion with bounded crawling, sitemap support, robots.txt disallow handling, and canonical URL citations

## 🛠️ MCP Tools

- search_content — Dynamic search (local → web)
- search_notion — Forced Notion-only search
- search_tistory — Forced Tistory-only search
- trigger_index_all_content — Run full indexing in background
- get_index_status — Check indexing progress
- list_sources — List configured ContextWiki sources
- sync_source — Run incremental sync for one source
- get_sync_status — Check source/job sync status
- search_context — Return citation-ready structured context
- fetch_context — Fetch a document or chunk by id
- answer_with_citations — Answer only from retrieved chunks and include citations

Phase B source ids:

- `source_github` — configured with `CONTEXTWIKI_GITHUB_REPOSITORIES`
- `source_web` — configured with `CONTEXTWIKI_WEB_URLS`

## Directory Structure

```
mcp-content-search/
│
├── environments/
│   ├── config.py             # AppConfig, NotionConfig, setup_chroma()
│   ├── runtime_env.py        # Runtime environment lookup helpers
│   └── token.py              # API keys, environment variables
│
├── core/
│   ├── models.py             # IndexState, DocumentModel, statuses
│   └── utils.py              # ContentHasher, helpers
│
├── indexing/
│   ├── converter.py          # Convert Notion/Tistory → unified format
│   ├── manager.py            # Handles index life-cycle
│   └── indexer.py            # Index documents into Chroma
│
├── fetching/
│   ├── connectors.py         # ContextWiki source registry and source connectors
│   ├── github.py             # GitHub repository file fetcher
│   ├── web_docs.py           # Website/docs bounded crawler
│   ├── notion.py             # Notion API client + processors
│   ├── tistory.py            # Tistory RSS extractor + HTML parser
│   ├── fetcher.py            # Unified fetcher for full indexing
│   └── web_searcher.py       # Notion/Tistory real-time search
│
├── search/
│   ├── dynamic_search.py     # Local-first auto-fallback search
│   ├── context_service.py    # Citation-ready structured context search
│   ├── answer_service.py     # Citation-grounded answer generation
│   └── service.py            # Local Chroma search only
│
├── storage/
│   └── metadata_store.py     # SQLite source/job/document/chunk metadata
│
├── api/
│   └── tools.py              # MCP tool handlers (search, indexing, status)
│
├── main.py                   # Application entry point
├── requirements.txt
└── README.md

```

# 📝 Module Overview

## 🔧 `environments/` — Configuration Layer

| File        | Description          | Key Components                                |
| ----------- | -------------------- | --------------------------------------------- |
| `config.py` | Application settings | `AppConfig`, `NotionConfig`, `setup_chroma()` |
| `runtime_env.py` | Runtime environment access | `get_env_secret()` |
| `token.py`  | Env variable loader  | `NOTION_API_KEY`, `TISTORY_BLOG_NAME`, etc.   |

---

## 🎯 `core/` — Core Models & Utilities

| File        | Description       | Key Components                                    |
| ----------- | ----------------- | ------------------------------------------------- |
| `models.py` | Data structures   | `DocumentModel`, `IndexStatusModel`, `IndexState` |
| `utils.py`  | Utility functions | `ContentHasher`                                   |

---

## 📚 `indexing/` — Indexing Pipeline

| File           | Description             | Key Components      |
| -------------- | ----------------------- | ------------------- |
| `converter.py` | Document transformation | `DocumentConverter` |
| `manager.py`   | Manager for indexing    | `IndexManager`      |
| `indexer.py`   | Index content.          | `ContentIndexer`    |

---

## 🌐 `fetching/` — Data Fetching Layer

| File              | Description                                       | Key Components                                             |
| ----------------- | ------------------------------------------------- | ---------------------------------------------------------- |
| `connectors.py`   | ContextWiki source registry and source connectors | `SourceRegistry`, `GitHubSourceConnector`, `WebsiteSourceConnector` |
| `github.py`       | GitHub repository file ingestion                  | `GitHubRepositoryFetcher`, `GitHubRepositorySpec`          |
| `web_docs.py`     | Bounded website/docs crawler                      | `WebsiteDocsFetcher`, `RobotsRules`                       |
| `notion.py`       | Notion integration                                | `NotionAPIClient`, `NotionPageProcessor`, `NotionSearcher` |
| `tistory.py`      | Tistory blog crawler                              | `TistoryPostExtractor`, `TistorySearcher`                  |
| `fetcher.py`      | Unified fetch interface used for indexing         | `DocumentFetcher`                                          |
| `web_searcher.py` | Unified search interface for real-time web search | `WebSearcher`                                              |

---

## 🔍 `search/` — Search Service

| File                | Description                                                                                | Key Components         |
| ------------------- | ------------------------------------------------------------------------------------------ | ---------------------- |
| `dynamic_search.py` | Semantic search via index DB or web, After web search, the results are indexed to index DB | `DynamicSearchService` |
| `context_service.py` | Structured context search with citation metadata | `ContextSearchService` |
| `answer_service.py` | Evidence-gated citation answers | `CitationAnswerService` |
| `service.py`        | Semantic search via index DB                                                               | `SearchService`        |

---

## 🧾 `storage/` — Metadata Store

| File                | Description                                                  | Key Components    |
| ------------------- | ------------------------------------------------------------ | ----------------- |
| `metadata_store.py` | SQLite metadata for sources, sync jobs, documents, and chunks | `MetadataStore`   |

---

## 🔌 `api/` — MCP Tools Layer

| File       | Description       | Key Components                    |
| ---------- | ----------------- | --------------------------------- |
| `tools.py` | MCP tool exposure | `register_tools()`, tool handlers |

---

## 🚀 `main.py` — Application Entry Point

| Function       | Description               |
| -------------- | ------------------------- |
| `create_app()` | Initialize app components |
| `main`         | Start MCP server          |

---

# 🔄 Architecture of MCP Tools

```
(Client)
   ↓
[FastMCP]
   ↓ calls tool
[api/tools.py]
   ↓
DynamicSearchService  →  SearchService (local search)
   ↓ fallback
WebSearcher (Notion/Tistory)
   ↓
Background Indexing
   ↓
ContentIndexer → Chroma → LlamaIndex

```

---

# 🚀 Running the Project

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the MCP server:

```bash
python main.py
```

The application will:

1. Load configuration
2. Initialize Chroma vector store
3. Prepare indexing and search services
4. Register MCP tools
5. Start the server

---

# ⚙️ ContextWiki Source Configuration

Notion and Tistory keep using the existing environment variables. Phase B adds optional GitHub and website/docs sources:

| Variable | Purpose |
| --- | --- |
| `CONTEXTWIKI_GITHUB_REPOSITORIES` | Comma-separated repositories such as `owner/repo@main`. If `@ref` is omitted, `CONTEXTWIKI_GITHUB_DEFAULT_REF` is used. |
| `GITHUB_TOKEN` | Optional GitHub API token. Source metadata stores only `env:GITHUB_TOKEN`. |
| `CONTEXTWIKI_GITHUB_DEFAULT_REF` | Default Git ref for repository specs. Defaults to `main`. |
| `CONTEXTWIKI_GITHUB_MAX_FILES` | Maximum text/code files fetched per configured repository per sync. Defaults to `200`. |
| `CONTEXTWIKI_GITHUB_MAX_FILE_BYTES` | Maximum GitHub file size fetched. Defaults to `512000`. |
| `CONTEXTWIKI_WEB_URLS` | Comma-separated website/docs seed URLs or sitemap URLs. |
| `CONTEXTWIKI_WEB_MAX_PAGES` | Maximum non-sitemap document page responses fetched per website/docs sync. Defaults to `50`. |
| `CONTEXTWIKI_WEB_MAX_RESPONSE_BYTES` | Maximum response body bytes read per website/docs request. Defaults to `1048576`. |
| `CONTEXTWIKI_WEB_CRAWL_DELAY_SECONDS` | Delay between page fetches. Defaults to `0.2`. |
| `CONTEXTWIKI_WEB_USER_AGENT` | User agent for GitHub/Web connector requests. |

Run a configured Phase B sync through the existing MCP tool:

```text
sync_source("source_github")
sync_source("source_web")
```

---

# ✅ Verification

Required verification includes compile checks plus unit, integration, and fake E2E tests:

```bash
scripts/verify_all.sh
```

The required test path excludes live external API smoke tests:

```bash
uv run pytest -m "not live"
```

Live API smoke tests are optional and must be explicitly enabled:

```bash
RUN_LIVE_E2E=1 uv run pytest -m live
```

This keeps CI deterministic while still allowing manual checks against real Notion, Tistory, GitHub, or web sources before demos/releases.

---

# 📌 Notes

- Ensure all required API keys (e.g., Notion, Tistory) are set in the environment.
- ChromaDB directory is configured via `AppConfig`.
- You can extend the system by adding new data fetchers or custom MCP tools.

---

# Demo
<img width="800" height="1000" alt="Image" src="https://github.com/user-attachments/assets/b256eb1e-9126-4778-94a8-dda4ff807e0f" />

### When enough posts exist in the local index DB (**found 3 results in local DB**)

<img width="1000" height="140" alt="Image" src="https://github.com/user-attachments/assets/79c20cf1-daaa-4954-b1b0-a47aecff7125" />

### When local results are insufficient (**Insufficient results (2/3), searching web...**)

<img width="1232" height="194" alt="Image" src="https://github.com/user-attachments/assets/aa6f0291-a572-4488-9d7a-119dccdc52c3" />

<img width="1352" height="118" alt="Image" src="https://github.com/user-attachments/assets/ec54b53e-126f-4241-b979-04938aeaae7f" />
