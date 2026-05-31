# Functional Smoke Matrix

Use this matrix after implementation tests pass and before `$subagent-review-loop`.
Copy the task-relevant inventory rows into the plan before review, then mark
each row `passed`, `failed`, `not affected`, or `blocked/gated`. PR notes may
copy or link to the plan matrix after review.

| Area | When Affected | Safest Real Caller Surface | Safe Data Mode | Approval Gate | Required Evidence |
| --- | --- | --- | --- | --- | --- |
| MCP tool contract | Tool parameters, return shape, errors, or orchestration changed | FastMCP/local MCP client or focused MCP smoke call | Fake fixture, temp Chroma, temp SQLite, or mocked service | Live source/LLM only with explicit user approval | Tool name, inputs, result summary, safe error text |
| Configured-source sync | `sync_source(source_id)`, source registry, connector cleanup, status, or configured source UI changed | MCP `sync_source` or Web Console configured source Sync button | Fake configured source or temp Chroma/SQLite | Real Notion/Tistory/GitHub/Web source or user data sync requires approval | Source id, storage mode, status/result, cleanup/tombstone expectation |
| Target or ad hoc sync | One-off GitHub repo, URL, fake source, smoke target, or target-specific cleanup changed | Target-sync UI, script smoke, or explicit MCP/API path if present | Fake target, public approved target with temp storage, or mocked connector | Live network target requires approval; user Chroma/SQLite mutation requires approval | Target spec, command/action, storage path type, result/skip reason |
| Web Console UI | Browser-facing flow, button, filter, download, diagnostics, or visible error text changed | Local Web Console in in-app browser; click affected controls | Fake/local fixture, temp storage, deterministic smoke mode | Live sync, GitHub Smoke, or user-data actions require approval | URL, controls clicked, visible pass/failure text, screenshot/log path if useful |
| Answer/search retrieval | Answer, search, citations, filters, source ids, used chunks, or backlinks changed | MCP tool and Web Console flow when UI is affected | Temp indexed fixture or fake smoke data | Live LLM synthesis or private indexed data requires approval | Query/topic, filters, citation/backlink/result summary |
| Auto Wiki | Wiki generation, Markdown/JSON download, backlinks, citations, or smoke scripts changed | `python scripts/smoke_generate_wiki_page.py --mode fake`; Web Console Generate Wiki if UI changed | Fake source data and temp output under `/private/tmp` | Live GitHub wiki smoke or LLM synthesis requires approval | Command/action, output path type, generated/safe failure summary |
| Source status and health | Source list, sync status, health/status display, or diagnostics changed | MCP `list_sources`/`get_sync_status` and Web Console status view when affected | Fake/temp metadata store or mocked status | User metadata inspection requires approval | Source/status fields checked, visible text or payload summary |
| Downloads and exports | Markdown, JSON, or other generated file download changed | Web Console download control or deterministic script output | Temp output directory under `/private/tmp` | Exporting private indexed content requires approval | File type, created path type, content safety check |
| External connectors | Notion, Tistory, GitHub, website/docs fetching/parsing/rate limits changed | Mocked HTTP/API test plus optional fake/temp smoke | Mocked API responses or approved public target with temp storage | Any live external API requires approval; never print tokens | Mock/live distinction, source scope, result or skip reason |
| Storage lifecycle | Chroma writes, SQLite metadata, document identity, chunks, tombstones, or cleanup changed | Local smoke using temp Chroma/SQLite plus focused tests | Temporary directories only | Inspecting/mutating user Chroma/SQLite requires approval | Temp storage mode, affected lifecycle state, rollback note |

## Row Template

```markdown
| Feature | Caller Surface | Data Mode | Expected Result | Action/Command | Result | Evidence | Skip Reason / Substitute |
| --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |
```

Rules:

- Start from the full inventory instead of only changed files. Exercise every
  changed feature, directly affected neighboring feature, and core workflow a
  user would naturally expect to still work after the change once through the
  safest real caller surface.
- Prefer fake fixtures, temporary Chroma/SQLite paths, deterministic smoke
  scripts, and mocked connectors.
- Gate live sync, live LLM, external APIs, and user-data mutation behind
  explicit user approval recorded in the plan.
- For every `blocked/gated` row, record the blocker and nearest substitute that
  was run or could be run safely.
