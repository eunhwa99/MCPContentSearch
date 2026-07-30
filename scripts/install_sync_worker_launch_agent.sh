#!/usr/bin/env bash
set -euo pipefail

# Bash 5.2 enables replacement-string match expansion by default, which can
# reintroduce @@...@@ tokens when an XML-escaped replacement contains "&".
shopt -u patsub_replacement 2>/dev/null || true

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
ROLLBACK_PLIST_PATH=""
TRANSACTION_ACTIVE=0
TRANSACTION_ORIGINAL_PLIST=0
TRANSACTION_ORIGINAL_LOADED=0
TRANSACTION_NEW_SERVICE_MAY_BE_LOADED=0
RENDER_TRANSACTION_ACTIVE=0
RENDER_ORIGINAL_PRESENT=0
RENDER_PREVIOUS_PATH=""
RENDER_TARGET_PATH=""
RENDER_PUBLISHED=0
RENDER_TEMPORARY_PATH=""
PENDING_INTERRUPT_NAME=""
PENDING_INTERRUPT_STATUS=0

cleanup() {
  if [[ "${RENDER_TRANSACTION_ACTIVE}" -eq 1 ]]; then
    if [[ "${RENDER_PUBLISHED}" -eq 1 ]]; then
      if [[ "${RENDER_ORIGINAL_PRESENT}" -eq 1 ]]; then
        if ! mv -f "${RENDER_PREVIOUS_PATH}" "${RENDER_TARGET_PATH}"; then
          printf 'error: rendered target rollback did not complete\n' >&2
        fi
      elif ! rm -f "${RENDER_TARGET_PATH}"; then
        printf 'error: newly rendered target cleanup did not complete\n' >&2
      fi
    fi
  fi
  rm -f "${RENDER_PREVIOUS_PATH:-}" "${RENDER_TEMPORARY_PATH:-}"
  rm -f "${PLIST_CANDIDATE:-}" "${ROLLBACK_PLIST_PATH:-}"
  if [[ "${TRANSACTION_ACTIVE}" -eq 0 ]]; then
    rm -f "${PREVIOUS_PLIST_PATH:-}"
  elif [[ -n "${PREVIOUS_PLIST_PATH:-}" ]]; then
    printf 'error: rollback was incomplete; retained previous plist snapshot: %s\n' \
      "${PREVIOUS_PLIST_PATH}" >&2
  fi
  sync_worker_launch_agent_release_lock
}

trap cleanup EXIT

record_interrupt() {
  if [[ -z "${PENDING_INTERRUPT_NAME}" ]]; then
    PENDING_INTERRUPT_NAME="$1"
    PENDING_INTERRUPT_STATUS="$2"
  fi
}

# Catchable SIGTERM/SIGINT finish the tracked child and then roll back. SIGKILL
# cannot offer the same guarantee without a durable journal.
trap 'record_interrupt TERM 143' TERM
trap 'record_interrupt INT 130' INT

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

run_launchctl_interrupt_safe() {
  local command_status=1
  local waiter_pid
  local wait_status

  (
    trap '' TERM INT
    launchctl "$@"
  ) &
  waiter_pid=$!
  while true; do
    if wait "${waiter_pid}"; then
      command_status=0
      break
    else
      wait_status=$?
    fi
    if kill -0 "${waiter_pid}" 2>/dev/null; then
      continue
    fi
    command_status="${wait_status}"
    break
  done
  return "${command_status}"
}

exit_if_interrupted_without_transaction() {
  if [[ -n "${PENDING_INTERRUPT_NAME}" &&
    "${TRANSACTION_ACTIVE}" -eq 0 ]]; then
    printf 'error: interrupted by SIG%s before installation mutation\n' \
      "${PENDING_INTERRUPT_NAME}" >&2
    exit "${PENDING_INTERRUPT_STATUS}"
  fi
}

