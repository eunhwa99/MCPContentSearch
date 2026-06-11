from __future__ import annotations

import json
import logging
from typing import Any

from search.debug_redaction import redact_prompt_query_text
from search.query_terms import query_term_groups


logger = logging.getLogger(__name__)
PROMPT_PLACEHOLDER_TERMS = {"redacted"}


class OpenAIQueryRewriter:
    """Produce short search-friendly rewrites from a user query."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout: float,
        max_rewrites: int,
    ):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_rewrites = max_rewrites

    async def rewrite_query(self, query: str, term_groups: list[set[str]]) -> list[str]:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.api_key, timeout=self.timeout)
        response = await client.chat.completions.create(
            model=self.model,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You rewrite search queries for retrieval. "
                        "Return strict JSON with one key: rewrites. "
                        "Each rewrite must be a short search query, not a sentence. "
                        "Preserve the user's intent, add likely canonical product or document terms, "
                        "and avoid explanations."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        self._prompt_payload(query, self.max_rewrites),
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        content = response.choices[0].message.content or ""
        rewrites = []
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("Query rewrite response was not valid JSON")
            return []

        for item in payload.get("rewrites", []):
            normalized = " ".join(str(item or "").split())
            if normalized and normalized.lower() != query.lower() and normalized not in rewrites:
                rewrites.append(normalized)
            if len(rewrites) >= self.max_rewrites:
                break
        return rewrites

    @staticmethod
    def _redact_secret_like(value: Any) -> Any:
        if isinstance(value, list):
            return [OpenAIQueryRewriter._redact_secret_like(item) for item in value]
        if isinstance(value, dict):
            return {
                key: OpenAIQueryRewriter._redact_secret_like(item)
                for key, item in value.items()
            }
        if not isinstance(value, str):
            return value
        return redact_prompt_query_text(value)

    @classmethod
    def _prompt_payload(cls, query: str, max_rewrites: int) -> dict:
        redacted_query = cls._redact_secret_like(query)
        normalized_terms = []
        for group in query_term_groups(redacted_query):
            terms = sorted(term for term in group if term not in PROMPT_PLACEHOLDER_TERMS)
            if terms:
                normalized_terms.append(terms)
        return {
            "query": redacted_query,
            "normalized_terms": normalized_terms,
            "max_rewrites": max_rewrites,
        }


def build_query_rewriter(config, *, api_key: str):
    if not getattr(config, "search_llm_enabled", False):
        return None
    if getattr(config, "search_llm_provider", "openai") != "openai":
        logger.warning("Unsupported search LLM provider configured")
        return None
    if not api_key:
        logger.warning(
            "Search LLM rewrite is enabled but %s is not set",
            config.search_llm_api_key_env_var,
        )
        return None
    return OpenAIQueryRewriter(
        api_key=api_key,
        model=config.search_llm_model,
        timeout=config.search_llm_timeout,
        max_rewrites=config.search_llm_max_rewrites,
    )
