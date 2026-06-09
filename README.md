# ContextWiki

[![CI](https://github.com/eunhwa99/MCPContentSearch/actions/workflows/ci.yml/badge.svg)](https://github.com/eunhwa99/MCPContentSearch/actions/workflows/ci.yml)

ContextWiki is a local-first MCP content search server that turns private or
project-specific sources into citation-backed answers and wiki pages. It is
built as a portfolio case study in pragmatic retrieval engineering: Chroma does
semantic search, SQLite decides which evidence is safe to cite, and deterministic
smokes prove the flow without live credentials or private data.

## Case Study

### Problem

Personal and team knowledge usually lives across Notion, blogs, repositories,
and documentation sites. A normal vector search demo can retrieve similar text,
but it often fails the harder product questions:

- Can the answer show where each claim came from?
- Can deleted or moved source documents stop appearing in citations?
- Can the demo run safely without indexing a user's private data?
- Can reviewers reproduce the core behavior in CI?

ContextWiki treats those as product requirements instead of afterthoughts.

### Product Outcome

- MCP tools for source sync, context search, citation answers, context fetch,
  and Auto Wiki generation.
- A local Web Console for the reviewer flow: sync a source, ask a question, see
  citations and chunks, then download a generated wiki page.
- Deterministic local evals and fake smokes that use temporary SQLite and
  Chroma paths under the system temp directory.
- CI coverage for locked dependency sync, Python syntax, browser JavaScript
  syntax, Ruff lint, scoped mypy, scoped Bandit, non-live pytest with coverage,
  and fake wiki generation.

### Architecture Decisions

The system is intentionally split by responsibility:

- `api/` owns MCP tool registration and caller-visible response contracts.
- `fetching/` owns Notion, Tistory, GitHub, and website/docs connectors.
- `indexing/` owns chunking, deduplication, lifecycle metadata writes, and
  Chroma/LlamaIndex indexing.
- `storage/` owns SQLite source, job, document, chunk, and tombstone metadata.
- `search/` owns retrieval, ranking, SQLite-backed active gates, and citation
  answer scaffolding.
- `wiki/` owns deterministic Auto Wiki generation and optional LLM synthesis.
- `web_console/` owns the local reviewer/debug console.

The main tradeoff is deliberate: Chroma is optimized for similarity retrieval,
while SQLite is the source of truth for citation metadata and document lifecycle.
That adds one more persistence layer, but it makes stale citation prevention and
deterministic tests much easier to reason about.

## Why SQLite Plus Chroma

Chroma answers "what chunks are semantically close to this query?" SQLite answers
"is this chunk still active, citation-ready, and attached to the right source?"

ContextWiki only cites chunks that pass SQLite metadata hydration. Full source
syncs update `last_seen` markers and tombstone documents that disappeared from a
successful connector snapshot. If a stale vector entry remains in Chroma, the
SQLite active chunk/document gate filters it before `search_context`,
`answer_with_citations`, or `generate_wiki_page` can expose it as evidence.

That design is especially useful for GitHub and docs sources, where files move,
paths change, and deleted content would otherwise keep surfacing from old vector
embeddings.

## Privacy And Safety

ContextWiki is private by default:

- Fake smokes and evals use temporary local storage, not user Chroma or SQLite.
- Live Notion, Tistory, GitHub, website/docs, and LLM checks are opt-in.
- GitHub authentication is read at runtime from `GITHUB_TOKEN`; raw tokens are
  not stored in SQLite, docs, tests, or logs.
- Auto Wiki LLM synthesis is disabled unless `CONTEXTWIKI_WIKI_LLM_ENABLED=true`
  and a configured provider key is available. The deterministic local wiki path
  runs without sending evidence to an external model.

## Reviewer Demo

### Seeded Local Demo

This path proves the MCP registration, fake source sync, active retrieval,
citations, backlinks, and wiki Markdown output without live services:

```bash
uv sync --locked --python 3.13 --dev
uv run --locked python scripts/smoke_generate_wiki_page.py --mode fake
```

Expected result: JSON with `status: "completed"`, a `passed` fake result, and
Markdown written under the system temp directory in `contextwiki-wiki-smoke`.

### Local Web Console

```bash
uv sync --locked --python 3.13 --dev
CONTEXTWIKI_AUTO_SYNC_SOURCES= \
  uv run --locked --python 3.13 uvicorn web_console.app:create_default_app --factory \
  --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765`, then use the product path:

1. Sync a one-off target or configured source.
2. Ask a question.
3. Inspect citations, sources, and used chunks.
4. Generate and download a wiki page as Markdown or JSON.

The Web Console command above disables startup auto-sync. Manual sync actions
are still live opt-in actions and write to the default local store under
`~/.mcp_content_search`; use the seeded fake smoke for a fully temporary demo.

Configured-source sync (`/api/sources/{source_id}/sync`) and one-off target sync
(`/api/targets/sync`) remain separate on purpose. Configured sync can apply
source lifecycle cleanup; one-off target sync avoids broad stale cleanup for
ad hoc review targets.

### Demo Screenshots

The screenshots below show the original local search and fallback behavior. The
seeded fake smoke above is the reproducible path for reviewers who want to run
the demo without credentials.

<img width="800" height="1000" alt="ContextWiki local console demo" src="https://github.com/user-attachments/assets/b256eb1e-9126-4778-94a8-dda4ff807e0f" />

Enough local results in DB (`found 3 results in local DB`):

<img width="1000" height="140" alt="Local DB search result" src="https://github.com/user-attachments/assets/79c20cf1-daaa-4954-b1b0-a47aecff7125" />

Web fallback path (`Insufficient results (2/3), searching web...`):

<img width="1232" height="194" alt="Web fallback trigger" src="https://github.com/user-attachments/assets/aa6f0291-a572-4488-9d7a-119dccdc52c3" />

<img width="1352" height="118" alt="Web fallback result" src="https://github.com/user-attachments/assets/ec54b53e-126f-4241-b979-04938aeaae7f" />

## MCP Tools

ContextWiki tools:

- `list_sources()`
- `sync_source(source_id)`
- `get_sync_status(source_id="")`
- `search_context(query, filters=None, top_k=10)`
- `fetch_context(document_id="", chunk_id="")`
- `answer_with_citations(question, filters=None, top_k=5)`
- `generate_wiki_page(topic, filters=None, top_k=8)`

Legacy search/indexing tools:

- `search_content(query, n_results=10)`
- `search_notion(query, n_results=10)`
- `search_tistory(query, n_results=10)`
- `search_github(query, n_results=10)`
- `trigger_index_all_content()`
- `get_index_status()`

## Verification Evidence

The full local gate is:

```bash
./scripts/verify_all.sh
```

It requires a healthy `uv` workspace, then runs Python compile checks, browser
JavaScript syntax, high-signal Ruff lint, scoped mypy type checking, scoped
Bandit security checks, non-live pytest with coverage fail-under, and the
functional E2E gate.

Latest local evidence from 2026-06-09, using deterministic non-live data:

| Gate | Evidence |
| --- | --- |
| Full local gate | `./scripts/verify_all.sh` passed |
| Non-live pytest + coverage | 872 tests passed, total coverage 84.74% against a 70% fail-under |
| Functional E2E | 198 tests passed |
| Fake wiki smoke | generated wiki page with 2 citations, 2 backlinks, 2 used chunks under the system temp directory |
| Web Console browser smoke | answer/debug, visible citations, Markdown/JSON downloads, Build Wiki visible Markdown, wiki downloads, configured-source sync, target sync, and validation failure path passed |
| Local eval runner | retrieval 5/5, answer 3/3, average score 1.0 for both suites |

Useful focused checks:

```bash
python -m compileall api core environments fetching indexing search storage wiki web_console main.py
node --check web/app.js
uv run --locked pytest -m "not live"
uv run --locked pytest -q tests/evals
PYTHONPATH=. uv run --locked python scripts/run_contextwiki_eval.py
uv run --locked python scripts/smoke_generate_wiki_page.py --mode fake
```

The GitHub Actions workflow mirrors the professional gates while staying
non-live:

```bash
uv lock --check
uv sync --locked --python 3.13 --dev
uv run --locked python -m compileall api core environments fetching indexing search storage wiki web_console main.py
node --check web/app.js
uv run --locked ruff check api core environments fetching indexing search storage wiki web_console main.py
uv run --locked mypy
uv run --locked bandit -q -c pyproject.toml \
  -r api core environments fetching indexing search storage wiki web_console main.py \
  --severity-level medium --confidence-level low
uv run --locked pytest -m "not live" \
  --ignore=tests/e2e/test_contextwiki_flow.py \
  --ignore=tests/e2e/test_phase_b_connectors_flow.py \
  --cov=api --cov=core --cov=environments --cov=fetching --cov=indexing \
  --cov=search --cov=storage --cov=wiki --cov=web_console
./scripts/verify_functional_e2e.sh
```

The functional E2E gate covers the fake wiki smoke, deterministic connector E2E
tests, Web Console contract tests, and the Playwright browser-click smoke.

## Local Evals

`evals/` contains deterministic evaluation scaffolding:

- payload-level answer grounding checks
- fixture-based retrieval quality checks
- fixture-based citation answer checks over temporary SQLite state

The eval runner swaps in local fixture retrieval and does not call live APIs,
user Chroma data, user SQLite data, or LLMs.

## Dependency Source Of Truth

- `pyproject.toml` declares runtime dependencies, dev dependency groups, and
  tool configuration.
- `uv.lock` is the resolved dependency lock used by local verification and CI.
- `requirements.txt` is a runtime-only compatibility mirror for pip-based
  environments. It is not the authoritative dependency manifest and is not used
  by CI.

Use `uv sync --locked --python 3.13 --dev` for development and CI parity.

## Configuration

Source configuration:

- `CONTEXTWIKI_GITHUB_REPOSITORIES`: comma-separated `owner/repo` entries,
  optional `@ref`
- `GITHUB_TOKEN`: optional GitHub API auth
- `CONTEXTWIKI_WEB_URLS`: seed URLs for website/docs crawling
- `CONTEXTWIKI_AUTO_SYNC_SOURCES`: source IDs to auto-sync at startup

Feature toggles:

- `CONTEXTWIKI_SEARCH_LLM_ENABLED`: enable search-quality LLM enhancement
- `CONTEXTWIKI_WIKI_LLM_ENABLED`: enable LLM-assisted wiki synthesis
- `CONTEXTWIKI_WIKI_LLM_PROVIDER`, `CONTEXTWIKI_WIKI_LLM_MODEL`
- `OPENAI_API_KEY`: required only for configured provider usage

## Project Map

- `api/`: MCP-facing tool contracts
- `core/`: shared models, errors, and utilities
- `environments/`: runtime configuration and token loading
- `fetching/`: source connectors and legacy live search helpers
- `indexing/`: conversion, chunking, deduplication, and vector writes
- `search/`: retrieval, active metadata gates, answers, and ranking
- `storage/`: SQLite source/job/document/chunk lifecycle metadata
- `wiki/`: citation-backed wiki generation
- `web/`, `web_console/`: local reviewer console
- `evals/`, `tests/`, `scripts/`: deterministic verification harness

## Additional Docs

- [ContextWiki Core Understanding](docs/contextwiki-core-understanding.md)
- [Architecture](.agents/docs/architecture.md)
- [ADRs](.agents/docs/adr/)
- [Harness and Workflow](.agents/docs/harness-engineering.md)
- [GitHub workflow policy](.agents/docs/github-workflow.md)
- [Plan log](docs/plan/)
