import os
import hashlib
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.models import DocumentModel
import yaml

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
        f"obsidian://open"
        f"?vault={urllib.parse.quote(vault_name)}"
        f"&file={urllib.parse.quote(relative_path)}"
    )


def _open_note_without_following_symlinks(
    vault_root: Path,
    relative_path: Path,
) -> tuple[str, float]:
    nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(vault_root, os.O_RDONLY | directory_flag)
    try:
        for part in relative_path.parts[:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY | directory_flag | nofollow_flag,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(
            relative_path.name,
            os.O_RDONLY | nofollow_flag,
            dir_fd=directory_fd,
        )
    finally:
        try:
            os.close(directory_fd)
        except OSError:
            pass
    with os.fdopen(file_fd, "r", encoding="utf-8") as handle:
        content = handle.read()
        mtime = os.fstat(handle.fileno()).st_mtime
    return content, mtime


@dataclass(frozen=True)
class ObsidianSnapshot:
    documents: list[DocumentModel]
    snapshot_complete: bool


def obsidian_disabled_reason(vault_path: Path | None) -> str:
    if vault_path is None:
        return _OBSIDIAN_DISABLED_REASON
    if not vault_path.is_absolute():
        return _OBSIDIAN_RELATIVE_PATH_REASON
    if vault_path.is_symlink():
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
    """Return (frontmatter_dict, body_without_frontmatter, original_body_line_start)."""
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
    meta: dict[str, str] = {}
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


def _iter_obsidian_markdown_files(vault_path: Path) -> tuple[list[Path], bool]:
    snapshot_complete = True
    markdown_files: list[Path] = []
    vault_root = _resolved_vault_path(vault_path)

    def handle_walk_error(_error: OSError):
        nonlocal snapshot_complete
        snapshot_complete = False

    for root, dirnames, filenames in os.walk(vault_path, onerror=handle_walk_error):
        root_path = Path(root)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if (
                name not in _OBSIDIAN_SKIP_DIRS
                and not name.startswith(".")
                and not (root_path / name).is_symlink()
            )
        )
        for filename in sorted(filenames):
            if filename.startswith(".") or not filename.endswith(".md"):
                continue
            candidate = root_path / filename
            if candidate.is_symlink():
                continue
            if not _is_vault_bounded_note(candidate, vault_root=vault_root):
                snapshot_complete = False
                continue
            markdown_files.append(candidate)

    return markdown_files, snapshot_complete


async def fetch_obsidian_documents(vault_path: Path) -> ObsidianSnapshot:
    disabled_reason = obsidian_disabled_reason(vault_path)
    if disabled_reason:
        raise FileNotFoundError(disabled_reason)

    vault_name = vault_path.name
    documents: list[DocumentModel] = []
    try:
        markdown_files, snapshot_complete = _iter_obsidian_markdown_files(vault_path)
    except OSError as exc:
        raise FileNotFoundError(
            obsidian_disabled_reason(vault_path) or _OBSIDIAN_DISABLED_REASON
        ) from exc

    for md_file in markdown_files:
        current_disabled_reason = obsidian_disabled_reason(vault_path)
        if current_disabled_reason:
            raise FileNotFoundError(current_disabled_reason)
        try:
            current_vault_root = _resolved_vault_path(vault_path)
        except OSError as exc:
            raise FileNotFoundError(
                obsidian_disabled_reason(vault_path) or _OBSIDIAN_DISABLED_REASON
            ) from exc
        if not _is_vault_bounded_note(md_file, vault_root=current_vault_root):
            snapshot_complete = False
            continue
        relative = md_file.relative_to(vault_path)

        relative_path = relative.as_posix()
        try:
            content, mtime = _open_note_without_following_symlinks(
                current_vault_root,
                relative,
            )
        except (OSError, UnicodeDecodeError):
            snapshot_complete = False
            continue

        frontmatter, indexed_content, body_line_start = _parse_frontmatter(content)

        title = frontmatter.get("title") or frontmatter.get("Title") or md_file.stem
        updated_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        canonical_url = _obsidian_uri(vault_name, relative_path)

        # external_id is the vault-relative path. It is stable across content edits but
        # changes if the user renames or moves the file (Obsidian rename creates a new
        # document and tombstones the old one). A user-managed frontmatter `id:` field
        # could provide rename-stable identity but is not enforced here.
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
                content_hash=_content_hash(indexed_content),
            )
        )

    if not snapshot_complete:
        raise RuntimeError(_OBSIDIAN_INCOMPLETE_SNAPSHOT_REASON)

    return ObsidianSnapshot(documents=documents, snapshot_complete=True)
