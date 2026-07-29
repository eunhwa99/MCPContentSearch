import hashlib
import os
import stat
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from core.models import DocumentModel


_OBSIDIAN_SKIP_DIRS = frozenset({".obsidian", ".trash"})
_OBSIDIAN_DISABLED_REASON = (
    "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
    "is not set or is not an existing directory."
)
_OBSIDIAN_RELATIVE_PATH_REASON = (
    "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
    "must be an absolute path."
)
_OBSIDIAN_SYMLINK_PATH_REASON = (
    "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
    "must not be a symlink."
)
_OBSIDIAN_INCOMPLETE_SNAPSHOT_REASON = (
    "Obsidian vault snapshot was incomplete because one or more notes could not be read."
)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _obsidian_uri(vault_name: str, relative_path: str) -> str:
    return (
        "obsidian://open"
        f"?vault={urllib.parse.quote(vault_name)}"
        f"&file={urllib.parse.quote(relative_path)}"
    )


def _same_file_stat(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _validate_opened_path(
    fd: int,
    path: str | Path,
    *,
    dir_fd: int | None = None,
    directory: bool,
) -> None:
    path_stat = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
    fd_stat = os.fstat(fd)
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(path_stat.st_mode) or not _same_file_stat(path_stat, fd_stat):
        raise OSError("opened path changed or resolved through a symlink")


def _open_directory_without_following_symlinks(
    path: str | Path,
    *,
    dir_fd: int | None = None,
) -> int:
    nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, os.O_RDONLY | directory_flag | nofollow_flag, dir_fd=dir_fd)
    try:
        _validate_opened_path(fd, path, dir_fd=dir_fd, directory=True)
    except Exception:
        os.close(fd)
        raise
    return fd


def _open_vault_root_without_following_symlinks(vault_root: Path) -> int:
    if not vault_root.is_absolute():
        return _open_directory_without_following_symlinks(vault_root)

    parts = vault_root.parts
    if not parts:
        raise OSError("vault root path is empty")

    directory_fd = _open_directory_without_following_symlinks(parts[0])
    try:
        for part in parts[1:]:
            next_fd = _open_directory_without_following_symlinks(
                part,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
    except Exception:
        os.close(directory_fd)
        raise
    return directory_fd


def _open_file_without_following_symlinks(
    path: str | Path,
    *,
    dir_fd: int,
) -> int:
    nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, os.O_RDONLY | nofollow_flag, dir_fd=dir_fd)
    try:
        _validate_opened_path(fd, path, dir_fd=dir_fd, directory=False)
    except Exception:
        os.close(fd)
        raise
    return fd


def _open_note_without_following_symlinks(
    vault_root: Path,
    relative_path: Path,
    *,
    max_file_bytes: int | None = None,
) -> tuple[str, float]:
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise OSError("note path must stay relative to the vault root")
    root_fd = _open_vault_root_without_following_symlinks(vault_root)
    try:
        return _open_note_from_root_fd(
            root_fd,
            relative_path,
            max_file_bytes=max_file_bytes,
        )
    finally:
        os.close(root_fd)


def _open_note_from_root_fd(
    root_fd: int,
    relative_path: Path,
    *,
    max_file_bytes: int | None = None,
) -> tuple[str, float]:
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise OSError("note path must stay relative to the vault root")
    directory_fd = os.dup(root_fd)
    try:
        for part in relative_path.parts[:-1]:
            next_fd = _open_directory_without_following_symlinks(
                part,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = _open_file_without_following_symlinks(
            relative_path.name,
            dir_fd=directory_fd,
        )
    finally:
        try:
            os.close(directory_fd)
        except OSError:
            pass
    file_stat = os.fstat(file_fd)
    if max_file_bytes is not None and file_stat.st_size > max_file_bytes:
        os.close(file_fd)
        raise OSError("note exceeds configured byte limit")
    mtime = file_stat.st_mtime
    with os.fdopen(file_fd, "rb") as handle:
        if max_file_bytes is None:
            raw_content = handle.read()
        else:
            raw_content = handle.read(max_file_bytes + 1)
            if len(raw_content) > max_file_bytes:
                raise OSError("note exceeds configured byte limit")
        content = raw_content.decode("utf-8")
    return content, mtime


@dataclass(frozen=True)
class ObsidianSnapshot:
    documents: list[DocumentModel]
    snapshot_complete: bool


def _path_has_symlink_component(path: Path) -> bool:
    if not path.is_absolute():
        return path.is_symlink()

    current = Path(path.parts[0])
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            return True
    return False


def obsidian_disabled_reason(vault_path: Path | None) -> str:
    if vault_path is None:
        return _OBSIDIAN_DISABLED_REASON
    if not vault_path.is_absolute():
        return _OBSIDIAN_RELATIVE_PATH_REASON
    if _path_has_symlink_component(vault_path):
        return _OBSIDIAN_SYMLINK_PATH_REASON
    if not vault_path.is_dir():
        return _OBSIDIAN_DISABLED_REASON
    if not os.access(vault_path, os.R_OK | os.X_OK):
        return _OBSIDIAN_DISABLED_REASON
    return ""


def _resolved_vault_path(vault_path: Path) -> Path:
    return vault_path.resolve(strict=True)


def _is_vault_bounded_note(note_path: Path, *, vault_root: Path) -> bool:
    if note_path.is_symlink():
        return False
    try:
        resolved_note = note_path.resolve(strict=True)
    except OSError:
        return False
    return resolved_note.is_relative_to(vault_root)


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str, int]:
    """Return frontmatter, body without frontmatter, and body start line."""
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, content, 1

    end_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}, content, 1

    fm_text = "".join(lines[1:end_index]).strip()
    body = "".join(lines[end_index + 1 :])
    try:
        parsed = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return {}, content, 1
    if parsed is None and (
        fm_text == ""
        or all(
            not line.strip() or line.lstrip().startswith("#")
            for line in fm_text.splitlines()
        )
    ):
        parsed = {}
    if not isinstance(parsed, dict):
        return {}, content, 1
    meta = {
        str(key).strip(): value if isinstance(value, str) else str(value)
        for key, value in parsed.items()
        if key is not None and value is not None
    }
    body_line_start = end_index + 2
    return meta, body, body_line_start


