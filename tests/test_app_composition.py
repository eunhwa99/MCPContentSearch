import asyncio
import json
import stat
from pathlib import Path

import pytest

import main
from app_runtime import build_ingestion_runtime
from environments.config import AppConfig


pytestmark = pytest.mark.unit


class FakeVectorStore:
    def __init__(self, chroma_collection):
        self.chroma_collection = chroma_collection


class FakeStorageContext:
    @staticmethod
    def from_defaults(vector_store):
        return {"vector_store": vector_store}


class FakeContentIndexer:
    def __init__(self, config, chroma_collection, storage_context):
        self.config = config
        self.chroma_collection = chroma_collection
        self.storage_context = storage_context


class FakeMetadataStore:
    instances = []

    def __init__(self, db_path, require_private=False):
        self.db_path = Path(db_path)
        self.require_private = require_private
        self.sources = {}
        self.recovered_source_ids = None
        self.__class__.instances.append(self)

    def ensure_schema(self):
        return None

    def register_source(self, source):
        self.sources[source.source_id] = source
        return source

    def recover_orphaned_running_jobs(self, *, started_before, error_message, source_ids):
        self.recovered_source_ids = tuple(source_ids)
        return 0

    def list_sources(self):
        return list(self.sources.values())

    def get_source(self, source_id):
        return self.sources.get(source_id)

    def get_latest_sync_job(self, source_id):
        return None

    def get_source_status_snapshot(self, source_id):
        return {
            "latest_success_at": "",
            "latest_failure_at": "",
            "latest_failure_reason": "",
            "document_count": 0,
            "chunk_count": 0,
        }

    def get_chunk(self, chunk_id):
        return None

    def get_document(self, document_id):
        return None

    def list_chunks_for_document(self, document_id):
        return []


class FakeContextSearchService:
    def __init__(self, metadata_store, indexer, config, default_source_ids):
        self.metadata_store = metadata_store
        self.indexer = indexer
        self.config = config
        self.default_source_ids = tuple(default_source_ids)

    async def search_context(self, query, filters=None, top_k=10):
        return {"query": query, "results": []}

    async def search_documents(self, query, filters=None, top_k=10):
        return {"query": query, "results": []}


class FakeCitationAnswerService:
    def __init__(self, context_search):
        self.context_search = context_search

    async def answer_with_citations(self, question, filters=None, top_k=5):
        return {
            "question": question,
            "answer": "Insufficient evidence.",
            "evidence_status": "insufficient",
            "citations": [],
            "used_chunks": [],
        }


class FakeEvidenceSearchService:
    instances = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.__class__.instances.append(self)

    async def search_evidence(self, request):
        del request
        return []


def test_create_app_registers_slim_mcp_tools_and_core_sources(monkeypatch, tmp_path):
    monkeypatch.delenv("CONTEXTWIKI_OBSIDIAN_VAULT_PATH", raising=False)
    monkeypatch.delenv("CONTEXTWIKI_OBSIDIAN_MAX_FILES", raising=False)
    monkeypatch.delenv("CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES", raising=False)
    config = AppConfig(
        chroma_db_path=tmp_path / "chroma",
        metadata_db_path=tmp_path / "contextwiki.sqlite3",
        github_repositories=(),
    )
    chroma_collections = []

    def fake_setup_chroma(app_config):
        assert app_config == config
        chroma_collections.append(object())
        return chroma_collections[-1]

    FakeMetadataStore.instances = []
    monkeypatch.setattr(main, "AppConfig", lambda: config)
    monkeypatch.setattr(main, "setup_chroma", fake_setup_chroma)
    monkeypatch.setattr(main, "ChromaVectorStore", FakeVectorStore)
    monkeypatch.setattr(main, "StorageContext", FakeStorageContext)
    monkeypatch.setattr(main, "ContentIndexer", FakeContentIndexer)
    monkeypatch.setattr(main, "MetadataStore", FakeMetadataStore)
    monkeypatch.setattr(main, "ContextSearchService", FakeContextSearchService)
    monkeypatch.setattr(main, "CitationAnswerService", FakeCitationAnswerService)
    FakeEvidenceSearchService.instances = []
    monkeypatch.setattr(
        main,
        "EvidenceSearchService",
        FakeEvidenceSearchService,
        raising=False,
    )
    monkeypatch.setattr(main, "NOTION_API_KEY", "")
    monkeypatch.setattr(main, "TISTORY_BLOG_NAME", "")
    monkeypatch.setattr(main, "get_env_secret", lambda name: "")

    app = main.create_app()

    expected_tools = {
        "list_sources",
        "sync_source",
        "sync_all",
        "get_sync_status",
        "search_context",
        "search_documents",
        "list_documents",
        "fetch_context",
        "search_evidence",
    }
    registered_tools = {tool.name for tool in asyncio.run(app.list_tools())}
    assert registered_tools == expected_tools

    list_sources_blocks = asyncio.run(app.call_tool("list_sources", {}))
    list_sources_payload = json.loads(list_sources_blocks[0].text)
    sources = {
        source["source_id"]: source
        for source in list_sources_payload["sources"]
    }
    assert set(sources) == {
        "source_github",
        "source_notion",
        "source_obsidian",
        "source_tistory",
    }
    assert all(source["enabled"] is False for source in sources.values())
    assert sources["source_obsidian"]["auth_ref"] == "env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH"
    assert sources["source_obsidian"]["last_error"] == (
        "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
        "is not set or is not an existing directory."
    )

    metadata_store = FakeMetadataStore.instances[0]
    assert metadata_store.db_path == tmp_path / "contextwiki.sqlite3"
    assert metadata_store.recovered_source_ids == (
        "source_notion",
        "source_tistory",
        "source_github",
        "source_obsidian",
    )
    assert chroma_collections


