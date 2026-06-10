import hashlib
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from core.models import DocumentModel

_OBSIDIAN_SKIP_DIRS = frozenset({".obsidian", ".trash"})


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _obsidian_uri(vault_name: str, relative_path: str) -> str:
    return (
        f"obsidian://open"
        f"?vault={urllib.parse.quote(vault_name)}"
        f"&file={urllib.parse.quote(relative_path)}"
    )


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter_dict, body). Best-effort YAML key:value parse."""
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---", 4)
    if end == -1:
        return {}, content
    fm_text = content[4:end].strip()
    body = content[end + 4:].lstrip("\n")
    meta: dict[str, str] = {}
    for line in fm_text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, body


async def fetch_obsidian_documents(vault_path: Path) -> list[DocumentModel]:
    vault_name = vault_path.name
    documents: list[DocumentModel] = []

    for md_file in sorted(vault_path.rglob("*.md")):
        # Skip Obsidian system dirs and dot-prefixed files/dirs at any depth including vault root.
        relative = md_file.relative_to(vault_path)
        if any(part in _OBSIDIAN_SKIP_DIRS or part.startswith(".") for part in relative.parts):
            continue

        try:
            content = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        relative_path = relative.as_posix()
        frontmatter, body = _parse_frontmatter(content)

        title = frontmatter.get("title") or frontmatter.get("Title") or md_file.stem
        mtime = md_file.stat().st_mtime
        updated_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        canonical_url = _obsidian_uri(vault_name, relative_path)

        # external_id is the vault-relative path. It is stable across content edits but
        # changes if the user renames or moves the file (Obsidian rename creates a new
        # document and tombstones the old one). A user-managed frontmatter `id:` field
        # could provide rename-stable identity but is not enforced here.
        indexed_content = body or content
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
                updated_at=updated_at,
                content_hash=_content_hash(indexed_content),
            )
        )

    return documents
