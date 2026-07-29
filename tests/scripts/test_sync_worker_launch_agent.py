from __future__ import annotations

import os
from pathlib import Path
import plistlib
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install_sync_worker_launch_agent.sh"
RUN_SCRIPT = REPO_ROOT / "scripts" / "run_sync_worker_launch_agent.sh"
MANAGEMENT_SCRIPTS = (
    REPO_ROOT / "scripts" / "status_sync_worker_launch_agent.sh",
    REPO_ROOT / "scripts" / "restart_sync_worker_launch_agent.sh",
    REPO_ROOT / "scripts" / "uninstall_sync_worker_launch_agent.sh",
)
OPERATION_LOCK_HELPER = (
    REPO_ROOT / "scripts" / "sync_worker_launch_agent_lock.sh"
)
LABEL = "com.eunaverse.contextwiki.sync-worker"


def _fake_executable(path: Path) -> Path:
    path.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _fake_launchctl(fake_bin: Path, tmp_path: Path) -> tuple[Path, Path]:
    call_log = tmp_path / "launchctl-calls"
    loaded_state = tmp_path / "launchctl-loaded"
    fail_next_bootstrap = tmp_path / "launchctl-fail-next-bootstrap"
    launchctl = fake_bin / "launchctl"
    launchctl.write_text(
        "\n".join(
            (
                "#!/usr/bin/env sh",
                f"printf '%s\\n' \"$*\" >> {shlex.quote(str(call_log))}",
                'case "$1" in',
                f"  print) test -f {shlex.quote(str(loaded_state))} ;;",
                (
                    "  bootstrap) "
                    f"if test -f {shlex.quote(str(fail_next_bootstrap))}; then "
                    f"rm -f {shlex.quote(str(fail_next_bootstrap))}; "
                    "exit 42; "
                    "fi; "
                    f"touch {shlex.quote(str(loaded_state))} ;;"
                ),
                (
                    "  bootout) "
                    'if test "$#" -eq 3 && test ! -f "$3"; then exit 44; fi; '
                    f"rm -f {shlex.quote(str(loaded_state))} ;;"
                ),
                "  *) exit 2 ;;",
                "esac",
                "",
            )
        ),
        encoding="utf-8",
    )
    launchctl.chmod(0o755)
    uname = fake_bin / "uname"
    uname.write_text("#!/usr/bin/env sh\nprintf 'Darwin\\n'\n", encoding="utf-8")
    uname.chmod(0o755)
    return call_log, loaded_state


def _blocking_fake_launchctl(fake_bin: Path, tmp_path: Path) -> tuple[Path, Path]:
    call_log = tmp_path / "serialized-launchctl-calls"
    loaded_state = tmp_path / "serialized-launchctl-loaded"
    launchctl = fake_bin / "launchctl"
    launchctl.write_text(
        "\n".join(
            (
                "#!/usr/bin/env sh",
                'operation="${CONTEXTWIKI_TEST_OPERATION_ID:-unknown}"',
                (
                    f"printf '%s:%s\\n' \"$operation\" \"$*\" >> "
                    f"{shlex.quote(str(call_log))}"
                ),
                (
                    'if test "${CONTEXTWIKI_TEST_BLOCK_COMMAND:-}" = "$1"; then'
                ),
                '  touch "${CONTEXTWIKI_TEST_BLOCK_ENTERED}"',
                (
                    '  while test ! -f "${CONTEXTWIKI_TEST_BLOCK_RELEASE}"; do '
                    "sleep 0.01; done"
                ),
                "fi",
                'case "$1" in',
                f"  print) test -f {shlex.quote(str(loaded_state))} ;;",
                (
                    "  bootstrap) "
                    f"touch {shlex.quote(str(loaded_state))} ;;"
                ),
                (
                    "  bootout) "
                    f"rm -f {shlex.quote(str(loaded_state))} ;;"
                ),
                (
                    "  kill) "
                    f"test -f {shlex.quote(str(loaded_state))} ;;"
                ),
                "  *) exit 2 ;;",
                "esac",
                "",
            )
        ),
        encoding="utf-8",
    )
    launchctl.chmod(0o755)
    uname = fake_bin / "uname"
    uname.write_text("#!/usr/bin/env sh\nprintf 'Darwin\\n'\n", encoding="utf-8")
    uname.chmod(0o755)
    return call_log, loaded_state


