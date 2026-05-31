#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="${PYTHONPATH:+${PYTHONPATH}:}${REPO_ROOT}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/private/tmp/uv-cache}"

FUNCTIONAL_GATE_EXCLUDES=(
  --ignore=tests/e2e/test_contextwiki_flow.py
  --ignore=tests/e2e/test_phase_b_connectors_flow.py
  --ignore=tests/web_console/test_app.py
)

python -m compileall api core environments fetching indexing search storage wiki web_console main.py

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is required for verification (needed for 'node --check web/app.js'). Install Node.js and retry." >&2
  exit 1
fi
node --check web/app.js

uv_workspace_healthy() {
  command -v uv >/dev/null 2>&1 && uv run python - <<'PY' >/dev/null 2>&1
import pytest  # noqa: F401
from llama_index.core import Document, StorageContext, VectorStoreIndex  # noqa: F401
from llama_index.core.llms import ChatMessage  # noqa: F401
PY
}

if uv_workspace_healthy; then
  uv run pytest -m "not live" "${FUNCTIONAL_GATE_EXCLUDES[@]}"
else
  echo "uv pytest is unavailable or workspace dependencies are unhealthy; falling back to python -m pytest -m \"not live\"" >&2
  python -m pytest -m "not live" "${FUNCTIONAL_GATE_EXCLUDES[@]}"
fi

# Functional E2E gate for end-to-end feature workflows.
"$REPO_ROOT/scripts/verify_functional_e2e.sh"
