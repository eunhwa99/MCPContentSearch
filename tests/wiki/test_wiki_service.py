import asyncio

import pytest

from core.models import ContextSearchResult
from wiki.service import WikiGenerationService


pytestmark = pytest.mark.unit


class FakeContextSearch:
    def __init__(self, results):
        self.results = results
        self.calls = []

    async def search_context(self, query, filters=None, top_k=10):
        self.calls.append({"query": query, "filters": filters, "top_k": top_k})
        return {"query": query, "results": self.results[:top_k]}


class FakeWikiSynthesizer:
    def __init__(self, response=None, *, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def synthesize_wiki_page(self, **payload):
        self.calls.append(payload)
        if self.error:
            raise self.error
        return self.response


def make_wiki_results():
    return [
        ContextSearchResult(
            chunk_id="chunk-1",
            document_id="doc-1",
            source_id="source_github",
            source_type="github",
            title="README",
            url="https://github.com/eunhwa99/MCPContentSearch/blob/main/README.md",
            path="README.md",
            score=0.91,
            text="ContextWiki generates citation-backed wiki pages from indexed chunks.",
            line_start=10,
            line_end=12,
            version_id="blob-sha",
        ),
        ContextSearchResult(
            chunk_id="chunk-2",
            document_id="doc-1",
            source_id="source_github",
            source_type="github",
            title="README",
            url="https://github.com/eunhwa99/MCPContentSearch/blob/main/README.md",
            path="README.md",
            score=0.82,
            text="Backlinks point to source documents represented in the page evidence.",
            line_start=14,
            line_end=16,
            version_id="blob-sha",
        ),
    ]


def test_generate_wiki_page_returns_citations_backlinks_and_markdown():
    service = WikiGenerationService(FakeContextSearch(make_wiki_results()))

    result = asyncio.run(
        service.generate_wiki_page(
            "  Auto   Wiki  ",
            filters={"source_id": "source_github"},
            top_k=5,
        )
    )

    assert result["status"] == "generated"
    assert result["topic"] == "Auto Wiki"
    assert result["used_chunks"] == ["chunk-1", "chunk-2"]
    assert [citation["marker"] for citation in result["citations"]] == ["C1", "C2"]
    assert result["citations"][0]["line_start"] == 10
    assert result["backlinks"] == [
        {
            "document_id": "doc-1",
            "source_id": "source_github",
            "source_type": "github",
            "title": "README",
            "url": "https://github.com/eunhwa99/MCPContentSearch/blob/main/README.md",
            "path": "README.md",
            "chunk_ids": ["chunk-1", "chunk-2"],
        }
    ]
    assert "# Auto Wiki Wiki" in result["markdown"]
    assert "[C1]" in result["markdown"]
    assert "`chunk-1`" in result["markdown"]


def test_generate_wiki_page_uses_async_synthesizer_when_configured():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Natural Auto Wiki",
            "sections": [
                {
                    "heading": "What It Does",
                    "content": "Auto Wiki turns indexed chunks into grounded pages [C1].",
                    "citation_markers": ["C1"],
                },
                {
                    "heading": "Traceability",
                    "content": "Backlinks keep generated pages tied to source documents [C2].",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# Natural Auto Wiki\n\n"
                "Auto Wiki turns indexed chunks into grounded pages [C1].\n\n"
                "Backlinks keep generated pages tied to source documents [C2].\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Natural Auto Wiki"
    assert result["sections"] == synthesizer.response["sections"]
    assert result["markdown"] == synthesizer.response["markdown"]
    assert [citation["marker"] for citation in result["citations"]] == ["C1", "C2"]
    assert result["backlinks"][0]["chunk_ids"] == ["chunk-1", "chunk-2"]
    assert result["used_chunks"] == ["chunk-1", "chunk-2"]
    assert len(synthesizer.calls) == 1
    assert synthesizer.calls[0]["topic"] == "Auto Wiki"
    assert [item["citation_marker"] for item in synthesizer.calls[0]["evidence"]] == [
        "C1",
        "C2",
    ]


def test_generate_wiki_page_accepts_citation_marker_after_sentence_punctuation():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Natural Auto Wiki",
            "sections": [
                {
                    "heading": "What It Does",
                    "content": "Auto Wiki turns chunks into grounded pages. [C1]",
                    "citation_markers": ["C1"],
                },
                {
                    "heading": "Traceability",
                    "content": "Backlinks keep pages tied to source documents. [C2]",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# Natural Auto Wiki\n\n"
                "Auto Wiki turns chunks into grounded pages. [C1]\n\n"
                "Backlinks keep pages tied to source documents. [C2]\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Natural Auto Wiki"
    assert result["markdown"] == synthesizer.response["markdown"]


def test_generate_wiki_page_accepts_cited_decimal_version_sentence():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Version Auto Wiki",
            "sections": [
                {
                    "heading": "Runtime",
                    "content": "Python 3.13 is supported by the wiki path [C1].",
                    "citation_markers": ["C1"],
                },
                {
                    "heading": "Traceability",
                    "content": "Backlinks keep pages tied to sources [C2].",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# Version Auto Wiki\n\n"
                "Python 3.13 is supported by the wiki path [C1].\n\n"
                "Backlinks keep pages tied to sources [C2].\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Version Auto Wiki"
    assert result["markdown"] == synthesizer.response["markdown"]


def test_generate_wiki_page_accepts_cited_abbreviation_sentence():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Abbreviation Auto Wiki",
            "sections": [
                {
                    "heading": "Runtime",
                    "content": "It supports e.g. FastMCP wiki flows [C1].",
                    "citation_markers": ["C1"],
                },
                {
                    "heading": "Traceability",
                    "content": "Backlinks keep pages tied to sources [C2].",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# Abbreviation Auto Wiki\n\n"
                "It supports e.g. FastMCP wiki flows [C1].\n\n"
                "Backlinks keep pages tied to sources [C2].\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Abbreviation Auto Wiki"
    assert result["markdown"] == synthesizer.response["markdown"]


def test_generate_wiki_page_accepts_cited_abbreviation_before_marker():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Abbreviation Marker Wiki",
            "sections": [
                {
                    "heading": "Runtime",
                    "content": "It supports e.g. [C1]",
                    "citation_markers": ["C1"],
                },
                {
                    "heading": "Traceability",
                    "content": "Backlinks keep pages tied to sources [C2].",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# Abbreviation Marker Wiki\n\n"
                "It supports e.g. [C1]\n\n"
                "Backlinks keep pages tied to sources [C2].\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Abbreviation Marker Wiki"
    assert result["markdown"] == synthesizer.response["markdown"]


def test_generate_wiki_page_accepts_cited_dotted_technical_terms():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Dotted Terms Wiki",
            "sections": [
                {
                    "heading": "Runtime",
                    "content": (
                        "Node.js, github.com, U.S. docs, and README.md are indexed [C1]."
                    ),
                    "citation_markers": ["C1"],
                },
                {
                    "heading": "Traceability",
                    "content": "The src/main.py path keeps source context [C2].",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# Dotted Terms Wiki\n\n"
                "Node.js, github.com, U.S. docs, and README.md are indexed [C1].\n\n"
                "The src/main.py path keeps source context [C2].\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Dotted Terms Wiki"
    assert result["markdown"] == synthesizer.response["markdown"]


def test_generate_wiki_page_accepts_cited_dotfile_term():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Dotfile Wiki",
            "sections": [
                {
                    "heading": "Runtime",
                    "content": "The .env.local file is excluded from prompts [C1].",
                    "citation_markers": ["C1"],
                },
                {
                    "heading": "Traceability",
                    "content": "Backlinks keep pages tied to sources [C2].",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# Dotfile Wiki\n\n"
                "The .env.local file is excluded from prompts [C1].\n\n"
                "Backlinks keep pages tied to sources [C2].\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Dotfile Wiki"
    assert result["markdown"] == synthesizer.response["markdown"]


def test_generate_wiki_page_falls_back_when_uncited_sentence_follows_dotted_token():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Dotted Uncited Wiki",
            "sections": [
                {
                    "heading": "Runtime",
                    "content": "The source is github.com. [C1] Unsupported claim.",
                    "citation_markers": ["C1"],
                },
                {
                    "heading": "Traceability",
                    "content": "Backlinks keep pages tied to sources [C2].",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# Dotted Uncited Wiki\n\n"
                "The source is github.com. [C1] Unsupported claim.\n\n"
                "Backlinks keep pages tied to sources [C2].\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Auto Wiki Wiki"
    assert "Dotted Uncited Wiki" not in result["markdown"]


def test_generate_wiki_page_falls_back_when_uncited_sentence_joins_dotted_token():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Dotted Joined Wiki",
            "sections": [
                {
                    "heading": "Runtime",
                    "content": (
                        "The domain is github.com.unsupported claim [C1]. "
                        "The file is src/main.py.unsupported claim [C1]."
                    ),
                    "citation_markers": ["C1"],
                },
                {
                    "heading": "Traceability",
                    "content": "Backlinks keep pages tied to sources [C2].",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# Dotted Joined Wiki\n\n"
                "The domain is github.com.unsupported claim [C1]. "
                "The file is src/main.py.unsupported claim [C1].\n\n"
                "Backlinks keep pages tied to sources [C2].\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Auto Wiki Wiki"
    assert "Dotted Joined Wiki" not in result["markdown"]


def test_generate_wiki_page_falls_back_when_uncited_sentence_joins_dotfile():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Dotfile Joined Wiki",
            "sections": [
                {
                    "heading": "Runtime",
                    "content": "The file is .env.local.unsupported claim [C1].",
                    "citation_markers": ["C1"],
                },
                {
                    "heading": "Traceability",
                    "content": "Backlinks keep pages tied to sources [C2].",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# Dotfile Joined Wiki\n\n"
                "The file is .env.local.unsupported claim [C1].\n\n"
                "Backlinks keep pages tied to sources [C2].\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Auto Wiki Wiki"
    assert "Dotfile Joined Wiki" not in result["markdown"]


def test_generate_wiki_page_falls_back_when_no_space_uncited_sentence_is_capitalized():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "No Space Wiki",
            "sections": [
                {
                    "heading": "Runtime",
                    "content": "First sentence.Second sentence [C1].",
                    "citation_markers": ["C1"],
                },
                {
                    "heading": "Traceability",
                    "content": "Backlinks keep pages tied to sources [C2].",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# No Space Wiki\n\n"
                "First sentence.Second sentence [C1].\n\n"
                "Backlinks keep pages tied to sources [C2].\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Auto Wiki Wiki"
    assert "No Space Wiki" not in result["markdown"]


def test_generate_wiki_page_falls_back_when_no_space_uncited_sentence_is_lowercase():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Lowercase No Space Wiki",
            "sections": [
                {
                    "heading": "Runtime",
                    "content": "Supported claim.unsupported claim [C1].",
                    "citation_markers": ["C1"],
                },
                {
                    "heading": "Traceability",
                    "content": "Backlinks keep pages tied to sources [C2].",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# Lowercase No Space Wiki\n\n"
                "Supported claim.unsupported claim [C1].\n\n"
                "Backlinks keep pages tied to sources [C2].\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Auto Wiki Wiki"
    assert "Lowercase No Space Wiki" not in result["markdown"]


def test_generate_wiki_page_falls_back_when_uncited_sentence_follows_abbreviation_marker():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Abbreviation Uncited Wiki",
            "sections": [
                {
                    "heading": "Runtime",
                    "content": "This concerns U.S. [C1] Unsupported claim.",
                    "citation_markers": ["C1"],
                },
                {
                    "heading": "Traceability",
                    "content": "Backlinks keep pages tied to sources [C2].",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# Abbreviation Uncited Wiki\n\n"
                "This concerns U.S. [C1] Unsupported claim.\n\n"
                "Backlinks keep pages tied to sources [C2].\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Auto Wiki Wiki"
    assert "Abbreviation Uncited Wiki" not in result["markdown"]


def test_generate_wiki_page_falls_back_when_synthesizer_fails():
    synthesizer = FakeWikiSynthesizer(error=RuntimeError("provider token expired"))
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Auto Wiki Wiki"
    assert "provider token expired" not in result["markdown"]
    assert "ContextWiki generates citation-backed wiki pages" in result["markdown"]
    assert "[C1]" in result["markdown"]


def test_generate_wiki_page_falls_back_when_synthesizer_omits_citations():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Ungrounded Auto Wiki",
            "sections": [
                {
                    "heading": "Unsupported",
                    "content": "Auto Wiki can summarize anything.",
                    "citation_markers": [],
                }
            ],
            "markdown": "# Ungrounded Auto Wiki\n\nAuto Wiki can summarize anything.\n",
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Auto Wiki Wiki"
    assert "Ungrounded Auto Wiki" not in result["markdown"]
    assert "[C1]" in result["markdown"]


def test_generate_wiki_page_falls_back_when_section_marker_metadata_does_not_match():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Mismatch Auto Wiki",
            "sections": [
                {
                    "heading": "Mismatch",
                    "content": "Auto Wiki cites the first chunk [C1].",
                    "citation_markers": ["C2"],
                },
                {
                    "heading": "Traceability",
                    "content": "Backlinks cite the second chunk [C2].",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# Mismatch Auto Wiki\n\n"
                "Auto Wiki cites the first chunk [C1].\n\n"
                "Backlinks cite the second chunk [C2].\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Auto Wiki Wiki"
    assert "Mismatch Auto Wiki" not in result["markdown"]
    assert "[C1]" in result["markdown"]


def test_generate_wiki_page_falls_back_when_section_marker_entries_are_not_strings():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Malformed Auto Wiki",
            "sections": [
                {
                    "heading": "Malformed",
                    "content": "Auto Wiki cites the first chunk [C1].",
                    "citation_markers": [{"marker": "C1"}],
                },
                {
                    "heading": "Traceability",
                    "content": "Backlinks cite the second chunk [C2].",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# Malformed Auto Wiki\n\n"
                "Auto Wiki cites the first chunk [C1].\n\n"
                "Backlinks cite the second chunk [C2].\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Auto Wiki Wiki"
    assert "Malformed Auto Wiki" not in result["markdown"]
    assert "[C1]" in result["markdown"]


def test_generate_wiki_page_falls_back_when_synthesizer_has_uncited_sentence():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Uncited Auto Wiki",
            "sections": [
                {
                    "heading": "Unsupported",
                    "content": (
                        "Auto Wiki can summarize anything. "
                        "It only returns grounded pages [C1]."
                    ),
                    "citation_markers": ["C1"],
                },
                {
                    "heading": "Traceability",
                    "content": "Backlinks keep generated pages tied to sources [C2].",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# Uncited Auto Wiki\n\n"
                "Auto Wiki can summarize anything. "
                "It only returns grounded pages [C1].\n\n"
                "Backlinks keep generated pages tied to sources [C2].\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Auto Wiki Wiki"
    assert "Uncited Auto Wiki" not in result["markdown"]
    assert "[C1]" in result["markdown"]


def test_generate_wiki_page_falls_back_when_uncited_sentence_has_no_leading_space():
    synthesizer = FakeWikiSynthesizer(
        {
            "title": "Joined Auto Wiki",
            "sections": [
                {
                    "heading": "Joined",
                    "content": "Supported claim [C1].Unsupported claim.",
                    "citation_markers": ["C1"],
                },
                {
                    "heading": "Traceability",
                    "content": "Backlinks cite the second chunk [C2].",
                    "citation_markers": ["C2"],
                },
            ],
            "markdown": (
                "# Joined Auto Wiki\n\n"
                "Supported claim [C1].Unsupported claim.\n\n"
                "Backlinks cite the second chunk [C2].\n"
            ),
        }
    )
    service = WikiGenerationService(
        FakeContextSearch(make_wiki_results()),
        llm_synthesizer=synthesizer,
    )

    result = asyncio.run(service.generate_wiki_page("Auto Wiki"))

    assert result["status"] == "generated"
    assert result["title"] == "Auto Wiki Wiki"
    assert "Joined Auto Wiki" not in result["markdown"]


def test_generate_wiki_page_respects_filters_and_top_k_limit():
    fake_search = FakeContextSearch(
        [
            ContextSearchResult(
                chunk_id="chunk-1",
                document_id="doc-1",
                source_id="source_notion",
                source_type="notion",
                title="ContextWiki",
                score=0.7,
                text="ContextWiki evidence.",
            )
        ]
    )
    service = WikiGenerationService(fake_search, max_top_k=3)

    result = asyncio.run(
        service.generate_wiki_page(
            "ContextWiki",
            filters={"source_ids": ["source_notion"]},
            top_k=99,
        )
    )

    assert result["status"] == "generated"
    assert fake_search.calls == [
        {
            "query": "ContextWiki",
            "filters": {"source_ids": ["source_notion"]},
            "top_k": 3,
        }
    ]


def test_generate_wiki_page_gates_low_score_results_as_insufficient():
    service = WikiGenerationService(
        FakeContextSearch(
            [
                ContextSearchResult(
                    chunk_id="chunk-low",
                    document_id="doc-low",
                    source_id="source_github",
                    source_type="github",
                    title="Unrelated",
                    score=0.1,
                    text="A weak nearest-neighbor result should not become a wiki page.",
                )
            ]
        ),
        min_score=0.35,
    )

    result = asyncio.run(service.generate_wiki_page("Precise topic"))

    assert result["status"] == "insufficient_evidence"
    assert result["used_chunks"] == []
    assert result["citations"] == []


def test_generate_wiki_page_returns_insufficient_evidence_without_results():
    service = WikiGenerationService(FakeContextSearch([]))

    result = asyncio.run(service.generate_wiki_page("Missing topic"))

    assert result["status"] == "insufficient_evidence"
    assert result["citations"] == []
    assert result["backlinks"] == []
    assert "Insufficient evidence" in result["message"]


def test_generate_wiki_page_requires_topic():
    service = WikiGenerationService(FakeContextSearch([]))

    result = asyncio.run(service.generate_wiki_page("   "))

    assert result["status"] == "insufficient_evidence"
    assert result["topic"] == ""
    assert "non-empty topic" in result["message"]
