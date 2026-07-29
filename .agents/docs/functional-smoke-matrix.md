# Functional Smoke Matrix

Use this matrix only after focused unit/integration/E2E tests pass, refactoring
and affected-test reruns finish, `./scripts/verify_all.sh` succeeds, and any
matching eval gate required by feature scope has already been satisfied after
that full-suite gate (record full-suite quality-eval evidence when already
covered; otherwise run the focused matching eval command). Run it before the
harness three-reviewer loop. Copy the
task-relevant inventory rows into the plan,
or into reviewer/final/PR evidence for plan-exempt work, before review. Mark
each row `passed`, `failed`, `not affected`, or `blocked/gated`. PR notes may
copy or link to the plan matrix when one exists.

| Area | When Affected | Safest Real Caller Surface | Safe Data Mode | Approval Gate | Required Evidence |
| --- | --- | --- | --- | --- | --- |
| MCP tool contract | Tool parameters, return shape, errors, or orchestration changed | FastMCP/local MCP client or focused MCP smoke call | Fake fixture, temp Chroma, temp SQLite, or mocked service | Live source/LLM only with explicit user approval and a plan | Tool name, inputs, result summary, safe error text |
| Configured-source sync | `sync_source(source_id)`, source registry, connector cleanup, or status changed | MCP `sync_source` through tests or local MCP smoke | Fake configured source or temp Chroma/SQLite | Real Notion/Tistory/GitHub source or user data sync requires explicit user approval and a plan | Source id, storage mode, status/result, cleanup/tombstone expectation |
| Answer/search retrieval | Answer, search, citations, filters, source ids, or used chunks changed | MCP `sync_all`, `search_context`, `search_documents`, `fetch_context`, plus retained local answer/eval smoke | Temp indexed fixture or fake smoke data | Private indexed data requires explicit user approval and a plan | Query, filters, citation/result summary |
| Source status and health | Source list, sync status, or diagnostics changed | MCP `list_sources`/`get_sync_status` | Fake/temp metadata store or mocked status | User metadata inspection requires explicit user approval and a plan | Source/status fields checked and payload summary |
| External connectors | Notion, Tistory, or GitHub fetching/parsing/rate limits changed | Mocked HTTP/API test plus fake/temp smoke | Mocked API responses or approved public target with temp storage | Any live external API requires explicit user approval and a plan; never print tokens | Mock/live distinction, source scope, result or skip reason |
| Storage lifecycle | Chroma writes, SQLite metadata, document identity, chunks, tombstones, or cleanup changed | Local smoke using temp Chroma/SQLite plus focused tests | Temporary directories only | Inspecting/mutating user Chroma/SQLite requires explicit user approval and a plan | Temp storage mode, affected lifecycle state, rollback note |

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
- Gate live sync, external APIs, user-data mutation, and destructive actions
  behind both explicit user approval and a plan. If plan-exempt work discovers
  such a need, reclassify it as non-exempt and write the plan before acting, or
  mark the row `blocked/gated` and use a fake/temp substitute.
- For every `blocked/gated` row, record the blocker and nearest substitute that
  was run or could be run safely.
