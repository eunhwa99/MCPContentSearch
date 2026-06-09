import re
from collections.abc import Iterable
from typing import Any

from core.models import ChunkModel, DocumentModel
from environments.config import AppConfig
from search.query_terms import (
    BROAD_TOPIC_TERMS,
    DOCUMENT_INTENT_TERMS,
    STRONG_ANCHOR_TERMS,
    TOKEN_RE,
    append_query_term_group as append_query_term_group_impl,
    query_term_groups as query_term_groups_impl,
    retrieval_query_variants,
    split_attached_latin_korean_token as split_attached_latin_korean_token_impl,
)
from storage.metadata_store import MetadataStore


GITHUB_IDENTITY_TERMS = {"github", *STRONG_ANCHOR_TERMS}
GENERIC_GITHUB_TERMS = {"github", "깃허브"}
GENERIC_SINGLE_TOKEN_TERMS = {
    "algorithm",
    "algorithms",
    "architecture",
    "authentication",
    "configuration",
    "database",
    "deployment",
    "document",
    "documents",
    "frontend",
    "infrastructure",
    "javascript",
    "kubernetes",
    "microservices",
    "observability",
    "performance",
    "read-only",
    "retrieval",
    "serialization",
    "troubleshooting",
}
GENERIC_SINGLE_TOKEN_SUFFIXES = (
    "able",
    "ance",
    "ence",
    "ible",
    "ics",
    "ing",
    "ism",
    "ity",
    "ment",
    "ness",
    "ology",
    "ship",
    "sion",
    "tion",
)
METADATA_TERMS = {*GITHUB_IDENTITY_TERMS, *DOCUMENT_INTENT_TERMS}
DOCUMENT_LIKE_PATH_RE = re.compile(
    r"(^|[/:])(readme(\.|/|$)|docs?/|documentation/)|\.(md|mdx|markdown|rst|txt)(\s|$)",
    re.IGNORECASE,
)


def document_haystack(document: DocumentModel) -> str:
    return " ".join(
        [
            document.id or "",
            document.document_id or "",
            document.external_id or "",
            document.title or "",
            document.url or "",
            document.canonical_url or "",
            document.path or "",
            document.platform or "",
            document.content or "",
        ]
    ).lower()


def document_metadata_haystack(document: DocumentModel) -> str:
    return " ".join(
        [
            document.document_id or "",
            document.external_id or "",
            document.title or "",
            document.url or "",
            document.canonical_url or "",
            document.path or "",
            document.platform or "",
        ]
    ).lower()


def is_document_like(document: DocumentModel, metadata_haystack: str, *, is_github: bool) -> bool:
    if not is_github:
        return True
    return bool(DOCUMENT_LIKE_PATH_RE.search(metadata_haystack))


def term_group_matches(
    term_group: set[str],
    haystack: str,
    metadata_haystack: str,
    is_document_like_match: bool,
) -> bool:
    if any(term in haystack for term in term_group):
        return True
    return bool(
        is_document_like_match
        and term_group.intersection(DOCUMENT_INTENT_TERMS)
        and DOCUMENT_LIKE_PATH_RE.search(metadata_haystack)
    )


def query_term_groups(query: str) -> list[set[str]]:
    return query_term_groups_impl(query)


def append_query_term_group(raw_term: str, groups: list[set[str]], seen: set[tuple[str, ...]]):
    append_query_term_group_impl(raw_term, groups, seen)


def split_attached_latin_korean_token(raw_token: str) -> list[str]:
    return split_attached_latin_korean_token_impl(raw_token)


def scoring_term_groups(term_groups: list[set[str]]) -> list[set[str]]:
    if not any(group.intersection(STRONG_ANCHOR_TERMS) for group in term_groups):
        return term_groups
    narrowed = [
        group
        for group in term_groups
        if not group.intersection(BROAD_TOPIC_TERMS)
    ]
    return narrowed or term_groups


def should_run_metadata_fallback(
    query: str,
    term_groups: list[set[str]],
    candidates: list[dict[str, Any]],
    top_k: int,
    source_ids: list[str] | None,
) -> bool:
    if len(candidates) < top_k:
        return True
    if source_ids:
        return len(candidates) < top_k or query_is_metadata_like(
            query,
            term_groups,
        )
    if any(term in GITHUB_IDENTITY_TERMS for group in term_groups for term in group):
        return True
    return query_is_metadata_like(query, term_groups)


