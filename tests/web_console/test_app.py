from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from core.models import SourceModel, SourceType, SyncJobModel, SyncJobStatus, SyncStatus
from web_console.app import ConsoleDependencies, create_console_app


pytestmark = pytest.mark.integration


class FakeAnswerService:
    def __init__(self):
        self.calls = []

    async def answer_with_citations(self, question, filters=None, top_k=5):
        self.calls.append({"question": question, "filters": filters, "top_k": top_k})
        return {
            "question": question,
            "answer": "ContextWiki evidence",
            "evidence_status": "grounded",
            "citations": [{"chunk_id": "chunk-1", "title": "README"}],
            "used_chunks": ["chunk-1"],
        }


class FakeWikiService:
    def __init__(self):
        self.calls = []

    async def generate_wiki_page(self, topic, filters=None, top_k=8):
        self.calls.append({"topic": topic, "filters": filters, "top_k": top_k})
        return {
            "topic": topic,
            "status": "generated",
            "title": f"{topic} Wiki",
            "markdown": "# ContextWiki\n\nGenerated page [C1]\n",
            "sections": [{"heading": "Overview", "content": "Generated page [C1]"}],
            "citations": [{"marker": "C1", "chunk_id": "chunk-1"}],
            "backlinks": [{"document_id": "doc-1", "chunk_ids": ["chunk-1"]}],
            "used_chunks": ["chunk-1"],
        }


class FakeMetadataStore:
    def list_sources(self):
        return [
            SourceModel(
                source_id="source_github",
                source_type=SourceType.GITHUB,
                name="MCPContentSearch",
                sync_status=SyncStatus.SUCCEEDED,
            ),
            SourceModel(
                source_id="source_notion",
                source_type=SourceType.NOTION,
                name="ContextWiki Notes",
            ),
        ]

    def get_source(self, source_id):
        return next(
            (source for source in self.list_sources() if source.source_id == source_id),
            None,
        )

    def get_latest_sync_job(self, source_id):
        if source_id != "source_github":
            return None
        return SyncJobModel(
            job_id="job-1",
            source_id="source_github",
            status=SyncJobStatus.SUCCEEDED,
            total_documents=2,
            processed_documents=1,
            indexed_chunks=4,
        )


class FakeIngestionService:
    def __init__(self):
        self.calls = []

    async def sync_source(self, source_id):
        self.calls.append(source_id)
        return SyncJobModel(
            job_id="job-sync",
            source_id=source_id,
            status=SyncJobStatus.SUCCEEDED,
            total_documents=3,
            processed_documents=2,
            indexed_chunks=5,
        )


class FakeGitHubSyncService:
    def __init__(self):
        self.calls = []

    async def sync_target(self, target):
        self.calls.append(target)
        return {
            "status": "succeeded",
            "source_id": "source_github",
            "target": target,
            "repository_count": 2,
            "repositories": [
                "eunhwa99/algorithms@main",
                "eunhwa99/neetcode@main",
            ],
            "stale_cleanup": "disabled",
            "job": {
                "job_id": "job-github",
                "source_id": "source_github",
                "status": "succeeded",
                "total_documents": 7,
                "processed_documents": 7,
                "indexed_chunks": 12,
                "skipped_documents": 0,
                "error_message": "",
            },
        }


class FakeSmokeRunner:
    def __init__(self):
        self.calls = []

    async def run_fake(self, *, topic=None):
        self.calls.append({"mode": "fake", "topic": topic})
        return {
            "mode": "fake",
            "status": "passed",
            "wiki_status": "generated",
            "citations": 1,
            "backlinks": 1,
            "used_chunks": 1,
        }

    async def run_github(self, *, topic=None, github_repository="", require_generated=False):
        self.calls.append(
            {
                "mode": "github",
                "topic": topic,
                "github_repository": github_repository,
                "require_generated": require_generated,
            }
        )
        return {
            "mode": "github",
            "status": "skipped",
            "reason": "No GitHub repository configured.",
        }


class FailingSmokeRunner:
    async def run_fake(self, *, topic=None):
        raise RuntimeError("fake smoke failed with token=secret-value")

    async def run_github(self, *, topic=None, github_repository="", require_generated=False):
        raise RuntimeError("github smoke failed")


