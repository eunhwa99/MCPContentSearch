"""Optional LLM synthesis for citation-backed Auto Wiki pages."""

from __future__ import annotations

import json
import logging
import re
from typing import Any


logger = logging.getLogger(__name__)
PEM_BLOCK_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
SENSITIVE_KEY_PATTERN = (
    r"access[-_]?key(?:[-_]?id)?|access[-_]?token|api[-_]?key|apikey|auth|"
    r"authorization|aws[-_]?access[-_]?key[-_]?id|"
    r"aws[-_]?secret[-_]?access[-_]?key|client[-_]?secret|code|cookie|"
    r"credential|csrf[-_]?token|csrf|j[-_]?session[-_]?id|jwt[-_]?token|"
    r"jwt|key|pass|password|passwd|private[-_]?key|pwd|secret[-_]?key|"
    r"secret|session[-_]?id|session[-_]?token|session|sig|signature|"
    r"sid|ssh[-_]?private[-_]?key|token|xsrf[-_]?token|xsrf|"
    r"x[-_]?amz[-_]?access[-_]?key[-_]?id|x[-_]?amz[-_]?credential"
)
QUOTED_ASSIGNMENT_SECRET_PATTERN = re.compile(
    rf"(?P<prefix>['\"]?(?:{SENSITIVE_KEY_PATTERN})['\"]?\s*[:=]\s*)"
    r"(?P<quote>['\"])(?P<secret>(?:\\.|(?!\2).)*)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
MULTIWORD_ASSIGNMENT_SECRET_PATTERN = re.compile(
    rf"(?P<prefix>['\"]?(?:{SENSITIVE_KEY_PATTERN})['\"]?\s*[:=]\s*['\"]?)"
    r"(?P<secret>[^'\"\n,;}]+)(?P<suffix>['\"]?)",
    re.IGNORECASE,
)
TOKEN_SECRET_PATTERN = re.compile(
    r"(gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|"
    r"xox[baprs]-[A-Za-z0-9-]+|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
    r"(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,})",
    re.IGNORECASE,
)
ASSIGNMENT_SECRET_PATTERN = re.compile(
    rf"(?P<prefix>['\"]?(?:{SENSITIVE_KEY_PATTERN})['\"]?\s*[:=]\s*['\"]?)"
    r"(?P<secret>[^'\"\s,;}]+)(?P<suffix>['\"]?)",
    re.IGNORECASE,
)
QUERY_SECRET_PATTERN = re.compile(
    rf"(?P<prefix>[?&](?:{SENSITIVE_KEY_PATTERN})=)(?P<secret>[^&#\s]+)",
    re.IGNORECASE,
)
SENSITIVE_DICT_KEY_PATTERN = re.compile(rf"^(?:{SENSITIVE_KEY_PATTERN})$", re.IGNORECASE)
SENSITIVE_DICT_KEY_PHRASES = (
    "access_key",
    "access_key_id",
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "code",
    "cookie",
    "credential",
    "csrf",
    "csrf_token",
    "jwt",
    "jwt_token",
    "key",
    "pass",
    "password",
    "passwd",
    "private_key",
    "pwd",
    "secret",
    "secret_key",
    "session",
    "session_id",
    "session_token",
    "sig",
    "signature",
    "sid",
    "ssh_private_key",
    "token",
    "xsrf",
    "xsrf_token",
    "x_amz_credential",
)


class OpenAIWikiSynthesizer:
    """Generate a natural wiki page from supplied evidence using OpenAI."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout: float,
        max_evidence_chars: int,
    ):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_evidence_chars = max_evidence_chars

    async def synthesize_wiki_page(self, **payload) -> dict[str, Any]:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.api_key, timeout=self.timeout)
        response = await client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You write concise, source-grounded wiki pages. "
                        "Use only the evidence JSON provided by the user. "
                        "Return strict JSON with keys: title, sections, markdown. "
                        "Each section must include heading, content, citation_markers. "
                        "Every substantive sentence must include citation markers like [C1]."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        self._build_prompt_payload(payload),
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        content = response.choices[0].message.content or ""
        return json.loads(content)

    def _build_prompt_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "topic": self._redact_secret_like(payload.get("topic")),
            "instructions": self._redact_secret_like(payload.get("instructions")),
            "output_contract": {
                "title": "Short page title.",
                "sections": [
                    {
                        "heading": "Section heading",
                        "content": "Section prose with citation markers such as [C1].",
                        "citation_markers": ["C1"],
                    }
                ],
                "markdown": "Full Markdown page using the same citation markers.",
            },
            "evidence": [
                self._redact_secret_like(
                    {key: value for key, value in item.items() if key != "text"}
                )
                | {
                    "text": self._truncate_text(
                        self._redact_secret_like(item.get("text", ""))
                    ),
                }
                for item in payload.get("evidence", [])
            ],
            "citations": self._redact_secret_like(payload.get("citations", [])),
            "backlinks": self._redact_secret_like(payload.get("backlinks", [])),
        }

    def _truncate_text(self, text: str) -> str:
        cleaned = " ".join(str(text or "").split())
        if len(cleaned) <= self.max_evidence_chars:
            return cleaned
        return cleaned[: self.max_evidence_chars].rstrip() + "..."

    @staticmethod
    def _redact_secret_like(value: Any) -> Any:
        if isinstance(value, list):
            return [
                OpenAIWikiSynthesizer._redact_secret_like(item)
                for item in value
            ]
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                redacted_key = OpenAIWikiSynthesizer._redact_dict_key(key)
                if OpenAIWikiSynthesizer._is_sensitive_dict_key(key):
                    redacted[redacted_key] = "[REDACTED]"
                else:
                    redacted[redacted_key] = OpenAIWikiSynthesizer._redact_secret_like(
                        item
                    )
            return redacted
        if not isinstance(value, str):
            return value

        redacted = PEM_BLOCK_PATTERN.sub("[REDACTED]", value)
        redacted = QUOTED_ASSIGNMENT_SECRET_PATTERN.sub(
            lambda match: f"{match.group('prefix')}{match.group('quote')}[REDACTED]{match.group('quote')}",
            redacted,
        )
        redacted = TOKEN_SECRET_PATTERN.sub("[REDACTED]", redacted)
        redacted = MULTIWORD_ASSIGNMENT_SECRET_PATTERN.sub(
            lambda match: f"{match.group('prefix')}[REDACTED]{match.group('suffix')}",
            redacted,
        )
        redacted = ASSIGNMENT_SECRET_PATTERN.sub(
            lambda match: f"{match.group('prefix')}[REDACTED]{match.group('suffix')}",
            redacted,
        )
        return QUERY_SECRET_PATTERN.sub(
            lambda match: f"{match.group('prefix')}[REDACTED]",
            redacted,
        )

    @staticmethod
    def _is_sensitive_dict_key(key: Any) -> bool:
        if not isinstance(key, str):
            return False
        key_with_boundaries = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
        normalized = re.sub(r"[^A-Za-z0-9]+", "_", key_with_boundaries).strip("_").lower()
        if not normalized:
            return False
        if SENSITIVE_DICT_KEY_PATTERN.fullmatch(normalized):
            return True
        tokens = normalized.split("_")
        for phrase in SENSITIVE_DICT_KEY_PHRASES:
            phrase_tokens = phrase.split("_")
            phrase_len = len(phrase_tokens)
            for index in range(0, len(tokens) - phrase_len + 1):
                if tokens[index : index + phrase_len] == phrase_tokens:
                    return True
        return False

    @staticmethod
    def _redact_dict_key(key: Any) -> Any:
        if not isinstance(key, str):
            return key
        redacted = OpenAIWikiSynthesizer._redact_secret_like(key)
        if redacted != key:
            return "[REDACTED_KEY]"
        return key


def build_wiki_synthesizer(config, *, api_key: str):
    """Build the configured wiki synthesizer, or None when safely disabled."""

    if not config.wiki_llm_enabled:
        return None
    if config.wiki_llm_provider != "openai":
        logger.warning("Unsupported wiki LLM provider configured")
        return None
    if not api_key:
        logger.warning(
            "Wiki LLM synthesis is enabled but %s is not set",
            config.wiki_llm_api_key_env_var,
        )
        return None
    return OpenAIWikiSynthesizer(
        api_key=api_key,
        model=config.wiki_llm_model,
        timeout=config.wiki_llm_timeout,
        max_evidence_chars=config.wiki_llm_max_evidence_chars,
    )
