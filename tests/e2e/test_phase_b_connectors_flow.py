import asyncio
import base64
import hashlib

import httpx
import pytest

from core.models import SyncJobStatus, SyncStatus
from environments.config import AppConfig
from fetching.connectors import GitHubSourceConnector, SourceRegistry, WebsiteSourceConnector
from fetching.web_docs import FetchResponse, WebsiteHTTPClient
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


class FakeWebHTTP:
    def __init__(self):
        self.responses = {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <head><title>Start</title></head>
                  <body><main><p>ContextWiki web source.</p></main></body>
                </html>
            """,
        }

    async def get_text(self, url, headers=None):
        return self.responses[url]


class MapWebHTTP:
    def __init__(self, responses):
        self.responses = responses

    async def get_text(self, url, headers=None):
        return self.responses[url]


class MutableMapWebHTTP(MapWebHTTP):
    def __init__(self, responses):
        super().__init__(responses)
        self.requested = []

    async def get_text(self, url, headers=None):
        self.requested.append(url)
        return await super().get_text(url, headers=headers)


class HeaderMapWebHTTP(MapWebHTTP):
    def __init__(self, responses, headers):
        super().__init__(responses)
        self.headers = headers
        self.requested = []

    async def get_response(self, url, headers=None):
        self.requested.append(url)
        return FetchResponse(
            url=url,
            text=self.responses[url],
            headers=self.headers.get(url, {}),
        )


class AliasRedirectingWebHTTP(MapWebHTTP):
    def __init__(self, responses):
        super().__init__(responses)
        self.requested = []

    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/start":
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "/guide"},
            )
        return FetchResponse(url=url, text=self.responses[url])


class DistinctAliasRedirectingWebHTTP(MapWebHTTP):
    def __init__(self, responses):
        super().__init__(responses)
        self.requested = []

    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url in {"https://docs.example.com/start", "https://docs.example.com/alias"}:
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "/guide"},
            )
        return FetchResponse(url=url, text=self.responses[url])


class DeferredDisallowedRedirectWebHTTP(MapWebHTTP):
    def __init__(self, responses):
        super().__init__(responses)
        self.requested = []

    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/alias":
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "/private/secret"},
            )
        return FetchResponse(url=url, text=self.responses[url])


class MediaRedirectWebHTTP(MapWebHTTP):
    def __init__(self, responses):
        super().__init__(responses)
        self.requested = []

    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/logo.png":
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "/asset"},
            )
        return FetchResponse(url=url, text=self.responses[url])


class SkippedRobotsWebHTTP(MapWebHTTP):
    def __init__(self, responses):
        super().__init__(responses)
        self.requested = []

    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/robots.txt":
            return FetchResponse(
                url=url,
                text="",
                headers={"Content-Type": "text/plain"},
                body_skipped=True,
            )
        return FetchResponse(url=url, text=self.responses[url])


class RecordingMapWebHTTP(MapWebHTTP):
    def __init__(self, responses):
        super().__init__(responses)
        self.requested = []

    async def get_text(self, url, headers=None):
        self.requested.append(url)
        return await super().get_text(url, headers=headers)


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


def test_web_connector_syncs_through_ingestion_service(tmp_path):
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP(),
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    job = asyncio.run(service.sync_source("source_web"))
    chunks = store.list_chunks_for_document("web:https://docs.example.com/start")

    assert job.status == SyncJobStatus.SUCCEEDED
    assert store.get_source("source_web").sync_status == SyncStatus.SUCCEEDED
    assert chunks[0].url == "https://docs.example.com/start"
    assert chunks[0].text == "ContextWiki web source."
    assert indexer.documents[0].source_id == "source_web"


def test_web_sync_drops_secret_like_response_validators_before_citations(tmp_path):
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html><body><main>ContextWiki validator docs.</main></body></html>
                """,
            },
            {
                "https://docs.example.com/start": {
                    "ETag": "token=privatevalue",
                    "Last-Modified": "session=privatevalue",
                },
            },
        ),
    )
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    job = asyncio.run(service.sync_source("source_web"))
    chunk = store.list_chunks_for_document("web:https://docs.example.com/start")[0]
    context_search = ContextSearchService(store, retriever=[chunk.to_document_model()])
    answer_service = CitationAnswerService(context_search, min_score=0.1, min_results=1)
    answer = asyncio.run(
        answer_service.answer_with_citations("ContextWiki validator", top_k=1)
    )

    assert job.status == SyncJobStatus.SUCCEEDED
    assert chunk.version_id == ""
    assert answer["citations"][0]["version_id"] == ""
    assert "privatevalue" not in repr(answer)


