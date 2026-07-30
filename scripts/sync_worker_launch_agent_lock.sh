#!/usr/bin/env bash

# Shared by every helper that mutates the LaunchAgent service or installed
# plist. macOS does not ship flock(1), so use an atomic directory lock.

CONTEXTWIKI_LAUNCH_AGENT_LOCK_HELD=0
CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR=""
CONTEXTWIKI_LAUNCH_AGENT_LOCK_OWNER_START=""
CONTEXTWIKI_LAUNCH_AGENT_LOCK_ORPHAN_GRACE=60
CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_OWNER_PID=""
CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_OWNER_START=""
CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_CHILD_PID=""
CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_CHILD_START=""
CONTEXTWIKI_LAUNCH_AGENT_LAUNCHCTL_PATH=""

sync_worker_launch_agent_process_start_id() {
  local process_id="$1"

  LC_ALL=C ps -p "${process_id}" -o lstart= 2>/dev/null |
    sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

sync_worker_launch_agent_read_published_lock_owner() {
  local owner_path="$1"
  local owner_pid=""
  local owner_start=""
  local child_pid=""
  local child_start=""

  [[ -f "${owner_path}" && ! -L "${owner_path}" ]] || return 1
  {
    IFS= read -r owner_pid || return 1
    IFS= read -r owner_start || return 1
    IFS= read -r child_pid || true
    IFS= read -r child_start || true
  } < "${owner_path}"
  case "${owner_pid}" in
    ""|*[!0-9]*) return 1 ;;
  esac
  [[ "${owner_pid}" -gt 0 ]] || return 1
  [[ -n "${owner_start}" ]] || return 1
  if [[ -n "${child_pid}" || -n "${child_start}" ]]; then
    case "${child_pid}" in
      ""|*[!0-9]*) return 1 ;;
    esac
    [[ "${child_pid}" -gt 0 && -n "${child_start}" ]] || return 1
  fi
  CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_OWNER_PID="${owner_pid}"
  CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_OWNER_START="${owner_start}"
  CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_CHILD_PID="${child_pid}"
  CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_CHILD_START="${child_start}"
}

sync_worker_launch_agent_process_identity_is_stale() {
  local process_id="$1"
  local process_start="$2"
  local current_start=""

  if ! kill -0 "${process_id}" 2>/dev/null; then
    return 0
  fi
  current_start="$(sync_worker_launch_agent_process_start_id "${process_id}")"
  [[ -n "${current_start}" && "${current_start}" != "${process_start}" ]]
}

sync_worker_launch_agent_published_owner_is_stale() {
  local owner_pid="$1"
  local owner_start="$2"
  local child_pid="${3:-}"
  local child_start="${4:-}"

  sync_worker_launch_agent_process_identity_is_stale \
    "${owner_pid}" "${owner_start}" || return 1
  if [[ -n "${child_pid}" ]]; then
    sync_worker_launch_agent_process_identity_is_stale \
      "${child_pid}" "${child_start}" || return 1
  fi
}

sync_worker_launch_agent_publish_owner() {
  local child_pid="${1:-}"
  local child_start="${2:-}"
  local owner_path="${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}/owner"
  local temporary_owner_path="${owner_path}.$$"

  if [[ -n "${child_pid}" ]]; then
    printf '%s\n%s\n%s\n%s\n' \
      "$$" \
      "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_OWNER_START}" \
      "${child_pid}" \
      "${child_start}" > "${temporary_owner_path}"
  else
    printf '%s\n%s\n' \
      "$$" \
      "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_OWNER_START}" > "${temporary_owner_path}"
  fi
  if ! mv -f "${temporary_owner_path}" "${owner_path}"; then
    rm -f "${temporary_owner_path}"
    return 1
  fi
}

