from collections.abc import Callable, Iterator

from core.models import ChunkModel, DocumentModel
from core.utils import ContentHasher


MARKDOWN_EXTENSIONS = {".md", ".mdx", ".markdown"}
MAX_SETEXT_HEADING_CHARS = 512
CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".kt",
    ".kts",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".swift",
    ".scala",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".xml",
    ".html",
    ".css",
}


class _ChunkingCancelled(Exception):
    """Internal cooperative stop signal for an off-loop chunking worker."""


class DocumentChunker:
    """Deterministic character chunker with best-effort line metadata."""

    def __init__(self, max_chars: int = 1200, overlap_chars: int = 120):
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        if overlap_chars < 0:
            raise ValueError("overlap_chars must be non-negative")
        if overlap_chars >= max_chars:
            raise ValueError("overlap_chars must be smaller than max_chars")
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars

    def chunk_document(
        self,
        document: DocumentModel,
        *,
        stop_checker: Callable[[], bool] | None = None,
    ) -> list[ChunkModel]:
        self._raise_if_cancelled(stop_checker)
        if not self._has_non_whitespace(
            document.content,
            stop_checker=stop_checker,
        ):
            return []

        path = (document.path or document.title).lower()
        if document.evidence_source_type and self._has_markdown_heading(
            document.content,
            stop_checker=stop_checker,
        ):
            chunks = self._chunk_markdown(
                document,
                document.content,
                stop_checker=stop_checker,
            )
        elif self._has_extension(path, MARKDOWN_EXTENSIONS):
            if self._has_markdown_heading(
                document.content,
                stop_checker=stop_checker,
            ):
                chunks = self._chunk_markdown(
                    document,
                    document.content,
                    stop_checker=stop_checker,
                )
            else:
                chunks = self._chunk_plain_text(
                    document,
                    document.content,
                    stop_checker=stop_checker,
                )
        elif self._has_extension(path, CODE_EXTENSIONS):
            chunks = self._chunk_code(
                document,
                document.content,
                stop_checker=stop_checker,
            )
        else:
            chunks = self._chunk_plain_text(
                document,
                document.content.strip(),
                stop_checker=stop_checker,
            )

        if document.evidence_source_type:
            return self._stable_evidence_chunk_ids(
                chunks,
                stop_checker=stop_checker,
            )
        return chunks

    @staticmethod
    def _stable_evidence_chunk_ids(
        chunks: list[ChunkModel],
        *,
        stop_checker: Callable[[], bool] | None = None,
    ) -> list[ChunkModel]:
        duplicate_ordinals: dict[str, int] = {}
        stable_chunks: list[ChunkModel] = []
        for chunk in chunks:
            DocumentChunker._raise_if_cancelled(stop_checker)
            ordinal = duplicate_ordinals.get(chunk.content_hash, 0)
            duplicate_ordinals[chunk.content_hash] = ordinal + 1
            stable_chunks.append(
                chunk.model_copy(
                    update={
                        "chunk_id": (
                            f"{chunk.document_id}:chunk:{chunk.content_hash}:{ordinal}"
                        )
                    }
                )
            )
        return stable_chunks

    @staticmethod
    def _base_line_offset(document: DocumentModel) -> int:
        return max(0, (document.line_start or 1) - 1)

    def _chunk_plain_text(
        self,
        document: DocumentModel,
        content: str,
        *,
        stop_checker: Callable[[], bool] | None = None,
    ) -> list[ChunkModel]:
        chunks: list[ChunkModel] = []
        base_line_offset = self._base_line_offset(document)
        previous_start_offset = 0
        previous_end_offset = 0
        start_newlines = 0
        end_newlines = 0
        start = 0
        while start < len(content):
            self._raise_if_cancelled(stop_checker)
            end = min(len(content), start + self.max_chars)
            raw_segment = content[start:end]
            leading_trim = len(raw_segment) - len(raw_segment.lstrip())
            trailing_trim = len(raw_segment.rstrip())
            text = raw_segment.strip()
            if text:
                effective_start = start + leading_trim
                effective_end = start + trailing_trim
                start_newlines += content.count(
                    "\n",
                    previous_start_offset,
                    effective_start,
                )
                end_newlines += content.count(
                    "\n",
                    previous_end_offset,
                    effective_end,
                )
                previous_start_offset = effective_start
                previous_end_offset = effective_end
                self._raise_if_cancelled(stop_checker)
                chunks.append(
                    self._build_chunk(
                        document,
                        text,
                        len(chunks),
                        start_newlines + 1 + base_line_offset,
                        end_newlines + 1 + base_line_offset,
                    )
                )

            if end >= len(content):
                break
            start = end - self.overlap_chars
        return chunks

    def _chunk_markdown(
        self,
        document: DocumentModel,
        content: str,
        *,
        stop_checker: Callable[[], bool] | None = None,
    ) -> list[ChunkModel]:
        base_line_start = document.line_start or 1
        current_start = base_line_start
        current_lines: list[str] = []
        current_text_length = 0
        active_fence: tuple[str, int] | None = None
        paragraph_start_index = 0
        paragraph_title_chars = 0
        paragraph_can_be_setext = True
        chunks: list[ChunkModel] = []
        heading_stack: dict[int, str] = {}
        section_title = ""
        parent_section_title = ""
        section_heading_resolved = False

        def reset_section_heading() -> None:
            nonlocal section_title, parent_section_title, section_heading_resolved
            section_title = ""
            parent_section_title = ""
            section_heading_resolved = False

        def resolve_section_heading(text: str) -> None:
            nonlocal section_title, parent_section_title, section_heading_resolved
            if section_heading_resolved:
                return
            heading = self._section_heading(
                text,
                stop_checker=stop_checker,
            )
            if heading is not None:
                heading_level, section_title = heading
                parent_levels = [
                    level for level in heading_stack if level < heading_level
                ]
                if parent_levels:
                    parent_section_title = heading_stack[max(parent_levels)]
                stale_levels = [
                    level for level in heading_stack if level >= heading_level
                ]
                for level in stale_levels:
                    del heading_stack[level]
                heading_stack[heading_level] = section_title
            section_heading_resolved = True

        def append_section_text(text: str, line_start: int) -> None:
            resolve_section_heading(text)
            section_chunks = self._split_section_text(
                document,
                text,
                len(chunks),
                line_start,
                section_title=section_title,
                parent_section_title=parent_section_title,
                stop_checker=stop_checker,
            )
            for section_chunk in section_chunks:
                self._raise_if_cancelled(stop_checker)
                chunks.append(section_chunk)

        def flush_section(
            lines: list[str],
            line_start: int,
            *,
            end_index: int | None = None,
        ) -> None:
            trimmed = self._trim_lines(
                lines,
                line_start,
                end_index=end_index,
                stop_checker=stop_checker,
            )
            if not trimmed:
                return
            text, trimmed_start, _trimmed_end = trimmed
            append_section_text(text, trimmed_start)

        def flush_oversized_prefix() -> None:
            nonlocal current_lines, current_start, current_text_length
            nonlocal paragraph_start_index, paragraph_title_chars
            nonlocal paragraph_can_be_setext
            if current_text_length <= self.max_chars:
                return
            if active_fence is None and paragraph_can_be_setext:
                return
            text = "\n".join(current_lines)
            cursor = 0
            line_start = current_start
            advance = self.max_chars - self.overlap_chars
            while len(text) - cursor > self.max_chars:
                self._raise_if_cancelled(stop_checker)
                append_section_text(
                    text[cursor : cursor + self.max_chars],
                    line_start,
                )
                next_cursor = cursor + advance
                line_start += text.count("\n", cursor, next_cursor)
                cursor = next_cursor
            remaining = text[cursor:]
            current_lines = list(self._iter_lines(remaining, stop_checker=stop_checker))
            current_start = line_start
            current_text_length = len("\n".join(current_lines))
            if active_fence is not None:
                paragraph_start_index = len(current_lines)
                paragraph_title_chars = 0
                paragraph_can_be_setext = True
            else:
                paragraph_start_index = 0
                paragraph_title_chars = MAX_SETEXT_HEADING_CHARS + 1
                paragraph_can_be_setext = False

        for line_number, line in enumerate(
            self._iter_lines(content, stop_checker=stop_checker),
            start=base_line_start,
        ):
            if line_number % 256 == 0:
                self._raise_if_cancelled(stop_checker)
            if not current_lines and not line.strip():
                continue
            is_setext_heading = (
                active_fence is None
                and self._is_setext_heading_underline(line)
                and current_lines
                and self._can_be_setext_heading_text(current_lines[-1])
                and paragraph_can_be_setext
            )
            if is_setext_heading:
                paragraph_start = paragraph_start_index
                flush_section(
                    current_lines,
                    current_start,
                    end_index=paragraph_start,
                )
                current_start = current_start + paragraph_start
                del current_lines[:paragraph_start]
                current_text_length = len("\n".join(current_lines))
                reset_section_heading()
                current_lines.append(line)
                current_text_length += len(line) + (1 if len(current_lines) > 1 else 0)
                paragraph_start_index = len(current_lines)
                paragraph_title_chars = 0
                paragraph_can_be_setext = True
                active_fence = self._update_markdown_fence(active_fence, line)
                resolve_section_heading("\n".join(current_lines))
                continue
            is_heading = self._is_markdown_heading(line) and active_fence is None
            if is_heading and current_lines:
                flush_section(current_lines, current_start)
                reset_section_heading()
                current_start = line_number
                current_lines = [line]
                current_text_length = len(line)
                paragraph_start_index = len(current_lines)
                paragraph_title_chars = 0
                paragraph_can_be_setext = True
                resolve_section_heading(line)
            else:
                if not current_lines:
                    current_start = line_number
                current_text_length += len(line) + (1 if current_lines else 0)
                current_lines.append(line)
                if is_heading:
                    paragraph_start_index = len(current_lines)
                    paragraph_title_chars = 0
                    paragraph_can_be_setext = True
                    resolve_section_heading(line)
                elif not line.strip():
                    paragraph_start_index = len(current_lines)
                    paragraph_title_chars = 0
                    paragraph_can_be_setext = True
                else:
                    paragraph_title_chars += len(line.strip()) + (
                        1 if paragraph_title_chars else 0
                    )
                    paragraph_can_be_setext = (
                        paragraph_can_be_setext
                        and self._can_be_setext_heading_text(line)
                        and paragraph_title_chars <= MAX_SETEXT_HEADING_CHARS
                    )
            active_fence = self._update_markdown_fence(active_fence, line)
            if active_fence is not None or self._markdown_fence_marker(
                line, closing=True
            ):
                paragraph_start_index = len(current_lines)
                paragraph_title_chars = 0
                paragraph_can_be_setext = True
            flush_oversized_prefix()

        if current_lines:
            flush_section(current_lines, current_start)
        return chunks

    def _chunk_code(
        self,
        document: DocumentModel,
        content: str,
        *,
        stop_checker: Callable[[], bool] | None = None,
    ) -> list[ChunkModel]:
        chunks: list[ChunkModel] = []
        current_lines: list[str] = []
        current_text_length = 0
        current_start = document.line_start or 1

        for line_number, line in enumerate(
            self._iter_lines(content, stop_checker=stop_checker),
            start=current_start,
        ):
            if line_number % 256 == 0:
                self._raise_if_cancelled(stop_checker)
            if not current_lines and not line.strip():
                continue
            if not current_lines:
                current_start = line_number
            candidate_length = current_text_length + len(line)
            if current_lines:
                candidate_length += 1
            if current_lines and candidate_length > self.max_chars:
                code_chunk = self._code_lines_to_chunk(current_lines, current_start)
                if code_chunk:
                    text, trimmed_start, trimmed_end = code_chunk
                    self._raise_if_cancelled(stop_checker)
                    chunks.append(
                        self._build_chunk(
                            document,
                            text,
                            len(chunks),
                            trimmed_start,
                            trimmed_end,
                        )
                    )
                current_lines = [line]
                current_text_length = len(line)
                current_start = line_number
                if len(line) > self.max_chars:
                    chunks.extend(
                        self._split_code_line(
                            document,
                            line,
                            len(chunks),
                            line_number,
                            stop_checker=stop_checker,
                        )
                    )
                    current_lines = []
                    current_text_length = 0
            else:
                if not current_lines and len(line) > self.max_chars:
                    chunks.extend(
                        self._split_code_line(
                            document,
                            line,
                            len(chunks),
                            line_number,
                            stop_checker=stop_checker,
                        )
                    )
                    current_text_length = 0
                else:
                    current_lines.append(line)
                    current_text_length = candidate_length

        if current_lines:
            code_chunk = self._code_lines_to_chunk(current_lines, current_start)
            if code_chunk:
                text, trimmed_start, trimmed_end = code_chunk
                self._raise_if_cancelled(stop_checker)
                chunks.append(
                    self._build_chunk(
                        document,
                        text,
                        len(chunks),
                        trimmed_start,
                        trimmed_end,
                    )
                )

        return chunks

    @staticmethod
    def _code_lines_to_chunk(
        lines: list[str], line_start: int
    ) -> tuple[str, int, int] | None:
        text = "\n".join(lines)
        if not text.strip():
            return None
        return text, line_start, line_start + len(lines) - 1

    def _split_code_line(
        self,
        document: DocumentModel,
        line: str,
        first_chunk_index: int,
        line_number: int,
        *,
        stop_checker: Callable[[], bool] | None = None,
    ) -> list[ChunkModel]:
        chunks: list[ChunkModel] = []
        start = 0
        while start < len(line):
            self._raise_if_cancelled(stop_checker)
            end = min(len(line), start + self.max_chars)
            text = line[start:end]
            if text.strip():
                chunks.append(
                    self._build_chunk(
                        document,
                        text,
                        first_chunk_index + len(chunks),
                        line_number,
                        line_number,
                    )
                )
            if end >= len(line):
                break
            start = end - self.overlap_chars
        return chunks

    def _split_section_text(
        self,
        document: DocumentModel,
        text: str,
        first_chunk_index: int,
        line_start: int,
        *,
        section_title: str = "",
        parent_section_title: str = "",
        stop_checker: Callable[[], bool] | None = None,
    ) -> list[ChunkModel]:
        chunks: list[ChunkModel] = []
        previous_start_offset = 0
        previous_end_offset = 0
        start_newlines = 0
        end_newlines = 0
        start = 0
        while start < len(text):
            self._raise_if_cancelled(stop_checker)
            end = min(len(text), start + self.max_chars)
            raw_segment = text[start:end]
            leading_trim = len(raw_segment) - len(raw_segment.lstrip())
            trailing_trim = len(raw_segment.rstrip())
            segment = raw_segment.strip()
            if segment:
                effective_start = start + leading_trim
                effective_end = start + trailing_trim
                start_newlines += text.count(
                    "\n",
                    previous_start_offset,
                    effective_start,
                )
                end_newlines += text.count(
                    "\n",
                    previous_end_offset,
                    effective_end,
                )
                previous_start_offset = effective_start
                previous_end_offset = effective_end
                self._raise_if_cancelled(stop_checker)
                chunks.append(
                    self._build_chunk(
                        document,
                        segment,
                        first_chunk_index + len(chunks),
                        line_start + start_newlines,
                        line_start + end_newlines,
                        section_title=section_title,
                        parent_section_title=parent_section_title,
                    )
                )
            if end >= len(text):
                break
            start = end - self.overlap_chars
        return chunks

    @staticmethod
    def _trim_lines(
        lines: list[str],
        line_start: int,
        *,
        end_index: int | None = None,
        stop_checker: Callable[[], bool] | None = None,
    ) -> tuple[str, int, int] | None:
        first = 0
        last = (len(lines) if end_index is None else end_index) - 1
        while first <= last and not lines[first].strip():
            DocumentChunker._raise_if_cancelled(stop_checker)
            first += 1
        while last >= first and not lines[last].strip():
            DocumentChunker._raise_if_cancelled(stop_checker)
            last -= 1
        if first > last:
            return None

        def iter_trimmed_lines() -> Iterator[str]:
            for index in range(first, last + 1):
                if index % 256 == 0:
                    DocumentChunker._raise_if_cancelled(stop_checker)
                yield lines[index]

        return (
            "\n".join(iter_trimmed_lines()),
            line_start + first,
            line_start + last,
        )

    @staticmethod
    def _build_chunk(
        document: DocumentModel,
        text: str,
        chunk_index: int,
        line_start: int,
        line_end: int,
        *,
        section_title: str = "",
        parent_section_title: str = "",
    ) -> ChunkModel:
        document_id = (
            document.document_id
            if document.evidence_source_type and document.document_id
            else document.external_id or document.document_id or document.id
        )
        content_hash = ContentHasher.hash_content(text)
        is_evidence = document.evidence_source_type is not None
        return ChunkModel(
            chunk_id=f"{document_id}:chunk:{chunk_index}:{content_hash[:12]}",
            document_id=document_id,
            source_id=document.source_id,
            title=document.title,
            text=text,
            url=document.canonical_url or document.url,
            path=document.path or document.title,
            chunk_index=chunk_index,
            line_start=line_start,
            line_end=line_end,
            version_id=document.version_id,
            document_version_id=(
                document.document_version_id or document.version_id
                if is_evidence
                else ""
            ),
            content_hash=content_hash,
            updated_at=document.updated_at or document.date,
            evidence_source_type=(
                document.evidence_source_type if is_evidence else None
            ),
            experience_type=(document.experience_type if is_evidence else "unknown"),
            file_name=document.file_name if is_evidence else "",
            document_title=(
                document.document_title or document.title if is_evidence else ""
            ),
            section_title=(
                section_title or document.section_title if is_evidence else ""
            ),
            parent_section_title=(
                parent_section_title or document.parent_section_title
                if is_evidence
                else ""
            ),
            exact_quote=text if is_evidence else "",
            created_at=document.created_at if is_evidence else "",
            company=document.company if is_evidence else "",
            role=document.role if is_evidence else "",
            project=document.project if is_evidence else "",
            start_date=document.start_date if is_evidence else "",
            end_date=document.end_date if is_evidence else "",
        )

    @staticmethod
    def _section_heading(
        text: str,
        *,
        stop_checker: Callable[[], bool] | None = None,
    ) -> tuple[int, str] | None:
        lines = DocumentChunker._iter_lines(text, stop_checker=stop_checker)
        first_line = next(lines, None)
        if first_line is None:
            return None
        first = first_line.lstrip()
        if DocumentChunker._is_markdown_heading(first_line):
            level = len(first) - len(first.lstrip("#"))
            title = first[level:].strip().rstrip("#").strip()
            return (level, title) if title else None
        title_lines = [first_line.strip()] if first_line.strip() else []
        for line in lines:
            if not DocumentChunker._is_setext_heading_underline(line):
                if line.strip():
                    title_lines.append(line.strip())
                continue
            if not title_lines:
                return None
            level = 1 if line.strip().startswith("=") else 2
            return level, " ".join(title_lines)
        return None

    @staticmethod
    def _has_extension(path: str, extensions: set[str]) -> bool:
        return any(path.endswith(extension) for extension in extensions)

    @staticmethod
    def _has_markdown_heading(
        content: str,
        *,
        stop_checker: Callable[[], bool] | None = None,
    ) -> bool:
        active_fence: tuple[str, int] | None = None
        paragraph_has_lines = False
        paragraph_can_be_setext = True
        for line_index, line in enumerate(
            DocumentChunker._iter_lines(content, stop_checker=stop_checker)
        ):
            if line_index % 256 == 0:
                DocumentChunker._raise_if_cancelled(stop_checker)
            if active_fence is None and DocumentChunker._is_markdown_heading(line):
                return True
            if (
                active_fence is None
                and paragraph_has_lines
                and DocumentChunker._is_setext_heading_underline(line)
                and paragraph_can_be_setext
            ):
                return True
            was_fence_line = (
                DocumentChunker._markdown_fence_marker(
                    line,
                    closing=active_fence is not None,
                )
                is not None
            )
            active_fence = DocumentChunker._update_markdown_fence(active_fence, line)
            if active_fence is not None or not line.strip() or was_fence_line:
                paragraph_has_lines = False
                paragraph_can_be_setext = True
            else:
                paragraph_has_lines = True
                paragraph_can_be_setext = (
                    paragraph_can_be_setext
                    and DocumentChunker._can_be_setext_heading_text(line)
                )
        return False

    @staticmethod
    def _iter_lines(
        content: str,
        *,
        stop_checker: Callable[[], bool] | None = None,
    ) -> Iterator[str]:
        """Yield ``str.splitlines()``-compatible lines without a full line list."""
        line_breaks = {
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
        start = 0
        index = 0
        next_stop_check = 0
        while index < len(content):
            if index >= next_stop_check:
                DocumentChunker._raise_if_cancelled(stop_checker)
                next_stop_check = index + 256
            character = content[index]
            if character not in line_breaks:
                index += 1
                continue
            yield content[start:index]
            if character == "\r" and index + 1 < len(content):
                if content[index + 1] == "\n":
                    index += 1
            index += 1
            start = index
        DocumentChunker._raise_if_cancelled(stop_checker)
        if start < len(content):
            yield content[start:]

    @staticmethod
    def _has_non_whitespace(
        content: str,
        *,
        stop_checker: Callable[[], bool] | None = None,
    ) -> bool:
        for index, character in enumerate(content):
            if index % 256 == 0:
                DocumentChunker._raise_if_cancelled(stop_checker)
            if not character.isspace():
                return True
        return False

    @staticmethod
    def _cumulative_newline_counts(
        content: str,
        offsets: list[int],
        *,
        stop_checker: Callable[[], bool] | None = None,
    ) -> list[int]:
        newline_count = 0
        previous_offset = 0
        counts: list[int] = []
        for offset in offsets:
            DocumentChunker._raise_if_cancelled(stop_checker)
            if offset < previous_offset:
                raise ValueError("line-number offsets must be monotonic")
            newline_count += content.count("\n", previous_offset, offset)
            counts.append(newline_count)
            previous_offset = offset
        return counts

    @staticmethod
    def _raise_if_cancelled(
        stop_checker: Callable[[], bool] | None,
    ) -> None:
        if stop_checker is not None and stop_checker():
            raise _ChunkingCancelled

    @staticmethod
    def _update_markdown_fence(
        active_fence: tuple[str, int] | None,
        line: str,
    ) -> tuple[str, int] | None:
        marker = DocumentChunker._markdown_fence_marker(
            line, closing=active_fence is not None
        )
        if marker is None:
            return active_fence
        marker_char, marker_length = marker
        if active_fence is None:
            return marker
        active_char, active_length = active_fence
        if marker_char == active_char and marker_length >= active_length:
            return None
        return active_fence

    @staticmethod
    def _markdown_fence_marker(
        line: str, *, closing: bool = False
    ) -> tuple[str, int] | None:
        indent = len(line) - len(line.lstrip(" "))
        if indent > 3:
            return None
        stripped = line[indent:]
        if stripped.startswith("```"):
            marker_char = "`"
        elif stripped.startswith("~~~"):
            marker_char = "~"
        else:
            return None
        marker_length = 0
        for character in stripped:
            if character != marker_char:
                break
            marker_length += 1
        if closing and stripped[marker_length:].strip():
            return None
        if not closing and marker_char == "`" and "`" in stripped[marker_length:]:
            return None
        return marker_char, marker_length

    @staticmethod
    def _is_markdown_heading(line: str) -> bool:
        indent = len(line) - len(line.lstrip(" "))
        if indent > 3:
            return False
        stripped = line[indent:]
        hash_count = 0
        for character in stripped:
            if character != "#":
                break
            hash_count += 1
        if hash_count == 0 or hash_count > 6:
            return False
        return len(stripped) == hash_count or stripped[hash_count].isspace()

    @staticmethod
    def _is_setext_heading_underline(line: str) -> bool:
        indent = len(line) - len(line.lstrip(" "))
        if indent > 3:
            return False
        stripped = line[indent:].strip()
        return bool(stripped) and set(stripped) in ({"="}, {"-"})

    @staticmethod
    def _can_be_setext_heading_text(line: str) -> bool:
        if not line.strip():
            return False
        indent = len(line) - len(line.lstrip(" "))
        if indent > 3:
            return False
        if DocumentChunker._is_markdown_heading(line):
            return False
        if DocumentChunker._is_setext_heading_underline(line):
            return False
        if DocumentChunker._markdown_fence_marker(line) is not None:
            return False
        return True