def test_web_sync_keeps_allowed_docs_when_disallowed_sitemap_entries_precede_them(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document("web:https://docs.example.com/guide")

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": (
                    "User-agent: *\nDisallow: /private\n"
                ),
                "https://docs.example.com/sitemap.xml": """
                    <urlset>
                      <url><loc>https://docs.example.com/private/one</loc></url>
                      <url><loc>https://docs.example.com/private/two</loc></url>
                      <url><loc>https://docs.example.com/guide</loc></url>
                    </urlset>
                """,
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("web:https://docs.example.com/guide").deleted_at == ""
    assert store.list_chunks_for_document("web:https://docs.example.com/guide")
    assert indexer.deleted_ids == []


def test_web_sync_keeps_allowed_doc_when_disallowed_seed_precedes_sitemap_result(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document("web:https://docs.example.com/guide")

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml", "https://docs.example.com/private"),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": (
                    "User-agent: *\nDisallow: /private\n"
                ),
                "https://docs.example.com/sitemap.xml": """
                    <urlset>
                      <url><loc>https://docs.example.com/guide</loc></url>
                    </urlset>
                """,
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("web:https://docs.example.com/guide").deleted_at == ""
    assert store.list_chunks_for_document("web:https://docs.example.com/guide")
    assert indexer.deleted_ids == []


