#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $# -ne 2 ]]; then
  printf 'Usage: %s UV_PATH REPO_ROOT\n' "$0" >&2
  exit 2
fi

UV_PATH="$1"
REPO_ROOT="$2"
DIAGNOSTIC_LOG_PATH="${CONTEXTZIP_SYNC_WORKER_DIAGNOSTIC_LOG_PATH:-}"
DIAGNOSTIC_LOG_MAX_BYTES="${CONTEXTZIP_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES:-1048576}"
SANITIZER_PYTHON_PATH="${CONTEXTZIP_SYNC_WORKER_SANITIZER_PYTHON_PATH:-}"

if [[ -z "${DIAGNOSTIC_LOG_PATH}" ]]; then
  printf 'error: CONTEXTZIP_SYNC_WORKER_DIAGNOSTIC_LOG_PATH is required\n' >&2
  exit 2
fi
if [[ "${UV_PATH}" != /* ]] || [[ ! -x "${UV_PATH}" ]]; then
  printf 'error: absolute executable uv path is required\n' >&2
  exit 2
fi
if [[ "${REPO_ROOT}" != /* ]] ||
  [[ ! -d "${REPO_ROOT}" ]] ||
  [[ ! -f "${REPO_ROOT}/core/error_sanitizer.py" ]]; then
  printf 'error: absolute ContextZip repository path is required\n' >&2
  exit 2
fi
if [[ ! "${DIAGNOSTIC_LOG_MAX_BYTES}" =~ ^[0-9]+$ ]] ||
  [[ "${DIAGNOSTIC_LOG_MAX_BYTES}" -lt 1024 ]]; then
  printf 'error: CONTEXTZIP_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES must be at least 1024\n' \
    >&2
  exit 2
fi

DIAGNOSTIC_LOG_COMPACT_BYTES=$((DIAGNOSTIC_LOG_MAX_BYTES / 2))
DIAGNOSTIC_CHUNK_BYTES=$((DIAGNOSTIC_LOG_MAX_BYTES / 8))
if [[ "${DIAGNOSTIC_CHUNK_BYTES}" -gt 65536 ]]; then
  DIAGNOSTIC_CHUNK_BYTES=65536
fi

DIAGNOSTIC_LOG_DIR="$(dirname "${DIAGNOSTIC_LOG_PATH}")"

runtime_path_mode() {
  local path="$1"
  local mode

  mode="$(/usr/bin/stat -f '%Lp' "${path}" 2>/dev/null)" ||
    mode="$(/usr/bin/stat -c '%a' "${path}" 2>/dev/null)" ||
    return 1
  printf '%s\n' "${mode}"
}

validate_canonical_absolute_path() {
  local path="$1"
  local framed_path="/${path#/}/"

  if [[ "${path}" != /* ]] ||
    [[ "${framed_path}" == *"//"* ]] ||
    [[ "${framed_path}" == *"/./"* ]] ||
    [[ "${framed_path}" == *"/../"* ]]; then
    return 1
  fi
}

ensure_private_runtime_directory() {
  local directory="$1"
  local component
  local current=""
  local final_mode

  validate_canonical_absolute_path "${directory}" || return 1
  IFS='/' read -r -a components <<< "${directory#/}"
  for component in "${components[@]}"; do
    [[ -n "${component}" ]] || continue
    current="${current}/${component}"
    if [[ -L "${current}" ]]; then
      return 1
    fi
    if [[ -e "${current}" ]]; then
      [[ -d "${current}" ]] || return 1
      continue
    fi
    mkdir -m 0700 "${current}" || return 1
    [[ ! -L "${current}" ]] && [[ -d "${current}" ]] || return 1
  done

  [[ -d "${directory}" ]] &&
    [[ ! -L "${directory}" ]] &&
    [[ -O "${directory}" ]] ||
    return 1
  final_mode="$(runtime_path_mode "${directory}")" || return 1
  [[ "${final_mode}" == "700" ]]
}

ensure_private_runtime_file() {
  local path="$1"
  local file_mode

  validate_canonical_absolute_path "${path}" || return 1
  if [[ -e "${path}" ]] || [[ -L "${path}" ]]; then
    [[ ! -L "${path}" ]] &&
      [[ -f "${path}" ]] &&
      [[ -O "${path}" ]] ||
      return 1
  else
    (set -o noclobber; : > "${path}") 2>/dev/null || return 1
  fi
  chmod 0600 "${path}" || return 1
  [[ ! -L "${path}" ]] &&
    [[ -f "${path}" ]] &&
    [[ -O "${path}" ]] ||
    return 1
  file_mode="$(runtime_path_mode "${path}")" || return 1
  [[ "${file_mode}" == "600" ]]
}

if ! ensure_private_runtime_directory "${DIAGNOSTIC_LOG_DIR}" ||
  ! ensure_private_runtime_file "${DIAGNOSTIC_LOG_PATH}"; then
  printf 'error: unsafe sync worker diagnostic log path\n' >&2
  exit 73
fi
if [[ -n "${SANITIZER_PYTHON_PATH}" ]] &&
  { [[ "${SANITIZER_PYTHON_PATH}" != /* ]] ||
    [[ ! -x "${SANITIZER_PYTHON_PATH}" ]]; }; then
  printf 'error: startup diagnostic sanitizer is unavailable\n' \
    >> "${DIAGNOSTIC_LOG_PATH}"
  exit 70
fi

bound_diagnostic_log() {
  local current_size
  local temporary_path

  current_size="$(wc -c < "${DIAGNOSTIC_LOG_PATH}")"
  current_size="${current_size//[[:space:]]/}"
  if [[ "${current_size}" -le "${DIAGNOSTIC_LOG_MAX_BYTES}" ]]; then
    return
  fi

  temporary_path="$(mktemp "${DIAGNOSTIC_LOG_DIR}/.sync-worker-startup.XXXXXX")"
  tail -c "${DIAGNOSTIC_LOG_MAX_BYTES}" "${DIAGNOSTIC_LOG_PATH}" \
    > "${temporary_path}"
  chmod 0600 "${temporary_path}"
  mv "${temporary_path}" "${DIAGNOSTIC_LOG_PATH}"
}

bound_diagnostic_log

DIAGNOSTIC_PIPE_PATH="$(mktemp "${DIAGNOSTIC_LOG_DIR}/.sync-worker-pipe.XXXXXX")"
rm -f "${DIAGNOSTIC_PIPE_PATH}"
mkfifo "${DIAGNOSTIC_PIPE_PATH}"
DIAGNOSTIC_SANITIZED_PIPE_PATH="$(
  mktemp "${DIAGNOSTIC_LOG_DIR}/.sync-worker-sanitized-pipe.XXXXXX"
)"
rm -f "${DIAGNOSTIC_SANITIZED_PIPE_PATH}"
mkfifo "${DIAGNOSTIC_SANITIZED_PIPE_PATH}"
DIAGNOSTIC_CHUNK_PATH="$(mktemp "${DIAGNOSTIC_LOG_DIR}/.sync-worker-chunk.XXXXXX")"
DIAGNOSTIC_SANITIZER_PID_PATH="$(
  mktemp "${DIAGNOSTIC_LOG_DIR}/.sync-worker-sanitizer-pid.XXXXXX"
)"

# shellcheck disable=SC2329  # Called indirectly by the EXIT trap below.
cleanup_runtime_files() {
  rm -f \
    "${DIAGNOSTIC_PIPE_PATH}" \
    "${DIAGNOSTIC_SANITIZED_PIPE_PATH}" \
    "${DIAGNOSTIC_CHUNK_PATH}" \
    "${DIAGNOSTIC_SANITIZER_PID_PATH}"
}
trap cleanup_runtime_files EXIT

append_diagnostic_chunk() {
  local current_size
  local chunk_size
  local replacement_path

  current_size="$(wc -c < "${DIAGNOSTIC_LOG_PATH}")"
  current_size="${current_size//[[:space:]]/}"
  chunk_size="$(wc -c < "${DIAGNOSTIC_CHUNK_PATH}")"
  chunk_size="${chunk_size//[[:space:]]/}"
  if [[ $((current_size + chunk_size)) -le "${DIAGNOSTIC_LOG_MAX_BYTES}" ]]; then
    cat "${DIAGNOSTIC_CHUNK_PATH}" >> "${DIAGNOSTIC_LOG_PATH}"
    return
  fi

  replacement_path="$(mktemp "${DIAGNOSTIC_LOG_DIR}/.sync-worker-startup.XXXXXX")"
  {
    cat "${DIAGNOSTIC_LOG_PATH}"
    cat "${DIAGNOSTIC_CHUNK_PATH}"
  } | tail -c "${DIAGNOSTIC_LOG_COMPACT_BYTES}" > "${replacement_path}"
  chmod 0600 "${replacement_path}"
  mv "${replacement_path}" "${DIAGNOSTIC_LOG_PATH}"
}

append_sanitizer_failure_diagnostic() {
  : > "${DIAGNOSTIC_CHUNK_PATH}"
  printf 'error: startup diagnostic sanitizer failed\n' \
    > "${DIAGNOSTIC_CHUNK_PATH}"
  append_diagnostic_chunk
}

append_writer_drain_cleanup_diagnostic() {
  : > "${DIAGNOSTIC_CHUNK_PATH}"
  printf 'error: startup diagnostic writer drain required forced cleanup\n' \
    > "${DIAGNOSTIC_CHUNK_PATH}"
  append_diagnostic_chunk
}

append_writer_failure_cleanup_diagnostic() {
  : > "${DIAGNOSTIC_CHUNK_PATH}"
  printf 'error: startup diagnostic writer failed and required forced cleanup\n' \
    > "${DIAGNOSTIC_CHUNK_PATH}"
  append_diagnostic_chunk
}

append_unexpected_pipeline_exit_diagnostic() {
  : > "${DIAGNOSTIC_CHUNK_PATH}"
  printf 'error: startup diagnostic pipeline stopped unexpectedly\n' \
    > "${DIAGNOSTIC_CHUNK_PATH}"
  append_diagnostic_chunk
}

run_diagnostic_sanitizer() {
  if [[ -n "${SANITIZER_PYTHON_PATH}" ]]; then
    exec "${SANITIZER_PYTHON_PATH}" -u -m core.error_sanitizer --stream
  fi
  exec "${UV_PATH}" \
    --directory "${REPO_ROOT}" \
    run --locked python -u -m core.error_sanitizer --stream
}

bounded_diagnostic_writer() {
  local sanitizer_pid
  local sanitizer_status

  set +m
  exec 4<&0
  (
    cd "${REPO_ROOT}"
    run_diagnostic_sanitizer
  ) <&4 > "${DIAGNOSTIC_SANITIZED_PIPE_PATH}" &
  sanitizer_pid="$!"
  exec 4<&-
  printf '%s\n' "${sanitizer_pid}" > "${DIAGNOSTIC_SANITIZER_PID_PATH}"

  exec 3< "${DIAGNOSTIC_SANITIZED_PIPE_PATH}"
  while true; do
    : > "${DIAGNOSTIC_CHUNK_PATH}"
    dd bs="${DIAGNOSTIC_CHUNK_BYTES}" count=1 \
      of="${DIAGNOSTIC_CHUNK_PATH}" <&3 2>/dev/null
    if [[ ! -s "${DIAGNOSTIC_CHUNK_PATH}" ]]; then
      break
    fi
    append_diagnostic_chunk
  done
  exec 3<&-

  set +e
  wait "${sanitizer_pid}"
  sanitizer_status="$?"
  set -e
  return "${sanitizer_status}"
}

child_pid=""
writer_pid=""
pending_signal=""
writer_drain_active=""
writer_drain_signal=""
writer_forced_cleanup=""
writer_failed_first=""
writer_reaped=""
writer_status=""
writer_exit_notified=""
writer_exited_while_worker_running=""

signal_writer_tree() {
  local signal_name="$1"
  local sanitizer_pid=""

  if [[ -z "${writer_pid}" ]]; then
    return
  fi
  IFS= read -r sanitizer_pid < "${DIAGNOSTIC_SANITIZER_PID_PATH}" || true
  if [[ "${sanitizer_pid}" =~ ^[1-9][0-9]*$ ]]; then
    kill "-${signal_name}" -- "-${sanitizer_pid}" 2>/dev/null ||
      kill "-${signal_name}" "${sanitizer_pid}" 2>/dev/null ||
      true
  fi
  kill "-${signal_name}" -- "-${writer_pid}" 2>/dev/null ||
    kill "-${signal_name}" "${writer_pid}" 2>/dev/null ||
    true
}

writer_tree_is_alive() {
  local sanitizer_pid=""

  if [[ -n "${writer_pid}" ]] && kill -0 "${writer_pid}" 2>/dev/null; then
    return 0
  fi
  IFS= read -r sanitizer_pid < "${DIAGNOSTIC_SANITIZER_PID_PATH}" || true
  if [[ "${sanitizer_pid}" =~ ^[1-9][0-9]*$ ]] &&
    kill -0 "${sanitizer_pid}" 2>/dev/null; then
    return 0
  fi
  return 1
}

signal_worker_tree() {
  local signal_name="$1"

  if [[ -z "${child_pid}" ]]; then
    return
  fi
  kill "-${signal_name}" -- "-${child_pid}" 2>/dev/null ||
    kill "-${signal_name}" "${child_pid}" 2>/dev/null ||
    true
}

# shellcheck disable=SC2329  # Called indirectly by the signal traps below.
forward_signal() {
  local signal_name="$1"
  if [[ -n "${child_pid}" ]]; then
    if kill -0 "${child_pid}" 2>/dev/null; then
      signal_worker_tree "${signal_name}"
      return
    fi
  fi
  if [[ -n "${writer_drain_active}" ]]; then
    if [[ -z "${writer_drain_signal}" ]]; then
      writer_drain_signal="${signal_name}"
    fi
  elif [[ -z "${pending_signal}" ]]; then
    pending_signal="${signal_name}"
  fi
}
trap 'forward_signal TERM' TERM
trap 'forward_signal INT' INT
trap 'writer_exit_notified=1' USR1

# Give the writer and its sanitizer pipeline a process group that can be
# terminated without also signaling this wrapper.
wrapper_pid="$$"
set -m
(
  set +e
  (
    set -e
    bounded_diagnostic_writer < "${DIAGNOSTIC_PIPE_PATH}"
  )
  completed_writer_status="$?"
  set +e
  kill -USR1 "${wrapper_pid}" 2>/dev/null || true
  exit "${completed_writer_status}"
) &
writer_pid="$!"
set +m
# Job control keeps SIGINT at its default disposition for the asynchronous
# worker; without it, non-interactive Bash starts background jobs with INT
# ignored before the worker executable can install its own handler.
set -m
(
  trap - INT TERM
  exec "${UV_PATH}" \
    --directory "${REPO_ROOT}" \
    run --locked python -m indexing.sync_worker
) > "${DIAGNOSTIC_PIPE_PATH}" 2>&1 &
child_pid="$!"
set +m
if [[ -n "${pending_signal}" ]]; then
  kill "-${pending_signal}" -- "-${child_pid}" 2>/dev/null || true
fi
set +e
writer_drain_active=1
while true; do
  if [[ -n "${writer_exit_notified}" ]] &&
    kill -0 "${child_pid}" 2>/dev/null; then
    writer_exited_while_worker_running=1
    break
  fi
  wait "${child_pid}"
  worker_status="$?"
  if ! kill -0 "${child_pid}" 2>/dev/null; then
    break
  fi
done

if [[ -n "${writer_exited_while_worker_running}" ]]; then
  signal_worker_tree TERM
  (
    /bin/sleep 1
    if kill -0 "${child_pid}" 2>/dev/null; then
      signal_worker_tree KILL
    fi
  ) &
  worker_escalation_pid="$!"
  while true; do
    wait "${child_pid}"
    worker_status="$?"
    if ! kill -0 "${child_pid}" 2>/dev/null; then
      break
    fi
  done
  kill -TERM "${worker_escalation_pid}" 2>/dev/null || true
  wait "${worker_escalation_pid}" 2>/dev/null || true
fi
child_pid=""

writer_drain_polls=0
while writer_tree_is_alive; do
  if ! kill -0 "${writer_pid}" 2>/dev/null; then
    wait "${writer_pid}"
    writer_status="$?"
    writer_reaped=1
    if writer_tree_is_alive; then
      writer_forced_cleanup=1
      writer_failed_first=1
    fi
    break
  fi
  /bin/sleep 0.05
  writer_drain_polls=$((writer_drain_polls + 1))
  if [[ -n "${writer_drain_signal}" ]] &&
    [[ "${writer_drain_polls}" -ge 20 ]]; then
    writer_forced_cleanup=1
    break
  fi
  if [[ "${writer_drain_polls}" -ge 100 ]]; then
    writer_forced_cleanup=1
    break
  fi
done

if [[ -n "${writer_forced_cleanup}" ]]; then
  signal_writer_tree TERM
  writer_drain_polls=0
  while writer_tree_is_alive &&
    [[ "${writer_drain_polls}" -lt 20 ]]; do
    /bin/sleep 0.05
    writer_drain_polls=$((writer_drain_polls + 1))
  done
  if writer_tree_is_alive; then
    signal_writer_tree KILL
    writer_drain_polls=0
    while writer_tree_is_alive &&
      [[ "${writer_drain_polls}" -lt 20 ]]; do
      /bin/sleep 0.05
      writer_drain_polls=$((writer_drain_polls + 1))
    done
  fi
fi

if [[ -z "${writer_reaped}" ]]; then
  while true; do
    wait "${writer_pid}"
    writer_status="$?"
    if ! kill -0 "${writer_pid}" 2>/dev/null; then
      break
    fi
  done
fi
writer_drain_active=""
if [[ -n "${writer_failed_first}" ]]; then
  append_writer_failure_cleanup_diagnostic
elif [[ -n "${writer_forced_cleanup}" ]]; then
  append_writer_drain_cleanup_diagnostic
elif [[ -n "${writer_exited_while_worker_running}" ]] &&
  [[ "${writer_status}" -eq 0 ]]; then
  append_unexpected_pipeline_exit_diagnostic
elif [[ "${writer_status}" -ne 0 ]]; then
  append_sanitizer_failure_diagnostic
fi
set -e
if [[ "${worker_status}" -eq 0 ]] &&
  [[ "${writer_status}" -ne 0 ]] &&
  [[ -z "${writer_forced_cleanup}" ]]; then
  worker_status="${writer_status}"
fi
if [[ -n "${writer_exited_while_worker_running}" ]] &&
  [[ "${writer_status}" -ne 0 ]]; then
  worker_status="${writer_status}"
fi
if [[ -n "${writer_exited_while_worker_running}" ]] &&
  [[ "${writer_status}" -eq 0 ]]; then
  worker_status=70
fi
exit "${worker_status}"