sync_worker_launch_agent_run_launchctl() {
  local child_pid=""
  local child_start=""
  local gate_path=""
  local owner_temporary_path=""
  local owner_pid="$$"
  local owner_start="${CONTEXTWIKI_LAUNCH_AGENT_LOCK_OWNER_START}"
  local command_status=1

  if [[ "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_HELD}" -ne 1 ]]; then
    command launchctl "$@"
    return
  fi

  gate_path="${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}/.launchctl-ready"
  owner_temporary_path="${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}/owner.$$"
  rm -f "${gate_path}" "${owner_temporary_path}"
  (
    while [[ ! -f "${gate_path}" ]]; do
      if sync_worker_launch_agent_process_identity_is_stale \
        "${owner_pid}" "${owner_start}"; then
        rm -f "${gate_path}" "${owner_temporary_path}"
        exit 125
      fi
      sleep 0.01
    done
    exec "${CONTEXTWIKI_LAUNCH_AGENT_LAUNCHCTL_PATH}" "$@"
  ) &
  child_pid=$!
  child_start="$(sync_worker_launch_agent_process_start_id "${child_pid}")"
  if [[ -z "${child_start}" ]]; then
    kill "${child_pid}" 2>/dev/null || true
    wait "${child_pid}" 2>/dev/null || true
    rm -f "${gate_path}" "${owner_temporary_path}"
    printf 'error: could not identify launchctl child process\n' >&2
    return 1
  fi
  if ! sync_worker_launch_agent_publish_owner \
    "${child_pid}" "${child_start}"; then
    kill "${child_pid}" 2>/dev/null || true
    wait "${child_pid}" 2>/dev/null || true
    rm -f "${gate_path}" "${owner_temporary_path}"
    printf 'error: could not record launchctl child process\n' >&2
    return 1
  fi
  touch "${gate_path}"
  if wait "${child_pid}"; then
    command_status=0
  else
    command_status=$?
  fi
  rm -f "${gate_path}"
  if [[ "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_HELD}" -eq 1 ]]; then
    sync_worker_launch_agent_publish_owner
  fi
  return "${command_status}"
}

sync_worker_launch_agent_enable_launchctl_child_tracking() {
  # shellcheck disable=SC2329  # Installed dynamically after lock acquisition.
  launchctl() {
    sync_worker_launch_agent_run_launchctl "$@"
  }
}

sync_worker_launch_agent_path_is_owned_by_current_user() {
  local path="$1"
  local owner_uid=""
  local current_uid=""

  owner_uid="$(stat -f '%u' "${path}" 2>/dev/null || true)"
  case "${owner_uid}" in
    ""|*[!0-9]*)
      owner_uid="$(stat -c '%u' "${path}" 2>/dev/null || true)"
      ;;
  esac
  case "${owner_uid}" in
    ""|*[!0-9]*) return 1 ;;
  esac
  current_uid="$(id -u)"
  [[ "${owner_uid}" == "${current_uid}" ]]
}

sync_worker_launch_agent_reclaim_marker_is_safe() {
  local reclaim_dir="$1"
  local owner_path="${reclaim_dir}/owner"

  [[ -d "${reclaim_dir}" && ! -L "${reclaim_dir}" ]] || return 1
  sync_worker_launch_agent_path_is_owned_by_current_user "${reclaim_dir}" ||
    return 1
  if [[ -e "${owner_path}" || -L "${owner_path}" ]]; then
    [[ -f "${owner_path}" && ! -L "${owner_path}" ]] || return 1
    sync_worker_launch_agent_path_is_owned_by_current_user "${owner_path}" ||
      return 1
  fi
}

sync_worker_launch_agent_path_is_old_orphan() {
  local path="$1"
  local directory_mtime=""
  local now=""

  directory_mtime="$(stat -f '%m' "${path}" 2>/dev/null || true)"
  case "${directory_mtime}" in
    ""|*[!0-9]*)
      directory_mtime="$(stat -c '%Y' "${path}" 2>/dev/null || true)"
      ;;
  esac
  case "${directory_mtime}" in
    ""|*[!0-9]*) return 1 ;;
  esac
  now="$(date +%s)"
  case "${now}" in
    ""|*[!0-9]*) return 1 ;;
  esac
  ((now >= directory_mtime)) || return 1
  ((now - directory_mtime >= CONTEXTWIKI_LAUNCH_AGENT_LOCK_ORPHAN_GRACE))
}

sync_worker_launch_agent_try_recover_stale_reclaim_marker() {
  local reclaim_dir="$1"
  local owner_path="${reclaim_dir}/owner"

  sync_worker_launch_agent_reclaim_marker_is_safe "${reclaim_dir}" ||
    return 1
  if sync_worker_launch_agent_read_published_lock_owner "${owner_path}"; then
    sync_worker_launch_agent_published_owner_is_stale \
      "${CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_OWNER_PID}" \
      "${CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_OWNER_START}" \
      "${CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_CHILD_PID}" \
      "${CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_CHILD_START}" ||
      return 1
  elif ! sync_worker_launch_agent_path_is_old_orphan "${reclaim_dir}"; then
    return 1
  fi

  rm -f "${owner_path}"
  rmdir "${reclaim_dir}" 2>/dev/null
}

