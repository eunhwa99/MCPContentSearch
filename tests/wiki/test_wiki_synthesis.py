import pytest

from environments.config import AppConfig
from wiki.synthesis import OpenAIWikiSynthesizer, build_wiki_synthesizer


pytestmark = pytest.mark.unit


def test_build_wiki_synthesizer_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CONTEXTWIKI_WIKI_LLM_ENABLED", raising=False)
    config = AppConfig()

    assert build_wiki_synthesizer(config, api_key="secret") is None


def test_build_wiki_synthesizer_requires_api_key_when_enabled():
    config = AppConfig(wiki_llm_enabled=True)

    assert build_wiki_synthesizer(config, api_key="") is None


def test_build_wiki_synthesizer_creates_openai_provider_when_enabled():
    config = AppConfig(
        wiki_llm_enabled=True,
        wiki_llm_model="test-model",
        wiki_llm_timeout=7,
        wiki_llm_max_evidence_chars=12,
    )

    synthesizer = build_wiki_synthesizer(config, api_key="secret")

    assert isinstance(synthesizer, OpenAIWikiSynthesizer)
    assert synthesizer.model == "test-model"
    assert synthesizer.timeout == 7
    assert synthesizer.max_evidence_chars == 12


def test_openai_synthesizer_prompt_payload_truncates_evidence_text():
    synthesizer = OpenAIWikiSynthesizer(
        api_key="secret",
        model="test-model",
        timeout=7,
        max_evidence_chars=12,
    )

    prompt = synthesizer._build_prompt_payload(
        {
            "topic": "Auto Wiki",
            "instructions": "Use citations.",
            "evidence": [
                {
                    "citation_marker": "C1",
                    "chunk_id": "chunk-1",
                    "text": "one two three four five",
                }
            ],
            "citations": [{"marker": "C1"}],
            "backlinks": [{"document_id": "doc-1"}],
        }
    )

    assert prompt["topic"] == "Auto Wiki"
    assert prompt["evidence"][0]["text"] == "one two thre..."
    assert prompt["citations"] == [{"marker": "C1"}]
    assert prompt["backlinks"] == [{"document_id": "doc-1"}]


def test_openai_synthesizer_prompt_payload_redacts_secret_like_evidence():
    synthesizer = OpenAIWikiSynthesizer(
        api_key="secret",
        model="test-model",
        timeout=7,
        max_evidence_chars=200,
    )

    prompt = synthesizer._build_prompt_payload(
        {
            "topic": "Auto Wiki",
            "evidence": [
                {
                    "citation_marker": "C1",
                    "chunk_id": "chunk-1",
                    "title": "Token sk-proj-aaaaaaaaaaaaaaaaaaaaaaaa",
                    "url": "https://example.com/docs?api_key=supersecret",
                    "text": (
                        "Use OPENAI_API_KEY=sk-proj-bbbbbbbbbbbbbbbbbbbb "
                        "and Authorization: Bearer abcdefghijklmnop."
                    ),
                }
            ],
            "citations": [{"marker": "C1"}],
            "backlinks": [],
        }
    )

    evidence = prompt["evidence"][0]
    assert "sk-proj-" not in evidence["title"]
    assert "supersecret" not in evidence["url"]
    assert "sk-proj-" not in evidence["text"]
    assert "Bearer abcdefghijklmnop" not in evidence["text"]
    assert "[REDACTED]" in evidence["text"]


def test_openai_synthesizer_prompt_payload_redacts_citations_and_backlinks():
    synthesizer = OpenAIWikiSynthesizer(
        api_key="secret",
        model="test-model",
        timeout=7,
        max_evidence_chars=200,
    )

    prompt = synthesizer._build_prompt_payload(
        {
            "topic": "Auto Wiki",
            "evidence": [
                {
                    "citation_marker": "C1",
                    "chunk_id": "chunk-1",
                    "text": "Evidence is safe [C1].",
                }
            ],
            "citations": [
                {
                    "marker": "C1",
                    "title": "Token sk-proj-aaaaaaaaaaaaaaaaaaaaaaaa",
                    "url": "https://example.com/citation?access_token=citation-secret",
                    "path": "/docs/Bearer abcdefghijklmnop",
                }
            ],
            "backlinks": [
                {
                    "title": "Backlink github_pat_aaaaaaaaaaaaaaaaaaaaaaaa",
                    "url": "https://example.com/backlink?api_key=backlink-secret",
                    "path": "/docs/sk-proj-bbbbbbbbbbbbbbbbbbbb",
                }
            ],
        }
    )

    prompt_text = str(prompt)
    assert "sk-proj-" not in prompt_text
    assert "github_pat_" not in prompt_text
    assert "citation-secret" not in prompt_text
    assert "backlink-secret" not in prompt_text
    assert "Bearer abcdefghijklmnop" not in prompt_text
    assert "[REDACTED]" in prompt_text


