#!/usr/bin/env bash
set -euo pipefail

export UV_CACHE_DIR="${UV_CACHE_DIR:-/private/tmp/uv-cache}"

python -m compileall api core environments fetching indexing search storage wiki main.py

if command -v uv >/dev/null 2>&1 && uv run --with pytest pytest --version >/dev/null 2>&1; then
  uv run --with pytest pytest -m "not live"
else
  echo "uv pytest is unavailable; falling back to python -m pytest -m \"not live\"" >&2
  python -m pytest -m "not live"
fi