sync_worker_launch_agent_release_reclaim_marker() {
  local reclaim_dir="$1"
  local owner_path="${reclaim_dir}/owner"
  local owner_pid=""
  local owner_start=""

  if [[ -f "${owner_path}" && ! -L "${owner_path}" ]]; then
    {
      IFS= read -r owner_pid || true
      IFS= read -r owner_start || true
    } < "${owner_path}"
  fi
  if [[ "${owner_pid}" == "$$" &&
    "${owner_start}" == "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_OWNER_START}" ]]; then
    rm -f "${owner_path}"
    rmdir "${reclaim_dir}" 2>/dev/null || true
  fi
}

sync_worker_launch_agent_try_recover_stale_lock() {
  local lock_dir="$1"
  local reclaim_dir="${lock_dir}.reclaim"
  local owner_path="${lock_dir}/owner"
  local reclaim_owner_path="${reclaim_dir}/owner"

  [[ -d "${lock_dir}" && ! -L "${lock_dir}" && -O "${lock_dir}" ]] ||
    return 1
  mkdir "${reclaim_dir}" 2>/dev/null || return 1
  if ! printf '%s\n%s\n' \
    "$$" \
    "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_OWNER_START}" > "${reclaim_owner_path}"; then
    rmdir "${reclaim_dir}" 2>/dev/null || true
    return 1
  fi
  if sync_worker_launch_agent_read_published_lock_owner "${owner_path}"; then
    if sync_worker_launch_agent_published_owner_is_stale \
      "${CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_OWNER_PID}" \
      "${CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_OWNER_START}" \
      "${CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_CHILD_PID}" \
      "${CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_CHILD_START}"; then
      rm -f "${owner_path}"
      rm -f "${lock_dir}/.launchctl-ready"
      rmdir "${lock_dir}" 2>/dev/null || true
    fi
  elif [[ ! -L "${owner_path}" ]] &&
    sync_worker_launch_agent_path_is_old_orphan "${lock_dir}"; then
    rm -f "${owner_path}"
    rmdir "${lock_dir}" 2>/dev/null || true
  fi
  sync_worker_launch_agent_release_reclaim_marker "${reclaim_dir}"
}

