---
title: Citation Safety
---

# Citation Safety

ContextZip prevents stale citations by validating retrieved chunks against
SQLite metadata before returning them as evidence.

If an old vector remains in Chroma after source content changed, the stale
chunk is filtered out unless SQLite still marks it as active.

This design keeps retrieval fast while making citation safety explicit.
