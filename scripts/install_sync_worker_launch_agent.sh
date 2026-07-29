#!/usr/bin/env bash
set -euo pipefail

LABEL="com.eunaverse.contextwiki.sync-worker"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DEFAULT_REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"
# shellcheck source=scripts/sync_worker_launch_agent_lock.sh
source "${SCRIPT_DIR}/sync_worker_launch_agent_lock.sh"

REPO_ROOT="${DEFAULT_REPO_ROOT}"
TEMPLATE_PATH=""
UV_PATH=""
LOG_DIR=""
LOG_DIR_WAS_EXPLICIT=0
LAUNCH_AGENTS_DIR=""
RENDER_ONLY_PATH=""
DRY_RUN=0
RESTART_CHANGED=0
PLIST_CANDIDATE=""
PREVIOUS_PLIST_PATH=""

cleanup() {
  rm -f "${PLIST_CANDIDATE:-}" "${PREVIOUS_PLIST_PATH:-}"
  sync_worker_launch_agent_release_lock
}

trap cleanup EXIT

usage() {
  cat <<'EOF'
Usage: scripts/install_sync_worker_launch_agent.sh [options]

Render and install the ContextWiki sync worker as a macOS LaunchAgent.

Options:
  --repo-root PATH          Repository working directory (default: detected)
  --uv-path PATH            uv executable (default: resolved from PATH)
  --log-dir PATH            Worker log directory
                            (default: ~/.mcp_content_search/logs)
  --launch-agents-dir PATH  LaunchAgents directory
                            (default: ~/Library/LaunchAgents)
  --render-only PATH        Render a plist to PATH without calling launchctl
  --dry-run                 Print resolved actions without writing or launching
  --restart                 Apply a changed plist and explicitly restart worker
  -h, --help                Show this help
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

absolute_existing_dir() {
  local path_value="$1"
  [[ -d "${path_value}" ]] || fail "directory does not exist: ${path_value}"
  (cd "${path_value}" && pwd -P)
}

absolute_output_path() {
  local path_value="$1"
  if [[ "${path_value}" = /* ]]; then
    printf '%s\n' "${path_value}"
  else
    printf '%s/%s\n' "$(pwd -P)" "${path_value}"
  fi
}

directory_mode() {
  local path_value="$1"
  local mode

  if mode="$(stat -f '%OLp' "${path_value}" 2>/dev/null)"; then
    printf '%s\n' "${mode}"
    return
  fi
  if mode="$(stat -c '%a' "${path_value}" 2>/dev/null)"; then
    printf '%s\n' "${mode}"
    return
  fi
  fail "could not inspect log directory permissions: ${path_value}"
}

directory_owner_uid() {
  local path_value="$1"
  local owner_uid

  if owner_uid="$(stat -f '%u' "${path_value}" 2>/dev/null)"; then
    printf '%s\n' "${owner_uid}"
    return
  fi
  if owner_uid="$(stat -c '%u' "${path_value}" 2>/dev/null)"; then
    printf '%s\n' "${owner_uid}"
    return
  fi
  fail "could not inspect log directory ownership: ${path_value}"
}

validate_custom_log_path_components() {
  local path_value="$1"
  local relative_path
  local current_path=""
  local component
  local -a components

  [[ "${path_value}" == /* ]] ||
    fail "custom log directory must be absolute: ${path_value}"
  if [[ "${path_value}" != "/" && "${path_value}" == */ ]]; then
    fail "custom log directory must use a canonical path without a trailing slash: ${path_value}"
  fi

  relative_path="${path_value#/}"
  IFS='/' read -r -a components <<< "${relative_path}"
  for component in "${components[@]}"; do
    if [[ -z "${component}" || "${component}" == "." || "${component}" == ".." ]]; then
      fail "custom log directory must use a canonical path: ${path_value}"
    fi
    current_path="${current_path}/${component}"
    if [[ -L "${current_path}" ]]; then
      if [[ "${current_path}" == "${path_value}" ]]; then
        fail "custom log directory must not be a symbolic link: ${path_value}"
      fi
      fail "custom log directory must not contain a symbolic-link component: ${current_path}"
    fi
  done
}

validate_existing_custom_log_directory() {
  local mode
  local owner_uid
  local current_uid

  mode="$(directory_mode "${LOG_DIR}")"
  if [[ "${mode}" != "700" && "${mode}" != "0700" ]]; then
    fail "existing custom log directory must have mode 0700; refusing to change permissions: ${LOG_DIR} (mode ${mode})"
  fi

  owner_uid="$(directory_owner_uid "${LOG_DIR}")"
  current_uid="$(id -u)"
  if [[ "${owner_uid}" != "${current_uid}" ]]; then
    fail "existing custom log directory must be owned by the current user: ${LOG_DIR} (owner ${owner_uid}, current ${current_uid})"
  fi
  if [[ ! -w "${LOG_DIR}" || ! -x "${LOG_DIR}" ]]; then
    fail "existing custom log directory must be writable and searchable by the current user: ${LOG_DIR}"
  fi
}

prepare_log_directory() {
  local previous_umask
  local directory_kind="default"

  if [[ "${LOG_DIR_WAS_EXPLICIT}" -eq 1 ]]; then
    directory_kind="custom"
    validate_custom_log_path_components "${LOG_DIR}"
  fi
  if [[ -L "${LOG_DIR}" ]]; then
    fail "${directory_kind} log directory must not be a symbolic link: ${LOG_DIR}"
  fi
  if [[ -e "${LOG_DIR}" && ! -d "${LOG_DIR}" ]]; then
    fail "${directory_kind} log directory is not a directory: ${LOG_DIR}"
  fi

  if [[ -d "${LOG_DIR}" ]]; then
    if [[ "${LOG_DIR_WAS_EXPLICIT}" -eq 1 ]]; then
      validate_existing_custom_log_directory
    else
      chmod 0700 "${LOG_DIR}"
    fi
    return
  fi

  previous_umask="$(umask)"
  umask 077
  mkdir -p "${LOG_DIR}"
  umask "${previous_umask}"
  if [[ -L "${LOG_DIR}" || ! -d "${LOG_DIR}" ]]; then
    fail "could not create a private ${directory_kind} log directory: ${LOG_DIR}"
  fi
  chmod 0700 "${LOG_DIR}"
  if [[ "${LOG_DIR_WAS_EXPLICIT}" -eq 1 ]]; then
    validate_custom_log_path_components "${LOG_DIR}"
    validate_existing_custom_log_directory
  fi
}

xml_escape() {
  local value="$1"
  value="${value//&/&amp;}"
  value="${value//</&lt;}"
  value="${value//>/&gt;}"
  printf '%s' "${value}"
}

render_plist() {
  local output_path="$1"
  local output_dir
  local temporary_path
  local line
  local escaped_label
  local escaped_launcher_path
  local escaped_repo_root
  local escaped_uv_path
  local escaped_worker_log
  local escaped_diagnostic_log

  output_dir="$(dirname "${output_path}")"
  mkdir -p "${output_dir}"
  temporary_path="$(mktemp "${output_dir}/.${LABEL}.XXXXXX")"
  trap 'rm -f "${temporary_path:-}"' RETURN

  escaped_label="$(xml_escape "${LABEL}")"
  escaped_launcher_path="$(xml_escape "${REPO_ROOT}/scripts/run_sync_worker_launch_agent.sh")"
  escaped_repo_root="$(xml_escape "${REPO_ROOT}")"
  escaped_uv_path="$(xml_escape "${UV_PATH}")"
  escaped_worker_log="$(xml_escape "${LOG_DIR}/sync-worker.log")"
  escaped_diagnostic_log="$(xml_escape "${LOG_DIR}/sync-worker-startup.log")"

  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line//@@LABEL@@/${escaped_label}}"
    line="${line//@@LAUNCHER_PATH@@/${escaped_launcher_path}}"
    line="${line//@@REPO_ROOT@@/${escaped_repo_root}}"
    line="${line//@@UV_PATH@@/${escaped_uv_path}}"
    line="${line//@@WORKER_LOG@@/${escaped_worker_log}}"
    line="${line//@@DIAGNOSTIC_LOG@@/${escaped_diagnostic_log}}"
    printf '%s\n' "${line}"
  done < "${TEMPLATE_PATH}" > "${temporary_path}"

  if grep -Eq '@@[A-Z_]+@@' "${temporary_path}"; then
    fail "rendered plist contains unresolved placeholders"
  fi
  if command -v plutil >/dev/null 2>&1; then
    plutil -lint "${temporary_path}" >/dev/null
  fi

  chmod 0644 "${temporary_path}"
  mv "${temporary_path}" "${output_path}"
  trap - RETURN
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)
      [[ $# -ge 2 ]] || fail "--repo-root requires a path"
      REPO_ROOT="$2"
      shift 2
      ;;
    --uv-path)
      [[ $# -ge 2 ]] || fail "--uv-path requires a path"
      UV_PATH="$2"
      shift 2
      ;;
    --log-dir)
      [[ $# -ge 2 ]] || fail "--log-dir requires a path"
      LOG_DIR="$2"
      LOG_DIR_WAS_EXPLICIT=1
      shift 2
      ;;
    --launch-agents-dir)
      [[ $# -ge 2 ]] || fail "--launch-agents-dir requires a path"
      LAUNCH_AGENTS_DIR="$2"
      shift 2
      ;;
    --render-only)
      [[ $# -ge 2 ]] || fail "--render-only requires an output path"
      RENDER_ONLY_PATH="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --restart)
      RESTART_CHANGED=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
done

[[ -z "${RENDER_ONLY_PATH}" || "${DRY_RUN}" -eq 0 ]] ||
  fail "--render-only and --dry-run cannot be combined"

REPO_ROOT="$(absolute_existing_dir "${REPO_ROOT}")"
[[ -f "${REPO_ROOT}/indexing/sync_worker.py" ]] ||
  fail "sync worker entrypoint not found under repository: ${REPO_ROOT}"
[[ -f "${REPO_ROOT}/scripts/run_sync_worker_launch_agent.sh" ]] ||
  fail "LaunchAgent runner not found under repository: ${REPO_ROOT}"
TEMPLATE_PATH="${REPO_ROOT}/deploy/launchd/${LABEL}.plist.template"
[[ -f "${TEMPLATE_PATH}" ]] || fail "LaunchAgent template not found: ${TEMPLATE_PATH}"

if [[ -z "${UV_PATH}" ]]; then
  UV_PATH="$(command -v uv || true)"
fi
[[ -n "${UV_PATH}" ]] || fail "uv was not found; pass --uv-path /absolute/path/to/uv"
if [[ "${UV_PATH}" != /* ]]; then
  UV_PATH="$(command -v "${UV_PATH}" || true)"
fi
[[ -n "${UV_PATH}" && -x "${UV_PATH}" ]] ||
  fail "uv executable is not executable: ${UV_PATH:-<empty>}"
UV_PATH="$(cd "$(dirname "${UV_PATH}")" && pwd -P)/$(basename "${UV_PATH}")"

USER_HOME="${CONTEXTWIKI_LAUNCH_AGENT_HOME:-${HOME:-}}"
if [[ -z "${LOG_DIR}" || -z "${LAUNCH_AGENTS_DIR}" ]]; then
  [[ -n "${USER_HOME}" ]] ||
    fail "home directory is unavailable; pass --log-dir and --launch-agents-dir"
fi
if [[ -z "${LOG_DIR}" ]]; then
  LOG_DIR="${USER_HOME}/.mcp_content_search/logs"
fi
if [[ -z "${LAUNCH_AGENTS_DIR}" ]]; then
  LAUNCH_AGENTS_DIR="${USER_HOME}/Library/LaunchAgents"
fi
LOG_DIR="$(absolute_output_path "${LOG_DIR}")"
LAUNCH_AGENTS_DIR="$(absolute_output_path "${LAUNCH_AGENTS_DIR}")"

if [[ -n "${RENDER_ONLY_PATH}" ]]; then
  RENDER_ONLY_PATH="$(absolute_output_path "${RENDER_ONLY_PATH}")"
  render_plist "${RENDER_ONLY_PATH}"
  printf 'Rendered LaunchAgent plist: %s\n' "${RENDER_ONLY_PATH}"
  exit 0
fi

PLIST_PATH="${LAUNCH_AGENTS_DIR}/${LABEL}.plist"
DOMAIN_TARGET="gui/$(id -u)"
SERVICE_TARGET="${DOMAIN_TARGET}/${LABEL}"

if [[ "${DRY_RUN}" -eq 1 ]]; then
  printf 'LaunchAgent label: %s\n' "${LABEL}"
  printf 'Repository: %s\n' "${REPO_ROOT}"
  printf 'uv executable: %s\n' "${UV_PATH}"
  printf 'Log directory: %s\n' "${LOG_DIR}"
  printf 'Plist destination: %s\n' "${PLIST_PATH}"
  if [[ "${RESTART_CHANGED}" -eq 1 ]]; then
    printf 'Would render the plist and explicitly restart %s if it changed.\n' \
      "${SERVICE_TARGET}"
  else
    printf 'Would install only if absent; changed config requires --restart.\n'
  fi
  exit 0
fi

[[ "$(uname -s)" == "Darwin" ]] || fail "LaunchAgent installation requires macOS"
command -v launchctl >/dev/null 2>&1 || fail "launchctl was not found"

sync_worker_launch_agent_acquire_lock "${LABEL}"
prepare_log_directory
mkdir -p "${LAUNCH_AGENTS_DIR}"
PLIST_CANDIDATE="$(mktemp "${LAUNCH_AGENTS_DIR}/.${LABEL}.candidate.XXXXXX")"
render_plist "${PLIST_CANDIDATE}"

SERVICE_LOADED=0
if launchctl print "${SERVICE_TARGET}" >/dev/null 2>&1; then
  SERVICE_LOADED=1
fi
if [[ ! -f "${PLIST_PATH}" && "${SERVICE_LOADED}" -eq 1 && "${RESTART_CHANGED}" -ne 1 ]]; then
  fail "loaded service has no installed plist; rerun with --restart to replace it explicitly"
fi

if [[ -f "${PLIST_PATH}" ]] && cmp -s "${PLIST_PATH}" "${PLIST_CANDIDATE}"; then
  rm -f "${PLIST_CANDIDATE}"
  PLIST_CANDIDATE=""
  if [[ "${SERVICE_LOADED}" -eq 1 ]]; then
    printf 'Already installed with identical configuration; no changes made: %s\n' \
      "${LABEL}"
  else
    launchctl bootstrap "${DOMAIN_TARGET}" "${PLIST_PATH}"
    printf 'LaunchAgent configuration is identical; started unloaded service: %s\n' \
      "${LABEL}"
  fi
elif [[ -f "${PLIST_PATH}" && "${RESTART_CHANGED}" -ne 1 ]]; then
  fail "LaunchAgent configuration changed; rerun with --restart after checking active sync status"
else
  WAS_LOADED=0
  if [[ -f "${PLIST_PATH}" ]]; then
    PREVIOUS_PLIST_PATH="$(mktemp "${LAUNCH_AGENTS_DIR}/.${LABEL}.previous.XXXXXX")"
    cp -p "${PLIST_PATH}" "${PREVIOUS_PLIST_PATH}"
  fi
  if [[ "${SERVICE_LOADED}" -eq 1 ]]; then
    WAS_LOADED=1
    launchctl bootout "${SERVICE_TARGET}"
  fi
  mv "${PLIST_CANDIDATE}" "${PLIST_PATH}"
  if ! launchctl bootstrap "${DOMAIN_TARGET}" "${PLIST_PATH}"; then
    if [[ -n "${PREVIOUS_PLIST_PATH}" ]]; then
      mv "${PREVIOUS_PLIST_PATH}" "${PLIST_PATH}"
      PREVIOUS_PLIST_PATH=""
      if [[ "${WAS_LOADED}" -eq 1 ]]; then
        if ! launchctl bootstrap "${DOMAIN_TARGET}" "${PLIST_PATH}"; then
          fail "new configuration failed to start and previous configuration was restored, but its service could not be restarted"
        fi
      fi
      fail "new configuration failed to start; restored previous configuration and service state"
    fi
    rm -f "${PLIST_PATH}"
    if [[ "${WAS_LOADED}" -eq 1 ]]; then
      fail "new configuration failed to start; no previous plist was available, so the service remains unloaded"
    fi
    fail "new configuration failed to start; removed the unstarted plist"
  fi
  rm -f "${PREVIOUS_PLIST_PATH:-}"
  PREVIOUS_PLIST_PATH=""
  printf 'Installed and started %s\n' "${LABEL}"
fi

printf 'Status: scripts/status_sync_worker_launch_agent.sh\n'
printf 'Logs: %s\n' "${LOG_DIR}"
