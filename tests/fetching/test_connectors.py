import asyncio
from pathlib import Path

import pytest

from core.models import DocumentModel
from environments.config import AppConfig
from fetching import connectors as connector_module
from fetching import obsidian as obsidian_module
from fetching.connectors import (
    GitHubSourceConnector,
    NotionSourceConnector,
    ObsidianSourceConnector,
    TistorySourceConnector,
    build_source_registry,
)
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.unit

OBSIDIAN_ENV_VARS = (
    "CONTEXTWIKI_OBSIDIAN_VAULT_PATH",
    "CONTEXTWIKI_OBSIDIAN_MAX_FILES",
    "CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES",
)


@pytest.fixture(autouse=True)
def clear_obsidian_env(monkeypatch):
    for name in OBSIDIAN_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_notion_connector_persists_external_id(monkeypatch, tmp_path):
    async def fake_fetch_notion_pages(
        api_key,
        config,
        progress_callback=None,
        progress_stop_signal=None,
        progress_stop_checker=None,
        existing_documents=None,
        existing_documents_loader=None,
    ):
        return [
            DocumentModel(
                id="notion_page-1",
                document_id="page-1",
                external_id="page-1",
                title="Page",
                content="body",
                url="https://notion.so/page-1",
                platform="Notion",
            )
        ]

    monkeypatch.setattr(connector_module, "fetch_notion_pages", fake_fetch_notion_pages)
    connector = NotionSourceConnector("secret", AppConfig())

    document = asyncio.run(connector.fetch_documents())[0]
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    persisted = store.upsert_document(document)

    assert document.source_id == "source_notion"
    assert document.document_id == "page-1"
    assert persisted.external_id == "page-1"
    assert persisted.canonical_url == "https://notion.so/page-1"


def test_notion_connector_passes_progress_callback(monkeypatch):
    captured = {}

    async def fake_fetch_notion_pages(
        api_key,
        config,
        progress_callback=None,
        progress_stop_signal=None,
        progress_stop_checker=None,
        existing_documents=None,
        existing_documents_loader=None,
    ):
        captured["api_key"] = api_key
        captured["progress_callback"] = progress_callback
        captured["progress_stop_signal"] = progress_stop_signal
        captured["progress_stop_checker"] = progress_stop_checker
        captured["existing_documents"] = existing_documents
        captured["existing_documents_loader"] = existing_documents_loader
        return []

    async def fake_progress(event):
        return None

    monkeypatch.setattr(connector_module, "fetch_notion_pages", fake_fetch_notion_pages)
    connector = NotionSourceConnector(
        "secret",
        AppConfig(),
        progress_callback=fake_progress,
    )

    assert asyncio.run(connector.fetch_documents()) == []
    assert captured["api_key"] == "secret"
    assert captured["progress_callback"] is fake_progress
    assert captured["progress_stop_signal"] is None
    assert captured["progress_stop_checker"] is None
    assert captured["existing_documents"] is None
    assert callable(captured["existing_documents_loader"])


def test_notion_connector_loader_gets_document_for_requested_ids_only(tmp_path):
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    kept = DocumentModel(
        id="notion_page-kept",
        document_id="page-kept",
        external_id="page-kept",
        source_id="source_notion",
        title="Kept",
        content="kept body",
        url="https://notion.so/page-kept",
        platform="Notion",
        modified_at="2026-06-01T00:00:00Z",
    )
    unrelated = DocumentModel(
        id="notion_page-unrelated",
        document_id="page-unrelated",
        external_id="page-unrelated",
        source_id="source_notion",
        title="Unrelated",
        content="unrelated body",
        url="https://notion.so/page-unrelated",
        platform="Notion",
        modified_at="2026-06-01T00:00:00Z",
    )
    store.upsert_document(kept)
    store.upsert_document(unrelated)
    list_calls = []
    get_calls = []
    original_list = store.list_documents
    original_get = store.get_document

    def tracking_list(*args, **kwargs):
        list_calls.append(1)
        return original_list(*args, **kwargs)

    def tracking_get(document_id):
        get_calls.append(document_id)
        return original_get(document_id)

    store.list_documents = tracking_list  # type: ignore[method-assign]
    store.get_document = tracking_get  # type: ignore[method-assign]

    connector = NotionSourceConnector("secret", AppConfig(), metadata_store=store)
    loaded = connector._load_existing_documents_for_page_ids(["page-kept"])

    assert list_calls == []
    assert get_calls == ["page-kept"]
    assert set(loaded) == {"page-kept"}
    assert loaded["page-kept"].content == "kept body"