class FailingAnswerService:
    async def answer_with_citations(self, question, filters=None, top_k=5):
        raise RuntimeError("answer failed with token=secret-value")


class FailingWikiService:
    async def generate_wiki_page(self, topic, filters=None, top_k=8):
        raise RuntimeError("wiki failed with token=secret-value")


class FailingMetadataStore:
    def list_sources(self):
        raise RuntimeError("sources failed with token=secret-value")

    def get_source(self, source_id):
        raise RuntimeError("source failed with token=secret-value")

    def get_latest_sync_job(self, source_id):
        raise RuntimeError("job failed with token=secret-value")


class FailingIngestionService:
    async def sync_source(self, source_id):
        raise RuntimeError("sync failed with token=secret-value")


class FailingGitHubSyncService:
    async def sync_target(self, target):
        raise RuntimeError("github sync failed with token=secret-value")


class SecretMetadataStore(FakeMetadataStore):
    def list_sources(self):
        return [
            SourceModel(
                source_id="source_github",
                source_type=SourceType.GITHUB,
                name="MCPContentSearch",
                sync_status=SyncStatus.FAILED,
                auth_ref="token=secret-value",
                last_error="source failed with token=secret-value",
            ),
        ]

    def get_latest_sync_job(self, source_id):
        return SyncJobModel(
            job_id="job-secret",
            source_id=source_id,
            status=SyncJobStatus.FAILED,
            error_message="sync failed with token=secret-value",
        )


class SecretJobIngestionService:
    async def sync_source(self, source_id):
        return SyncJobModel(
            job_id="job-secret",
            source_id=source_id,
            status=SyncJobStatus.FAILED,
            error_message="sync failed with token=secret-value",
        )


class SecretPayloadGitHubSyncService:
    async def sync_target(self, target):
        return {
            "status": "failed",
            "source_id": "source_github",
            "target": target,
            "job": {
                "job_id": "job-secret",
                "source_id": "source_github",
                "status": "failed",
                "error_message": "github sync failed with token=secret-value",
            },
        }


def make_client():
    answer_service = FakeAnswerService()
    wiki_service = FakeWikiService()
    ingestion_service = FakeIngestionService()
    github_sync_service = FakeGitHubSyncService()
    smoke_runner = FakeSmokeRunner()
    app = create_console_app(
        ConsoleDependencies(
            answer_service=answer_service,
            wiki_service=wiki_service,
            metadata_store=FakeMetadataStore(),
            ingestion_service=ingestion_service,
            github_sync_service=github_sync_service,
            smoke_runner=smoke_runner,
        )
    )
    return (
        TestClient(app),
        answer_service,
        wiki_service,
        smoke_runner,
        ingestion_service,
        github_sync_service,
    )


def make_unconfigured_client():
    return TestClient(create_console_app(ConsoleDependencies()))


def test_health_marks_console_as_local_only():
    client, *_ = make_client()

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "contextwiki-web-console",
        "local_only": True,
    }


def test_rejects_non_loopback_clients():
    app = create_console_app(ConsoleDependencies())
    client = TestClient(app, client=("203.0.113.10", 50000))

    response = client.get("/api/health")

    assert response.status_code == 403
    assert response.json()["detail"] == "web console is local-only"


def test_rejects_loopback_client_with_untrusted_host():
    app = create_console_app(ConsoleDependencies())
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.get("/api/health", headers={"host": "attacker.example"})

    assert response.status_code == 403
    assert response.json()["detail"] == "web console host is not local"


