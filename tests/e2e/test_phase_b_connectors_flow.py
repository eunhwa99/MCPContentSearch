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
from indexing.sync_worker import SyncWorker
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


async def _call_tool_json_async(mcp: FastMCP, name: str, arguments: dict | None = None) -> dict:
    blocks = await mcp.call_tool(name, arguments or {})
    return json.loads(blocks[0].text)


async def _wait_for_sync_completion(mcp: FastMCP, source_id: str, attempts: int = 500) -> dict:
    latest = None
    for _ in range(attempts):
        latest = await _call_tool_json_async(mcp, "get_sync_status", {"source_id": source_id})
        latest_job = latest.get("latest_job") or {}
        if latest_job.get("status") in {"succeeded", "failed"}:
            return latest
        await asyncio.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {source_id} sync completion: {latest}")


async def _wait_for_exact_sync_completion(
    mcp: FastMCP,
    targets: dict[str, str],
    attempts: int = 500,
) -> dict[str, dict]:
    latest_by_source: dict[str, dict] = {}
    for _ in range(attempts):
        for source_id, job_id in targets.items():
            latest_by_source[source_id] = await _call_tool_json_async(
                mcp,
                "get_sync_status",
                {"source_id": source_id, "job_id": job_id},
            )
        if all(
            (latest_by_source[source_id].get("job") or {}).get("status")
            in {"succeeded", "failed"}
            for source_id in targets
        ):
            return latest_by_source
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"Timed out waiting for exact sync completion: {latest_by_source}"
    )


async def _run_next_queued_sync(ingestion: IngestionService):
    claimed = ingestion.metadata_store.claim_next_sync_job()
    assert claimed is not None
    assert claimed.status == SyncJobStatus.RUNNING
    return await ingestion.run_claimed_sync_job(claimed.job_id)


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


class OwnerMultiRepoGitHubHTTP:
    def __init__(self):
        self.urls = []
        self.contents = {
            "alpha-readme": b"# Alpha MCP\n\nAlpha orchestration handbook.\n",
            "beta-guide": b"# Beta citations\n\nBeta citation search guide.\n",
        }

    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        self.urls.append(url)
        if "/users/eunaverse/repos?" in url:
            return [
                {
                    "name": "alpha",
                    "default_branch": "main",
                    "owner": {"login": "eunaverse"},
                },
                {
                    "name": "beta",
                    "default_branch": "stable",
                    "owner": {"login": "eunaverse"},
                },
            ]
        if "/repos/eunaverse/alpha/commits/main" in url:
            return {
                "sha": _sha("alpha-commit"),
                "commit": {"tree": {"sha": _sha("alpha-tree")}},
            }
        if "/repos/eunaverse/beta/commits/stable" in url:
            return {
                "sha": _sha("beta-commit"),
                "commit": {"tree": {"sha": _sha("beta-tree")}},
            }
        if "/repos/eunaverse/alpha/git/trees/alpha-tree" in url:
            return {
                "tree": [
                    {
                        "path": "README.md",
                        "type": "blob",
                        "sha": _sha("alpha-readme"),
                        "size": len(self.contents["alpha-readme"]),
                    }
                ]
            }
        if "/repos/eunaverse/beta/git/trees/beta-tree" in url:
            return {
                "tree": [
                    {
                        "path": "docs/guide.md",
                        "type": "blob",
                        "sha": _sha("beta-guide"),
                        "size": len(self.contents["beta-guide"]),
                    }
                ]
            }
        if "/git/blobs/alpha-readme" in url:
            return _blob_payload(self.contents["alpha-readme"])
        if "/git/blobs/beta-guide" in url:
            return _blob_payload(self.contents["beta-guide"])
        raise AssertionError(f"unexpected GitHub API URL: {url}")


class BlockingOwnerMultiRepoGitHubHTTP(OwnerMultiRepoGitHubHTTP):
    def __init__(self):
        super().__init__()
        self.discovery_started = asyncio.Event()
        self.release_discovery = asyncio.Event()

    async def get_json(self, url, headers=None):
        if "/users/eunaverse/repos?" in url:
            self.discovery_started.set()
            await self.release_discovery.wait()
        return await super().get_json(url, headers=headers)