def test_openai_synthesizer_prompt_payload_redacts_sensitive_dict_keys():
    synthesizer = OpenAIWikiSynthesizer(
        api_key="secret",
        model="test-model",
        timeout=7,
        max_evidence_chars=300,
    )

    prompt = synthesizer._build_prompt_payload(
        {
            "topic": "Auto Wiki",
            "evidence": [
                {
                    "citation_marker": "C1",
                    "chunk_id": "chunk-1",
                    "api_key": "plain secret with spaces",
                    "metadata": {
                        "client secret": "nested citation secret",
                        "safe": "keep this context",
                    },
                    "text": "Evidence is safe [C1].",
                }
            ],
            "citations": [
                {
                    "marker": "C1",
                    "password": "citation password",
                    "nested": {"token": "nested token"},
                }
            ],
            "backlinks": [
                {
                    "secret": "backlink secret",
                    "details": {"x-amz-credential": "aws credential"},
                }
            ],
        }
    )

    prompt_text = str(prompt)
    assert "plain secret with spaces" not in prompt_text
    assert "nested citation secret" not in prompt_text
    assert "citation password" not in prompt_text
    assert "nested token" not in prompt_text
    assert "backlink secret" not in prompt_text
    assert "aws credential" not in prompt_text
    assert "keep this context" in prompt_text
    assert "[REDACTED]" in prompt_text


def test_openai_synthesizer_prompt_payload_redacts_compound_sensitive_dict_keys():
    synthesizer = OpenAIWikiSynthesizer(
        api_key="secret",
        model="test-model",
        timeout=7,
        max_evidence_chars=300,
    )

    prompt = synthesizer._build_prompt_payload(
        {
            "topic": "Auto Wiki",
            "evidence": [
                {
                    "citation_marker": "C1",
                    "chunk_id": "chunk-1",
                    "OPENAI_API_KEY": "openai plain secret",
                    "metadata": {
                        "github_token": "github plain token",
                        "database_password": "database plain password",
                    },
                    "text": "Evidence is safe [C1].",
                }
            ],
            "citations": [
                {
                    "marker": "C1",
                    "NOTION_TOKEN": "notion plain token",
                    "nested": {"refresh_token": "refresh plain token"},
                }
            ],
            "backlinks": [{"notion_api_key": "notion api key"}],
        }
    )

    prompt_text = str(prompt)
    assert "openai plain secret" not in prompt_text
    assert "github plain token" not in prompt_text
    assert "database plain password" not in prompt_text
    assert "notion plain token" not in prompt_text
    assert "refresh plain token" not in prompt_text
    assert "notion api key" not in prompt_text
    assert "[REDACTED]" in prompt_text


def test_openai_synthesizer_prompt_payload_redacts_camelcase_sensitive_dict_keys():
    synthesizer = OpenAIWikiSynthesizer(
        api_key="secret",
        model="test-model",
        timeout=7,
        max_evidence_chars=300,
    )

    prompt = synthesizer._build_prompt_payload(
        {
            "topic": "Auto Wiki",
            "evidence": [
                {
                    "citation_marker": "C1",
                    "chunk_id": "chunk-1",
                    "openaiApiKey": "openai camel secret",
                    "metadata": {
                        "githubToken": "github camel token",
                        "databasePassword": "database camel password",
                    },
                    "text": "Evidence is safe [C1].",
                }
            ],
            "citations": [{"marker": "C1", "refreshToken": "refresh camel token"}],
            "backlinks": [{"authToken": "auth camel token"}],
        }
    )

    prompt_text = str(prompt)
    assert "openai camel secret" not in prompt_text
    assert "github camel token" not in prompt_text
    assert "database camel password" not in prompt_text
    assert "refresh camel token" not in prompt_text
    assert "auth camel token" not in prompt_text
    assert "[REDACTED]" in prompt_text


