#!/usr/bin/env bash
set -euo pipefail

LABEL="com.eunaverse.context-zip.sync-worker"
OLD_LABEL="com.eunaverse.context""wiki.sync-worker"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=scripts/sync_worker_launch_agent_lock.sh
source "${SCRIPT_DIR}/sync_worker_launch_agent_lock.sh"
USER_HOME="${CONTEXTZIP_LAUNCH_AGENT_HOME:-${HOME:-}}"
LAUNCH_AGENTS_DIR=""
DRY_RUN=0

cleanup() {
  sync_worker_launch_agent_release_lock
}

trap cleanup EXIT

usage() {
  printf 'Usage: %s [--launch-agents-dir PATH] [--dry-run]\n' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --launch-agents-dir)
      [[ $# -ge 2 ]] || {
        printf 'error: --launch-agents-dir requires a path\n' >&2
        exit 2
      }
      LAUNCH_AGENTS_DIR="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "${LAUNCH_AGENTS_DIR}" ]]; then
  [[ -n "${USER_HOME}" ]] || {
    printf 'error: home directory is unavailable; pass --launch-agents-dir\n' >&2
    exit 1
  }
  LAUNCH_AGENTS_DIR="${USER_HOME}/Library/LaunchAgents"
fi
if [[ "${LAUNCH_AGENTS_DIR}" != /* ]]; then
  LAUNCH_AGENTS_DIR="$(pwd -P)/${LAUNCH_AGENTS_DIR}"
fi
PLIST_PATH="${LAUNCH_AGENTS_DIR}/${LABEL}.plist"
OLD_PLIST_PATH="${LAUNCH_AGENTS_DIR}/${OLD_LABEL}.plist"

DOMAIN_TARGET="gui/$(id -u)"
SERVICE_TARGET="${DOMAIN_TARGET}/${LABEL}"
OLD_SERVICE_TARGET="${DOMAIN_TARGET}/${OLD_LABEL}"
if [[ "${DRY_RUN}" -eq 1 ]]; then
  printf 'Would boot out %s and remove %s\n' "${SERVICE_TARGET}" "${PLIST_PATH}"
  if [[ -f "${OLD_PLIST_PATH}" ]]; then
    printf 'Would boot out %s and remove %s\n' \
      "${OLD_SERVICE_TARGET}" "${OLD_PLIST_PATH}"
  fi
  exit 0
fi

command -v launchctl >/dev/null 2>&1 || {
  printf 'error: launchctl was not found\n' >&2
  exit 1
}
sync_worker_launch_agent_acquire_lock "${LABEL}"
if launchctl print "${SERVICE_TARGET}" >/dev/null 2>&1; then
  launchctl bootout "${SERVICE_TARGET}"
fi
if [[ -f "${OLD_PLIST_PATH}" ]]; then
  if launchctl print "${OLD_SERVICE_TARGET}" >/dev/null 2>&1 &&
    ! launchctl bootout "${OLD_SERVICE_TARGET}"; then
    printf 'error: could not stop legacy LaunchAgent service; preserved legacy plist: %s\n' \
      "${OLD_PLIST_PATH}" >&2
    exit 1
  fi
  rm -f "${OLD_PLIST_PATH}"
fi
if [[ -f "${PLIST_PATH}" ]]; then
  rm -f "${PLIST_PATH}"
fi
printf 'Uninstalled %s\n' "${LABEL}"
