from __future__ import annotations

import re

TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣_/-]+")
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
    "search",
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
    "적은",
    "라는",
    "리포지토리",
    "알려줘",
    "에서",
    "찾아와",
    "찾아줘",
    "정리",
    "정리해줘",
    "코드",
}
DOCUMENT_INTENT_TERMS = {"doc", "docs", "document", "documents", "문서"}
BROAD_TOPIC_TERMS = {
    "algorithm",
    "algorithms",
    "concept",
    "concepts",
    "configuration",
    "config",
    "example",
    "examples",
    "guide",
    "guides",
    "howto",
    "how-to",
    "intro",
    "introduction",
    "overview",
    "알고리즘",
    "problem",
    "problems",
    "question",
    "questions",
    "sample",
    "samples",
    "setup",
    "solution",
    "solutions",
    "tutorial",
    "tutorials",
    "usage",
    "개념",
    "가이드",
    "문제",
    "사용법",
    "설정",
    "예제",
    "풀이",
}
STRONG_ANCHOR_TERMS = {"neetcode", "니트코드"}
QUERY_TERM_EXPANSIONS = {
    "깃허브": {"github"},
    "그래프": {"graph"},
    "구조": {"structure", "architecture"},
    "검색": {"search"},
    "니트코드": {"neetcode"},
    "가이드": {"guide", "guides", "tutorial", "tutorials"},
    "개념": {"concept", "concepts", "overview", "intro", "introduction"},
    "문제": {"problem", "problems", "question", "questions", "solution", "solutions"},
    "문서": {"document", "documents", "docs"},
    "사용법": {"usage", "howto", "how-to", "guide", "guides", "tutorial", "tutorials"},
    "설정": {"setup", "configuration", "config"},
    "예제": {"example", "examples", "sample", "samples"},
    "풀이": {"solution", "solutions"},
    "소스": {"source"},
    "웹": {"web", "website"},
    "알고리즘": {"algorithm", "algorithms"},
    "인덱싱": {"indexing", "index"},
    "프로젝트": {"project"},
    "aws": {"amazon web services"},
    "amazonwebservices": {"aws", "amazon web services"},
    "아마존웹서비스": {"aws", "amazon web services"},
    "ddb": {"dynamodb"},
    "dynamodb": {"ddb"},
    "s3": {"simple storage service", "aws s3"},
    "ec2": {"elastic compute cloud", "aws ec2"},
    "rds": {"relational database service", "aws rds"},
    "iam": {"identity access management", "identity and access management", "aws iam"},
    "vpc": {"virtual private cloud", "aws vpc"},
    "eks": {"elastic kubernetes service", "aws eks"},
    "ecs": {"elastic container service", "aws ecs"},
    "lambda": {"aws lambda"},
}


def split_attached_latin_korean_token(raw_token: str) -> list[str]:
    match = re.fullmatch(r"([0-9a-z_/-]+)([가-힣]+)", raw_token)
    if not match:
        return [raw_token]
    latin, korean = match.groups()
    return [latin, korean]


def append_query_term_group(raw_term: str, groups: list[set[str]], seen: set[tuple[str, ...]]) -> None:
    candidates = {raw_term}
    matched_terms = []
    if raw_term in QUERY_TERM_EXPANSIONS:
        matched_terms.append(raw_term)
        candidates.update(QUERY_TERM_EXPANSIONS[raw_term])
    else:
        for term, expansions in QUERY_TERM_EXPANSIONS.items():
            if term in raw_term:
                matched_terms.append(term)
                candidates.update(expansions)
                if term != raw_term:
                    candidates.add(term)
    if len(matched_terms) > 1:
        for term in matched_terms:
            term_candidates = {term, *QUERY_TERM_EXPANSIONS[term]}
            normalized = {
                candidate.strip("_-/")
                for candidate in term_candidates
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


def query_term_groups(query: str) -> list[set[str]]:
    groups = []
    seen = set()
    for raw_token in TOKEN_RE.findall(query.lower()):
        for raw_term in split_attached_latin_korean_token(raw_token):
            append_query_term_group(raw_term, groups, seen)
    return groups


def query_terms(query: str) -> set[str]:
    terms = set()
    for group in query_term_groups(query):
        terms.update(group)
    return terms


def _preferred_group_term(group: set[str], *, document_intent: bool = False) -> str:
    if not group:
        return ""
    if document_intent:
        ranked = sorted(group, key=lambda term: (0 if term == "docs" else 1, len(term), term))
        return ranked[0]
    ranked = sorted(
        group,
        key=lambda term: (
            0 if term in STRONG_ANCHOR_TERMS else 1,
            0 if "-" in term or "/" in term or "_" in term else 1,
            0 if " " not in term else 1,
            len(term),
            term,
        ),
    )
    return ranked[0]


def _expansion_group_term(group: set[str]) -> str:
    if not group:
        return ""
    ranked = sorted(
        group,
        key=lambda term: (
            0 if " " in term else 1,
            0 if term in STRONG_ANCHOR_TERMS else 1,
            -len(term),
            term,
        ),
    )
    return ranked[0]


def retrieval_query_variants(query: str, term_groups: list[set[str]] | None = None) -> list[str]:
    normalized_query = " ".join(str(query or "").split())
    groups = term_groups or query_term_groups(normalized_query)
    variants = []
    seen = set()

    def add_variant(value: str) -> None:
        normalized = " ".join(str(value or "").split())
        if not normalized:
            return
        key = normalized.lower()
        if key in seen:
            return
        seen.add(key)
        variants.append(normalized)

    add_variant(normalized_query)

    focused_terms = []
    topic_terms = []
    intent_terms = []
    expansions = []
    for group in groups:
        if group.intersection(DOCUMENT_INTENT_TERMS):
            intent_term = _preferred_group_term(group, document_intent=True)
            if intent_term:
                intent_terms.append(intent_term)
            continue

        preferred_term = _preferred_group_term(group)
        if preferred_term:
            focused_terms.append(preferred_term)
            topic_terms.append(preferred_term)

        expanded_term = _expansion_group_term(group)
        if expanded_term and expanded_term.lower() not in normalized_query.lower():
            expansions.append(expanded_term)

    if focused_terms:
        add_variant(" ".join([*focused_terms, *intent_terms]))

    if topic_terms and intent_terms:
        add_variant(" ".join(topic_terms))

    if expansions:
        add_variant(" ".join([normalized_query, *expansions]))
        if focused_terms or intent_terms:
            add_variant(" ".join([*focused_terms, *intent_terms, *expansions]))

    return variants
