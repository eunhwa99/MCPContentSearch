# MCP Content Search

MCP Content Search is an MCP-based content indexing and search server built with LlamaIndex, ChromaDB, and a custom tool API.

## ✨ Features

- Content Indexing: Automatically builds and maintains a vector index using Chroma and LlamaIndex.
- Semantic Search: Provides high-quality retrieval over indexed content.
- Tool Registration: Exposes indexing and search functionalities through MCP tools.
- Configurable Environment: Uses an external configuration system and modular architecture for flexibility.

## Architecture

- **FastMCP** server as the core runtime
- **ChromaVectorStore** for vector embedding storage
- **LlamaIndex StorageContext** for managing index state
- **ContentIndexer** for ingesting and updating indexed data
- **SearchService** for semantic and hybrid search
- Tool binding layer exposing indexing/search via MCP

## Directory Structure

```
mcp-content-search/
│
├── environments/
│   ├── config.py             # AppConfig, NotionConfig, setup_chroma
│   └── token.py              # load environment variables
│
├── core/
│   ├── models.py
│   └── utils.py
│
├── indexing/
│   ├── converter.py          # DocumentConverter
│   ├── manager.py            # IndexManager
│   └── indexer.py            # ContentIndexer
│
├── fetching/
│   ├── notion.py             # NotionAPIClient, NotionPageProcessor
│   ├── tistory.py            # TistoryPostExtractor, fetch_post
│   └── fetcher.py            # DocumentFetcher
│
├── search/
│   └── service.py            # SearchService
│
├── api/
│   └── tools.py              # register_tools, MCP tools
│
├── main.py
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

| File         | Description             | Key Components                           |
| ------------ | ----------------------- | ---------------------------------------- |
| `notion.py`  | Notion integration      | `NotionAPIClient`, `NotionPageProcessor` |
| `tistory.py` | Tistory blog crawler    | `TistoryPostExtractor`, `fetch_post()`   |
| `fetcher.py` | Unified fetch interface | `DocumentFetcher`                        |

---

## 🔍 `search/` — Search Service

| File         | Description     | Key Components  |
| ------------ | --------------- | --------------- |
| `service.py` | Semantic search | `SearchService` |

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

# 🔄 Dependency Flow

```
main.py
  ↓
  ├─→ environments/config.py   (load configs)
  ├─→ indexing/indexer.py      (create ContentIndexer)
  ├─→ search/service.py        (create SearchService)
  └─→ api/tools.py             (register MCP tools)
        ↓
        ├─→ fetching/fetcher.py
        │     ├─→ fetching/notion.py
        │     └─→ fetching/tistory.py
        ├─→ indexing/indexer.py
        │     ├─→ indexing/manager.py
        │     └─→ indexing/converter.py
        └─→ search/service.py
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
