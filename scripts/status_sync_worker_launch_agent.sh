#!/usr/bin/env bash
set -euo pipefail

LABEL="com.eunaverse.contextwiki.sync-worker"
DRY_RUN=0

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
  shift
fi
if [[ $# -ne 0 ]]; then
  printf 'Usage: %s [--dry-run]\n' "$0" >&2
  exit 2
fi

SERVICE_TARGET="gui/$(id -u)/${LABEL}"
if [[ "${DRY_RUN}" -eq 1 ]]; then
  printf 'Would inspect %s\n' "${SERVICE_TARGET}"
  exit 0
fi

command -v launchctl >/dev/null 2>&1 || {
  printf 'error: launchctl was not found\n' >&2
  exit 1
}
launchctl print "${SERVICE_TARGET}"