def _iter_obsidian_markdown_files(
    root_fd: int,
    *,
    max_files: int,
    max_file_bytes: int,
) -> tuple[list[Path], bool]:
    snapshot_complete = True
    markdown_files: list[Path] = []

    def handle_walk_error(_error: OSError):
        nonlocal snapshot_complete
        snapshot_complete = False

    for root, dirnames, filenames, directory_fd in os.fwalk(
        ".",
        topdown=True,
        onerror=handle_walk_error,
        follow_symlinks=False,
        dir_fd=root_fd,
    ):
        rel_dir = Path(root)
        kept_dirnames: list[str] = []
        for name in sorted(dirnames):
            if name in _OBSIDIAN_SKIP_DIRS or name.startswith("."):
                continue
            try:
                entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError:
                snapshot_complete = False
                continue
            if stat.S_ISDIR(entry_stat.st_mode):
                kept_dirnames.append(name)
            else:
                snapshot_complete = False
        dirnames[:] = kept_dirnames

        for filename in sorted(filenames):
            if filename.startswith(".") or not filename.endswith(".md"):
                continue
            try:
                entry_stat = os.stat(
                    filename,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except OSError:
                snapshot_complete = False
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                snapshot_complete = False
                continue
            if entry_stat.st_size > max_file_bytes:
                snapshot_complete = False
                continue
            if len(markdown_files) >= max_files:
                snapshot_complete = False
                continue
            markdown_files.append(rel_dir / filename)

    return markdown_files, snapshot_complete


async def fetch_obsidian_documents(
    vault_path: Path,
    *,
    max_files: int = 2_000,
    max_file_bytes: int = 512_000,
) -> ObsidianSnapshot:
    disabled_reason = obsidian_disabled_reason(vault_path)
    if disabled_reason:
        raise FileNotFoundError(disabled_reason)

    vault_name = vault_path.name
    documents: list[DocumentModel] = []
    root_fd: int | None = None
    try:
        root_fd = _open_vault_root_without_following_symlinks(vault_path)
        markdown_files, snapshot_complete = _iter_obsidian_markdown_files(
            root_fd,
            max_files=max_files,
            max_file_bytes=max_file_bytes,
        )
    except OSError as exc:
        if root_fd is not None:
            os.close(root_fd)
        raise FileNotFoundError(
            obsidian_disabled_reason(vault_path) or _OBSIDIAN_DISABLED_REASON
        ) from exc

    try:
        assert root_fd is not None
        if not snapshot_complete:
            raise RuntimeError(_OBSIDIAN_INCOMPLETE_SNAPSHOT_REASON)

        for md_file in markdown_files:
            relative_path = md_file.as_posix()
            try:
                content, mtime = _open_note_from_root_fd(
                    root_fd,
                    md_file,
                    max_file_bytes=max_file_bytes,
                )
            except (OSError, UnicodeDecodeError):
                snapshot_complete = False
                continue

            frontmatter, indexed_content, body_line_start = _parse_frontmatter(content)
            title = frontmatter.get("title") or frontmatter.get("Title") or md_file.stem
            updated_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
            canonical_url = _obsidian_uri(vault_name, relative_path)

            documents.append(
                DocumentModel(
                    id=relative_path,
                    title=title,
                    content=indexed_content,
                    url=canonical_url,
                    platform="obsidian",
                    source_id="source_obsidian",
                    document_id=relative_path,
                    external_id=relative_path,
                    canonical_url=canonical_url,
                    path=relative_path,
                    line_start=body_line_start,
                    updated_at=updated_at,
                    modified_at=updated_at,
                    date_provenance="filesystem",
                    content_hash=_content_hash(indexed_content),
                )
            )
    finally:
        if root_fd is not None:
            os.close(root_fd)

    if not snapshot_complete:
        raise RuntimeError(_OBSIDIAN_INCOMPLETE_SNAPSHOT_REASON)

    return ObsidianSnapshot(documents=documents, snapshot_complete=True)
