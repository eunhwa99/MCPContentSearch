from mcp.server.fastmcp import FastMCP
import logging
import asyncio
import hashlib
from llama_index.core import VectorStoreIndex, Document, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core.retrievers import VectorIndexRetriever
from environments.config import setup_chroma
from posts.notion import fetch_notion_pages
from posts.tistory import fetch_tistory_posts

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -----------------------------
# FastMCP 서버 초기화
# -----------------------------
mcp = FastMCP("content-search-server")

# -----------------------------
# Chroma 설정
# -----------------------------
chroma_collection = setup_chroma()
vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
storage_context = StorageContext.from_defaults(vector_store=vector_store)
Settings.cache_dir = ".llama_cache"

# -----------------------------
# 글로벌 상태 변수
# -----------------------------
index = None
index_status = {
    "state": "idle",       # idle, running, done, error
    "message": "",
    "progress": 0.0,       # 0.0 ~ 1.0
    "total_docs": 0,
    "processed_docs": 0
}

# -----------------------------
# 해시 유틸
# -----------------------------
def get_content_hash(content: str) -> str:
    return hashlib.md5(content.encode("utf-8")).hexdigest()


# ================================================================
# 🟡 1️⃣ 인덱싱 트리거 (즉시 응답)
# ================================================================
@mcp.tool()
async def trigger_index_all_content() -> str:
    """
    인덱싱을 백그라운드에서 시작합니다.
    즉시 응답하며, 진행상황은 get_index_status()로 확인하세요.

    Returns:
        str: 인덱싱 결과 요약
            - 수집된 문서 수
            - 생성된 청크 수
            - 성공/실패 메시지
    """
    if index_status["state"] == "running":
        return "⚙️ 이미 인덱싱이 진행 중입니다. 잠시 후 다시 확인해주세요."

    asyncio.create_task(index_all_content_background())
    return "🟡 인덱싱 작업을 백그라운드에서 시작했습니다. 'get_index_status'로 상태를 확인하세요."


# ================================================================
# ⚙️ 2️⃣ 실제 인덱싱 로직 (백그라운드)
# ================================================================
async def index_all_content_background():
    global index, index_status
    index_status.update({"state": "running", "message": "문서 수집 중...", "progress": 0.0})
    try:
        # Step 1: 문서 수집
        notion_docs = await fetch_notion_pages()
        tistory_docs = await fetch_tistory_posts()
        all_docs = (notion_docs or []) + (tistory_docs or [])

        total = len(all_docs)
        index_status["total_docs"] = total

        if not all_docs:
            index_status.update({"state": "done", "message": "❌ 수집된 문서가 없습니다.", "progress": 1.0})
            return

        # Step 2: 기존 인덱스 비교
        existing_data = chroma_collection.get(include=["metadatas"])
        existing_docs = {
            metadata.get("doc_id"): metadata.get("content_hash", "")
            for metadata in existing_data["metadatas"]
        }

        new_or_updated_documents = []
        new_count, update_count = 0, 0

        for i, doc in enumerate(all_docs, 1):
            doc_id = doc["id"]
            content_hash = get_content_hash(doc["content"])

            if doc_id not in existing_docs:
                new_count += 1
            elif existing_docs[doc_id] != content_hash:
                update_count += 1
                chroma_collection.delete(where={"doc_id": doc_id})
            else:
                index_status["processed_docs"] = i
                index_status["progress"] = round(i / total, 2)
                continue

            new_or_updated_documents.append(
                Document(
                    text=doc["content"],
                    metadata={
                        "title": doc["title"],
                        "platform": doc["platform"],
                        "url": doc["url"],
                        "date": doc.get("date", ""),
                        "doc_id": doc_id,
                        "content_hash": content_hash,
                    },
                )
            )

            if i % 10 == 0:
                index_status["message"] = f"인덱싱 준비 중... ({i}/{total})"
                index_status["progress"] = round(i / total, 2)
                await asyncio.sleep(0.01)

        # Step 3: 인덱싱 수행
        batch_size = 50
        index_status["message"] = "문서 인덱싱 중..."

        for i in range(0, len(new_or_updated_documents), batch_size):
            batch = new_or_updated_documents[i : i + batch_size]

            if index is None:
                index = VectorStoreIndex.from_documents(batch, storage_context=storage_context, show_progress=True)
            else:
                for doc in batch:
                    index.insert(doc)

            index_status["processed_docs"] = min(total, i + batch_size)
            index_status["progress"] = round(index_status["processed_docs"] / total, 2)
            await asyncio.sleep(0.1)

        index_status.update({
            "state": "done",
            "message": f"✅ 인덱싱 완료 (신규 {new_count}개 / 업데이트 {update_count}개)",
            "progress": 1.0
        })
        logger.info(index_status["message"])

    except Exception as e:
        logger.error(f"인덱싱 오류: {e}")
        index_status.update({
            "state": "error",
            "message": f"❌ 인덱싱 중 오류 발생: {str(e)}",
            "progress": 1.0
        })


# ================================================================
# 🔍 3️⃣ 검색 기능 (기존과 동일)
# ================================================================
@mcp.tool()
async def search_content(query: str, n_results: int = 10) -> str:
    """
    하이브리드 검색을 수행합니다.
    LlamaIndex의 고급 검색 기능을 사용하여 더 정확한 결과를 제공합니다.
    
    Args:
        query: 검색할 내용
        n_results: 반환할 결과 개수
    """
    global index
    try:
        if index is None:
            index = VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)

        retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=n_results * 2,
            vector_store_query_mode="hybrid",
        )

        nodes = retriever.retrieve(query)
        if not nodes:
            return f"'{query}'에 대한 검색 결과가 없습니다."

        seen_titles = set()
        results = []
        for node in nodes:
            title = node.metadata.get("title", "Untitled")
            if title not in seen_titles:
                seen_titles.add(title)
                results.append({
                    "title": title,
                    "platform": node.metadata.get("platform", "Unknown"),
                    "url": node.metadata.get("url", ""),
                    "date": node.metadata.get("date", ""),
                    "score": node.score,
                    "text": node.text[:200] + "..."
                })
                if len(results) >= n_results:
                    break

        output = f"# 🔍 검색 결과: '{query}'\n\n총 {len(results)}개의 문서를 찾았습니다.\n\n"
        for i, r in enumerate(results, 1):
            output += f"## {i}. [{r['title']}]({r['url']})\n"
            output += f"**플랫폼**: {r['platform']} | **날짜**: {r['date']}\n"
            output += f"**관련도**: {r['score']:.3f}\n"
            output += f"**미리보기**: {r['text']}\n\n"

        return output

    except Exception as e:
        logger.error(f"검색 오류: {e}")
        return f"❌ 검색 중 오류 발생: {str(e)}"


# ================================================================
# 📊 4️⃣ 인덱싱 상태 조회
# ================================================================
@mcp.tool()
async def get_index_status() -> dict:
    """
    현재 인덱싱 상태를 반환합니다.
    """
    return index_status


# ================================================================
# 🚀 메인 실행
# ================================================================
if __name__ == "__main__":
    mcp.run()
