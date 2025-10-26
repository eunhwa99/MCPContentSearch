from mcp.server.fastmcp import FastMCP
import logging
from llama_index.core import VectorStoreIndex, Document, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core.retrievers import VectorIndexRetriever
from environments.config import setup_chroma
from posts.notion import fetch_notion_pages

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastMCP 서버
mcp = FastMCP("content-search-server")
# ChromaDB 설정
chroma_collection = setup_chroma()

# LlamaIndex 설정
vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
storage_context = StorageContext.from_defaults(vector_store=vector_store)

Settings.cache_dir = ".llama_cache"

# 글로벌 인덱스 변수
index = None

@mcp.tool()
async def index_all_content() -> str:
    """
    모든 플랫폼(Notion, GitHub, Tistory 등)의 글을 수집하고 벡터 인덱스를 생성합니다.
    
    🔹 최초 실행 또는 필요한 경우 실행합니다.
    🔹 LlamaIndex/Chroma를 사용하여 문서를 자동으로 청킹하고 임베딩합니다.
    🔹 이후 search_content 도구로 의미 기반 검색이 가능합니다.

    Returns:
        str: 인덱싱 결과 요약
            - 수집된 문서 수
            - 생성된 청크 수
            - 성공/실패 메시지
    """
    global index
    
    try:
        logger.info("📥 문서 수집 중...")
        
        # 문서 수집
        notion_docs = await fetch_notion_pages()
        # github_docs = await fetch_github_files()
        # tistory_docs = await fetch_tistory_posts()
        
        if not notion_docs:
            return "❌ 수집된 문서가 없습니다."
        
        logger.info(f"📊 총 {len(notion_docs)}개 문서 수집 완료")
        
        # 기존 인덱스에서 doc_id 가져오기
        existing_ids = [m['id'] for m in chroma_collection.get()['ids']]

        # 신규 문서만 필터링
        new_documents = [
            Document(
                text=doc['content'],
                metadata={
                    'title': doc['title'],
                    'platform': doc['platform'],
                    'url': doc['url'],
                    'date': doc.get('date', ''),
                    'doc_id': doc['id']
                }
            )
            for doc in notion_docs
            if doc['id'] not in existing_ids
        ]

        if not new_documents:
            return "신규 문서가 없습니다. 인덱스가 최신 상태입니다."

        logger.info(f"{len(new_documents)}개의 신규 문서 인덱싱 중...")

        index =  VectorStoreIndex.from_documents(
            new_documents,
            storage_context=storage_context,
            show_progress=True
        )
        
        return f"""
            ✅ 인덱싱 완료!

            📊 수집된 문서: {len(notion_docs)}개
            ✂️ LlamaIndex가 자동으로 청킹 및 임베딩 처리

            이제 search_content로 검색할 수 있습니다!
        """
        
    except Exception as e:
        logger.error(f"인덱싱 오류: {e}")
        return f"❌ 인덱싱 중 오류 발생: {str(e)}"


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
            # 기존 인덱스 로드
            index = VectorStoreIndex.from_vector_store(
                vector_store,
                storage_context=storage_context
            )
        
        # Retriever 설정 (하이브리드 검색)
        retriever = VectorIndexRetriever(
            index=index,
            similarity_top_k=n_results * 2,  # 더 많은 후보군 확보 후 필터링
            vector_store_query_mode="hybrid"
        )
        
        # 검색 수행
        nodes = retriever.retrieve(query)
    
        if not nodes:
            return f"'{query}'에 대한 검색 결과가 없습니다."
        
        # 중복 제거 및 정렬
        seen_titles = set()
        results = []
        
        for node in nodes:
            title = node.metadata.get('title', 'Untitled')
            
            if title not in seen_titles:
                seen_titles.add(title)
                results.append({
                    'title': title,
                    'platform': node.metadata.get('platform', 'Unknown'),
                    'url': node.metadata.get('url', ''),
                    'date': node.metadata.get('date', ''),
                    'score': node.score,
                    'text': node.text[:200] + "..."
                })
                
                if len(results) >= n_results:
                    break
        
        # 결과 포맷팅
        output = f"# 🔍 검색 결과: '{query}'\n\n"
        output += f"총 {len(results)}개의 문서를 찾았습니다.\n\n"
        
        for i, result in enumerate(results, 1):
            output += f"## {i}. [{result['title']}]({result['url']})\n"
            output += f"**플랫폼**: {result['platform']} | **날짜**: {result['date']}\n"
            output += f"**관련도**: {result['score']:.3f}\n"
            output += f"**미리보기**: {result['text']}\n\n"
        
        return output
        
    except Exception as e:
        logger.error(f"검색 오류: {e}")
        return f"❌ 검색 중 오류 발생: {str(e)}"


if __name__ == "__main__":
    mcp.run()