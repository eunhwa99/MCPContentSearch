from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

class IndexState(str, Enum):
    """인덱싱 상태 열거형"""
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class SourceType(str, Enum):
    """지원하는 source 유형"""
    NOTION = "notion"
    TISTORY = "tistory"
    GITHUB = "github"
    WEB = "web"


class SyncStatus(str, Enum):
    """source 단위 sync 상태"""
    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SyncJobStatus(str, Enum):
    """sync job 상태"""
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SourceModel(BaseModel):
    """ContextWiki source metadata"""
    source_id: str
    source_type: SourceType
    name: str
    enabled: bool = True
    auth_ref: str = ""
    sync_status: SyncStatus = SyncStatus.IDLE
    last_synced_at: str = ""
    last_error: str = ""
    created_at: str = ""
    updated_at: str = ""

    model_config = ConfigDict(frozen=True)


class SyncJobModel(BaseModel):
    """ContextWiki source sync job metadata"""
    job_id: str
    source_id: str
    status: SyncJobStatus = SyncJobStatus.QUEUED
    started_at: str = ""
    finished_at: str = ""
    total_documents: int = Field(ge=0, default=0)
    processed_documents: int = Field(ge=0, default=0)
    indexed_chunks: int = Field(ge=0, default=0)
    skipped_documents: int = Field(ge=0, default=0)
    error_message: str = ""

    model_config = ConfigDict(frozen=True)


class DocumentModel(BaseModel):
    """문서 데이터 모델"""
    id: str
    title: str
    content: str
    url: str
    platform: str
    date: str = ""
    source_id: str = ""
    document_id: str = ""
    path: str = ""
    updated_at: str = ""
    content_hash: str = ""
    chunk_id: str = ""
    chunk_index: Optional[int] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    
    model_config = ConfigDict(frozen=True)


class ChunkModel(BaseModel):
    """Citation 가능한 chunk metadata"""
    chunk_id: str
    document_id: str
    source_id: str
    title: str
    text: str
    url: str = ""
    path: str = ""
    chunk_index: int = Field(ge=0)
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    content_hash: str
    updated_at: str = ""

    model_config = ConfigDict(frozen=True)

    def to_document_model(self, platform: str = "") -> DocumentModel:
        """Indexer가 기존 DocumentModel 경로를 재사용할 수 있게 변환한다."""
        return DocumentModel(
            id=self.chunk_id,
            chunk_id=self.chunk_id,
            document_id=self.document_id,
            source_id=self.source_id,
            title=self.title,
            content=self.text,
            url=self.url,
            platform=platform or self.source_id,
            path=self.path,
            updated_at=self.updated_at,
            content_hash=self.content_hash,
            chunk_index=self.chunk_index,
            line_start=self.line_start,
            line_end=self.line_end,
        )


class ContextSearchResult(BaseModel):
    """MCP citation search 결과"""
    chunk_id: str
    document_id: str
    source_id: str
    source_type: str
    title: str
    url: str = ""
    path: str = ""
    score: float = 0.0
    preview: str = ""
    text: str = ""
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    updated_at: str = ""

    model_config = ConfigDict(frozen=True)


class IndexStatusModel(BaseModel):
    """인덱싱 상태 모델"""
    state: IndexState = IndexState.IDLE
    message: str = ""
    progress: float = Field(ge=0.0, le=1.0, default=0.0)
    total_docs: int = Field(ge=0, default=0)
    processed_docs: int = Field(ge=0, default=0)
