# Security and data flow

ContextWiki indexes private knowledge. Operators should understand which data
stays local, which paths can call external services, and which checks are safe
to run without credentials.

## Local data

The default persistent paths are under `~/.mcp_content_search/`:

- `chroma_db/` stores vector-index content and metadata;
- `contextwiki_metadata.sqlite3` stores source, sync job, document, chunk,
  lifecycle, and tombstone records, including document/chunk text used by
  retrieval and direct fetch.

Docker examples mount this directory through the `contextwiki_data` volume.
Treat the host directory or Docker volume as private user data. Back it up and
protect it with operating-system permissions appropriate to the indexed
sources.

ContextWiki does not encrypt these stores itself. Do not publish them as build
artifacts, commit them, or attach them to bug reports.

## External data egress

There are three distinct external paths:

1. **Source sync.** Enabled Notion, Tistory, and GitHub connectors send normal
   authenticated or public API requests to those services and receive the
   configured source content. Obsidian reads a bounded local vault and does not
   require the Obsidian application.
2. **Embeddings.** The current default LlamaIndex embedding path uses OpenAI.
   Indexing can send document chunks, and semantic search can send query text,
   to the embedding provider. The current application startup has no supported
   environment-variable switch for embeddings. Avoiding this egress requires
   code-level composition with a local or otherwise non-egress LlamaIndex
   embedding model.
3. **Optional query rewrite.** Rewrite is disabled by default. When
   `CONTEXTWIKI_SEARCH_LLM_ENABLED=true`, ContextWiki sends a redacted form of
   the user's query plus normalized query terms to the configured OpenAI model.
   The rewrite path does not send the whole indexed corpus and does not mutate
   Chroma or SQLite.

Turning query rewrite off disables only item 3. It does not disable embedding
egress.

Before enabling a provider, review its retention terms, regional processing,
access controls, and account logging policy for the sensitivity of the indexed
content.

## Secrets

- Keep provider keys and source credentials in `.env` or process environment
  variables.
- Never commit `.env`, API keys, tokens, local databases, Chroma data, or cache
  directories.
- Avoid putting credentials in MCP client arguments, prompts, screenshots, or
  issue reports.
- Repository-local `.env` values do not override values already inherited from
  the parent process. Remove stale client or shell variables as well as editing
  `.env`.
- Rotate a credential immediately if it appears in logs, screenshots, commits,
  generated artifacts, or prompts sent to an unintended provider.

## Logging and returned debug data

The code includes redaction for common credential shapes, query text passed to
rewrite, background-task errors, and debug locations. This is defense in depth,
not a guarantee that arbitrary sensitive text can never appear.

Operational guidance:

- keep the default INFO logs access-controlled;
- do not enable or publish debug output for sensitive queries without review;
- inspect generated reports before sharing them;
- prefer source ids and bounded error summaries over raw source content in bug
  reports; and
- never rely on redaction as permission to log secrets.

## Safe verification

These commands are designed to avoid credentials and default user storage:

```bash
./scripts/demo.sh
uv run --locked python scripts/run_contextwiki_eval.py \
  --output-dir artifacts/contextwiki-evals
./scripts/verify_functional_e2e.sh
```

They use bundled fixtures, fake connectors, mock embeddings, and temporary
storage. Still review output before publishing it.

`scripts/live_query_smoke.py`, normal configured source sync, and a production
server run are different: they can use real credentials, call external
providers, read source content, and update the configured Chroma/SQLite stores.
Run them only against sources and storage you intend to use. Never use live
checks as a casual substitute for the deterministic gates.

## Deletion and stale-data behavior

Chroma vector deletion is best effort; SQLite remains the active-evidence
authority and can suppress tombstoned chunks even if an old vector candidate
still exists. Stale cleanup is allowed only after a cleanup-capable source
finishes a complete successful snapshot. Failed or bounded partial syncs do not
treat every absent document as deleted.

Disabling a source prevents future sync attempts but does not automatically
erase or hide already-active indexed documents. Removing local persistent data
is an operator action and should be backed up or otherwise made recoverable
before deletion.

## Reporting a vulnerability

No private security-reporting channel is currently published. Do not post
credentials, private indexed content, local database files, unredacted logs, or
other sensitive evidence in a public issue. First contact the owner through the
[eunaverse GitHub profile](https://github.com/eunaverse) without vulnerability
details and ask to arrange a private disclosure channel.
