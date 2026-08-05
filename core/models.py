from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    OBSIDIAN = "obsidian"


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


class DocumentSortBy(str, Enum):
    """Normalized document timestamp fields supported by browse/search sorting."""

    PUBLISHED_AT = "published_at"
    MODIFIED_AT = "modified_at"
    INDEXED_AT = "indexed_at"


class SearchSortBy(str, Enum):
    """Semantic document-search sort fields."""

    RELEVANCE = "relevance"
    PUBLISHED_AT = "published_at"
    MODIFIED_AT = "modified_at"
    INDEXED_AT = "indexed_at"


class SortOrder(str, Enum):
    """Deterministic document sort directions."""

    ASC = "asc"
    DESC = "desc"


def _parse_filter_timestamp(value: Any) -> datetime:
    if value in (None, ""):
        raise ValueError("Date filters must be valid ISO 8601 timestamps")
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Date filters must be valid ISO 8601 timestamps") from exc
    else:
        raise ValueError("Date filters must be valid ISO 8601 timestamps")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError("Date filters must be valid ISO 8601 timestamps") from exc


def _normalize_filter_timestamp(value: Any) -> str:
    if value in (None, ""):
        return ""
    parsed = _parse_filter_timestamp(value)
    return parsed.isoformat().replace("+00:00", "Z")


class SearchFilters(BaseModel):
    """Typed inclusive source and normalized UTC date filters."""

    source_id: str = ""
    source_ids: list[str] = Field(default_factory=list)
    published_from: str = ""
    published_to: str = ""
    modified_from: str = ""
    modified_to: str = ""
    indexed_from: str = ""
    indexed_to: str = ""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
    )

    @field_validator(
        "published_from",
        "published_to",
        "modified_from",
        "modified_to",
        "indexed_from",
        "indexed_to",
        mode="before",
    )
    @classmethod
    def normalize_timestamp(cls, value: Any) -> str:
        return _normalize_filter_timestamp(value)

    @field_validator("source_ids", mode="before")
    @classmethod
    def coerce_source_ids(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, tuple):
            return list(value)
        return value

    @field_validator("source_ids")
    @classmethod
    def normalize_source_ids(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            source_id = value.strip()
            if not source_id:
                continue
            if source_id not in normalized:
                normalized.append(source_id)
        return normalized

    @field_validator("source_id", mode="before")
    @classmethod
    def coerce_source_id(cls, value: Any) -> Any:
        return "" if value is None else value

    @field_validator("source_id")
    @classmethod
    def normalize_source_id(cls, value: str) -> str:
        return value.strip()

    @property
    def effective_source_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    source_id
                    for source_id in (self.source_id, *self.source_ids)
                    if source_id
                }
            )
        )

    @model_validator(mode="after")
    def validate_ranges(self):
        for prefix in ("published", "modified", "indexed"):
            lower = getattr(self, f"{prefix}_from")
            upper = getattr(self, f"{prefix}_to")
            if (
                lower
                and upper
                and _parse_filter_timestamp(lower) > _parse_filter_timestamp(upper)
            ):
                raise ValueError(f"{prefix}_from must be before or equal to {prefix}_to")
        return self


class SourceModel(BaseModel):
    """ContextZip source metadata"""
    source_id: str
    source_type: SourceType
    name: str
    enabled: bool = True
    auth_ref: str = ""
    sync_status: SyncStatus = SyncStatus.IDLE
    last_synced_at: str = ""
    last_error: str = ""
    stale_cleanup_disabled_reason: str = ""
    created_at: str = ""
    updated_at: str = ""

    model_config = ConfigDict(frozen=True)


class SyncJobModel(BaseModel):
    """ContextZip source sync job metadata"""
    job_id: str
    source_id: str
    status: SyncJobStatus = SyncJobStatus.QUEUED
    started_at: str = ""
    finished_at: str = ""
    total_documents: int = Field(ge=0, default=0)
    processed_documents: int = Field(ge=0, default=0)
    indexed_chunks: int = Field(ge=0, default=0)
    skipped_documents: int = Field(ge=0, default=0)
    phase: str = ""
    upstream_total: int = Field(ge=0, default=0)
    upstream_done: int = Field(ge=0, default=0)
    last_progress_at: str = ""
    status_message: str = ""
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
    external_id: str = ""
    canonical_url: str = ""
    path: str = ""
    updated_at: str = ""
    published_at: str = ""
    modified_at: str = ""
    indexed_at: str = ""
    date_provenance: str = ""
    last_seen_at: str = ""
    last_seen_sync_id: str = ""
    deleted_at: str = ""
    version_id: str = ""
    content_hash: str = ""
    chunk_id: str = ""
    chunk_index: Optional[int] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None

    model_config = ConfigDict(frozen=True)

    @field_validator("published_at", "modified_at", "indexed_at", mode="before")
    @classmethod
    def normalize_null_timestamp(cls, value: Any) -> Any:
        return "" if value is None else value


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
    version_id: str = ""
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
            canonical_url=self.url,
            platform=platform or "",
            path=self.path,
            updated_at=self.updated_at,
            content_hash=self.content_hash,
            chunk_index=self.chunk_index,
            line_start=self.line_start,
            line_end=self.line_end,
            version_id=self.version_id,
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
    vector_score: float = 0.0
    metadata_priority: int = 0
    preview: str = ""
    text: str = ""
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    version_id: str = ""
    updated_at: str = ""
    published_at: str = ""
    modified_at: str = ""
    indexed_at: str = ""
    date_provenance: str = ""

    model_config = ConfigDict(frozen=True)


class DocumentSearchResult(BaseModel):
    """문서 단위로 그룹화된 MCP search 결과"""
    document_id: str
    chunk_id: str
    source_id: str
    source_type: str
    title: str
    url: str = ""
    path: str = ""
    score: float = 0.0
    vector_score: float = 0.0
    metadata_priority: int = 0
    matched_context: str
    published_at: str = ""
    modified_at: str = ""
    indexed_at: str = ""
    date_provenance: str = ""

    model_config = ConfigDict(frozen=True)


class IndexStatusModel(BaseModel):
    """인덱싱 상태 모델"""
    state: IndexState = IndexState.IDLE
    message: str = ""
    progress: float = Field(ge=0.0, le=1.0, default=0.0)
    total_docs: int = Field(ge=0, default=0)
    processed_docs: int = Field(ge=0, default=0)