exit_if_interrupted_after_commit() {
  if [[ -n "${PENDING_INTERRUPT_NAME}" &&
    "${TRANSACTION_ACTIVE}" -eq 0 ]]; then
    printf 'error: interrupted by SIG%s; installation committed before interruption\n' \
      "${PENDING_INTERRUPT_NAME}" >&2
    exit "${PENDING_INTERRUPT_STATUS}"
  fi
}

exit_if_interrupted_after_render_commit() {
  if [[ -n "${PENDING_INTERRUPT_NAME}" &&
    "${RENDER_TRANSACTION_ACTIVE}" -eq 0 ]]; then
    printf 'error: interrupted by SIG%s; rendered target was committed before interruption\n' \
      "${PENDING_INTERRUPT_NAME}" >&2
    exit "${PENDING_INTERRUPT_STATUS}"
  fi
}

finalize_success_output() {
  local outcome="$1"

  trap - TERM INT
  case "${outcome}" in
    committed)
      exit_if_interrupted_after_commit
      ;;
    rendered)
      exit_if_interrupted_after_render_commit
      ;;
    *)
      exit_if_interrupted_without_transaction
      ;;
  esac
}

begin_render_transaction() {
  local output_dir

  RENDER_TARGET_PATH="$1"
  RENDER_ORIGINAL_PRESENT=0
  RENDER_PUBLISHED=0
  output_dir="$(dirname "${RENDER_TARGET_PATH}")"
  mkdir -p "${output_dir}"
  if [[ -e "${RENDER_TARGET_PATH}" || -L "${RENDER_TARGET_PATH}" ]]; then
    RENDER_PREVIOUS_PATH="$(
      mktemp "${output_dir}/.${LABEL}.render-previous.XXXXXX"
    )"
    cp -p "${RENDER_TARGET_PATH}" "${RENDER_PREVIOUS_PATH}"
    RENDER_ORIGINAL_PRESENT=1
  fi
  RENDER_TRANSACTION_ACTIVE=1
}

exit_if_render_interrupted() {
  local rollback_succeeded=1

  [[ -n "${PENDING_INTERRUPT_NAME}" ]] || return 0
  if [[ "${RENDER_PUBLISHED}" -eq 0 ]]; then
    RENDER_TRANSACTION_ACTIVE=0
    rm -f "${RENDER_PREVIOUS_PATH:-}"
    RENDER_PREVIOUS_PATH=""
    if [[ "${RENDER_ORIGINAL_PRESENT}" -eq 1 ]]; then
      printf 'error: render interrupted by SIG%s before publication; previous rendered target was preserved\n' \
        "${PENDING_INTERRUPT_NAME}" >&2
    else
      printf 'error: render interrupted by SIG%s before publication; no rendered target was published\n' \
        "${PENDING_INTERRUPT_NAME}" >&2
    fi
    exit "${PENDING_INTERRUPT_STATUS}"
  fi

  if [[ "${RENDER_ORIGINAL_PRESENT}" -eq 1 ]]; then
    mv -f "${RENDER_PREVIOUS_PATH}" "${RENDER_TARGET_PATH}" ||
      rollback_succeeded=0
  else
    rm -f "${RENDER_TARGET_PATH}" || rollback_succeeded=0
  fi
  if [[ "${rollback_succeeded}" -eq 1 ]]; then
    RENDER_TRANSACTION_ACTIVE=0
    RENDER_PREVIOUS_PATH=""
    if [[ "${RENDER_ORIGINAL_PRESENT}" -eq 1 ]]; then
      printf 'error: render interrupted by SIG%s during publication; restored previous rendered target\n' \
        "${PENDING_INTERRUPT_NAME}" >&2
    else
      printf 'error: render interrupted by SIG%s during publication; removed newly rendered target\n' \
        "${PENDING_INTERRUPT_NAME}" >&2
    fi
  else
    printf 'error: render interrupted by SIG%s; rendered target rollback did not complete\n' \
      "${PENDING_INTERRUPT_NAME}" >&2
  fi
  exit "${PENDING_INTERRUPT_STATUS}"
}

