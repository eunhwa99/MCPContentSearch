from collections import OrderedDict
import inspect
import re

from core.models import ContextSearchResult


_CITATION_MARKER_PATTERN = re.compile(r"\[C(\d+)\]")
_SENTENCE_BOUNDARY_PATTERN = re.compile(r"(?<!\d)(?<=[.!?])\s*(?!\d)")
_ABBREVIATION_DOT_PLACEHOLDER = "<ABBR_DOT>"
_DOTTED_TOKEN_PATTERN = re.compile(
    r"\b[A-Za-z0-9_-]+(?:\.[a-z0-9_-]+)+"
)
_DOTFILE_TOKEN_PATTERN = re.compile(
    r"(^|(?<=[\s/]))\.[A-Za-z0-9_-]+(?:\.[a-z0-9_-]+)*"
)
_INITIALISM_PATTERN = re.compile(r"\b(?:[A-Za-z]\.){2,}")
_TECHNICAL_DOTTED_SUFFIXES = {
    "ai",
    "app",
    "com",
    "dev",
    "env",
    "go",
    "io",
    "js",
    "json",
    "local",
    "md",
    "net",
    "org",
    "py",
    "rs",
    "toml",
    "ts",
    "txt",
    "yaml",
    "yml",
}
_SENTENCE_ABBREVIATIONS = (
    "e.g.",
    "i.e.",
    "etc.",
    "vs.",
    "Mr.",
    "Mrs.",
    "Ms.",
    "Dr.",
    "Prof.",
    "Sr.",
    "Jr.",
)


