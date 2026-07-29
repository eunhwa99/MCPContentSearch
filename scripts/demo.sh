#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="${PYTHONPATH:+${PYTHONPATH}:}${REPO_ROOT}"

choose_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    printf '%s\n' "$PYTHON"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s\n' "python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    printf '%s\n' "python"
    return 0
  fi
  return 1
}

uv_workspace_healthy() {
  command -v uv >/dev/null 2>&1 && uv lock --check >/dev/null 2>&1 && uv run --locked python - <<'PY' >/dev/null 2>&1
import chromadb  # noqa: F401
from llama_index.core.embeddings import MockEmbedding  # noqa: F401
PY
}

if uv_workspace_healthy; then
  uv run --locked python scripts/demo_public_flow.py "$@"
else
  PYTHON_BIN="$(choose_python || true)"
  if [[ -z "${PYTHON_BIN:-}" ]]; then
    echo "No usable Python interpreter found. Install Python 3.13 and run 'uv sync --locked --python 3.13 --dev' first." >&2
    exit 1
  fi
  if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import chromadb  # noqa: F401
from llama_index.core.embeddings import MockEmbedding  # noqa: F401
PY
  then
    echo "Demo dependencies are not installed for ${PYTHON_BIN}. Run 'uv sync --locked --python 3.13 --dev' first, then retry ./scripts/demo.sh." >&2
    exit 1
  fi
  "$PYTHON_BIN" scripts/demo_public_flow.py "$@"
fi