def test_rejects_post_with_untrusted_origin():
    client, *_ = make_client()

    response = client.post(
        "/api/answer",
        json={"question": "What is ContextWiki?"},
        headers={"origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "web console origin is not local"


def test_remote_override_still_rejects_untrusted_origin(monkeypatch):
    monkeypatch.setenv("CONTEXTWIKI_WEB_CONSOLE_ALLOW_REMOTE", "true")
    client, *_ = make_client()

    response = client.post(
        "/api/answer",
        json={"question": "What is ContextWiki?"},
        headers={"origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "web console origin is not local"


def test_remote_override_rejects_missing_host(monkeypatch):
    monkeypatch.setenv("CONTEXTWIKI_WEB_CONSOLE_ALLOW_REMOTE", "true")
    app = create_console_app(ConsoleDependencies())
    client = TestClient(app, client=("203.0.113.10", 50000))

    response = client.get("/api/health", headers={"host": ""})

    assert response.status_code == 403
    assert response.json()["detail"] == "web console host is not local"


def test_rejects_local_prefix_suffix_host():
    app = create_console_app(ConsoleDependencies())
    client = TestClient(app, client=("127.0.0.1", 50000))

    response = client.get("/api/health", headers={"host": "localhost:8765.evil.com"})

    assert response.status_code == 403
    assert response.json()["detail"] == "web console host is not local"


def test_rejects_local_prefix_suffix_origin():
    client, *_ = make_client()

    response = client.post(
        "/api/answer",
        json={"question": "What is ContextWiki?"},
        headers={"origin": "http://127.0.0.1:8765.evil.com"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "web console origin is not local"


def test_accepts_bracketed_ipv6_loopback_host():
    app = create_console_app(ConsoleDependencies())
    client = TestClient(app, client=("::1", 50000))

    response = client.get("/api/health", headers={"host": "[::1]:8765"})

    assert response.status_code == 200


def test_sources_returns_metadata_store_sources():
    client, *_ = make_client()

    response = client.get("/api/sources")

    assert response.status_code == 200
    assert [source["source_id"] for source in response.json()["sources"]] == [
        "source_github",
        "source_notion",
    ]


def test_sources_returns_empty_list_without_metadata_store():
    client = make_unconfigured_client()

    response = client.get("/api/sources")

    assert response.status_code == 200
    assert response.json() == {"sources": []}


def test_sources_endpoint_returns_structured_failure_without_logging_secret(caplog):
    app = create_console_app(
        ConsoleDependencies(metadata_store=FailingMetadataStore())
    )
    client = TestClient(app)

    with caplog.at_level(logging.ERROR, logger="web_console.app"):
        response = client.get("/api/sources")

    assert response.status_code == 200
    assert response.json() == {
        "sources": [],
        "status": "error",
        "message": "Source listing failed. See server logs for details.",
    }
    assert "secret-value" not in caplog.text
    assert "token=secret-value" not in caplog.text


def test_sources_endpoint_redacts_persisted_source_errors():
    app = create_console_app(ConsoleDependencies(metadata_store=SecretMetadataStore()))
    client = TestClient(app)

    response = client.get("/api/sources")

    assert response.status_code == 200
    body = response.json()
    assert body["sources"][0]["last_error"] == "Source sync failed. See server logs for details."
    assert body["sources"][0]["auth_ref"] == "redacted"
    assert "secret-value" not in str(body)
    assert "token=secret-value" not in str(body)


def test_source_sync_status_returns_source_and_latest_job():
    client, *_ = make_client()

    response = client.get("/api/sources/source_github/sync-status")

    assert response.status_code == 200
    assert response.json()["source"]["source_id"] == "source_github"
    assert response.json()["latest_job"]["job_id"] == "job-1"
    assert response.json()["latest_job"]["indexed_chunks"] == 4


def test_source_sync_status_returns_503_without_metadata_store():
    client = make_unconfigured_client()

    response = client.get("/api/sources/source_github/sync-status")

    assert response.status_code == 503
    assert response.json()["detail"] == "metadata store is not configured"


def test_source_sync_status_returns_structured_failure_without_logging_secret(caplog):
    app = create_console_app(ConsoleDependencies(metadata_store=FailingMetadataStore()))
    client = TestClient(app)

    with caplog.at_level(logging.ERROR, logger="web_console.app"):
        response = client.get("/api/sources/source_github/sync-status")

    assert response.status_code == 200
    assert response.json() == {
        "source_id": "source_github",
        "source": None,
        "latest_job": None,
        "status": "error",
        "message": "Source sync status failed. See server logs for details.",
    }
    assert "secret-value" not in caplog.text
    assert "token=secret-value" not in caplog.text


def test_source_sync_status_redacts_persisted_source_and_job_errors():
    app = create_console_app(ConsoleDependencies(metadata_store=SecretMetadataStore()))
    client = TestClient(app)

    response = client.get("/api/sources/source_github/sync-status")

    assert response.status_code == 200
    body = response.json()
    assert body["source"]["last_error"] == "Source sync failed. See server logs for details."
    assert body["source"]["auth_ref"] == "redacted"
    assert body["latest_job"]["error_message"] == "Sync failed. See server logs for details."
    assert "secret-value" not in str(body)
    assert "token=secret-value" not in str(body)


def test_source_sync_endpoint_delegates_to_ingestion_service():
    client, *_, ingestion_service, _ = make_client()

    response = client.post("/api/sources/source_github/sync", json={})

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["indexed_chunks"] == 5
    assert ingestion_service.calls == ["source_github"]


def test_source_sync_endpoint_returns_503_without_ingestion_service():
    client = make_unconfigured_client()

    response = client.post("/api/sources/source_github/sync", json={})

    assert response.status_code == 503
    assert response.json()["detail"] == "ingestion service is not configured"


def test_source_sync_endpoint_returns_structured_failure_without_logging_secret(caplog):
    app = create_console_app(
        ConsoleDependencies(ingestion_service=FailingIngestionService())
    )
    client = TestClient(app)

    with caplog.at_level(logging.ERROR, logger="web_console.app"):
        response = client.post("/api/sources/source_github/sync", json={})

    assert response.status_code == 200
    assert response.json() == {
        "source_id": "source_github",
        "status": "error",
        "message": "Source sync failed. See server logs for details.",
    }
    assert "secret-value" not in caplog.text
    assert "token=secret-value" not in caplog.text


def test_source_sync_endpoint_redacts_returned_job_error():
    app = create_console_app(
        ConsoleDependencies(ingestion_service=SecretJobIngestionService())
    )
    client = TestClient(app)

    response = client.post("/api/sources/source_github/sync", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["error_message"] == "Sync failed. See server logs for details."
    assert "secret-value" not in str(body)
    assert "token=secret-value" not in str(body)


def test_github_target_sync_endpoint_delegates_to_service():
    client, *_, github_sync_service = make_client()

    response = client.post("/api/github/sync", json={"target": "github.com/eunhwa99"})

    assert response.status_code == 200
    assert response.json()["repository_count"] == 2
    assert response.json()["repositories"] == [
        "eunhwa99/algorithms@main",
        "eunhwa99/neetcode@main",
    ]
    assert response.json()["stale_cleanup"] == "disabled"
    assert github_sync_service.calls == ["github.com/eunhwa99"]


def test_github_target_sync_endpoint_returns_503_without_service():
    client = make_unconfigured_client()

    response = client.post("/api/github/sync", json={"target": "github.com/eunhwa99"})

    assert response.status_code == 503
    assert response.json()["detail"] == "github sync service is not configured"


def test_github_target_sync_endpoint_requires_target():
    client, *_ = make_client()

    response = client.post("/api/github/sync", json={"target": "   "})

    assert response.status_code == 400
    assert response.json()["detail"] == "target is required"


def test_github_target_sync_endpoint_returns_structured_failure_without_logging_secret(caplog):
    app = create_console_app(
        ConsoleDependencies(github_sync_service=FailingGitHubSyncService())
    )
    client = TestClient(app)

    with caplog.at_level(logging.ERROR, logger="web_console.app"):
        response = client.post("/api/github/sync", json={"target": "github.com/eunhwa99"})

    assert response.status_code == 200
    assert response.json() == {
        "source_id": "source_github",
        "status": "error",
        "message": "GitHub target sync failed. See server logs for details.",
    }
    assert "secret-value" not in caplog.text
    assert "token=secret-value" not in caplog.text


def test_github_target_sync_endpoint_does_not_echo_secret_target_on_failure(caplog):
    app = create_console_app(
        ConsoleDependencies(github_sync_service=FailingGitHubSyncService())
    )
    client = TestClient(app)

    with caplog.at_level(logging.ERROR, logger="web_console.app"):
        response = client.post(
            "/api/github/sync",
            json={"target": "https://github.com/eunhwa99/repo?token=secret-value"},
        )

    assert response.status_code == 200
    body = response.json()
    assert "target" not in body
    assert "secret-value" not in str(body)
    assert "token=secret-value" not in str(body)
    assert "secret-value" not in caplog.text
    assert "token=secret-value" not in caplog.text


def test_github_target_sync_endpoint_redacts_returned_target_and_job_error():
    app = create_console_app(
        ConsoleDependencies(github_sync_service=SecretPayloadGitHubSyncService())
    )
    client = TestClient(app)

    response = client.post(
        "/api/github/sync",
        json={"target": "https://github.com/eunhwa99/repo?token=secret-value"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["target"] == "redacted"
    assert body["job"]["error_message"] == "Sync failed. See server logs for details."
    assert "secret-value" not in str(body)
    assert "token=secret-value" not in str(body)


def test_answer_endpoint_normalizes_source_filters_and_calls_service():
    client, answer_service, *_ = make_client()

    response = client.post(
        "/api/answer",
        json={
            "question": "What is ContextWiki?",
            "top_k": 3,
            "source_types": ["github"],
            "source_ids": ["manual_source"],
        },
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "ContextWiki evidence"
    assert answer_service.calls == [
        {
            "question": "What is ContextWiki?",
            "top_k": 3,
            "filters": {"source_ids": ["source_github", "manual_source"]},
        }
    ]


def test_answer_endpoint_does_not_forward_source_types_filter():
    client, answer_service, *_ = make_client()

    response = client.post(
        "/api/answer",
        json={
            "question": "What is ContextWiki?",
            "filters": {"source_types": ["github"]},
        },
    )

    assert response.status_code == 200
    assert answer_service.calls == [
        {
            "question": "What is ContextWiki?",
            "top_k": 5,
            "filters": {"source_ids": ["source_github"]},
        }
    ]


def test_answer_endpoint_uses_default_top_k_when_omitted():
    client, answer_service, *_ = make_client()

    response = client.post(
        "/api/answer",
        json={"question": "What is ContextWiki?"},
    )

    assert response.status_code == 200
    assert answer_service.calls == [
        {
            "question": "What is ContextWiki?",
            "top_k": 5,
            "filters": {},
        }
    ]


def test_answer_endpoint_rejects_unmatched_source_type_filters():
    client, answer_service, *_ = make_client()

    response = client.post(
        "/api/answer",
        json={"question": "What is ContextWiki?", "source_types": ["web"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "no configured sources match selected source types"
    assert answer_service.calls == []


def test_answer_endpoint_returns_503_without_service():
    client = make_unconfigured_client()

    response = client.post("/api/answer", json={"question": "What is ContextWiki?"})

    assert response.status_code == 503
    assert response.json()["detail"] == "answer service is not configured"


def test_answer_endpoint_returns_structured_failure_without_logging_secret(caplog):
    app = create_console_app(
        ConsoleDependencies(
            answer_service=FailingAnswerService(),
            metadata_store=FakeMetadataStore(),
        )
    )
    client = TestClient(app)

    with caplog.at_level(logging.ERROR, logger="web_console.app"):
        response = client.post("/api/answer", json={"question": "What is ContextWiki?"})

    assert response.status_code == 200
    assert response.json() == {
        "question": "What is ContextWiki?",
        "answer": "Answer failed. See server logs for details.",
        "evidence_status": "error",
        "citations": [],
        "used_chunks": [],
    }
    assert "secret-value" not in caplog.text
    assert "token=secret-value" not in caplog.text


def test_answer_endpoint_returns_structured_filter_failure_without_logging_secret(caplog):
    app = create_console_app(
        ConsoleDependencies(
            answer_service=FakeAnswerService(),
            metadata_store=FailingMetadataStore(),
        )
    )
    client = TestClient(app)

    with caplog.at_level(logging.ERROR, logger="web_console.app"):
        response = client.post(
            "/api/answer",
            json={"question": "What is ContextWiki?", "source_types": ["github"]},
        )

    assert response.status_code == 200
    assert response.json() == {
        "question": "What is ContextWiki?",
        "answer": "Answer failed. See server logs for details.",
        "evidence_status": "error",
        "citations": [],
        "used_chunks": [],
    }
    assert "secret-value" not in caplog.text
    assert "token=secret-value" not in caplog.text


def test_wiki_endpoint_returns_generation_payload():
    client, _, wiki_service, *_ = make_client()

    response = client.post(
        "/api/wiki/generate",
        json={"topic": "Auto Wiki", "top_k": 4, "source_ids": ["source_github"]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "generated"
    assert wiki_service.calls == [
        {
            "topic": "Auto Wiki",
            "top_k": 4,
            "filters": {"source_ids": ["source_github"]},
        }
    ]


def test_wiki_endpoint_uses_default_top_k_when_omitted():
    client, _, wiki_service, *_ = make_client()

    response = client.post(
        "/api/wiki/generate",
        json={"topic": "Auto Wiki"},
    )

    assert response.status_code == 200
    assert wiki_service.calls == [
        {
            "topic": "Auto Wiki",
            "top_k": 8,
            "filters": {},
        }
    ]


def test_wiki_endpoint_returns_503_without_service():
    client = make_unconfigured_client()

    response = client.post("/api/wiki/generate", json={"topic": "ContextWiki"})

    assert response.status_code == 503
    assert response.json()["detail"] == "wiki service is not configured"


def test_wiki_endpoint_returns_structured_failure_without_logging_secret(caplog):
    app = create_console_app(
        ConsoleDependencies(
            wiki_service=FailingWikiService(),
            metadata_store=FakeMetadataStore(),
        )
    )
    client = TestClient(app)

    with caplog.at_level(logging.ERROR, logger="web_console.app"):
        response = client.post("/api/wiki/generate", json={"topic": "ContextWiki"})

    assert response.status_code == 200
    assert response.json() == {
        "topic": "ContextWiki",
        "status": "error",
        "title": "ContextWiki Wiki",
        "markdown": "Wiki generation failed. See server logs for details.",
        "sections": [],
        "citations": [],
        "backlinks": [],
        "used_chunks": [],
        "message": "Wiki generation failed. See server logs for details.",
    }
    assert "secret-value" not in caplog.text
    assert "token=secret-value" not in caplog.text


def test_wiki_endpoint_returns_structured_filter_failure_without_logging_secret(caplog):
    app = create_console_app(
        ConsoleDependencies(
            wiki_service=FakeWikiService(),
            metadata_store=FailingMetadataStore(),
        )
    )
    client = TestClient(app)

    with caplog.at_level(logging.ERROR, logger="web_console.app"):
        response = client.post(
            "/api/wiki/generate",
            json={"topic": "ContextWiki", "source_types": ["github"]},
        )

    assert response.status_code == 200
    assert response.json() == {
        "topic": "ContextWiki",
        "status": "error",
        "title": "ContextWiki Wiki",
        "markdown": "Wiki generation failed. See server logs for details.",
        "sections": [],
        "citations": [],
        "backlinks": [],
        "used_chunks": [],
        "message": "Wiki generation failed. See server logs for details.",
    }
    assert "secret-value" not in caplog.text
    assert "token=secret-value" not in caplog.text


def test_smoke_endpoints_delegate_to_runner():
    client, _, _, smoke_runner, *_ = make_client()

    fake = client.post("/api/smoke/fake", json={"topic": "ContextWiki"})
    github = client.post(
        "/api/smoke/github",
        json={
            "topic": "README",
            "github_repository": "eunhwa99/MCPContentSearch@main",
            "require_generated": True,
        },
    )

    assert fake.status_code == 200
    assert fake.json()["status"] == "passed"
    assert github.status_code == 200
    assert github.json()["status"] == "skipped"
    assert smoke_runner.calls == [
        {"mode": "fake", "topic": "ContextWiki"},
        {
            "mode": "github",
            "topic": "README",
            "github_repository": "eunhwa99/MCPContentSearch@main",
            "require_generated": True,
        },
    ]


def test_smoke_endpoint_returns_structured_failure(caplog):
    app = create_console_app(
        ConsoleDependencies(smoke_runner=FailingSmokeRunner())
    )
    client = TestClient(app)

    with caplog.at_level(logging.ERROR, logger="web_console.app"):
        response = client.post("/api/smoke/fake", json={"topic": "ContextWiki"})

    assert response.status_code == 200
    assert response.json() == {
        "mode": "fake",
        "status": "failed",
        "error": "Smoke check failed. See server logs for details.",
    }
    assert "secret-value" not in caplog.text
    assert "token=secret-value" not in caplog.text
