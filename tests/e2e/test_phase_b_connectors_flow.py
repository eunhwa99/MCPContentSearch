import asyncio
import base64
import hashlib
import json

import pytest
from mcp.server.fastmcp import FastMCP

from api.tools import register_tools
from core.models import DocumentModel, SourceModel, SourceType, SyncJobStatus, SyncStatus
from environments.config import AppConfig
from fetching.connectors import GitHubSourceConnector, SourceConnector, SourceRegistry
from indexing.chunker import DocumentChunker
from indexing.ingestion_service import IngestionService
from search.answer_service import CitationAnswerService
from search.context_service import ContextSearchService
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.e2e


_SHA_LABELS: dict[str, str] = {}


def _sha(label: str) -> str:
    value = hashlib.sha1(label.encode("utf-8")).hexdigest()
    _SHA_LABELS[value] = label
    return value


def _labelled_url(url: str) -> str:
    for value, label in _SHA_LABELS.items():
        url = url.replace(value, label)
    return url


def _blob_payload(content: bytes) -> dict:
    return {
        "encoding": "base64",
        "content": base64.b64encode(content).decode(),
        "size": len(content),
    }


class RecordingIndexer:
    def __init__(self):
        self.documents = []
        self.deleted_ids = []

    async def index_documents(self, documents):
        self.documents.extend(documents)

    def delete_documents_by_ids(self, document_ids, source_id=""):
        self.deleted_ids.extend(document_ids)


def _call_tool_json(mcp: FastMCP, name: str, arguments: dict | None = None) -> dict:
    blocks = asyncio.run(mcp.call_tool(name, arguments or {}))
    return json.loads(blocks[0].text)


class FakeRetainedSourceConnector(SourceConnector):
    def __init__(
        self,
        *,
        source_id: str,
        source_type: SourceType,
        name: str,
        document: DocumentModel,
    ):
        self.source = SourceModel(
            source_id=source_id,
            source_type=source_type,
            name=name,
            enabled=True,
            auth_ref=f"env:FAKE_{source_id.upper()}",
            sync_status=SyncStatus.IDLE,
        )
        self._document = document
        self.fetch_count = 0

    async def fetch_documents(self) -> list[DocumentModel]:
        self.fetch_count += 1
        return [self._document]


def _retained_source_connectors() -> dict[str, FakeRetainedSourceConnector]:
    return {
        "source_notion": FakeRetainedSourceConnector(
            source_id="source_notion",
            source_type=SourceType.NOTION,
            name="Notion",
            document=DocumentModel(
                id="notion-retained-page",
                document_id="notion-retained-page",
                external_id="notion-retained-page",
                source_id="source_notion",
                title="Retained Notion Runbook",
                content="Notion retained smoke coverage writes searchable citation chunks.",
                url="https://example.test/notion/retained-runbook",
                canonical_url="https://example.test/notion/retained-runbook",
                platform="Notion",
                path="Notion/Retained Runbook",
                updated_at="2026-06-10T00:00:00Z",
            ),
        ),
        "source_tistory": FakeRetainedSourceConnector(
            source_id="source_tistory",
            source_type=SourceType.TISTORY,
            name="Tistory",
            document=DocumentModel(
                id="tistory-retained-post",
                document_id="tistory-retained-post",
                external_id="tistory-retained-post",
                source_id="source_tistory",
                title="Retained Tistory Post",
                content="Tistory retained smoke coverage writes searchable citation chunks.",
                url="https://example.test/tistory/retained-post",
                canonical_url="https://example.test/tistory/retained-post",
                platform="Tistory",
                path="posts/retained-tistory-post",
                updated_at="2026-06-10T00:00:00Z",
            ),
        ),
    }


class FakeGitHubHTTP:
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": "api/tools.py",
                        "type": "blob",
                        "sha": _sha('blob-tools'),
                        "size": 38,
                    }
                ]
            }
        if "/git/blobs/blob-tools" in url:
            return _blob_payload(b"def register_tools():\n    return 'ok'\n")
        raise AssertionError(f"unexpected GitHub API URL: {url}")


