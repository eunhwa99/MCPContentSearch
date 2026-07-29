#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  printf 'Usage: %s UV_PATH REPO_ROOT\n' "$0" >&2
  exit 2
fi

UV_PATH="$1"
REPO_ROOT="$2"
DIAGNOSTIC_LOG_PATH="${CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_PATH:-}"
DIAGNOSTIC_LOG_MAX_BYTES="${CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES:-1048576}"
SANITIZER_PYTHON_PATH="${CONTEXTWIKI_SYNC_WORKER_SANITIZER_PYTHON_PATH:-}"

if [[ -z "${DIAGNOSTIC_LOG_PATH}" ]]; then
  printf 'error: CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_PATH is required\n' >&2
  exit 2
fi
if [[ "${UV_PATH}" != /* ]] || [[ ! -x "${UV_PATH}" ]]; then
  printf 'error: absolute executable uv path is required\n' >&2
  exit 2
fi
if [[ "${REPO_ROOT}" != /* ]] ||
  [[ ! -d "${REPO_ROOT}" ]] ||
  [[ ! -f "${REPO_ROOT}/core/error_sanitizer.py" ]]; then
  printf 'error: absolute ContextWiki repository path is required\n' >&2
  exit 2
fi
if [[ ! "${DIAGNOSTIC_LOG_MAX_BYTES}" =~ ^[0-9]+$ ]] ||
  [[ "${DIAGNOSTIC_LOG_MAX_BYTES}" -lt 1024 ]]; then
  printf 'error: CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES must be at least 1024\n' \
    >&2
  exit 2
fi

DIAGNOSTIC_LOG_COMPACT_BYTES=$((DIAGNOSTIC_LOG_MAX_BYTES / 2))
DIAGNOSTIC_CHUNK_BYTES=$((DIAGNOSTIC_LOG_MAX_BYTES / 8))
if [[ "${DIAGNOSTIC_CHUNK_BYTES}" -gt 65536 ]]; then
  DIAGNOSTIC_CHUNK_BYTES=65536
fi

DIAGNOSTIC_LOG_DIR="$(dirname "${DIAGNOSTIC_LOG_PATH}")"
mkdir -p "${DIAGNOSTIC_LOG_DIR}"
touch "${DIAGNOSTIC_LOG_PATH}"
chmod 0600 "${DIAGNOSTIC_LOG_PATH}"
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
DIAGNOSTIC_CHUNK_PATH="$(mktemp "${DIAGNOSTIC_LOG_DIR}/.sync-worker-chunk.XXXXXX")"

# shellcheck disable=SC2329  # Called indirectly by the EXIT trap below.
cleanup_runtime_files() {
  rm -f "${DIAGNOSTIC_PIPE_PATH}" "${DIAGNOSTIC_CHUNK_PATH}"
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

run_diagnostic_sanitizer() {
  if [[ -n "${SANITIZER_PYTHON_PATH}" ]]; then
    exec "${SANITIZER_PYTHON_PATH}" -u -m core.error_sanitizer --stream
  fi
  exec "${UV_PATH}" \
    --directory "${REPO_ROOT}" \
    run --locked python -u -m core.error_sanitizer --stream
}

bounded_diagnostic_writer() {
  (
    cd "${REPO_ROOT}"
    run_diagnostic_sanitizer
  ) |
    while true; do
      : > "${DIAGNOSTIC_CHUNK_PATH}"
      dd bs="${DIAGNOSTIC_CHUNK_BYTES}" count=1 \
        of="${DIAGNOSTIC_CHUNK_PATH}" 2>/dev/null
      if [[ ! -s "${DIAGNOSTIC_CHUNK_PATH}" ]]; then
        break
      fi
      append_diagnostic_chunk
    done
}

child_pid=""
pending_signal=""
# shellcheck disable=SC2329  # Called indirectly by the signal traps below.
forward_signal() {
  local signal_name="$1"
  if [[ -n "${child_pid}" ]]; then
    kill "-${signal_name}" -- "-${child_pid}" 2>/dev/null || true
  elif [[ -z "${pending_signal}" ]]; then
    pending_signal="${signal_name}"
  fi
}
trap 'forward_signal TERM' TERM
trap 'forward_signal INT' INT

bounded_diagnostic_writer < "${DIAGNOSTIC_PIPE_PATH}" &
writer_pid="$!"
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
while true; do
  wait "${child_pid}"
  worker_status="$?"
  if ! kill -0 "${child_pid}" 2>/dev/null; then
    break
  fi
done
child_pid=""
while true; do
  wait "${writer_pid}"
  writer_status="$?"
  if ! kill -0 "${writer_pid}" 2>/dev/null; then
    break
  fi
done
if [[ "${writer_status}" -ne 0 ]]; then
  append_sanitizer_failure_diagnostic
fi
set -e
if [[ "${worker_status}" -eq 0 && "${writer_status}" -ne 0 ]]; then
  worker_status="${writer_status}"
fi
exit "${worker_status}"
