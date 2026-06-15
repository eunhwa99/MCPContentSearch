import pytest

from core.models import DocumentModel, SourceModel, SourceType, SyncStatus
from search.intent import RetrievalIntent, classify_intent
from search import ranking


pytestmark = pytest.mark.unit


class FakeMetadataStore:
    def __init__(self, sources=None):
        self.sources = sources or {}

    def get_source(self, source_id):
        return self.sources.get(source_id)


def test_query_source_type_terms_recognizes_obsidian_intent():
    assert ranking.query_source_type_terms(
        ranking.query_term_groups("obsidian sync notes")
    ) == {"obsidian"}
    assert ranking.query_source_type_terms(
        ranking.query_term_groups("옵시디언 vault notes")
    ) == {"obsidian"}


def test_document_matches_source_type_terms_recognizes_obsidian_source_ids():
    source = SourceModel(
        source_id="team-vault",
        source_type=SourceType.OBSIDIAN,
        name="Team Vault",
        sync_status=SyncStatus.IDLE,
    )
    document = DocumentModel(
        id="daily.md",
        document_id="daily.md",
        external_id="daily.md",
        title="Daily Note",
        content="Body",
        url="obsidian://open?vault=team&file=daily.md",
        platform="obsidian",
        source_id="team-vault",
    )

    ranker = ranking.ContextCandidateRanker(
        FakeMetadataStore({"team-vault": source}),
        object(),
    )

    assert ranker.document_matches_source_type_terms(document, {"obsidian"})

    canonical_document = document.model_copy(update={"source_id": "source_obsidian"})
    fallback_ranker = ranking.ContextCandidateRanker(FakeMetadataStore(), object())

    assert fallback_ranker.document_matches_source_type_terms(
        canonical_document,
        {"obsidian"},
    )


def test_classify_intent_prefers_strict_lookup_for_document_anchored_query():
    decision = classify_intent(
        "ImageGallery docs",
        ranking.query_term_groups("ImageGallery docs"),
    )

    assert decision.intent is RetrievalIntent.STRICT_LOOKUP
    assert "document_hint" in decision.reasons or "strong_anchor" in decision.reasons


def test_classify_intent_prefers_list_for_collection_request():
    decision = classify_intent(
        "AWS 관련 문서 모아줘",
        ranking.query_term_groups("AWS 관련 문서 모아줘"),
    )

    assert decision.intent is RetrievalIntent.LIST
    assert "list_hint" in decision.reasons


def test_classify_intent_prefers_comparison_for_vs_query():
    decision = classify_intent(
        "DynamoDB vs Cassandra 차이 비교",
        ranking.query_term_groups("DynamoDB vs Cassandra 차이 비교"),
    )

    assert decision.intent is RetrievalIntent.COMPARISON
    assert "comparison_hint" in decision.reasons


def test_classify_intent_does_not_treat_korean_prefix_collision_as_comparison():
    decision = classify_intent(
        "비교적 쉬운 문서",
        ranking.query_term_groups("비교적 쉬운 문서"),
    )

    assert decision.intent is not RetrievalIntent.COMPARISON
