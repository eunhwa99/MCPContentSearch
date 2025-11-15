# 📁 완성된 프로젝트 파일 구조

## 🗂️ 디렉토리 구조

```
mcp-content-search/
│
├── environments/
│   ├── config.py             # AppConfig, NotionConfig, setup_chroma
│   └── token.py              # 환경 변수 로드
│
├── core/
│   ├── exceptions.py         # 모든 커스텀 예외 클래스
│   ├── models.py             # Pydantic 데이터 모델
│   └── utils.py              # ContentHasher 유틸리티
│
├── indexing/
│   ├── converter.py          # DocumentConverter
│   ├── manager.py            # IndexManager
│   └── indexer.py            # ContentIndexer
│
├── fetching/
│   ├── notion.py             # NotionAPIClient, NotionPageProcessor
│   ├── tistory.py            # TistoryPostExtractor, fetch_post
│   └── fetcher.py            # DocumentFetcher (통합)
│
├── search/
│   └── service.py            # SearchService
│
├── api/
│   └── tools.py              # register_tools, MCP 도구 정의
│
├── main.py                   # 애플리케이션 진입점
├── requirements.txt
├── .env
└── README.md
```

## 📝 각 파일의 역할

### 🔧 environments/ - 환경 설정

| 파일        | 역할           | 주요 클래스/함수                              |
| ----------- | -------------- | --------------------------------------------- |
| `config.py` | 앱 설정 관리   | `AppConfig`, `NotionConfig`, `setup_chroma()` |
| `token.py`  | 환경 변수 로드 | `NOTION_API_KEY`, `TISTORY_BLOG_NAME`         |

### 🎯 core/ - 핵심 기능

| 파일            | 역할        | 주요 클래스/함수                                     |
| --------------- | ----------- | ---------------------------------------------------- |
| `exceptions.py` | 예외 정의   | `ContentSearchError`, `IndexingError`, `APIError` 등 |
| `models.py`     | 데이터 모델 | `DocumentModel`, `IndexStatusModel`, `IndexState`    |
| `utils.py`      | 유틸리티    | `ContentHasher`                                      |

### 📚 indexing/ - 인덱싱

| 파일           | 역할        | 주요 클래스/함수    |
| -------------- | ----------- | ------------------- |
| `converter.py` | 문서 변환   | `DocumentConverter` |
| `manager.py`   | 인덱스 관리 | `IndexManager`      |
| `indexer.py`   | 인덱싱 실행 | `ContentIndexer`    |

### 🌐 fetching/ - 데이터 수집

| 파일         | 역할           | 주요 클래스/함수                         |
| ------------ | -------------- | ---------------------------------------- |
| `notion.py`  | Notion API     | `NotionAPIClient`, `NotionPageProcessor` |
| `tistory.py` | Tistory 크롤링 | `TistoryPostExtractor`, `fetch_post()`   |
| `fetcher.py` | 통합 수집      | `DocumentFetcher`                        |

### 🔍 search/ - 검색

| 파일         | 역할        | 주요 클래스/함수 |
| ------------ | ----------- | ---------------- |
| `service.py` | 검색 서비스 | `SearchService`  |

### 🔌 api/ - API 레이어

| 파일       | 역할     | 주요 클래스/함수                    |
| ---------- | -------- | ----------------------------------- |
| `tools.py` | MCP 도구 | `register_tools()`, MCP 도구 함수들 |

### 🚀 main.py - 진입점

| 역할                        | 주요 함수              |
| --------------------------- | ---------------------- |
| 애플리케이션 초기화 및 실행 | `create_app()`, `main` |

## 🔄 의존성 흐름

```
main.py
  ↓
  ├─→ environments/config.py (설정 로드)
  ├─→ indexing/indexer.py (인덱서 생성)
  ├─→ search/service.py (검색 서비스 생성)
  └─→ api/tools.py (MCP 도구 등록)
        ↓
        ├─→ fetching/fetcher.py
        │     ├─→ fetching/notion.py
        │     └─→ fetching/tistory.py
        ├─→ indexing/indexer.py
        │     ├─→ indexing/manager.py
        │     └─→ indexing/converter.py
        └─→ search/service.py
```

## 🚀 실행 방법

```bash
# 기존과 동일
python main.py

# 또는
python -m mcp_content_search.main
```

## 📦 requirements.txt

```txt
fastmcp
llama-index
llama-index-vector-stores-chroma
chromadb
httpx
aiohttp
beautifulsoup4
certifi
pydantic
python-dotenv
tenacity
```
