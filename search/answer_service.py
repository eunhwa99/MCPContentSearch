from core.models import ContextSearchResult


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
        evidence = [item for item in results if item.score >= self.min_score]

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
