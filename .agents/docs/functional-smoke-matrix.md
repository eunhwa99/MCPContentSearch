# Functional Smoke Matrix

Use this matrix after implementation tests pass and before `$subagent-review-loop`.
Copy the task-relevant inventory rows into the plan before review, then mark
each row `passed`, `failed`, `not affected`, or `blocked/gated`. PR notes may
copy or link to the plan matrix after review.

| Area | When Affected | Safest Real Caller Surface | Safe Data Mode | Approval Gate | Required Evidence |
| --- | --- | --- | --- | --- | --- |
| MCP tool contract | Tool parameters, return shape, errors, or orchestration changed | FastMCP/local MCP client or focused MCP smoke call | Fake fixture, temp Chroma, temp SQLite, or mocked service | Live source/LLM only with explicit user approval | Tool name, inputs, result summary, safe error text |
| Configured-source sync | `sync_source(source_id)`, source registry, connector cleanup, or status changed | MCP `sync_source` through tests or local MCP smoke | Fake configured source or temp Chroma/SQLite | Real Notion/Tistory/GitHub source or user data sync requires approval | Source id, storage mode, status/result, cleanup/tombstone expectation |
| Answer/search retrieval | Answer, search, citations, filters, source ids, or used chunks changed | MCP `search_context`, `fetch_context`, and `answer_with_citations` through tests or local MCP smoke | Temp indexed fixture or fake smoke data | Private indexed data requires approval | Query, filters, citation/result summary |
| Source status and health | Source list, sync status, or diagnostics changed | MCP `list_sources`/`get_sync_status` | Fake/temp metadata store or mocked status | User metadata inspection requires approval | Source/status fields checked and payload summary |
| External connectors | Notion, Tistory, or GitHub fetching/parsing/rate limits changed | Mocked HTTP/API test plus optional fake/temp smoke | Mocked API responses or approved public target with temp storage | Any live external API requires approval; never print tokens | Mock/live distinction, source scope, result or skip reason |
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
- Prefer fake fixtures, temporary Chroma/SQLite paths, deterministic retained
  functional tests, and mocked connectors.
- Gate live sync, external APIs, and user-data mutation behind explicit user
  approval recorded in the plan.
- For every `blocked/gated` row, record the blocker and nearest substitute that
  was run or could be run safely.
