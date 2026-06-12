---
title: ContextWiki Overview
---

# ContextWiki Overview

ContextWiki is a slim MCP retrieval server for project and private knowledge.

It syncs configured sources into Chroma for semantic candidate retrieval and
stores lifecycle plus citation metadata in SQLite.

SQLite is the active evidence gate. Chroma can return semantically similar
chunks, but ContextWiki only uses chunks that still exist and are active in
SQLite metadata.
