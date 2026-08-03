#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONPATH="${PYTHONPATH:+${PYTHONPATH}:}${REPO_ROOT}"
export IS_TESTING="${IS_TESTING:-1}"
export CONTEXTWIKI_DISABLE_DOTENV=1
DEFAULT_UV_CACHE_DIR="${TMPDIR:-/tmp}/uv-cache"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$DEFAULT_UV_CACHE_DIR}"
unset CONTEXTWIKI_OBSIDIAN_VAULT_PATH
unset CONTEXTWIKI_OBSIDIAN_MAX_FILES
unset CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES
unset CONTEXTWIKI_CAREER_MANIFEST_PATH
unset CONTEXTWIKI_CAREER_MAX_FILE_BYTES
unset CONTEXTWIKI_CAREER_MAX_FILES
unset CONTEXTWIKI_CAREER_MAX_TOTAL_RAW_BYTES
unset CONTEXTWIKI_CAREER_MAX_TOTAL_EXTRACTED_TEXT_BYTES
mkdir -p "$UV_CACHE_DIR"

RETAINED_PACKAGES=(
  api
  core
  environments
  evaluation
  fetching
  indexing
  parsing
  search
  storage
  app_runtime.py
  main.py
)

COVERAGE_TARGETS=(
  --cov=api
  --cov=core
  --cov=environments
  --cov=evaluation
  --cov=fetching
  --cov=indexing
  --cov=parsing
  --cov=search
  --cov=storage
  --cov=app_runtime
  --cov-report=term-missing
)

eval_git_identifier="$(bash "$REPO_ROOT/scripts/evaluation_git_identifier.sh")"

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
  echo "== Deterministic career retrieval evaluation gate =="
  uv run --locked python -m evaluation.runner \
    --dataset evaluation/datasets/retrieval_gold.example.jsonl \
    --corpus evaluation/datasets/career_corpus.example.jsonl \
    --configuration evaluation/configs/deterministic_fixture.json \
    --output-dir artifacts/career-retrieval-evaluation \
    --git-identifier "${eval_git_identifier}" \
    --public-only
  uv run --locked python -m evaluation.compare \
    --baseline evaluation/reports/retrieval_fixture_baseline.json \
    --current artifacts/career-retrieval-evaluation/report.json \
    --thresholds evaluation/reports/ci_thresholds.json \
    --output artifacts/career-retrieval-evaluation/comparison.json
else
  echo "uv workspace dependencies are unhealthy; cannot run required ruff, mypy, bandit, or coverage gates." >&2
  echo "Running closest dependency-free pytest fallback before failing the full gate." >&2
  python -m compileall "${RETAINED_PACKAGES[@]}"
  python -m pytest -m "not live"
  exit 1
fi

echo "== Deterministic functional E2E layer =="
"$REPO_ROOT/scripts/verify_functional_e2e.sh"
