from core.models import ChunkModel, DocumentModel
from core.utils import ContentHasher


MARKDOWN_EXTENSIONS = {".md", ".mdx", ".markdown"}
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

    def chunk_document(self, document: DocumentModel) -> list[ChunkModel]:
        if not document.content.strip():
            return []

        path = (document.path or document.title).lower()
        if self._has_extension(path, MARKDOWN_EXTENSIONS) and self._has_markdown_heading(document.content):
            return self._chunk_markdown(document, document.content)
        if self._has_extension(path, CODE_EXTENSIONS):
            return self._chunk_code(document, document.content)

        return self._chunk_plain_text(document, document.content.strip())

    def _chunk_plain_text(self, document: DocumentModel, content: str) -> list[ChunkModel]:
        chunks = []
        start = 0
        chunk_index = 0
        while start < len(content):
            end = min(len(content), start + self.max_chars)
            text = content[start:end].strip()
            if text:
                line_start = content.count("\n", 0, start) + 1
                line_end = line_start + text.count("\n")
                chunks.append(self._build_chunk(document, text, chunk_index, line_start, line_end))
                chunk_index += 1

            if end >= len(content):
                break
            start = end - self.overlap_chars

        return chunks

    def _chunk_markdown(self, document: DocumentModel, content: str) -> list[ChunkModel]:
        sections: list[tuple[int, list[str]]] = []
        current_start = 1
        current_lines: list[str] = []
        active_fence: tuple[str, int] | None = None
        paragraph_start_index = 0

        for line_number, line in enumerate(content.splitlines(), start=1):
            if not current_lines and not line.strip():
                continue
            is_setext_heading = (
                active_fence is None
                and self._is_setext_heading_underline(line)
                and current_lines
                and self._can_be_setext_heading_text(current_lines[-1])
            )
            if is_setext_heading:
                paragraph_start = paragraph_start_index
                before_heading = current_lines[:paragraph_start]
                heading_lines = [*current_lines[paragraph_start:], line]
                if self._trim_lines(before_heading, current_start):
                    sections.append((current_start, before_heading))
                current_start = current_start + paragraph_start
                current_lines = heading_lines
                paragraph_start_index = len(current_lines)
                active_fence = self._update_markdown_fence(active_fence, line)
                continue
            is_heading = self._is_markdown_heading(line) and active_fence is None
            if is_heading and current_lines:
                sections.append((current_start, current_lines))
                current_start = line_number
                current_lines = [line]
                paragraph_start_index = len(current_lines)
            else:
                if not current_lines:
                    current_start = line_number
                current_lines.append(line)
                if is_heading:
                    paragraph_start_index = len(current_lines)
                elif not line.strip():
                    paragraph_start_index = len(current_lines)
            active_fence = self._update_markdown_fence(active_fence, line)
            if active_fence is not None or self._markdown_fence_marker(line, closing=True):
                paragraph_start_index = len(current_lines)

        if current_lines:
            sections.append((current_start, current_lines))

        chunks = []
        for line_start, lines in sections:
            trimmed = self._trim_lines(lines, line_start)
            if not trimmed:
                continue
            text, trimmed_start, trimmed_end = trimmed
            chunks.extend(
                self._split_section_text(
                    document,
                    text,
                    len(chunks),
                    trimmed_start,
                )
            )
        return chunks

    def _chunk_code(self, document: DocumentModel, content: str) -> list[ChunkModel]:
        chunks = []
        current_lines: list[str] = []
        current_start = 1

        for line_number, line in enumerate(content.splitlines(), start=1):
            if not current_lines and not line.strip():
                continue
            if not current_lines:
                current_start = line_number
            candidate_lines = [*current_lines, line]
            candidate_text = "\n".join(candidate_lines)
            if current_lines and len(candidate_text) > self.max_chars:
                code_chunk = self._code_lines_to_chunk(current_lines, current_start)
                if code_chunk:
                    text, trimmed_start, trimmed_end = code_chunk
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
                current_start = line_number
                if len(line) > self.max_chars:
                    chunks.extend(
                        self._split_code_line(document, line, len(chunks), line_number)
                    )
                    current_lines = []
            else:
                if not current_lines and len(line) > self.max_chars:
                    chunks.extend(
                        self._split_code_line(document, line, len(chunks), line_number)
                    )
                else:
                    current_lines.append(line)

        if current_lines:
            code_chunk = self._code_lines_to_chunk(current_lines, current_start)
            if code_chunk:
                text, trimmed_start, trimmed_end = code_chunk
                chunks.append(
                    self._build_chunk(
                        document,
                        text,
                        len(chunks),
                        trimmed_start,
                        trimmed_end,
                    )
                )

        return [chunk for chunk in chunks if chunk.text]

    @staticmethod
    def _code_lines_to_chunk(lines: list[str], line_start: int) -> tuple[str, int, int] | None:
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
    ) -> list[ChunkModel]:
        chunks = []
        start = 0
        while start < len(line):
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
    ) -> list[ChunkModel]:
        chunks = []
        start = 0
        while start < len(text):
            end = min(len(text), start + self.max_chars)
            raw_segment = text[start:end]
            leading_trim = len(raw_segment) - len(raw_segment.lstrip())
            trailing_trim = len(raw_segment.rstrip())
            segment = raw_segment.strip()
            if segment:
                effective_start = start + leading_trim
                effective_end = start + trailing_trim
                segment_line_start = line_start + text.count("\n", 0, effective_start)
                segment_line_end = line_start + text.count("\n", 0, effective_end)
                chunks.append(
                    self._build_chunk(
                        document,
                        segment,
                        first_chunk_index + len(chunks),
                        segment_line_start,
                        segment_line_end,
                    )
                )
            if end >= len(text):
                break
            start = end - self.overlap_chars
        return chunks

    @staticmethod
    def _trim_lines(lines: list[str], line_start: int) -> tuple[str, int, int] | None:
        first = 0
        last = len(lines) - 1
        while first <= last and not lines[first].strip():
            first += 1
        while last >= first and not lines[last].strip():
            last -= 1
        if first > last:
            return None
        trimmed_lines = lines[first : last + 1]
        return "\n".join(trimmed_lines), line_start + first, line_start + last

    @staticmethod
    def _build_chunk(
        document: DocumentModel,
        text: str,
        chunk_index: int,
        line_start: int,
        line_end: int,
    ) -> ChunkModel:
        document_id = document.external_id or document.document_id or document.id
        content_hash = ContentHasher.hash_content(text)
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
            content_hash=content_hash,
            updated_at=document.updated_at or document.date,
        )

    @staticmethod
    def _has_extension(path: str, extensions: set[str]) -> bool:
        return any(path.endswith(extension) for extension in extensions)

    @staticmethod
    def _has_markdown_heading(content: str) -> bool:
        active_fence: tuple[str, int] | None = None
        paragraph_lines: list[str] = []
        for line in content.splitlines():
            if active_fence is None and DocumentChunker._is_markdown_heading(line):
                return True
            if (
                active_fence is None
                and paragraph_lines
                and DocumentChunker._is_setext_heading_underline(line)
                and all(
                    DocumentChunker._can_be_setext_heading_text(paragraph_line)
                    for paragraph_line in paragraph_lines
                )
            ):
                return True
            was_fence_line = DocumentChunker._markdown_fence_marker(
                line,
                closing=active_fence is not None,
            ) is not None
            active_fence = DocumentChunker._update_markdown_fence(active_fence, line)
            if active_fence is not None or not line.strip() or was_fence_line:
                paragraph_lines = []
            else:
                paragraph_lines.append(line)
        return False

    @staticmethod
    def _update_markdown_fence(
        active_fence: tuple[str, int] | None,
        line: str,
    ) -> tuple[str, int] | None:
        marker = DocumentChunker._markdown_fence_marker(line, closing=active_fence is not None)
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
    def _markdown_fence_marker(line: str, *, closing: bool = False) -> tuple[str, int] | None:
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