def test_create_app_requires_private_stores_when_career_source_is_enabled(
    monkeypatch,
    tmp_path,
):
    career_root = tmp_path / "career"
    career_root.mkdir()
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(
        json.dumps({"root": "career", "documents": []}),
        encoding="utf-8",
    )
    config = AppConfig(
        chroma_db_path=tmp_path / "private" / "chroma",
        metadata_db_path=tmp_path / "private" / "contextwiki.sqlite3",
        career_manifest_path=manifest,
        github_repositories=(),
    )
    setup_flags = []

    def fake_setup_chroma(app_config, *, require_private=False):
        assert app_config == config
        setup_flags.append(require_private)
        return object()

    FakeMetadataStore.instances = []
    monkeypatch.setattr(main, "AppConfig", lambda: config)
    monkeypatch.setattr(main, "setup_chroma", fake_setup_chroma)
    monkeypatch.setattr(main, "ChromaVectorStore", FakeVectorStore)
    monkeypatch.setattr(main, "StorageContext", FakeStorageContext)
    monkeypatch.setattr(main, "ContentIndexer", FakeContentIndexer)
    monkeypatch.setattr(main, "MetadataStore", FakeMetadataStore)
    monkeypatch.setattr(main, "ContextSearchService", FakeContextSearchService)
    monkeypatch.setattr(main, "CitationAnswerService", FakeCitationAnswerService)
    monkeypatch.setattr(main, "EvidenceSearchService", FakeEvidenceSearchService)
    monkeypatch.setattr(main, "NOTION_API_KEY", "")
    monkeypatch.setattr(main, "TISTORY_BLOG_NAME", "")
    monkeypatch.setattr(main, "get_env_secret", lambda _name: "")

    app = main.create_app()
    sources = json.loads(asyncio.run(app.call_tool("list_sources", {}))[0].text)[
        "sources"
    ]

    assert setup_flags == [True]
    assert FakeMetadataStore.instances[0].require_private is True
    assert any(source["source_id"] == "source_career" for source in sources)


def test_create_app_preflights_both_private_stores_before_opening_chroma(
    monkeypatch,
    tmp_path,
):
    career_root = tmp_path / "career"
    career_root.mkdir()
    manifest = tmp_path / "career-manifest.json"
    manifest.write_text(
        json.dumps({"root": "career", "documents": []}),
        encoding="utf-8",
    )
    private_parent = tmp_path / "private"
    private_parent.mkdir(mode=0o700)
    sqlite_path = private_parent / "contextwiki.sqlite3"
    sqlite_path.write_bytes(b"")
    sqlite_path.chmod(0o644)
    config = AppConfig(
        chroma_db_path=private_parent / "chroma",
        metadata_db_path=sqlite_path,
        career_manifest_path=manifest,
        github_repositories=(),
    )
    setup_calls = []

    def fake_setup_chroma(_config, *, require_private=False):
        setup_calls.append(require_private)
        return object()

    monkeypatch.setattr(main, "AppConfig", lambda: config)
    monkeypatch.setattr(main, "setup_chroma", fake_setup_chroma)
    monkeypatch.setattr(main, "ChromaVectorStore", FakeVectorStore)
    monkeypatch.setattr(main, "StorageContext", FakeStorageContext)
    monkeypatch.setattr(main, "ContentIndexer", FakeContentIndexer)
    monkeypatch.setattr(main, "MetadataStore", FakeMetadataStore)
    monkeypatch.setattr(main, "NOTION_API_KEY", "")
    monkeypatch.setattr(main, "TISTORY_BLOG_NAME", "")
    monkeypatch.setattr(main, "get_env_secret", lambda _name: "")

    with pytest.raises(RuntimeError, match="chmod 600") as exc_info:
        main.create_app()

    assert setup_calls == []
    assert str(tmp_path) not in str(exc_info.value)
    assert stat.S_IMODE(sqlite_path.stat().st_mode) == 0o644


def test_missing_configured_career_manifest_is_preflighted_before_later_enable(
    tmp_path,
):
    manifest = tmp_path / "career-manifest.json"
    private_parent = tmp_path / "private"
    config = AppConfig(
        chroma_db_path=private_parent / "chroma",
        metadata_db_path=private_parent / "contextwiki.sqlite3",
        career_manifest_path=manifest,
        github_repositories=(),
    )
    setup_flags = []

    def fake_setup_chroma(_config, *, require_private=False):
        setup_flags.append(require_private)
        return object()

    FakeMetadataStore.instances = []
    runtime = build_ingestion_runtime(
        config=config,
        notion_api_key="",
        tistory_blog_name="",
        setup_chroma_fn=fake_setup_chroma,
        vector_store_cls=FakeVectorStore,
        storage_context_cls=FakeStorageContext,
        indexer_cls=FakeContentIndexer,
        metadata_store_cls=FakeMetadataStore,
    )
    connector = runtime.source_registry.get_connector("source_career")
    assert connector.source.enabled is False

    career_root = tmp_path / "career"
    career_root.mkdir()
    manifest.write_text(
        json.dumps({"root": "career", "documents": []}),
        encoding="utf-8",
    )
    connector.refresh_source_state()

    assert connector.source.enabled is True
    assert setup_flags == [True]
    assert runtime.metadata_store.require_private is True
    assert stat.S_IMODE(private_parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(config.chroma_db_path.stat().st_mode) == 0o700
    assert stat.S_IMODE(config.metadata_db_path.stat().st_mode) == 0o600
