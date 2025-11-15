from enum import Enum
from pydantic import BaseModel, Field

class IndexState(str, Enum):
    """인덱싱 상태 열거형"""
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class DocumentModel(BaseModel):
    """문서 데이터 모델"""
    id: str
    title: str
    content: str
    url: str
    platform: str
    date: str = ""
    
    class Config:
        frozen = True


class IndexStatusModel(BaseModel):
    """인덱싱 상태 모델"""
    state: IndexState = IndexState.IDLE
    message: str = ""
    progress: float = Field(ge=0.0, le=1.0, default=0.0)
    total_docs: int = Field(ge=0, default=0)
    processed_docs: int = Field(ge=0, default=0)
