from __future__ import annotations

import hashlib
import io
import math
import multiprocessing
import os
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Callable
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from pypdf import PdfReader

from core.exceptions import ParsingError
from core.models import DocumentModel, EvidenceSourceType, ExperienceType
from core.utils import ContentHasher


SUPPORTED_SUFFIXES = frozenset({".pdf", ".docx", ".md", ".markdown", ".txt"})
_WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WORD = f"{{{_WORD_NAMESPACE}}}"
_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_TRUSTED_STICKY_TEMP_DIRECTORIES = frozenset(
    {
        Path("/tmp"),  # nosec B108
        Path("/var/tmp"),  # nosec B108
        Path("/private/tmp"),
        Path("/private/var/tmp"),
    }
)
MAX_DOCUMENT_TITLE_CHARS = 512
MAX_SECTION_TITLE_CHARS = 512
MAX_CAREER_METADATA_CHARS = 512
MAX_CAREER_DATE_CHARS = 64
MAX_CAREER_DOCUMENT_ID_CHARS = 512
_PDF_WORKER_MEMORY_BYTES = 512 * 1024 * 1024
_DARWIN_PS_PATH = Path("/bin/ps")
_DARWIN_RSS_SAMPLE_INTERVAL_SECONDS = 0.5
_DOCX_XML_READ_CHUNK_BYTES = 64 * 1024
_LINE_BREAK_CHARACTERS = frozenset(
    {
        "\n",
        "\r",
        "\v",
        "\f",
        "\x1c",
        "\x1d",
        "\x1e",
        "\x85",
        "\u2028",
        "\u2029",
    }
)


class _PdfBoundExceeded(Exception):
    def __init__(self, limit_name: str):
        self.limit_name = limit_name
        super().__init__(limit_name)


class CareerParsingCancelled(Exception):
    """Internal cooperative-cancellation signal for blocking career parsing."""


@dataclass(frozen=True)
class _LoadedCareerFile:
    relative_path: Path
    raw: bytes
    file_stat: os.stat_result


def _raise_if_cancelled_callback(
    cancel_check: Callable[[], bool] | None,
) -> None:
    if cancel_check is not None and cancel_check():
        raise CareerParsingCancelled("Career document parsing cancelled")


def _iter_text_lines(
    content: str,
    *,
    cancel_check: Callable[[], bool] | None = None,
):
    """Yield ``splitlines()``-compatible text without a document-wide list."""
    start = 0
    index = 0
    next_cancel_check = 0
    while index < len(content):
        if index >= next_cancel_check:
            _raise_if_cancelled_callback(cancel_check)
            next_cancel_check = index + 256
        character = content[index]
        if character not in _LINE_BREAK_CHARACTERS:
            index += 1
            continue
        yield content[start:index]
        if character == "\r" and index + 1 < len(content):
            if content[index + 1] == "\n":
                index += 1
        index += 1
        start = index
    _raise_if_cancelled_callback(cancel_check)
    if start < len(content):
        yield content[start:]


class _InterruptibleDocxXmlReader:
    """Bound XML reads so cooperative cancellation is observed during parsing."""

    def __init__(
        self,
        handle: IO[bytes],
        *,
        cancel_check: Callable[[], bool] | None,
    ):
        self._handle = handle
        self._cancel_check = cancel_check

    def read(self, size: int = -1) -> bytes:
        _raise_if_cancelled_callback(self._cancel_check)
        bounded_size = (
            _DOCX_XML_READ_CHUNK_BYTES
            if size < 0
            else min(size, _DOCX_XML_READ_CHUNK_BYTES)
        )
        return self._handle.read(bounded_size)


def _current_uid() -> int:
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        raise ParsingError("Secure career file owner checks are unavailable")
    return int(getuid())


def _trusted_sticky_temp_directory(path: Path, file_stat: os.stat_result) -> bool:
    mode = stat.S_IMODE(file_stat.st_mode)
    return (
        path in _TRUSTED_STICKY_TEMP_DIRECTORIES
        and file_stat.st_uid == 0
        and bool(file_stat.st_mode & stat.S_ISVTX)
        and bool(mode & 0o022)
    )


def _secure_directory_flags(safe_name: str) -> int:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if no_follow is None or directory_flag is None or not _OPEN_SUPPORTS_DIR_FD:
        raise ParsingError(f"Secure file access is unavailable for {safe_name}")
    return os.O_RDONLY | directory_flag | no_follow | getattr(os, "O_CLOEXEC", 0)


