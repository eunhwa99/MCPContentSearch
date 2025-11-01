## “Content Search Server”

이 서버의 역할은 **노션(Notion)**과 **티스토리(Tistory)**에서 글을 가져와,
내용을 벡터화(Vectorization)해 검색 가능한 형태로 저장하고,
**AI 기반 의미 검색(Semantic Search)**을 수행하는 것입니다.

즉,

> “나의 모든 글을 한 번에, 주제나 의미로 검색할 수 있는 개인 콘텐츠 검색 엔진”

---

## 🧩 전체 아키텍처 (시각화)

```
                   ┌────────────────────┐
                   │  사용자 / 클라이언트   │
                   └─────────┬──────────┘
                             │
                    [FastMCP Tool 호출]
                             │
          ┌──────────────────┴──────────────────┐
          │                                     │
 ┌────────────────────┐              ┌────────────────────┐
 │ trigger_index_all  │              │   search_content   │
 │ (인덱싱 시작)         │              │ (검색 실행)          │
 └────────────────────┘              └────────────────────┘
          │                                     │
          ▼                                     │
 ┌────────────────────────────┐                 │
 │   DocumentManager          │                 │
 │   (Notion/Tistory 문서 수집)│                  │
 └──────────┬─────────────────┘                 │
            │                                   │
            ▼                                   ▼
 ┌────────────────────────────┐       ┌────────────────────────────┐
 │   ContentIndexer           │       │  VectorIndexRetriever      │
 │   (LlamaIndex + Chroma)    │       │  (Hybrid Search)           │
 └──────────┬─────────────────┘       └────────────────────────────┘
            │
            ▼
 ┌────────────────────────────┐
 │   Chroma Vector Store      │
 │   (문서 벡터 저장소)           │
 └────────────────────────────┘
```

- LlamaIndex → “인덱싱”과 “검색 로직”을 담당
- Chroma → “벡터 저장소”
- LLM → “자연어 생성기”

---

## 🧠 핵심 개념 정리

### 1. **LlamaIndex란 무엇인가?**

LlamaIndex는 **“데이터 → 벡터화 → 검색 가능한 인덱스 구조”** 를 만들어주는 프레임워크입니다.
RAG(Retrieval-Augmented Generation) 시스템의 핵심 구성요소로,
LLM에게 **외부 지식 기반 검색 기능**을 제공하는 역할을 합니다.

즉,

> LlamaIndex = “문서를 LLM이 이해할 수 있는 벡터 형태로 인덱싱하고,
> 의미 기반 검색이 가능하게 해주는 중간 계층”

이 코드에서는 LlamaIndex를 통해:

- 노션 / 티스토리의 글을 벡터화
- ChromaDB에 저장
- 검색 시, 단순 키워드가 아닌 **의미 유사도 기반**으로 결과 반환 (hybrid)

### 핵심 구성 요소

| 구성 요소            | 설명                                  | 코드에서                                  |
| -------------------- | ------------------------------------- | ----------------------------------------- |
| **Document**         | 텍스트와 메타데이터를 담은 단위 객체  | `DocumentManager.create_llama_document()` |
| **VectorStore**      | 임베딩 벡터 저장소 (Chroma, FAISS 등) | `ChromaVectorStore`                       |
| **StorageContext**   | 인덱스의 저장 환경 (캐시, DB 등)      | `StorageContext.from_defaults()`          |
| **VectorStoreIndex** | 벡터 인덱스를 생성 및 관리            | `indexer.get_or_create_index()`           |
| **Retriever**        | 쿼리에 맞는 문서 검색                 | `VectorIndexRetriever()`                  |

### 정리

| 키워드          | 설명                                                            |
| --------------- | --------------------------------------------------------------- |
| **LlamaIndex**  | LLM이 외부 데이터를 인덱싱하고 검색할 수 있도록 돕는 프레임워크 |
| **VectorStore** | 임베딩된 벡터를 저장하는 DB (예: Chroma)                        |
| **Index**       | 빠른 검색을 위한 벡터 색인 구조                                 |
| **Retriever**   | 쿼리에 맞는 문서를 유사도 기반으로 찾아줌                       |
| **결과**        | LLM이 “외부 지식”을 이용한 정확한 응답 생성 가능                |

---

### 2. **Chroma Vector Store란? 왜 선택했나?**

