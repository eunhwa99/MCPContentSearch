#!/usr/bin/env bash
set -euo pipefail

LABEL="com.eunaverse.contextwiki.sync-worker"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=scripts/sync_worker_launch_agent_lock.sh
source "${SCRIPT_DIR}/sync_worker_launch_agent_lock.sh"
DRY_RUN=0

cleanup() {
  sync_worker_launch_agent_release_lock
}

trap cleanup EXIT

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
  printf 'Would send SIGTERM to restart %s gracefully\n' "${SERVICE_TARGET}"
  exit 0
fi

command -v launchctl >/dev/null 2>&1 || {
  printf 'error: launchctl was not found\n' >&2
  exit 1
}
sync_worker_launch_agent_acquire_lock "${LABEL}"
launchctl kill SIGTERM "${SERVICE_TARGET}"
printf 'Restarted %s\n' "${LABEL}"