class TreeGitHubHTTP:
    def __init__(self, paths):
        self.paths = tuple(paths)

    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": path,
                        "type": "blob",
                        "sha": _sha(f"blob-{path}"),
                        "size": len(f"print({path!r})\n".encode()),
                    }
                    for path in self.paths
                ]
            }
        if "/git/blobs/blob-" in url:
            blob_path = url.rsplit("/git/blobs/blob-", 1)[1]
            return _blob_payload(f"print({blob_path!r})\n".encode())
        raise AssertionError(f"unexpected GitHub API URL: {url}")


class MissingTreePayloadGitHubHTTP:
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {"sha": _sha('tree-main')}
        raise AssertionError(f"unexpected GitHub API URL: {url}")


class MissingBlobContentGitHubHTTP:
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": "a.py",
                        "type": "blob",
                        "sha": _sha('blob-a.py'),
                        "size": 20,
                    }
                ]
            }
        if "/git/blobs/blob-a.py" in url:
            return {"encoding": "base64", "size": 20}
        raise AssertionError(f"unexpected GitHub API URL: {url}")


class BinaryBlobGitHubHTTP:
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": "a.py",
                        "type": "blob",
                        "sha": _sha('blob-a.py'),
                        "size": 4,
                    }
                ]
            }
        if "/git/blobs/blob-a.py" in url:
            return _blob_payload(b"\x00\x01OK")
        raise AssertionError(f"unexpected GitHub API URL: {url}")


class SizedTreeGitHubHTTP:
    def __init__(self, sizes):
        self.sizes = dict(sizes)

    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": path,
                        "type": "blob",
                        "sha": _sha(f"blob-{path}"),
                        "size": size,
                    }
                    for path, size in self.sizes.items()
                ]
            }
        if "/git/blobs/blob-" in url:
            blob_path = url.rsplit("/git/blobs/blob-", 1)[1]
            content = f"print({blob_path!r})\n".encode().ljust(
                self.sizes[blob_path],
                b"#",
            )
            return _blob_payload(content)
        raise AssertionError(f"unexpected GitHub API URL: {url}")


class MissingSizeLargeBlobGitHubHTTP:
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": "large.py",
                        "type": "blob",
                        "sha": _sha('blob-large'),
                    }
                ]
            }
        if "/git/blobs/blob-large" in url:
            return {
                "encoding": "base64",
                "content": base64.b64encode(b"print('larger than cap')\n").decode(),
            }
        raise AssertionError(f"unexpected GitHub API URL: {url}")


class RefChangingGitHubHTTP:
    def __init__(self, ref, blob_sha):
        self.ref = ref
        self.blob_sha = blob_sha

    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if f"/commits/{self.ref}" in url:
            return {
                "sha": _sha(f"commit-{self.ref}"),
                "commit": {"tree": {"sha": _sha(f"tree-{self.ref}")}},
            }
        if f"/git/trees/tree-{self.ref}" in url:
            return {
                "tree": [
                    {
                        "path": "api/tools.py",
                        "type": "blob",
                        "sha": _sha(self.blob_sha),
                        "size": len(f"def {self.ref}_tools():\n    return 'ok'\n".encode()),
                    }
                ]
            }
        if f"/git/blobs/{self.blob_sha}" in url:
            return _blob_payload(f"def {self.ref}_tools():\n    return 'ok'\n".encode())
        raise AssertionError(f"unexpected GitHub API URL: {url}")