def metadata_terms_are_repo_or_identity(metadata_terms: set[str]) -> bool:
    return any(
        term in GITHUB_IDENTITY_TERMS
        or "/" in term
        or "_" in term
        or "-" in term
        for term in metadata_terms
    )


def metadata_lookup_terms(
    term_groups: list[set[str]],
    source_ids: list[str] | None,
) -> set[str] | None:
    del source_ids
    identity_terms = set()
    for group in term_groups:
        identity_group = {
            term
            for term in group
            if term in GITHUB_IDENTITY_TERMS and term not in GENERIC_GITHUB_TERMS
        }
        identity_terms.update(preferred_metadata_terms(identity_group))
    repo_terms = repository_lookup_terms_from_groups(term_groups)
    topical_terms = metadata_lookup_topical_terms(term_groups)
    if identity_terms or repo_terms:
        return identity_terms | repo_terms | topical_terms
    return None


def github_metadata_anchor_groups(query: str, term_groups: list[set[str]]) -> list[set[str]]:
    repo_terms = set()
    has_document_intent = any(
        group.intersection(DOCUMENT_INTENT_TERMS) for group in term_groups
    )
    has_repository_intent = bool(
        re.search(r"\b(repo|repository)\b", query.lower())
        or "리포지토리" in query
    )
    if (
        has_document_intent
        or has_repository_intent
    ) and (
        query_has_strong_repository_signal(query, term_groups)
        or query_has_lowercase_repository_probe(term_groups)
    ):
        repo_terms = repository_lookup_terms_from_groups(term_groups)
    anchor_groups = []
    for group in term_groups:
        if group.intersection(STRONG_ANCHOR_TERMS):
            anchor_groups.append(group)
            continue
        if repo_terms and preferred_metadata_terms(group).intersection(repo_terms):
            anchor_groups.append(group)
    return anchor_groups


def ordinary_metadata_lookup_terms(term_groups: list[set[str]]) -> set[str] | None:
    terms = set()
    for group in scoring_term_groups(term_groups):
        if group.intersection(DOCUMENT_INTENT_TERMS):
            continue
        terms.update(group)
    return terms or None


def document_intent_metadata_lookup_terms(term_groups: list[set[str]]) -> set[str] | None:
    if not term_groups:
        return None
    terms = set()
    for group in scoring_term_groups(term_groups):
        if not group.intersection(DOCUMENT_INTENT_TERMS):
            return None
        terms.update(group.intersection(DOCUMENT_INTENT_TERMS))
    return terms or None


def preferred_metadata_terms(terms: set[str]) -> set[str]:
    ascii_terms = {term for term in terms if re.fullmatch(r"[a-z0-9_/-]+", term)}
    return ascii_terms or terms


def preferred_query_phrases(
    query: str,
    term_groups: list[set[str]],
) -> list[str]:
    phrases = []
    seen = set()
    for variant in retrieval_query_variants(query, term_groups):
        normalized = " ".join(str(variant or "").split()).lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if len(normalized.split()) >= 2:
            phrases.append(normalized)
    return phrases[:3]


def phrase_match_bonus(phrases: list[str], metadata_haystack: str) -> float:
    bonus = 0.0
    for phrase in phrases:
        if phrase in metadata_haystack:
            bonus = max(bonus, 0.18 if len(phrase.split()) >= 3 else 0.12)
    return bonus


