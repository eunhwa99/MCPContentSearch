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


def _split_env(name: str) -> tuple[str, ...]:
    value = os.getenv(name, "")
    return tuple(item.strip() for item in value.replace("\n", ",").split(",") if item.strip())


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


def _search_llm_enabled_default() -> bool:
    return _bool_env("CONTEXTWIKI_SEARCH_LLM_ENABLED", False)


def _obsidian_vault_path_default() -> Path | None:
    raw_value = os.getenv("CONTEXTWIKI_OBSIDIAN_VAULT_PATH", "").strip()
    if not raw_value:
        return None
    return _expanduser_safe(raw_value)


def _expanduser_safe(value: str | Path) -> Path:
    path_value = Path(value)
    try:
        return path_value.expanduser()
    except RuntimeError:
        return path_value


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
    cache_dir: str = field(
        default_factory=lambda: str(Path.home() / ".mcp_content_search" / "llama_cache")
    )

    # 인덱싱
    batch_size: int = 50
    progress_log_interval: int = 10

    # 검색
    search_multiplier: int = 2
    preview_length: int = 200
    default_search_results: int = 10
    search_llm_enabled: bool = field(default_factory=_search_llm_enabled_default)
    search_llm_provider: str = field(
        default_factory=lambda: os.getenv("CONTEXTWIKI_SEARCH_LLM_PROVIDER", "openai")
        .strip()
        .lower()
    )
    search_llm_model: str = field(
        default_factory=lambda: os.getenv("CONTEXTWIKI_SEARCH_LLM_MODEL", "gpt-4.1-mini")
        .strip()
    )
    search_llm_api_key_env_var: str = "OPENAI_API_KEY"
    search_llm_timeout: float = field(
        default_factory=lambda: _float_env("CONTEXTWIKI_SEARCH_LLM_TIMEOUT", 10.0)
    )
    search_llm_max_rewrites: int = field(
        default_factory=lambda: _int_env("CONTEXTWIKI_SEARCH_LLM_MAX_REWRITES", 3)
    )

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
    github_user_agent: str = field(
        default_factory=lambda: os.getenv(
            "CONTEXTWIKI_GITHUB_USER_AGENT",
            "ContextWikiBot/0.1 (+https://github.com/eunhwa99/MCPContentSearch)",
        )
    )

    # Obsidian connector
    obsidian_vault_path: Path | None = field(
        default_factory=_obsidian_vault_path_default
    )
    obsidian_max_files: int = field(
        default_factory=lambda: _int_env("CONTEXTWIKI_OBSIDIAN_MAX_FILES", 2_000)
    )
    obsidian_max_file_bytes: int = field(
        default_factory=lambda: _int_env(
            "CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES",
            512_000,
        )
    )

    def __post_init__(self):
        if self.obsidian_vault_path is not None:
            obsidian_vault_path = self.obsidian_vault_path
            if not isinstance(obsidian_vault_path, Path):
                obsidian_vault_path = Path(obsidian_vault_path)
            object.__setattr__(
                self,
                "obsidian_vault_path",
                _expanduser_safe(obsidian_vault_path),
            )
        _require_positive_int("github_max_files", self.github_max_files)
        _require_positive_int("github_max_file_bytes", self.github_max_file_bytes)
        _require_positive_int("obsidian_max_files", self.obsidian_max_files)
        _require_positive_int(
            "obsidian_max_file_bytes",
            self.obsidian_max_file_bytes,
        )
        _require_safe_env_var_name("github_token_env_var", self.github_token_env_var)
        _require_safe_env_var_name(
            "search_llm_api_key_env_var",
            self.search_llm_api_key_env_var,
        )
        _require_non_negative("search_llm_timeout", self.search_llm_timeout)
        _require_positive_int("search_llm_max_rewrites", self.search_llm_max_rewrites)
        if (
            self.search_llm_enabled
            and self.search_llm_provider == "openai"
            and not self.search_llm_model
        ):
            raise ValueError(
                "CONTEXTWIKI_SEARCH_LLM_MODEL must be set when search LLM is enabled"
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
