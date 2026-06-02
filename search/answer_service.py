import math
import re

from core.models import ContextSearchResult
from search.query_terms import (
    BROAD_TOPIC_TERMS,
    DOCUMENT_INTENT_TERMS,
    STRONG_ANCHOR_TERMS,
    append_query_term_group,
    query_term_groups,
    query_terms,
    retrieval_query_variants,
    split_attached_latin_korean_token,
)
DOCUMENT_LIKE_PATH_RE = re.compile(
    r"(^|[/:])(readme(\.|/|$)|docs?/|documentation/)|\.(md|mdx|markdown|rst|txt)(\s|$)",
    re.IGNORECASE,
)


class CitationAnswerService:
    """Ground answers in returned context and emit explicit citations."""

    def __init__(self, context_search, min_score: float = 0.35, min_results: int = 1):
        self.context_search = context_search
        self.min_score = min_score
        self.min_results = min_results

    async def answer_with_citations(
        self,
        question: str,
        filters: dict | None = None,
        top_k: int = 5,
    ) -> dict:
        search_result = await self.context_search.search_context(question, filters=filters, top_k=top_k)
        results = [self._as_result(item) for item in search_result.get("results", [])]
        search_debug = search_result.get("debug", {})
        query_term_groups = self._effective_query_term_groups(question, search_debug)
        query_terms = {term for group in query_term_groups for term in group}
        relaxed_match = bool(search_debug.get("rewritten_queries"))
        evidence = [
            item
            for item in results
            if item.score >= self.min_score
            and self._is_relevant_to_query(
                item,
                query_terms,
                query_term_groups,
                relaxed_match=relaxed_match,
            )
        ]
        citations = [
            {
                "chunk_id": item.chunk_id,
                "title": item.title,
                "url": item.url,
                "path": item.path,
                "line_start": item.line_start,
                "line_end": item.line_end,
                "version_id": item.version_id,
            }
            for item in evidence
        ]
        debug_payload = self._build_debug_payload(
            question,
            results,
            evidence,
            query_term_groups,
            retrieval_queries=search_debug.get("retrieval_queries"),
            rewritten_queries=search_debug.get("rewritten_queries"),
        )

        if len(evidence) < self.min_results:
            return {
                "question": question,
                "answer": "Insufficient evidence in indexed context to answer this question.",
                "answer_mode": "contextwiki_debug",
                "evidence_status": "insufficient",
                "citations": [],
                "used_chunks": [],
                "debug": debug_payload,
                "debug_markdown": self._render_debug_markdown(
                    question,
                    query_term_groups,
                    results,
                    evidence,
                    "insufficient",
                    retrieval_queries=search_debug.get("retrieval_queries"),
                    rewritten_queries=search_debug.get("rewritten_queries"),
                ),
            }

        answer = self._render_structured_answer(question, evidence)

        return {
            "question": question,
            "answer": answer,
            "answer_mode": "contextwiki_debug",
            "evidence_status": "grounded",
            "citations": citations,
            "used_chunks": [item.chunk_id for item in evidence],
            "debug": debug_payload,
            "debug_markdown": self._render_debug_markdown(
                question,
                query_term_groups,
                results,
                evidence,
                "grounded",
                retrieval_queries=search_debug.get("retrieval_queries"),
                rewritten_queries=search_debug.get("rewritten_queries"),
            ),
        }

    @staticmethod
    def _as_result(item) -> ContextSearchResult:
        if isinstance(item, ContextSearchResult):
            return item
        return ContextSearchResult(**item)

    @classmethod
    def _query_terms(cls, question: str) -> set[str]:
        return query_terms(question)

    @classmethod
    def _query_term_groups(cls, question: str) -> list[set[str]]:
        return query_term_groups(question)

    @classmethod
    def _effective_query_term_groups(
        cls,
        question: str,
        search_debug: dict | None = None,
    ) -> list[set[str]]:
        effective = (search_debug or {}).get("effective_term_groups") or []
        if effective:
            return [set(group) for group in effective]
        return cls._query_term_groups(question)

    @classmethod
    def _append_query_term_group(cls, raw_term: str, groups: list[set[str]], seen: set[tuple[str, ...]]):
        append_query_term_group(raw_term, groups, seen)

    @staticmethod
    def _split_attached_latin_korean_token(raw_token: str) -> list[str]:
        return split_attached_latin_korean_token(raw_token)

    @staticmethod
    def _is_relevant_to_query(
        item: ContextSearchResult,
        query_terms: set[str],
        query_term_groups: list[set[str]] | None = None,
        relaxed_match: bool = False,
    ) -> bool:
        if not query_terms:
            return True
        haystack = " ".join(
            [
                item.title or "",
                item.document_id or "",
                item.url or "",
                item.path or "",
                item.preview or "",
                item.text or "",
            ]
        ).lower()
        metadata_haystack = " ".join(
            [
                item.title or "",
                item.document_id or "",
                item.url or "",
                item.path or "",
            ]
        ).lower()
        is_github = item.source_id == "source_github" or item.source_type == "github"
        is_document_like = not is_github or bool(DOCUMENT_LIKE_PATH_RE.search(metadata_haystack))
        groups = query_term_groups or [{term} for term in query_terms]
        matched_groups = [
            term_group
            for term_group in groups
            if (
                term_group.intersection(DOCUMENT_INTENT_TERMS)
                and is_github
                and is_document_like
            )
            or (
                not (term_group.intersection(DOCUMENT_INTENT_TERMS) and is_github)
                and (
                    any(term in haystack for term in term_group)
                    or (term_group.intersection(DOCUMENT_INTENT_TERMS) and is_document_like)
                )
            )
        ]
        strong_anchors = query_terms.intersection(STRONG_ANCHOR_TERMS)
        if strong_anchors:
            anchor_matched = any(term in metadata_haystack for term in strong_anchors)
            doc_intent_groups = [
                term_group
                for term_group in groups
                if term_group.intersection(DOCUMENT_INTENT_TERMS)
            ]
            topical_groups = [
                term_group
                for term_group in groups
                if not term_group.intersection(STRONG_ANCHOR_TERMS)
                and not term_group.intersection(DOCUMENT_INTENT_TERMS)
                and not term_group.intersection(BROAD_TOPIC_TERMS)
            ]
            return (
                anchor_matched
                and all(term_group in matched_groups for term_group in doc_intent_groups)
                and all(term_group in matched_groups for term_group in topical_groups)
            )
        required_groups = [
            term_group
            for term_group in groups
            if not term_group.intersection(BROAD_TOPIC_TERMS)
        ] or groups
        matched_required_groups = [
            term_group for term_group in required_groups if term_group in matched_groups
        ]
        required_matches = (
            len(required_groups)
            if len(required_groups) <= 3
            else math.ceil(len(required_groups) / 2)
        )
        if relaxed_match and len(required_groups) >= 3:
            required_matches = max(2, math.ceil(len(required_groups) / 2))
        if relaxed_match and len(groups) >= 3:
            relaxed_required_matches = max(2, math.ceil(len(groups) / 2))
            return len(matched_groups) >= relaxed_required_matches
        return len(matched_required_groups) >= required_matches

    @staticmethod
    def _render_structured_answer(question: str, evidence: list[ContextSearchResult]) -> str:
        if not evidence:
            return "Insufficient evidence in indexed context to answer this question."

        lines = [
            "## Summary",
            "",
            f"- Indexed evidence matched this request for `{question}`.",
            f"- Grounded chunks used: {len(evidence)}.",
            "",
            "## Best Matches",
            "",
        ]
        for index, item in enumerate(evidence[:3], 1):
            location = item.path or item.url or item.document_id or "unknown location"
            lines.append(
                f"- [C{index}] **{item.title or item.document_id or item.chunk_id}** "
                f"(`{location}`): {CitationAnswerService._snippet(item)}"
            )
        if len(evidence) > 3:
            lines.extend(
                [
                    "",
                    "## Notes",
                    "",
                    f"- Additional grounded chunks exist beyond the top 3 shown here: {len(evidence) - 3}.",
                ]
            )
        return "\n".join(lines)

    @staticmethod
    def _snippet(item: ContextSearchResult, limit: int = 180) -> str:
        text = " ".join((item.preview or item.text or "").split())
        if not text:
            return "No preview text available."
        return text if len(text) <= limit else text[:limit].rstrip() + "..."

    @classmethod
    def _build_debug_payload(
        cls,
        question: str,
        results: list[ContextSearchResult],
        evidence: list[ContextSearchResult],
        query_term_groups: list[set[str]],
        *,
        retrieval_queries: list[str] | None = None,
        rewritten_queries: list[str] | None = None,
    ) -> dict:
        variants = retrieval_queries or retrieval_query_variants(question, query_term_groups)
        return {
            "question": question,
            "retrieval_queries": variants,
            "rewritten_queries": rewritten_queries or [],
            "normalized_term_groups": [sorted(group) for group in query_term_groups],
            "retrieved_count": len(results),
            "grounded_count": len(evidence),
            "selected_chunks": [
                cls._debug_chunk_payload(index, item, query_term_groups)
                for index, item in enumerate(evidence, 1)
            ],
            "retrieved_chunks": [
                cls._debug_chunk_payload(index, item, query_term_groups)
                for index, item in enumerate(results, 1)
            ],
        }

    @classmethod
    def _debug_chunk_payload(
        cls,
        rank: int,
        item: ContextSearchResult,
        query_term_groups: list[set[str]],
    ) -> dict:
        return {
            "rank": rank,
            "chunk_id": item.chunk_id,
            "score": round(float(item.score or 0.0), 4),
            "title": item.title,
            "path": item.path,
            "url": item.url,
            "matched_terms": cls._matched_terms(item, query_term_groups),
            "preview": cls._snippet(item, limit=220),
        }

    @classmethod
    def _matched_terms(
        cls,
        item: ContextSearchResult,
        query_term_groups: list[set[str]],
    ) -> list[str]:
        haystack = " ".join(
            [
                item.title or "",
                item.document_id or "",
                item.url or "",
                item.path or "",
                item.preview or "",
                item.text or "",
            ]
        ).lower()
        matched = []
        for group in query_term_groups:
            for term in sorted(group):
                if term in haystack:
                    matched.append(term)
                    break
        return matched

    @classmethod
    def _render_debug_markdown(
        cls,
        question: str,
        query_term_groups: list[set[str]],
        results: list[ContextSearchResult],
        evidence: list[ContextSearchResult],
        evidence_status: str,
        *,
        retrieval_queries: list[str] | None = None,
        rewritten_queries: list[str] | None = None,
    ) -> str:
        lines = [
            "## Query",
            "",
            f"- original: `{question}`",
        ]
        retrieval_queries = retrieval_queries or retrieval_query_variants(question, query_term_groups)
        if retrieval_queries:
            lines.append(f"- retrieval queries: `{retrieval_queries[0]}`")
            for variant in retrieval_queries[1:]:
                lines.append(f"  - expanded: `{variant}`")
        if rewritten_queries:
            lines.append(f"- rewritten queries used: `{rewritten_queries[0]}`")
            for variant in rewritten_queries[1:]:
                lines.append(f"  - rewrite: `{variant}`")
        lines.extend(
            [
                f"- evidence status: `{evidence_status}`",
                "",
                "## Normalized Terms",
                "",
            ]
        )
        if query_term_groups:
            for group in query_term_groups:
                lines.append(f"- {', '.join(sorted(group))}")
        else:
            lines.append("- none")

        lines.extend(
            [
                "",
                "## Retrieval Summary",
                "",
                f"- retrieved chunks: {len(results)}",
                f"- grounded chunks used: {len(evidence)}",
                "",
                "## Selected Chunks",
                "",
            ]
        )
        if evidence:
            for index, item in enumerate(evidence, 1):
                matched = ", ".join(cls._matched_terms(item, query_term_groups)) or "none"
                lines.extend(
                    [
                        f"{index}. [C{index}] **{item.title or item.chunk_id}**",
                        f"   - score: {float(item.score or 0.0):.3f}",
                        f"   - path: `{item.path or item.url or item.document_id or 'unknown'}`",
                        f"   - matched terms: {matched}",
                        f"   - preview: {cls._snippet(item, limit=220)}",
                    ]
                )
        else:
            lines.append("- none")

        if results:
            lines.extend(["", "## Retrieved Chunks", ""])
            for index, item in enumerate(results, 1):
                matched = ", ".join(cls._matched_terms(item, query_term_groups)) or "none"
                lines.extend(
                    [
                        f"{index}. **{item.title or item.chunk_id}**",
                        f"   - score: {float(item.score or 0.0):.3f}",
                        f"   - matched terms: {matched}",
                    ]
                )

        lines.extend(["", "## Structured Answer", "", cls._render_structured_answer(question, evidence)])
        return "\n".join(lines)
