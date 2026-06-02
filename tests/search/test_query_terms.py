from search.query_terms import query_term_groups, retrieval_query_variants


def test_retrieval_query_variants_adds_generic_focused_variant_for_natural_language_query():
    groups = query_term_groups("이 프로젝트 구조 정리해줘")

    variants = retrieval_query_variants("이 프로젝트 구조 정리해줘", groups)

    assert variants[0] == "이 프로젝트 구조 정리해줘"
    assert "프로젝트 구조" in variants


def test_retrieval_query_variants_adds_topic_only_variant_for_document_query():
    groups = query_term_groups("zero-trust docs")

    variants = retrieval_query_variants("zero-trust docs", groups)

    assert "zero-trust docs" in variants
    assert "zero-trust" in variants


def test_retrieval_query_variants_preserves_alias_expansion_but_is_not_aws_specific():
    groups = query_term_groups("AWS에 적은 문서를 찾아줘")

    variants = retrieval_query_variants("AWS에 적은 문서를 찾아줘", groups)

    assert "aws docs" in variants
    assert "aws" in variants
    assert any("amazon web services" in variant for variant in variants)


def test_query_term_groups_expand_korean_problem_terms_generically():
    groups = query_term_groups("neetcode 문제")

    assert {"neetcode"} in groups
    assert any(
        {"문제", "problem", "problems", "question", "questions", "solution", "solutions"} == group
        for group in groups
    )


def test_query_term_groups_expand_korean_usage_terms_generically():
    groups = query_term_groups("redis 사용법")

    assert {"redis"} in groups
    assert any(
        {"사용법", "usage", "howto", "how-to", "guide", "guides", "tutorial", "tutorials"} == group
        for group in groups
    )
