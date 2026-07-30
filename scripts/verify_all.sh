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

RETAINED_PACKAGES=(
  api
  core
  environments
  fetching
  indexing
  search
  storage
  main.py
)

COVERAGE_TARGETS=(
  --cov=api
  --cov=core
  --cov=environments
  --cov=fetching
  --cov=indexing
  --cov=search
  --cov=storage
  --cov-report=term-missing
)

uv_workspace_healthy() {
  command -v uv >/dev/null 2>&1 && uv lock --check >/dev/null 2>&1 && uv run --locked python - <<'PY' >/dev/null 2>&1
import pytest  # noqa: F401
from llama_index.core import Document, StorageContext, VectorStoreIndex  # noqa: F401
PY
}

if uv_workspace_healthy; then
  echo "== Static verification layer =="
  uv run --locked python -m compileall "${RETAINED_PACKAGES[@]}"
  uv run --locked ruff check "${RETAINED_PACKAGES[@]}"
  uv run --locked mypy
  uv run --locked bandit \
    -q \
    -c pyproject.toml \
    -r "${RETAINED_PACKAGES[@]}" \
    --severity-level medium \
    --confidence-level low
  echo "== Public MCP contract layer =="
  uv run --locked pytest -q \
    tests/contracts/test_public_mcp_contracts.py \
    tests/test_app_composition.py
  echo "== Broad non-live regression layer =="
  uv run --locked pytest -m "not live" "${COVERAGE_TARGETS[@]}"
  echo "== Deterministic quality eval layer =="
  uv run --locked python scripts/run_contextwiki_eval.py \
    --output-dir artifacts/contextwiki-evals
  uv run --locked python scripts/run_retrieval_benchmark.py \
    --split test \
    --output-dir artifacts/rag-evals
else
  echo "uv workspace dependencies are unhealthy; cannot run required ruff, mypy, bandit, or coverage gates." >&2
  echo "Running closest dependency-free pytest fallback before failing the full gate." >&2
  python -m compileall "${RETAINED_PACKAGES[@]}"
  python -m pytest -m "not live"
  exit 1
fi

echo "== Deterministic functional E2E layer =="
"$REPO_ROOT/scripts/verify_functional_e2e.sh"