class OwnerEmptyAndPopulatedGitHubHTTP:
    def __init__(self):
        self.urls = []
        self.content = b"# Populated repository\n\nSearchable owner sync evidence.\n"

    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        self.urls.append(url)
        if "/users/eunaverse/repos?" in url:
            return [
                {
                    "name": "empty",
                    "default_branch": "main",
                    "owner": {"login": "eunaverse"},
                    "size": 0,
                    "pushed_at": None,
                },
                {
                    "name": "populated",
                    "default_branch": "stable",
                    "owner": {"login": "eunaverse"},
                    "size": 1,
                    "pushed_at": "2026-07-29T00:00:00Z",
                },
            ]
        if "/repos/eunaverse/empty/commits/" in url:
            raise AssertionError("confirmed empty repository must not request a commit")
        if "/repos/eunaverse/populated/commits/stable" in url:
            return {
                "sha": _sha("populated-commit"),
                "commit": {"tree": {"sha": _sha("populated-tree")}},
            }
        if "/repos/eunaverse/populated/git/trees/populated-tree" in url:
            return {
                "tree": [
                    {
                        "path": "README.md",
                        "type": "blob",
                        "sha": _sha("populated-readme"),
                        "size": len(self.content),
                    }
                ]
            }
        if "/repos/eunaverse/populated/git/blobs/populated-readme" in url:
            return _blob_payload(self.content)
        raise AssertionError(f"unexpected GitHub API URL: {url}")


class SeedOwnerLifecycleGitHubHTTP:
    def __init__(self):
        self.repositories = {
            "populated": ("old.py", b"print('old populated')\n"),
            "empty": ("old.py", b"print('old empty')\n"),
            "historical-private": ("legacy.py", b"print('private history')\n"),
        }

    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        for repository, (path, content) in self.repositories.items():
            if f"/repos/eunaverse/{repository}/commits/main" in url:
                return {
                    "sha": _sha(f"{repository}-seed-commit"),
                    "commit": {
                        "tree": {"sha": _sha(f"{repository}-seed-tree")}
                    },
                }
            if (
                f"/repos/eunaverse/{repository}/git/trees/"
                f"{repository}-seed-tree"
            ) in url:
                return {
                    "tree": [
                        {
                            "path": path,
                            "type": "blob",
                            "sha": _sha(f"{repository}-seed-blob"),
                            "size": len(content),
                        }
                    ]
                }
            if (
                f"/repos/eunaverse/{repository}/git/blobs/"
                f"{repository}-seed-blob"
            ) in url:
                return _blob_payload(content)
        raise AssertionError(f"unexpected GitHub API URL: {url}")