@pytest.mark.parametrize(
    ("source_id", "query", "expected_title"),
    [
        ("source_notion", "Notion retained smoke", "Retained Notion Runbook"),
        ("source_tistory", "Tistory retained smoke", "Retained Tistory Post"),
    ],
)
def test_retained_notion_and_tistory_sync_through_mcp_tools(
    tmp_path,
    source_id,
    query,
    expected_title,
):
    connectors = _retained_source_connectors()
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    registry = SourceRegistry(connectors.values())
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=registry,
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )
    context_search = ContextSearchService(metadata_store=store, retriever=indexer.documents)
    answer_service = CitationAnswerService(
        context_search=context_search,
        min_score=0.1,
        min_results=1,
    )
    mcp = FastMCP("retained-source-smoke")
    register_tools(
        mcp,
        ingestion_service=ingestion,
        context_search_service=context_search,
        answer_service=answer_service,
        metadata_store=store,
        source_registry=registry,
    )

    listed = _call_tool_json(mcp, "list_sources")
    sync_job = _call_tool_json(mcp, "sync_source", {"source_id": source_id})
    status = _call_tool_json(mcp, "get_sync_status", {"source_id": source_id})
    document_id = connectors[source_id]._document.document_id
    chunks = store.list_chunks_for_document(document_id)
    search_result = _call_tool_json(
        mcp,
        "search_context",
        {
            "query": query,
            "filters": {"source_id": source_id},
            "top_k": 3,
        },
    )
    fetched_document = _call_tool_json(
        mcp,
        "fetch_context",
        {"document_id": document_id},
    )
    fetched_chunk = _call_tool_json(
        mcp,
        "fetch_context",
        {"chunk_id": search_result["results"][0]["chunk_id"]},
    )
    answer = _call_tool_json(
        mcp,
        "answer_with_citations",
        {
            "question": query,
            "filters": {"source_id": source_id},
            "top_k": 3,
        },
    )

    assert [source["source_id"] for source in listed["sources"]] == [
        "source_notion",
        "source_tistory",
    ]
    assert sync_job["status"] == "succeeded"
    assert sync_job["source_id"] == source_id
    assert sync_job["processed_documents"] == 1
    assert sync_job["indexed_chunks"] >= 1
    assert status["source"]["sync_status"] == "succeeded"
    assert status["latest_job"]["status"] == "succeeded"
    assert connectors[source_id].fetch_count == 1
    assert all(
        connector.fetch_count == (1 if connector.source.source_id == source_id else 0)
        for connector in connectors.values()
    )
    assert store.get_source(source_id).sync_status == SyncStatus.SUCCEEDED
    assert store.get_document(document_id).source_id == source_id
    assert chunks
    assert chunks[0].source_id == source_id
    assert indexer.documents[0].source_id == source_id
    assert search_result["results"][0]["source_id"] == source_id
    assert search_result["results"][0]["title"] == expected_title
    assert fetched_document["document"]["document_id"] == document_id
    assert fetched_document["chunks"][0]["chunk_id"] == chunks[0].chunk_id
    assert fetched_chunk["chunk"]["text"] == connectors[source_id]._document.content
    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == [search_result["results"][0]["chunk_id"]]


def test_retained_github_sync_through_mcp_tools(tmp_path):
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=FakeGitHubHTTP(),
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    registry = SourceRegistry([connector])
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=registry,
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )
    context_search = ContextSearchService(metadata_store=store, retriever=indexer.documents)
    answer_service = CitationAnswerService(
        context_search=context_search,
        min_score=0.1,
        min_results=1,
    )
    mcp = FastMCP("retained-github-smoke")
    register_tools(
        mcp,
        ingestion_service=ingestion,
        context_search_service=context_search,
        answer_service=answer_service,
        metadata_store=store,
        source_registry=registry,
    )

    listed = _call_tool_json(mcp, "list_sources")
    sync_job = _call_tool_json(mcp, "sync_source", {"source_id": "source_github"})
    status = _call_tool_json(mcp, "get_sync_status", {"source_id": "source_github"})
    document_id = "github:eunhwa99/mcpcontentsearch:api/tools.py"
    chunks = store.list_chunks_for_document(document_id)
    search_result = _call_tool_json(
        mcp,
        "search_context",
        {
            "query": "register_tools ok",
            "filters": {"source_id": "source_github"},
            "top_k": 3,
        },
    )
    fetched_document = _call_tool_json(
        mcp,
        "fetch_context",
        {"document_id": document_id},
    )
    fetched_chunk = _call_tool_json(
        mcp,
        "fetch_context",
        {"chunk_id": search_result["results"][0]["chunk_id"]},
    )
    answer = _call_tool_json(
        mcp,
        "answer_with_citations",
        {
            "question": "register_tools ok",
            "filters": {"source_id": "source_github"},
            "top_k": 3,
        },
    )

    assert [source["source_id"] for source in listed["sources"]] == ["source_github"]
    assert sync_job["status"] == "succeeded"
    assert sync_job["source_id"] == "source_github"
    assert sync_job["processed_documents"] == 1
    assert sync_job["indexed_chunks"] >= 1
    assert status["source"]["sync_status"] == "succeeded"
    assert status["latest_job"]["status"] == "succeeded"
    assert store.get_source("source_github").sync_status == SyncStatus.SUCCEEDED
    assert store.get_document(document_id).source_id == "source_github"
    assert chunks
    assert chunks[0].path == "api/tools.py"
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 2
    assert indexer.documents[0].source_id == "source_github"
    assert indexer.documents[0].document_id == document_id
    assert search_result["results"][0]["source_id"] == "source_github"
    assert search_result["results"][0]["chunk_id"] == chunks[0].chunk_id
    assert fetched_document["document"]["document_id"] == document_id
    assert fetched_document["chunks"][0]["chunk_id"] == chunks[0].chunk_id
    assert fetched_chunk["chunk"]["text"] == chunks[0].text
    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == [search_result["results"][0]["chunk_id"]]