def _printable_path_components(path: Path) -> bool:
    return all(component.isprintable() for component in path.parts)


def _safe_path_label(value: str, fallback: str = "document") -> str:
    return value if value and value.isprintable() else fallback


def _validate_trusted_directory(
    descriptor: int,
    logical_path: Path,
    safe_name: str,
) -> os.stat_result:
    file_stat = os.fstat(descriptor)
    if not stat.S_ISDIR(file_stat.st_mode):
        raise ParsingError(f"Could not read {safe_name}")
    if file_stat.st_uid not in {0, _current_uid()}:
        raise ParsingError(f"Untrusted career path ownership for {safe_name}")
    if stat.S_IMODE(file_stat.st_mode) & 0o022 and not _trusted_sticky_temp_directory(
        logical_path,
        file_stat,
    ):
        raise ParsingError(f"Untrusted career path permissions for {safe_name}")
    return file_stat


def _open_trusted_absolute_directory(path: Path, safe_name: str) -> int:
    safe_name = _safe_path_label(safe_name)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or not _printable_path_components(path)
    ):
        raise ParsingError(f"Could not read {safe_name}")
    flags = _secure_directory_flags(safe_name)
    descriptor = -1
    try:
        descriptor = os.open(os.sep, flags)
        logical_path = Path(os.sep)
        _validate_trusted_directory(descriptor, logical_path, safe_name)
        for component in path.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            logical_path = logical_path / component
            _validate_trusted_directory(descriptor, logical_path, safe_name)
        return descriptor
    except ParsingError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except (OSError, ValueError) as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise ParsingError(f"Could not read {safe_name}") from exc


def _open_trusted_child_directory(
    parent_descriptor: int,
    component: str,
    logical_path: Path,
    safe_name: str,
) -> int:
    safe_name = _safe_path_label(safe_name)
    if not component or not component.isprintable():
        raise ParsingError(f"Could not read {safe_name}")
    descriptor = -1
    try:
        descriptor = os.open(
            component,
            _secure_directory_flags(safe_name),
            dir_fd=parent_descriptor,
        )
        _validate_trusted_directory(descriptor, logical_path, safe_name)
        return descriptor
    except ParsingError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except (OSError, ValueError) as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise ParsingError(f"Could not read {safe_name}") from exc


def _validate_trusted_regular_file(
    descriptor: int,
    safe_name: str,
) -> os.stat_result:
    file_stat = os.fstat(descriptor)
    if not stat.S_ISREG(file_stat.st_mode):
        raise ParsingError(f"Could not read {safe_name}")
    if file_stat.st_uid not in {0, _current_uid()}:
        raise ParsingError(f"Untrusted career file ownership for {safe_name}")
    if stat.S_IMODE(file_stat.st_mode) & 0o022:
        raise ParsingError(f"Untrusted career file permissions for {safe_name}")
    return file_stat


def _trusted_file_read_snapshot(
    file_stat: os.stat_result,
) -> tuple[int, int, int, int, int]:
    mtime_ns = getattr(file_stat, "st_mtime_ns", int(file_stat.st_mtime * 1e9))
    ctime_ns = getattr(file_stat, "st_ctime_ns", int(file_stat.st_ctime * 1e9))
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        mtime_ns,
        ctime_ns,
    )


def _verify_trusted_file_read_snapshot(
    before: os.stat_result,
    after: os.stat_result,
    safe_name: str,
) -> None:
    if _trusted_file_read_snapshot(before) != _trusted_file_read_snapshot(after):
        raise ParsingError(f"Could not read {safe_name}")


def _open_trusted_regular_file(path: Path, safe_name: str) -> int:
    safe_name = _safe_path_label(safe_name)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or not path.name
        or not _printable_path_components(path)
    ):
        raise ParsingError(f"Could not read {safe_name}")
    parent_descriptor = _open_trusted_absolute_directory(path.parent, safe_name)
    try:
        return _open_trusted_regular_file_at(
            parent_descriptor,
            path.name,
            safe_name,
        )
    finally:
        os.close(parent_descriptor)


