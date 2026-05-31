# ContextWiki

ContextWiki is an MCP-first knowledge backend that indexes personal/work knowledge sources and lets AI agents search, fetch, and answer with citations. It evolves the original MCP Content Search project into a production-oriented backend with source sync state, incremental ingestion, citation metadata, and deterministic verification.

## ✨ Features

- Dynamic auto-fallback search (Local DB ➝ Web ➝ Auto-index)
- Vector-based semantic search via LlamaIndex + ChromaDB
- Real-time web search for Notion & Tistory
- HTML crawling for sites without APIs
- MCP tool exposure for seamless integration with AI clients
- Source metadata for Notion, Tistory, GitHub, and website/docs sources
- Incremental source sync with per-job status
- SQLite metadata store for sources, jobs, documents, and citation chunks
- Citation-oriented context search and fetch
- Evidence-gated citation answer responses that return insufficient evidence instead of unsupported claims
- GitHub repository ingestion with stable file identities, blob version metadata, and code line citations
- Website/docs ingestion with bounded crawling, sitemap support, robots.txt disallow handling, and canonical URL citations
- Read-only Auto Wiki page generation from active ContextWiki chunks with citations and backlinks
- Local-only web test console for asking indexed sources, syncing test targets, and inspecting citations
- Deterministic local answer-quality and grounding eval scaffolding for current citation answer payloads

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
- generate_wiki_page — Generate a citation-backed Markdown wiki page from indexed ContextWiki evidence

Phase B source ids:

- `source_github` — configured with `CONTEXTWIKI_GITHUB_REPOSITORIES`
- `source_web` — configured with `CONTEXTWIKI_WEB_URLS`

## 📖 Project Docs

- [`docs/contextwiki-core-understanding.md`](docs/contextwiki-core-understanding.md) — maintained learning note for explaining ContextWiki's data flow, source connectors, lifecycle metadata, retrieval gate, and current limitations.
- [`docs/plan/`](docs/plan/) — phase plans and verification logs.
- [`.agents/docs/adr/`](.agents/docs/adr/) — accepted architecture decisions.

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
│   ├── chunker.py            # Source-aware citation chunking
│   ├── background_tasks.py   # Process-local background indexing task status
│   ├── ingestion_service.py  # ContextWiki source sync and incremental indexing
│   ├── converter.py          # Convert DocumentModel → LlamaIndex document
│   ├── manager.py            # Handles index life-cycle
│   └── indexer.py            # Index documents into Chroma
│
├── fetching/
│   ├── connectors.py         # ContextWiki source registry and source connectors
│   ├── github.py             # GitHub repository file fetcher
│   ├── web_docs.py           # Website/docs bounded crawler
│   ├── web_media.py          # Web media/content classification helpers
│   ├── web_safety.py         # URL validation and credential redaction helpers
│   ├── notion.py             # Notion API client + processors
│   ├── tistory.py            # Tistory RSS extractor + HTML parser
│   ├── fetcher.py            # Unified fetcher for full indexing
│   └── web_searcher.py       # Notion/Tistory real-time search
│
├── search/
│   ├── dynamic_search.py     # Local-first auto-fallback search
│   ├── context_service.py    # Citation-ready structured context search
│   ├── answer_service.py     # Evidence-gated citation answer responses
│   └── service.py            # Local Chroma search only
│
├── wiki/
│   ├── service.py            # Read-only Auto Wiki generation over ContextWiki search results
│   └── synthesis.py          # Optional opt-in LLM wiki synthesis provider
│
├── web_console/
│   ├── app.py                # Local FastAPI wrapper for browser manual testing
│   ├── codex_cli.py          # Codex CLI runner and subprocess isolation helpers
│   └── payloads.py           # Local-console payload shaping and redaction helpers
│
├── evals/
│   ├── answer_quality.py     # Deterministic answer payload grounding checks
│   └── answer_quality_cases.json  # Local example eval cases
│
├── web/
│   ├── index.html            # Local Web Test Console shell
│   ├── app.js                # Console API calls and download helpers
│   └── styles.css            # Console styling
│
├── storage/
│   └── metadata_store.py     # SQLite source/job/document/chunk metadata
│
├── api/
│   └── tools.py              # MCP tool handlers (search, source sync, context, status)
│
├── docs/
│   ├── contextwiki-core-understanding.md  # Maintained architecture learning note
│   └── plan/                 # Harness plans and verification logs
│
├── scripts/
│   ├── smoke_generate_wiki_page.py  # FastMCP wiki generation smoke checks
│   └── verify_all.sh                # Compile + non-live test suite
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

