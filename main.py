import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Set
from enum import Enum

from mcp.server.fastmcp import FastMCP
from llama_index.core import VectorStoreIndex, Document, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core.retrievers import VectorIndexRetriever

from environments.config import setup_chroma
from posts.notion import fetch_notion_pages
from posts.tistory import fetch_tistory_posts

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 상수 정의
CACHE_DIR = ".llama_cache"
BATCH_SIZE = 50
PROGRESS_LOG_INTERVAL = 10
SEARCH_MULTIPLIER = 2
PREVIEW_LENGTH = 200

class IndexState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class IndexStatus:
    """Datataclass for tracking indexing status"""
    state: IndexState = IndexState.IDLE
    message: str = ""
    progress: float = 0.0
    total_docs: int = 0
    processed_docs: int = 0
    
    def to_dict(self) -> Dict:
        return {
            "state": self.state.value,
            "message": self.message,
            "progress": self.progress,
            "total_docs": self.total_docs,
            "processed_docs": self.processed_docs
        }
    
    def update(self, **kwargs):
        for key, value in kwargs.items():
            if key == "state" and isinstance(value, str):
                value = IndexState(value)
            setattr(self, key, value)
    
    def reset(self):
        self.state = IndexState.IDLE
        self.message = ""
        self.progress = 0.0
        self.total_docs = 0
        self.processed_docs = 0


class ContentHasher:
    """Content hashing utility"""
    
    @staticmethod
    def md5_hash(content: str) -> str:
        return hashlib.md5(content.encode("utf-8")).hexdigest()


class DocumentManager:
    """Document fetching and creation"""
    
    @staticmethod
    async def fetch_all_documents() -> List[Dict]:
        logger.info("Start collecting documents...")
        
        notion_docs = await fetch_notion_pages()
        tistory_docs = await fetch_tistory_posts()
        
        all_docs = (notion_docs or []) + (tistory_docs or [])
        
        logger.info(f"Collect complete: Notion {len(notion_docs or [])}, "
                   f"Tistory {len(tistory_docs or [])}")
        
        return all_docs
    
    @staticmethod
    def create_llama_document(doc: Dict) -> Document:
        doc_id = doc["id"]
        content = doc["content"]
        content_hash = ContentHasher.md5_hash(content)
        
        return Document(
            text=content,
            metadata={
                "title": doc["title"],
                "platform": doc["platform"],
                "url": doc["url"],
                "date": doc.get("date", ""),
                "doc_id": doc_id,
                "content_hash": content_hash,
            },
        )


class IndexComparator:
    """Index comparison for new/updated documents"""
    
    def __init__(self, chroma_collection):
        self.chroma_collection = chroma_collection
        self.existing_docs: Dict[str, str] = {}
        self._load_existing_docs()
    
    def _load_existing_docs(self):
        existing_data = self.chroma_collection.get(include=["metadatas"])
        self.existing_docs = {
            metadata.get("doc_id"): metadata.get("content_hash", "")
            for metadata in existing_data["metadatas"]
        }
        logger.info(f"Existing documents: {len(self.existing_docs)} loaded")
    
    def is_new_document(self, doc_id: str) -> bool:
        return doc_id not in self.existing_docs
    
    def is_updated_document(self, doc_id: str, content_hash: str) -> bool:
        return (doc_id in self.existing_docs and 
                self.existing_docs[doc_id] != content_hash)
    
    def delete_outdated_document(self, doc_id: str):
        self.chroma_collection.delete(where={"doc_id": doc_id})


class SearchResultFormatter:
    """Format search results into markdown"""
    
    @staticmethod
    def format_results(query: str, nodes: List, n_results: int) -> str:
        if not nodes:
            return f"There's no result for '{query}'"
        
        # 중복 제거
        results = SearchResultFormatter._deduplicate_results(nodes, n_results)
        
        # 마크다운 생성
        output = [
            f"# 🔍 Search results: '{query}'",
            "",
            f"Total {len(results)} documents found",
            ""
        ]
        
        for i, result in enumerate(results, 1):
            output.extend([
                f"## {i}. [{result['title']}]({result['url']})",
                f"**플랫폼**: {result['platform']} | **날짜**: {result['date']}",
                f"**관련도**: {result['score']:.3f}",
                f"**미리보기**: {result['text']}",
                ""
            ])
        
        return "\n".join(output)
    
    @staticmethod
    def _deduplicate_results(nodes: List, limit: int) -> List[Dict]:
        seen_titles: Set[str] = set()
        results = []
        
        for node in nodes:
            title = node.metadata.get("title", "Untitled")
            
            if title in seen_titles:
                continue
            
            seen_titles.add(title)
            results.append({
                "title": title,
                "platform": node.metadata.get("platform", "Unknown"),
                "url": node.metadata.get("url", ""),
                "date": node.metadata.get("date", ""),
                "score": node.score,
                "text": node.text[:PREVIEW_LENGTH] + "..."
            })
            
            if len(results) >= limit:
                break
        
        return results