class OwnerLifecycleGitHubHTTP:
    def __init__(self, *, incomplete=False):
        self.incomplete = incomplete
        self.urls = []
        self.content = b"print('current populated')\n"

    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        self.urls.append(url)
        if "/users/eunaverse/repos?" in url:
            return [
                {
                    "name": "populated",
                    "default_branch": "stable",
                    "owner": {"login": "eunaverse"},
                    "size": 1,
                    "pushed_at": "2026-07-29T00:00:00Z",
                },
                {
                    "name": "empty",
                    "default_branch": "main",
                    "owner": {"login": "eunaverse"},
                    "size": 0,
                    "pushed_at": None,
                },
            ]
        if "/repos/eunaverse/empty/commits/" in url:
            raise AssertionError("confirmed empty repository must not request a commit")
        if "/repos/eunaverse/populated/commits/stable" in url:
            return {
                "sha": _sha("populated-current-commit"),
                "commit": {"tree": {"sha": _sha("populated-current-tree")}},
            }
        if "/repos/eunaverse/populated/git/trees/populated-current-tree" in url:
            if self.incomplete:
                return {
                    "tree": [
                        {
                            "path": "unknown-size.py",
                            "type": "blob",
                            "sha": _sha("populated-unknown-size"),
                        }
                    ]
                }
            return {
                "tree": [
                    {
                        "path": "current.py",
                        "type": "blob",
                        "sha": _sha("populated-current-blob"),
                        "size": len(self.content),
                    }
                ]
            }
        if "/repos/eunaverse/populated/git/blobs/populated-current-blob" in url:
            return _blob_payload(self.content)
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
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
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

    async def run_flow():
        listed = await _call_tool_json_async(mcp, "list_sources")
        sync_job = await _call_tool_json_async(mcp, "sync_source", {"source_id": source_id})
        await _run_next_queued_sync(ingestion)
        status = await _wait_for_sync_completion(mcp, source_id)
        return listed, sync_job, status

    listed, sync_job, status = asyncio.run(run_flow())
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
    answer = asyncio.run(
        answer_service.answer_with_citations(
            query,
            filters={"source_id": source_id},
            top_k=3,
        )
    )

    assert [source["source_id"] for source in listed["sources"]] == [
        "source_notion",
        "source_tistory",
    ]
    assert sync_job["status"] == "queued"
    assert sync_job["source_id"] == source_id
    assert status["source"]["sync_status"] == "succeeded"
    assert status["latest_job"]["status"] == "succeeded"
    assert status["latest_job"]["processed_documents"] == 1
    assert status["latest_job"]["indexed_chunks"] >= 1
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
    http = OwnerMultiRepoGitHubHTTP()
    connector = GitHubSourceConnector(
        repositories=("eunaverse",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=http,
    )
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
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

    async def run_flow():
        listed = await _call_tool_json_async(mcp, "list_sources")
        sync_job = await _call_tool_json_async(mcp, "sync_source", {"source_id": "source_github"})
        await _run_next_queued_sync(ingestion)
        status = await _wait_for_sync_completion(mcp, "source_github")
        return listed, sync_job, status

    listed, sync_job, status = asyncio.run(run_flow())
    alpha_document_id = "github:eunaverse/alpha:README.md"
    beta_document_id = "github:eunaverse/beta:docs/guide.md"
    alpha_chunks = store.list_chunks_for_document(alpha_document_id)
    beta_chunks = store.list_chunks_for_document(beta_document_id)
    alpha_search = _call_tool_json(
        mcp,
        "search_context",
        {
            "query": "alpha orchestration handbook",
            "filters": {"source_id": "source_github"},
            "top_k": 3,
        },
    )
    beta_search = _call_tool_json(
        mcp,
        "search_context",
        {
            "query": "beta citation search guide",
            "filters": {"source_id": "source_github"},
            "top_k": 3,
        },
    )
    fetched_alpha = _call_tool_json(
        mcp,
        "fetch_context",
        {"document_id": alpha_document_id},
    )
    fetched_beta = _call_tool_json(
        mcp,
        "fetch_context",
        {"document_id": beta_document_id},
    )
    fetched_chunk = _call_tool_json(
        mcp,
        "fetch_context",
        {"chunk_id": alpha_search["results"][0]["chunk_id"]},
    )
    answer = asyncio.run(
        answer_service.answer_with_citations(
            "alpha orchestration handbook",
            filters={"source_id": "source_github"},
            top_k=3,
        )
    )

    assert [source["source_id"] for source in listed["sources"]] == ["source_github"]
    assert sync_job["status"] == "queued"
    assert sync_job["source_id"] == "source_github"
    assert status["source"]["sync_status"] == "succeeded"
    assert status["latest_job"]["status"] == "succeeded"
    assert status["latest_job"]["processed_documents"] == 2
    assert status["latest_job"]["indexed_chunks"] >= 2
    assert store.get_source("source_github").sync_status == SyncStatus.SUCCEEDED
    assert store.get_document(alpha_document_id).source_id == "source_github"
    assert store.get_document(beta_document_id).source_id == "source_github"
    assert alpha_chunks
    assert beta_chunks
    assert alpha_chunks[0].path == "README.md"
    assert beta_chunks[0].path == "docs/guide.md"
    assert {document.document_id for document in indexer.documents} == {
        alpha_document_id,
        beta_document_id,
    }
    assert alpha_search["results"][0]["source_id"] == "source_github"
    assert alpha_search["results"][0]["document_id"] == alpha_document_id
    assert beta_search["results"][0]["document_id"] == beta_document_id
    assert fetched_alpha["document"]["document_id"] == alpha_document_id
    assert fetched_alpha["chunks"][0]["chunk_id"] == alpha_chunks[0].chunk_id
    assert fetched_beta["document"]["document_id"] == beta_document_id
    assert fetched_beta["chunks"][0]["chunk_id"] == beta_chunks[0].chunk_id
    assert fetched_chunk["chunk"]["text"] == alpha_chunks[0].text
    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == [alpha_search["results"][0]["chunk_id"]]
    assert connector.cleanup_document_id_prefixes == (
        "github:eunaverse/alpha:",
        "github:eunaverse/beta:",
    )
    assert any("/repos/eunaverse/alpha/commits/main" in url for url in http.urls)
    assert any("/repos/eunaverse/beta/commits/stable" in url for url in http.urls)


def test_retained_owner_sync_all_polls_exact_job_and_keeps_search_flow(tmp_path):
    config = AppConfig(github_max_files=5, github_max_file_bytes=1000)
    metadata_path = tmp_path / "context_zip.sqlite3"
    mcp_connector = GitHubSourceConnector(
        repositories=("eunaverse",),
        config=config,
        http_client=OwnerMultiRepoGitHubHTTP(),
    )
    store = MetadataStore(metadata_path)
    indexer = RecordingIndexer()
    registry = SourceRegistry([mcp_connector])
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=registry,
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
        durable_dispatch=True,
    )
    context_search = ContextSearchService(
        metadata_store=store,
        retriever=indexer.documents,
    )
    answer_service = CitationAnswerService(
        context_search=context_search,
        min_score=0.1,
        min_results=1,
    )
    mcp = FastMCP("retained-owner-sync-all-poll-smoke")
    register_tools(
        mcp,
        ingestion_service=ingestion,
        context_search_service=context_search,
        answer_service=answer_service,
        metadata_store=store,
        source_registry=registry,
    )

    http = BlockingOwnerMultiRepoGitHubHTTP()
    worker_connector = GitHubSourceConnector(
        repositories=("eunaverse",),
        config=config,
        http_client=http,
    )
    worker_store = MetadataStore(metadata_path)
    worker_ingestion = IngestionService(
        metadata_store=worker_store,
        source_registry=SourceRegistry([worker_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
        register_source_config=False,
        durable_dispatch=True,
    )
    worker = SyncWorker(
        worker_ingestion,
        worker_store,
        source_ids=("source_github",),
        poll_interval_seconds=0.1,
    )

    async def run_flow():
        listed = await _call_tool_json_async(mcp, "list_sources")
        launched = await _call_tool_json_async(mcp, "sync_all")
        worker_task = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(http.discovery_started.wait(), timeout=1)
        running = await _call_tool_json_async(
            mcp,
            "get_sync_status",
            {"source_id": ""},
        )
        http.release_discovery.set()
        completed = await asyncio.wait_for(worker_task, timeout=3)
        terminal_by_source = await _wait_for_exact_sync_completion(
            mcp,
            {
                item["source_id"]: item["job"]["job_id"]
                for item in launched["results"]
                if item["launch_outcome"] in {"started", "already_running"}
            },
        )
        return listed, launched, running, terminal_by_source, completed

    listed, launched, running, terminal_by_source, completed = asyncio.run(run_flow())
    alpha_document_id = "github:eunaverse/alpha:README.md"
    beta_document_id = "github:eunaverse/beta:docs/guide.md"
    alpha_search = _call_tool_json(
        mcp,
        "search_context",
        {
            "query": "alpha orchestration handbook",
            "filters": {"source_id": "source_github"},
            "top_k": 3,
        },
    )
    beta_search = _call_tool_json(
        mcp,
        "search_context",
        {
            "query": "beta citation search guide",
            "filters": {"source_id": "source_github"},
            "top_k": 3,
        },
    )
    fetched_alpha = _call_tool_json(
        mcp,
        "fetch_context",
        {"document_id": alpha_document_id},
    )
    fetched_beta = _call_tool_json(
        mcp,
        "fetch_context",
        {"document_id": beta_document_id},
    )
    alpha_answer = asyncio.run(
        answer_service.answer_with_citations(
            "alpha orchestration handbook",
            filters={"source_id": "source_github"},
            top_k=3,
        )
    )
    beta_answer = asyncio.run(
        answer_service.answer_with_citations(
            "beta citation search guide",
            filters={"source_id": "source_github"},
            top_k=3,
        )
    )

    assert [source["source_id"] for source in listed["sources"]] == ["source_github"]
    assert launched["status"] == "accepted"
    assert launched["summary"]["started"] == 1
    assert len(launched["results"]) == 1
    launched_result = launched["results"][0]
    assert launched_result["source_id"] == "source_github"
    assert launched_result["launch_outcome"] == "started"
    assert launched_result["job"]["status"] == "queued"
    launched_job_id = launched_result["job"]["job_id"]
    assert completed.job_id == launched_job_id
    assert completed.status == SyncJobStatus.SUCCEEDED

    running_item = running["sources"][0]
    assert running_item["source"]["source_id"] == "source_github"
    assert running_item["latest_job"]["status"] == "running"
    assert running_item["latest_job"]["job_id"] == launched_job_id

    terminal_item = terminal_by_source["source_github"]
    assert terminal_item["source"]["sync_status"] == "succeeded"
    assert terminal_item["job"]["status"] == "succeeded"
    assert terminal_item["job"]["job_id"] == launched_job_id
    assert terminal_item["job"]["processed_documents"] == 2
    assert store.get_document(alpha_document_id).deleted_at == ""
    assert store.get_document(beta_document_id).deleted_at == ""
    assert alpha_search["results"][0]["document_id"] == alpha_document_id
    assert beta_search["results"][0]["document_id"] == beta_document_id
    assert fetched_alpha["document"]["document_id"] == alpha_document_id
    assert fetched_beta["document"]["document_id"] == beta_document_id
    assert alpha_answer["evidence_status"] == "grounded"
    assert beta_answer["evidence_status"] == "grounded"
    assert alpha_answer["used_chunks"] == [
        alpha_search["results"][0]["chunk_id"]
    ]
    assert beta_answer["used_chunks"] == [
        beta_search["results"][0]["chunk_id"]
    ]
    assert worker_connector.cleanup_document_id_prefixes == (
        "github:eunaverse/alpha:",
        "github:eunaverse/beta:",
    )


def test_retained_owner_sync_keeps_confirmed_empty_repo_in_cleanup_scope(tmp_path):
    config = AppConfig(github_max_files=5, github_max_file_bytes=1000)
    metadata_path = tmp_path / "context_zip.sqlite3"
    mcp_connector = GitHubSourceConnector(
        repositories=("eunaverse",),
        config=config,
        http_client=OwnerEmptyAndPopulatedGitHubHTTP(),
    )
    store = MetadataStore(metadata_path)
    indexer = RecordingIndexer()
    registry = SourceRegistry([mcp_connector])
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=registry,
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
        durable_dispatch=True,
    )
    context_search = ContextSearchService(
        metadata_store=store,
        retriever=indexer.documents,
    )
    answer_service = CitationAnswerService(
        context_search=context_search,
        min_score=0.1,
        min_results=1,
    )
    mcp = FastMCP("retained-owner-empty-repo-smoke")
    register_tools(
        mcp,
        ingestion_service=ingestion,
        context_search_service=context_search,
        answer_service=answer_service,
        metadata_store=store,
        source_registry=registry,
    )

    http = OwnerEmptyAndPopulatedGitHubHTTP()
    worker_connector = GitHubSourceConnector(
        repositories=("eunaverse",),
        config=config,
        http_client=http,
    )
    worker_store = MetadataStore(metadata_path)
    worker_ingestion = IngestionService(
        metadata_store=worker_store,
        source_registry=SourceRegistry([worker_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
        register_source_config=False,
        durable_dispatch=True,
    )
    worker = SyncWorker(
        worker_ingestion,
        worker_store,
        source_ids=("source_github",),
        poll_interval_seconds=0.1,
    )

    async def run_flow():
        listed = await _call_tool_json_async(mcp, "list_sources")
        sync_job = await _call_tool_json_async(
            mcp,
            "sync_source",
            {"source_id": "source_github"},
        )
        completed = await worker.run_once()
        status = await _wait_for_sync_completion(mcp, "source_github")
        return listed, sync_job, status, completed

    listed, sync_job, status, completed = asyncio.run(run_flow())
    document_id = "github:eunaverse/populated:README.md"
    search_result = _call_tool_json(
        mcp,
        "search_context",
        {
            "query": "searchable owner sync evidence",
            "filters": {"source_id": "source_github"},
            "top_k": 3,
        },
    )
    fetched = _call_tool_json(
        mcp,
        "fetch_context",
        {"document_id": document_id},
    )
    answer = asyncio.run(
        answer_service.answer_with_citations(
            "searchable owner sync evidence",
            filters={"source_id": "source_github"},
            top_k=3,
        )
    )

    assert [source["source_id"] for source in listed["sources"]] == ["source_github"]
    assert sync_job["status"] == "queued"
    assert completed.job_id == sync_job["job_id"]
    assert completed.status == SyncJobStatus.SUCCEEDED
    assert status["source"]["sync_status"] == "succeeded"
    assert status["latest_job"]["status"] == "succeeded"
    assert status["latest_job"]["processed_documents"] == 1
    assert store.get_document(document_id).deleted_at == ""
    assert search_result["results"][0]["document_id"] == document_id
    assert fetched["document"]["document_id"] == document_id
    assert answer["evidence_status"] == "grounded"
    assert answer["used_chunks"] == [search_result["results"][0]["chunk_id"]]
    assert worker_connector.cleanup_document_id_prefixes == (
        "github:eunaverse/empty:",
        "github:eunaverse/populated:",
    )
    assert worker_connector.supports_stale_cleanup is True
    assert not any("/repos/eunaverse/empty/commits/" in url for url in http.urls)


def test_github_connector_syncs_through_ingestion_service(tmp_path):
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@main",),
        config=AppConfig(github_max_files=5, github_max_file_bytes=1000),
        http_client=FakeGitHubHTTP(),
    )
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    indexer = RecordingIndexer()
    service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    job = asyncio.run(service.sync_source("source_github"))
    chunks = store.list_chunks_for_document(
        "github:eunhwa99/context-zip:api/tools.py"
    )

    assert job.status == SyncJobStatus.SUCCEEDED
    assert store.get_source("source_github").sync_status == SyncStatus.SUCCEEDED
    assert chunks[0].path == "api/tools.py"
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 2
    assert indexer.documents[0].source_id == "source_github"
    assert indexer.documents[0].document_id == (
        "github:eunhwa99/context-zip:api/tools.py"
    )


def test_owner_cleanup_lifecycle_is_scoped_and_incomplete_followup_is_safe(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    indexer = RecordingIndexer()
    config = AppConfig(github_max_files=5, github_max_file_bytes=1000)

    seed_connector = GitHubSourceConnector(
        repositories=(
            "eunaverse/populated@main",
            "eunaverse/empty@main",
            "eunaverse/historical-private@main",
        ),
        config=config,
        http_client=SeedOwnerLifecycleGitHubHTTP(),
        allow_stale_cleanup=False,
    )
    seed_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([seed_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    seed_job = asyncio.run(seed_service.sync_source("source_github"))
    populated_stale_id = "github:eunaverse/populated:old.py"
    empty_stale_id = "github:eunaverse/empty:old.py"
    historical_private_id = "github:eunaverse/historical-private:legacy.py"
    assert seed_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document(populated_stale_id).deleted_at == ""
    assert store.get_document(empty_stale_id).deleted_at == ""
    assert store.get_document(historical_private_id).deleted_at == ""

    complete_http = OwnerLifecycleGitHubHTTP()
    complete_connector = GitHubSourceConnector(
        repositories=("eunaverse",),
        config=config,
        http_client=complete_http,
    )
    complete_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([complete_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    complete_job = asyncio.run(complete_service.sync_source("source_github"))
    current_id = "github:eunaverse/populated:current.py"

    assert complete_job.status == SyncJobStatus.SUCCEEDED
    assert complete_connector.supports_stale_cleanup is True
    assert complete_connector.cleanup_document_id_prefixes == (
        "github:eunaverse/populated:",
        "github:eunaverse/empty:",
    )
    assert store.get_document(populated_stale_id).deleted_at != ""
    assert store.get_document(empty_stale_id).deleted_at != ""
    assert store.get_document(historical_private_id).deleted_at == ""
    assert store.get_document(current_id).deleted_at == ""
    assert not any(
        "/repos/eunaverse/empty/commits/" in url for url in complete_http.urls
    )

    incomplete_connector = GitHubSourceConnector(
        repositories=("eunaverse",),
        config=config,
        http_client=OwnerLifecycleGitHubHTTP(incomplete=True),
    )
    incomplete_service = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([incomplete_connector]),
        chunker=DocumentChunker(max_chars=80, overlap_chars=0),
        indexer=indexer,
    )

    incomplete_job = asyncio.run(incomplete_service.sync_source("source_github"))

    assert incomplete_job.status == SyncJobStatus.SUCCEEDED
    assert incomplete_connector.supports_stale_cleanup is False
    assert store.get_document(current_id).deleted_at == ""
    assert store.list_chunks_for_document(current_id)
    assert store.get_document(historical_private_id).deleted_at == ""


def test_github_sync_skips_stale_cleanup_when_file_cap_is_exceeded(tmp_path):
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    indexer = RecordingIndexer()

    first_connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@main",),
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
    b_document_id = "github:eunhwa99/context-zip:b.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(b_document_id)
    assert first_connector.supports_stale_cleanup is True

    second_connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@main",),
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
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    indexer = RecordingIndexer()

    first_configured_connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@main",),
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
    stale_configured_document_id = "github:eunhwa99/context-zip:old.py"
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
        repositories=("eunhwa99/context-zip@main",),
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
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
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
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    indexer = RecordingIndexer()

    first_connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@main",),
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
    document_id = "github:eunhwa99/context-zip:a.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(document_id)

    second_connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@main",),
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
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    indexer = RecordingIndexer()

    first_connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@main",),
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
    document_id = "github:eunhwa99/context-zip:a.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(document_id)

    second_connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@main",),
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
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    indexer = RecordingIndexer()

    first_connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@main",),
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
    document_id = "github:eunhwa99/context-zip:a.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(document_id)

    second_connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@main",),
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
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    indexer = RecordingIndexer()

    first_connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@main",),
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
    large_document_id = "github:eunhwa99/context-zip:large.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(large_document_id)
    assert first_connector.supports_stale_cleanup is True

    second_connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@main",),
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
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    indexer = RecordingIndexer()

    first_connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@main",),
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
    other_document_id = "github:eunhwa99/context-zip:other.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(other_document_id)

    second_connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@main",),
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
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    indexer = RecordingIndexer()

    first_connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@main",),
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
    document_id = "github:eunhwa99/context-zip:api/tools.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(document_id)

    second_connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@release",),
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
    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    indexer = RecordingIndexer()

    first_connector = GitHubSourceConnector(
        repositories=("eunhwa99/context-zip@main",),
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
    document_id = "github:eunhwa99/context-zip:api/tools.py"
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.list_chunks_for_document(document_id)

    second_connector = GitHubSourceConnector(
        repositories=("EUNHWA99/context-zip@main",),
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