| File                   | Description                               | Key Components      |
| ---------------------- | ----------------------------------------- | ------------------- |
| `chunker.py`           | Source-aware citation chunking             | `DocumentChunker`   |
| `ingestion_service.py` | Source sync and incremental indexing       | `IngestionService`  |
| `converter.py`         | DocumentModel to LlamaIndex document metadata | `DocumentConverter` |
| `manager.py`           | Manager for indexing                       | `IndexManager`      |
| `indexer.py`           | Index content into Chroma                  | `ContentIndexer`    |

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

## 🧭 `wiki/` — Auto Wiki Layer

| File           | Description                                     | Key Components          |
| -------------- | ----------------------------------------------- | ----------------------- |
| `service.py`   | Citation-backed wiki page generation over active ContextWiki search results | `WikiGenerationService` |
| `synthesis.py` | Optional opt-in LLM synthesis for more natural citation-backed wiki pages | `OpenAIWikiSynthesizer`, `build_wiki_synthesizer` |

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

Legacy dynamic search flow:

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

ContextWiki source and retrieval flow:

```
(Client)
   ↓
[FastMCP]
   ↓ calls tool
[api/tools.py]
   ├─ list_sources / get_sync_status → MetadataStore (SQLite)
   ├─ sync_source → IngestionService → SourceRegistry/connector
   │                   ↓
   │               MetadataStore source registration/sync guard → connector fetch → DocumentChunker
   │                                                    ↓
   │                                            ContentIndexer → Chroma
   ├─ search_context → ContextSearchService → Chroma candidates → MetadataStore validation
   ├─ fetch_context → MetadataStore document/chunk hydration
   ├─ answer_with_citations → CitationAnswerService → validated evidence
   └─ generate_wiki_page → WikiGenerationService → ContextSearchService → Markdown + citations + backlinks
```

---

# 🚀 Running the Project

Install dependencies:

```bash
uv sync --python 3.13
```

The fallback `requirements.txt` file mirrors the direct runtime dependencies in
`pyproject.toml` plus the direct dev dependency used by the test suite.

Use Python 3.13 for local development. Python 3.14 is not currently supported
because the Chroma dependency stack includes wheels, such as `onnxruntime`, that
do not publish `cp314` artifacts for the locked version. If you use the fallback
`pip install -r requirements.txt` path, run it from a Python 3.13 virtual
environment.

Start the MCP server:

```bash
uv run --python 3.13 python main.py
```

The application will:

1. Load configuration
2. Initialize Chroma vector store
3. Initialize SQLite metadata store
4. Prepare indexing, source sync, and search services
5. Register MCP tools
6. Start the server

Start the local Phase C.5 web test console:

```bash
uv run --python 3.13 uvicorn web_console.app:create_default_app --factory --host 127.0.0.1 --port 8765
```

Then open `http://127.0.0.1:8765`.

The web console is local-only developer tooling, not a production UI. The
browser screen focuses on asking questions, inspecting evidence, downloading
Markdown/JSON, and starting source sync. It serves static files from `web/` and
calls these local HTTP endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Check console availability. |
| `GET /api/sources` | List configured ContextWiki sources from SQLite metadata. |
| `GET /api/sources/{source_id}/sync-status` | Inspect one source and its latest sync job. |
| `POST /api/sources/{source_id}/sync` | Run the existing configured ContextWiki source sync. |
| `POST /api/targets/sync` | Sync a one-off target selected in the browser as GitHub, Notion, or Web URL. |
| `POST /api/github/sync` | Sync a GitHub target typed in the browser, including `owner/repo@branch`, GitHub repo URLs, or owner URLs such as `github.com/eunhwa99`. |
| `POST /api/answer` | Call `answer_with_citations` through the HTTP wrapper. |
| `POST /api/answer/codex` | Experimental local Codex CLI answer mode over retrieved ContextWiki chunks. |
| `POST /api/wiki/generate` | Script/API-only wrapper for `generate_wiki_page`; not shown in the browser UI. |
| `POST /api/smoke/fake` | Script/API-only deterministic fake wiki smoke with temporary storage. |
| `POST /api/smoke/github` | Script/API-only optional GitHub smoke, skipping gracefully when not configured. |

The Configured Sources panel can trigger sync for sources already registered by
environment config. The Target Sync control is for one-off ad hoc targets:
GitHub accepts a single repository (`owner/repo@branch`), a GitHub repository
URL, or a GitHub owner URL such as `github.com/eunhwa99`; Notion accepts a page
or database URL/id that the configured integration can read; Web URL accepts an
HTTP(S) docs/page URL. Browser target sync disables stale cleanup so partial ad
hoc targets do not tombstone previously indexed configured-source documents.
GitHub private repositories require `GITHUB_TOKEN`, and Notion targets require
the page/database to be shared with the configured `NOTION_API_KEY`
integration. The console polls sync status while work is running and displays
available document/chunk progress. If the same source is already syncing, a
one-off target sync reports `already_running` and follows the active source job
instead of claiming that the newly typed target started.