def query_source_type_terms(term_groups: list[set[str]]) -> set[str]:
    source_terms = set()
    lowered_groups = [{term.lower() for term in group} for group in term_groups]
    has_explicit_non_web_source = any(
        group.intersection({"github", "깃허브", "notion", "노션", "tistory", "티스토리"})
        for group in lowered_groups
    )
    has_site_web_context = any(
        group.intersection(
            {
                "auth",
                "authentication",
                "docs",
                "documentation",
                "login",
                "official",
                "signin",
                "signup",
                "web",
                "website",
                "웹",
            }
        )
        for group in lowered_groups
    )
    has_docs_topical_sibling = any(
        not group.intersection(BROAD_TOPIC_TERMS)
        and not group.intersection({"aws", "amazon", "amazon web services", "services"})
        and not group.intersection({"docs", "documentation"})
        and not group.intersection(DOCUMENT_INTENT_TERMS)
        for group in lowered_groups
    )
    for index, lowered in enumerate(lowered_groups):
        if lowered.intersection({"github", "깃허브"}):
            source_terms.add("github")
        if lowered.intersection({"notion", "노션"}):
            source_terms.add("notion")
        if lowered.intersection({"tistory", "티스토리"}):
            source_terms.add("tistory")
        docs_like_web = (
            ("docs" in lowered or "documentation" in lowered)
            and not has_explicit_non_web_source
            and has_docs_topical_sibling
        )
        explicit_web = (
            "web" in lowered
            or "웹" in lowered
            or "website" in lowered
            or ("site" in lowered and has_site_web_context)
            or docs_like_web
        )
        aws_phrase_middle = (
            lowered.issubset({"web", "website"})
            and "web" in lowered
            and index > 0
            and index + 1 < len(lowered_groups)
            and lowered_groups[index - 1].intersection({"amazon", "아마존", "aws"})
            and lowered_groups[index + 1].intersection({"services", "service", "서비스"})
        )
        aws_only_expansion = aws_phrase_middle
        if explicit_web and not aws_only_expansion:
            source_terms.add("web")
    return source_terms


def metadata_lookup_topical_terms(term_groups: list[set[str]]) -> set[str]:
    if not any(group.intersection(STRONG_ANCHOR_TERMS) for group in term_groups):
        return set()
    terms = set()
    for group in term_groups:
        if (
            group.intersection(STRONG_ANCHOR_TERMS)
            or group.intersection(DOCUMENT_INTENT_TERMS)
            or group.intersection(BROAD_TOPIC_TERMS)
        ):
            continue
        terms.update(preferred_metadata_terms(group))
    return terms


def strong_anchor_lookup_terms(term_groups: list[set[str]]) -> tuple[set[str], set[str]]:
    identity_terms = set()
    topical_terms = set()
    for group in term_groups:
        if group.intersection(STRONG_ANCHOR_TERMS):
            identity_terms.update(preferred_metadata_terms(group))
            continue
        if group.intersection(DOCUMENT_INTENT_TERMS) or group.intersection(BROAD_TOPIC_TERMS):
            continue
        topical_terms.update(preferred_metadata_terms(group))
    return identity_terms, topical_terms


def requires_all_metadata_lookup_terms(term_groups: list[set[str]]) -> bool:
    return bool(metadata_lookup_topical_terms(term_groups))


def allows_github_document_body_lookup(term_groups: list[set[str]]) -> bool:
    return (
        any(group.intersection(STRONG_ANCHOR_TERMS) for group in term_groups)
        and any(group.intersection(DOCUMENT_INTENT_TERMS) for group in term_groups)
        and bool(metadata_lookup_topical_terms(term_groups))
    )


def query_is_metadata_like(query: str, term_groups: list[set[str]]) -> bool:
    if any(term in GITHUB_IDENTITY_TERMS for group in term_groups for term in group):
        return True
    if (
        repository_lookup_terms_from_groups(term_groups)
        and any(group.intersection(DOCUMENT_INTENT_TERMS) for group in term_groups)
    ):
        return True
    return (
        query_has_strong_repository_signal(query, term_groups)
        or query_has_lowercase_repository_probe(term_groups)
    )


def should_try_lowercase_github_probe(
    query: str,
    term_groups: list[set[str]],
    source_ids: list[str] | None,
) -> bool:
    if source_ids:
        return False
    if any(term in GITHUB_IDENTITY_TERMS for group in term_groups for term in group):
        return False
    if query_has_strong_repository_signal(query, term_groups):
        return False
    return query_has_lowercase_repository_probe(term_groups)


def query_looks_like_repository_name(query: str) -> bool:
    if any(separator in query for separator in ("/", "-", "_")):
        return True
    return bool(re.search(r"[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*", query))


def query_looks_like_repository_name_from_groups(term_groups: list[set[str]]) -> bool:
    return bool(repository_lookup_terms_from_groups(term_groups))