LlamaIndex는 다양한 벡터 스토어를 지원합니다.
예: FAISS, Milvus, Pinecone, Weaviate, Chroma 등

그중 **Chroma**를 선택한 이유는 다음과 같습니다.

| 항목                          | 설명                                                          |
| ----------------------------- | ------------------------------------------------------------- |
| **오픈소스 & 로컬 사용 가능** | 별도 서버 없이 Python 환경 내에서 로컬 DB로 작동              |
| **LlamaIndex와 높은 호환성**  | 기본 지원 벡터 스토어로, 설정이 간단함                        |
| **빠른 쿼리 속도**            | cosine similarity 기반 빠른 유사도 검색                       |
| **메타데이터 저장 가능**      | 문서별 URL, 플랫폼, 날짜 등을 함께 저장 가능                  |
| **확장성**                    | 향후 Milvus, Pinecone 등으로 쉽게 교체 가능 (인터페이스 동일) |

즉, **“개인 로컬 환경 + 빠른 검색 + 손쉬운 통합”**이라는 3박자를 충족시키기에 Chroma가 적합합니다.

---

## ⚙️ 주요 클래스별 역할

| 클래스                           | 역할 요약                                                                                                  |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| **`IndexState` / `IndexStatus`** | 인덱싱의 현재 상태를 관리 (진행률, 상태, 메시지 등)                                                        |
| **`ContentHasher`**              | 문서 내용 해시(MD5) 생성 → 변경 여부 판별용                                                                |
| **`DocumentManager`**            | Notion / Tistory API로부터 문서 수집 및 LlamaIndex `Document` 생성                                         |
| **`IndexComparator`**            | 기존 인덱스(Chroma)에 저장된 해시와 비교하여 **신규/업데이트 문서**만 선별                                 |
| **`SearchResultFormatter`**      | 검색 결과를 마크다운 형태로 보기 좋게 정리                                                                 |
| **`ContentIndexer`**             | 문서를 배치 단위로 인덱싱하는 핵심 클래스 (LlamaIndex → Chroma 저장)                                       |
| **`FastMCP`**                    | OpenAI MCP(Message Control Protocol) 기반 서버 프레임워크. 각 기능을 “tool”로 등록해 외부 호출 가능하게 함 |

---

## 🔄 인덱싱 흐름 (Trigger → Index → Store)

1. `trigger_index_all_content()` 호출
   → 백그라운드 태스크로 `_index_all_content_background()` 실행

2. `DocumentManager.fetch_all_documents()`
   → Notion / Tistory 글 비동기 수집

3. `ContentIndexer.index_documents()`

   - 기존 인덱스(Chroma)와 비교 (`IndexComparator`)
   - 신규 또는 변경된 문서만 추출
   - 배치 단위로 인덱싱 수행 (`VectorStoreIndex.from_documents()`)

4. 결과 저장
   → LlamaIndex가 내부적으로 문서 임베딩을 계산 후
   Chroma Vector Store에 저장

---

## 🔍 검색 흐름 (Search → Retrieve → Format)

1. `search_content(query)` 호출
   → LlamaIndex의 `VectorIndexRetriever` 생성

2. `vector_store_query_mode="hybrid"`
   → 키워드 + 의미 기반 하이브리드 검색 수행

3. `retriever.retrieve(query)`
   → 유사도가 높은 벡터 노드 반환

4. `SearchResultFormatter.format_results()`
   → 중복 제거 + Markdown 형식으로 결과 정리

---

## 💾 Index 구조 개념 (시각화)

```
Chroma Collection
│
├── Document 1
│   ├── text: "Tistory 글 내용..."
│   ├── metadata:
│   │   ├── title: "Redis 튜토리얼"
│   │   ├── platform: "Tistory"
│   │   ├── url: "https://..."
│   │   ├── content_hash: "a8b1..."
│   │   └── date: "2025-10-31"
│
├── Document 2
│   └── ...
│
└── Document N
```

---

## ✅ 정리

- **자동 인덱싱 관리**: 변경된 문서만 효율적으로 재처리
- **비동기 수집 + 배치 처리**로 빠른 인덱싱
- **의미 기반 검색**으로 키워드 한계를 극복
- **Chroma 사용으로 로컬 환경에서도 고성능 검색**
- **모듈형 구조**로 향후 Slack, GitHub, Wiki 등 데이터 소스 확장 용이
