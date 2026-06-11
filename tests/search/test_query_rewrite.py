from search.query_rewrite import OpenAIQueryRewriter


def test_query_rewriter_redacts_assignment_and_url_query_secrets_before_llm_prompt():
    redacted = OpenAIQueryRewriter._redact_secret_like(
        {
            "query": (
                "find api_key=super-secret-value in "
                "https://example.com/private?token=another-secret-value"
            ),
            "terms": [
                "token=third-secret-value",
                "github_pat_secretcredential",
            ],
        }
    )
    payload = repr(redacted)

    assert "super-secret-value" not in payload
    assert "another-secret-value" not in payload
    assert "third-secret-value" not in payload
    assert "github_pat_secretcredential" not in payload
    assert "api_key=[REDACTED]" in redacted["query"]
    assert redacted["terms"][0] == "token=[REDACTED]"


def test_query_rewriter_redacts_quoted_key_secrets_before_llm_prompt():
    payload = OpenAIQueryRewriter._prompt_payload(
        'find {"api_key":"supersecretvalue123456"} source-filter-sanitization',
        max_rewrites=3,
    )
    payload_text = repr(payload)
    prompt_terms = {
        term
        for group in payload["normalized_terms"]
        for term in group
    }

    assert "supersecretvalue123456" not in payload_text
    assert '"api_key":"[REDACTED]"' in payload["query"]
    assert "source-filter-sanitization" in prompt_terms


def test_query_rewriter_redacts_complete_multiword_quoted_secret_values():
    payload = OpenAIQueryRewriter._prompt_payload(
        'find {"api_key":"plain secret\nwith spaces"} docs',
        max_rewrites=3,
    )
    payload_text = repr(payload)
    prompt_terms = {
        term
        for group in payload["normalized_terms"]
        for term in group
    }

    assert "plain secret" not in payload_text
    assert "with spaces" not in payload_text
    assert '"api_key":"[REDACTED]"' in payload["query"]
    assert "plain" not in prompt_terms
    assert "spaces" not in prompt_terms


def test_query_rewriter_redacts_common_credential_labels_before_llm_prompt():
    payload = OpenAIQueryRewriter._prompt_payload(
        'find cookie=supersecretcookie123456 '
        '{"jwt":"supersecretjwt123456"} '
        "pwd=supersecretpwd123456 code=supersecretcode123456 docs",
        max_rewrites=3,
    )
    payload_text = repr(payload)
    prompt_terms = {
        term
        for group in payload["normalized_terms"]
        for term in group
    }

    assert "supersecretcookie123456" not in payload_text
    assert "supersecretjwt123456" not in payload_text
    assert "supersecretpwd123456" not in payload_text
    assert "supersecretcode123456" not in payload_text
    assert "cookie=[REDACTED]" in payload["query"]
    assert '"jwt":"[REDACTED]"' in payload["query"]
    assert "pwd=[REDACTED]" in payload["query"]
    assert "code=[REDACTED]" in payload["query"]
    assert not {
        "supersecretcookie123456",
        "supersecretjwt123456",
        "supersecretpwd123456",
        "supersecretcode123456",
    } & prompt_terms


def test_query_rewriter_redacts_auth_scheme_tokens_before_llm_prompt():
    payload = OpenAIQueryRewriter._prompt_payload(
        "find authorization Bearer abcdefgh12345678 "
        "and authorization Basic dXNlcjpwYXNz docs",
        max_rewrites=3,
    )
    payload_text = repr(payload)
    prompt_terms = {
        term
        for group in payload["normalized_terms"]
        for term in group
    }

    assert "abcdefgh12345678" not in payload_text
    assert "dXNlcjpwYXNz" not in payload_text
    assert "Bearer [REDACTED]" in payload["query"]
    assert "Basic [REDACTED]" in payload["query"]
    assert not {"abcdefgh12345678", "dxnlcjpwyxnz"} & prompt_terms


def test_query_rewriter_derives_prompt_terms_from_redacted_query():
    payload = OpenAIQueryRewriter._prompt_payload(
        "find api_key=supersecretvalue123456 in docs",
        max_rewrites=3,
    )
    payload_text = repr(payload)

    assert payload["query"] == "find api_key=[REDACTED] in docs"
    assert "supersecretvalue123456" not in payload_text
    assert "redacted" not in {
        term
        for group in payload["normalized_terms"]
        for term in group
    }


def test_query_rewriter_redacts_whitespace_secret_from_prompt_terms():
    payload = OpenAIQueryRewriter._prompt_payload(
        "find token supersecretvalue123456 in docs",
        max_rewrites=3,
    )
    payload_text = repr(payload)

    assert payload["query"] == "find token [REDACTED] in docs"
    assert "supersecretvalue123456" not in payload_text
    assert "redacted" not in {
        term
        for group in payload["normalized_terms"]
        for term in group
    }


def test_query_rewriter_prompt_preserves_benign_hyphenated_identifiers():
    payload = OpenAIQueryRewriter._prompt_payload(
        "context-wiki-debug source-filter-sanitization guide",
        max_rewrites=3,
    )
    prompt_terms = {
        term
        for group in payload["normalized_terms"]
        for term in group
    }

    assert payload["query"] == (
        "context-wiki-debug source-filter-sanitization guide"
    )
    assert "context-wiki-debug" in prompt_terms
    assert "source-filter-sanitization" in prompt_terms


def test_query_rewriter_prompt_preserves_common_nonsecret_phrases():
    payload = OpenAIQueryRewriter._prompt_payload(
        "code examples cookie settings jwt examples basic examples",
        max_rewrites=3,
    )
    prompt_terms = {
        term
        for group in payload["normalized_terms"]
        for term in group
    }

    assert payload["query"] == (
        "code examples cookie settings jwt examples basic examples"
    )
    assert "examples" in prompt_terms
    assert "settings" in prompt_terms