def query_has_strong_repository_signal(query: str, term_groups: list[set[str]]) -> bool:
    if not repository_lookup_terms_from_groups(term_groups):
        return False
    if re.search(r"[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*", query):
        return True
    for raw_token in TOKEN_RE.findall(query):
        token = raw_token.strip("_-/")
        if token_looks_like_api_path(raw_token):
            continue
        if "/" in raw_token or "_" in raw_token:
            return len(token) >= 3
        if "-" in raw_token and any(char.isdigit() for char in raw_token):
            return len(token) >= 3
    return False


def query_has_lowercase_repository_probe(term_groups: list[set[str]]) -> bool:
    repo_terms = repository_lookup_terms_from_groups(term_groups)
    if len(repo_terms) != 1:
        return False
    term = next(iter(repo_terms))
    return bool(re.fullmatch(r"[a-z0-9]{10,}", term))


def repository_lookup_terms_from_groups(term_groups: list[set[str]]) -> set[str]:
    terms = set()
    for group in term_groups:
        if group.intersection(DOCUMENT_INTENT_TERMS):
            continue
        for term in group:
            if is_generic_single_token_term(term):
                continue
            if token_looks_like_api_path(term):
                continue
            if "/" in term or "_" in term or "-" in term:
                if len(term.strip("_-/")) >= 3:
                    terms.add(term)
            elif re.fullmatch(r"[a-z0-9][a-z0-9]{9,}", term):
                terms.add(term)
    return terms


def is_generic_single_token_term(term: str) -> bool:
    return term in GENERIC_SINGLE_TOKEN_TERMS or term.endswith(GENERIC_SINGLE_TOKEN_SUFFIXES)


def token_looks_like_api_path(token: str) -> bool:
    normalized = token.strip("_-/").lower()
    if normalized.startswith("api/"):
        return True
    return bool(re.search(r"(^|/)v\d+($|/)", normalized))


def preview(text: str, length: int = 240) -> str:
    return text if len(text) <= length else text[:length].rstrip() + "..."


