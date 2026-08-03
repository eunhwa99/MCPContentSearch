from __future__ import annotations

import os
import stat

import pytest

from evaluation.private_workflow import (
    PrivateWorkflowError,
    _open_exclusive_log,
    create_private_run_directory,
)


pytestmark = pytest.mark.integration


def test_private_run_directory_and_logs_are_unique_and_owner_only(tmp_path):
    first_path, first_fd = create_private_run_directory(tmp_path)
    second_path, second_fd = create_private_run_directory(tmp_path)
    try:
        assert first_path != second_path
        assert first_path.parent == tmp_path
        assert second_path.parent == tmp_path
        assert stat.S_IMODE(first_path.stat().st_mode) == 0o700
        assert stat.S_IMODE(second_path.stat().st_mode) == 0o700

        stdout_fd = _open_exclusive_log(first_fd, "runner.stdout.log")
        stderr_fd = _open_exclusive_log(first_fd, "runner.stderr.log")
        try:
            assert stat.S_IMODE(os.fstat(stdout_fd).st_mode) == 0o600
            assert stat.S_IMODE(os.fstat(stderr_fd).st_mode) == 0o600
        finally:
            os.close(stdout_fd)
            os.close(stderr_fd)
    finally:
        os.close(first_fd)
        os.close(second_fd)


def test_private_run_directory_rejects_symlinked_runner_temp(tmp_path):
    real_runner_temp = tmp_path / "real-runner-temp"
    real_runner_temp.mkdir()
    linked_runner_temp = tmp_path / "linked-runner-temp"
    linked_runner_temp.symlink_to(real_runner_temp, target_is_directory=True)

    with pytest.raises(PrivateWorkflowError, match="runner temporary directory"):
        create_private_run_directory(linked_runner_temp)


def test_private_run_directory_rejects_symlinked_runner_temp_ancestor(tmp_path):
    real_parent = tmp_path / "real-parent"
    runner_temp = real_parent / "runner-temp"
    runner_temp.mkdir(parents=True)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(PrivateWorkflowError, match="runner temporary directory"):
        create_private_run_directory(linked_parent / "runner-temp")


def test_private_log_creation_rejects_existing_symlink_without_touching_target(
    tmp_path,
):
    run_path, run_fd = create_private_run_directory(tmp_path)
    target = tmp_path / "private-target"
    target.write_text("unchanged\n", encoding="utf-8")
    (run_path / "runner.stdout.log").symlink_to(target)
    try:
        with pytest.raises(PrivateWorkflowError, match="restricted log"):
            _open_exclusive_log(run_fd, "runner.stdout.log")
    finally:
        os.close(run_fd)

    assert target.read_text(encoding="utf-8") == "unchanged\n"
