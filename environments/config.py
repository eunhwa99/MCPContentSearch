from dataclasses import dataclass
from pathlib import Path
import chromadb

@dataclass(frozen=True)
class AppConfig:
    """애플리케이션 전역 설정"""
    # ChromaDB
    chroma_db_path: Path = None
    collection_name: str = "content_collection"
    
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
    
    def __post_init__(self):
        if self.chroma_db_path is None:
            object.__setattr__(
                self, 
                'chroma_db_path', 
                Path.home() / ".mcp_content_search" / "chroma_db"
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