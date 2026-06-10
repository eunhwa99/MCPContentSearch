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
    WebsiteSourceConnector,
    build_source_registry,
)
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.unit


def test_notion_connector_persists_external_id(monkeypatch, tmp_path):
    async def fake_fetch_notion_pages(api_key, config):
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


def test_build_source_registry_includes_phase_b_sources():
    config = AppConfig(
        github_repositories=("eunhwa99/MCPContentSearch@main",),
        web_seed_urls=("https://docs.example.com",),
    )

    registry = build_source_registry(
        config=config,
        notion_api_key="notion-secret",
        tistory_blog_name="devlog",
        github_token="github-secret",
        github_http_client=object(),
        web_http_client=object(),
    )
    sources = {source.source_id: source for source in registry.list_sources()}

    assert set(sources) == {
        "source_github",
        "source_notion",
        "source_tistory",
        "source_web",
        "source_obsidian",
    }
    assert isinstance(registry.get_connector("source_github"), GitHubSourceConnector)
    assert isinstance(registry.get_connector("source_web"), WebsiteSourceConnector)
    assert sources["source_github"].enabled is True
    assert sources["source_github"].auth_ref == "env:GITHUB_TOKEN"
    assert sources["source_web"].enabled is True
    assert sources["source_web"].auth_ref == "env:CONTEXTWIKI_WEB_URLS"
    assert sources["source_obsidian"].auth_ref == "env:CONTEXTWIKI_OBSIDIAN_VAULT_PATH"


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


def test_obsidian_config_expands_user_home_from_env(monkeypatch):
    monkeypatch.setenv("CONTEXTWIKI_OBSIDIAN_VAULT_PATH", "~/vaults/contextwiki")

    config = AppConfig()

    assert config.obsidian_vault_path is not None
    assert config.obsidian_vault_path.is_absolute()
    assert "~" not in str(config.obsidian_vault_path)


def test_obsidian_config_expands_user_home_from_constructor_string():
    config = AppConfig(obsidian_vault_path="~/vaults/contextwiki")

    assert config.obsidian_vault_path is not None
    assert config.obsidian_vault_path.is_absolute()
    assert "~" not in str(config.obsidian_vault_path)


def test_obsidian_config_expands_user_home_from_constructor_path():
    config = AppConfig(obsidian_vault_path=Path("~/vaults/contextwiki"))

    assert config.obsidian_vault_path is not None
    assert config.obsidian_vault_path.is_absolute()
    assert "~" not in str(config.obsidian_vault_path)


def test_obsidian_config_invalid_tilde_user_does_not_raise():
    config = AppConfig(obsidian_vault_path="~nonexistentuser/vault")

    assert config.obsidian_vault_path == Path("~nonexistentuser/vault")


def test_obsidian_config_invalid_tilde_user_env_does_not_raise(monkeypatch):
    monkeypatch.setenv("CONTEXTWIKI_OBSIDIAN_VAULT_PATH", "~nonexistentuser/vault")

    config = AppConfig()

    assert config.obsidian_vault_path == Path("~nonexistentuser/vault")


def test_obsidian_connector_is_disabled_for_relative_vault_path(tmp_path):
    relative_path = Path("relative-vault")
    config = AppConfig(obsidian_vault_path=relative_path)
    connector = ObsidianSourceConnector(config)

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
    config = AppConfig(obsidian_vault_path=symlink_vault)
    connector = ObsidianSourceConnector(config)

    assert connector.source.enabled is False
    assert connector.disabled_reason == (
        "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
        "must not be a symlink."
    )
    assert connector.source.last_error == connector.disabled_reason


def test_obsidian_connector_is_disabled_for_unreadable_vault_root(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    config = AppConfig(obsidian_vault_path=vault)
    original_access = obsidian_module.os.access

    def fake_access(path, mode):
        if Path(path) == vault:
            return False
        return original_access(path, mode)

    monkeypatch.setattr(obsidian_module.os, "access", fake_access)
    connector = ObsidianSourceConnector(config)

    assert connector.source.enabled is False
    assert connector.disabled_reason == (
        "Source source_obsidian is disabled because CONTEXTWIKI_OBSIDIAN_VAULT_PATH "
        "is not set or is not an existing directory."
    )


def test_obsidian_connector_disables_stale_cleanup_for_partial_snapshot(monkeypatch, tmp_path):
    class PartialSnapshot:
        def __init__(self, documents, snapshot_complete):
            self.documents = documents
            self.snapshot_complete = snapshot_complete

    async def fake_fetch_obsidian_documents(vault_path):
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
