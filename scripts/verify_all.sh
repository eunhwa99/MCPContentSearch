#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="${PYTHONPATH:+${PYTHONPATH}:}${REPO_ROOT}"
DEFAULT_UV_CACHE_DIR="${TMPDIR:-/tmp}/uv-cache"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$DEFAULT_UV_CACHE_DIR}"
mkdir -p "$UV_CACHE_DIR"

FUNCTIONAL_GATE_EXCLUDES=(
  --ignore=tests/e2e/test_contextwiki_flow.py
  --ignore=tests/e2e/test_phase_b_connectors_flow.py
)

COVERAGE_TARGETS=(
  --cov=api
  --cov=core
  --cov=environments
  --cov=fetching
  --cov=indexing
  --cov=search
  --cov=storage
  --cov=wiki
  --cov=web_console
  --cov-report=term-missing
)

python -m compileall api core environments fetching indexing search storage wiki web_console main.py

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is required for verification (needed for 'node --check web/app.js'). Install Node.js and retry." >&2
  exit 1
fi
node --check web/app.js

uv_workspace_healthy() {
  command -v uv >/dev/null 2>&1 && uv lock --check >/dev/null 2>&1 && uv run --locked python - <<'PY' >/dev/null 2>&1
import pytest  # noqa: F401
from llama_index.core import Document, StorageContext, VectorStoreIndex  # noqa: F401
from llama_index.core.llms import ChatMessage  # noqa: F401
PY
}

if uv_workspace_healthy; then
  uv run --locked ruff check api core environments fetching indexing search storage wiki web_console main.py
  uv run --locked mypy
  uv run --locked bandit \
    -q \
    -c pyproject.toml \
    -r api core environments fetching indexing search storage wiki web_console main.py \
    --severity-level medium \
    --confidence-level low
  uv run --locked pytest -m "not live" "${FUNCTIONAL_GATE_EXCLUDES[@]}" "${COVERAGE_TARGETS[@]}"
else
  echo "uv workspace dependencies are unhealthy; cannot run required ruff, mypy, bandit, or coverage gates." >&2
  echo "Running closest dependency-free pytest fallback before failing the full gate." >&2
  python -m pytest -m "not live" "${FUNCTIONAL_GATE_EXCLUDES[@]}"
  exit 1
fi

# Functional E2E gate for end-to-end feature workflows.
"$REPO_ROOT/scripts/verify_functional_e2e.sh"