def test_openai_synthesizer_prompt_payload_redacts_secret_like_dict_keys():
    synthesizer = OpenAIWikiSynthesizer(
        api_key="secret",
        model="test-model",
        timeout=7,
        max_evidence_chars=300,
    )
    github_pat = "github_pat_aaaaaaaaaaaaaaaaaaaaaaaa"
    openai_key = "sk-proj-aaaaaaaaaaaaaaaaaaaaaaaa"

    prompt = synthesizer._build_prompt_payload(
        {
            "topic": "Auto Wiki",
            "evidence": [
                {
                    "citation_marker": "C1",
                    "chunk_id": "chunk-1",
                    openai_key: "safe value",
                    "metadata": {github_pat: "nested safe value"},
                    "text": "Evidence is safe [C1].",
                }
            ],
            "citations": [{github_pat: "citation safe value", "marker": "C1"}],
            "backlinks": [{openai_key: "backlink safe value"}],
        }
    )

    prompt_text = str(prompt)
    assert github_pat not in prompt_text
    assert openai_key not in prompt_text
    assert "[REDACTED_KEY]" in prompt_text


def test_openai_synthesizer_prompt_payload_redacts_aws_and_assignment_secrets():
    synthesizer = OpenAIWikiSynthesizer(
        api_key="secret",
        model="test-model",
        timeout=7,
        max_evidence_chars=300,
    )

    prompt = synthesizer._build_prompt_payload(
        {
            "topic": "Auto Wiki",
            "evidence": [
                {
                    "citation_marker": "C1",
                    "chunk_id": "chunk-1",
                    "text": (
                        "AWS key AKIAIOSFODNN7EXAMPLE and "
                        "password=hunter2 and \"api_key\": \"json-secret\" "
                        "should not leave the process."
                    ),
                }
            ],
            "citations": [
                {
                    "marker": "C1",
                    "path": "AWS_ACCESS_KEY_ID=ASIAIOSFODNN7EXAMPLE",
                }
            ],
            "backlinks": [
                {
                    "title": "token=plain-secret",
                    "path": "aws_secret_access_key:very-secret-key",
                }
            ],
        }
    )

    prompt_text = str(prompt)
    assert "AKIAIOSFODNN7EXAMPLE" not in prompt_text
    assert "ASIAIOSFODNN7EXAMPLE" not in prompt_text
    assert "hunter2" not in prompt_text
    assert "json-secret" not in prompt_text
    assert "plain-secret" not in prompt_text
    assert "very-secret-key" not in prompt_text
    assert "[REDACTED]" in prompt_text


def test_openai_synthesizer_prompt_payload_redacts_broad_sensitive_keys():
    synthesizer = OpenAIWikiSynthesizer(
        api_key="secret",
        model="test-model",
        timeout=7,
        max_evidence_chars=500,
    )

    prompt = synthesizer._build_prompt_payload(
        {
            "topic": "Auto Wiki",
            "evidence": [
                {
                    "citation_marker": "C1",
                    "chunk_id": "chunk-1",
                    "text": (
                        "secret_key=secret-key-value "
                        "secret-key:hyphen-secret "
                        "private_key=private-value "
                        "ssh_private_key:ssh-value "
                        "credential=credential-value"
                    ),
                }
            ],
            "citations": [
                {
                    "marker": "C1",
                    "url": "https://example.com/file?credential=query-credential",
                }
            ],
            "backlinks": [
                {
                    "path": "x-amz-credential=amz-credential-value",
                }
            ],
        }
    )

    prompt_text = str(prompt)
    assert "secret-key-value" not in prompt_text
    assert "hyphen-secret" not in prompt_text
    assert "private-value" not in prompt_text
    assert "ssh-value" not in prompt_text
    assert "credential-value" not in prompt_text
    assert "query-credential" not in prompt_text
    assert "amz-credential-value" not in prompt_text
    assert "[REDACTED]" in prompt_text


