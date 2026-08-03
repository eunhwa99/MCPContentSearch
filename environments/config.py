from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import chromadb

from environments.runtime_env import load_repo_dotenv
from storage.metadata_store import prepare_private_directory, private_creation_umask


load_repo_dotenv()


SAFE_ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
SECRET_LIKE_ENV_VALUE_RE = re.compile(
    r"^(?:GH[POUSR]_[A-Z0-9_]+|GITHUB_PAT_[A-Z0-9_]+|(?:AKIA|ASIA)[A-Z0-9]{16})$",
    re.IGNORECASE,
)
MAX_CAREER_FILE_BYTES = 50_000_000
MAX_CAREER_FILES = 1_000
MAX_CAREER_TOTAL_BYTES = 500_000_000


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


def _obsidian_vault_path_default() -> Path | None:
    raw_value = os.getenv("CONTEXTWIKI_OBSIDIAN_VAULT_PATH", "").strip()
    if not raw_value:
        return None
    return _expanduser_safe(raw_value)


def _career_manifest_path_default() -> Path | None:
    raw_value = os.getenv("CONTEXTWIKI_CAREER_MANIFEST_PATH", "").strip()
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


def _require_bounded_int(name: str, value: int, maximum: int):
    _require_positive_int(name, value)
    if value > maximum:
        raise ValueError(f"{name} exceeds maximum {maximum}")


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
            "ContextWikiBot/0.1 (+https://github.com/eunaverse/MCPContentSearch)",
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

    # Explicit local career evidence manifest
    career_manifest_path: Path | None = field(
        default_factory=_career_manifest_path_default
    )
    career_max_file_bytes: int = field(
        default_factory=lambda: _int_env(
            "CONTEXTWIKI_CAREER_MAX_FILE_BYTES",
            10_000_000,
        )
    )
    career_max_files: int = field(
        default_factory=lambda: _int_env("CONTEXTWIKI_CAREER_MAX_FILES", 100)
    )
    career_max_total_raw_bytes: int = field(
        default_factory=lambda: _int_env(
            "CONTEXTWIKI_CAREER_MAX_TOTAL_RAW_BYTES",
            50_000_000,
        )
    )
    career_max_total_extracted_text_bytes: int = field(
        default_factory=lambda: _int_env(
            "CONTEXTWIKI_CAREER_MAX_TOTAL_EXTRACTED_TEXT_BYTES",
            100_000_000,
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
        if self.career_manifest_path is not None:
            career_manifest_path = self.career_manifest_path
            if not isinstance(career_manifest_path, Path):
                career_manifest_path = Path(career_manifest_path)
            object.__setattr__(
                self,
                "career_manifest_path",
                _expanduser_safe(career_manifest_path),
            )
        _require_positive_int("github_max_files", self.github_max_files)
        _require_positive_int("github_max_file_bytes", self.github_max_file_bytes)
        _require_positive_int("obsidian_max_files", self.obsidian_max_files)
        _require_positive_int(
            "obsidian_max_file_bytes",
            self.obsidian_max_file_bytes,
        )
        _require_bounded_int(
            "career_max_file_bytes",
            self.career_max_file_bytes,
            MAX_CAREER_FILE_BYTES,
        )
        _require_bounded_int(
            "career_max_files",
            self.career_max_files,
            MAX_CAREER_FILES,
        )
        _require_bounded_int(
            "career_max_total_raw_bytes",
            self.career_max_total_raw_bytes,
            MAX_CAREER_TOTAL_BYTES,
        )
        _require_bounded_int(
            "career_max_total_extracted_text_bytes",
            self.career_max_total_extracted_text_bytes,
            MAX_CAREER_TOTAL_BYTES,
        )
        _require_safe_env_var_name("github_token_env_var", self.github_token_env_var)
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


def setup_chroma(
    config: AppConfig,
    *,
    require_private: bool | None = None,
) -> chromadb.Collection:
    """ChromaDB 초기화"""
    private = (
        config.career_manifest_path is not None
        if require_private is None
        else require_private
    )
    if private:
        chroma_path = prepare_private_directory(config.chroma_db_path)
        with private_creation_umask():
            client = chromadb.PersistentClient(path=str(chroma_path))
            collection = client.get_or_create_collection(config.collection_name)
        return collection
    config.chroma_db_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(config.chroma_db_path))
    collection = client.get_or_create_collection(config.collection_name)
    return collection