def _open_trusted_regular_file_at(
    parent_descriptor: int,
    file_name: str,
    safe_name: str,
) -> int:
    safe_name = _safe_path_label(safe_name)
    if (
        not file_name
        or file_name in {".", ".."}
        or os.sep in file_name
        or not file_name.isprintable()
    ):
        raise ParsingError(f"Could not read {safe_name}")
    descriptor = -1
    try:
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise ParsingError(f"Secure file access is unavailable for {safe_name}")
        descriptor = os.open(
            file_name,
            os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_descriptor,
        )
        _validate_trusted_regular_file(descriptor, safe_name)
        return descriptor
    except ParsingError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except (OSError, ValueError) as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise ParsingError(f"Could not read {safe_name}") from exc


def validate_career_document_id(value: str) -> str:
    normalized = value.strip()
    if len(normalized) > MAX_CAREER_DOCUMENT_ID_CHARS:
        raise ParsingError("Career document_id exceeds character limit")
    if normalized and not normalized.isprintable():
        raise ParsingError("Career document_id contains control characters")
    return normalized


def validate_career_metadata(field_name: str, value: str) -> str:
    normalized = value.strip()
    limit = (
        MAX_DOCUMENT_TITLE_CHARS
        if field_name == "document_title"
        else MAX_CAREER_DATE_CHARS
        if field_name in {"start_date", "end_date"}
        else MAX_CAREER_METADATA_CHARS
    )
    if len(normalized) > limit:
        label = (
            "document title (document_title)"
            if field_name == "document_title"
            else field_name
        )
        raise ParsingError(f"Career {label} exceeds character limit")
    return normalized


def _extract_pdf_content(
    raw: bytes,
    *,
    max_pages: int,
    max_chars: int,
    max_bytes: int,
) -> tuple[str, str]:
    reader = PdfReader(io.BytesIO(raw), strict=True)
    if len(reader.pages) > max_pages:
        raise _PdfBoundExceeded("page")
    pages: list[str] = []
    extracted_chars = 0
    extracted_bytes = 0
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        extracted_chars += len(text)
        if extracted_chars > max_chars:
            raise _PdfBoundExceeded("character")
        extracted_bytes += len(text.encode("utf-8"))
        if extracted_bytes > max_bytes:
            raise _PdfBoundExceeded("UTF-8 byte")
        pages.append(text)
    content = "\n\n".join(page for page in pages if page).strip()
    first_line = next(
        (line.strip() for line in _iter_text_lines(content) if line.strip()), ""
    )
    return content, first_line


def _apply_pdf_worker_resource_limits(
    resource_module,
    *,
    platform: str,
    cpu_seconds: int,
) -> bool:
    resource_module.setrlimit(
        resource_module.RLIMIT_CPU,
        (cpu_seconds, cpu_seconds + 1),
    )
    if platform.startswith("darwin"):
        return False
    address_space_limit = getattr(resource_module, "RLIMIT_AS", None)
    if address_space_limit is None:
        raise RuntimeError(f"PDF worker memory limit unavailable on {platform}")
    resource_module.setrlimit(
        address_space_limit,
        (_PDF_WORKER_MEMORY_BYTES, _PDF_WORKER_MEMORY_BYTES),
    )
    return True


