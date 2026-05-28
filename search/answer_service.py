import re

from core.models import ContextSearchResult


KOREAN_QUERY_TERM_EXPANSIONS = {
    "깃허브": {"github"},
    "그래프": {"graph"},
    "구조": {"structure", "architecture"},
    "검색": {"search"},
    "니트코드": {"leetcode", "neetcode"},
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
    "give",
    "how",
    "is",
    "me",
    "related",
    "tell",
    "the",
    "what",
    "with",
    "관련",
    "알려줘",
    "에서",
    "정리",
    "정리해줘",
    "코드",
}
STRONG_ANCHOR_TERMS = {"leetcode", "neetcode", "니트코드"}
TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣_/-]+")


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
        evidence = [
            item
            for item in results
            if item.score >= self.min_score and self._is_relevant_to_query(item, query_terms)
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
        for raw_term in TOKEN_RE.findall(question.lower()):
            candidates = {raw_term}
            for korean_term, expansions in KOREAN_QUERY_TERM_EXPANSIONS.items():
                if korean_term in raw_term:
                    candidates.update(expansions)
                    if korean_term != raw_term:
                        candidates.add(korean_term)
            for candidate in candidates:
                normalized = candidate.strip("_-/")
                if len(normalized) >= 2 and normalized not in QUERY_STOP_TERMS:
                    terms.add(normalized)
        return terms

    @staticmethod
    def _is_relevant_to_query(item: ContextSearchResult, query_terms: set[str]) -> bool:
        if not query_terms:
            return True
        haystack = " ".join(
            [
                item.title or "",
                item.path or "",
                item.preview or "",
                item.text or "",
            ]
        ).lower()
        strong_anchors = query_terms.intersection(STRONG_ANCHOR_TERMS)
        if strong_anchors:
            return any(term in haystack for term in strong_anchors)
        return any(term in haystack for term in query_terms)