sync_worker_launch_agent_acquire_lock() {
  local label="$1"
  local lock_root
  local reclaim_dir
  local owner_path
  local timeout_seconds
  local orphan_grace_seconds
  local started_at
  local now
  local previous_umask
  local lock_created

  if [[ "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_HELD}" -eq 1 ]]; then
    return 0
  fi

  lock_root="${CONTEXTWIKI_LAUNCH_AGENT_LOCK_ROOT:-${TMPDIR:-/tmp}/contextwiki-launch-agent-locks-$(id -u)}"
  [[ "${lock_root}" = /* ]] || {
    printf 'error: LaunchAgent lock root must be absolute: %s\n' \
      "${lock_root}" >&2
    return 1
  }
  [[ ! -L "${lock_root}" ]] || {
    printf 'error: LaunchAgent lock root must not be a symbolic link: %s\n' \
      "${lock_root}" >&2
    return 1
  }
  [[ ! -e "${lock_root}" || -d "${lock_root}" ]] || {
    printf 'error: LaunchAgent lock root is not a directory: %s\n' \
      "${lock_root}" >&2
    return 1
  }
  previous_umask="$(umask)"
  umask 077
  mkdir -p "${lock_root}"
  umask "${previous_umask}"
  [[ ! -L "${lock_root}" && -d "${lock_root}" ]] || {
    printf 'error: could not create a safe LaunchAgent lock root: %s\n' \
      "${lock_root}" >&2
    return 1
  }
  [[ -O "${lock_root}" ]] || {
    printf 'error: LaunchAgent lock root must be owned by the current user: %s\n' \
      "${lock_root}" >&2
    return 1
  }
  chmod 0700 "${lock_root}"

  timeout_seconds="${CONTEXTWIKI_LAUNCH_AGENT_LOCK_TIMEOUT_SECONDS:-30}"
  case "${timeout_seconds}" in
    ""|*[!0-9]*)
      printf 'error: LaunchAgent lock timeout must be whole seconds\n' >&2
      return 1
      ;;
  esac
  orphan_grace_seconds="${CONTEXTWIKI_LAUNCH_AGENT_LOCK_ORPHAN_GRACE_SECONDS:-60}"
  case "${orphan_grace_seconds}" in
    ""|*[!0-9]*)
      printf 'error: LaunchAgent orphan grace must be whole seconds\n' >&2
      return 1
      ;;
  esac
  if ((orphan_grace_seconds <= timeout_seconds)); then
    printf '%s\n' \
      'error: LaunchAgent orphan grace must exceed the operation lock timeout' \
      >&2
    return 1
  fi
  CONTEXTWIKI_LAUNCH_AGENT_LOCK_ORPHAN_GRACE="${orphan_grace_seconds}"

  CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR="${lock_root}/${label}.lock"
  reclaim_dir="${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}.reclaim"
  owner_path="${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}/owner"
  CONTEXTWIKI_LAUNCH_AGENT_LOCK_OWNER_START="$(
    sync_worker_launch_agent_process_start_id "$$"
  )"
  [[ -n "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_OWNER_START}" ]] || {
    printf 'error: could not identify LaunchAgent lock owner process\n' >&2
    return 1
  }

  started_at="${SECONDS}"
  while true; do
    if [[ -L "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}" ||
      (-e "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}" &&
      (! -d "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}" ||
      ! -O "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}")) ]]; then
      printf 'error: unsafe LaunchAgent operation lock: %s\n' \
        "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}" >&2
      return 1
    fi

    if [[ -e "${reclaim_dir}" || -L "${reclaim_dir}" ]]; then
      if ! sync_worker_launch_agent_reclaim_marker_is_safe "${reclaim_dir}"; then
        printf 'error: unsafe LaunchAgent recovery marker: %s\n' \
          "${reclaim_dir}" >&2
        return 1
      fi
      sync_worker_launch_agent_try_recover_stale_reclaim_marker \
        "${reclaim_dir}" || true
    fi

    lock_created=0
    previous_umask="$(umask)"
    umask 077
    if [[ ! -d "${reclaim_dir}" ]] &&
      mkdir "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}" 2>/dev/null; then
      lock_created=1
    fi
    umask "${previous_umask}"
    if [[ "${lock_created}" -eq 1 ]]; then
      if ! printf '%s\n%s\n' \
        "$$" \
        "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_OWNER_START}" > "${owner_path}"; then
        rmdir "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}" 2>/dev/null || true
        printf 'error: could not record LaunchAgent lock owner\n' >&2
        return 1
      fi
      CONTEXTWIKI_LAUNCH_AGENT_LOCK_HELD=1
      CONTEXTWIKI_LAUNCH_AGENT_LAUNCHCTL_PATH="$(type -P launchctl)"
      [[ -n "${CONTEXTWIKI_LAUNCH_AGENT_LAUNCHCTL_PATH}" ]] || {
        CONTEXTWIKI_LAUNCH_AGENT_LOCK_HELD=0
        rm -f "${owner_path}"
        rmdir "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}" 2>/dev/null || true
        printf 'error: could not identify launchctl executable\n' >&2
        return 1
      }
      sync_worker_launch_agent_enable_launchctl_child_tracking
      return 0
    fi

    sync_worker_launch_agent_try_recover_stale_lock \
      "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}" || true
    now="${SECONDS}"
    if ((now - started_at >= timeout_seconds)); then
      printf 'error: timed out waiting for LaunchAgent operation lock: %s\n' \
        "${label}" >&2
      return 1
    fi
    sleep 0.05
  done
}

sync_worker_launch_agent_release_lock() {
  local owner_path

  [[ "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_HELD}" -eq 1 ]] || return 0
  owner_path="${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}/owner"
  if sync_worker_launch_agent_read_published_lock_owner "${owner_path}" &&
    [[ "${CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_OWNER_PID}" == "$$" &&
    "${CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_OWNER_START}" == \
    "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_OWNER_START}" ]] &&
    { [[ -z "${CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_CHILD_PID}" ]] ||
      sync_worker_launch_agent_process_identity_is_stale \
        "${CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_CHILD_PID}" \
        "${CONTEXTWIKI_LAUNCH_AGENT_PUBLISHED_CHILD_START}"; }; then
    rm -f "${owner_path}"
    rm -f "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}/.launchctl-ready"
    rmdir "${CONTEXTWIKI_LAUNCH_AGENT_LOCK_DIR}" 2>/dev/null || true
  fi
  CONTEXTWIKI_LAUNCH_AGENT_LOCK_HELD=0
}