def test_web_sync_keeps_existing_doc_when_redirect_alias_self_link_precedes_it(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/other",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/other": """
                    <html><body><main>Other</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document("web:https://docs.example.com/other")

    second_client = AliasRedirectingWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/guide": """
                <html>
                  <body>
                    <main>
                      <a href="/guide">Self</a>
                      <a href="/other">Other</a>
                      <p>Guide.</p>
                    </main>
                  </body>
                </html>
            """,
            "https://docs.example.com/other": """
                <html><body><main>Other</main></body></html>
            """,
        }
    )
    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=second_client,
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("web:https://docs.example.com/other").deleted_at == ""
    assert store.list_chunks_for_document("web:https://docs.example.com/other")
    assert second_client.requested.count("https://docs.example.com/guide") == 1


def test_web_sync_keeps_existing_doc_when_duplicate_redirect_seed_precedes_it(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/other",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/other": """
                    <html><body><main>Other</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document("web:https://docs.example.com/other")

    second_client = AliasRedirectingWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/guide": """
                <html>
                  <body>
                    <main>
                      <a href="/other">Other</a>
                      <p>Guide.</p>
                    </main>
                  </body>
                </html>
            """,
            "https://docs.example.com/other": """
                <html><body><main>Other</main></body></html>
            """,
        }
    )
    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start", "https://docs.example.com/start"),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=second_client,
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("web:https://docs.example.com/other").deleted_at == ""
    assert store.list_chunks_for_document("web:https://docs.example.com/other")
    assert second_client.requested.count("https://docs.example.com/start") == 1


def test_web_sync_keeps_existing_doc_when_robots_blank_line_separates_groups(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document("web:https://docs.example.com/guide")

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": (
                    "User-agent: ContextWikiBot\n"
                    "\n"
                    "User-agent: OtherBot\n"
                    "Disallow: /\n"
                ),
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("web:https://docs.example.com/guide").deleted_at == ""
    assert store.list_chunks_for_document("web:https://docs.example.com/guide")


def test_web_sync_applies_robots_comment_inside_matching_group(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/private/secret",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/private/secret": """
                    <html><body><main>Secret</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    document_id = "web:https://docs.example.com/private/secret"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/private/secret",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": (
                    "User-agent: ContextWikiBot\n"
                    "# keep this group active\n"
                    "Disallow: /private\n"
                ),
                "https://docs.example.com/private/secret": """
                    <html><body><main>Secret</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(document_id).deleted_at == ""
    assert store.list_chunks_for_document(document_id)


def test_web_sync_skips_stale_cleanup_when_seed_becomes_robots_disallowed(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/private/secret",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/private/secret": """
                    <html><body><main>Secret</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    document_id = "web:https://docs.example.com/private/secret"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/private/secret",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": (
                    "User-agent: *\nDisallow: /private\n"
                ),
                "https://docs.example.com/private/secret": """
                    <html><body><main>Secret</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(document_id).deleted_at == ""
    assert store.list_chunks_for_document(document_id)


def test_web_sync_blocks_percent_encoded_robots_disallowed_paths(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_client = MutableMapWebHTTP(
        {
            "https://docs.example.com/robots.txt": "User-agent: *\nDisallow: /private\n",
            "https://docs.example.com/sitemap.xml": """
                <urlset>
                  <url><loc>https://docs.example.com/pri%76ate/secret</loc></url>
                  <url><loc>https://docs.example.com/guide</loc></url>
                </urlset>
            """,
            "https://docs.example.com/pri%76ate/secret": """
                <html><body><main>Secret</main></body></html>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide</main></body></html>
            """,
        }
    )
    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=second_client,
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/guide").deleted_at == ""
    assert store.get_document(stale_document_id).deleted_at == ""
    assert "https://docs.example.com/pri%76ate/secret" not in second_client.requested


def test_web_sync_skips_stale_cleanup_for_wrong_namespace_sitemap_child(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/sitemap.xml": """
                    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                      <url xmlns="">
                        <loc>https://docs.example.com/guide</loc>
                      </url>
                    </urlset>
                """,
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_unknown_sitemap_child(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/sitemap.xml": """
                    <urlset>
                      <entry><loc>https://docs.example.com/guide</loc></entry>
                    </urlset>
                """,
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_empty_sitemap(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/sitemap.xml": """
                    <urlset>
                    </urlset>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_cross_origin_sitemap_loc(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/sitemap.xml": """
                    <urlset>
                      <url><loc>https://other.example.com/guide</loc></url>
                    </urlset>
                """,
                "https://other.example.com/guide": """
                    <html><body><main>Other guide</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_self_sitemap_loc(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/sitemap.xml": """
                    <urlset>
                      <url><loc>#self</loc></url>
                    </urlset>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_refreshes_robots_rules_between_syncs(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()
    client = MutableMapWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/private/secret": """
                <html><body><main>Secret</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/private/secret",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(service.sync_source("source_web"))
    document_id = "web:https://docs.example.com/private/secret"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(document_id)

    client.responses["https://docs.example.com/robots.txt"] = (
        "User-agent: *\nDisallow: /private\n"
    )
    client.requested.clear()

    second_job = asyncio.run(service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert client.requested.count("https://docs.example.com/robots.txt") == 1
    assert "https://docs.example.com/private/secret" not in client.requested
    assert connector.supports_stale_cleanup is False
    assert store.get_document(document_id).deleted_at == ""


def test_web_sync_keeps_existing_doc_and_deletes_stale_after_distinct_redirect_alias(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/other", "https://docs.example.com/stale"),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/other": """
                    <html><body><main>Other</main></body></html>
                """,
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document("web:https://docs.example.com/other")
    assert store.list_chunks_for_document("web:https://docs.example.com/stale")

    second_client = DistinctAliasRedirectingWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/guide": """
                <html>
                  <body>
                    <main>
                      <a href="/other">Other</a>
                      <p>Guide.</p>
                    </main>
                  </body>
                </html>
            """,
            "https://docs.example.com/other": """
                <html><body><main>Other</main></body></html>
            """,
        }
    )
    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start", "https://docs.example.com/alias"),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=second_client,
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is True
    assert store.get_document("web:https://docs.example.com/other").deleted_at == ""
    assert store.get_document("web:https://docs.example.com/stale").deleted_at != ""
    assert store.list_chunks_for_document("web:https://docs.example.com/other")
    assert second_client.requested.count("https://docs.example.com/start") == 1
    assert second_client.requested.count("https://docs.example.com/alias") == 1


def test_web_sync_deletes_stale_after_complete_canonical_alias_crawl(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide", "https://docs.example.com/stale"),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document("web:https://docs.example.com/guide")
    assert store.list_chunks_for_document("web:https://docs.example.com/stale")

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide": """
                    <html>
                      <body>
                        <main><a href="/alias">Alias</a><p>Guide</p></main>
                      </body>
                    </html>
                """,
                "https://docs.example.com/alias": """
                    <html>
                      <head>
                        <link rel="canonical" href="https://docs.example.com/guide" />
                      </head>
                      <body><main>Alias</main></body>
                    </html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is True
    assert store.get_document("web:https://docs.example.com/guide").deleted_at == ""
    assert store.get_document("web:https://docs.example.com/stale").deleted_at != ""


def test_web_sync_uses_stable_identity_for_case_variant_urls(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html><body><main>Start</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document("web:https://docs.example.com/start")

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://Docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://Docs.example.com/start": """
                    <html><body><main>Start</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("web:https://docs.example.com/start").deleted_at == ""
    assert store.get_document("web:https://Docs.example.com/start") is None


def test_web_sync_uses_stable_identity_for_default_port_urls(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html><body><main>Start</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document("web:https://docs.example.com/start")

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com:443/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com:443/start": """
                    <html><body><main>Start</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("web:https://docs.example.com/start").deleted_at == ""
    assert store.get_document("web:https://docs.example.com:443/start") is None


def test_web_sync_ignores_cross_origin_canonical_for_stale_cleanup(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html><body><main>Start</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document("web:https://docs.example.com/start")

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <head>
                        <link rel="canonical" href="https://other.example.com/start" />
                      </head>
                      <body><main>Start</main></body>
                    </html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document("web:https://docs.example.com/start").deleted_at == ""
    assert store.get_document("web:https://other.example.com/start") is None


def test_web_sync_ignores_robots_disallowed_canonical_for_stale_cleanup(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/private/secret",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/private/secret": """
                    <html><body><main>Secret</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    disallowed_id = "web:https://docs.example.com/private/secret"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(disallowed_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/alias",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": (
                    "User-agent: *\nDisallow: /private\n"
                ),
                "https://docs.example.com/alias": """
                    <html>
                      <head>
                        <link rel="canonical" href="https://docs.example.com/private/secret" />
                      </head>
                      <body><main>Alias</main></body>
                    </html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/alias").deleted_at == ""
    assert store.get_document(disallowed_id).deleted_at == ""
    assert store.list_chunks_for_document(disallowed_id)


def test_web_sync_skips_stale_cleanup_when_page_extracts_empty_content(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html><body><main>Start</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    document_id = "web:https://docs.example.com/start"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html><body><script>renderLater()</script></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(document_id).deleted_at == ""
    assert store.list_chunks_for_document(document_id)


def test_web_sync_skips_stale_cleanup_when_crawl_cap_is_exhausted(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/four",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/four": """
                    <html><body><main>Four</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document("web:https://docs.example.com/four")

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/sitemap.xml": """
                    <urlset>
                      <url><loc>https://docs.example.com/one</loc></url>
                      <url><loc>https://docs.example.com/two</loc></url>
                      <url><loc>https://docs.example.com/three</loc></url>
                      <url><loc>https://docs.example.com/four</loc></url>
                    </urlset>
                """,
                "https://docs.example.com/one": """
                    <html><body><main>One</main></body></html>
                """,
                "https://docs.example.com/two": """
                    <html><body><main>Two</main></body></html>
                """,
                "https://docs.example.com/three": """
                    <html><body><main>Three</main></body></html>
                """,
                "https://docs.example.com/four": """
                    <html><body><main>Four</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/four").deleted_at == ""
    assert store.list_chunks_for_document("web:https://docs.example.com/four")


def test_web_sync_skips_stale_cleanup_after_deferred_candidate_hits_disallowed_redirect(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=DeferredDisallowedRedirectWebHTTP(
            {
                "https://docs.example.com/robots.txt": (
                    "User-agent: *\nDisallow: /private\n"
                ),
                "https://docs.example.com/start": """
                    <html><body><main>Start</main>
                      <a href="/alias">Alias</a>
                      <a href="/two">Two</a>
                      <a href="/three">Three</a>
                    </body></html>
                """,
                "https://docs.example.com/two": """
                    <html><body><main>Two</main></body></html>
                """,
                "https://docs.example.com/three": """
                    <html><body><main>Three</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/three").deleted_at == ""
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)
    assert "https://docs.example.com/private/secret" not in (
        second_connector.fetcher.http_client.requested
    )


def test_web_sync_discovers_prefixed_xhtml_links_before_stale_cleanup(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    guide_id = "web:https://docs.example.com/guide"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(guide_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": (
                    "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                    "<xhtml:html xmlns:xhtml=\"http://www.w3.org/1999/xhtml\">"
                    "<xhtml:body><xhtml:main>"
                    "<xhtml:a href=\"/guide\">Guide</xhtml:a>"
                    "<xhtml:p>Start</xhtml:p>"
                    "</xhtml:main></xhtml:body></xhtml:html>"
                ),
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            },
            {
                "https://docs.example.com/start": {
                    "Content-Type": "application/xhtml+xml",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is True
    assert store.get_document("web:https://docs.example.com/start").deleted_at == ""
    assert store.get_document(guide_id).deleted_at == ""
    assert store.list_chunks_for_document(guide_id)


def test_web_sync_skips_stale_cleanup_for_mislabelled_xhtml_xml(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": (
                    "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                    "<project><name>Not XHTML docs</name></project>"
                ),
            },
            {
                "https://docs.example.com/start": {
                    "Content-Type": "application/xhtml+xml",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_mislabelled_text_html_xml(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": (
                    "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                    "<project><name>Not HTML docs</name></project>"
                ),
            },
            {
                "https://docs.example.com/start": {
                    "Content-Type": "text/html",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_mislabelled_xhtml_sitemap_root(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": "<urlset></urlset>",
            },
            {
                "https://docs.example.com/start": {
                    "Content-Type": "application/xhtml+xml",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_mislabelled_text_html_sitemap_root(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": "<urlset></urlset>",
            },
            {
                "https://docs.example.com/start": {
                    "Content-Type": "text/html",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_markdown_doc_links(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide.md",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide.md": "# Guide\n",
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    guide_id = "web:https://docs.example.com/guide.md"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(guide_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/README.md",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/README.md": (
                    "<html><body>Index [Guide](/guide.md)</body></html>"
                ),
            },
            {
                "https://docs.example.com/README.md": {
                    "Content-Type": "text/markdown",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/README.md").deleted_at == ""
    assert store.get_document(guide_id).deleted_at == ""
    assert store.list_chunks_for_document(guide_id)


def test_web_sync_skips_stale_cleanup_for_headerless_markdown_html_body(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide.md",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide.md": "# Guide\n",
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    guide_id = "web:https://docs.example.com/guide.md"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(guide_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/README.md",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/README.md": (
                    "<p>Index</p>\n\nSee [Guide](/guide.md).\n"
                ),
            },
            {},
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/README.md").deleted_at == ""
    assert store.get_document(guide_id).deleted_at == ""
    assert store.list_chunks_for_document(guide_id)


def test_web_sync_resolves_base_href_links_before_stale_cleanup(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/docs/guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/docs/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    guide_id = "web:https://docs.example.com/docs/guide"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(guide_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/index.html": """
                    <html>
                      <head><base target="_blank" /><base href="/docs/" /></head>
                      <body><main><a href="guide">Guide</a>Index</main></body>
                    </html>
                """,
                "https://docs.example.com/docs/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
                "https://docs.example.com/guide": """
                    <html><body><main>Wrong guide</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is True
    assert store.get_document("web:https://docs.example.com/index.html").deleted_at == ""
    assert store.get_document(guide_id).deleted_at == ""
    assert store.get_document("web:https://docs.example.com/guide") is None


def test_web_sync_skips_stale_cleanup_for_body_nested_head_base(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    guide_id = "web:https://docs.example.com/guide"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(guide_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/index.html": """
                    <html>
                      <body>
                        <main>
                          <head><base href="/wrong/" /></head>
                          <a href="guide">Guide</a>
                          Index
                        </main>
                      </body>
                    </html>
                """,
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
                "https://docs.example.com/wrong/guide": """
                    <html><body><main>Wrong guide</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/index.html").deleted_at == ""
    assert store.get_document(guide_id).deleted_at == ""
    assert store.get_document("web:https://docs.example.com/wrong/guide") is None


def test_web_sync_skips_stale_cleanup_for_fragment_nested_head_base(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    guide_id = "web:https://docs.example.com/guide"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(guide_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/index.html": """
                    <div>
                      <head><base href="/wrong/" /></head>
                      <a href="guide">Guide</a>
                      Index
                    </div>
                """,
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
                "https://docs.example.com/wrong/guide": """
                    <html><body><main>Wrong guide</main></body></html>
                """,
            },
            {
                "https://docs.example.com/index.html": {
                    "Content-Type": "text/html",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/index.html").deleted_at == ""
    assert store.get_document(guide_id).deleted_at == ""
    assert store.get_document("web:https://docs.example.com/wrong/guide") is None


def test_web_sync_skips_stale_cleanup_for_root_level_late_head_base(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    guide_id = "web:https://docs.example.com/guide"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(guide_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/index.html": """
                    <p>Index</p>
                    <head><base href="/wrong/" /></head>
                    <a href="guide">Guide</a>
                """,
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
                "https://docs.example.com/wrong/guide": """
                    <html><body><main>Wrong guide</main></body></html>
                """,
            },
            {
                "https://docs.example.com/index.html": {
                    "Content-Type": "text/html",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/index.html").deleted_at == ""
    assert store.get_document(guide_id).deleted_at == ""
    assert store.get_document("web:https://docs.example.com/wrong/guide") is None


def test_web_sync_skips_stale_cleanup_for_text_preceded_head_base(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    guide_id = "web:https://docs.example.com/guide"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(guide_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/index.html": """
                    <!DOCTYPE html>
                    Intro text
                    <head><base href="/wrong/" /></head>
                    <a href="guide">Guide</a>
                """,
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
                "https://docs.example.com/wrong/guide": """
                    <html><body><main>Wrong guide</main></body></html>
                """,
            },
            {
                "https://docs.example.com/index.html": {
                    "Content-Type": "text/html",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/index.html").deleted_at == ""
    assert store.get_document(guide_id).deleted_at == ""
    assert store.get_document("web:https://docs.example.com/wrong/guide") is None


def test_web_sync_skips_stale_cleanup_for_content_preceded_html_head_base(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    guide_id = "web:https://docs.example.com/guide"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(guide_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/index.html": """
                    <p>Intro</p>
                    <html>
                      <head><base href="/wrong/" /></head>
                      <body><main><a href="guide">Guide</a>Index</main></body>
                    </html>
                """,
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
                "https://docs.example.com/wrong/guide": """
                    <html><body><main>Wrong guide</main></body></html>
                """,
            },
            {
                "https://docs.example.com/index.html": {
                    "Content-Type": "text/html",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/index.html").deleted_at == ""
    assert store.get_document(guide_id).deleted_at == ""
    assert store.get_document("web:https://docs.example.com/wrong/guide") is None


def test_web_sync_skips_stale_cleanup_for_title_preceded_head_base(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    guide_id = "web:https://docs.example.com/guide"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(guide_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/index.html": """
                    <title>Index</title>
                    <head><base href="/wrong/" /></head>
                    <a href="guide">Guide</a>
                """,
                "https://docs.example.com/guide": """
                    <html><body><main>Guide</main></body></html>
                """,
                "https://docs.example.com/wrong/guide": """
                    <html><body><main>Wrong guide</main></body></html>
                """,
            },
            {
                "https://docs.example.com/index.html": {
                    "Content-Type": "text/html",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/index.html").deleted_at == ""
    assert store.get_document(guide_id).deleted_at == ""
    assert store.get_document("web:https://docs.example.com/wrong/guide") is None


def test_web_sync_skips_stale_cleanup_for_body_canonical_href(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <body>
                        <main>
                          <link rel="canonical" href="/wrong" />
                          Start
                        </main>
                      </body>
                    </html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/start").deleted_at == ""
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_body_nested_head_canonical(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <body>
                        <main>
                          <head><link rel="canonical" href="/wrong" /></head>
                          Start
                        </main>
                      </body>
                    </html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/start").deleted_at == ""
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_late_document_head_canonical(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <body><main>Start</main></body>
                      <head><link rel="canonical" href="/wrong" /></head>
                    </html>
                """,
            },
            {
                "https://docs.example.com/start": {
                    "Content-Type": "text/html",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/start").deleted_at == ""
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_html_content_preceded_head_canonical(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <main>Start</main>
                      <head><link rel="canonical" href="/wrong" /></head>
                    </html>
                """,
            },
            {
                "https://docs.example.com/start": {
                    "Content-Type": "text/html",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/start").deleted_at == ""
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_head_only_html(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": (
                    "<html><head><title>Only title</title></head></html>"
                ),
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_headerless_html_non_html_body(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide.html": """
                    <html><body><main>Guide</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    guide_id = "web:https://docs.example.com/guide.html"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(guide_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/index.html": (
                    "# Start\n\nSee [Guide](/guide.html).\n"
                ),
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/index.html").deleted_at == ""
    assert store.get_document(guide_id).deleted_at == ""
    assert store.list_chunks_for_document(guide_id)


def test_web_sync_skips_stale_cleanup_for_invalid_port_page_link(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html><body><main>
                      <a href="https://docs.example.com:99999/bad">Bad</a>
                      <p>Start</p>
                    </main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/start").deleted_at == ""
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_malformed_canonical_href(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <head><link rel="canonical" href="https://[::1" /></head>
                      <body><main>Start</main></body>
                    </html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/start").deleted_at == ""
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_credentialed_canonical_href(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <head>
                        <link rel="canonical" href="https://user:secret@docs.example.com/private" />
                      </head>
                      <body><main>Start</main></body>
                    </html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/start").deleted_at == ""
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_sensitive_query_canonical_href(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <head>
                        <link rel="canonical" href="https://docs.example.com/private?token=secret" />
                      </head>
                      <body><main>Start</main></body>
                    </html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/start").deleted_at == ""
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_token_like_page_link(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MutableMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <body>
                        <main>Start</main>
                        <a href="/guide?foo=ghp%5Fsecret123">Guide</a>
                        <a href="/guide?foo=token+%3Dprivatevalue">Token value</a>
                      </body>
                    </html>
                """,
                "https://docs.example.com/guide?foo=ghp%5Fsecret123": """
                    <html><body><main>Secret guide</main></body></html>
                """,
                "https://docs.example.com/guide?foo=token+%3Dprivatevalue": """
                    <html><body><main>Secret guide</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/start").deleted_at == ""
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.get_document("web:https://docs.example.com/guide?foo=ghp%5Fsecret123") is None
    assert store.get_document(
        "web:https://docs.example.com/guide?foo=token+%3Dprivatevalue"
    ) is None
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_session_cookie_jwt_page_links(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=MutableMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <body>
                        <main>Start</main>
                        <a href="/guide?session=privatevalue">Session</a>
                        <a href="/guide?jwt=privatevalue">JWT</a>
                        <a href="/guide?csrf=privatevalue">CSRF</a>
                      </body>
                    </html>
                """,
                "https://docs.example.com/guide?session=privatevalue": """
                    <html><body><main>Secret guide</main></body></html>
                """,
                "https://docs.example.com/guide?jwt=privatevalue": """
                    <html><body><main>Secret guide</main></body></html>
                """,
                "https://docs.example.com/guide?csrf=privatevalue": """
                    <html><body><main>Secret guide</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/start").deleted_at == ""
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.get_document("web:https://docs.example.com/guide?session=privatevalue") is None
    assert store.get_document("web:https://docs.example.com/guide?jwt=privatevalue") is None
    assert store.get_document("web:https://docs.example.com/guide?csrf=privatevalue") is None
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_unsupported_media_response(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide.pdf",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide.pdf": "%PDF-1.7 binary-ish text",
            },
            {
                "https://docs.example.com/guide.pdf": {
                    "Content-Type": "application/pdf",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_mislabelled_media_response(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide.pdf",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide.pdf": "%PDF-1.7 binary-ish text",
            },
            {
                "https://docs.example.com/guide.pdf": {
                    "Content-Type": "text/html",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_media_hinted_html_response(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    media_url = "https://docs.example.com/download?file=guide.pdf"
    second_connector = WebsiteSourceConnector(
        seed_urls=(media_url,),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                media_url: """
                    <html><body><main>Download landing page.</main></body></html>
                """,
            },
            {
                media_url: {
                    "Content-Type": "text/html",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.get_document(f"web:{media_url}").deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_media_hinted_canonical(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <head><link rel="canonical" href="/manual.pdf" /></head>
                      <body><main>Manual landing page.</main></body>
                    </html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/start").deleted_at == ""
    assert store.get_document("web:https://docs.example.com/manual.pdf") is None
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_media_hinted_redirect_to_html(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/logo.png",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MediaRedirectWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/asset": """
                    <html><body><main>Asset landing page.</main></body></html>
                """,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("web:https://docs.example.com/asset").deleted_at == ""
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_text_plain_query_media_response(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    media_url = "https://docs.example.com/download?file=guide.pdf"
    second_connector = WebsiteSourceConnector(
        seed_urls=(media_url,),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                media_url: "%PDF-1.7 binary-ish text",
            },
            {
                media_url: {
                    "Content-Type": "text/plain",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_text_plain_query_key_media_response(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    media_url = "https://docs.example.com/download?logo.svg"
    second_connector = WebsiteSourceConnector(
        seed_urls=(media_url,),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                media_url: "<svg><text>Logo asset</text></svg>",
            },
            {
                media_url: {
                    "Content-Type": "text/plain",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_text_plain_raw_jpeg_response(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    def handler(request):
        if str(request.url) == "https://docs.example.com/robots.txt":
            return httpx.Response(200, content=b"", request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain"},
            content=b"\xff\xd8\xff\xe0\x00\x10JFIF binary-ish jpeg",
            request=request,
        )

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/download",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=WebsiteHTTPClient(
            timeout=1,
            max_response_bytes=1000,
            transport=httpx.MockTransport(handler),
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


@pytest.mark.parametrize(
    "media_body",
    [
        "\ufeff  <svg><text>Logo asset</text></svg>",
        "<!-- generated asset --><svg><text>Logo asset</text></svg>",
        "BZh91AY&SY binary-ish bzip2",
        "<rss><channel><title>Feed asset</title></channel></rss>",
        "<feed><title>Atom asset</title></feed>",
        "a" * 257 + "ustar archive marker",
    ],
)
def test_web_sync_skips_stale_cleanup_for_text_plain_extensionless_media_bodies(
    tmp_path,
    media_body,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/download",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/download": media_body,
            },
            {
                "https://docs.example.com/download": {
                    "Content-Type": "text/plain",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_extensionless_plain_text_doc(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/release-notes",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderMapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/release-notes": "Release notes as plain text.",
            },
            {
                "https://docs.example.com/release-notes": {
                    "Content-Type": "text/plain",
                },
            },
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_supported_extension_media_bytes(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    def handler(request):
        if str(request.url) == "https://docs.example.com/robots.txt":
            return httpx.Response(200, content=b"", request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain"},
            content=b"\x1aE\xdf\xa3\x93B\x82\x88webm",
            request=request,
        )

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/asset.txt",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=WebsiteHTTPClient(
            timeout=1,
            max_response_bytes=1000,
            transport=httpx.MockTransport(handler),
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_headerless_media_response(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide.pdf",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/guide.pdf": "%PDF-1.7 binary-ish text",
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


@pytest.mark.parametrize(
    ("media_url", "media_body"),
    [
        ("https://docs.example.com/logo.svg", "<svg><text>Logo asset</text></svg>"),
        ("https://docs.example.com/logo.png?v=1", "PNG binary-ish text"),
        ("https://docs.example.com/guide%2Epdf", "%PDF-1.7 binary-ish text"),
        ("https://docs.example.com/download", "%PDF-1.7 binary-ish text"),
        ("https://docs.example.com/download?file=guide.pdf", "%PDF-1.7 binary-ish text"),
    ],
)
def test_web_sync_skips_stale_cleanup_for_headerless_media_url_variants(
    tmp_path,
    media_url,
    media_body,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=(media_url,),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                media_url: media_body,
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_skips_stale_cleanup_for_media_redirect_to_extensionless_asset(
    tmp_path,
):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/logo.png",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MediaRedirectWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/asset": "PNG binary-ish text",
            }
        ),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)


def test_web_sync_does_not_cleanup_for_whitespace_only_seed_urls(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_connector = WebsiteSourceConnector(
        seed_urls=("   ",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP({}),
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.FAILED
    assert second_connector.source.enabled is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)
    assert indexer.deleted_ids == []


def test_web_sync_fails_without_stale_cleanup_when_robots_body_is_skipped(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_client = SkippedRobotsWebHTTP(
        {
            "https://docs.example.com/private/secret": """
                <html><body><main>Secret docs.</main></body></html>
            """,
        }
    )
    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/private/secret",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=second_client,
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.FAILED
    assert "robots.txt body was skipped" in second_job.error_message
    assert store.get_document(stale_document_id).deleted_at == ""
    assert store.list_chunks_for_document(stale_document_id)
    assert "https://docs.example.com/private/secret" not in second_client.requested
    assert indexer.deleted_ids == []


def test_web_sync_blocks_bom_prefixed_robots_disallow_before_cleanup(tmp_path):
    store = MetadataStore(tmp_path / "contextwiki.sqlite3")
    indexer = RecordingIndexer()

    first_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/stale",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MapWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/stale": """
                    <html><body><main>Stale</main></body></html>
                """,
            }
        ),
    )
    first_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([first_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    first_job = asyncio.run(first_service.sync_source("source_web"))
    stale_document_id = "web:https://docs.example.com/stale"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(stale_document_id)

    second_client = RecordingMapWebHTTP(
        {
            "https://docs.example.com/robots.txt": (
                "\ufeffUser-agent: *\nDisallow: /private\n"
            ),
            "https://docs.example.com/private/secret": """
                <html><body><main>Secret docs.</main></body></html>
            """,
        }
    )
    second_connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/private/secret",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=second_client,
    )
    second_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([second_connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=indexer,
    )

    second_job = asyncio.run(second_service.sync_source("source_web"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert second_connector.supports_stale_cleanup is False
    assert store.get_document(stale_document_id).deleted_at == ""
    assert "https://docs.example.com/private/secret" not in second_client.requested
