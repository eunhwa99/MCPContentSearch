# config.py
import pathlib
import chromadb

def setup_chroma():
    # ChromaDB 설정
    chroma_db_path = pathlib.Path.home() / ".mcp_content_search" / "chroma_db"
    chroma_db_path.mkdir(parents=True, exist_ok=True)
    chroma_client = chromadb.PersistentClient(path=str(chroma_db_path))

    # 컬렉션 생성
    chroma_collection = chroma_client.get_or_create_collection("content_collection")
    
    return chroma_collection
