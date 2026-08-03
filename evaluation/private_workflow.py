"""Fail-closed runner for approved local-only retrieval evaluation jobs."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
from typing import Mapping, cast


class PrivateWorkflowError(ValueError):
    """Raised without exposing private paths, output, or configuration."""


_INPUT_ENVIRONMENT = {
    "private_local": (
        "CONTEXTWIKI_PRIVATE_RETRIEVAL_DATASET",
        "CONTEXTWIKI_PRIVATE_RETRIEVAL_CORPUS",
        "CONTEXTWIKI_PRIVATE_RETRIEVAL_CONFIG",
    ),
    "larger_local": (
        "CONTEXTWIKI_LARGER_RETRIEVAL_DATASET",
        "CONTEXTWIKI_LARGER_RETRIEVAL_CORPUS",
        "CONTEXTWIKI_LARGER_RETRIEVAL_CONFIG",
    ),
}
_RUN_DIRECTORY_PREFIX = "contextwiki-private-retrieval-evaluation-"
_LOG_NAMES = frozenset({"runner.stdout.log", "runner.stderr.log"})


def create_private_run_directory(runner_temp: str | Path) -> tuple[Path, int]:
    """Create a random owner-only child beneath a no-follow RUNNER_TEMP."""
    supplied = Path(runner_temp)
    if not supplied.is_absolute():
        raise PrivateWorkflowError("runner temporary directory is unsafe")
    absolute = Path(os.path.abspath(os.fspath(supplied)))
    parent_fd = _open_verified_runner_temp(absolute)
    created_name = ""
    try:
        for _ in range(32):
            candidate = f"{_RUN_DIRECTORY_PREFIX}{secrets.token_hex(12)}"
            try:
                os.mkdir(candidate, 0o700, dir_fd=parent_fd)
            except FileExistsError:
                continue
            created_name = candidate
            break
        if not created_name:
            raise PrivateWorkflowError("private run directory could not be created")
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        run_fd = os.open(created_name, directory_flags, dir_fd=parent_fd)
        try:
            os.fchmod(run_fd, 0o700)
            metadata = os.fstat(run_fd)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise PrivateWorkflowError(
                    "private run directory could not be restricted"
                )
        except Exception:
            os.close(run_fd)
            raise
        return absolute / created_name, run_fd
    except Exception:
        if created_name:
            try:
                os.rmdir(created_name, dir_fd=parent_fd)
            except OSError:
                pass
        raise
    finally:
        os.close(parent_fd)


def _open_verified_runner_temp(path: Path) -> int:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    current_fd = -1
    try:
        current_fd = os.open(path.anchor, directory_flags)
        for component in path.parts[1:]:
            next_fd = os.open(component, directory_flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        metadata = os.fstat(current_fd)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise PrivateWorkflowError("runner temporary directory is unsafe")
        result = current_fd
        current_fd = -1
        return result
    except PrivateWorkflowError:
        raise
    except OSError:
        raise PrivateWorkflowError("runner temporary directory is unsafe") from None
    finally:
        if current_fd >= 0:
            os.close(current_fd)


def _open_exclusive_log(run_directory_fd: int, name: str) -> int:
    if name not in _LOG_NAMES:
        raise PrivateWorkflowError("restricted log could not be created")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    file_descriptor = -1
    try:
        file_descriptor = os.open(name, flags, 0o600, dir_fd=run_directory_fd)
        os.fchmod(file_descriptor, 0o600)
        metadata = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise PrivateWorkflowError("restricted log could not be created")
        result = file_descriptor
        file_descriptor = -1
        return result
    except PrivateWorkflowError:
        raise
    except OSError:
        raise PrivateWorkflowError("restricted log could not be created") from None
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)


def _select_inputs(
    mode: str,
    environment: Mapping[str, str],
) -> tuple[str, str, str]:
    if mode == "live_provider":
        raise PrivateWorkflowError(
            "live_provider is unavailable: no reviewed provider adapter is implemented"
        )
    names = _INPUT_ENVIRONMENT.get(mode)
    if names is None:
        raise PrivateWorkflowError("unsupported local-only mode")
    values = tuple(environment.get(name, "") for name in names)
    if any(not value or not Path(value).is_file() for value in values):
        raise PrivateWorkflowError("local-only evaluation inputs are unavailable")
    return cast(tuple[str, str, str], values)


def run_private_evaluation(
    *,
    mode: str,
    git_identifier: str,
    environment: Mapping[str, str] | None = None,
) -> int:
    child_environment = dict(os.environ if environment is None else environment)
    dataset, corpus, configuration = _select_inputs(mode, child_environment)
    runner_temp = child_environment.get("RUNNER_TEMP", "")
    run_path, run_fd = create_private_run_directory(runner_temp)
    stdout_fd = -1
    stderr_fd = -1
    try:
        stdout_fd = _open_exclusive_log(run_fd, "runner.stdout.log")
        stderr_fd = _open_exclusive_log(run_fd, "runner.stderr.log")
        child_environment["CONTEXTWIKI_DISABLE_DOTENV"] = "1"
        # The executable and module are fixed; only non-executable data is supplied.
        completed = subprocess.run(  # nosec B603
            [
                sys.executable,
                "-m",
                "evaluation.runner",
                "--dataset",
                dataset,
                "--corpus",
                corpus,
                "--configuration",
                configuration,
                "--output-dir",
                os.fspath(run_path / "reports"),
                "--git-identifier",
                git_identifier,
            ],
            stdin=subprocess.DEVNULL,
            stdout=stdout_fd,
            stderr=stderr_fd,
            env=child_environment,
            check=False,
        )
        os.fsync(stdout_fd)
        os.fsync(stderr_fd)
        return completed.returncode
    finally:
        if stdout_fd >= 0:
            os.close(stdout_fd)
        if stderr_fd >= 0:
            os.close(stderr_fd)
        os.close(run_fd)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one approved local-only retrieval evaluation."
    )
    parser.add_argument("--mode", required=True)
    parser.add_argument("--git-identifier", required=True)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    try:
        return_code = run_private_evaluation(
            mode=args.mode,
            git_identifier=args.git_identifier,
        )
    except PrivateWorkflowError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(3 if args.mode == "live_provider" else 2) from None
    except OSError:
        print(
            "Non-public evaluation failed. Inspect runner-local restricted logs.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    if return_code != 0:
        print(
            "Non-public evaluation failed. Inspect runner-local restricted logs.",
            file=sys.stderr,
        )
        raise SystemExit(return_code)
    print("Non-public evaluation completed. Results remain on isolated runner.")


if __name__ == "__main__":
    main()