commit_render_transaction() {
  RENDER_TRANSACTION_ACTIVE=0
  rm -f "${RENDER_PREVIOUS_PATH:-}"
  RENDER_PREVIOUS_PATH=""
  RENDER_PUBLISHED=0
}

begin_install_transaction() {
  TRANSACTION_ORIGINAL_LOADED="$1"
  TRANSACTION_ORIGINAL_PLIST=0
  TRANSACTION_NEW_SERVICE_MAY_BE_LOADED=0
  if [[ -f "${PLIST_PATH}" ]]; then
    PREVIOUS_PLIST_PATH="$(
      mktemp "${LAUNCH_AGENTS_DIR}/.${LABEL}.previous.XXXXXX"
    )"
    cp -p "${PLIST_PATH}" "${PREVIOUS_PLIST_PATH}"
    TRANSACTION_ORIGINAL_PLIST=1
  fi
  TRANSACTION_ACTIVE=1
}

restore_previous_plist_snapshot() {
  if [[ "${TRANSACTION_ORIGINAL_PLIST}" -eq 1 ]]; then
    ROLLBACK_PLIST_PATH="$(
      mktemp "${LAUNCH_AGENTS_DIR}/.${LABEL}.rollback.XXXXXX"
    )"
    if ! cp -p "${PREVIOUS_PLIST_PATH}" "${ROLLBACK_PLIST_PATH}"; then
      return 1
    fi
    if ! mv "${ROLLBACK_PLIST_PATH}" "${PLIST_PATH}"; then
      return 1
    fi
    ROLLBACK_PLIST_PATH=""
  else
    rm -f "${PLIST_PATH}"
  fi
}

rollback_install_transaction() {
  local current_loaded=0

  if run_launchctl_interrupt_safe print "${SERVICE_TARGET}" \
    >/dev/null 2>&1; then
    current_loaded=1
  fi
  if [[ "${current_loaded}" -eq 1 &&
    ("${TRANSACTION_NEW_SERVICE_MAY_BE_LOADED}" -eq 1 ||
    "${TRANSACTION_ORIGINAL_LOADED}" -eq 0) ]]; then
    if ! run_launchctl_interrupt_safe bootout "${SERVICE_TARGET}"; then
      return 1
    fi
    current_loaded=0
  fi
  if ! restore_previous_plist_snapshot; then
    return 1
  fi
  if [[ "${TRANSACTION_ORIGINAL_LOADED}" -eq 1 &&
    "${current_loaded}" -eq 0 ]]; then
    if [[ "${TRANSACTION_ORIGINAL_PLIST}" -ne 1 ]]; then
      TRANSACTION_ACTIVE=0
      rm -f "${PREVIOUS_PLIST_PATH:-}"
      PREVIOUS_PLIST_PATH=""
      return 2
    fi
    if ! run_launchctl_interrupt_safe \
      bootstrap "${DOMAIN_TARGET}" "${PLIST_PATH}"; then
      return 1
    fi
  fi

  TRANSACTION_ACTIVE=0
  rm -f "${PREVIOUS_PLIST_PATH:-}"
  PREVIOUS_PLIST_PATH=""
  TRANSACTION_NEW_SERVICE_MAY_BE_LOADED=0
  return 0
}

commit_install_transaction() {
  TRANSACTION_ACTIVE=0
  rm -f "${PREVIOUS_PLIST_PATH:-}"
  PREVIOUS_PLIST_PATH=""
  TRANSACTION_NEW_SERVICE_MAY_BE_LOADED=0
}

rollback_and_exit_for_interrupt() {
  local rollback_status=0

  if rollback_install_transaction; then
    rollback_status=0
  else
    rollback_status=$?
  fi
  if [[ "${rollback_status}" -eq 0 ]]; then
    printf 'error: interrupted by SIG%s; restored previous configuration and service state\n' \
      "${PENDING_INTERRUPT_NAME}" >&2
  elif [[ "${rollback_status}" -eq 2 ]]; then
    printf 'error: interrupted by SIG%s; no previous plist was available, so the prior loaded service could not be restored\n' \
      "${PENDING_INTERRUPT_NAME}" >&2
  else
    printf 'error: interrupted by SIG%s; rollback did not complete\n' \
      "${PENDING_INTERRUPT_NAME}" >&2
  fi
  exit "${PENDING_INTERRUPT_STATUS}"
}