def test_github_connector_syncs_through_ingestion_service(tmp_path):
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=FakeGitHubHTTP(),
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    job = asyncio.run(service.sync_source("source_github"))
    chunks = store.list_chunks_for_document(
        "github:eunhwa99/mcpcontentsearch:api/tools.py"
    )

    assert job.status == SyncJobStatus.SUCCEEDED
    assert store.get_source("source_github").sync_status == SyncStatus.SUCCEEDED
    assert chunks[0].path == "api/tools.py"
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 2
    assert indexer.documents[0].source_id == "source_github"
    assert indexer.documents[0].document_id == (
        "github:eunhwa99/mcpcontentsearch:api/tools.py"
    )


def test_github_sync_skips_stale_cleanup_when_file_cap_is_exceeded(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=2, github_max_file_bytes=1000),
        http_client=TreeGitHubHTTP(("a.py", "b.py")),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_github"))
    b_document_id = "github:eunhwa99/mcpcontentsearch:b.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(b_document_id)
    assert first_connector.supports_stale_cleanup is True

    second_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=2, github_max_file_bytes=1000),
        http_client=TreeGitHubHTTP(("0.py", "a.py", "b.py")),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_github"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(b_document_id).deleted_at == ""
    assert store.list_chunks_for_document(b_document_id)
    assert indexer.deleted_ids == []