class ContextCandidateRanker:
    def __init__(self, metadata_store: MetadataStore, config: AppConfig):
        self.metadata_store = metadata_store
        self.config = config

    def keyword_candidates(
        self,
        query: str,
        documents: Iterable[DocumentModel],
        top_k: int,
        source_ids: list[str] | None,
        term_groups: list[set[str]] | None = None,
    ) -> list[dict[str, Any]]:
        term_groups = term_groups or query_term_groups(query)
        scoring_groups = scoring_term_groups(term_groups)
        github_anchor_groups = github_metadata_anchor_groups(
            query,
            term_groups,
        )
        candidates: list[dict[str, Any]] = []
        for document in documents:
            if source_ids and document.source_id not in source_ids:
                continue
            haystack = document_haystack(document)
            metadata_haystack = document_metadata_haystack(document)
            if (
                self.is_github_document(document)
                and github_anchor_groups
                and not all(
                    any(term in metadata_haystack for term in term_group)
                    for term_group in github_anchor_groups
                )
            ):
                continue
            has_document_intent = any(
                term_group.intersection(DOCUMENT_INTENT_TERMS) for term_group in scoring_groups
            )
            is_document_like_match = self.is_document_like(document, metadata_haystack)
            if has_document_intent and self.is_github_document(document) and not is_document_like_match:
                continue
            matches = sum(
                1
                for term_group in scoring_groups
                if term_group_matches(
                    term_group,
                    haystack,
                    metadata_haystack,
                    is_document_like_match,
                )
            )
            if matches == 0:
                continue
            body_haystack = (document.content or "").lower()
            body_matches = sum(
                1
                for term_group in scoring_groups
                if any(term in body_haystack for term in term_group)
            )
            score = matches / max(len(scoring_groups), 1)
            if self.is_github_document(document) and not is_document_like_match and body_matches == 0:
                score = min(score, 0.49)
            candidates.append(
                {
                    "chunk_id": document.chunk_id or document.id,
                    "source_id": document.source_id,
                    "score": score,
                }
            )
        candidates.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
        return candidates[:top_k]

    def metadata_fallback_candidates(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
        term_groups: list[set[str]],
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not term_groups:
            return []

        fallback_source_ids = self.metadata_fallback_source_ids(
            query,
            term_groups,
            source_ids,
        )
        metadata_terms = metadata_lookup_terms(
            term_groups,
            fallback_source_ids,
        ) or ordinary_metadata_lookup_terms(
            term_groups,
        ) or document_intent_metadata_lookup_terms(term_groups)
        metadata_only_terms = None
        if allows_github_document_body_lookup(term_groups) and (
            not fallback_source_ids or self.source_ids_include_github(fallback_source_ids)
        ):
            identity_terms, topical_terms = strong_anchor_lookup_terms(term_groups)
            if identity_terms and topical_terms:
                metadata_only_terms = identity_terms
                metadata_terms = topical_terms
        if len(candidates) >= top_k and not metadata_terms and not metadata_only_terms:
            return []

        metadata_candidates = self.metadata_keyword_candidates(
            query,
            top_k,
            fallback_source_ids,
            term_groups=term_groups,
            metadata_terms=metadata_terms,
            require_all_metadata_terms=requires_all_metadata_lookup_terms(term_groups),
            require_document_like=self.requires_document_like_metadata_lookup(
                term_groups,
                fallback_source_ids,
            ),
            prefer_document_like=self.prefers_document_like_metadata_lookup(
                query,
                term_groups,
                fallback_source_ids,
            ),
            include_text=self.includes_text_in_metadata_lookup(
                query,
                term_groups,
                fallback_source_ids,
            ),
            metadata_only_terms=metadata_only_terms,
        )
        if (
            should_try_lowercase_github_probe(query, term_groups, source_ids)
            and (
                len(candidates) < top_k
                or not self.candidates_have_textual_matches(candidates, term_groups)
            )
        ):
            all_source_metadata_candidates = self.metadata_keyword_candidates(
                query,
                max(top_k * 4, top_k + len(metadata_candidates), 10),
                None,
                term_groups=term_groups,
                metadata_terms=(
                    metadata_lookup_terms(term_groups, None)
                    or ordinary_metadata_lookup_terms(term_groups)
                    or document_intent_metadata_lookup_terms(term_groups)
                ),
                require_all_metadata_terms=requires_all_metadata_lookup_terms(
                    term_groups,
                ),
                require_document_like=self.requires_document_like_metadata_lookup(
                    term_groups,
                    None,
                ),
                prefer_document_like=False,
                include_text=True,
            )
            seen_metadata_chunk_ids = {
                candidate["chunk_id"] for candidate in metadata_candidates
            }
            metadata_candidates.extend(
                candidate
                for candidate in all_source_metadata_candidates
                if candidate["chunk_id"] not in seen_metadata_chunk_ids
            )
        return metadata_candidates

    def metadata_keyword_candidates(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
        *,
        term_groups: list[set[str]] | None = None,
        metadata_terms: set[str] | None = None,
        require_all_metadata_terms: bool = False,
        require_document_like: bool = False,
        prefer_document_like: bool = False,
        include_text: bool | None = None,
        metadata_only_terms: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        term_groups = term_groups or query_term_groups(query)
        if not term_groups and not metadata_terms:
            return []
        github_source_ids = self.github_source_ids(source_ids)
        if source_ids and github_source_ids and len(source_ids) > len(github_source_ids) and include_text is False:
            non_github_source_ids = [
                source_id for source_id in source_ids if source_id not in github_source_ids
            ]
            github_candidates = self.metadata_keyword_candidates(
                query,
                top_k,
                list(github_source_ids),
                term_groups=term_groups,
                metadata_terms=metadata_terms,
                require_all_metadata_terms=require_all_metadata_terms,
                require_document_like=require_document_like,
                prefer_document_like=prefer_document_like,
                include_text=False,
                metadata_only_terms=metadata_only_terms,
            )
            non_github_candidates = self.metadata_keyword_candidates(
                query,
                top_k,
                non_github_source_ids,
                term_groups=term_groups,
                metadata_terms=metadata_terms,
                require_all_metadata_terms=require_all_metadata_terms,
                require_document_like=require_document_like,
                prefer_document_like=False,
                include_text=True,
                metadata_only_terms=None,
            )
            return self.merge_ranked_candidates(
                [],
                [*github_candidates, *non_github_candidates],
                set(),
                top_k,
            )
        if prefer_document_like:
            chunks = self.metadata_chunks(
                source_ids,
                metadata_terms,
                True,
                include_text=bool(include_text),
                require_all_terms=require_all_metadata_terms,
                metadata_only_terms=metadata_only_terms,
            )
            if chunks is not None:
                documents = [
                    chunk.to_document_model(platform=self.document_platform(chunk.source_id))
                    for chunk in chunks
                ]
                candidates = self.keyword_candidates(query, documents, top_k, source_ids, term_groups)
                if candidates:
                    return candidates
        if include_text is None:
            include_text = not (
                metadata_terms
                and (
                    metadata_terms_are_repo_or_identity(metadata_terms)
                    or bool(github_source_ids)
                )
            )
        chunks = self.metadata_chunks(
            source_ids,
            metadata_terms,
            require_document_like,
            include_text,
            require_all_metadata_terms,
            metadata_only_terms=metadata_only_terms,
        )
        if chunks is None:
            return []
        documents = [
            chunk.to_document_model(platform=self.document_platform(chunk.source_id))
            for chunk in chunks
        ]
        return self.keyword_candidates(query, documents, top_k, source_ids, term_groups)

    def metadata_chunks(
        self,
        source_ids: list[str] | None,
        metadata_terms: set[str] | None,
        require_document_like: bool,
        include_text: bool = False,
        require_all_terms: bool = False,
        metadata_only_terms: set[str] | None = None,
    ) -> list[ChunkModel] | None:
        if metadata_terms or metadata_only_terms:
            list_matching = getattr(self.metadata_store, "list_chunks_matching_metadata_terms", None)
            if callable(list_matching):
                base_limit = int(getattr(self.config, "similarity_top_k", 10))
                kwargs: dict[str, Any] = {
                    "limit": max(base_limit * 50, 200),
                    "require_document_like": require_document_like,
                    "include_text": include_text,
                    "require_all_terms": require_all_terms,
                }
                if metadata_only_terms:
                    kwargs["metadata_only_terms"] = metadata_only_terms
                return list_matching(
                    metadata_terms or [],
                    source_ids,
                    **kwargs,
                )
        list_chunks = getattr(self.metadata_store, "list_chunks", None)
        if not callable(list_chunks):
            return None
        return list_chunks(source_ids)

    def merge_ranked_candidates(
        self,
        vector_candidates: list[dict[str, Any]],
        metadata_candidates: list[dict[str, Any]],
        rejected_chunk_ids: set[str],
        top_k: int,
    ) -> list[dict[str, Any]]:
        ranked: dict[str, dict[str, Any]] = {}
        order: dict[str, int] = {}

        for index, candidate in enumerate(vector_candidates):
            chunk_id = candidate["chunk_id"]
            ranked_candidate = dict(candidate)
            ranked_candidate.setdefault(
                "vector_score",
                float(candidate.get("vector_score", candidate.get("score", 0.0))),
            )
            ranked[chunk_id] = ranked_candidate
            order.setdefault(chunk_id, index)

        metadata_offset = len(order)
        for index, candidate in enumerate(metadata_candidates):
            chunk_id = candidate["chunk_id"]
            score = float(candidate["score"])
            if chunk_id in rejected_chunk_ids and not (
                self.is_github_source_id(str(candidate.get("source_id") or "")) and score >= 1.0
            ):
                continue
            boost_priority = 1 if score >= 1.0 else 0
            boosted_score = max(score, 1.0) if boost_priority else score
            boosted = {
                **candidate,
                "score": boosted_score,
                "metadata_priority": boost_priority,
                "vector_score": float(candidate.get("vector_score", 0.0) or 0.0),
            }
            if chunk_id not in ranked:
                ranked[chunk_id] = boosted
            elif boost_priority > int(ranked[chunk_id].get("metadata_priority", 0)):
                ranked[chunk_id] = {
                    **ranked[chunk_id],
                    **boosted,
                    "score": max(float(ranked[chunk_id]["score"]), boosted["score"]),
                    "metadata_priority": boost_priority,
                    "vector_score": float(
                        ranked[chunk_id].get("vector_score", boosted.get("vector_score", 0.0)) or 0.0
                    ),
                }
            elif boosted["score"] > float(ranked[chunk_id]["score"]):
                ranked[chunk_id] = {
                    **boosted,
                    "vector_score": float(
                        ranked[chunk_id].get("vector_score", boosted.get("vector_score", 0.0)) or 0.0
                    ),
                }
            order.setdefault(chunk_id, metadata_offset + index)

        return sorted(
            ranked.values(),
            key=lambda item: (
                -int(item.get("metadata_priority", 0)),
                -float(item["score"]),
                order[item["chunk_id"]],
            ),
        )[:top_k]

    def candidates_have_textual_matches(
        self,
        candidates: list[dict[str, Any]],
        term_groups: list[set[str]],
    ) -> bool:
        concrete_groups = [
            term_group
            for term_group in scoring_term_groups(term_groups)
            if not term_group.intersection(DOCUMENT_INTENT_TERMS)
        ]
        if not candidates or not concrete_groups:
            return False
        for candidate in candidates:
            chunk = self.metadata_store.get_chunk(candidate["chunk_id"])
            if not chunk:
                return False
            document = chunk.to_document_model(platform=self.document_platform(chunk.source_id))
            text = " ".join(
                [
                    document_haystack(document),
                    document_metadata_haystack(document),
                ]
            ).lower()
            if not any(
                any(term in text for term in term_group)
                for term_group in concrete_groups
            ):
                return False
        return True

    def rerank_candidates(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        term_groups: list[set[str]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        scoring_groups = scoring_term_groups(term_groups)
        preferred_phrases = preferred_query_phrases(query, term_groups)
        source_type_terms = query_source_type_terms(term_groups)
        reranked = []
        for order, candidate in enumerate(candidates):
            chunk = self.metadata_store.get_chunk(candidate["chunk_id"])
            if not chunk:
                continue
            document = chunk.to_document_model(platform=self.document_platform(chunk.source_id))
            haystack = document_haystack(document)
            metadata_haystack = document_metadata_haystack(document)
            is_document_like_match = self.is_document_like(document, metadata_haystack)
            match_count = sum(
                1
                for term_group in scoring_groups
                if term_group_matches(
                    term_group,
                    haystack,
                    metadata_haystack,
                    is_document_like_match,
                )
            )
            metadata_match_count = sum(
                1
                for term_group in scoring_groups
                if any(term in metadata_haystack for term in term_group)
            )
            rerank_score = float(candidate.get("score", 0.0))
            rerank_score += match_count * 0.12
            rerank_score += metadata_match_count * 0.08
            if preferred_phrases:
                rerank_score += phrase_match_bonus(preferred_phrases, metadata_haystack)
            if source_type_terms and self.document_matches_source_type_terms(document, source_type_terms):
                rerank_score += 0.12
            if (
                any(group.intersection(DOCUMENT_INTENT_TERMS) for group in scoring_groups)
                and self.is_github_document(document)
                and is_document_like_match
            ):
                rerank_score += 0.05
            if any(group.intersection(STRONG_ANCHOR_TERMS) for group in scoring_groups) and any(
                term in metadata_haystack
                for group in scoring_groups
                if group.intersection(STRONG_ANCHOR_TERMS)
                for term in group
            ):
                rerank_score += 0.1
            reranked.append(
                {
                    **candidate,
                    "vector_score": float(candidate.get("vector_score", candidate.get("score", 0.0))),
                    "score": rerank_score,
                    "rerank_score": rerank_score,
                    "_order": order,
                }
            )
        reranked.sort(
            key=lambda item: (
                -float(item.get("rerank_score", item.get("score", 0.0))),
                -float(item.get("score", 0.0)),
                item["_order"],
            )
        )
        return [
            {key: value for key, value in item.items() if key != "_order"}
            for item in reranked[:top_k]
        ]

    def metadata_fallback_source_ids(
        self,
        query: str,
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> list[str] | None:
        if source_ids:
            return source_ids
        github_source_ids = sorted(self.github_source_ids())
        if any(term in GITHUB_IDENTITY_TERMS for group in term_groups for term in group):
            return github_source_ids or ["source_github"]
        if (
            repository_lookup_terms_from_groups(term_groups)
            and query_has_strong_repository_signal(query, term_groups)
        ):
            return github_source_ids or ["source_github"]
        if query_has_lowercase_repository_probe(term_groups):
            return github_source_ids or ["source_github"]
        return None

    def document_matches_source_type_terms(
        self,
        document: DocumentModel,
        source_type_terms: set[str],
    ) -> bool:
        source = self.metadata_store.get_source(document.source_id) if document.source_id else None
        actual_source_type = (
            getattr(getattr(source, "source_type", None), "value", "") or ""
        ).lower()
        if source is not None:
            return bool(actual_source_type and actual_source_type in source_type_terms)
        source_id = (document.source_id or "").lower()
        if source_id == "source_github" and "github" in source_type_terms:
            return True
        return any(term and term in source_id for term in source_type_terms)

    def includes_text_in_metadata_lookup(
        self,
        query: str,
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> bool:
        if source_ids and not self.source_ids_include_github(source_ids):
            return True
        if allows_github_document_body_lookup(term_groups):
            return True
        if source_ids and self.source_ids_include_github(source_ids):
            return False
        if query_has_strong_repository_signal(query, term_groups):
            return False
        if query_has_lowercase_repository_probe(term_groups):
            return False
        return True

    def requires_document_like_metadata_lookup(
        self,
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> bool:
        if source_ids and not self.source_ids_include_github(source_ids):
            return False
        return any(group.intersection(DOCUMENT_INTENT_TERMS) for group in term_groups)

    def prefers_document_like_metadata_lookup(
        self,
        query: str,
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> bool:
        if not source_ids or not self.source_ids_include_github(source_ids):
            return False
        if any(group.intersection(DOCUMENT_INTENT_TERMS) for group in term_groups):
            return False
        return (
            bool(repository_lookup_terms_from_groups(term_groups))
            and (
                query_has_strong_repository_signal(query, term_groups)
                or query_has_lowercase_repository_probe(term_groups)
            )
        )

    def document_intent_allows_chunk(self, term_groups: list[set[str]], chunk: ChunkModel) -> bool:
        has_document_intent = any(
            term_group.intersection(DOCUMENT_INTENT_TERMS) for term_group in term_groups
        )
        if not has_document_intent or not self.is_github_source_id(chunk.source_id):
            return True
        document = chunk.to_document_model(platform=self.document_platform(chunk.source_id))
        metadata_haystack = document_metadata_haystack(document)
        return self.is_document_like(document, metadata_haystack)

    def github_source_ids(self, source_ids: list[str] | None = None) -> set[str]:
        candidate_ids = list(source_ids or [])
        if not candidate_ids:
            list_sources = getattr(self.metadata_store, "list_sources", None)
            if callable(list_sources):
                candidate_ids = [
                    source.source_id
                    for source in list_sources()
                    if (
                        getattr(getattr(source, "source_type", None), "value", "")
                        or str(getattr(source, "source_type", ""))
                    ).lower()
                    == "github"
                ]
        github_ids = {source_id for source_id in candidate_ids if self.is_github_source_id(source_id)}
        if not github_ids and (not source_ids or "source_github" in source_ids):
            source = self.metadata_store.get_source("source_github")
            if source is not None or not source_ids:
                github_ids.add("source_github")
        return github_ids

    def source_ids_include_github(self, source_ids: list[str] | None) -> bool:
        return bool(source_ids and self.github_source_ids(source_ids))

    def is_github_source_id(self, source_id: str) -> bool:
        normalized = str(source_id or "")
        if not normalized:
            return False
        source = self.metadata_store.get_source(normalized)
        source_type = (
            getattr(getattr(source, "source_type", None), "value", "")
            or str(getattr(source, "source_type", ""))
        ).lower()
        if source_type:
            return source_type == "github"
        return normalized == "source_github"

    def is_github_document(self, document: DocumentModel) -> bool:
        if self.is_github_source_id(document.source_id):
            return True
        return str(document.platform or "").lower() == "github"

    def is_document_like(self, document: DocumentModel, metadata_haystack: str) -> bool:
        return is_document_like(
            document,
            metadata_haystack,
            is_github=self.is_github_document(document),
        )

    def document_platform(self, source_id: str) -> str:
        if self.is_github_source_id(source_id):
            return "github"
        source = self.metadata_store.get_source(source_id)
        source_type = getattr(getattr(source, "source_type", None), "value", "") or ""
        return str(source_type)