rollback_for_command_failure() {
  local failure_message="$1"
  local rollback_status=0

  if rollback_install_transaction; then
    rollback_status=0
  else
    rollback_status=$?
  fi
  if [[ "${rollback_status}" -eq 0 ]]; then
    fail "${failure_message}; restored previous configuration and service state"
  fi
  if [[ "${rollback_status}" -eq 2 ]]; then
    fail "${failure_message}; no previous plist was available, so the service remains unloaded"
  fi
  fail "${failure_message}; rollback did not complete"
}

rollback_if_interrupted() {
  if [[ -n "${PENDING_INTERRUPT_NAME}" ]]; then
    rollback_and_exit_for_interrupt
  fi
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

validate_log_path_components() {
  local path_value="$1"
  local directory_kind="$2"
  local relative_path
  local current_path=""
  local component
  local -a components

  [[ "${path_value}" == /* ]] ||
    fail "${directory_kind} log directory must be absolute: ${path_value}"
  if [[ "${path_value}" != "/" && "${path_value}" == */ ]]; then
    fail "${directory_kind} log directory must use a canonical path without a trailing slash: ${path_value}"
  fi

  relative_path="${path_value#/}"
  IFS='/' read -r -a components <<< "${relative_path}"
  for component in "${components[@]}"; do
    if [[ -z "${component}" || "${component}" == "." || "${component}" == ".." ]]; then
      fail "${directory_kind} log directory must use a canonical path: ${path_value}"
    fi
    current_path="${current_path}/${component}"
    if [[ -L "${current_path}" ]]; then
      if [[ "${current_path}" == "${path_value}" ]]; then
        fail "${directory_kind} log directory must not be a symbolic link: ${path_value}"
      fi
      fail "${directory_kind} log directory must not contain a symbolic-link component: ${current_path}"
    fi
    if [[ -e "${current_path}" && ! -d "${current_path}" ]]; then
      fail "${directory_kind} log directory path contains a non-directory component: ${current_path}"
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
  local owner_uid
  local current_uid

  if [[ "${LOG_DIR_WAS_EXPLICIT}" -eq 1 ]]; then
    directory_kind="custom"
  fi
  validate_log_path_components "${LOG_DIR}" "${directory_kind}"
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
      owner_uid="$(directory_owner_uid "${LOG_DIR}")"
      current_uid="$(id -u)"
      if [[ "${owner_uid}" != "${current_uid}" ]]; then
        fail "existing default log directory must be owned by the current user: ${LOG_DIR} (owner ${owner_uid}, current ${current_uid})"
      fi
      if [[ ! -w "${LOG_DIR}" || ! -x "${LOG_DIR}" ]]; then
        fail "existing default log directory must be writable and searchable by the current user: ${LOG_DIR}"
      fi
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
  validate_log_path_components "${LOG_DIR}" "${directory_kind}"
  chmod 0700 "${LOG_DIR}"
  if [[ "${LOG_DIR_WAS_EXPLICIT}" -eq 1 ]]; then
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
  RENDER_TEMPORARY_PATH="${temporary_path}"
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

  if [[ "${RENDER_TRANSACTION_ACTIVE}" -eq 1 ]]; then
    exit_if_render_interrupted
  fi
  chmod 0644 "${temporary_path}"
  if [[ "${RENDER_TRANSACTION_ACTIVE}" -eq 1 ]]; then
    exit_if_render_interrupted
  fi
  mv "${temporary_path}" "${output_path}"
  if [[ "${RENDER_TRANSACTION_ACTIVE}" -eq 1 ]]; then
    RENDER_PUBLISHED=1
    exit_if_render_interrupted
  fi
  RENDER_TEMPORARY_PATH=""
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
  begin_render_transaction "${RENDER_ONLY_PATH}"
  render_plist "${RENDER_ONLY_PATH}"
  commit_render_transaction
  finalize_success_output "rendered"
  printf 'Rendered LaunchAgent plist: %s\n' "${RENDER_ONLY_PATH}"
  exit 0
fi

PLIST_PATH="${LAUNCH_AGENTS_DIR}/${LABEL}.plist"
DOMAIN_TARGET="gui/$(id -u)"
SERVICE_TARGET="${DOMAIN_TARGET}/${LABEL}"

if [[ "${DRY_RUN}" -eq 1 ]]; then
  finalize_success_output "unchanged"
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
if run_launchctl_interrupt_safe print "${SERVICE_TARGET}" >/dev/null 2>&1; then
  SERVICE_LOADED=1
fi
exit_if_interrupted_without_transaction
if [[ ! -f "${PLIST_PATH}" && "${SERVICE_LOADED}" -eq 1 && "${RESTART_CHANGED}" -ne 1 ]]; then
  fail "loaded service has no installed plist; rerun with --restart to replace it explicitly"
fi

PLIST_IS_IDENTICAL=0
if [[ -f "${PLIST_PATH}" ]] &&
  cmp -s "${PLIST_PATH}" "${PLIST_CANDIDATE}"; then
  PLIST_IS_IDENTICAL=1
fi
exit_if_interrupted_without_transaction

if [[ "${PLIST_IS_IDENTICAL}" -eq 1 ]]; then
  exit_if_interrupted_without_transaction
  rm -f "${PLIST_CANDIDATE}"
  PLIST_CANDIDATE=""
  exit_if_interrupted_without_transaction
  if [[ "${SERVICE_LOADED}" -eq 1 ]]; then
    finalize_success_output "unchanged"
    printf 'Already installed with identical configuration; no changes made: %s\n' \
      "${LABEL}"
  else
    begin_install_transaction "${SERVICE_LOADED}"
    rollback_if_interrupted
    TRANSACTION_NEW_SERVICE_MAY_BE_LOADED=1
    if ! run_launchctl_interrupt_safe \
      bootstrap "${DOMAIN_TARGET}" "${PLIST_PATH}"; then
      rollback_if_interrupted
      rollback_for_command_failure \
        "identical LaunchAgent configuration failed to start"
    fi
    rollback_if_interrupted
    commit_install_transaction
    finalize_success_output "committed"
    printf 'LaunchAgent configuration is identical; started unloaded service: %s\n' \
      "${LABEL}"
  fi
elif [[ -f "${PLIST_PATH}" && "${RESTART_CHANGED}" -ne 1 ]]; then
  fail "LaunchAgent configuration changed; rerun with --restart after checking active sync status"
else
  begin_install_transaction "${SERVICE_LOADED}"
  rollback_if_interrupted
  if [[ "${SERVICE_LOADED}" -eq 1 ]]; then
    if ! run_launchctl_interrupt_safe bootout "${SERVICE_TARGET}"; then
      rollback_if_interrupted
      rollback_for_command_failure \
        "could not stop the previous LaunchAgent service"
    fi
  fi
  rollback_if_interrupted
  if ! mv "${PLIST_CANDIDATE}" "${PLIST_PATH}"; then
    rollback_for_command_failure \
      "could not install the new LaunchAgent configuration"
  fi
  PLIST_CANDIDATE=""
  rollback_if_interrupted
  TRANSACTION_NEW_SERVICE_MAY_BE_LOADED=1
  if ! run_launchctl_interrupt_safe \
    bootstrap "${DOMAIN_TARGET}" "${PLIST_PATH}"; then
    rollback_if_interrupted
    rollback_for_command_failure "new configuration failed to start"
  fi
  rollback_if_interrupted
  commit_install_transaction
  finalize_success_output "committed"
  printf 'Installed and started %s\n' "${LABEL}"
fi

printf 'Status: scripts/status_sync_worker_launch_agent.sh\n'
printf 'Logs: %s\n' "${LOG_DIR}"
