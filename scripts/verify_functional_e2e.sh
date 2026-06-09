#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="${PYTHONPATH:+${PYTHONPATH}:}${REPO_ROOT}"
DEFAULT_UV_CACHE_DIR="${TMPDIR:-/tmp}/uv-cache"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$DEFAULT_UV_CACHE_DIR}"
mkdir -p "$UV_CACHE_DIR"

uv_workspace_healthy() {
  command -v uv >/dev/null 2>&1 && uv lock --check >/dev/null 2>&1 && uv run --locked python - <<'PY' >/dev/null 2>&1
import pytest  # noqa: F401
from llama_index.core import Document, StorageContext, VectorStoreIndex  # noqa: F401
from llama_index.core.llms import ChatMessage  # noqa: F401
PY
}

USE_UV=0
if uv_workspace_healthy; then
  USE_UV=1
fi
ALLOW_SYSTEM_PYTHON="${VERIFY_E2E_ALLOW_SYSTEM_PYTHON:-0}"

run_py() {
  if [[ "$USE_UV" == "1" ]]; then
    uv run --locked python "$@"
  else
    python "$@"
  fi
}

ensure_playwright_module_available() {
  if run_py - <<'PY' >/dev/null 2>&1
import playwright  # noqa: F401
PY
  then
    return
  fi
  echo "Playwright Python package is not available in the active runtime." >&2
  echo "Run 'uv sync --locked --python 3.13 --dev' (or install playwright in your Python environment) and retry." >&2
  exit 1
}

run_playwright_smoke_with_optional_bootstrap() {
  local output_file
  output_file="$(mktemp)"
  local auto_install="${VERIFY_E2E_AUTO_INSTALL_PLAYWRIGHT:-1}"
  local install_hint
  if [[ "$USE_UV" == "1" ]]; then
    install_hint="uv run --locked python -m playwright install chromium"
  else
    install_hint="python -m playwright install chromium"
  fi
  trap 'rm -f "$output_file"' RETURN

  if run_py scripts/smoke_web_console_playwright.py >"$output_file" 2>&1; then
    cat "$output_file"
    return
  fi

  if grep -Eq "Executable doesn't exist|playwright install|BrowserType.launch" "$output_file"; then
    if [[ "$auto_install" == "1" ]]; then
      echo "Playwright browser binaries are missing. Bootstrapping Chromium once..." >&2
      if ! run_py -m playwright install chromium >/dev/null; then
        echo "Playwright Chromium bootstrap failed. Run '$install_hint' manually and retry." >&2
        return 1
      fi
      if run_py scripts/smoke_web_console_playwright.py >"$output_file" 2>&1; then
        cat "$output_file"
        return
      fi
      cat "$output_file" >&2
      echo "Playwright smoke still failed after bootstrap. Run '$install_hint' manually and retry." >&2
      return 1
    fi
    cat "$output_file" >&2
    echo "Playwright browser binaries are missing. Set VERIFY_E2E_AUTO_INSTALL_PLAYWRIGHT=1 or run '$install_hint' first." >&2
    return 1
  fi

  cat "$output_file" >&2
  return 1
}

# Deterministic FastMCP functional smoke for wiki generation.
run_py scripts/smoke_generate_wiki_page.py --mode fake

# Full functional E2E/API/browser-contract coverage for non-live paths.
if [[ "$USE_UV" == "1" ]]; then
  uv run --locked pytest \
    tests/e2e/test_contextwiki_flow.py \
    tests/e2e/test_phase_b_connectors_flow.py \
    tests/web_console/test_app.py
else
  echo "uv e2e verification is unavailable or workspace dependencies are unhealthy; falling back to python -m pytest for functional e2e set" >&2
  python -m pytest \
    tests/e2e/test_contextwiki_flow.py \
    tests/e2e/test_phase_b_connectors_flow.py \
    tests/web_console/test_app.py
  if [[ "$ALLOW_SYSTEM_PYTHON" != "1" ]]; then
    echo "Functional E2E fallback ran outside locked uv dependencies; set VERIFY_E2E_ALLOW_SYSTEM_PYTHON=1 only for explicit local fallback acceptance." >&2
    exit 1
  fi
fi

# Browser-click E2E smoke using Playwright and local fake Web Console app.
ensure_playwright_module_available
run_playwright_smoke_with_optional_bootstrap
