"""Deterministic E2E: unchanged GitHub blobs skip download on second sync."""

from __future__ import annotations

import asyncio
import base64
import hashlib

import pytest

from core.models import SyncJobStatus
from environments.config import AppConfig
from fetching.connectors import GitHubSourceConnector, SourceRegistry
from indexing.chunker import DocumentChunker
from indexing.ingestion_service import IngestionService
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.e2e


def _sha(label: str) -> str:
    return hashlib.sha1(label.encode("utf-8")).hexdigest()


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


class TrackingGitHubHTTP:
    def __init__(self):
        self.json_urls: list[str] = []
        self.remote_paths = ["README.md", "docs/peer.md"]
        self.bodies = {
            "README.md": b"# Deterministic GitHub body for fetch-skip E2E.\n",
            "docs/peer.md": b"# Peer doc removed on second sync.\n",
        }
        self.shas = {
            "README.md": _sha("blob-readme"),
            "docs/peer.md": _sha("blob-peer"),
        }

    async def get_json(self, url, headers=None):
        self.json_urls.append(url)
        if "/commits/main" in url:
            return {
                "sha": _sha("commit-main"),
                "commit": {"tree": {"sha": _sha("tree-main")}},
            }
        if "/git/trees/" in url:
            return {
                "tree": [
                    {
                        "path": path,
                        "type": "blob",
                        "sha": self.shas[path],
                        "size": len(self.bodies[path]),
                    }
                    for path in self.remote_paths
                ]
            }
        for path, sha in self.shas.items():
            if sha in url or f"/git/blobs/{sha}" in url:
                return _blob_payload(self.bodies[path])
            # labelled path fallback when sha hex is inlined
            label = "blob-readme" if path == "README.md" else "blob-peer"
            if f"/git/blobs/{label}" in url.replace(sha, label):
                return _blob_payload(self.bodies[path])
        # Match by sha substring in URL
        for path, sha in self.shas.items():
            if sha in url:
                return _blob_payload(self.bodies[path])
        raise AssertionError(f"unexpected GitHub API URL: {url}")


def _blob_fetch_urls(client: TrackingGitHubHTTP) -> list[str]:
    return [url for url in client.json_urls if "/git/blobs/" in url]


def test_second_github_sync_skips_blob_fetch_for_unchanged_files(tmp_path):
    client = TrackingGitHubHTTP()
    client.remote_paths = ["README.md"]
    document_id = "github:eunhwa99/context-zip:README.md"

    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    connector = GitHubSourceConnector(
        ("eunhwa99/context-zip@main",),
        AppConfig(
            chroma_db_path=tmp_path / "chroma",
            metadata_db_path=tmp_path / "context_zip.sqlite3",
            cache_dir=str(tmp_path / "cache"),
            github_max_files=10,
            github_max_file_bytes=1000,
        ),
        http_client=client,
        metadata_store=store,
    )
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    first_job = asyncio.run(ingestion.sync_source("source_github"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert _blob_fetch_urls(client)
    assert store.get_document(document_id) is not None
    assert store.get_document(document_id).content == client.bodies["README.md"].decode()
    assert store.get_document(document_id).version_id == client.shas["README.md"]

    client.json_urls.clear()
    second_job = asyncio.run(ingestion.sync_source("source_github"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert _blob_fetch_urls(client) == []
    assert store.get_document(document_id).content == client.bodies["README.md"].decode()


def test_skipped_unchanged_github_blob_is_not_tombstoned_when_peer_disappears(tmp_path):
    client = TrackingGitHubHTTP()
    kept_id = "github:eunhwa99/context-zip:README.md"
    removed_id = "github:eunhwa99/context-zip:docs/peer.md"

    store = MetadataStore(tmp_path / "context_zip.sqlite3")
    connector = GitHubSourceConnector(
        ("eunhwa99/context-zip@main",),
        AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=client,
        metadata_store=store,
    )
    ingestion = IngestionService(
        metadata_store=store,
        source_registry=SourceRegistry([connector]),
        chunker=DocumentChunker(max_chars=120, overlap_chars=0),
        indexer=RecordingIndexer(),
    )

    first_job = asyncio.run(ingestion.sync_source("source_github"))
    assert first_job.status == SyncJobStatus.SUCCEEDED
    assert store.get_document(kept_id).deleted_at == ""
    assert store.get_document(removed_id).deleted_at == ""

    client.json_urls.clear()
    client.remote_paths = ["README.md"]
    second_job = asyncio.run(ingestion.sync_source("source_github"))

    assert second_job.status == SyncJobStatus.SUCCEEDED
    assert _blob_fetch_urls(client) == [], "kept blob must skip download"
    kept = store.get_document(kept_id)
    removed = store.get_document(removed_id)
    assert kept is not None
    assert kept.deleted_at == ""
    assert kept.last_seen_at
    assert kept.last_seen_sync_id == second_job.job_id
    assert kept.content == client.bodies["README.md"].decode()
    assert removed is not None
    assert removed.deleted_at