class ContentIndexer:
    """Content indexing manager"""
    
    def __init__(
        self,
        chroma_collection,
        storage_context: StorageContext
    ):
        self.chroma_collection = chroma_collection
        self.storage_context = storage_context
        self.index: Optional[VectorStoreIndex] = None
        self.status = IndexStatus()
    
    async def index_documents(self, documents: List[Dict]):
        self.status.update(
            state=IndexState.RUNNING,
            message="Collecting documents...",
            progress=0.0,
            total_docs=len(documents)
        )
        
        try:
            if not documents:
                self.status.update(
                    state=IndexState.DONE,
                    message="No documents to index.",
                    progress=1.0
                )
                return
            
            # 신규/업데이트 문서 필터링
            new_docs = await self._filter_documents(documents)
            
            if not new_docs["documents"]:
                self.status.update(
                    state=IndexState.DONE,
                    message="No new or updated documents to index.",
                    progress=1.0
                )
                return
            
            # 배치 인덱싱
            await self._batch_index(new_docs["documents"])
            
            self.status.update(
                state=IndexState.DONE,
                message=f"Indexing completed (New {new_docs['new_count']} documents/ "
                       f"Updated {new_docs['update_count']} documents.",
                progress=1.0
            )
            logger.info(self.status.message)
        
        except Exception as e:
            logger.error(f"Indexing error: {e}")
            self.status.update(
                state=IndexState.ERROR,
                message=f"Indexing error: {str(e)}",
                progress=1.0
            )
    
    async def _filter_documents(
        self, 
        documents: List[Dict]
    ) -> Dict[str, any]:
        comparator = IndexComparator(self.chroma_collection)
        new_or_updated = []
        new_count = 0
        update_count = 0
        total = len(documents)
        
        for i, doc in enumerate(documents, 1):
            doc_id = doc["id"]
            content_hash = ContentHasher.md5_hash(doc["content"])
            
            # 신규 문서
            if comparator.is_new_document(doc_id):
                new_count += 1
            # 업데이트된 문서
            elif comparator.is_updated_document(doc_id, content_hash):
                update_count += 1
                comparator.delete_outdated_document(doc_id)
            # 변경 없음
            else:
                self._update_progress(i, total)
                continue
            
            new_or_updated.append(DocumentManager.create_llama_document(doc))
            
            if i % PROGRESS_LOG_INTERVAL == 0:
                self.status.message = f"Preparing indexing... ({i}/{total})"
                self._update_progress(i, total)
                await asyncio.sleep(0.01)
        
        return {
            "documents": new_or_updated,
            "new_count": new_count,
            "update_count": update_count
        }
    
    async def _batch_index(self, documents: List[Document]):
        self.status.message = "Indexing documents..."
        total = self.status.total_docs
        
        for i in range(0, len(documents), BATCH_SIZE):
            batch = documents[i:i + BATCH_SIZE]
            
            if self.index is None:
                self.index = VectorStoreIndex.from_documents(
                    batch,
                    storage_context=self.storage_context,
                    show_progress=True
                )
            else:
                for doc in batch:
                    self.index.insert(doc)
            
            processed = min(total, i + BATCH_SIZE)
            self._update_progress(processed, total)
            await asyncio.sleep(0.1)
    
    def _update_progress(self, processed: int, total: int):
        self.status.processed_docs = processed
        self.status.progress = round(processed / total, 2)
    
    def get_or_create_index(self) -> VectorStoreIndex:
        # 검색하려는 경우 호출
        if self.index is None:
            self.index = VectorStoreIndex.from_vector_store(
                vector_store=ChromaVectorStore(
                    chroma_collection=self.chroma_collection
                ),
                storage_context=self.storage_context
            )
        return self.index


# ================================================================
# 🚀 Initialize FastMCP 
# ================================================================
mcp = FastMCP("content-search-server")

# Chroma 
chroma_collection = setup_chroma()
vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
storage_context = StorageContext.from_defaults(vector_store=vector_store)
Settings.cache_dir = CACHE_DIR

# ContentIndexer
indexer = ContentIndexer(chroma_collection, storage_context)


# ================================================================
# 🔧 MCP Tools
# ================================================================

@mcp.tool()
async def trigger_index_all_content() -> str:
    """
    인덱싱을 백그라운드에서 시작합니다.
    즉시 응답하며, 진행상황은 get_index_status()로 확인하세요.

    Returns:
        str: 인덱싱 시작 메시지
    """
    if indexer.status.state == IndexState.RUNNING:
        return "이미 인덱싱이 진행 중입니다. 잠시 후 다시 확인해주세요."
    
    # 백그라운드 작업 시작
    asyncio.create_task(_index_all_content_background())
    return "인덱싱 작업을 백그라운드에서 시작했습니다. 'get_index_status'로 상태를 확인하세요."


async def _index_all_content_background():
    documents = await DocumentManager.fetch_all_documents()
    await indexer.index_documents(documents)


@mcp.tool()
async def search_content(query: str, n_results: int = 10) -> str:
    """
    하이브리드 검색을 수행합니다.
    LlamaIndex의 고급 검색 기능을 사용하여 더 정확한 결과를 제공합니다.
    
    Args:
        query: 검색할 내용
        n_results: 반환할 결과 개수
    
    Returns:
        str: 마크다운 형식의 검색 결과
    """
    try:
        index = indexer.get_or_create_index()
        
        retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=n_results * SEARCH_MULTIPLIER,
            vector_store_query_mode="hybrid",
        )
        
        nodes = retriever.retrieve(query)
        
        return SearchResultFormatter.format_results(query, nodes, n_results)
    
    except Exception as e:
        logger.error(f"검색 오류: {e}")
        return f"검색 중 오류 발생: {str(e)}"


@mcp.tool()
async def get_index_status() -> dict:
    """
    현재 인덱싱 상태를 반환합니다.
    
    Returns:
        dict: 인덱싱 상태 정보
            - state: 현재 상태 (idle/running/done/error)
            - message: 상태 메시지
            - progress: 진행률 (0.0~1.0)
            - total_docs: 전체 문서 수
            - processed_docs: 처리된 문서 수
    """
    return indexer.status.to_dict()


# ================================================================
# 🚀 메인 실행
# ================================================================
if __name__ == "__main__":
    mcp.run()