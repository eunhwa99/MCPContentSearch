import re
from collections.abc import Callable, Iterable
from typing import Any

from llama_index.core.retrievers import VectorIndexRetriever
from llama_index.core.vector_stores import FilterOperator, MetadataFilter, MetadataFilters

from core.models import ChunkModel, ContextSearchResult, DocumentModel
from environments.config import AppConfig
from storage.metadata_store import MetadataStore


KOREAN_QUERY_TERM_EXPANSIONS = {
    "깃허브": {"github"},
    "그래프": {"graph"},
    "니트코드": {"neetcode"},
    "문서": {"document", "documents", "docs"},
    "알고리즘": {"algorithm", "algorithms"},
}
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
TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣_/-]+")
DOCUMENT_INTENT_TERMS = {"doc", "docs", "document", "documents", "문서"}
BROAD_TOPIC_TERMS = {"algorithm", "algorithms", "알고리즘"}
STRONG_ANCHOR_TERMS = {"neetcode", "니트코드"}
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


class ContextSearchService:
    """Structured citation search over indexed chunks."""

    def __init__(
        self,
        metadata_store: MetadataStore,
        indexer=None,
        config: AppConfig | None = None,
        retriever: Callable | Iterable[DocumentModel] | None = None,
    ):
        self.metadata_store = metadata_store
        self.indexer = indexer
        self.config = config or AppConfig()
        self.retriever = retriever

    async def search_context(self, query: str, filters: dict | None = None, top_k: int = 10) -> dict:
        filters = filters or {}
        source_ids = self._normalize_source_ids(filters)
        candidates = self._retrieve_candidates(query, top_k, source_ids)
        results = []

        for candidate in candidates:
            chunk_id = candidate["chunk_id"]
            chunk = self.metadata_store.get_chunk(chunk_id)
            if not chunk:
                continue
            if source_ids and chunk.source_id not in source_ids:
                continue

            source = self.metadata_store.get_source(chunk.source_id)
            source_type = source.source_type.value if source else ""
            results.append(
                ContextSearchResult(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    source_id=chunk.source_id,
                    source_type=source_type,
                    title=chunk.title,
                    url=chunk.url,
                    path=chunk.path,
                    score=candidate["score"],
                    preview=self._preview(chunk.text),
                    text=chunk.text,
                    line_start=chunk.line_start,
                    line_end=chunk.line_end,
                    version_id=chunk.version_id,
                    updated_at=chunk.updated_at,
                )
            )
            if len(results) >= top_k:
                break

        return {"query": query, "results": results}

    @staticmethod
    def _normalize_source_ids(filters: dict) -> list[str] | None:
        normalized = []

        for key in ("source_ids", "source_id"):
            value = filters.get(key)
            if not value:
                continue
            if isinstance(value, str):
                values = [value]
            elif isinstance(value, Iterable):
                values = list(value)
            else:
                values = [value]

            for source_id in values:
                if source_id and source_id not in normalized:
                    normalized.append(str(source_id))

        return normalized or None

    def _retrieve_candidates(self, query: str, top_k: int, source_ids: list[str] | None) -> list[dict[str, Any]]:
        if self.retriever is not None:
            if callable(self.retriever):
                return list(self.retriever(query, top_k, source_ids))
            return self._keyword_candidates(query, self.retriever, top_k, source_ids)

        term_groups = self._query_term_groups(query)
        if self.indexer is None:
            if not term_groups:
                return []
            return self._metadata_fallback_candidates(
                query,
                top_k,
                source_ids,
                term_groups,
                [],
            )

        index = self.indexer.get_or_create_index()
        base_limit = max(top_k, top_k * self.config.search_multiplier)
        max_limit = self._max_retrieval_limit(base_limit)
        seen = set()
        rejected = set()
        candidates = []
        limit = base_limit

        while limit <= max_limit:
            retriever = VectorIndexRetriever(
                index=index,
                similarity_top_k=limit,
                vector_store_query_mode="hybrid",
                filters=self._metadata_filters(source_ids),
            )
            nodes = retriever.retrieve(query)
            for node in nodes:
                chunk_id = node.metadata.get("chunk_id") or node.metadata.get("doc_id")
                if not chunk_id or chunk_id in seen:
                    continue
                chunk = self.metadata_store.get_chunk(chunk_id)
                if not chunk:
                    continue
                if not self._managed_hit_matches_chunk(node.metadata, chunk):
                    rejected.add(chunk_id)
                    continue
                if source_ids and chunk.source_id not in source_ids:
                    rejected.add(chunk_id)
                    continue
                if not self._document_intent_allows_chunk(term_groups, chunk):
                    rejected.add(chunk_id)
                    continue
                rejected.discard(chunk_id)
                seen.add(chunk_id)
                candidates.append(
                    {
                        "chunk_id": chunk_id,
                        "score": float(node.score or 0.0),
                    }
                )
            if len(candidates) >= top_k:
                break
            if len(nodes) < limit:
                break
            next_limit = min(limit * 2, max_limit)
            if next_limit == limit:
                break
            limit = next_limit

        metadata_candidates = []
        lowercase_github_probe = self._should_try_lowercase_github_probe(
            query,
            term_groups,
            source_ids,
        )
        if self._should_run_metadata_fallback(
            query,
            term_groups,
            candidates,
            top_k,
            source_ids,
        ) and not (
            lowercase_github_probe
            and len(candidates) >= top_k
            and self._candidates_have_textual_matches(candidates, term_groups)
        ):
            metadata_candidates = self._metadata_fallback_candidates(
                query,
                top_k,
                source_ids,
                term_groups,
                candidates,
            )

        return self._merge_ranked_candidates(
            candidates,
            metadata_candidates,
            rejected,
            top_k,
        )

    def _metadata_fallback_candidates(
        self,
        query: str,
        top_k: int,
        source_ids: list[str] | None,
        term_groups: list[set[str]],
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not term_groups:
            return []

        fallback_source_ids = self._metadata_fallback_source_ids(
            query,
            term_groups,
            source_ids,
        )
        metadata_terms = self._metadata_lookup_terms(
            term_groups,
            fallback_source_ids,
        ) or self._ordinary_metadata_lookup_terms(
            term_groups,
        ) or self._document_intent_metadata_lookup_terms(term_groups)
        metadata_only_terms = None
        if self._allows_github_document_body_lookup(term_groups) and (
            not fallback_source_ids or "source_github" in fallback_source_ids
        ):
            identity_terms, topical_terms = self._strong_anchor_lookup_terms(term_groups)
            if identity_terms and topical_terms:
                metadata_only_terms = identity_terms
                metadata_terms = topical_terms
        if len(candidates) >= top_k and not metadata_terms and not metadata_only_terms:
            return []

        metadata_candidates = self._metadata_keyword_candidates(
            query,
            top_k,
            fallback_source_ids,
            term_groups=term_groups,
            metadata_terms=metadata_terms,
            require_all_metadata_terms=self._requires_all_metadata_lookup_terms(term_groups),
            require_document_like=self._requires_document_like_metadata_lookup(
                term_groups,
                fallback_source_ids,
            ),
            prefer_document_like=self._prefers_document_like_metadata_lookup(
                query,
                term_groups,
                fallback_source_ids,
            ),
            include_text=self._includes_text_in_metadata_lookup(
                query,
                term_groups,
                fallback_source_ids,
            ),
            metadata_only_terms=metadata_only_terms,
        )
        if (
            self._should_try_lowercase_github_probe(query, term_groups, source_ids)
            and len(candidates) < top_k
        ):
            all_source_metadata_candidates = self._metadata_keyword_candidates(
                query,
                max(top_k * 4, top_k + len(metadata_candidates), 10),
                None,
                term_groups=term_groups,
                metadata_terms=(
                    self._metadata_lookup_terms(term_groups, None)
                    or self._ordinary_metadata_lookup_terms(term_groups)
                    or self._document_intent_metadata_lookup_terms(term_groups)
                ),
                require_all_metadata_terms=self._requires_all_metadata_lookup_terms(
                    term_groups,
                ),
                require_document_like=self._requires_document_like_metadata_lookup(
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

    @staticmethod
    def _managed_hit_matches_chunk(metadata: dict[str, Any], chunk) -> bool:
        if metadata.get("contextwiki_managed") != "true":
            return False
        source_id = metadata.get("source_id")
        document_id = metadata.get("document_id")
        if source_id != chunk.source_id:
            return False
        if document_id != chunk.document_id:
            return False
        return True

    @staticmethod
    def _metadata_filters(source_ids: list[str] | None):
        filters = [MetadataFilter(key="contextwiki_managed", value="true")]
        if not source_ids:
            return MetadataFilters(filters=filters)
        if len(source_ids) == 1:
            filters.append(MetadataFilter(key="source_id", value=source_ids[0]))
        else:
            filters.append(
                MetadataFilter(
                    key="source_id",
                    value=source_ids,
                    operator=FilterOperator.IN,
                )
            )
        return MetadataFilters(filters=filters)

    @staticmethod
    def _keyword_candidates(
        query: str,
        documents: Iterable[DocumentModel],
        top_k: int,
        source_ids: list[str] | None,
        term_groups: list[set[str]] | None = None,
    ) -> list[dict[str, Any]]:
        term_groups = term_groups or ContextSearchService._query_term_groups(query)
        scoring_groups = ContextSearchService._scoring_term_groups(term_groups)
        github_anchor_groups = ContextSearchService._github_metadata_anchor_groups(
            query,
            term_groups,
        )
        candidates = []
        for document in documents:
            if source_ids and document.source_id not in source_ids:
                continue
            haystack = ContextSearchService._document_haystack(document)
            metadata_haystack = ContextSearchService._document_metadata_haystack(document)
            if (
                document.source_id == "source_github"
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
            is_document_like = ContextSearchService._is_document_like(document, metadata_haystack)
            if has_document_intent and document.source_id == "source_github" and not is_document_like:
                continue
            matches = sum(
                1
                for term_group in scoring_groups
                if ContextSearchService._term_group_matches(
                    term_group,
                    haystack,
                    metadata_haystack,
                    is_document_like,
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
            if document.source_id == "source_github" and not is_document_like and body_matches == 0:
                score = min(score, 0.49)
            candidates.append(
                {
                    "chunk_id": document.chunk_id or document.id,
                    "source_id": document.source_id,
                    "score": score,
                }
            )
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[:top_k]

    def _metadata_keyword_candidates(
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
        term_groups = term_groups or self._query_term_groups(query)
        if not term_groups and not metadata_terms:
            return []
        if source_ids and "source_github" in source_ids and len(source_ids) > 1 and include_text is False:
            non_github_source_ids = [
                source_id for source_id in source_ids if source_id != "source_github"
            ]
            github_candidates = self._metadata_keyword_candidates(
                query,
                top_k,
                ["source_github"],
                term_groups=term_groups,
                metadata_terms=metadata_terms,
                require_all_metadata_terms=require_all_metadata_terms,
                require_document_like=require_document_like,
                prefer_document_like=prefer_document_like,
                include_text=False,
                metadata_only_terms=metadata_only_terms,
            )
            non_github_candidates = self._metadata_keyword_candidates(
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
            return self._merge_ranked_candidates(
                [],
                [*github_candidates, *non_github_candidates],
                set(),
                top_k,
            )
        if prefer_document_like:
            chunks = self._metadata_chunks(
                source_ids,
                metadata_terms,
                True,
                include_text=bool(include_text),
                require_all_terms=require_all_metadata_terms,
                metadata_only_terms=metadata_only_terms,
            )
            if chunks is not None:
                documents = [chunk.to_document_model() for chunk in chunks]
                candidates = self._keyword_candidates(query, documents, top_k, source_ids, term_groups)
                if candidates:
                    return candidates
        if include_text is None:
            include_text = not (
                metadata_terms
                and (
                    self._metadata_terms_are_repo_or_identity(metadata_terms)
                    or (source_ids and "source_github" in source_ids)
                )
            )
        chunks = self._metadata_chunks(
            source_ids,
            metadata_terms,
            require_document_like,
            include_text,
            require_all_metadata_terms,
            metadata_only_terms=metadata_only_terms,
        )
        if chunks is None:
            return []
        documents = [chunk.to_document_model() for chunk in chunks]
        return self._keyword_candidates(query, documents, top_k, source_ids, term_groups)

    def _metadata_chunks(
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
                kwargs = {
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

    @staticmethod
    def _metadata_terms_are_repo_or_identity(metadata_terms: set[str]) -> bool:
        return any(
            term in GITHUB_IDENTITY_TERMS
            or "/" in term
            or "_" in term
            or "-" in term
            for term in metadata_terms
        )

    @staticmethod
    def _merge_ranked_candidates(
        vector_candidates: list[dict[str, Any]],
        metadata_candidates: list[dict[str, Any]],
        rejected_chunk_ids: set[str],
        top_k: int,
    ) -> list[dict[str, Any]]:
        ranked = {}
        order = {}

        for index, candidate in enumerate(vector_candidates):
            chunk_id = candidate["chunk_id"]
            ranked[chunk_id] = dict(candidate)
            order.setdefault(chunk_id, index)

        metadata_offset = len(order)
        for index, candidate in enumerate(metadata_candidates):
            chunk_id = candidate["chunk_id"]
            score = float(candidate["score"])
            if chunk_id in rejected_chunk_ids and not (
                candidate.get("source_id") == "source_github" and score >= 1.0
            ):
                continue
            boost_priority = 1 if score >= 1.0 else 0
            boosted_score = max(score, 1.0) if boost_priority else score
            boosted = {
                **candidate,
                "score": boosted_score,
                "metadata_priority": boost_priority,
            }
            if chunk_id not in ranked:
                ranked[chunk_id] = boosted
            elif boost_priority > int(ranked[chunk_id].get("metadata_priority", 0)):
                ranked[chunk_id] = {
                    **ranked[chunk_id],
                    **boosted,
                    "score": max(float(ranked[chunk_id]["score"]), boosted["score"]),
                    "metadata_priority": boost_priority,
                }
            elif boosted["score"] > float(ranked[chunk_id]["score"]):
                ranked[chunk_id] = boosted
            order.setdefault(chunk_id, metadata_offset + index)

        return sorted(
            ranked.values(),
            key=lambda item: (
                -int(item.get("metadata_priority", 0)),
                -float(item["score"]),
                order[item["chunk_id"]],
            ),
        )[:top_k]

    def _candidates_have_textual_matches(
        self,
        candidates: list[dict[str, Any]],
        term_groups: list[set[str]],
    ) -> bool:
        concrete_groups = [
            term_group
            for term_group in self._scoring_term_groups(term_groups)
            if not term_group.intersection(DOCUMENT_INTENT_TERMS)
        ]
        if not candidates or not concrete_groups:
            return False
        for candidate in candidates:
            chunk = self.metadata_store.get_chunk(candidate["chunk_id"])
            if not chunk:
                return False
            text = " ".join([chunk.title or "", chunk.text or ""]).lower()
            if not any(
                any(term in text for term in term_group)
                for term_group in concrete_groups
            ):
                return False
        return True

    @staticmethod
    def _document_haystack(document: DocumentModel) -> str:
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

    @staticmethod
    def _document_metadata_haystack(document: DocumentModel) -> str:
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

    @staticmethod
    def _is_document_like(document: DocumentModel, metadata_haystack: str) -> bool:
        if document.source_id != "source_github":
            return True
        return bool(DOCUMENT_LIKE_PATH_RE.search(metadata_haystack))

    @staticmethod
    def _document_intent_allows_chunk(term_groups: list[set[str]], chunk: ChunkModel) -> bool:
        has_document_intent = any(
            term_group.intersection(DOCUMENT_INTENT_TERMS) for term_group in term_groups
        )
        if not has_document_intent or chunk.source_id != "source_github":
            return True
        document = chunk.to_document_model()
        metadata_haystack = ContextSearchService._document_metadata_haystack(document)
        return ContextSearchService._is_document_like(document, metadata_haystack)

    @staticmethod
    def _term_group_matches(
        term_group: set[str],
        haystack: str,
        metadata_haystack: str,
        is_document_like: bool,
    ) -> bool:
        if any(term in haystack for term in term_group):
            return True
        return bool(
            is_document_like
            and term_group.intersection(DOCUMENT_INTENT_TERMS)
            and DOCUMENT_LIKE_PATH_RE.search(metadata_haystack)
        )

    @staticmethod
    def _query_term_groups(query: str) -> list[set[str]]:
        groups = []
        seen = set()
        for raw_token in TOKEN_RE.findall(query.lower()):
            for raw_term in ContextSearchService._split_attached_latin_korean_token(raw_token):
                ContextSearchService._append_query_term_group(raw_term, groups, seen)
        return groups

    @staticmethod
    def _append_query_term_group(raw_term: str, groups: list[set[str]], seen: set[tuple[str, ...]]):
        candidates = {raw_term}
        matched_korean_terms = []
        for korean_term, expansions in KOREAN_QUERY_TERM_EXPANSIONS.items():
            if korean_term in raw_term:
                matched_korean_terms.append(korean_term)
                candidates.update(expansions)
                if korean_term != raw_term:
                    candidates.add(korean_term)
        if len(matched_korean_terms) > 1:
            for korean_term in matched_korean_terms:
                cls_candidates = {korean_term, *KOREAN_QUERY_TERM_EXPANSIONS[korean_term]}
                normalized = {
                    candidate.strip("_-/")
                    for candidate in cls_candidates
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

    @staticmethod
    def _split_attached_latin_korean_token(raw_token: str) -> list[str]:
        match = re.fullmatch(r"([0-9a-z_/-]+)([가-힣]+)", raw_token)
        if not match:
            return [raw_token]
        latin, korean = match.groups()
        return [latin, korean]

    @staticmethod
    def _scoring_term_groups(term_groups: list[set[str]]) -> list[set[str]]:
        if not any(group.intersection(STRONG_ANCHOR_TERMS) for group in term_groups):
            return term_groups
        narrowed = [
            group
            for group in term_groups
            if not group.intersection(BROAD_TOPIC_TERMS)
        ]
        return narrowed or term_groups

    @staticmethod
    def _should_run_metadata_fallback(
        query: str,
        term_groups: list[set[str]],
        candidates: list[dict[str, Any]],
        top_k: int,
        source_ids: list[str] | None,
    ) -> bool:
        if len(candidates) < top_k:
            return True
        if source_ids:
            return len(candidates) < top_k or ContextSearchService._query_is_metadata_like(
                query,
                term_groups,
            )
        if any(term in GITHUB_IDENTITY_TERMS for group in term_groups for term in group):
            return True
        return ContextSearchService._query_is_metadata_like(query, term_groups)

    @staticmethod
    def _metadata_fallback_source_ids(
        query: str,
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> list[str] | None:
        if source_ids:
            return source_ids
        if any(term in GITHUB_IDENTITY_TERMS for group in term_groups for term in group):
            return ["source_github"]
        if (
            ContextSearchService._repository_lookup_terms_from_groups(term_groups)
            and ContextSearchService._query_has_strong_repository_signal(query, term_groups)
        ):
            return ["source_github"]
        if ContextSearchService._query_has_lowercase_repository_probe(term_groups):
            return ["source_github"]
        return None

    @staticmethod
    def _metadata_lookup_terms(
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> set[str] | None:
        identity_terms = set()
        for group in term_groups:
            identity_group = {
                term
                for term in group
                if term in GITHUB_IDENTITY_TERMS and term not in GENERIC_GITHUB_TERMS
            }
            identity_terms.update(ContextSearchService._preferred_metadata_terms(identity_group))
        repo_terms = ContextSearchService._repository_lookup_terms_from_groups(term_groups)
        topical_terms = ContextSearchService._metadata_lookup_topical_terms(term_groups)
        if identity_terms or repo_terms:
            return identity_terms | repo_terms | topical_terms
        return None

    @staticmethod
    def _github_metadata_anchor_groups(query: str, term_groups: list[set[str]]) -> list[set[str]]:
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
            ContextSearchService._query_has_strong_repository_signal(query, term_groups)
            or ContextSearchService._query_has_lowercase_repository_probe(term_groups)
        ):
            repo_terms = ContextSearchService._repository_lookup_terms_from_groups(term_groups)
        anchor_groups = []
        for group in term_groups:
            if group.intersection(STRONG_ANCHOR_TERMS):
                anchor_groups.append(group)
                continue
            if repo_terms and ContextSearchService._preferred_metadata_terms(group).intersection(repo_terms):
                anchor_groups.append(group)
        return anchor_groups

    @staticmethod
    def _ordinary_metadata_lookup_terms(term_groups: list[set[str]]) -> set[str] | None:
        terms = set()
        for group in ContextSearchService._scoring_term_groups(term_groups):
            if group.intersection(DOCUMENT_INTENT_TERMS):
                continue
            terms.update(group)
        return terms or None

    @staticmethod
    def _document_intent_metadata_lookup_terms(term_groups: list[set[str]]) -> set[str] | None:
        if not term_groups:
            return None
        terms = set()
        for group in ContextSearchService._scoring_term_groups(term_groups):
            if not group.intersection(DOCUMENT_INTENT_TERMS):
                return None
            terms.update(group.intersection(DOCUMENT_INTENT_TERMS))
        return terms or None

    @staticmethod
    def _preferred_metadata_terms(terms: set[str]) -> set[str]:
        ascii_terms = {term for term in terms if re.fullmatch(r"[a-z0-9_/-]+", term)}
        return ascii_terms or terms

    @staticmethod
    def _metadata_lookup_topical_terms(term_groups: list[set[str]]) -> set[str]:
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
            terms.update(ContextSearchService._preferred_metadata_terms(group))
        return terms

    @staticmethod
    def _strong_anchor_lookup_terms(term_groups: list[set[str]]) -> tuple[set[str], set[str]]:
        identity_terms = set()
        topical_terms = set()
        for group in term_groups:
            if group.intersection(STRONG_ANCHOR_TERMS):
                identity_terms.update(ContextSearchService._preferred_metadata_terms(group))
                continue
            if group.intersection(DOCUMENT_INTENT_TERMS) or group.intersection(BROAD_TOPIC_TERMS):
                continue
            topical_terms.update(ContextSearchService._preferred_metadata_terms(group))
        return identity_terms, topical_terms

    @staticmethod
    def _requires_all_metadata_lookup_terms(term_groups: list[set[str]]) -> bool:
        return bool(ContextSearchService._metadata_lookup_topical_terms(term_groups))

    @staticmethod
    def _allows_github_document_body_lookup(term_groups: list[set[str]]) -> bool:
        return (
            any(group.intersection(STRONG_ANCHOR_TERMS) for group in term_groups)
            and any(group.intersection(DOCUMENT_INTENT_TERMS) for group in term_groups)
            and bool(ContextSearchService._metadata_lookup_topical_terms(term_groups))
        )

    @staticmethod
    def _includes_text_in_metadata_lookup(
        query: str,
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> bool:
        if source_ids and "source_github" not in source_ids:
            return True
        if ContextSearchService._allows_github_document_body_lookup(term_groups):
            return True
        if source_ids and "source_github" in source_ids:
            return False
        if ContextSearchService._query_has_strong_repository_signal(query, term_groups):
            return False
        if ContextSearchService._query_has_lowercase_repository_probe(term_groups):
            return False
        return True

    @staticmethod
    def _requires_document_like_metadata_lookup(
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> bool:
        if source_ids and "source_github" not in source_ids:
            return False
        return any(group.intersection(DOCUMENT_INTENT_TERMS) for group in term_groups)

    @staticmethod
    def _prefers_document_like_metadata_lookup(
        query: str,
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> bool:
        if not source_ids or "source_github" not in source_ids:
            return False
        if any(group.intersection(DOCUMENT_INTENT_TERMS) for group in term_groups):
            return False
        return (
            bool(ContextSearchService._repository_lookup_terms_from_groups(term_groups))
            and (
                ContextSearchService._query_has_strong_repository_signal(query, term_groups)
                or ContextSearchService._query_has_lowercase_repository_probe(term_groups)
            )
        )

    @staticmethod
    def _query_is_metadata_like(query: str, term_groups: list[set[str]]) -> bool:
        if any(term in GITHUB_IDENTITY_TERMS for group in term_groups for term in group):
            return True
        if (
            ContextSearchService._repository_lookup_terms_from_groups(term_groups)
            and any(group.intersection(DOCUMENT_INTENT_TERMS) for group in term_groups)
        ):
            return True
        return (
            ContextSearchService._query_has_strong_repository_signal(query, term_groups)
            or ContextSearchService._query_has_lowercase_repository_probe(term_groups)
        )

    @staticmethod
    def _should_try_lowercase_github_probe(
        query: str,
        term_groups: list[set[str]],
        source_ids: list[str] | None,
    ) -> bool:
        if source_ids:
            return False
        if any(term in GITHUB_IDENTITY_TERMS for group in term_groups for term in group):
            return False
        if ContextSearchService._query_has_strong_repository_signal(query, term_groups):
            return False
        return ContextSearchService._query_has_lowercase_repository_probe(term_groups)

    @staticmethod
    def _query_looks_like_repository_name(query: str) -> bool:
        if any(separator in query for separator in ("/", "-", "_")):
            return True
        return bool(re.search(r"[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*", query))

    @staticmethod
    def _query_looks_like_repository_name_from_groups(term_groups: list[set[str]]) -> bool:
        return bool(ContextSearchService._repository_lookup_terms_from_groups(term_groups))

    @staticmethod
    def _query_has_strong_repository_signal(query: str, term_groups: list[set[str]]) -> bool:
        if not ContextSearchService._repository_lookup_terms_from_groups(term_groups):
            return False
        if re.search(r"[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*", query):
            return True
        for raw_token in TOKEN_RE.findall(query):
            token = raw_token.strip("_-/")
            if ContextSearchService._token_looks_like_api_path(raw_token):
                continue
            if "/" in raw_token or "_" in raw_token:
                return len(token) >= 3
            if "-" in raw_token and any(char.isdigit() for char in raw_token):
                return len(token) >= 3
        return False

    @staticmethod
    def _query_has_lowercase_repository_probe(term_groups: list[set[str]]) -> bool:
        repo_terms = ContextSearchService._repository_lookup_terms_from_groups(term_groups)
        if len(repo_terms) != 1:
            return False
        term = next(iter(repo_terms))
        return bool(re.fullmatch(r"[a-z0-9]{10,}", term))

    @staticmethod
    def _repository_lookup_terms_from_groups(term_groups: list[set[str]]) -> set[str]:
        terms = set()
        for group in term_groups:
            if group.intersection(DOCUMENT_INTENT_TERMS):
                continue
            for term in group:
                if ContextSearchService._is_generic_single_token_term(term):
                    continue
                if ContextSearchService._token_looks_like_api_path(term):
                    continue
                if "/" in term or "_" in term or "-" in term:
                    if len(term.strip("_-/")) >= 3:
                        terms.add(term)
                elif re.fullmatch(r"[a-z0-9][a-z0-9]{9,}", term):
                    terms.add(term)
        return terms

    @staticmethod
    def _is_generic_single_token_term(term: str) -> bool:
        return term in GENERIC_SINGLE_TOKEN_TERMS or term.endswith(GENERIC_SINGLE_TOKEN_SUFFIXES)

    @staticmethod
    def _token_looks_like_api_path(token: str) -> bool:
        normalized = token.strip("_-/").lower()
        if normalized.startswith("api/"):
            return True
        return bool(re.search(r"(^|/)v\d+($|/)", normalized))

    @staticmethod
    def _preview(text: str, length: int = 240) -> str:
        return text if len(text) <= length else text[:length].rstrip() + "..."

    def _max_retrieval_limit(self, base_limit: int) -> int:
        collection = getattr(self.indexer, "collection", None)
        if collection is not None and hasattr(collection, "count"):
            try:
                return max(base_limit, int(collection.count()))
            except Exception:
                pass
        return max(base_limit, base_limit * 64)