def test_openai_synthesizer_prompt_payload_redacts_multiline_private_keys():
    synthesizer = OpenAIWikiSynthesizer(
        api_key="secret",
        model="test-model",
        timeout=7,
        max_evidence_chars=500,
    )

    prompt = synthesizer._build_prompt_payload(
        {
            "topic": "Auto Wiki",
            "evidence": [
                {
                    "citation_marker": "C1",
                    "chunk_id": "chunk-1",
                    "text": (
                        'ssh_private_key="-----BEGIN OPENSSH PRIVATE KEY-----\n'
                        "abc123SECRET\n"
                        '-----END OPENSSH PRIVATE KEY-----"'
                    ),
                }
            ],
            "citations": [{"marker": "C1"}],
            "backlinks": [
                {
                    "path": (
                        "private_key='-----BEGIN PRIVATE KEY-----\n"
                        "PRIVATEKEYBODY\n"
                        "-----END PRIVATE KEY-----'"
                    )
                }
            ],
        }
    )

    prompt_text = str(prompt)
    assert "OPENSSH PRIVATE KEY" not in prompt_text
    assert "abc123SECRET" not in prompt_text
    assert "PRIVATEKEYBODY" not in prompt_text
    assert "[REDACTED]" in prompt_text


def test_openai_synthesizer_prompt_payload_redacts_quoted_secret_values_with_spaces():
    synthesizer = OpenAIWikiSynthesizer(
        api_key="secret",
        model="test-model",
        timeout=7,
        max_evidence_chars=500,
    )

    prompt = synthesizer._build_prompt_payload(
        {
            "topic": "Auto Wiki",
            "evidence": [
                {
                    "citation_marker": "C1",
                    "chunk_id": "chunk-1",
                    "text": '"password": "hunter 2" secret_key="abc def ghi"',
                }
            ],
            "citations": [{"marker": "C1"}],
            "backlinks": [],
        }
    )

    prompt_text = str(prompt)
    assert "hunter 2" not in prompt_text
    assert "abc def ghi" not in prompt_text
    assert "[REDACTED]" in prompt_text


def test_openai_synthesizer_prompt_payload_redacts_unquoted_multiword_secrets():
    synthesizer = OpenAIWikiSynthesizer(
        api_key="secret",
        model="test-model",
        timeout=7,
        max_evidence_chars=500,
    )

    prompt = synthesizer._build_prompt_payload(
        {
            "topic": "Auto Wiki",
            "evidence": [
                {
                    "citation_marker": "C1",
                    "chunk_id": "chunk-1",
                    "text": "api_key: plain secret with spaces\nnext line is safe",
                }
            ],
            "citations": [{"marker": "C1"}],
            "backlinks": [{"path": "password=another plain secret; safe tail"}],
        }
    )

    prompt_text = str(prompt)
    assert "plain secret with spaces" not in prompt_text
    assert "another plain secret" not in prompt_text
    assert "next line is safe" in prompt_text
    assert "safe tail" in prompt_text
    assert "[REDACTED]" in prompt_text


def test_openai_synthesizer_prompt_payload_redacts_escaped_quote_secrets():
    synthesizer = OpenAIWikiSynthesizer(
        api_key="secret",
        model="test-model",
        timeout=7,
        max_evidence_chars=500,
    )

    prompt = synthesizer._build_prompt_payload(
        {
            "topic": "Auto Wiki",
            "evidence": [
                {
                    "citation_marker": "C1",
                    "chunk_id": "chunk-1",
                    "text": (
                        '"password": "abc\\"def secret" '
                        "'secret_key': 'single\\'quote secret'"
                    ),
                }
            ],
            "citations": [{"marker": "C1"}],
            "backlinks": [],
        }
    )

    prompt_text = str(prompt)
    assert "abc" not in prompt_text
    assert "def secret" not in prompt_text
    assert "single" not in prompt_text
    assert "quote secret" not in prompt_text
    assert "[REDACTED]" in prompt_text


def test_openai_synthesizer_prompt_payload_redacts_topic_and_instructions():
    synthesizer = OpenAIWikiSynthesizer(
        api_key="secret",
        model="test-model",
        timeout=7,
        max_evidence_chars=300,
    )

    prompt = synthesizer._build_prompt_payload(
        {
            "topic": "Find token=topic-secret",
            "instructions": "Use password=instruction-secret carefully.",
            "evidence": [
                {
                    "citation_marker": "C1",
                    "chunk_id": "chunk-1",
                    "text": "Evidence is safe [C1].",
                }
            ],
            "citations": [{"marker": "C1"}],
            "backlinks": [],
        }
    )

    prompt_text = str(prompt)
    assert "topic-secret" not in prompt_text
    assert "instruction-secret" not in prompt_text
    assert "[REDACTED]" in prompt_text