def _read_process_rss_bytes(process_id: int) -> int | None:
    try:
        ps_stat = _DARWIN_PS_PATH.stat()
        ps_mode = stat.S_IMODE(ps_stat.st_mode)
        if (
            not stat.S_ISREG(ps_stat.st_mode)
            or ps_stat.st_uid != 0
            or ps_mode & 0o022
            or not os.access(_DARWIN_PS_PATH, os.X_OK)
        ):
            return None
        result = subprocess.run(
            [os.fspath(_DARWIN_PS_PATH), "-o", "rss=", "-p", str(process_id)],
            capture_output=True,
            text=True,
            timeout=0.5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    raw_rss = result.stdout.strip()
    if not raw_rss:
        return None
    try:
        return int(raw_rss.splitlines()[0].strip()) * 1024
    except ValueError:
        return None


def _pdf_extraction_worker(
    connection,
    raw: bytes,
    max_pages: int,
    max_chars: int,
    max_bytes: int,
    cpu_seconds: int,
) -> None:
    try:
        import resource

        _apply_pdf_worker_resource_limits(
            resource,
            platform=sys.platform,
            cpu_seconds=cpu_seconds,
        )
        content, title = _extract_pdf_content(
            raw,
            max_pages=max_pages,
            max_chars=max_chars,
            max_bytes=max_bytes,
        )
        connection.send(("ok", content, title))
    except _PdfBoundExceeded as exc:
        connection.send(("limit", exc.limit_name))
    except BaseException:
        connection.send(("error",))
    finally:
        connection.close()


class CareerDocumentParser:
    """Parse explicit, root-bounded career files into the shared document model."""

    def __init__(
        self,
        *,
        root: str | Path,
        root_descriptor: int | None = None,
        max_file_bytes: int = 10_000_000,
        max_pdf_pages: int = 200,
        max_pdf_extracted_chars: int = 2_000_000,
        max_pdf_extracted_bytes: int = 8_000_000,
        pdf_extraction_timeout_seconds: float = 10.0,
        cancel_check: Callable[[], bool] | None = None,
    ):
        root_path = Path(root)
        if not root_path.is_absolute() or ".." in root_path.parts:
            raise ParsingError(
                "Career document root must be an existing absolute directory"
            )
        for name, value in (
            ("max_file_bytes", max_file_bytes),
            ("max_pdf_pages", max_pdf_pages),
            ("max_pdf_extracted_chars", max_pdf_extracted_chars),
            ("max_pdf_extracted_bytes", max_pdf_extracted_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (
            isinstance(pdf_extraction_timeout_seconds, bool)
            or not isinstance(pdf_extraction_timeout_seconds, (int, float))
            or pdf_extraction_timeout_seconds <= 0
            or not math.isfinite(pdf_extraction_timeout_seconds)
        ):
            raise ValueError("pdf_extraction_timeout_seconds must be positive")
        self.root = Path(os.path.abspath(os.fspath(root_path)))
        opened_root_descriptor = -1
        retained_root_descriptor = -1
        try:
            opened_root_descriptor = _open_trusted_absolute_directory(
                self.root,
                "career document root",
            )
            root_stat = os.fstat(opened_root_descriptor)
            self._root_identity = (root_stat.st_dev, root_stat.st_ino)
            self._root_descriptor = -1
            if root_descriptor is not None:
                if (
                    isinstance(root_descriptor, bool)
                    or not isinstance(root_descriptor, int)
                    or root_descriptor < 0
                ):
                    raise ParsingError("Invalid bound career document root")
                retained_root_descriptor = os.dup(root_descriptor)
                bound_stat = _validate_trusted_directory(
                    retained_root_descriptor,
                    self.root,
                    "career document root",
                )
                if (bound_stat.st_dev, bound_stat.st_ino) != self._root_identity:
                    raise ParsingError("Career document root identity changed")
                self._root_descriptor = retained_root_descriptor
                retained_root_descriptor = -1
        except (OSError, ParsingError) as exc:
            raise ParsingError(
                "Career document root must be an existing trusted absolute directory"
            ) from exc
        finally:
            if retained_root_descriptor >= 0:
                os.close(retained_root_descriptor)
            if opened_root_descriptor >= 0:
                os.close(opened_root_descriptor)
        self.max_file_bytes = max_file_bytes
        self.max_pdf_pages = max_pdf_pages
        self.max_pdf_extracted_chars = max_pdf_extracted_chars
        self.max_pdf_extracted_bytes = max_pdf_extracted_bytes
        self.pdf_extraction_timeout_seconds = float(pdf_extraction_timeout_seconds)
        self.cancel_check = cancel_check

    def close(self) -> None:
        descriptor = getattr(self, "_root_descriptor", -1)
        if descriptor >= 0:
            os.close(descriptor)
            self._root_descriptor = -1

    def __enter__(self) -> CareerDocumentParser:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except (AttributeError, OSError):
            pass

    def parse_file(
        self,
        path: str | Path,
        *,
        source_type: EvidenceSourceType | str,
        experience_type: ExperienceType | str | None = None,
        document_id: str = "",
        document_title: str = "",
        company: str = "",
        role: str = "",
        project: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> DocumentModel:
        requested = Path(path)
        if not _printable_path_components(requested):
            raise ParsingError("Career document path is unsafe")
        safe_name = requested.name or "document"
        prepared = self._prepare_metadata(
            safe_name=safe_name,
            source_type=source_type,
            experience_type=experience_type,
            document_id=document_id,
            document_title=document_title,
            company=company,
            role=role,
            project=project,
            start_date=start_date,
            end_date=end_date,
        )
        loaded = self.read_file(requested)
        return self._parse_loaded_file(loaded, prepared)

    def read_file(self, path: str | Path) -> _LoadedCareerFile:
        self._raise_if_cancelled()
        requested = Path(path)
        if not _printable_path_components(requested):
            raise ParsingError("Career document path is unsafe")
        safe_name = requested.name or "document"
        candidate = requested if requested.is_absolute() else self.root / requested
        relative_path = self._bounded_relative_path(candidate)
        raw, file_stat = self._read_bounded(relative_path, safe_name)
        self._raise_if_cancelled()
        return _LoadedCareerFile(
            relative_path=relative_path,
            raw=raw,
            file_stat=file_stat,
        )

    def revalidate_loaded_file(self, loaded: _LoadedCareerFile) -> None:
        """Reopen one loaded path and verify its initial trusted snapshot."""
        self._raise_if_cancelled()
        safe_name = loaded.relative_path.name or "document"
        file_descriptor = -1
        try:
            file_descriptor, current_file_stat = self._open_bounded_file(
                loaded.relative_path,
                safe_name,
            )
            _verify_trusted_file_read_snapshot(
                loaded.file_stat,
                current_file_stat,
                safe_name,
            )
            if self._root_descriptor >= 0:
                self._verify_bound_root_path(safe_name)
        finally:
            if file_descriptor >= 0:
                os.close(file_descriptor)
        self._raise_if_cancelled()

    def parse_loaded_file(
        self,
        loaded: _LoadedCareerFile,
        *,
        source_type: EvidenceSourceType | str,
        experience_type: ExperienceType | str | None = None,
        document_id: str = "",
        document_title: str = "",
        company: str = "",
        role: str = "",
        project: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> DocumentModel:
        prepared = self._prepare_metadata(
            safe_name=loaded.relative_path.name or "document",
            source_type=source_type,
            experience_type=experience_type,
            document_id=document_id,
            document_title=document_title,
            company=company,
            role=role,
            project=project,
            start_date=start_date,
            end_date=end_date,
        )
        return self._parse_loaded_file(loaded, prepared)

    @staticmethod
    def _prepare_metadata(
        *,
        safe_name: str,
        source_type: EvidenceSourceType | str,
        experience_type: ExperienceType | str | None,
        document_id: str,
        document_title: str,
        company: str,
        role: str,
        project: str,
        start_date: str,
        end_date: str,
    ) -> tuple[EvidenceSourceType, ExperienceType, str, dict[str, str]]:
        normalized_metadata = {
            field_name: validate_career_metadata(field_name, value)
            for field_name, value in (
                ("document_title", document_title),
                ("company", company),
                ("role", role),
                ("project", project),
                ("start_date", start_date),
                ("end_date", end_date),
            )
        }
        normalized_document_id = validate_career_document_id(document_id)
        try:
            evidence_source_type = EvidenceSourceType(source_type)
            normalized_experience = (
                ExperienceType.UNKNOWN
                if experience_type is None
                else ExperienceType(experience_type)
            )
        except (TypeError, ValueError) as exc:
            raise ParsingError(f"Invalid career metadata for {safe_name}") from exc
        return (
            evidence_source_type,
            normalized_experience,
            normalized_document_id,
            normalized_metadata,
        )

    def _parse_loaded_file(
        self,
        loaded: _LoadedCareerFile,
        prepared: tuple[EvidenceSourceType, ExperienceType, str, dict[str, str]],
    ) -> DocumentModel:
        self._raise_if_cancelled()
        relative_path = loaded.relative_path
        raw = loaded.raw
        file_stat = loaded.file_stat
        safe_name = relative_path.name or "document"
        (
            evidence_source_type,
            normalized_experience,
            normalized_document_id,
            normalized_metadata,
        ) = prepared
        try:
            suffix = relative_path.suffix.lower()
            if suffix not in SUPPORTED_SUFFIXES:
                raise ParsingError(f"Unsupported career document format: {safe_name}")
            if suffix == ".pdf":
                content, extracted_title = self._parse_pdf(raw, safe_name)
            elif suffix == ".docx":
                content, extracted_title = self._parse_docx(raw, safe_name)
            elif suffix in {".md", ".markdown"}:
                content = self._decode_utf8(raw, safe_name)
                extracted_title = self._markdown_title(
                    content,
                    cancel_check=self.cancel_check,
                )
            else:
                content = self._decode_utf8(raw, safe_name)
                extracted_title = ""
        except CareerParsingCancelled:
            raise
        except ParsingError:
            raise
        except Exception as exc:
            raise ParsingError(f"Could not parse {safe_name}") from exc

        self._raise_if_cancelled()
        if not content.strip():
            raise ParsingError(f"Could not parse {safe_name}: document is empty")
        title = (
            normalized_metadata["document_title"]
            or extracted_title
            or relative_path.stem
        )
        title = validate_career_metadata("document_title", title)
        self._validate_section_titles(
            content,
            cancel_check=self.cancel_check,
        )

        normalized_relative_path = relative_path.as_posix()
        stable_document_id = normalized_document_id or self._stable_document_id(
            normalized_relative_path
        )
        content_hash = ContentHasher.hash_content(content)
        version_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        version_id = f"sha256:{version_digest}"
        birthtime = getattr(file_stat, "st_birthtime", None)
        created_at = self._timestamp(birthtime) if birthtime is not None else ""
        updated_at = self._timestamp(file_stat.st_mtime)
        url = f"career://{stable_document_id}"
        return DocumentModel(
            id=stable_document_id,
            document_id=stable_document_id,
            external_id=stable_document_id,
            title=title,
            document_title=title,
            content=content,
            url=url,
            canonical_url=url,
            platform="career",
            source_id="source_career",
            path=normalized_relative_path,
            file_name=relative_path.name,
            updated_at=updated_at,
            modified_at=updated_at,
            created_at=created_at,
            date_provenance="filesystem",
            version_id=version_id,
            document_version_id=version_id,
            content_hash=content_hash,
            evidence_source_type=evidence_source_type,
            experience_type=normalized_experience,
            company=normalized_metadata["company"],
            role=normalized_metadata["role"],
            project=normalized_metadata["project"],
            start_date=normalized_metadata["start_date"],
            end_date=normalized_metadata["end_date"],
        )

    def _raise_if_cancelled(self) -> None:
        if self.cancel_check is not None and self.cancel_check():
            raise CareerParsingCancelled("Career document parsing cancelled")

    def _bounded_relative_path(self, candidate: Path) -> Path:
        if not _printable_path_components(candidate):
            raise ParsingError("Career document path is unsafe")
        try:
            unresolved_relative = candidate.relative_to(self.root)
        except ValueError as exc:
            raise ParsingError(
                f"Career document is outside approved root: {candidate.name}"
            ) from exc
        if (
            not unresolved_relative.parts
            or ".." in unresolved_relative.parts
            or unresolved_relative.is_absolute()
        ):
            raise ParsingError(
                f"Career document is outside approved root: {candidate.name}"
            )
        return unresolved_relative

    def _read_bounded(
        self,
        relative_path: Path,
        safe_name: str,
    ) -> tuple[bytes, os.stat_result]:
        file_descriptor = -1
        try:
            file_descriptor, file_stat = self._open_bounded_file(
                relative_path,
                safe_name,
            )
            if file_stat.st_size > self.max_file_bytes:
                raise ParsingError(f"Career document exceeds byte limit: {safe_name}")
            try:
                with os.fdopen(file_descriptor, "rb") as handle:
                    file_descriptor = -1
                    raw = handle.read(self.max_file_bytes + 1)
                    completed_file_stat = os.fstat(handle.fileno())
            except (OSError, ValueError) as exc:
                raise ParsingError(f"Could not read {safe_name}") from exc
            _verify_trusted_file_read_snapshot(
                file_stat,
                completed_file_stat,
                safe_name,
            )
            if len(raw) > self.max_file_bytes:
                raise ParsingError(f"Career document exceeds byte limit: {safe_name}")
            if self._root_descriptor >= 0:
                self._verify_bound_root_path(safe_name)
            return raw, file_stat
        finally:
            if file_descriptor >= 0:
                os.close(file_descriptor)

    def _open_bounded_file(
        self,
        relative_path: Path,
        safe_name: str,
    ) -> tuple[int, os.stat_result]:
        root_descriptor = -1
        parent_descriptor = -1
        file_descriptor = -1
        try:
            if not _printable_path_components(relative_path):
                raise ParsingError("Career document path is unsafe")
            if self._root_descriptor >= 0:
                self._verify_bound_root_path(safe_name)
                root_descriptor = os.dup(self._root_descriptor)
                _validate_trusted_directory(
                    root_descriptor,
                    self.root,
                    safe_name,
                )
            else:
                root_descriptor = _open_trusted_absolute_directory(
                    self.root,
                    safe_name,
                )
            root_stat = os.fstat(root_descriptor)
            if (root_stat.st_dev, root_stat.st_ino) != self._root_identity:
                raise ParsingError(f"Could not read {safe_name}")
            parent_descriptor = root_descriptor
            logical_parent = self.root
            for component in relative_path.parts[:-1]:
                logical_parent = logical_parent / component
                next_descriptor = _open_trusted_child_directory(
                    parent_descriptor,
                    component,
                    logical_parent,
                    safe_name,
                )
                if parent_descriptor != root_descriptor:
                    os.close(parent_descriptor)
                parent_descriptor = next_descriptor
            file_descriptor = _open_trusted_regular_file_at(
                parent_descriptor,
                relative_path.name,
                safe_name,
            )
            file_stat = os.fstat(file_descriptor)
            retained_descriptor = file_descriptor
            file_descriptor = -1
            return retained_descriptor, file_stat
        except ParsingError:
            raise
        except (OSError, ValueError) as exc:
            raise ParsingError(f"Could not read {safe_name}") from exc
        finally:
            if file_descriptor >= 0:
                os.close(file_descriptor)
            if parent_descriptor >= 0 and parent_descriptor != root_descriptor:
                os.close(parent_descriptor)
            if root_descriptor >= 0:
                os.close(root_descriptor)

    def _verify_bound_root_path(self, safe_name: str) -> None:
        descriptor = -1
        try:
            descriptor = _open_trusted_absolute_directory(self.root, safe_name)
            root_stat = os.fstat(descriptor)
            if (root_stat.st_dev, root_stat.st_ino) != self._root_identity:
                raise ParsingError(f"Could not read {safe_name}")
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @staticmethod
    def _decode_utf8(raw: bytes, safe_name: str) -> str:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ParsingError(f"Could not parse {safe_name}: invalid UTF-8") from exc

    def _parse_pdf(self, raw: bytes, safe_name: str) -> tuple[str, str]:
        if not PdfReader.__module__.startswith("pypdf"):
            try:
                return _extract_pdf_content(
                    raw,
                    max_pages=self.max_pdf_pages,
                    max_chars=self.max_pdf_extracted_chars,
                    max_bytes=self.max_pdf_extracted_bytes,
                )
            except _PdfBoundExceeded as exc:
                self._raise_pdf_limit(exc.limit_name, safe_name)
            except Exception as exc:
                raise ParsingError(f"Could not parse {safe_name}") from exc

        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_pdf_extraction_worker,
            args=(
                sender,
                raw,
                self.max_pdf_pages,
                self.max_pdf_extracted_chars,
                self.max_pdf_extracted_bytes,
                max(1, math.ceil(self.pdf_extraction_timeout_seconds)),
            ),
            name="contextwiki-pdf-extractor",
            daemon=True,
        )
        process_started = False
        try:
            process.start()
            process_started = True
            sender.close()
            deadline = time.monotonic() + self.pdf_extraction_timeout_seconds
            monitor_rss = sys.platform.startswith("darwin")
            next_rss_sample_at = 0.0
            while True:
                self._raise_if_cancelled()
                now = time.monotonic()
                if monitor_rss and process.is_alive() and now >= next_rss_sample_at:
                    next_rss_sample_at = now + _DARWIN_RSS_SAMPLE_INTERVAL_SECONDS
                    process_id = process.pid
                    rss_bytes = (
                        _read_process_rss_bytes(process_id)
                        if process_id is not None
                        else None
                    )
                    if rss_bytes is None:
                        self._stop_pdf_worker(process)
                        raise ParsingError(
                            f"PDF extraction memory monitoring unavailable: {safe_name}"
                        )
                    if rss_bytes > _PDF_WORKER_MEMORY_BYTES:
                        self._stop_pdf_worker(process)
                        raise ParsingError(
                            f"Career PDF memory limit exceeded: {safe_name}"
                        )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._stop_pdf_worker(process)
                    raise ParsingError(f"PDF extraction timed out: {safe_name}")
                if receiver.poll(min(0.05, remaining)):
                    break
            payload = receiver.recv()
            process.join(0.2)
            if process.is_alive():
                self._stop_pdf_worker(process)
            if not payload or payload[0] == "error":
                raise ParsingError(f"Could not parse {safe_name}")
            if payload[0] == "limit":
                self._raise_pdf_limit(str(payload[1]), safe_name)
            if payload[0] != "ok" or len(payload) != 3:
                raise ParsingError(f"Could not parse {safe_name}")
            return str(payload[1]), str(payload[2])
        except CareerParsingCancelled:
            raise
        except ParsingError:
            raise
        except Exception as exc:
            raise ParsingError(f"Could not parse {safe_name}") from exc
        finally:
            sender.close()
            receiver.close()
            if process_started and process.is_alive():
                self._stop_pdf_worker(process)
            process.close()

    @staticmethod
    def _stop_pdf_worker(process) -> None:
        process.terminate()
        process.join(0.2)
        if process.is_alive():
            process.kill()
            process.join()

    @staticmethod
    def _raise_pdf_limit(limit_name: str, safe_name: str) -> None:
        raise ParsingError(f"Career PDF {limit_name} limit exceeded: {safe_name}")

    def _parse_docx(self, raw: bytes, safe_name: str) -> tuple[str, str]:
        output: list[str] = []
        title = ""
        try:
            with ZipFile(io.BytesIO(raw)) as archive:
                member = archive.getinfo("word/document.xml")
                if member.file_size > self.max_file_bytes:
                    raise ParsingError(
                        f"Career document XML exceeds byte limit: {safe_name}"
                    )
                with archive.open(member) as handle:
                    reader = _InterruptibleDocxXmlReader(
                        handle,
                        cancel_check=self.cancel_check,
                    )
                    for _event, element in ElementTree.iterparse(
                        reader,
                        events=("end",),
                    ):
                        self._raise_if_cancelled()
                        if element.tag != f"{_WORD}p":
                            continue
                        text_parts: list[str] = []
                        for node_index, node in enumerate(element.iter(f"{_WORD}t")):
                            if node_index % 64 == 0:
                                self._raise_if_cancelled()
                            text_parts.append(node.text or "")
                        text = "".join(text_parts).strip()
                        if text:
                            style_node = element.find(f"{_WORD}pPr/{_WORD}pStyle")
                            style = (
                                style_node.get(f"{_WORD}val", "")
                                if style_node is not None
                                else ""
                            )
                            heading_level = self._word_heading_level(style)
                            if style.lower() == "title" and not title:
                                title = text
                            if heading_level:
                                output.append(f"{'#' * heading_level} {text}")
                                if not title:
                                    title = text
                            else:
                                output.append(text)
                        element.clear()
            self._raise_if_cancelled()
        except ParsingError:
            raise
        except (
            BadZipFile,
            KeyError,
            ElementTree.ParseError,
            DefusedXmlException,
            OSError,
            RuntimeError,
        ) as exc:
            raise ParsingError(f"Could not parse {safe_name}") from exc
        return "\n\n".join(output), title

    @staticmethod
    def _word_heading_level(style: str) -> int:
        normalized = style.lower().replace(" ", "").replace("_", "")
        if normalized == "title":
            return 1
        if not normalized.startswith("heading"):
            return 0
        suffix = normalized.removeprefix("heading")
        if not suffix.isdigit():
            return 0
        return min(max(int(suffix), 1), 6)

    @staticmethod
    def _markdown_title(
        content: str,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        for line in _iter_text_lines(content, cancel_check=cancel_check):
            stripped = line.lstrip()
            if stripped.startswith("# "):
                return stripped[2:].strip().rstrip("#").strip()
        return ""

    @staticmethod
    def _validate_section_titles(
        content: str,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        paragraph_title_chars = 0
        for line in _iter_text_lines(content, cancel_check=cancel_check):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                heading_prefix = len(stripped) - len(stripped.lstrip("#"))
                if 1 <= heading_prefix <= 6:
                    title = stripped[heading_prefix:].strip().rstrip("#").strip()
                    if len(title) > MAX_SECTION_TITLE_CHARS:
                        raise ParsingError(
                            "Career section title exceeds character limit"
                        )
            if not stripped:
                paragraph_title_chars = 0
                continue
            if not set(stripped) - {"=", "-"}:
                if paragraph_title_chars > MAX_SECTION_TITLE_CHARS:
                    raise ParsingError("Career section title exceeds character limit")
                paragraph_title_chars = 0
                continue
            paragraph_title_chars += len(stripped) + (1 if paragraph_title_chars else 0)

    @staticmethod
    def _stable_document_id(relative_path: str) -> str:
        digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:24]
        return f"career:path:{digest}"

    @staticmethod
    def _timestamp(value: float) -> str:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
