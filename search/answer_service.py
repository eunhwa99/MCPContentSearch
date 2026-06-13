import math
import re
from urllib.parse import urlparse

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
from search.ranking import CANONICAL_SOURCE_ID_BY_TYPE, source_type_terms_for_group
PROMPT_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?P<prefix>(?:access[-_]?token|api[-_]?key|apikey|auth|authorization|"
    r"client[-_]?secret|cookie|credential|jwt|key|pass|password|passwd|"
    r"private[-_]?key|pwd|secret|session|token)\s*[:=]\s*['\"]?)"
    r"(?P<secret>[^'\"\s,;}]+)(?P<suffix>['\"]?)",
    re.IGNORECASE,
)
PROMPT_QUERY_SECRET_RE = re.compile(
    r"(?P<prefix>[?&](?:access[-_]?token|api[-_]?key|apikey|auth|authorization|"
    r"client[-_]?secret|credential|key|password|secret|session|sig|signature|"
    r"token)=)(?P<secret>[^&#\s]+)",
    re.IGNORECASE,
)
PROMPT_SPACE_SECRET_RE = re.compile(
    r"(?P<prefix>\b(?:access[-_]?token|api[-_]?key|apikey|auth|authorization|"
    r"client[-_]?secret|cookie|credential|jwt|key|pass|password|passwd|"
    r"private[-_]?key|pwd|secret|session|token)\b\s+)"
    r"(?P<secret>(?:gh[pousr]_[^\s,;}]+|github_pat_[^\s,;}]+|"
    r"xox[baprs]-[^\s,;}]+|sk-(?:proj-)?[^\s,;}]+|AIza[^\s,;}]+|"
    r"eyJ[^\s,;}]+|[A-Za-z0-9_-]{16,}))",
    re.IGNORECASE,
)
SECRET_LIKE_RE = re.compile(
    r"(gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|"
    r"xox[baprs]-[A-Za-z0-9-]+|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
SECRET_VALUE_SHAPE_RE = re.compile(
    r"\b(?=[A-Za-z0-9_-]{16,}\b)(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9]{2,}(?:[-_][A-Za-z0-9]{2,}){2,}\b"
)
DEBUG_SECRET_VALUE_SHAPE_RE = re.compile(
    r"\b(?=[A-Za-z0-9_-]{16,}\b)(?:"
    r"[A-Za-z0-9_-]*(?:secret|token|passwd|password|apikey|api-key|access-token|credential)[A-Za-z0-9_-]*"
    r"|[A-Za-z0-9_-]*\d[A-Za-z0-9_-]*"
    r")\b",
    re.IGNORECASE,
)
DEBUG_HTTP_URL_RE = re.compile(r"https?://[^\s`]+", re.IGNORECASE)
DEBUG_FILE_URL_RE = re.compile(r"file://[^\s`]+", re.IGNORECASE)
DEBUG_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9])/(?:[^\s`]+/)+[^\s`]+")
DEBUG_LOCAL_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])/(?:Users|home|private|tmp|var)/(?:[^\s`]+/)*[^\s`]+"
)
DEBUG_HOME_PATH_RE = re.compile(r"~/(?:[^\s`]+/)*[^\s`]+")
DEBUG_HOME_BACKSLASH_PATH_RE = re.compile(r"~\\(?:[^\s`\\]+\\)*[^\s`\\]+")
DEBUG_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:[\\/](?:[^\s`\\/]+[\\/])*[^\s`\\/]+")
DEBUG_URL_FRAGMENT_TOKEN_RE = re.compile(r"\b[A-Za-z0-9.-]+\.[A-Za-z]{2,}/[^\s`]+")
DEBUG_TLD_FRAGMENT_RE = re.compile(r"\b(?:com|net|org|io|dev|app|ai|co)/[^\s`]+")
PROBLEM_HINT_TERMS = {
    "problem",
    "problems",
    "question",
    "questions",
    "solution",
    "solutions",
    "문제",
    "풀이",
}
DOCUMENT_LIKE_PATH_RE = re.compile(
    r"(^|[/:])(readme(\.|/|$)|docs?/|documentation/)|\.(md|mdx|markdown|rst|txt)(\s|$)",
    re.IGNORECASE,
)


class CitationAnswerService:
    """Ground answers in returned context and emit explicit citations."""

    _METADATA_GROUNDING_VECTOR_FLOOR = 0.1

    def __init__(self, context_search, min_score: float = 0.35, min_results: int = 1):
        self.context_search = context_search
        self.min_score = min_score
        self.min_results = min_results

    async def answer_with_citations(
        self,
        question: str,
        filters: dict | None = None,
        top_k: int = 5,
        *,
        include_debug: bool = False,
    ) -> dict:
        if hasattr(self.context_search, "search_context_for_answer"):
            search_result, grounding_state = await self.context_search.search_context_for_answer(
                question,
                filters=filters,
                top_k=top_k,
                include_debug=include_debug,
            )
        else:
            search_result = await self.context_search.search_context(
                question,
                filters=filters,
                top_k=top_k,
                include_debug=include_debug,
                include_internal_metadata=True,
            )
            grounding_state = search_result.get("_grounding", {})
        results = [self._as_result(item) for item in search_result.get("results", [])]
        search_debug = search_result.get("_debug") or search_result.get("debug", {})
        query_term_groups = self._effective_query_term_groups(question, grounding_state, search_debug)
        required_term_groups = self._required_query_term_groups(question, grounding_state, search_debug)
        preserve_original_constraints = self._should_preserve_original_constraints(
            grounding_state=grounding_state,
            search_debug=search_debug,
        )
        display_term_groups = self._display_query_term_groups(
            query_term_groups,
            search_debug=search_debug,
        )
        query_terms = {term for group in query_term_groups for term in group}
        relaxed_match = bool(search_debug.get("rewritten_queries"))
        evidence = [
            item
            for item in results
            if self._grounding_score(item) >= self.min_score
            and self._is_relevant_to_query(
                item,
                query_terms,
                query_term_groups,
                required_term_groups=required_term_groups,
                preserve_original_constraints=preserve_original_constraints,
                relaxed_match=relaxed_match,
            )
        ]
        citations = [
            {
                "chunk_id": item.chunk_id,
                "title": self._redact_public_answer_text(item.title),
                "url": self._safe_public_location(item.url),
                "path": self._safe_public_location(item.path),
                "line_start": item.line_start,
                "line_end": item.line_end,
                "version_id": item.version_id,
            }
            for item in evidence
        ]
        debug_payload = (
            self._build_debug_payload(
                question,
                results,
                evidence,
                display_term_groups,
                search_debug=search_debug,
                retrieval_queries=search_debug.get("retrieval_queries"),
                rewritten_queries=search_debug.get("rewritten_queries"),
            )
            if include_debug
            else None
        )

        if len(evidence) < self.min_results:
            payload = {
                "question": self._redact_public_answer_text(question),
                "answer": "Insufficient evidence in indexed context to answer this question.",
                "evidence_status": "insufficient",
                "citations": [],
                "used_chunks": [],
            }
            if include_debug:
                payload.update(
                    {
                        "answer_mode": "contextwiki_debug",
                        "debug": debug_payload,
                        "debug_markdown": self._render_debug_markdown(
                            question,
                            display_term_groups,
                            results,
                            evidence,
                            "insufficient",
                            search_debug=search_debug,
                            retrieval_queries=search_debug.get("retrieval_queries"),
                            rewritten_queries=search_debug.get("rewritten_queries"),
                        ),
                    }
                )
            return payload

        answer = self._render_structured_answer(question, evidence)

        payload = {
            "question": self._redact_public_answer_text(question),
            "answer": answer,
            "evidence_status": "grounded",
            "citations": citations,
            "used_chunks": [item.chunk_id for item in evidence],
        }
        if include_debug:
            payload.update(
                {
                    "answer_mode": "contextwiki_debug",
                    "debug": debug_payload,
                    "debug_markdown": self._render_debug_markdown(
                        question,
                        display_term_groups,
                        results,
                        evidence,
                        "grounded",
                        search_debug=search_debug,
                        retrieval_queries=search_debug.get("retrieval_queries"),
                        rewritten_queries=search_debug.get("rewritten_queries"),
                    ),
                }
            )
        return payload

    @staticmethod
    def _as_result(item) -> ContextSearchResult:
        if isinstance(item, ContextSearchResult):
            return item
        return ContextSearchResult(**item)

    @staticmethod
    def _grounding_score(item: ContextSearchResult) -> float:
        vector_score = float(getattr(item, "vector_score", 0.0) or 0.0)
        if (
            int(getattr(item, "metadata_priority", 0) or 0) > 0
            and vector_score >= CitationAnswerService._METADATA_GROUNDING_VECTOR_FLOOR
        ):
            return float(item.score or 0.0)
        return vector_score if vector_score > 0 else float(item.score or 0.0)

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
        grounding_state: dict | None = None,
        search_debug: dict | None = None,
    ) -> list[set[str]]:
        effective = (
            (grounding_state or {}).get("effective_term_groups")
            or (search_debug or {}).get("effective_term_groups")
            or []
        )
        if effective:
            return [set(group) for group in effective]
        return cls._query_term_groups(question)

    @classmethod
    def _required_query_term_groups(
        cls,
        question: str,
        grounding_state: dict | None = None,
        search_debug: dict | None = None,
    ) -> list[set[str]]:
        original = (
            (grounding_state or {}).get("original_term_groups")
            or (search_debug or {}).get("original_term_groups")
            or []
        )
        if original:
            return [set(group) for group in original]
        effective = (
            (grounding_state or {}).get("effective_term_groups")
            or (search_debug or {}).get("effective_term_groups")
            or []
        )
        if effective:
            return [set(group) for group in effective]
        return cls._query_term_groups(question)

    @classmethod
    def _display_query_term_groups(
        cls,
        query_term_groups: list[set[str]],
        *,
        search_debug: dict | None = None,
    ) -> list[set[str]]:
        display_groups = (search_debug or {}).get("effective_term_groups") or []
        if display_groups:
            return [set(group) for group in display_groups]
        return query_term_groups

    @classmethod
    def _should_preserve_original_constraints(
        cls,
        *,
        grounding_state: dict | None = None,
        search_debug: dict | None = None,
    ) -> bool:
        original_groups = cls._required_query_term_groups(
            "",
            grounding_state=grounding_state,
            search_debug=search_debug,
        )
        return any(group.intersection(STRONG_ANCHOR_TERMS) for group in original_groups)

    @classmethod
    def _append_query_term_group(cls, raw_term: str, groups: list[set[str]], seen: set[tuple[str, ...]]):
        append_query_term_group(raw_term, groups, seen)

    @staticmethod
    def _split_attached_latin_korean_token(raw_token: str) -> list[str]:
        return split_attached_latin_korean_token(raw_token)

    @classmethod
    def _is_relevant_to_query(
        cls,
        item: ContextSearchResult,
        query_terms: set[str],
        query_term_groups: list[set[str]] | None = None,
        required_term_groups: list[set[str]] | None = None,
        preserve_original_constraints: bool = False,
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
        required_groups_for_match = required_term_groups or groups
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
                    or cls._matches_source_type_term_group(item, term_group)
                )
            )
        ]
        strong_anchor_terms = {
            term
            for group in required_groups_for_match
            for term in group
            if term in STRONG_ANCHOR_TERMS
        }
        strong_anchors = strong_anchor_terms or query_terms.intersection(STRONG_ANCHOR_TERMS)
        if strong_anchors and (preserve_original_constraints or not relaxed_match):
            anchor_matched = any(term in metadata_haystack for term in strong_anchors)
            doc_intent_groups = [
                term_group
                for term_group in required_groups_for_match
                if term_group.intersection(DOCUMENT_INTENT_TERMS)
            ]
            topical_groups = [
                term_group
                for term_group in required_groups_for_match
                if not term_group.intersection(STRONG_ANCHOR_TERMS)
                and not term_group.intersection(DOCUMENT_INTENT_TERMS)
                and not term_group.intersection(BROAD_TOPIC_TERMS)
            ]
            broad_topical_groups = [
                term_group
                for term_group in required_groups_for_match
                if not term_group.intersection(STRONG_ANCHOR_TERMS)
                and not term_group.intersection(DOCUMENT_INTENT_TERMS)
                and term_group.intersection(PROBLEM_HINT_TERMS)
            ]
            return (
                anchor_matched
                and all(term_group in matched_groups for term_group in doc_intent_groups)
                and all(term_group in matched_groups for term_group in topical_groups)
                and (
                    not broad_topical_groups
                    or any(term_group in matched_groups for term_group in broad_topical_groups)
                )
            )
        required_groups = [
            term_group
            for term_group in required_groups_for_match
            if not term_group.intersection(BROAD_TOPIC_TERMS)
            and not (
                relaxed_match
                and not preserve_original_constraints
                and term_group.intersection(STRONG_ANCHOR_TERMS)
            )
        ] or required_groups_for_match
        matched_required_groups = [
            term_group for term_group in required_groups if term_group in matched_groups
        ]
        required_matches = (
            len(required_groups)
            if len(required_groups) <= 3
            else math.ceil(len(required_groups) / 2)
        )
        if relaxed_match and required_groups:
            required_matches = max(1, math.ceil(len(required_groups) / 2))
        return len(matched_required_groups) >= required_matches

    @staticmethod
    def _matches_source_type_term_group(
        item: ContextSearchResult,
        term_group: set[str],
    ) -> bool:
        source_type_terms = source_type_terms_for_group(term_group)
        if not source_type_terms:
            return False
        item_source_type = (getattr(item.source_type, "value", item.source_type) or "").lower()
        item_source_id = (item.source_id or "").lower()
        if item_source_type in source_type_terms:
            return True
        return any(
            item_source_id == canonical_source_id
            for source_type, canonical_source_id in CANONICAL_SOURCE_ID_BY_TYPE.items()
            if source_type in source_type_terms
        )

    @staticmethod
    def _render_structured_answer(question: str, evidence: list[ContextSearchResult]) -> str:
        if not evidence:
            return "Insufficient evidence in indexed context to answer this question."

        lines = [
            "## Summary",
            "",
            f"- Indexed evidence matched this request for `{CitationAnswerService._redact_public_answer_text(question)}`.",
            f"- Grounded chunks used: {len(evidence)}.",
            "",
            "## Best Matches",
            "",
        ]
        for index, item in enumerate(evidence[:3], 1):
            location = CitationAnswerService._safe_debug_location(
                item.path or item.url or item.document_id or "unknown location"
            )
            lines.append(
                f"- [C{index}] **{CitationAnswerService._redact_public_answer_text(item.title or item.document_id or item.chunk_id)}** "
                f"(`{location}`): {CitationAnswerService._redact_public_answer_text(CitationAnswerService._snippet(item))}"
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
        search_debug: dict | None = None,
        retrieval_queries: list[str] | None = None,
        rewritten_queries: list[str] | None = None,
    ) -> dict:
        variants = retrieval_queries or retrieval_query_variants(question, query_term_groups)
        return {
            "question": cls._redact_debug_query_text(question),
            "retrieval_queries": [cls._redact_debug_query_text(variant) for variant in variants],
            "rewritten_queries": [
                cls._redact_debug_query_text(variant)
                for variant in (rewritten_queries or [])
            ],
            "normalized_term_groups": [
                [cls._redact_debug_text(term) for term in sorted(group)]
                for group in query_term_groups
            ],
            "query_rewrite": dict((search_debug or {}).get("query_rewrite", {})),
            "filters": dict((search_debug or {}).get("filters", {})),
            "retrieved_count": len(results),
            "grounded_count": len(evidence),
            "retrieval_selected_results": list((search_debug or {}).get("selected_results", [])),
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
        grounding_score = cls._grounding_score(item)
        search_score = float(item.score or 0.0)
        return {
            "rank": rank,
            "chunk_id": item.chunk_id,
            "score": round(grounding_score, 4),
            "search_score": round(search_score, 4),
            "title": cls._redact_debug_text(item.title),
            "path": cls._safe_debug_location(item.path),
            "url": cls._safe_debug_location(item.url),
            "matched_terms": cls._matched_terms(item, query_term_groups),
            "preview": cls._redact_debug_text(cls._snippet(item, limit=220)),
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
                if term in haystack or cls._matches_source_type_term_group(item, group):
                    matched.append(cls._redact_debug_text(term))
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
        search_debug: dict | None = None,
        retrieval_queries: list[str] | None = None,
        rewritten_queries: list[str] | None = None,
    ) -> str:
        lines = [
            "## Query",
            "",
            f"- original: `{cls._redact_debug_query_text(question)}`",
        ]
        retrieval_queries = retrieval_queries or retrieval_query_variants(question, query_term_groups)
        if retrieval_queries:
            lines.append(f"- retrieval queries: `{cls._redact_debug_query_text(retrieval_queries[0])}`")
            for variant in retrieval_queries[1:]:
                lines.append(f"  - expanded: `{cls._redact_debug_query_text(variant)}`")
        if rewritten_queries:
            lines.append(
                f"- rewritten queries used: `{cls._redact_debug_query_text(rewritten_queries[0])}`"
            )
            for variant in rewritten_queries[1:]:
                lines.append(f"  - rewrite: `{cls._redact_debug_query_text(variant)}`")
        rewrite_reason = str((search_debug or {}).get("query_rewrite", {}).get("reason", "")).strip()
        if rewrite_reason:
            lines.append(f"- rewrite reason: `{cls._redact_debug_text(rewrite_reason)}`")
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
                lines.append(
                    f"- {', '.join(cls._redact_debug_text(term) for term in sorted(group))}"
                )
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
                        f"{index}. [C{index}] **{cls._redact_debug_text(item.title or item.chunk_id)}**",
                        f"   - grounding score: {cls._grounding_score(item):.3f}",
                        f"   - search score: {float(item.score or 0.0):.3f}",
                        f"   - path: `{cls._safe_debug_location(item.path or item.url or item.document_id or 'unknown')}`",
                        f"   - matched terms: {matched}",
                        f"   - preview: {cls._redact_debug_text(cls._snippet(item, limit=220))}",
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
                        f"{index}. **{cls._redact_debug_text(item.title or item.chunk_id)}**",
                        f"   - grounding score: {cls._grounding_score(item):.3f}",
                        f"   - search score: {float(item.score or 0.0):.3f}",
                        f"   - matched terms: {matched}",
                    ]
                )

        lines.extend(["", "## Structured Answer", "", cls._render_structured_answer(question, evidence)])
        return "\n".join(lines)

    @staticmethod
    def _redact_secret_only(value: str) -> str:
        text = str(value or "")
        text = SECRET_LIKE_RE.sub("[REDACTED]", text)
        text = PROMPT_ASSIGNMENT_SECRET_RE.sub(
            lambda match: f"{match.group('prefix')}[REDACTED]{match.group('suffix')}",
            text,
        )
        text = PROMPT_SPACE_SECRET_RE.sub(
            lambda match: f"{match.group('prefix')}[REDACTED]",
            text,
        )
        text = PROMPT_QUERY_SECRET_RE.sub(
            lambda match: f"{match.group('prefix')}[REDACTED]",
            text,
        )
        return SECRET_VALUE_SHAPE_RE.sub("[REDACTED]", text)

    @staticmethod
    def _redact_debug_text(value: str) -> str:
        text = CitationAnswerService._redact_public_answer_text(value)
        text = DEBUG_ABSOLUTE_PATH_RE.sub("redacted", text)
        return DEBUG_SECRET_VALUE_SHAPE_RE.sub("[REDACTED]", text)

    @staticmethod
    def _redact_public_answer_text(value: str) -> str:
        text = str(value or "")
        text = DEBUG_HTTP_URL_RE.sub(
            lambda match: CitationAnswerService._safe_debug_location(match.group(0)),
            text,
        )
        text = DEBUG_FILE_URL_RE.sub("redacted", text)
        text = DEBUG_HOME_PATH_RE.sub("redacted", text)
        text = DEBUG_HOME_BACKSLASH_PATH_RE.sub("redacted", text)
        text = DEBUG_WINDOWS_PATH_RE.sub("redacted", text)
        text = DEBUG_LOCAL_ABSOLUTE_PATH_RE.sub("redacted", text)
        text = DEBUG_URL_FRAGMENT_TOKEN_RE.sub("redacted", text)
        text = DEBUG_TLD_FRAGMENT_RE.sub("redacted", text)
        return CitationAnswerService._redact_secret_only(text)

    @classmethod
    def _redact_debug_query_text(cls, value: str) -> str:
        return cls._redact_debug_text(value)

    @classmethod
    def _safe_debug_location(cls, value: str) -> str:
        text = str(value or "")
        parsed = urlparse(text)
        if parsed.scheme in {"http", "https"}:
            if parsed.username or parsed.password:
                return "redacted"
            suffix = " [path redacted]" if parsed.path and parsed.path != "/" else ""
            sanitized = f"{parsed.scheme}://{parsed.netloc}{suffix}"
            return sanitized
        if parsed.scheme or text.startswith("/"):
            return "redacted"
        return cls._redact_secret_only(text)

    @classmethod
    def _safe_public_location(cls, value: str) -> str:
        text = str(value or "")
        parsed = urlparse(text)
        if parsed.scheme in {"http", "https"}:
            if parsed.username or parsed.password:
                return "redacted"
            return cls._redact_secret_only(text)
        if parsed.scheme == "file" or text.startswith("/"):
            return "redacted"
        return cls._redact_secret_only(text)