def test_github_configured_sync_preserves_ad_hoc_target_repo_documents(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_configured_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=TreeGitHubHTTP(("README.md", "old.py")),
    )
    first_configured_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_configured_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_configured_service.sync_source("source_github"))
    stale_configured_document_id = "github:eunhwa99/mcpcontentsearch:old.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_configured_document_id)

    ad_hoc_connector = GitHubSourceConnector(
        repositories=("eunhwa99/leetcode@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=TreeGitHubHTTP(("graph.py",)),
        allow_stale_cleanup=False,
    )
    ad_hoc_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ad_hoc_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
        register_source_config=False,
    )

    ad_hoc_job = asyncio.run(ad_hoc_service.sync_source("source_github"))
    ad_hoc_document_id = "github:eunhwa99/leetcode:graph.py"
    assert ad_hoc_job.status == SyncJobStatus.SUCCEEDED
    assert ad_hoc_connector.supports_stale_cleanup is False
    assert store.list_chunks_for_document(ad_hoc_document_id)

    second_configured_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=TreeGitHubHTTP(("README.md",)),
    )
    second_configured_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_configured_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_configured_service.sync_source("source_github"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_configured_connector.supports_stale_cleanup is True
    assert store.get_document(stale_configured_document_id).deleted_at != ""
    assert store.get_document(ad_hoc_document_id).deleted_at == ""
    assert store.list_chunks_for_document(ad_hoc_document_id)


def test_github_repo_cleanup_prefix_treats_underscore_literally(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_configured_connector = GitHubSourceConnector(
        repositories=("eunhwa99/foo_bar@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=TreeGitHubHTTP(("old.py",)),
    )
    first_configured_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_configured_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_configured_service.sync_source("source_github"))
    configured_document_id = "github:eunhwa99/foo_bar:old.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(configured_document_id)

    ad_hoc_connector = GitHubSourceConnector(
        repositories=("eunhwa99/fooxbar@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=TreeGitHubHTTP(("graph.py",)),
        allow_stale_cleanup=False,
    )
    ad_hoc_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([ad_hoc_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
        register_source_config=False,
    )

    ad_hoc_job = asyncio.run(ad_hoc_service.sync_source("source_github"))
    ad_hoc_document_id = "github:eunhwa99/fooxbar:graph.py"
    assert ad_hoc_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(ad_hoc_document_id)

    second_configured_connector = GitHubSourceConnector(
        repositories=("eunhwa99/foo_bar@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=TreeGitHubHTTP(()),
    )
    second_configured_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_configured_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_configured_service.sync_source("source_github"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document(configured_document_id).deleted_at != ""
    assert store.get_document(ad_hoc_document_id).deleted_at == ""
    assert store.list_chunks_for_document(ad_hoc_document_id)


def test_github_sync_fails_without_stale_cleanup_for_missing_tree_payload(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=TreeGitHubHTTP(("a.py",)),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_github"))
    document_id = "github:eunhwa99/mcpcontentsearch:a.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(document_id)

    second_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=MissingTreePayloadGitHubHTTP(),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_github"))

    assert second_job.status == SyncJobStatus.FAILED
    assert store.get_document(document_id).deleted_at == ""
    assert store.list_chunks_for_document(document_id)
    assert indexer.deleted_ids == []


def test_github_sync_fails_without_deleting_chunks_for_missing_blob_content(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=TreeGitHubHTTP(("a.py",)),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_github"))
    document_id = "github:eunhwa99/mcpcontentsearch:a.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(document_id)

    second_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=MissingBlobContentGitHubHTTP(),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_github"))

    assert second_job.status == SyncJobStatus.FAILED
    assert store.get_document(document_id).deleted_at == ""
    assert store.list_chunks_for_document(document_id)
    assert indexer.deleted_ids == []


def test_github_sync_skips_stale_cleanup_for_binary_blob_content(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=TreeGitHubHTTP(("a.py",)),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_github"))
    document_id = "github:eunhwa99/mcpcontentsearch:a.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(document_id)

    second_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=BinaryBlobGitHubHTTP(),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_github"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(document_id).deleted_at == ""
    assert store.list_chunks_for_document(document_id)
    assert indexer.deleted_ids == []


def test_github_sync_skips_stale_cleanup_when_byte_cap_is_exceeded(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=SizedTreeGitHubHTTP({"a.py": 20, "large.py": 80}),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_github"))
    large_document_id = "github:eunhwa99/mcpcontentsearch:large.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(large_document_id)
    assert first_connector.supports_stale_cleanup is True

    second_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=100),
        http_client=SizedTreeGitHubHTTP({"a.py": 20, "large.py": 200}),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_github"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(large_document_id).deleted_at == ""
    assert store.list_chunks_for_document(large_document_id)
    assert indexer.deleted_ids == []


def test_github_sync_skips_stale_cleanup_when_blob_byte_cap_is_exceeded(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=TreeGitHubHTTP(("other.py",)),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_github"))
    other_document_id = "github:eunhwa99/mcpcontentsearch:other.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(other_document_id)

    second_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=5),
        http_client=MissingSizeLargeBlobGitHubHTTP(),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_github"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(other_document_id).deleted_at == ""
    assert store.list_chunks_for_document(other_document_id)
    assert indexer.deleted_ids == []


def test_github_sync_preserves_document_identity_when_configured_ref_changes(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=RefChangingGitHubHTTP("main", "blob-main-tools"),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_github"))
    document_id = "github:eunhwa99/mcpcontentsearch:api/tools.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(document_id)

    second_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@release",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=RefChangingGitHubHTTP("release", "blob-release-tools"),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_github"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    persisted = store.get_document(document_id)
    assert persisted is not None
    assert persisted.deleted_at == ""
    assert persisted.version_id == _sha("blob-release-tools")
    assert all(not document_id == deleted_id for deleted_id in indexer.deleted_ids)


def test_github_sync_preserves_document_identity_when_repository_case_changes(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=RefChangingGitHubHTTP("main", "blob-main-tools"),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_github"))
    document_id = "github:eunhwa99/mcpcontentsearch:api/tools.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(document_id)

    second_connector = GitHubSourceConnector(
        repositories=("EUNHWA99/mcpcontentsearch@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=RefChangingGitHubHTTP("main", "blob-main-tools-updated"),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_github"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    persisted = store.get_document(document_id)
    assert persisted is not None
    assert persisted.deleted_at == ""
    assert persisted.version_id == _sha("blob-main-tools-updated")
    assert all(document_id != deleted_id for deleted_id in indexer.deleted_ids)
