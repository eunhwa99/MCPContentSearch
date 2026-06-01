import math
import re

from core.models import ContextSearchResult


KOREAN_QUERY_TERM_EXPANSIONS = {
    "깃허브": {"github"},
    "그래프": {"graph"},
    "구조": {"structure", "architecture"},
    "검색": {"search"},
    "니트코드": {"neetcode"},
    "문서": {"document", "documents", "docs"},
    "소스": {"source"},
    "알고리즘": {"algorithm", "algorithms"},
    "인덱싱": {"indexing", "index"},
    "프로젝트": {"project"},
}
QUERY_STOP_TERMS = {
    "about",
    "answer",
    "code",
    "does",
    "find",
    "for",
    "get",
    "give",
    "have",
    "how",
    "is",
    "me",
    "please",
    "repo",
    "repository",
    "related",
    "show",
    "tell",
    "the",
    "what",
    "with",
    "관련",
    "라고",
    "검색",
    "검색해도",
    "검색해줘",
    "라는",
    "리포지토리",
    "search",
    "알려줘",
    "에서",
    "찾아와",
    "찾아줘",
    "정리",
    "정리해줘",
    "코드",
}
STRONG_ANCHOR_TERMS = {"leetcode", "neetcode", "니트코드"}
TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣_/-]+")
DOCUMENT_INTENT_TERMS = {"doc", "docs", "document", "documents", "문서"}
BROAD_TOPIC_TERMS = {"algorithm", "algorithms", "알고리즘"}
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
        query_terms = self._query_terms(question)
        query_term_groups = self._query_term_groups(question)
        evidence = [
            item
            for item in results
            if item.score >= self.min_score
            and self._is_relevant_to_query(item, query_terms, query_term_groups)
        ]

        if len(evidence) < self.min_results:
            return {
                "question": question,
                "answer": "Insufficient evidence in indexed context to answer this question.",
                "evidence_status": "insufficient",
                "citations": [],
                "used_chunks": [],
            }

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
        answer = "\n\n".join(item.text for item in evidence if item.text)

        return {
            "question": question,
            "answer": answer,
            "evidence_status": "grounded",
            "citations": citations,
            "used_chunks": [item.chunk_id for item in evidence],
        }

    @staticmethod
    def _as_result(item) -> ContextSearchResult:
        if isinstance(item, ContextSearchResult):
            return item
        return ContextSearchResult(**item)

    @classmethod
    def _query_terms(cls, question: str) -> set[str]:
        terms = set()
        for group in cls._query_term_groups(question):
            terms.update(group)
        return terms

    @classmethod
    def _query_term_groups(cls, question: str) -> list[set[str]]:
        groups = []
        seen = set()
        for raw_token in TOKEN_RE.findall(question.lower()):
            for raw_term in cls._split_attached_latin_korean_token(raw_token):
                cls._append_query_term_group(raw_term, groups, seen)
        return groups

    @classmethod
    def _append_query_term_group(cls, raw_term: str, groups: list[set[str]], seen: set[tuple[str, ...]]):
        candidates = {raw_term}
        matched_korean_terms = []
        for korean_term, expansions in KOREAN_QUERY_TERM_EXPANSIONS.items():
            if korean_term in raw_term:
                matched_korean_terms.append(korean_term)
                candidates.update(expansions)
                if korean_term != raw_term:
                    candidates.add(korean_term)
        if len(matched_korean_terms) > 1:
            for korean_term in matched_korean_terms:
                cls_candidates = {korean_term, *KOREAN_QUERY_TERM_EXPANSIONS[korean_term]}
                normalized = {
                    candidate.strip("_-/")
                    for candidate in cls_candidates
                    if len(candidate.strip("_-/")) >= 2
                    and candidate.strip("_-/") not in QUERY_STOP_TERMS
                }
                if normalized:
                    key = tuple(sorted(normalized))
                    if key not in seen:
                        seen.add(key)
                        groups.append(normalized)
            return
        normalized = {
            candidate.strip("_-/")
            for candidate in candidates
            if len(candidate.strip("_-/")) >= 2
            and candidate.strip("_-/") not in QUERY_STOP_TERMS
        }
        if not normalized:
            return
        key = tuple(sorted(normalized))
        if key in seen:
            return
        seen.add(key)
        groups.append(normalized)

    @staticmethod
    def _split_attached_latin_korean_token(raw_token: str) -> list[str]:
        match = re.fullmatch(r"([0-9a-z_/-]+)([가-힣]+)", raw_token)
        if not match:
            return [raw_token]
        latin, korean = match.groups()
        return [latin, korean]

    @staticmethod
    def _is_relevant_to_query(
        item: ContextSearchResult,
        query_terms: set[str],
        query_term_groups: list[set[str]] | None = None,
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
        required_matches = len(groups) if len(groups) <= 3 else math.ceil(len(groups) / 2)
        return len(matched_groups) >= required_matches