class WikiGenerationService:
    """Generate read-only wiki pages from citation-ready ContextWiki evidence."""

    def __init__(
        self,
        context_search,
        *,
        default_top_k: int = 8,
        max_top_k: int = 20,
        min_score: float = 0.35,
        min_results: int = 1,
        llm_synthesizer=None,
    ):
        self.context_search = context_search
        self.default_top_k = default_top_k
        self.max_top_k = max_top_k
        self.min_score = min_score
        self.min_results = min_results
        self.llm_synthesizer = llm_synthesizer

    async def generate_wiki_page(
        self,
        topic: str,
        filters: dict | None = None,
        top_k: int | None = None,
    ) -> dict:
        normalized_topic = self._normalize_topic(topic)
        if not normalized_topic:
            return self._insufficient_response(
                topic=topic,
                message="A non-empty topic is required to generate a wiki page.",
            )

        limit = self._normalize_top_k(top_k)
        search_result = await self.context_search.search_context(
            normalized_topic,
            filters=filters or {},
            top_k=limit,
        )
        results = [self._as_result(item) for item in search_result.get("results", [])]
        evidence = [item for item in results if item.score >= self.min_score]
        if len(evidence) < self.min_results:
            return self._insufficient_response(
                topic=normalized_topic,
                message="Insufficient evidence in indexed context to generate a wiki page.",
            )

        citations = self._build_citations(evidence)
        backlinks = self._build_backlinks(evidence)
        sections = self._build_sections(normalized_topic, evidence, citations, backlinks)
        markdown = self._render_markdown(normalized_topic, sections, citations, backlinks)
        title = f"{normalized_topic} Wiki"
        synthesized = await self._try_synthesize_wiki_page(
            topic=normalized_topic,
            evidence=evidence,
            citations=citations,
            backlinks=backlinks,
            fallback_title=title,
            fallback_sections=sections,
            fallback_markdown=markdown,
        )
        if synthesized:
            title = synthesized["title"]
            sections = synthesized["sections"]
            markdown = synthesized["markdown"]

        return {
            "topic": normalized_topic,
            "status": "generated",
            "title": title,
            "markdown": markdown,
            "sections": sections,
            "citations": citations,
            "backlinks": backlinks,
            "used_chunks": [item.chunk_id for item in evidence],
        }

    def _normalize_top_k(self, top_k: int | None) -> int:
        if top_k is None:
            return self.default_top_k
        try:
            requested = int(top_k)
        except (TypeError, ValueError):
            requested = self.default_top_k
        return max(1, min(requested, self.max_top_k))

    @staticmethod
    def _normalize_topic(topic: str) -> str:
        return " ".join(str(topic or "").split())

    @staticmethod
    def _as_result(item) -> ContextSearchResult:
        if isinstance(item, ContextSearchResult):
            return item
        return ContextSearchResult(**item)

    @staticmethod
    def _build_citations(evidence: list[ContextSearchResult]) -> list[dict]:
        citations = []
        for index, item in enumerate(evidence, start=1):
            citations.append(
                {
                    "marker": f"C{index}",
                    "chunk_id": item.chunk_id,
                    "document_id": item.document_id,
                    "source_id": item.source_id,
                    "source_type": item.source_type,
                    "title": item.title,
                    "url": item.url,
                    "path": item.path,
                    "line_start": item.line_start,
                    "line_end": item.line_end,
                    "version_id": item.version_id,
                    "score": item.score,
                }
            )
        return citations

    @staticmethod
    def _build_backlinks(evidence: list[ContextSearchResult]) -> list[dict]:
        by_document = OrderedDict()
        for item in evidence:
            if item.document_id not in by_document:
                by_document[item.document_id] = {
                    "document_id": item.document_id,
                    "source_id": item.source_id,
                    "source_type": item.source_type,
                    "title": item.title,
                    "url": item.url,
                    "path": item.path,
                    "chunk_ids": [],
                }
            by_document[item.document_id]["chunk_ids"].append(item.chunk_id)
        return list(by_document.values())

    async def _try_synthesize_wiki_page(
        self,
        *,
        topic: str,
        evidence: list[ContextSearchResult],
        citations: list[dict],
        backlinks: list[dict],
        fallback_title: str,
        fallback_sections: list[dict],
        fallback_markdown: str,
    ) -> dict | None:
        if self.llm_synthesizer is None:
            return None

        payload = {
            "topic": topic,
            "evidence": self._build_synthesis_evidence(evidence, citations),
            "citations": citations,
            "backlinks": backlinks,
            "fallback_title": fallback_title,
            "fallback_sections": fallback_sections,
            "fallback_markdown": fallback_markdown,
            "instructions": (
                "Generate a natural wiki page using only the supplied evidence. "
                "Keep citation markers exactly as provided, such as [C1]. "
                "Do not add facts that are not supported by the evidence."
            ),
        }

        try:
            raw = await self._call_synthesizer(payload)
            return self._normalize_synthesized_page(raw, citations)
        except Exception:
            return None

    async def _call_synthesizer(self, payload: dict):
        if hasattr(self.llm_synthesizer, "synthesize_wiki_page"):
            result = self.llm_synthesizer.synthesize_wiki_page(**payload)
        elif callable(self.llm_synthesizer):
            result = self.llm_synthesizer(**payload)
        else:
            return None

        if inspect.isawaitable(result):
            return await result
        return result

    @staticmethod
    def _build_synthesis_evidence(
        evidence: list[ContextSearchResult],
        citations: list[dict],
    ) -> list[dict]:
        synthesis_evidence = []
        for item, citation in zip(evidence, citations, strict=True):
            synthesis_evidence.append(
                {
                    "citation_marker": citation["marker"],
                    "chunk_id": item.chunk_id,
                    "document_id": item.document_id,
                    "source_id": item.source_id,
                    "source_type": item.source_type,
                    "title": item.title,
                    "url": item.url,
                    "path": item.path,
                    "line_start": item.line_start,
                    "line_end": item.line_end,
                    "version_id": item.version_id,
                    "score": item.score,
                    "text": item.text or item.preview,
                }
            )
        return synthesis_evidence

    @staticmethod
    def _normalize_synthesized_page(raw, citations: list[dict]) -> dict | None:
        if not isinstance(raw, dict):
            return None

        title = WikiGenerationService._clean_single_line(raw.get("title"))
        markdown = raw.get("markdown")
        sections = raw.get("sections")
        if not title or not isinstance(markdown, str) or not markdown.strip():
            return None
        if not WikiGenerationService._has_valid_citation_markers(
            markdown,
            citations,
            require_all=True,
        ):
            return None
        if not WikiGenerationService._all_substantive_sentences_cited(
            markdown,
            citations,
        ):
            return None
        normalized_sections = WikiGenerationService._normalize_synthesized_sections(
            sections,
            citations,
        )
        if normalized_sections is None:
            return None
        return {
            "title": title,
            "markdown": markdown.rstrip() + "\n",
            "sections": normalized_sections,
        }

    @staticmethod
    def _normalize_synthesized_sections(
        sections,
        citations: list[dict],
    ) -> list[dict] | None:
        if not isinstance(sections, list) or not sections:
            return None

        normalized_sections = []
        known_markers = {citation["marker"] for citation in citations}
        for section in sections:
            if not isinstance(section, dict):
                return None
            heading = WikiGenerationService._clean_single_line(section.get("heading"))
            content = section.get("content")
            markers = section.get("citation_markers", [])
            if not heading or not isinstance(content, str) or not content.strip():
                return None
            if not isinstance(markers, list):
                return None
            if any(not isinstance(marker, str) for marker in markers):
                return None
            if any(marker not in known_markers for marker in markers):
                return None
            content_markers = WikiGenerationService._citation_markers_in_text(content)
            if (
                not content_markers
                or not content_markers.issubset(known_markers)
                or set(markers) != content_markers
                or not WikiGenerationService._all_substantive_sentences_cited(
                    content,
                    citations,
                )
            ):
                return None
            normalized_sections.append(
                {
                    "heading": heading,
                    "content": content.strip(),
                    "citation_markers": markers,
                }
            )
        return normalized_sections

    @staticmethod
    def _citation_markers_in_text(text: str) -> set[str]:
        return {f"C{match}" for match in _CITATION_MARKER_PATTERN.findall(text)}

    @staticmethod
    def _has_valid_citation_markers(
        text: str,
        citations: list[dict],
        *,
        require_all: bool = False,
    ) -> bool:
        known_markers = {citation["marker"] for citation in citations}
        found_markers = WikiGenerationService._citation_markers_in_text(text)
        if not found_markers or not found_markers.issubset(known_markers):
            return False
        return not require_all or known_markers.issubset(found_markers)

    @staticmethod
    def _all_substantive_sentences_cited(text: str, citations: list[dict]) -> bool:
        known_markers = {citation["marker"] for citation in citations}
        for sentence in WikiGenerationService._substantive_sentences(text):
            found_markers = WikiGenerationService._citation_markers_in_text(sentence)
            if not found_markers or not found_markers.issubset(known_markers):
                return False
        return True

    @staticmethod
    def _substantive_sentences(text: str) -> list[str]:
        sentences = []
        in_fence = False
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if line.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence or not line or line.startswith("#"):
                continue
            line = re.sub(r"^[-*]\s+", "", line)
            line = re.sub(r"^\d+[.)]\s+", "", line)
            line = WikiGenerationService._protect_sentence_abbreviations(line)
            line = re.sub(r"([.!?])\s+((?:\[C\d+\]\s*)+)", r" \2\1 ", line)
            line = re.sub(r"((?:\[C\d+\]\s*)+)\s+(?=\S)", r"\1. ", line)
            for sentence in _SENTENCE_BOUNDARY_PATTERN.split(line):
                cleaned = WikiGenerationService._restore_sentence_abbreviations(
                    sentence.strip()
                )
                if re.search(r"[A-Za-z0-9가-힣]", cleaned):
                    sentences.append(cleaned)
        return sentences

    @staticmethod
    def _protect_sentence_abbreviations(text: str) -> str:
        protected = text
        protected = _INITIALISM_PATTERN.sub(
            lambda match: match.group(0).replace(".", _ABBREVIATION_DOT_PLACEHOLDER),
            protected,
        )
        protected = _DOTFILE_TOKEN_PATTERN.sub(
            WikiGenerationService._protect_dotfile_token_match,
            protected,
        )
        protected = _DOTTED_TOKEN_PATTERN.sub(
            WikiGenerationService._protect_dotted_token_match,
            protected,
        )
        for abbreviation in _SENTENCE_ABBREVIATIONS:
            protected = re.sub(
                re.escape(abbreviation),
                abbreviation.replace(".", _ABBREVIATION_DOT_PLACEHOLDER),
                protected,
                flags=re.IGNORECASE,
            )
        return protected

    @staticmethod
    def _protect_dotfile_token_match(match: re.Match) -> str:
        token = match.group(0)
        parts = token.split(".")
        protected_parts = [parts[0]]
        index = 1
        while index < len(parts):
            suffix = parts[index].lower()
            if suffix not in _TECHNICAL_DOTTED_SUFFIXES:
                break
            protected_parts.append(parts[index])
            index += 1
        if len(protected_parts) > 1:
            protected = ".".join(protected_parts).replace(
                ".", _ABBREVIATION_DOT_PLACEHOLDER
            )
            return protected + "".join(f".{part}" for part in parts[index:])
        return token

    @staticmethod
    def _protect_dotted_token_match(match: re.Match) -> str:
        token = match.group(0)
        parts = token.split(".")
        protected_parts = [parts[0]]
        index = 1
        while index < len(parts):
            suffix = parts[index].lower()
            if suffix not in _TECHNICAL_DOTTED_SUFFIXES:
                break
            protected_parts.append(parts[index])
            index += 1
        if len(protected_parts) > 1:
            protected = ".".join(protected_parts).replace(
                ".", _ABBREVIATION_DOT_PLACEHOLDER
            )
            return protected + "".join(f".{part}" for part in parts[index:])
        return token

    @staticmethod
    def _restore_sentence_abbreviations(text: str) -> str:
        return text.replace(_ABBREVIATION_DOT_PLACEHOLDER, ".")

    @staticmethod
    def _clean_single_line(text) -> str:
        return " ".join(str(text or "").split())

    @staticmethod
    def _build_sections(
        topic: str,
        evidence: list[ContextSearchResult],
        citations: list[dict],
        backlinks: list[dict],
    ) -> list[dict]:
        overview_lines = [
            f"- {WikiGenerationService._clean_snippet(item.text or item.preview)} [{citations[index]['marker']}]"
            for index, item in enumerate(evidence)
            if item.text or item.preview
        ]
        evidence_lines = [
            f"- {link['title']} ({link['source_type'] or link['source_id']})"
            for link in backlinks
        ]
        return [
            {
                "heading": "Overview",
                "content": "\n".join(overview_lines)
                or f"No narrative evidence was available for {topic}.",
                "citation_markers": [citation["marker"] for citation in citations],
            },
            {
                "heading": "Related Sources",
                "content": "\n".join(evidence_lines),
                "citation_markers": [],
            },
        ]

    @staticmethod
    def _render_markdown(
        topic: str,
        sections: list[dict],
        citations: list[dict],
        backlinks: list[dict],
    ) -> str:
        lines = [
            f"# {topic} Wiki",
            "",
            "Generated from active ContextWiki chunks with citation markers.",
            "",
        ]

        for section in sections:
            lines.extend(
                [
                    f"## {section['heading']}",
                    "",
                    section["content"],
                    "",
                ]
            )

        lines.extend(["## Backlinks", ""])
        for backlink in backlinks:
            target = backlink["url"] or backlink["path"] or backlink["document_id"]
            lines.append(f"- {backlink['title']} -> {target}")
        lines.append("")

        lines.extend(["## Citations", ""])
        for citation in citations:
            location = WikiGenerationService._format_location(citation)
            lines.append(
                f"- [{citation['marker']}] {citation['title']} "
                f"({citation['source_id']}) {location} "
                f"`{citation['chunk_id']}`"
            )

        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _clean_snippet(text: str, limit: int = 320) -> str:
        cleaned = " ".join((text or "").split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[:limit].rstrip() + "..."

    @staticmethod
    def _format_location(citation: dict) -> str:
        path_or_url = citation.get("path") or citation.get("url") or citation.get("document_id", "")
        line_start = citation.get("line_start")
        line_end = citation.get("line_end")
        if line_start is not None and line_end is not None:
            return f"{path_or_url}:{line_start}-{line_end}"
        if line_start is not None:
            return f"{path_or_url}:{line_start}"
        return path_or_url

    @staticmethod
    def _insufficient_response(topic: str, message: str) -> dict:
        normalized_topic = WikiGenerationService._normalize_topic(topic)
        return {
            "topic": normalized_topic,
            "status": "insufficient_evidence",
            "title": f"{normalized_topic} Wiki" if normalized_topic else "",
            "markdown": message,
            "sections": [],
            "citations": [],
            "backlinks": [],
            "used_chunks": [],
            "message": message,
        }
