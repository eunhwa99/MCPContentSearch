#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="${PYTHONPATH:+${PYTHONPATH}:}${REPO_ROOT}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/private/tmp/uv-cache}"

python -m compileall api core environments fetching indexing search storage wiki main.py

uv_workspace_healthy() {
  command -v uv >/dev/null 2>&1 && uv run python - <<'PY' >/dev/null 2>&1
import pytest  # noqa: F401
from llama_index.core import Document, StorageContext, VectorStoreIndex  # noqa: F401
from llama_index.core.llms import ChatMessage  # noqa: F401
PY
}

if uv_workspace_healthy; then
  uv run pytest -m "not live"
else
  echo "uv pytest is unavailable or workspace dependencies are unhealthy; falling back to python -m pytest -m \"not live\"" >&2
  python -m pytest -m "not live"
fi
