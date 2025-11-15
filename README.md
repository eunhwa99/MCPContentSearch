# MCP Content Search

MCP Content Search is an MCP-based content indexing and search server built with LlamaIndex, ChromaDB, and a custom tool API.

## ✨ Features

- Dynamic auto-fallback search (Local DB ➝ Web ➝ Auto-index)
- Vector-based semantic search via LlamaIndex + ChromaDB
- Real-time web search for Notion & Tistory
- HTML crawling for sites without APIs
- MCP tool exposure for seamless integration with AI clients

## 🛠️ MCP Tools

- search_content — Dynamic search (local → web)
- search_notion — Forced Notion-only search
- search_tistory — Forced Tistory-only search
- trigger_index_all_content — Run full indexing in background
- get_index_status — Check indexing progress

## Directory Structure

```
mcp-content-search/
│
├── environments/
│   ├── config.py             # AppConfig, NotionConfig, setup_chroma()
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
│   ├── notion.py             # Notion API client + processors
│   ├── tistory.py            # Tistory RSS extractor + HTML parser
│   ├── fetcher.py            # Unified fetcher for full indexing
│   └── web_searcher.py       # Notion/Tistory real-time search
│
├── search/
│   ├── dynamic_search.py     # Local-first auto-fallback search
│   └── service.py            # Local Chroma search only
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
| `notion.py`       | Notion integration                                | `NotionAPIClient`, `NotionPageProcessor`, `NotionSearcher` |
| `tistory.py`      | Tistory blog crawler                              | `TistoryPostExtractor`, `TistorySearcher`                  |
| `fetcher.py`      | Unified fetch interface used for indexing         | `DocumentFetcher`                                          |
| `web_searcher.py` | Unified search interface for real-time web search | `WebSearcher`                                              |

---

## 🔍 `search/` — Search Service

| File                | Description                                                                                | Key Components         |
| ------------------- | ------------------------------------------------------------------------------------------ | ---------------------- |
| `dynamic_search.py` | Semantic search via index DB or web, After web search, the results are indexed to index DB | `DynamicSearchService` |
| `service.py`        | Semantic search via index DB                                                               | `SearchService`        |

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

# 📌 Notes

- Ensure all required API keys (e.g., Notion, Tistory) are set in the environment.
- ChromaDB directory is configured via `AppConfig`.
- You can extend the system by adding new data fetchers or custom MCP tools.

---
