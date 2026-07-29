#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="${PYTHONPATH:+${PYTHONPATH}:}${REPO_ROOT}"
export IS_TESTING="${IS_TESTING:-1}"
DEFAULT_UV_CACHE_DIR="${TMPDIR:-/tmp}/uv-cache"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$DEFAULT_UV_CACHE_DIR}"
unset CONTEXTWIKI_OBSIDIAN_VAULT_PATH
unset CONTEXTWIKI_OBSIDIAN_MAX_FILES
unset CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES
mkdir -p "$UV_CACHE_DIR"

uv_workspace_healthy() {
  command -v uv >/dev/null 2>&1 && uv lock --check >/dev/null 2>&1 && uv run --locked python - <<'PY' >/dev/null 2>&1
import pytest  # noqa: F401
from llama_index.core import Document, StorageContext, VectorStoreIndex  # noqa: F401
PY
}

USE_UV=0
if uv_workspace_healthy; then
  USE_UV=1
fi
ALLOW_SYSTEM_PYTHON="${VERIFY_E2E_ALLOW_SYSTEM_PYTHON:-0}"

RETAINED_FUNCTIONAL_TESTS=(
  tests/e2e/test_contextwiki_flow.py
  tests/e2e/test_obsidian_connector_flow.py
  tests/e2e/test_phase_b_connectors_flow.py
)

if [[ "$USE_UV" == "1" ]]; then
  uv run --locked pytest -q "${RETAINED_FUNCTIONAL_TESTS[@]}"
else
  echo "uv functional verification is unavailable or workspace dependencies are unhealthy; falling back to python -m pytest for retained functional tests" >&2
  python -m pytest -q "${RETAINED_FUNCTIONAL_TESTS[@]}"
  if [[ "$ALLOW_SYSTEM_PYTHON" != "1" ]]; then
    echo "Functional E2E fallback ran outside locked uv dependencies; set VERIFY_E2E_ALLOW_SYSTEM_PYTHON=1 only for explicit local fallback acceptance." >&2
    exit 1
  fi
fi