def _wait_for_path(path: Path, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")


def _install_command(
    *,
    fake_uv: Path,
    log_dir: Path,
    launch_agents_dir: Path,
) -> list[str]:
    return [
        str(INSTALL_SCRIPT),
        "--repo-root",
        str(REPO_ROOT),
        "--uv-path",
        str(fake_uv),
        "--log-dir",
        str(log_dir),
        "--launch-agents-dir",
        str(launch_agents_dir),
    ]


def _fake_stat(
    fake_bin: Path,
    *,
    mode: str = "700",
    owner_uid: int | None = None,
) -> Path:
    owner_uid = os.getuid() if owner_uid is None else owner_uid
    fake_stat = fake_bin / "stat"
    fake_stat.write_text(
        "\n".join(
            (
                "#!/usr/bin/env sh",
                'case "$1:$2" in',
                f"  -f:%OLp|-c:%a) printf '{mode}\\n' ;;",
                f"  -f:%u|-c:%u) printf '{owner_uid}\\n' ;;",
                "  *) exit 2 ;;",
                "esac",
                "",
            )
        ),
        encoding="utf-8",
    )
    fake_stat.chmod(0o755)
    return fake_stat


def test_mutating_launch_agent_helpers_share_packaged_operation_lock():
    assert OPERATION_LOCK_HELPER.is_file()
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY scripts ./scripts" in dockerfile

    helper_name = OPERATION_LOCK_HELPER.name
    mutating_scripts = (
        INSTALL_SCRIPT,
        REPO_ROOT / "scripts" / "restart_sync_worker_launch_agent.sh",
        REPO_ROOT / "scripts" / "uninstall_sync_worker_launch_agent.sh",
    )
    for script_path in mutating_scripts:
        script = script_path.read_text(encoding="utf-8")
        assert f'source "${{SCRIPT_DIR}}/{helper_name}"' in script


def test_launch_agent_operation_lock_recovers_dead_owner_and_bounds_unknown_owner(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _blocking_fake_launchctl(fake_bin, tmp_path)
    lock_root = tmp_path / "locks"
    lock_dir = lock_root / f"{LABEL}.lock"
    lock_dir.mkdir(parents=True)
    exited_process = subprocess.Popen(["/usr/bin/true"])
    exited_process.wait(timeout=5)
    (lock_dir / "owner").write_text(
        f"{exited_process.pid}\nexited-owner\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ROOT": str(lock_root),
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_TIMEOUT_SECONDS": "1",
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ORPHAN_GRACE_SECONDS": "60",
    }

    recovered = subprocess.run(
        [str(REPO_ROOT / "scripts" / "restart_sync_worker_launch_agent.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert recovered.returncode != 0
    assert "launch agent operation lock" not in recovered.stderr.lower()
    assert not lock_dir.exists()

    lock_dir.mkdir()
    (lock_dir / "owner").write_text("not-a-valid-owner\n", encoding="utf-8")
    bounded = subprocess.run(
        [str(REPO_ROOT / "scripts" / "restart_sync_worker_launch_agent.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )

    assert bounded.returncode != 0
    assert (
        "timed out waiting for launchagent operation lock"
        in bounded.stderr.lower()
    )
    assert lock_dir.exists()


@pytest.mark.parametrize("owner_contents", [None, "partial-owner\n"])
def test_launch_agent_operation_lock_recovers_old_unpublished_owner(
    tmp_path: Path,
    owner_contents: str | None,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _blocking_fake_launchctl(fake_bin, tmp_path)
    lock_root = tmp_path / "locks"
    lock_dir = lock_root / f"{LABEL}.lock"
    lock_dir.mkdir(parents=True)
    if owner_contents is not None:
        (lock_dir / "owner").write_text(owner_contents, encoding="utf-8")
    old_timestamp = time.time() - 120
    os.utime(lock_dir, (old_timestamp, old_timestamp))
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ROOT": str(lock_root),
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_TIMEOUT_SECONDS": "2",
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ORPHAN_GRACE_SECONDS": "3",
    }

    recovered = subprocess.run(
        [str(REPO_ROOT / "scripts" / "restart_sync_worker_launch_agent.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )

    assert recovered.returncode != 0
    assert "launchagent operation lock" not in recovered.stderr.lower()
    assert not lock_dir.exists()


def test_launch_agent_operation_lock_never_reclaims_old_published_live_owner(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _blocking_fake_launchctl(fake_bin, tmp_path)
    lock_root = tmp_path / "locks"
    lock_dir = lock_root / f"{LABEL}.lock"
    lock_dir.mkdir(parents=True)
    live_owner = subprocess.Popen(["/bin/sleep", "5"])
    try:
        owner_start = subprocess.run(
            ["ps", "-p", str(live_owner.pid), "-o", "lstart="],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        (lock_dir / "owner").write_text(
            f"{live_owner.pid}\n{owner_start}\n",
            encoding="utf-8",
        )
        old_timestamp = time.time() - 120
        os.utime(lock_dir, (old_timestamp, old_timestamp))
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ROOT": str(lock_root),
            "CONTEXTWIKI_LAUNCH_AGENT_LOCK_TIMEOUT_SECONDS": "1",
            "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ORPHAN_GRACE_SECONDS": "3",
        }

        result = subprocess.run(
            [str(REPO_ROOT / "scripts" / "restart_sync_worker_launch_agent.sh")],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
        )

        assert result.returncode != 0
        assert "timed out waiting for launchagent operation lock" in (
            result.stderr.lower()
        )
        assert lock_dir.exists()
    finally:
        live_owner.terminate()
        live_owner.wait(timeout=5)


@pytest.mark.parametrize(
    ("child_state", "expected_result"),
    [
        ("live-matching", "protected"),
        ("live-reused-pid", "stale"),
        ("exited", "stale"),
    ],
)
def test_published_child_identity_staleness_truth_table(
    child_state: str,
    expected_result: str,
):
    exited_owner = subprocess.Popen(["/usr/bin/true"])
    exited_owner.wait(timeout=5)
    live_child = subprocess.Popen(["/bin/sleep", "5"])
    exited_child = subprocess.Popen(["/usr/bin/true"])
    exited_child.wait(timeout=5)
    try:
        live_child_start = subprocess.run(
            ["ps", "-p", str(live_child.pid), "-o", "lstart="],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if child_state == "live-matching":
            child_pid = live_child.pid
            child_start = live_child_start
        elif child_state == "live-reused-pid":
            child_pid = live_child.pid
            child_start = "different-process-start"
        else:
            child_pid = exited_child.pid
            child_start = "exited-process-start"

        result = subprocess.run(
            [
                "/bin/bash",
                "-c",
                (
                    'source "$1"; '
                    'if sync_worker_launch_agent_published_owner_is_stale '
                    '"$2" "dead-owner" "$3" "$4"; then '
                    "printf 'stale\\n'; else printf 'protected\\n'; fi"
                ),
                "bash",
                str(OPERATION_LOCK_HELPER),
                str(exited_owner.pid),
                str(child_pid),
                child_start,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        assert result.stdout.strip() == expected_result
    finally:
        live_child.terminate()
        live_child.wait(timeout=5)


def test_launch_agent_operation_lock_never_recovers_through_a_symlink(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _blocking_fake_launchctl(fake_bin, tmp_path)
    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    attacker_target = tmp_path / "do-not-touch"
    attacker_target.mkdir()
    exited_process = subprocess.Popen(["/usr/bin/true"])
    exited_process.wait(timeout=5)
    owner_path = attacker_target / "owner"
    owner_contents = f"{exited_process.pid}\nexited-owner\n"
    owner_path.write_text(owner_contents, encoding="utf-8")
    lock_dir = lock_root / f"{LABEL}.lock"
    lock_dir.symlink_to(attacker_target, target_is_directory=True)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ROOT": str(lock_root),
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_TIMEOUT_SECONDS": "1",
    }

    result = subprocess.run(
        [str(REPO_ROOT / "scripts" / "restart_sync_worker_launch_agent.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )

    assert result.returncode != 0
    assert "unsafe launchagent operation lock" in result.stderr.lower()
    assert lock_dir.is_symlink()
    assert owner_path.read_text(encoding="utf-8") == owner_contents


@pytest.mark.parametrize("owner_contents", [None, "partial-owner\n"])
def test_reclaim_marker_helper_recovers_grace_expired_unpublished_state(
    tmp_path: Path,
    owner_contents: str | None,
):
    reclaim_dir = tmp_path / f"{LABEL}.lock.reclaim"
    reclaim_dir.mkdir()
    if owner_contents is not None:
        (reclaim_dir / "owner").write_text(owner_contents, encoding="utf-8")
    old_timestamp = time.time() - 120
    os.utime(reclaim_dir, (old_timestamp, old_timestamp))

    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            (
                'source "$1"; '
                "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ORPHAN_GRACE=3; "
                'sync_worker_launch_agent_try_recover_stale_reclaim_marker "$2"'
            ),
            "bash",
            str(OPERATION_LOCK_HELPER),
            str(reclaim_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not reclaim_dir.exists()


@pytest.mark.parametrize("owner_contents", [None, "partial-owner\n"])
def test_launch_agent_operation_lock_recovers_old_reclaim_marker(
    tmp_path: Path,
    owner_contents: str | None,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _blocking_fake_launchctl(fake_bin, tmp_path)
    lock_root = tmp_path / "locks"
    reclaim_dir = lock_root / f"{LABEL}.lock.reclaim"
    reclaim_dir.mkdir(parents=True)
    if owner_contents is not None:
        (reclaim_dir / "owner").write_text(
            owner_contents,
            encoding="utf-8",
        )
    old_timestamp = time.time() - 120
    os.utime(reclaim_dir, (old_timestamp, old_timestamp))
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ROOT": str(lock_root),
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_TIMEOUT_SECONDS": "2",
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ORPHAN_GRACE_SECONDS": "3",
    }

    result = subprocess.run(
        [str(REPO_ROOT / "scripts" / "restart_sync_worker_launch_agent.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )

    assert result.returncode != 0
    assert "launchagent operation lock" not in result.stderr.lower()
    assert not reclaim_dir.exists()


def test_launch_agent_operation_lock_preserves_fresh_reclaim_marker(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _blocking_fake_launchctl(fake_bin, tmp_path)
    lock_root = tmp_path / "locks"
    reclaim_dir = lock_root / f"{LABEL}.lock.reclaim"
    reclaim_dir.mkdir(parents=True)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ROOT": str(lock_root),
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_TIMEOUT_SECONDS": "1",
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ORPHAN_GRACE_SECONDS": "3",
    }

    result = subprocess.run(
        [str(REPO_ROOT / "scripts" / "restart_sync_worker_launch_agent.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )

    assert result.returncode != 0
    assert "timed out waiting for launchagent operation lock" in (
        result.stderr.lower()
    )
    assert reclaim_dir.is_dir()


def test_launch_agent_operation_lock_preserves_live_reclaim_marker(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _blocking_fake_launchctl(fake_bin, tmp_path)
    lock_root = tmp_path / "locks"
    reclaim_dir = lock_root / f"{LABEL}.lock.reclaim"
    reclaim_dir.mkdir(parents=True)
    live_owner = subprocess.Popen(["/bin/sleep", "5"])
    try:
        owner_start = subprocess.run(
            ["ps", "-p", str(live_owner.pid), "-o", "lstart="],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        (reclaim_dir / "owner").write_text(
            f"{live_owner.pid}\n{owner_start}\n",
            encoding="utf-8",
        )
        old_timestamp = time.time() - 120
        os.utime(reclaim_dir, (old_timestamp, old_timestamp))
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ROOT": str(lock_root),
            "CONTEXTWIKI_LAUNCH_AGENT_LOCK_TIMEOUT_SECONDS": "1",
            "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ORPHAN_GRACE_SECONDS": "3",
        }

        result = subprocess.run(
            [
                str(
                    REPO_ROOT
                    / "scripts"
                    / "restart_sync_worker_launch_agent.sh"
                )
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
        )

        assert result.returncode != 0
        assert "timed out waiting for launchagent operation lock" in (
            result.stderr.lower()
        )
        assert reclaim_dir.is_dir()
    finally:
        live_owner.terminate()
        live_owner.wait(timeout=5)


@pytest.mark.parametrize("unsafe_kind", ["symlink", "regular", "foreign"])
def test_launch_agent_operation_lock_rejects_unsafe_reclaim_marker(
    tmp_path: Path,
    unsafe_kind: str,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _blocking_fake_launchctl(fake_bin, tmp_path)
    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    reclaim_dir = lock_root / f"{LABEL}.lock.reclaim"
    protected_target = tmp_path / "do-not-touch-reclaim"
    protected_target.mkdir()
    protected_owner = protected_target / "owner"
    protected_contents = "protected recovery owner\n"
    protected_owner.write_text(protected_contents, encoding="utf-8")

    if unsafe_kind == "symlink":
        reclaim_dir.symlink_to(protected_target, target_is_directory=True)
    elif unsafe_kind == "regular":
        reclaim_dir.write_text("not a directory\n", encoding="utf-8")
    else:
        reclaim_dir.mkdir()
        real_stat = shutil.which("stat")
        assert real_stat is not None
        fake_stat = fake_bin / "stat"
        fake_stat.write_text(
            "\n".join(
                (
                    "#!/usr/bin/env sh",
                    (
                        f"case \"$1:$2:$3\" in "
                        f"'-f:%u:{reclaim_dir}'|'-c:%u:{reclaim_dir}') "
                        f"printf '{os.getuid() + 1}\\n'; exit 0 ;; esac"
                    ),
                    f"exec {shlex.quote(real_stat)} \"$@\"",
                    "",
                )
            ),
            encoding="utf-8",
        )
        fake_stat.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ROOT": str(lock_root),
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_TIMEOUT_SECONDS": "1",
    }

    result = subprocess.run(
        [str(REPO_ROOT / "scripts" / "restart_sync_worker_launch_agent.sh")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )

    assert result.returncode != 0
    assert "unsafe launchagent recovery marker" in result.stderr.lower()
    if unsafe_kind == "symlink":
        assert reclaim_dir.is_symlink()
    elif unsafe_kind == "regular":
        assert reclaim_dir.read_text(encoding="utf-8") == "not a directory\n"
    else:
        assert reclaim_dir.is_dir()
    assert protected_owner.read_text(encoding="utf-8") == protected_contents


def test_concurrent_reclaim_marker_recovery_allows_only_one_operation_at_a_time(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, _ = _blocking_fake_launchctl(fake_bin, tmp_path)
    lock_root = tmp_path / "locks"
    reclaim_dir = lock_root / f"{LABEL}.lock.reclaim"
    reclaim_dir.mkdir(parents=True)
    old_timestamp = time.time() - 120
    os.utime(reclaim_dir, (old_timestamp, old_timestamp))
    entered = tmp_path / "reclaimed-operation-entered"
    release = tmp_path / "reclaimed-operation-release"
    common_env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ROOT": str(lock_root),
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_TIMEOUT_SECONDS": "3",
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ORPHAN_GRACE_SECONDS": "4",
        "CONTEXTWIKI_TEST_BLOCK_COMMAND": "kill",
        "CONTEXTWIKI_TEST_BLOCK_ENTERED": str(entered),
        "CONTEXTWIKI_TEST_BLOCK_RELEASE": str(release),
    }
    processes = [
        subprocess.Popen(
            [str(REPO_ROOT / "scripts" / "restart_sync_worker_launch_agent.sh")],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={
                **common_env,
                "CONTEXTWIKI_TEST_OPERATION_ID": operation_id,
            },
        )
        for operation_id in ("first", "second")
    ]

    try:
        _wait_for_path(entered)
        time.sleep(0.2)
        assert len(call_log.read_text(encoding="utf-8").splitlines()) == 1
        release.touch()
        outputs = [process.communicate(timeout=8) for process in processes]
    finally:
        release.touch()
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

    for process, (stdout, stderr) in zip(processes, outputs, strict=True):
        assert process.returncode != 0, (stdout, stderr)
        assert "launchagent operation lock" not in stderr.lower()
    assert not reclaim_dir.exists()
    assert not (lock_root / f"{LABEL}.lock").exists()
    operations = [
        line.split(":", 1)[0]
        for line in call_log.read_text(encoding="utf-8").splitlines()
    ]
    assert operations == ["first", "second"] or operations == [
        "second",
        "first",
    ]


def test_operation_lock_survives_sigkill_while_launchctl_child_is_live(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, _ = _blocking_fake_launchctl(fake_bin, tmp_path)
    lock_root = tmp_path / "locks"
    entered = tmp_path / "orphan-child-entered"
    release = tmp_path / "orphan-child-release"
    common_env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ROOT": str(lock_root),
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_TIMEOUT_SECONDS": "3",
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ORPHAN_GRACE_SECONDS": "4",
        "CONTEXTWIKI_TEST_BLOCK_COMMAND": "kill",
        "CONTEXTWIKI_TEST_BLOCK_ENTERED": str(entered),
        "CONTEXTWIKI_TEST_BLOCK_RELEASE": str(release),
    }
    first = subprocess.Popen(
        [str(REPO_ROOT / "scripts" / "restart_sync_worker_launch_agent.sh")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={
            **common_env,
            "CONTEXTWIKI_TEST_OPERATION_ID": "first",
        },
    )
    second: subprocess.Popen[str] | None = None

    try:
        _wait_for_path(entered)
        owner_lines = (
            lock_root / f"{LABEL}.lock" / "owner"
        ).read_text(encoding="utf-8").splitlines()
        assert len(owner_lines) == 4
        assert owner_lines[0] == str(first.pid)
        assert owner_lines[2].isdigit()
        assert owner_lines[3]
        first.kill()
        first.wait(timeout=5)
        second = subprocess.Popen(
            [
                str(
                    REPO_ROOT
                    / "scripts"
                    / "restart_sync_worker_launch_agent.sh"
                )
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={
                **common_env,
                "CONTEXTWIKI_TEST_OPERATION_ID": "second",
            },
        )

        time.sleep(0.3)
        assert call_log.read_text(encoding="utf-8").splitlines() == [
            f"first:kill SIGTERM gui/{os.getuid()}/{LABEL}"
        ]

        release.touch()
        second_stdout, second_stderr = second.communicate(timeout=8)
        assert second.returncode != 0, (second_stdout, second_stderr)
        assert "launchagent operation lock" not in second_stderr.lower()
    finally:
        release.touch()
        if first.poll() is None:
            first.kill()
            first.wait(timeout=5)
        if second is not None and second.poll() is None:
            second.terminate()
            second.wait(timeout=5)

    assert [
        line.split(":", 1)[0]
        for line in call_log.read_text(encoding="utf-8").splitlines()
    ] == ["first", "second"]
    assert not (lock_root / f"{LABEL}.lock").exists()


def test_concurrent_first_installs_serialize_state_snapshot_and_commit(
    tmp_path: Path,
):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, loaded_state = _blocking_fake_launchctl(fake_bin, tmp_path)
    launch_agents_dir = tmp_path / "LaunchAgents"
    lock_root = tmp_path / "locks"
    entered = tmp_path / "first-print-entered"
    release = tmp_path / "first-print-release"
    command = _install_command(
        fake_uv=fake_uv,
        log_dir=tmp_path / "logs",
        launch_agents_dir=launch_agents_dir,
    )
    common_env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ROOT": str(lock_root),
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_TIMEOUT_SECONDS": "5",
    }
    first_env = {
        **common_env,
        "CONTEXTWIKI_TEST_OPERATION_ID": "first",
        "CONTEXTWIKI_TEST_BLOCK_COMMAND": "print",
        "CONTEXTWIKI_TEST_BLOCK_ENTERED": str(entered),
        "CONTEXTWIKI_TEST_BLOCK_RELEASE": str(release),
    }
    second_env = {
        **common_env,
        "CONTEXTWIKI_TEST_OPERATION_ID": "second",
    }

    first = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=first_env,
    )
    second: subprocess.Popen[str] | None = None
    try:
        _wait_for_path(entered)
        second = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=second_env,
        )
        time.sleep(0.2)
        calls_while_first_is_blocked = call_log.read_text(
            encoding="utf-8"
        ).splitlines()
        assert calls_while_first_is_blocked == [
            f"first:print gui/{os.getuid()}/{LABEL}"
        ]

        release.touch()
        first_stdout, first_stderr = first.communicate(timeout=5)
        second_stdout, second_stderr = second.communicate(timeout=5)
        assert first.returncode == 0, (first_stdout, first_stderr)
        assert second.returncode == 0, (second_stdout, second_stderr)
    finally:
        release.touch()
        for process in (first, second):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

    plist_path = launch_agents_dir / f"{LABEL}.plist"
    assert plist_path.exists()
    assert loaded_state.exists()
    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert [line.split(":", 1)[0] for line in calls] == [
        "first",
        "first",
        "second",
    ]


@pytest.mark.parametrize("management_action", ["restart", "uninstall"])
def test_install_and_management_mutations_are_serialized_end_to_end(
    tmp_path: Path,
    management_action: str,
):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, loaded_state = _blocking_fake_launchctl(fake_bin, tmp_path)
    launch_agents_dir = tmp_path / "LaunchAgents"
    lock_root = tmp_path / "locks"
    entered = tmp_path / "bootstrap-entered"
    release = tmp_path / "bootstrap-release"
    install_command = _install_command(
        fake_uv=fake_uv,
        log_dir=tmp_path / "logs",
        launch_agents_dir=launch_agents_dir,
    )
    common_env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_ROOT": str(lock_root),
        "CONTEXTWIKI_LAUNCH_AGENT_LOCK_TIMEOUT_SECONDS": "5",
    }
    install_env = {
        **common_env,
        "CONTEXTWIKI_TEST_OPERATION_ID": "install",
        "CONTEXTWIKI_TEST_BLOCK_COMMAND": "bootstrap",
        "CONTEXTWIKI_TEST_BLOCK_ENTERED": str(entered),
        "CONTEXTWIKI_TEST_BLOCK_RELEASE": str(release),
    }
    management_env = {
        **common_env,
        "CONTEXTWIKI_TEST_OPERATION_ID": management_action,
    }
    if management_action == "restart":
        management_command = [
            str(REPO_ROOT / "scripts" / "restart_sync_worker_launch_agent.sh")
        ]
    else:
        management_command = [
            str(REPO_ROOT / "scripts" / "uninstall_sync_worker_launch_agent.sh"),
            "--launch-agents-dir",
            str(launch_agents_dir),
        ]

    install = subprocess.Popen(
        install_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=install_env,
    )
    management: subprocess.Popen[str] | None = None
    try:
        _wait_for_path(entered)
        plist_path = launch_agents_dir / f"{LABEL}.plist"
        assert plist_path.exists()
        management = subprocess.Popen(
            management_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=management_env,
        )
        time.sleep(0.2)
        calls_while_bootstrap_is_blocked = call_log.read_text(
            encoding="utf-8"
        ).splitlines()
        assert calls_while_bootstrap_is_blocked == [
            f"install:print gui/{os.getuid()}/{LABEL}",
            f"install:bootstrap gui/{os.getuid()} {plist_path}",
        ]
        assert plist_path.exists()

        release.touch()
        install_stdout, install_stderr = install.communicate(timeout=5)
        management_stdout, management_stderr = management.communicate(timeout=5)
        assert install.returncode == 0, (install_stdout, install_stderr)
        assert management.returncode == 0, (
            management_stdout,
            management_stderr,
        )
    finally:
        release.touch()
        for process in (install, management):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

    if management_action == "uninstall":
        assert not plist_path.exists()
        assert not loaded_state.exists()
    else:
        assert plist_path.exists()
        assert loaded_state.exists()


def test_render_only_creates_valid_absolute_secret_free_plist(tmp_path: Path):
    fake_uv = _fake_executable(tmp_path / "uv")
    output_path = tmp_path / "rendered.plist"
    log_dir = tmp_path / "worker & logs"
    env = {
        **os.environ,
        "NOTION_API_KEY": "must-not-appear",
        "OPENAI_API_KEY": "also-must-not-appear",
    }

    result = subprocess.run(
        [
            str(INSTALL_SCRIPT),
            "--repo-root",
            str(REPO_ROOT),
            "--uv-path",
            str(fake_uv),
            "--log-dir",
            str(log_dir),
            "--render-only",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    with output_path.open("rb") as plist_file:
        payload = plistlib.load(plist_file)

    assert result.stdout == f"Rendered LaunchAgent plist: {output_path}\n"
    assert payload["Label"] == LABEL
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True
    assert payload["WorkingDirectory"] == str(REPO_ROOT)
    assert payload["ProgramArguments"] == [
        "/bin/bash",
        str(RUN_SCRIPT),
        str(fake_uv),
        str(REPO_ROOT),
    ]
    assert payload["StandardOutPath"] == "/dev/null"
    assert payload["StandardErrorPath"] == "/dev/null"
    assert payload["EnvironmentVariables"] == {
        "CONTEXTWIKI_SYNC_WORKER_LOG_PATH": str(log_dir / "sync-worker.log"),
        "CONTEXTWIKI_SYNC_WORKER_LOG_MAX_BYTES": "5242880",
        "CONTEXTWIKI_SYNC_WORKER_LOG_BACKUP_COUNT": "3",
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_PATH": str(
            log_dir / "sync-worker-startup.log"
        ),
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "1048576",
    }
    assert Path(payload["WorkingDirectory"]).is_absolute()
    assert all(
        Path(path).is_absolute()
        for path in (
            payload["ProgramArguments"][1],
            payload["ProgramArguments"][2],
            payload["EnvironmentVariables"]["CONTEXTWIKI_SYNC_WORKER_LOG_PATH"],
            payload["EnvironmentVariables"][
                "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_PATH"
            ],
        )
    )
    rendered = output_path.read_text(encoding="utf-8")
    assert "must-not-appear" not in rendered
    assert "also-must-not-appear" not in rendered
    assert "@@" not in rendered
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o644


def test_render_only_is_idempotent_and_never_calls_launchctl(tmp_path: Path):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    launchctl_marker = tmp_path / "launchctl-called"
    fake_launchctl = fake_bin / "launchctl"
    fake_launchctl.write_text(
        f"#!/usr/bin/env sh\ntouch '{launchctl_marker}'\nexit 99\n",
        encoding="utf-8",
    )
    fake_launchctl.chmod(0o755)
    output_path = tmp_path / "rendered.plist"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
    command = [
        str(INSTALL_SCRIPT),
        "--repo-root",
        str(REPO_ROOT),
        "--uv-path",
        str(fake_uv),
        "--log-dir",
        str(tmp_path / "logs"),
        "--render-only",
        str(output_path),
    ]

    subprocess.run(command, check=True, env=env)
    first_render = output_path.read_bytes()
    subprocess.run(command, check=True, env=env)

    assert output_path.read_bytes() == first_render
    assert not launchctl_marker.exists()


def test_install_and_management_dry_runs_have_no_side_effects(tmp_path: Path):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    launchctl_marker = tmp_path / "launchctl-called"
    fake_launchctl = fake_bin / "launchctl"
    fake_launchctl.write_text(
        f"#!/usr/bin/env sh\ntouch '{launchctl_marker}'\nexit 99\n",
        encoding="utf-8",
    )
    fake_launchctl.chmod(0o755)
    launch_agents_dir = tmp_path / "LaunchAgents"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CONTEXTWIKI_LAUNCH_AGENT_HOME": str(tmp_path),
    }

    install_result = subprocess.run(
        [
            str(INSTALL_SCRIPT),
            "--repo-root",
            str(REPO_ROOT),
            "--uv-path",
            str(fake_uv),
            "--log-dir",
            str(tmp_path / "logs"),
            "--launch-agents-dir",
            str(launch_agents_dir),
            "--dry-run",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    for script_path in MANAGEMENT_SCRIPTS:
        subprocess.run([str(script_path), "--dry-run"], check=True, env=env)

    assert LABEL in install_result.stdout
    assert not launch_agents_dir.exists()
    assert not launchctl_marker.exists()


def test_install_rejects_unsafe_existing_custom_log_dir_without_changing_mode(
    tmp_path: Path,
):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, _ = _fake_launchctl(fake_bin, tmp_path)
    log_dir = tmp_path / "shared-logs"
    log_dir.mkdir(mode=0o755)
    launch_agents_dir = tmp_path / "LaunchAgents"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    result = subprocess.run(
        [
            str(INSTALL_SCRIPT),
            "--repo-root",
            str(REPO_ROOT),
            "--uv-path",
            str(fake_uv),
            "--log-dir",
            str(log_dir),
            "--launch-agents-dir",
            str(launch_agents_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "existing custom log directory must have mode 0700" in result.stderr
    assert "refusing to change permissions" in result.stderr
    assert stat.S_IMODE(log_dir.stat().st_mode) == 0o755
    assert not launch_agents_dir.exists()
    assert not call_log.exists()


def test_install_rejects_custom_log_dir_symlink_without_mutating_target(
    tmp_path: Path,
):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, _ = _fake_launchctl(fake_bin, tmp_path)
    target_log_dir = tmp_path / "real-logs"
    target_log_dir.mkdir(mode=0o755)
    log_dir = tmp_path / "linked-logs"
    log_dir.symlink_to(target_log_dir, target_is_directory=True)
    launch_agents_dir = tmp_path / "LaunchAgents"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    result = subprocess.run(
        [
            str(INSTALL_SCRIPT),
            "--repo-root",
            str(REPO_ROOT),
            "--uv-path",
            str(fake_uv),
            "--log-dir",
            str(log_dir),
            "--launch-agents-dir",
            str(launch_agents_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "custom log directory must not be a symbolic link" in result.stderr
    assert stat.S_IMODE(target_log_dir.stat().st_mode) == 0o755
    assert log_dir.is_symlink()
    assert not launch_agents_dir.exists()
    assert not call_log.exists()


@pytest.mark.parametrize("suffix", ["/", "/."])
def test_install_rejects_noncanonical_custom_log_dir_symlink_spelling(
    tmp_path: Path,
    suffix: str,
):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, _ = _fake_launchctl(fake_bin, tmp_path)
    target_log_dir = tmp_path / "real-logs"
    target_log_dir.mkdir(mode=0o700)
    linked_log_dir = tmp_path / "linked-logs"
    linked_log_dir.symlink_to(target_log_dir, target_is_directory=True)
    launch_agents_dir = tmp_path / "LaunchAgents"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    result = subprocess.run(
        [
            str(INSTALL_SCRIPT),
            "--repo-root",
            str(REPO_ROOT),
            "--uv-path",
            str(fake_uv),
            "--log-dir",
            f"{linked_log_dir}{suffix}",
            "--launch-agents-dir",
            str(launch_agents_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "symbolic-link" in result.stderr or "canonical" in result.stderr
    assert stat.S_IMODE(target_log_dir.stat().st_mode) == 0o700
    assert not launch_agents_dir.exists()
    assert not call_log.exists()


def test_install_rejects_symlink_in_custom_log_dir_parent_component(tmp_path: Path):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, _ = _fake_launchctl(fake_bin, tmp_path)
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir(mode=0o700)
    target_log_dir = real_parent / "logs"
    target_log_dir.mkdir(mode=0o700)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    launch_agents_dir = tmp_path / "LaunchAgents"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    result = subprocess.run(
        [
            str(INSTALL_SCRIPT),
            "--repo-root",
            str(REPO_ROOT),
            "--uv-path",
            str(fake_uv),
            "--log-dir",
            str(linked_parent / "logs"),
            "--launch-agents-dir",
            str(launch_agents_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "symbolic-link" in result.stderr or "canonical" in result.stderr
    assert stat.S_IMODE(target_log_dir.stat().st_mode) == 0o700
    assert not launch_agents_dir.exists()
    assert not call_log.exists()


def test_install_rejects_custom_log_dir_not_owned_by_current_user(tmp_path: Path):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, _ = _fake_launchctl(fake_bin, tmp_path)
    _fake_stat(fake_bin, owner_uid=os.getuid() + 1)
    log_dir = tmp_path / "private-logs"
    log_dir.mkdir(mode=0o700)
    launch_agents_dir = tmp_path / "LaunchAgents"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    result = subprocess.run(
        [
            str(INSTALL_SCRIPT),
            "--repo-root",
            str(REPO_ROOT),
            "--uv-path",
            str(fake_uv),
            "--log-dir",
            str(log_dir),
            "--launch-agents-dir",
            str(launch_agents_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "owned by the current user" in result.stderr
    assert not launch_agents_dir.exists()
    assert not call_log.exists()


def test_install_rejects_custom_log_dir_without_write_and_search_access(
    tmp_path: Path,
):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, _ = _fake_launchctl(fake_bin, tmp_path)
    _fake_stat(fake_bin)
    log_dir = tmp_path / "private-logs"
    log_dir.mkdir(mode=0o600)
    launch_agents_dir = tmp_path / "LaunchAgents"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    result = subprocess.run(
        [
            str(INSTALL_SCRIPT),
            "--repo-root",
            str(REPO_ROOT),
            "--uv-path",
            str(fake_uv),
            "--log-dir",
            str(log_dir),
            "--launch-agents-dir",
            str(launch_agents_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "writable and searchable" in result.stderr
    assert not launch_agents_dir.exists()
    assert not call_log.exists()


def test_install_creates_new_custom_log_dir_with_private_mode(tmp_path: Path):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_launchctl(fake_bin, tmp_path)
    log_dir = tmp_path / "private-logs"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    subprocess.run(
        [
            str(INSTALL_SCRIPT),
            "--repo-root",
            str(REPO_ROOT),
            "--uv-path",
            str(fake_uv),
            "--log-dir",
            str(log_dir),
            "--launch-agents-dir",
            str(tmp_path / "LaunchAgents"),
        ],
        check=True,
        env=env,
    )

    assert stat.S_IMODE(log_dir.stat().st_mode) == 0o700


def test_install_accepts_existing_current_user_private_custom_log_dir(
    tmp_path: Path,
):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, loaded_state = _fake_launchctl(fake_bin, tmp_path)
    log_dir = tmp_path / "private-logs"
    log_dir.mkdir(mode=0o700)
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    subprocess.run(
        [
            str(INSTALL_SCRIPT),
            "--repo-root",
            str(REPO_ROOT),
            "--uv-path",
            str(fake_uv),
            "--log-dir",
            str(log_dir),
            "--launch-agents-dir",
            str(tmp_path / "LaunchAgents"),
        ],
        check=True,
        env=env,
    )

    assert stat.S_IMODE(log_dir.stat().st_mode) == 0o700
    assert loaded_state.exists()
    assert any(
        line.startswith("bootstrap ")
        for line in call_log.read_text(encoding="utf-8").splitlines()
    )


def test_install_secures_existing_default_log_dir(tmp_path: Path):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_launchctl(fake_bin, tmp_path)
    log_dir = tmp_path / ".mcp_content_search" / "logs"
    log_dir.mkdir(parents=True, mode=0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CONTEXTWIKI_LAUNCH_AGENT_HOME": str(tmp_path),
    }

    subprocess.run(
        [
            str(INSTALL_SCRIPT),
            "--repo-root",
            str(REPO_ROOT),
            "--uv-path",
            str(fake_uv),
            "--launch-agents-dir",
            str(tmp_path / "LaunchAgents"),
        ],
        check=True,
        env=env,
    )

    assert stat.S_IMODE(log_dir.stat().st_mode) == 0o700


def test_dry_run_and_render_only_do_not_mutate_existing_custom_log_dir(
    tmp_path: Path,
):
    fake_uv = _fake_executable(tmp_path / "uv")
    log_dir = tmp_path / "shared-logs"
    log_dir.mkdir(mode=0o755)
    common_arguments = [
        str(INSTALL_SCRIPT),
        "--repo-root",
        str(REPO_ROOT),
        "--uv-path",
        str(fake_uv),
        "--log-dir",
        str(log_dir),
    ]

    subprocess.run([*common_arguments, "--dry-run"], check=True)
    assert stat.S_IMODE(log_dir.stat().st_mode) == 0o755

    subprocess.run(
        [*common_arguments, "--render-only", str(tmp_path / "rendered.plist")],
        check=True,
    )
    assert stat.S_IMODE(log_dir.stat().st_mode) == 0o755


def test_repo_root_selects_its_own_launch_agent_template(tmp_path: Path):
    fake_uv = _fake_executable(tmp_path / "uv")
    alternate_repo = tmp_path / "alternate repo"
    (alternate_repo / "indexing").mkdir(parents=True)
    (alternate_repo / "indexing" / "sync_worker.py").write_text(
        "# alternate worker\n",
        encoding="utf-8",
    )
    alternate_scripts = alternate_repo / "scripts"
    alternate_scripts.mkdir()
    (alternate_scripts / "run_sync_worker_launch_agent.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n",
        encoding="utf-8",
    )
    alternate_deploy = alternate_repo / "deploy" / "launchd"
    alternate_deploy.mkdir(parents=True)
    template = (
        REPO_ROOT
        / "deploy"
        / "launchd"
        / f"{LABEL}.plist.template"
    ).read_text(encoding="utf-8")
    template = template.replace(
        "  <key>ThrottleInterval</key>",
        "  <key>LowPriorityIO</key>\n"
        "  <true/>\n"
        "  <key>ThrottleInterval</key>",
    )
    (alternate_deploy / f"{LABEL}.plist.template").write_text(
        template,
        encoding="utf-8",
    )
    output_path = tmp_path / "alternate.plist"

    subprocess.run(
        [
            str(INSTALL_SCRIPT),
            "--repo-root",
            str(alternate_repo),
            "--uv-path",
            str(fake_uv),
            "--log-dir",
            str(tmp_path / "logs"),
            "--render-only",
            str(output_path),
        ],
        check=True,
    )

    with output_path.open("rb") as plist_file:
        payload = plistlib.load(plist_file)
    assert payload["LowPriorityIO"] is True
    assert payload["WorkingDirectory"] == str(alternate_repo.resolve())


def test_install_is_noop_when_identical_and_changed_config_requires_restart(
    tmp_path: Path,
):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, loaded_state = _fake_launchctl(fake_bin, tmp_path)
    launch_agents_dir = tmp_path / "LaunchAgents"
    first_log_dir = tmp_path / "logs-one"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
    base_command = [
        str(INSTALL_SCRIPT),
        "--repo-root",
        str(REPO_ROOT),
        "--uv-path",
        str(fake_uv),
        "--log-dir",
        str(first_log_dir),
        "--launch-agents-dir",
        str(launch_agents_dir),
    ]

    subprocess.run(base_command, check=True, env=env)
    plist_path = launch_agents_dir / f"{LABEL}.plist"
    first_plist = plist_path.read_bytes()
    calls_after_first_install = call_log.read_text(encoding="utf-8").splitlines()
    subprocess.run(base_command, check=True, env=env)

    calls_after_noop = call_log.read_text(encoding="utf-8").splitlines()
    assert calls_after_noop[:-1] == calls_after_first_install
    assert calls_after_noop[-1].startswith("print ")
    assert sum(line.startswith("bootstrap ") for line in calls_after_noop) == 1
    assert not any(line.startswith("bootout ") for line in calls_after_noop)
    assert loaded_state.exists()
    assert plist_path.read_bytes() == first_plist

    changed_command = [
        *base_command[: base_command.index("--log-dir") + 1],
        str(tmp_path / "logs-two"),
        *base_command[base_command.index("--launch-agents-dir") :],
    ]
    rejected = subprocess.run(
        changed_command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert rejected.returncode != 0
    assert "rerun with --restart" in rejected.stderr
    assert plist_path.read_bytes() == first_plist
    calls_after_rejection = call_log.read_text(encoding="utf-8").splitlines()
    assert calls_after_rejection[:-1] == calls_after_noop
    assert calls_after_rejection[-1].startswith("print ")
    assert not any(
        line.startswith(("bootout ", "bootstrap "))
        for line in calls_after_rejection[len(calls_after_noop) :]
    )

    subprocess.run([*changed_command, "--restart"], check=True, env=env)

    final_calls = call_log.read_text(encoding="utf-8").splitlines()
    assert sum(line.startswith("bootstrap ") for line in final_calls) == 2
    assert sum(line.startswith("bootout ") for line in final_calls) == 1
    with plist_path.open("rb") as plist_file:
        payload = plistlib.load(plist_file)
    assert payload["EnvironmentVariables"]["CONTEXTWIKI_SYNC_WORKER_LOG_PATH"] == str(
        tmp_path / "logs-two" / "sync-worker.log"
    )


def test_identical_install_bootstraps_when_service_is_unloaded(tmp_path: Path):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, loaded_state = _fake_launchctl(fake_bin, tmp_path)
    launch_agents_dir = tmp_path / "LaunchAgents"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
    command = [
        str(INSTALL_SCRIPT),
        "--repo-root",
        str(REPO_ROOT),
        "--uv-path",
        str(fake_uv),
        "--log-dir",
        str(tmp_path / "logs"),
        "--launch-agents-dir",
        str(launch_agents_dir),
    ]

    subprocess.run(command, check=True, env=env)
    loaded_state.unlink()
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert sum(line.startswith("bootstrap ") for line in calls) == 2
    assert loaded_state.exists()
    assert "configuration is identical" in result.stdout
    assert "started" in result.stdout


def test_install_requires_restart_to_replace_loaded_service_when_plist_is_missing(
    tmp_path: Path,
):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, loaded_state = _fake_launchctl(fake_bin, tmp_path)
    loaded_state.touch()
    launch_agents_dir = tmp_path / "LaunchAgents"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    command = [
        str(INSTALL_SCRIPT),
        "--repo-root",
        str(REPO_ROOT),
        "--uv-path",
        str(fake_uv),
        "--log-dir",
        str(tmp_path / "logs"),
        "--launch-agents-dir",
        str(launch_agents_dir),
    ]

    rejected = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    plist_path = launch_agents_dir / f"{LABEL}.plist"
    assert rejected.returncode != 0
    assert "loaded service has no installed plist" in rejected.stderr
    assert "rerun with --restart" in rejected.stderr
    assert not plist_path.exists()
    assert loaded_state.exists()
    calls_after_rejection = call_log.read_text(encoding="utf-8").splitlines()
    assert not any(line.startswith("bootout ") for line in calls_after_rejection)
    assert not any(line.startswith("bootstrap ") for line in calls_after_rejection)

    subprocess.run([*command, "--restart"], check=True, env=env)

    assert plist_path.exists()
    assert loaded_state.exists()
    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert f"bootout gui/{os.getuid()}/{LABEL}" in calls
    assert sum(line.startswith("bootstrap ") for line in calls) == 1


def test_missing_plist_restart_failure_cannot_restore_prior_loaded_service(
    tmp_path: Path,
):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, loaded_state = _fake_launchctl(fake_bin, tmp_path)
    loaded_state.touch()
    (tmp_path / "launchctl-fail-next-bootstrap").touch()
    launch_agents_dir = tmp_path / "LaunchAgents"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    result = subprocess.run(
        [
            str(INSTALL_SCRIPT),
            "--repo-root",
            str(REPO_ROOT),
            "--uv-path",
            str(fake_uv),
            "--log-dir",
            str(tmp_path / "logs"),
            "--launch-agents-dir",
            str(launch_agents_dir),
            "--restart",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "no previous plist was available" in result.stderr
    assert "service remains unloaded" in result.stderr
    assert not (launch_agents_dir / f"{LABEL}.plist").exists()
    assert not loaded_state.exists()
    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert f"bootout gui/{os.getuid()}/{LABEL}" in calls
    assert sum(line.startswith("bootstrap ") for line in calls) == 1


def test_changed_install_restores_previous_plist_and_service_on_bootstrap_failure(
    tmp_path: Path,
):
    fake_uv = _fake_executable(tmp_path / "uv")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, loaded_state = _fake_launchctl(fake_bin, tmp_path)
    launch_agents_dir = tmp_path / "LaunchAgents"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
    first_command = [
        str(INSTALL_SCRIPT),
        "--repo-root",
        str(REPO_ROOT),
        "--uv-path",
        str(fake_uv),
        "--log-dir",
        str(tmp_path / "logs-one"),
        "--launch-agents-dir",
        str(launch_agents_dir),
    ]
    subprocess.run(first_command, check=True, env=env)
    plist_path = launch_agents_dir / f"{LABEL}.plist"
    previous_plist = plist_path.read_bytes()
    (tmp_path / "launchctl-fail-next-bootstrap").touch()

    result = subprocess.run(
        [
            *first_command[: first_command.index("--log-dir") + 1],
            str(tmp_path / "logs-two"),
            *first_command[first_command.index("--launch-agents-dir") :],
            "--restart",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "restored previous configuration" in result.stderr
    assert plist_path.read_bytes() == previous_plist
    assert loaded_state.exists()
    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert sum(line.startswith("bootout ") for line in calls) == 1
    assert sum(line.startswith("bootstrap ") for line in calls) == 3


def test_launch_agent_runner_preserves_and_bounds_startup_stderr(tmp_path: Path):
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env sh\n"
        "printf 'initialization failed\\n' >&2\n"
        "head -c 4096 /dev/zero | tr '\\\\0' x >&2\n"
        "printf '\\nlast startup diagnostic\\n' >&2\n"
        "exit 23\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    diagnostic_log = tmp_path / "logs" / "startup.log"
    env = {
        **os.environ,
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_PATH": str(diagnostic_log),
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "1024",
        "CONTEXTWIKI_SYNC_WORKER_SANITIZER_PYTHON_PATH": sys.executable,
    }

    result = subprocess.run(
        [str(RUN_SCRIPT), str(fake_uv), str(REPO_ROOT)],
        check=False,
        env=env,
    )

    assert result.returncode == 23
    assert diagnostic_log.stat().st_size <= 1024
    diagnostic = diagnostic_log.read_text(encoding="utf-8")
    assert "last startup diagnostic" in diagnostic


def test_launch_agent_runner_forwards_sigterm_and_waits_for_worker(tmp_path: Path):
    ready_marker = tmp_path / "worker-ready"
    stopped_marker = tmp_path / "worker-stopped"
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env sh\n"
        f"touch {shlex.quote(str(ready_marker))}\n"
        f"trap 'touch {shlex.quote(str(stopped_marker))}; exit 0' TERM\n"
        "while true; do sleep 1; done\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    env = {
        **os.environ,
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_PATH": str(
            tmp_path / "logs" / "startup.log"
        ),
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "1024",
        "CONTEXTWIKI_SYNC_WORKER_SANITIZER_PYTHON_PATH": sys.executable,
    }

    process = subprocess.Popen(
        [str(RUN_SCRIPT), str(fake_uv), str(REPO_ROOT)],
        env=env,
    )
    for _ in range(100):
        if ready_marker.exists():
            break
        time.sleep(0.01)
    assert ready_marker.exists()

    process.terminate()
    assert process.wait(timeout=5) == 0
    assert stopped_marker.exists()


@pytest.mark.parametrize("signal_number", (signal.SIGTERM, signal.SIGINT))
def test_launch_agent_runner_waits_for_writer_drain_after_signal(
    tmp_path: Path,
    signal_number: signal.Signals,
):
    drain_started = tmp_path / "writer-drain-started"
    drain_release = tmp_path / "writer-drain-release"
    sanitizer_pid_path = tmp_path / "sanitizer-pid"
    fake_sanitizer = tmp_path / "slow-sanitizer"
    fake_sanitizer.write_text(
        "\n".join(
            (
                f"#!{sys.executable}",
                "from pathlib import Path",
                "import os",
                "import sys",
                "import time",
                f"drain_started = Path({str(drain_started)!r})",
                f"drain_release = Path({str(drain_release)!r})",
                (
                    f"Path({str(sanitizer_pid_path)!r}).write_text("
                    "str(os.getpid()), encoding='utf-8')"
                ),
                "for chunk in sys.stdin:",
                "    sys.stdout.write(chunk)",
                "    sys.stdout.flush()",
                "drain_started.touch()",
                "while not drain_release.exists():",
                "    time.sleep(0.01)",
                "",
            )
        ),
        encoding="utf-8",
    )
    fake_sanitizer.chmod(0o755)
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        "printf 'benign startup diagnostic\\n' >&2\n"
        "exit 17\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    diagnostic_dir = tmp_path / "logs"
    env = {
        **os.environ,
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_PATH": str(
            diagnostic_dir / "startup.log"
        ),
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "1024",
        "CONTEXTWIKI_SYNC_WORKER_SANITIZER_PYTHON_PATH": str(fake_sanitizer),
    }
    process = subprocess.Popen(
        [str(RUN_SCRIPT), str(fake_uv), str(REPO_ROOT)],
        env=env,
    )
    sanitizer_pid: int | None = None

    try:
        _wait_for_path(drain_started)
        sanitizer_pid = int(sanitizer_pid_path.read_text(encoding="utf-8"))

        process.send_signal(signal_number)
        time.sleep(0.2)

        assert process.poll() is None
        os.kill(sanitizer_pid, 0)

        drain_release.touch()
        assert process.wait(timeout=5) == 17
        for _ in range(100):
            try:
                os.kill(sanitizer_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        else:
            raise AssertionError("diagnostic sanitizer remained alive after runner exit")
        assert list(diagnostic_dir.glob(".sync-worker-pipe.*")) == []
        assert list(diagnostic_dir.glob(".sync-worker-chunk.*")) == []
    finally:
        drain_release.touch()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if sanitizer_pid is not None:
            try:
                os.kill(sanitizer_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize(
    ("signal_number", "signal_name", "expected_status"),
    (
        (signal.SIGTERM, "TERM", 41),
        (signal.SIGINT, "INT", 42),
    ),
)
def test_launch_agent_runner_replays_signal_received_before_child_pid_assignment(
    tmp_path: Path,
    signal_number: signal.Signals,
    signal_name: str,
    expected_status: int,
):
    assignment_blocked_marker = tmp_path / "assignment-blocked"
    assignment_release_marker = tmp_path / "assignment-release"
    signal_recorded_marker = tmp_path / "signal-recorded"
    child_pid_marker = tmp_path / "worker-pid"
    descendant_pid_marker = tmp_path / "worker-descendant-pid"
    stopped_marker = tmp_path / "worker-stopped"
    descendant_stopped_marker = tmp_path / "worker-descendant-stopped"
    instrumented_runner = tmp_path / "run-sync-worker"
    runner_source = RUN_SCRIPT.read_text(encoding="utf-8")
    assignment = 'child_pid="$!"'
    assert runner_source.count(assignment) == 1
    pending_assignment = 'pending_signal="${signal_name}"'
    assert runner_source.count(pending_assignment) == 1
    instrumented_runner.write_text(
        runner_source.replace(
            pending_assignment,
            "\n".join(
                (
                    pending_assignment,
                    'touch "${CONTEXTWIKI_TEST_SIGNAL_RECORDED_MARKER}"',
                )
            ),
        ).replace(
            assignment,
            "\n".join(
                (
                    'touch "${CONTEXTWIKI_TEST_ASSIGNMENT_BLOCKED_MARKER}"',
                    (
                        "while [[ ! -f "
                        '"${CONTEXTWIKI_TEST_ASSIGNMENT_RELEASE_MARKER}" ]]; do'
                    ),
                    "  sleep 0.01",
                    "done",
                    assignment,
                )
            ),
        ),
        encoding="utf-8",
    )
    instrumented_runner.chmod(0o755)

    fake_uv = tmp_path / "uv"
    descendant_program = "\n".join(
        (
            "import os",
            "from pathlib import Path",
            "import signal",
            "import sys",
            "import time",
            "def stop(signal_name):",
            "    Path(sys.argv[2]).write_text(signal_name, encoding='utf-8')",
            "    raise SystemExit(0)",
            "signal.signal(signal.SIGTERM, lambda *_: stop('TERM'))",
            "signal.signal(signal.SIGINT, lambda *_: stop('INT'))",
            "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')",
            "while True:",
            "    time.sleep(0.05)",
        )
    )
    fake_uv.write_text(
        "\n".join(
            (
                f"#!{sys.executable}",
                "import os",
                "from pathlib import Path",
                "import signal",
                "import subprocess",
                "import sys",
                "import time",
                f"stopped_path = Path({str(stopped_marker)!r})",
                "def stop(signal_name, status):",
                "    stopped_path.write_text(signal_name, encoding='utf-8')",
                "    raise SystemExit(status)",
                "signal.signal(signal.SIGTERM, lambda *_: stop('TERM', 41))",
                "signal.signal(signal.SIGINT, lambda *_: stop('INT', 42))",
                (
                    f"Path({str(child_pid_marker)!r}).write_text("
                    "str(os.getpid()), encoding='utf-8')"
                ),
                "subprocess.Popen([",
                "    sys.executable,",
                "    '-c',",
                f"    {descendant_program!r},",
                f"    {str(descendant_pid_marker)!r},",
                f"    {str(descendant_stopped_marker)!r},",
                "])",
                "while True:",
                "    time.sleep(0.05)",
                "",
            )
        ),
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    diagnostic_dir = tmp_path / "logs"
    env = {
        **os.environ,
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_PATH": str(
            diagnostic_dir / "startup.log"
        ),
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "1024",
        "CONTEXTWIKI_SYNC_WORKER_SANITIZER_PYTHON_PATH": sys.executable,
        "CONTEXTWIKI_TEST_ASSIGNMENT_BLOCKED_MARKER": str(
            assignment_blocked_marker
        ),
        "CONTEXTWIKI_TEST_ASSIGNMENT_RELEASE_MARKER": str(
            assignment_release_marker
        ),
        "CONTEXTWIKI_TEST_SIGNAL_RECORDED_MARKER": str(signal_recorded_marker),
    }

    process = subprocess.Popen(
        [str(instrumented_runner), str(fake_uv), str(REPO_ROOT)],
        env=env,
    )
    child_pid: int | None = None
    descendant_pid: int | None = None
    try:
        for _ in range(200):
            if (
                assignment_blocked_marker.exists()
                and child_pid_marker.exists()
                and descendant_pid_marker.exists()
            ):
                child_pid = int(child_pid_marker.read_text(encoding="utf-8"))
                descendant_pid = int(
                    descendant_pid_marker.read_text(encoding="utf-8")
                )
                break
            time.sleep(0.01)
        assert child_pid is not None
        assert descendant_pid is not None

        process.send_signal(signal_number)
        for _ in range(100):
            if signal_recorded_marker.exists():
                break
            time.sleep(0.01)
        assert signal_recorded_marker.exists()
        assert process.poll() is None
        assert not stopped_marker.exists()

        assignment_release_marker.touch()
        assert process.wait(timeout=5) == expected_status
        assert stopped_marker.read_text(encoding="utf-8") == signal_name
        assert descendant_stopped_marker.read_text(encoding="utf-8") == signal_name
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
        with pytest.raises(ProcessLookupError):
            os.kill(descendant_pid, 0)
        assert list(diagnostic_dir.glob(".sync-worker-pipe.*")) == []
        assert list(diagnostic_dir.glob(".sync-worker-chunk.*")) == []
    finally:
        assignment_release_marker.touch()
        if descendant_pid is not None:
            try:
                os.kill(descendant_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_launch_agent_runner_bounds_diagnostics_while_worker_is_running(
    tmp_path: Path,
):
    ready_marker = tmp_path / "worker-ready"
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env sh\n"
        f"touch {shlex.quote(str(ready_marker))}\n"
        "trap 'exit 0' TERM\n"
        "while true; do\n"
        "  printf 'live startup diagnostic %0256d\\n' 1 >&2\n"
        "  sleep 0.01\n"
        "done\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    diagnostic_log = tmp_path / "logs" / "startup.log"
    env = {
        **os.environ,
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_PATH": str(diagnostic_log),
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "1024",
        "CONTEXTWIKI_SYNC_WORKER_SANITIZER_PYTHON_PATH": sys.executable,
    }
    process = subprocess.Popen(
        [str(RUN_SCRIPT), str(fake_uv), str(REPO_ROOT)],
        env=env,
    )

    try:
        for _ in range(100):
            if ready_marker.exists():
                break
            time.sleep(0.01)
        assert ready_marker.exists()
        time.sleep(0.4)

        assert process.poll() is None
        assert 0 < diagnostic_log.stat().st_size <= 1024
        assert "live startup diagnostic" in diagnostic_log.read_text(encoding="utf-8")
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_launch_agent_runner_amortizes_startup_log_compaction(tmp_path: Path):
    real_dd = shutil.which("dd")
    real_tail = shutil.which("tail")
    assert real_dd is not None
    assert real_tail is not None

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    dd_calls = tmp_path / "dd-calls"
    tail_calls = tmp_path / "tail-calls"
    fake_dd = fake_bin / "dd"
    fake_dd.write_text(
        "\n".join(
            (
                "#!/usr/bin/env sh",
                f"printf x >> {shlex.quote(str(dd_calls))}",
                'output_path=""',
                'for argument in "$@"; do',
                '  case "$argument" in of=*) output_path="${argument#of=}" ;; esac',
                "done",
                f"exec {shlex.quote(real_dd)} bs=64 count=1 "
                '"of=${output_path}" 2>/dev/null',
                "",
            )
        ),
        encoding="utf-8",
    )
    fake_dd.chmod(0o755)
    fake_tail = fake_bin / "tail"
    fake_tail.write_text(
        "#!/usr/bin/env sh\n"
        f"printf x >> {shlex.quote(str(tail_calls))}\n"
        f"exec {shlex.quote(real_tail)} \"$@\"\n",
        encoding="utf-8",
    )
    fake_tail.chmod(0o755)
    fake_uv = tmp_path / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env sh\n"
        "head -c 16384 /dev/zero | tr '\\\\0' x >&2\n"
        "exit 23\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    diagnostic_log = tmp_path / "logs" / "startup.log"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_PATH": str(diagnostic_log),
        "CONTEXTWIKI_SYNC_WORKER_DIAGNOSTIC_LOG_MAX_BYTES": "1024",
        "CONTEXTWIKI_SYNC_WORKER_SANITIZER_PYTHON_PATH": sys.executable,
    }

    result = subprocess.run(
        [str(RUN_SCRIPT), str(fake_uv), str(REPO_ROOT)],
        check=False,
        env=env,
        timeout=10,
    )

    dd_call_count = len(dd_calls.read_text(encoding="utf-8"))
    tail_call_count = len(tail_calls.read_text(encoding="utf-8"))
    assert result.returncode == 23
    assert dd_call_count > 100
    assert tail_call_count > 0
    assert tail_call_count * 4 < dd_call_count
    assert diagnostic_log.stat().st_size <= 1024


def test_uninstall_removes_exact_plist_from_custom_launch_agents_dir(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, loaded_state = _fake_launchctl(fake_bin, tmp_path)
    loaded_state.touch()
    launch_agents_dir = tmp_path / "Custom LaunchAgents"
    launch_agents_dir.mkdir()
    plist_path = launch_agents_dir / f"{LABEL}.plist"
    plist_path.write_text("test plist", encoding="utf-8")
    neighbor_path = launch_agents_dir / "keep-me.plist"
    neighbor_path.write_text("keep", encoding="utf-8")
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "uninstall_sync_worker_launch_agent.sh"),
            "--launch-agents-dir",
            str(launch_agents_dir),
        ],
        check=True,
        env=env,
    )

    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("print ") for line in calls)
    assert any(line.startswith("bootout ") for line in calls)
    assert not plist_path.exists()
    assert neighbor_path.exists()


def test_uninstall_boots_out_loaded_service_when_plist_is_missing(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log, loaded_state = _fake_launchctl(fake_bin, tmp_path)
    loaded_state.touch()
    launch_agents_dir = tmp_path / "Missing LaunchAgents"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "uninstall_sync_worker_launch_agent.sh"),
            "--launch-agents-dir",
            str(launch_agents_dir),
        ],
        check=True,
        env=env,
    )

    calls = call_log.read_text(encoding="utf-8").splitlines()
    assert f"bootout gui/{os.getuid()}/{LABEL}" in calls
    assert not loaded_state.exists()
