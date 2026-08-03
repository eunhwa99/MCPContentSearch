"""Restricted atomic text output for private evaluation artifacts."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import secrets
import stat


class SecureOutputError(ValueError):
    """Raised without exposing private output paths or content."""


PRIVATE_OUTPUT_PREFIXES = (
    Path("evaluation/reports/private"),
    Path("artifacts/private-evaluation"),
)
PRIVATE_DATASET_PATH = Path("evaluation/datasets/retrieval_gold.local.jsonl")


def require_private_output_destination(
    destination: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> bool:
    """Return whether an approved private destination is inside the repository."""
    return _require_private_destination(
        destination,
        repository_root=repository_root,
        allowed=lambda relative: any(
            relative == prefix or prefix in relative.parents
            for prefix in PRIVATE_OUTPUT_PREFIXES
        ),
        repository_error=(
            "private output inside the repository must be under "
            "evaluation/reports/private or artifacts/private-evaluation"
        ),
    )


def require_private_dataset_destination(
    destination: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> bool:
    """Return whether the private dataset destination is inside the repository."""
    return _require_private_destination(
        destination,
        repository_root=repository_root,
        allowed=lambda relative: relative == PRIVATE_DATASET_PATH,
        repository_error=(
            "private dataset output inside the repository must be "
            "evaluation/datasets/retrieval_gold.local.jsonl"
        ),
    )


def trusted_repository_root(repository_root: str | Path | None = None) -> Path:
    """Resolve the trusted checkout independently from the process CWD."""
    source = (
        repository_root
        if repository_root is not None
        else _default_repository_root()
    )
    return Path(os.path.abspath(os.fspath(source)))


def _require_private_destination(
    destination: str | Path,
    *,
    repository_root: str | Path | None,
    allowed: Callable[[Path], bool],
    repository_error: str,
) -> bool:
    root = trusted_repository_root(repository_root)
    absolute = Path(os.path.abspath(os.fspath(destination)))
    containing_repository = _nearest_git_repository(absolute)
    if containing_repository is not None and containing_repository != root:
        raise SecureOutputError(
            "private output inside an untrusted Git repository is forbidden"
        )
    try:
        relative = absolute.relative_to(root)
    except ValueError:
        return False
    if not allowed(relative):
        raise SecureOutputError(repository_error)
    return True


def _default_repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _nearest_git_repository(path: Path) -> Path | None:
    current = path if path.is_dir() else path.parent
    for candidate in (current, *current.parents):
        try:
            (candidate / ".git").lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise SecureOutputError(
                "private output repository boundary could not be verified"
            ) from None
        else:
            return candidate
    return None


def secure_atomic_write_text(
    destination: str | Path,
    content: str,
    *,
    enforce_parent_mode: bool = False,
) -> Path:
    path = Path(os.path.abspath(os.fspath(destination)))
    parent_fd = -1
    try:
        _reject_symlink_components(path)
        parent_fd = _open_restricted_parent(
            path.parent,
            enforce_mode=enforce_parent_mode,
        )
        _atomic_write_in_parent(parent_fd, path.name, content)
    except SecureOutputError:
        raise
    except OSError:
        raise SecureOutputError(
            "private output could not be written securely"
        ) from None
    finally:
        if parent_fd >= 0:
            os.close(parent_fd)
    return path


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current /= part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                raise SecureOutputError("private output could not be written securely")
        if path.exists() and not path.is_file():
            raise SecureOutputError("private output could not be written securely")
    except SecureOutputError:
        raise
    except OSError:
        raise SecureOutputError(
            "private output could not be written securely"
        ) from None


def _open_restricted_parent(parent: Path, *, enforce_mode: bool) -> int:
    if enforce_mode and parent == Path(parent.anchor):
        raise SecureOutputError("private output could not be written securely")
    directory_flags = os.O_RDONLY
    directory_flags |= getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    current_fd = os.open(parent.anchor, directory_flags)
    try:
        for component in parent.parts[1:]:
            created = False
            try:
                next_fd = os.open(
                    component,
                    directory_flags,
                    dir_fd=current_fd,
                )
            except FileNotFoundError:
                os.mkdir(component, 0o700, dir_fd=current_fd)
                next_fd = os.open(
                    component,
                    directory_flags,
                    dir_fd=current_fd,
                )
                created = True
            os.close(current_fd)
            current_fd = next_fd
            if created:
                metadata = os.fstat(current_fd)
                if metadata.st_uid != os.geteuid():
                    raise SecureOutputError(
                        "private output could not be written securely"
                    )
                os.fchmod(current_fd, 0o700)
                metadata = os.fstat(current_fd)
                if metadata.st_uid != os.geteuid() or (
                    stat.S_IMODE(metadata.st_mode) != 0o700
                ):
                    raise SecureOutputError(
                        "private output could not be written securely"
                    )
        metadata = os.fstat(current_fd)
        if metadata.st_uid != os.geteuid():
            raise SecureOutputError("private output could not be written securely")
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            if not enforce_mode:
                raise SecureOutputError("private output could not be written securely")
            os.fchmod(current_fd, 0o700)
            metadata = os.fstat(current_fd)
            if metadata.st_uid != os.geteuid() or (
                stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise SecureOutputError("private output could not be written securely")
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _atomic_write_in_parent(parent_fd: int, name: str, content: str) -> None:
    temporary_name = f".{name}.{secrets.token_hex(12)}.tmp"
    temporary_created = False
    try:
        try:
            target_metadata = os.stat(
                name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            target_metadata = None
        if target_metadata is not None and not stat.S_ISREG(target_metadata.st_mode):
            raise SecureOutputError("private output could not be written securely")

        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        file_flags |= getattr(os, "O_NOFOLLOW", 0)
        file_fd = os.open(temporary_name, file_flags, 0o600, dir_fd=parent_fd)
        temporary_created = True
        try:
            os.fchmod(file_fd, 0o600)
            with os.fdopen(file_fd, "w", encoding="utf-8") as output:
                file_fd = -1
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
        finally:
            if file_fd >= 0:
                os.close(file_fd)

        os.replace(
            temporary_name,
            name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        temporary_created = False
    finally:
        if temporary_created:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