On startup, the local web console schedules configured-source sync for GitHub,
Notion, and Tistory by default (`source_github`, `source_notion`, and
`source_tistory`). This uses the existing source sync service in the background,
so server startup is not blocked and progress is still inspected through
`/api/sources` and `/api/sources/{source_id}/sync-status`. Set
`CONTEXTWIKI_AUTO_SYNC_SOURCES` to a comma/newline-separated source ID list to
override the startup set; set it to an empty string to disable startup auto-sync
entirely. Missing credentials or disabled sources are handled by the existing
sync status/error paths.

The console does not add authentication, deployment, multi-user state, or
server-side generated page persistence. It rejects non-loopback clients by
default. `CONTEXTWIKI_WEB_CONSOLE_ALLOW_REMOTE=true` only bypasses the client
IP check for explicit proxy/test experiments; `Host`, `Origin`, and `Referer`
must still resolve to loopback. Markdown downloads use rendered answer/Markdown
content instead of falling back to raw JSON, JSON panes/downloads use the
browser's sanitized result state, and HTTP smoke endpoints clean up temporary
Markdown files before returning.

The Answer mode selector defaults to Codex CLI Answer because the browser is
meant for manual local evaluation of the final answer experience. ContextWiki
is labeled as a raw debug fallback when the CLI is unavailable or when a raw
citation-gated answer scaffold is useful. Codex CLI Answer retrieves bounded
ContextWiki chunks first, redacts obvious secret-looking strings from the
prompt, and keeps citations/used chunks from the retrieval result instead of
trusting generated citation metadata.
The HTTP wrapper supplies only bounded/redacted prompt fields as evidence, starts
`codex exec` from a temporary working directory with an allowlisted environment,
`--ephemeral`, `--ignore-user-config`, and `--ignore-rules`, and disables Codex
shell/browser/computer/plugin/multi-agent and related tool feature flags for the
invocation when supported. `CONTEXTWIKI_CODEX_USE_SANDBOX_EXEC=true` can
experimentally wrap the subprocess in a macOS `sandbox-exec` profile; when that
flag is set and `sandbox-exec` is unavailable, the request fails closed with a
safe configuration error instead of silently weakening the requested hardening.
The wrapper is off by default because it can block local Codex runtimes.
Temporary output and sandbox files are deleted after the request. This is still
not an OS/container sandbox proof: `/api/answer/codex` is only a local
HTTP/subprocess wrapper, while the Codex CLI runtime may use external model
behavior and process the bounded evidence under the user's Codex configuration.
This mode is best-effort prompt isolation for local testing rather than a
production answer service. If `codex` is missing, times out, or exits
unsuccessfully, the console returns a safe failure payload and the user can
switch back to the raw ContextWiki debug fallback.

The local server boundary does not make every operation offline:
Answer/Search over the real vector index may call the configured embedding
provider during retrieval, so the server needs the same valid embedding
environment, such as `OPENAI_API_KEY`, unless a local/mock embedding model is
configured. Startup auto-sync may contact configured GitHub, Notion, and Tistory
sources by default unless `CONTEXTWIKI_AUTO_SYNC_SOURCES` is disabled or
overridden. `generate_wiki_page` follows the existing opt-in wiki LLM
configuration, Codex CLI Answer may invoke local Codex authentication and
external model behavior depending on the user's CLI setup, and smoke endpoints
plus Target Sync for GitHub, Notion, or Web URL perform live network work only
when explicitly invoked and configured.

Background status is split by subsystem today. ContextWiki source sync has
SQLite-backed source/job status through `get_sync_status` and the web console
polling endpoints. Legacy background indexing started by
`trigger_index_all_content`, `search_content`, `search_notion`, or
`search_tistory` is process-local and currently reports through
`get_index_status` plus logs; those fire-and-forget tasks do not expose durable
job ids, retry history, or per-task persisted failures yet.

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
| `CONTEXTWIKI_AUTO_SYNC_SOURCES` | Optional startup auto-sync source IDs. Defaults to `source_github,source_notion,source_tistory`; set to an empty string to disable. |
| `NOTION_API_KEY` | Canonical Notion integration token env var. The local web console also accepts compatibility aliases `NOTION_TOKEN`, `NOTION_API_TOKEN`, and `notion_token`, but source metadata still stores only `env:NOTION_API_KEY`. |
| `CONTEXTWIKI_WEB_URLS` | Comma-separated website/docs seed URLs or sitemap URLs. |
| `CONTEXTWIKI_WEB_MAX_PAGES` | Maximum non-sitemap document page responses fetched per website/docs sync. Defaults to `50`. |
| `CONTEXTWIKI_WEB_MAX_RESPONSE_BYTES` | Maximum response body bytes read per website/docs request. Defaults to `1048576`. |
| `CONTEXTWIKI_WEB_CRAWL_DELAY_SECONDS` | Delay between page fetches. Defaults to `0.2`. |
| `CONTEXTWIKI_WEB_USER_AGENT` | User agent for GitHub/Web connector requests. |