def test_notion_connector_loader_exposes_doc_under_page_id_when_external_id_differs():
    """Lookup uses Notion page_id; loader must key by that id even if external_id differs."""
    page_id = "page-kept"
    kept = DocumentModel(
        id="notion_page-kept",
        document_id=page_id,
        external_id="legacy-external-other",
        source_id="source_notion",
        title="Kept",
        content="kept body under mismatched external_id",
        url=f"https://notion.so/{page_id}",
        platform="Notion",
        modified_at="2026-06-01T00:00:00Z",
    )

    class FakeStore:
        def get_document(self, document_id: str):
            if document_id == page_id:
                return kept
            return None

    connector = NotionSourceConnector(
        "secret",
        AppConfig(),
        metadata_store=FakeStore(),  # type: ignore[arg-type]
    )
    loaded = connector._load_existing_documents_for_page_ids([page_id])

    assert page_id in loaded
    assert loaded[page_id].content == "kept body under mismatched external_id"
    assert loaded[page_id].document_id == page_id
    assert loaded[page_id].external_id == "legacy-external-other"


def test_tistory_connector_persists_external_id(monkeypatch, tmp_path):
    async def fake_fetch_tistory_posts(
        blog_name,
        max_id,
        connection_limit,
        request_timeout,
        log_interval,
    ):
        return [
            DocumentModel(
                id="tistory_7",
                document_id="devlog:7",
                external_id="devlog:7",
                title="Post",
                content="body",
                url="https://devlog.tistory.com/7",
                platform="Tistory",
            )
        ]

    monkeypatch.setattr(connector_module, "fetch_tistory_posts", fake_fetch_tistory_posts)
    connector = TistorySourceConnector("devlog", AppConfig(tistory_max_post_id=7))

    document = asyncio.run(connector.fetch_documents())[0]
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    persisted = store.upsert_document(document)

    assert document.source_id == "source_tistory"
    assert document.document_id == "devlog:7"
    assert persisted.external_id == "devlog:7"
    assert persisted.canonical_url == "https://devlog.tistory.com/7"


def test_build_source_registry_includes_core_sources():
    config = AppConfig(
        github_repositories=("eunhwa99/MCPContentSearch@main",),
    )

    registry = build_source_registry(
        config=config,
        notion_api_key="notion-secret",
        tistory_blog_name="devlog",
        github_token="github-secret",
        github_http_client=object(),
    )
    sources = {source.source_id: source for source in registry.list_sources()}

    assert set(sources) == {
        "source_github",
        "source_notion",
        "source_obsidian",
        "source_tistory",
    }
    assert isinstance(registry.get_connector("source_github"), GitHubSourceConnector)
    assert sources["source_github"].enabled is True
    assert sources["source_github"].auth_ref == "env:GITHUB_TOKEN"
    assert isinstance(registry.get_connector("source_obsidian"), ObsidianSourceConnector)
    assert sources["source_obsidian"].enabled is False
    assert sources["source_obsidian"].auth_ref == "env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH"
    assert sources["source_obsidian"].last_error == (
        "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
        "is not set or is not an existing directory."
    )


def test_build_source_registry_disables_tistory_until_blog_is_configured():
    registry = build_source_registry(
        config=AppConfig(
            github_repositories=("eunhwa99/MCPContentSearch@main",),
        ),
        notion_api_key="notion-secret",
        tistory_blog_name="",
        github_token="github-secret",
        github_http_client=object(),
    )
    sources = {source.source_id: source for source in registry.list_sources()}

    assert sources["source_tistory"].enabled is False
    assert sources["source_tistory"].auth_ref == "env:TISTORY_BLOG_NAME"


