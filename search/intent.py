from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from search.query_terms import (
    BROAD_TOPIC_TERMS,
    DOCUMENT_INTENT_TERMS,
    STRONG_ANCHOR_TERMS,
    TOKEN_RE,
)


class RetrievalIntent(str, Enum):
    STRICT_LOOKUP = "strict_lookup"
    BROAD_TOPIC = "broad_topic"
    LIST = "list"
    COMPARISON = "comparison"


LIST_HINT_TERMS = {
    "all",
    "collection",
    "collections",
    "list",
    "lists",
    "모아",
    "모아줘",
    "목록",
    "종류",
}
COMPARISON_HINT_TERMS = {
    "compare",
    "comparison",
    "versus",
    "vs",
    "비교",
    "차이",
}
STRICT_LOOKUP_HINT_TERMS = {
    "doc",
    "docs",
    "document",
    "documents",
    "find",
    "lookup",
    "readme",
    "찾아줘",
    "문서",
}


@dataclass(frozen=True)
class IntentDecision:
    intent: RetrievalIntent
    confidence: float
    reasons: tuple[str, ...]

    def as_debug_payload(self) -> dict[str, object]:
        return {
            "name": self.intent.value,
            "raw_name": self.intent.value,
            "confidence": round(self.confidence, 3),
            "reasons": list(self.reasons),
        }


def classify_intent(query: str, term_groups: list[set[str]] | None = None) -> IntentDecision:
    lowered_query = str(query or "").lower()
    groups = term_groups or []
    flattened_terms = {term for group in groups for term in group}

    scores = {
        RetrievalIntent.STRICT_LOOKUP: 0.0,
        RetrievalIntent.BROAD_TOPIC: 0.0,
        RetrievalIntent.LIST: 0.0,
        RetrievalIntent.COMPARISON: 0.0,
    }
    reasons: dict[RetrievalIntent, list[str]] = {intent: [] for intent in scores}

    def add(intent: RetrievalIntent, score: float, reason: str) -> None:
        scores[intent] += score
        reasons[intent].append(reason)

    query_tokens = set(TOKEN_RE.findall(lowered_query))

    if _matches_any(query_tokens, COMPARISON_HINT_TERMS) or any(
        group.intersection(COMPARISON_HINT_TERMS) for group in groups
    ):
        add(RetrievalIntent.COMPARISON, 3.0, "comparison_hint")

    if _matches_any(query_tokens, LIST_HINT_TERMS) or any(
        group.intersection(LIST_HINT_TERMS) for group in groups
    ):
        add(RetrievalIntent.LIST, 3.0, "list_hint")

    if _matches_any(query_tokens, STRICT_LOOKUP_HINT_TERMS) or any(
        group.intersection(DOCUMENT_INTENT_TERMS) for group in groups
    ):
        add(RetrievalIntent.STRICT_LOOKUP, 2.0, "document_hint")

    if any(_looks_like_anchor(term) for term in flattened_terms) or any(
        group.intersection(STRONG_ANCHOR_TERMS) for group in groups
    ):
        add(RetrievalIntent.STRICT_LOOKUP, 2.5, "strong_anchor")

    if any(group.intersection(BROAD_TOPIC_TERMS) for group in groups):
        add(RetrievalIntent.BROAD_TOPIC, 1.5, "broad_topic_terms")

    if len(flattened_terms) <= 2 and not any(_looks_like_anchor(term) for term in flattened_terms):
        add(RetrievalIntent.BROAD_TOPIC, 0.5, "short_unanchored_query")

    if any(group.intersection(DOCUMENT_INTENT_TERMS) for group in groups) and any(
        group.intersection(LIST_HINT_TERMS) for group in groups
    ):
        add(RetrievalIntent.LIST, 0.5, "document_collection_request")

    if not any(score > 0 for score in scores.values()):
        add(RetrievalIntent.BROAD_TOPIC, 1.0, "default")

    best_intent = max(
        scores,
        key=lambda intent: (scores[intent], _intent_priority(intent)),
    )
    total = sum(scores.values()) or 1.0
    confidence = scores[best_intent] / total
    return IntentDecision(
        intent=best_intent,
        confidence=confidence,
        reasons=tuple(reasons[best_intent] or ("default",)),
    )


def _matches_any(query_tokens: set[str], terms: set[str]) -> bool:
    return any(term in query_tokens for term in terms)


def _looks_like_anchor(term: str) -> bool:
    return (
        term in STRONG_ANCHOR_TERMS
        or "/" in term
        or "." in term
        or "_" in term
        or "-" in term
    )


def _intent_priority(intent: RetrievalIntent) -> int:
    if intent is RetrievalIntent.COMPARISON:
        return 4
    if intent is RetrievalIntent.LIST:
        return 3
    if intent is RetrievalIntent.STRICT_LOOKUP:
        return 2
    return 1
