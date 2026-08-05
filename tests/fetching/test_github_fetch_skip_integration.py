"""Integration: GitHub connector + MetadataStore fetch-before-index skip."""

from __future__ import annotations

import asyncio
import base64
import hashlib

import pytest

from core.models import DocumentModel
from core.utils import ContentHasher
from environments.config import AppConfig
from fetching.connectors import GitHubSourceConnector
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.integration


def _sha(label: str) -> str:
    return hashlib.sha1(label.encode("utf-8")).hexdigest()


def _blob_payload(content: bytes) -> dict:
    return {
        "encoding": "base64",
        "content": base64.b64encode(content).decode(),
        "size": len(content),
    }


class SingleBlobGitHubHTTP:
    def __init__(self, *, path="README.md", body=b"# Body\n", sha_label="blob-readme"):
        self.json_urls: list[tuple[str, dict]] = []
        self.path = path
        self.body = body
        self.sha_label = sha_label
        self.sha = _sha(sha_label)

    async def get_json(self, url, headers=None):
        self.json_urls.append((url, headers or {}))
        labelled = url
        for value, label in ((self.sha, self.sha_label), (_sha("tree-main"), "tree-main"), (_sha("commit-main"), "commit-main")):
            labelled = labelled.replace(value, label)
        if "/commits/main" in labelled:
            return {
                "sha": _sha("commit-main"),
                "commit": {"tree": {"sha": _sha("tree-main")}},
            }
        if "/git/trees/" in labelled:
            return {
                "tree": [
                    {
                        "path": self.path,
                        "type": "blob",
                        "sha": self.sha,
                        "size": len(self.body),
                    }
                ]
            }
        if "/git/blobs/" in labelled:
            return _blob_payload(self.body)
        raise AssertionError(f"unexpected GitHub API URL: {url}")


def _stored_github_document(
    document_id: str,
    *,
    content: str,
    version_id: str,
    path: str = "README.md",
) -> DocumentModel:
    return DocumentModel(
        id=document_id,
        document_id=document_id,
        external_id=document_id,
        source_id="source_github",
        title="Stored GitHub File",
        content=content,
        url=f"https://github.com/eunhwa99/context-zip/blob/main/{path}",
        canonical_url=f"https://github.com/eunhwa99/context-zip/blob/main/{path}",
        platform="GitHub",
        path=path,
        version_id=version_id,
        content_hash=ContentHasher.hash_content(content),
    )


def _blob_urls(client: SingleBlobGitHubHTTP) -> list[str]:
    return [url for url, _ in client.json_urls if "/git/blobs/" in url]


@pytest.mark.integration
def test_github_connector_skips_blob_fetch_for_unchanged_stored_document(
    monkeypatch, tmp_path
):
    client = SingleBlobGitHubHTTP()
    document_id = "github:eunhwa99/context-zip:README.md"
    stored_content = "already indexed github body"
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.upsert_document(
        _stored_github_document(
            document_id,
            content=stored_content,
            version_id=client.sha,
        )
    )

    list_calls: list[object] = []
    get_calls: list[str] = []
    batch_calls: list[list[str]] = []
    original_list = store.list_documents
    original_get = store.get_document

    def tracking_list_documents(*args, **kwargs):
        list_calls.append({"args": args, "kwargs": kwargs})
        return original_list(*args, **kwargs)

    def tracking_get_document(document_id_):
        get_calls.append(document_id_)
        return original_get(document_id_)

    def tracking_batch(document_ids):
        batch_calls.append(list(document_ids))
        return {
            doc_id: original_get(doc_id)
            for doc_id in document_ids
            if original_get(doc_id) is not None
        }

    store.list_documents = tracking_list_documents  # type: ignore[method-assign]
    store.get_document = tracking_get_document  # type: ignore[method-assign]
    store.get_documents_for_fetch_reuse = tracking_batch  # type: ignore[method-assign]

    captured: dict[str, object] = {}
    from fetching.github import GitHubRepositoryFetcher

    original_fetch = GitHubRepositoryFetcher.fetch_documents

    async def spy_fetch_documents(self, *args, **kwargs):
        captured["existing_documents_loader"] = kwargs.get("existing_documents_loader")
        return await original_fetch(self, *args, **kwargs)

    monkeypatch.setattr(GitHubRepositoryFetcher, "fetch_documents", spy_fetch_documents)

    connector = GitHubSourceConnector(
        ("eunhwa99/context-zip@main",),
        AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=client,
        metadata_store=store,
    )
    documents = asyncio.run(connector.fetch_documents())

    assert _blob_urls(client) == []
    assert len(documents) == 1
    assert documents[0].document_id == document_id
    assert documents[0].content == stored_content
    assert list_calls == [], "must not browse full corpus via list_documents"
    assert get_calls == [], "hydrate must use batch API, not per-id get_document"
    assert batch_calls == [[document_id]]
    loader = captured.get("existing_documents_loader")
    assert callable(loader)
    loaded = loader([document_id])  # type: ignore[operator]
    assert document_id in loaded
    assert loaded[document_id].content == stored_content
    assert loaded[document_id].version_id == client.sha


@pytest.mark.integration
def test_github_connector_fetches_when_stored_version_id_differs(tmp_path):
    client = SingleBlobGitHubHTTP(body=b"# Fresh body\n")
    document_id = "github:eunhwa99/context-zip:README.md"
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    store.upsert_document(
        _stored_github_document(
            document_id,
            content="# Old body\n",
            version_id=_sha("blob-old"),
        )
    )

    connector = GitHubSourceConnector(
        ("eunhwa99/context-zip@main",),
        AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=client,
        metadata_store=store,
    )
    documents = asyncio.run(connector.fetch_documents())

    assert _blob_urls(client)
    assert documents[0].content == "# Fresh body\n"


@pytest.mark.integration
def test_build_ingestion_runtime_wires_metadata_store_onto_github_connector(tmp_path):
    from app_runtime import build_ingestion_runtime

    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "context_zip.sqlite3",
        cache_dir=str(tmp_path / "cache"),
        github_repositories=("eunhwa99/context-zip@main",),
    )

    class FakeCollection:
        pass

    class FakeVectorStore:
        def __init__(self, chroma_collection):
            self.chroma_collection = chroma_collection

    class FakeStorageContext:
        @staticmethod
        def from_defaults(vector_store):
            return {"vector_store": vector_store}

    class FakeIndexer:
        def __init__(self, config, chroma_collection, storage_context):
            self.config = config

    runtime = build_ingestion_runtime(
        config=config,
        notion_api_key="",
        tistory_blog_name="",
        github_token="github-secret",
        setup_chroma_fn=lambda _config: FakeCollection(),
        vector_store_cls=FakeVectorStore,
        storage_context_cls=FakeStorageContext,
        indexer_cls=FakeIndexer,
    )
    connector = runtime.source_registry.get_connector("source_github")

    assert isinstance(connector, GitHubSourceConnector)
    assert connector.metadata_store is runtime.metadata_store