def test_build_source_registry_accepts_github_owner_target_for_runtime_discovery():
    registry = build_source_registry(
        config=AppConfig(github_repositories=("eunaverse",)),
        notion_api_key="",
        tistory_blog_name="",
        github_http_client=object(),
    )

    connector = registry.get_connector("source_github")

    assert isinstance(connector, GitHubSourceConnector)
    assert connector.source.enabled is True
    assert connector.repositories == ("eunaverse",)
    assert connector.cleanup_document_id_prefixes == ()


def test_github_connector_uses_validated_custom_token_env_ref():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_token_env_var="CONTEXTWIKI_GITHUB_TOKEN"),
    )

    assert connector.source.auth_ref == "env:CONTEXTWIKI_GITHUB_TOKEN"


def test_github_connector_exposes_public_disabled_reason_for_missing_repositories():
    connector = GitHubSourceConnector(
        repositories=(),
        config=AppConfig(github_token_env_var="GITHUB_TOKEN"),
        token="ghp_secretcredential",
        http_client=object(),
    )

    assert connector.source.enabled is False
    assert connector.disabled_reason == (
        "Source source_github is disabled because no GitHub repositories are "
        "configured in CONTEXTWIKI_GITHUB_REPOSITORIES."
    )
    assert "ghp_secretcredential" not in connector.disabled_reason


