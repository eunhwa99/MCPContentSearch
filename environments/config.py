from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import re
import chromadb


SAFE_ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
SECRET_LIKE_ENV_VALUE_RE = re.compile(
    r"^(?:GH[POUSR]_[A-Z0-9_]+|GITHUB_PAT_[A-Z0-9_]+|(?:AKIA|ASIA)[A-Z0-9]{16})$",
    re.IGNORECASE,
)
DEFAULT_CONTEXTWIKI_AUTO_SYNC_SOURCES = (
    "source_github",
    "source_notion",
    "source_tistory",
)


def _split_env(name: str) -> tuple[str, ...]:
    value = os.getenv(name, "")
    return tuple(item.strip() for item in value.replace("\n", ",").split(",") if item.strip())


def _split_env_with_default(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    if name not in os.environ:
        return default
    return _split_env(name)


def _int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return int(raw_value)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


def _float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        raise ValueError(f"{name} must be a finite float") from None
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite float")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _require_positive_int(name: str, value: int):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _require_non_negative(name: str, value: float):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def _require_safe_env_var_name(name: str, value: str):
    if (
        not isinstance(value, str)
        or not SAFE_ENV_VAR_RE.match(value)
        or SECRET_LIKE_ENV_VALUE_RE.match(value)
    ):
        raise ValueError(f"{name} must be an uppercase environment variable name")


@dataclass(frozen=True)
class AppConfig:
    """애플리케이션 전역 설정"""
    # ChromaDB
    chroma_db_path: Path = None
    collection_name: str = "content_collection"
    metadata_db_path: Path = None

    # LlamaIndex
    cache_dir: str = ".llama_cache"

    # 인덱싱
    batch_size: int = 50
    progress_log_interval: int = 10

    # 검색
    search_multiplier: int = 2
    preview_length: int = 200
    default_search_results: int = 10

    # API
    request_timeout: float = 10.0
    connection_limit: int = 10

    # Tistory
    tistory_max_post_id: int = 200
    tistory_log_interval: int = 10

    # Notion
    notion_page_size: int = 100
    notion_max_depth: int = 10
    notion_api_version: str = "2025-09-03"

    # GitHub connector
    github_repositories: tuple[str, ...] = field(
        default_factory=lambda: _split_env("CONTEXTWIKI_GITHUB_REPOSITORIES")
    )
    github_default_ref: str = field(
        default_factory=lambda: os.getenv("CONTEXTWIKI_GITHUB_DEFAULT_REF", "main")
    )
    github_token_env_var: str = "GITHUB_TOKEN"
    github_max_files: int = field(
        default_factory=lambda: _int_env("CONTEXTWIKI_GITHUB_MAX_FILES", 200)
    )
    github_max_file_bytes: int = field(
        default_factory=lambda: _int_env("CONTEXTWIKI_GITHUB_MAX_FILE_BYTES", 512_000)
    )

    # Website/docs connector
    web_seed_urls: tuple[str, ...] = field(
        default_factory=lambda: _split_env("CONTEXTWIKI_WEB_URLS")
    )
    web_max_pages: int = field(
        default_factory=lambda: _int_env("CONTEXTWIKI_WEB_MAX_PAGES", 50)
    )
    web_max_response_bytes: int = field(
        default_factory=lambda: _int_env("CONTEXTWIKI_WEB_MAX_RESPONSE_BYTES", 1_048_576)
    )
    web_crawl_delay_seconds: float = field(
        default_factory=lambda: _float_env("CONTEXTWIKI_WEB_CRAWL_DELAY_SECONDS", 0.2)
    )
    web_user_agent: str = field(
        default_factory=lambda: os.getenv(
            "CONTEXTWIKI_WEB_USER_AGENT",
            "ContextWikiBot/0.1 (+https://github.com/eunhwa99/MCPContentSearch)",
        )
    )

    # Auto Wiki LLM synthesis. Disabled by default because source evidence may
    # include private user content and should only leave the machine by opt-in.
    wiki_llm_enabled: bool = field(
        default_factory=lambda: _bool_env("CONTEXTWIKI_WIKI_LLM_ENABLED", False)
    )
    wiki_llm_provider: str = field(
        default_factory=lambda: os.getenv("CONTEXTWIKI_WIKI_LLM_PROVIDER", "openai")
        .strip()
        .lower()
    )
    wiki_llm_model: str = field(
        default_factory=lambda: os.getenv("CONTEXTWIKI_WIKI_LLM_MODEL", "gpt-4.1-mini")
        .strip()
    )
    wiki_llm_api_key_env_var: str = "OPENAI_API_KEY"
    wiki_llm_timeout: float = field(
        default_factory=lambda: _float_env("CONTEXTWIKI_WIKI_LLM_TIMEOUT", 20.0)
    )
    wiki_llm_max_evidence_chars: int = field(
        default_factory=lambda: _int_env("CONTEXTWIKI_WIKI_LLM_MAX_EVIDENCE_CHARS", 1200)
    )

    # Local Web Console startup sync. Empty env value intentionally disables it.
    contextwiki_auto_sync_sources: tuple[str, ...] = field(
        default_factory=lambda: _split_env_with_default(
            "CONTEXTWIKI_AUTO_SYNC_SOURCES",
            DEFAULT_CONTEXTWIKI_AUTO_SYNC_SOURCES,
        )
    )

    def __post_init__(self):
        _require_positive_int("github_max_files", self.github_max_files)
        _require_positive_int("github_max_file_bytes", self.github_max_file_bytes)
        _require_positive_int("web_max_pages", self.web_max_pages)
        _require_positive_int("web_max_response_bytes", self.web_max_response_bytes)
        _require_non_negative("web_crawl_delay_seconds", self.web_crawl_delay_seconds)
        _require_safe_env_var_name("github_token_env_var", self.github_token_env_var)
        _require_safe_env_var_name(
            "wiki_llm_api_key_env_var",
            self.wiki_llm_api_key_env_var,
        )
        _require_non_negative("wiki_llm_timeout", self.wiki_llm_timeout)
        _require_positive_int(
            "wiki_llm_max_evidence_chars",
            self.wiki_llm_max_evidence_chars,
        )
        if (
            self.wiki_llm_enabled
            and self.wiki_llm_provider == "openai"
            and not self.wiki_llm_model
        ):
            raise ValueError(
                "CONTEXTWIKI_WIKI_LLM_MODEL must be set when wiki LLM is enabled"
            )
        if self.chroma_db_path is None:
            object.__setattr__(
                self,
                'chroma_db_path',
                Path.home() / ".mcp_content_search" / "chroma_db"
            )
        if self.metadata_db_path is None:
            object.__setattr__(
                self,
                'metadata_db_path',
                Path.home() / ".mcp_content_search" / "contextwiki_metadata.sqlite3"
            )


@dataclass(frozen=True)
class NotionConfig:
    """Notion API 설정"""
    api_key: str
    api_version: str = "2025-09-03"
    base_url: str = "https://api.notion.com/v1"

    supported_block_types: frozenset = frozenset({
        "paragraph", "heading_1", "heading_2", "heading_3",
        "bulleted_list_item", "numbered_list_item",
        "to_do", "toggle", "quote", "callout", "code"
    })

    title_property_names: tuple = ("title", "Title", "Name", "이름")


def setup_chroma(config: AppConfig) -> chromadb.Collection:
    """ChromaDB 초기화"""
    config.chroma_db_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(config.chroma_db_path))
    collection = client.get_or_create_collection(config.collection_name)
    return collection
