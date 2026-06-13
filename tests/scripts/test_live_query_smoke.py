from scripts.live_query_smoke import format_smoke_summary, sanitize_live_query_result


def test_format_smoke_summary_includes_rewrite_decision_hits_and_citations():
    summary = format_smoke_summary(
        query="aws startup",
        question="How do I start EC2?",
        source_id="source_github",
        top_k=3,
        search_payload={
            "results": [
                {
                    "source_id": "source_github",
                    "title": "EC2 setup guide",
                    "chunk_id": "chunk-1",
                    "score": 0.91,
                }
            ],
            "debug": {
                "rewrite_enabled": True,
                "rewrite_attempted": True,
                "rewrite_applied": True,
                "rewrite_skipped_reason": "",
                "rewritten_queries": ["aws ec2 setup"],
            },
        },
        answer_payload={
            "evidence_status": "grounded",
            "citations": [
                {
                    "title": "EC2 setup guide",
                    "chunk_id": "chunk-1",
                }
            ],
        },
    )

    assert "search query: aws startup" in summary
    assert "answer question: How do I start EC2?" in summary
    assert "rewrite: enabled=yes attempted=yes applied=yes reason=-" in summary
    assert "rewrites: aws ec2 setup" in summary
    assert "hit 1: source_github | EC2 setup guide | chunk-1 | score=0.910" in summary
    assert "answer: grounded" in summary
    assert "citation 1: EC2 setup guide | chunk-1" in summary


def test_format_smoke_summary_uses_safe_placeholders_for_empty_optional_sections():
    summary = format_smoke_summary(
        query="plain query",
        question="plain question",
        source_id=None,
        top_k=5,
        search_payload={
            "results": [],
            "debug": {
                "rewrite_enabled": False,
                "rewrite_attempted": False,
                "rewrite_applied": False,
                "rewrite_skipped_reason": "disabled",
                "rewritten_queries": [],
            },
        },
        answer_payload={
            "evidence_status": "insufficient",
            "citations": [],
        },
    )

    assert "source filter: -" in summary
    assert "rewrite: enabled=no attempted=no applied=no reason=disabled" in summary
    assert "rewrites: -" in summary
    assert "hits: 0" in summary
    assert "citations: 0" in summary


def test_format_smoke_summary_redacts_secret_like_query_text():
    summary = format_smoke_summary(
        query="token super-secret-value docs",
        question="show /Users/eunhwa/private docs",
        source_id=None,
        top_k=5,
        search_payload={"results": [], "debug": {}},
        answer_payload={"evidence_status": "insufficient", "citations": []},
    )

    assert "super-secret-value" not in summary
    assert "/Users/eunhwa/private" not in summary
    assert "[REDACTED]" in summary


def test_sanitize_live_query_result_omits_raw_text_and_keeps_structured_json():
    payload = sanitize_live_query_result(
        {
            "query": "aws startup",
            "question": "How do I start EC2?",
            "source_id": "source_github",
            "top_k": 3,
            "rewrite_mode": "auto",
            "search": {
                "results": [
                    {
                        "chunk_id": "chunk-1",
                        "document_id": "doc-1",
                        "source_id": "source_github",
                        "title": "EC2 setup guide",
                        "score": 0.91,
                        "preview": "compact preview",
                        "text": "full chunk text should not leak",
                    }
                ],
                "debug": {
                    "rewrite_enabled": True,
                    "rewrite_attempted": True,
                    "rewrite_applied": True,
                    "rewrite_skipped_reason": "",
                    "rewritten_queries": ["aws ec2 setup"],
                },
            },
            "answer": {
                "evidence_status": "grounded",
                "answer": "Use EC2.",
                "citations": [{"title": "EC2 setup guide", "chunk_id": "chunk-1"}],
                "used_chunks": [{"chunk_id": "chunk-1", "text": "used chunk raw text"}],
            },
        }
    )

    assert payload["search"]["results"][0]["chunk_id"] == "chunk-1"
    assert "text" not in payload["search"]["results"][0]
    assert payload["answer"]["citations"][0]["chunk_id"] == "chunk-1"
    assert "used_chunks" not in payload["answer"]