def test_obsidian_connector_is_enabled_for_temp_vault(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    connector = ObsidianSourceConnector(AppConfig(obsidian_vault_path=vault))

    assert connector.source.source_id == "source_obsidian"
    assert connector.source.enabled is True
    assert connector.source.auth_ref == "env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH"
    assert connector.disabled_reason == ""
    assert connector.supports_stale_cleanup is True


def test_obsidian_connector_normalizes_filesystem_modified_time(tmp_path):
    note = tmp_path / "dated.md"
    note.write_text("# Dated\n\nfilesystem timestamp", encoding="utf-8")
    expected_modified_at = obsidian_module.datetime.fromtimestamp(
        note.stat().st_mtime,
        tz=obsidian_module.timezone.utc,
    ).isoformat()

    document = asyncio.run(
        ObsidianSourceConnector(
            AppConfig(obsidian_vault_path=tmp_path)
        ).fetch_documents()
    )[0]

    assert document.modified_at == expected_modified_at
    assert document.date_provenance == "filesystem"
    assert document.published_at == ""


def test_obsidian_connector_is_disabled_for_relative_vault_path():
    connector = ObsidianSourceConnector(
        AppConfig(obsidian_vault_path=Path("relative-vault"))
    )

    assert connector.source.enabled is False
    assert connector.disabled_reason == (
        "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
        "must be an absolute path."
    )
    assert connector.source.last_error == connector.disabled_reason


def test_obsidian_connector_is_disabled_for_symlinked_vault_path(tmp_path):
    real_vault = tmp_path / "real-vault"
    real_vault.mkdir()
    symlink_vault = tmp_path / "vault-link"
    symlink_vault.symlink_to(real_vault, target_is_directory=True)

    connector = ObsidianSourceConnector(AppConfig(obsidian_vault_path=symlink_vault))

    assert connector.source.enabled is False
    assert connector.disabled_reason == (
        "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
        "must not be a symlink."
    )
    assert connector.source.last_error == connector.disabled_reason


def test_obsidian_connector_is_disabled_for_symlinked_vault_ancestor(tmp_path):
    real_parent = tmp_path / "real-parent"
    real_vault = real_parent / "vault"
    real_vault.mkdir(parents=True)
    symlink_parent = tmp_path / "parent-link"
    symlink_parent.symlink_to(real_parent, target_is_directory=True)

    connector = ObsidianSourceConnector(
        AppConfig(obsidian_vault_path=symlink_parent / "vault")
    )

    assert connector.source.enabled is False
    assert connector.disabled_reason == (
        "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
        "must not be a symlink."
    )
    assert connector.source.last_error == connector.disabled_reason


def test_obsidian_connector_is_disabled_for_unreadable_vault_root(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    original_access = obsidian_module.os.access

    def fake_access(path, mode):
        if Path(path) == vault:
            return False
        return original_access(path, mode)

    monkeypatch.setattr(obsidian_module.os, "access", fake_access)
    connector = ObsidianSourceConnector(AppConfig(obsidian_vault_path=vault))

    assert connector.source.enabled is False
    assert connector.disabled_reason == (
        "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
        "is not set or is not an existing directory."
    )
    assert connector.source.last_error == connector.disabled_reason


def test_obsidian_connector_disables_stale_cleanup_for_partial_snapshot(monkeypatch, tmp_path):
    class PartialSnapshot:
        def __init__(self, documents, snapshot_complete):
            self.documents = documents
            self.snapshot_complete = snapshot_complete

    async def fake_fetch_obsidian_documents(
        vault_path,
        *,
        max_files,
        max_file_bytes,
    ):
        return PartialSnapshot(
            [
                DocumentModel(
                    id="notes/partial.md",
                    document_id="notes/partial.md",
                    external_id="notes/partial.md",
                    title="Partial",
                    content="Body",
                    url="obsidian://open?vault=test&file=notes%2Fpartial.md",
                    canonical_url="obsidian://open?vault=test&file=notes%2Fpartial.md",
                    path="notes/partial.md",
                    platform="obsidian",
                    source_id="source_obsidian",
                )
            ],
            snapshot_complete=False,
        )

    monkeypatch.setattr(
        connector_module,
        "fetch_obsidian_documents",
        fake_fetch_obsidian_documents,
    )
    connector = ObsidianSourceConnector(AppConfig(obsidian_vault_path=tmp_path))

    with pytest.raises(RuntimeError, match="snapshot was incomplete"):
        asyncio.run(connector.fetch_documents())

    assert connector.supports_stale_cleanup is False


def test_obsidian_connector_passes_configured_snapshot_bounds(monkeypatch, tmp_path):
    captured = {}

    async def fake_fetch_obsidian_documents(
        vault_path,
        *,
        max_files,
        max_file_bytes,
    ):
        captured["vault_path"] = vault_path
        captured["max_files"] = max_files
        captured["max_file_bytes"] = max_file_bytes
        return obsidian_module.ObsidianSnapshot(documents=[], snapshot_complete=True)

    monkeypatch.setattr(
        connector_module,
        "fetch_obsidian_documents",
        fake_fetch_obsidian_documents,
    )
    connector = ObsidianSourceConnector(
        AppConfig(
            obsidian_vault_path=tmp_path,
            obsidian_max_files=7,
            obsidian_max_file_bytes=1024,
        )
    )

    assert asyncio.run(connector.fetch_documents()) == []
    assert captured == {
        "vault_path": tmp_path,
        "max_files": 7,
        "max_file_bytes": 1024,
    }


def test_obsidian_connector_fails_incomplete_snapshot_when_file_count_exceeds_limit(
    tmp_path,
):
    (tmp_path / "one.md").write_text("one", encoding="utf-8")
    (tmp_path / "two.md").write_text("two", encoding="utf-8")
    connector = ObsidianSourceConnector(
        AppConfig(obsidian_vault_path=tmp_path, obsidian_max_files=1)
    )

    with pytest.raises(RuntimeError, match="snapshot was incomplete"):
        asyncio.run(connector.fetch_documents())

    assert connector.supports_stale_cleanup is False


def test_obsidian_connector_fails_incomplete_snapshot_when_file_bytes_exceed_limit(
    tmp_path,
):
    (tmp_path / "large.md").write_text("larger than limit", encoding="utf-8")
    connector = ObsidianSourceConnector(
        AppConfig(obsidian_vault_path=tmp_path, obsidian_max_file_bytes=4)
    )

    with pytest.raises(RuntimeError, match="snapshot was incomplete"):
        asyncio.run(connector.fetch_documents())

    assert connector.supports_stale_cleanup is False


def test_obsidian_connector_fails_incomplete_snapshot_for_visible_symlinked_note(
    tmp_path,
):
    outside_note = tmp_path.parent / "outside.md"
    outside_note.write_text("must not be followed", encoding="utf-8")
    (tmp_path / "linked.md").symlink_to(outside_note)
    connector = ObsidianSourceConnector(AppConfig(obsidian_vault_path=tmp_path))

    with pytest.raises(RuntimeError, match="snapshot was incomplete"):
        asyncio.run(connector.fetch_documents())

    assert connector.supports_stale_cleanup is False


def test_obsidian_connector_fails_incomplete_snapshot_for_visible_symlinked_directory(
    tmp_path,
):
    outside_dir = tmp_path.parent / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "outside.md").write_text("must not be followed", encoding="utf-8")
    (tmp_path / "linked-dir").symlink_to(outside_dir, target_is_directory=True)
    connector = ObsidianSourceConnector(AppConfig(obsidian_vault_path=tmp_path))

    with pytest.raises(RuntimeError, match="snapshot was incomplete"):
        asyncio.run(connector.fetch_documents())

    assert connector.supports_stale_cleanup is False


def test_obsidian_open_note_rejects_symlinked_root_fd(tmp_path):
    outside_vault = tmp_path / "outside-vault"
    outside_vault.mkdir()
    (outside_vault / "note.md").write_text("must not be read", encoding="utf-8")
    vault_link = tmp_path / "vault-link"
    vault_link.symlink_to(outside_vault, target_is_directory=True)

    with pytest.raises(OSError):
        obsidian_module._open_note_without_following_symlinks(
            vault_link,
            Path("note.md"),
        )


def test_obsidian_open_note_rejects_symlinked_vault_ancestor(tmp_path):
    real_parent = tmp_path / "real-parent"
    real_vault = real_parent / "vault"
    real_vault.mkdir(parents=True)
    (real_vault / "note.md").write_text("must not be read", encoding="utf-8")
    symlink_parent = tmp_path / "parent-link"
    symlink_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(OSError):
        obsidian_module._open_note_without_following_symlinks(
            symlink_parent / "vault",
            Path("note.md"),
        )


def test_obsidian_open_note_rechecks_byte_limit_after_open(tmp_path):
    (tmp_path / "large.md").write_text("larger than limit", encoding="utf-8")

    with pytest.raises(OSError):
        obsidian_module._open_note_without_following_symlinks(
            tmp_path,
            Path("large.md"),
            max_file_bytes=4,
        )


def test_obsidian_open_note_rejects_file_growth_after_fd_stat(
    monkeypatch,
    tmp_path,
):
    note_path = tmp_path / "growing.md"
    note_path.write_text("tiny", encoding="utf-8")
    original_fdopen = obsidian_module.os.fdopen
    grew_file = False

    def grow_before_read(fd, *args, **kwargs):
        nonlocal grew_file
        if not grew_file:
            grew_file = True
            note_path.write_text("tiny!", encoding="utf-8")
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(obsidian_module.os, "fdopen", grow_before_read)

    with pytest.raises(OSError, match="byte limit"):
        obsidian_module._open_note_without_following_symlinks(
            tmp_path,
            Path("growing.md"),
            max_file_bytes=4,
        )


def test_obsidian_file_limit_failure_does_not_tombstone_active_notes(tmp_path):
    (tmp_path / "keep.md").write_text("This note stays active.", encoding="utf-8")
    config = AppConfig(obsidian_vault_path=tmp_path, obsidian_max_files=10)
    first_connector = ObsidianSourceConnector(config)
    first_documents = asyncio.run(first_connector.fetch_documents())
    store = MetadataStore(tmp_path / "metadata.sqlite3")
    for document in first_documents:
        store.upsert_document(document)

    (tmp_path / "extra.md").write_text("This exceeds the limit.", encoding="utf-8")
    second_connector = ObsidianSourceConnector(
        AppConfig(obsidian_vault_path=tmp_path, obsidian_max_files=1)
    )

    with pytest.raises(RuntimeError, match="snapshot was incomplete"):
        asyncio.run(second_connector.fetch_documents())

    assert second_connector.supports_stale_cleanup is False
    assert store.get_document("keep.md").deleted_at == ""