Auto Wiki can optionally use an LLM to turn citation-ready evidence into more
natural Markdown. This is disabled by default because evidence may contain
private source content.

| Variable | Purpose |
| --- | --- |
| `CONTEXTWIKI_WIKI_LLM_ENABLED` | Set to `true` to enable LLM synthesis for `generate_wiki_page`. Defaults to `false`. |
| `OPENAI_API_KEY` | OpenAI API key used by OpenAI-backed embeddings during Answer/Search retrieval, and by wiki LLM synthesis when `CONTEXTWIKI_WIKI_LLM_ENABLED=true`. |
| `CONTEXTWIKI_WIKI_LLM_MODEL` | OpenAI model for wiki synthesis. Defaults to `gpt-4.1-mini`; override as needed. |
| `CONTEXTWIKI_WIKI_LLM_TIMEOUT` | LLM request timeout in seconds. Defaults to `20`. |
| `CONTEXTWIKI_WIKI_LLM_MAX_EVIDENCE_CHARS` | Maximum characters per evidence chunk sent to the LLM. Defaults to `1200`. |

Run a configured Phase B sync through the existing MCP tool:

```text
sync_source("source_github")
sync_source("source_web")
```

---

# ✅ Verification

Required verification includes compile checks plus unit, integration, fake E2E
tests, and `node --check` for the local web console JavaScript. Install Node.js
before running the required script.

```bash
./scripts/verify_all.sh
```

Wiki-generation PRs should also run the safe FastMCP smoke. This uses a fake
source, temporary Chroma/SQLite under `/private/tmp`, actual `FastMCP`
registration, and `call_tool("generate_wiki_page", ...)`; it writes Markdown
under `/private/tmp/contextwiki-wiki-smoke` by default.

```bash
python scripts/smoke_generate_wiki_page.py --mode fake
```

The required test path excludes live external API smoke tests:

```bash
uv run pytest -q
uv run pytest -m "not live"
```

Deterministic local answer-quality eval groundwork lives under `evals/` with
focused tests under `tests/evals/`. These checks evaluate already-produced
`answer_with_citations`-style payloads for expected terms, forbidden unsupported
claims, citation coverage, used-chunk consistency, and obvious secret-like
output. They do not call live APIs, Chroma, SQLite, embedding providers, or
LLMs.

```bash
uv run pytest -q tests/evals
```

Full LLM answer generation, LLM-as-judge grading, retrieval tuning metrics, and
external observability are future work and should remain opt-in because source
evidence may contain private content.

Live API smoke tests are non-default and must be explicitly enabled. For wiki
generation, run the live GitHub smoke only when network is available and an
appropriate public or approved repository source exists. The command uses
temporary Chroma/SQLite and writes generated Markdown under `/private/tmp` by
default. It skips gracefully when no repository is configured or the source is
unavailable, and it must not print raw secrets or tokens.

```bash
python scripts/smoke_generate_wiki_page.py \
  --mode github \
  --github-repository owner/repo@main \
  --topic README \
  --require-generated
```

Live pytest markers, when added or expanded, must also stay explicitly enabled:

```bash
RUN_LIVE_E2E=1 uv run pytest -m live
```

This keeps CI deterministic while still making live smoke part of manual PR
validation for MCP/wiki changes when network access and an appropriate source
are available.

---

# 📌 Notes

- Ensure all required API keys (e.g., Notion, Tistory) are set in the environment.
- ChromaDB directory is configured via `AppConfig`.
- SQLite metadata path is configured via `AppConfig`.
- You can extend the system by adding new data fetchers or custom MCP tools.

---

# Demo
<img width="800" height="1000" alt="Image" src="https://github.com/user-attachments/assets/b256eb1e-9126-4778-94a8-dda4ff807e0f" />

### When enough posts exist in the local index DB (**found 3 results in local DB**)

<img width="1000" height="140" alt="Image" src="https://github.com/user-attachments/assets/79c20cf1-daaa-4954-b1b0-a47aecff7125" />

### When local results are insufficient (**Insufficient results (2/3), searching web...**)

<img width="1232" height="194" alt="Image" src="https://github.com/user-attachments/assets/aa6f0291-a572-4488-9d7a-119dccdc52c3" />

<img width="1352" height="118" alt="Image" src="https://github.com/user-attachments/assets/ec54b53e-126f-4241-b979-04938aeaae7f" />
