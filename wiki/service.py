from collections import OrderedDict

from core.models import ContextSearchResult


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
    ):
        self.context_search = context_search
        self.default_top_k = default_top_k
        self.max_top_k = max_top_k
        self.min_score = min_score
        self.min_results = min_results

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

        return {
            "topic": normalized_topic,
            "status": "generated",
            "title": f"{normalized_topic} Wiki",
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
