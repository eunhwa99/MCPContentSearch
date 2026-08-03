from __future__ import annotations

import os
import stat
from types import SimpleNamespace

import pytest

from environments.config import AppConfig, setup_chroma
from storage import metadata_store as metadata_store_module
from storage.metadata_store import MetadataStore


pytestmark = pytest.mark.unit


class FakePersistentClient:
    def __init__(self, path):
        self.path = metadata_store_module.Path(path)
        (self.path / "chroma.sqlite3").write_text("synthetic", encoding="utf-8")

    @staticmethod
    def get_or_create_collection(_name):
        return object()


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_private_career_storage_creates_owner_only_paths(monkeypatch, tmp_path):
    private_parent = tmp_path / "private-store"
    chroma_path = private_parent / "chroma"
    sqlite_path = private_parent / "contextwiki.sqlite3"
    config = AppConfig(
        chroma_db_path=chroma_path,
        metadata_db_path=sqlite_path,
    )
    monkeypatch.setattr(
        "environments.config.chromadb.PersistentClient",
        FakePersistentClient,
    )

    setup_chroma(config, require_private=True)
    MetadataStore(sqlite_path, require_private=True)

    assert _mode(private_parent) == 0o700
    assert _mode(chroma_path) == 0o700
    assert _mode(chroma_path / "chroma.sqlite3") == 0o600
    assert _mode(sqlite_path) == 0o600


@pytest.mark.parametrize("target", ["parent", "chroma"])
def test_private_career_storage_rejects_existing_public_directories(
    monkeypatch,
    tmp_path,
    target,
):
    private_parent = tmp_path / "private-store"
    private_parent.mkdir(mode=0o700)
    chroma_path = private_parent / "chroma"
    if target == "parent":
        private_parent.chmod(0o755)
    else:
        chroma_path.mkdir(mode=0o755)
    config = AppConfig(
        chroma_db_path=chroma_path,
        metadata_db_path=private_parent / "contextwiki.sqlite3",
    )
    monkeypatch.setattr(
        "environments.config.chromadb.PersistentClient",
        FakePersistentClient,
    )

    with pytest.raises(RuntimeError, match="chmod 700") as exc_info:
        setup_chroma(config, require_private=True)

    assert str(tmp_path) not in str(exc_info.value)
    assert _mode(private_parent if target == "parent" else chroma_path) == 0o755


def test_private_career_storage_rejects_existing_public_sqlite_without_chmod(
    tmp_path,
):
    private_parent = tmp_path / "private-store"
    private_parent.mkdir(mode=0o700)
    sqlite_path = private_parent / "contextwiki.sqlite3"
    sqlite_path.write_bytes(b"")
    sqlite_path.chmod(0o644)

    with pytest.raises(RuntimeError, match="chmod 600") as exc_info:
        MetadataStore(sqlite_path, require_private=True)

    assert str(tmp_path) not in str(exc_info.value)
    assert sqlite_path.name in str(exc_info.value)
    assert _mode(sqlite_path) == 0o644


@pytest.mark.parametrize("target", ["chroma", "sqlite"])
def test_private_career_storage_rejects_symlinks(monkeypatch, tmp_path, target):
    private_parent = tmp_path / "private-store"
    private_parent.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    chroma_path = private_parent / "chroma"
    sqlite_path = private_parent / "contextwiki.sqlite3"
    if target == "chroma":
        chroma_path.symlink_to(outside, target_is_directory=True)
    else:
        outside_file = outside / "outside.sqlite3"
        outside_file.write_bytes(b"")
        sqlite_path.symlink_to(outside_file)

    with pytest.raises(RuntimeError, match="symlink") as exc_info:
        if target == "chroma":
            config = AppConfig(
                chroma_db_path=chroma_path,
                metadata_db_path=sqlite_path,
            )
            monkeypatch.setattr(
                "environments.config.chromadb.PersistentClient",
                FakePersistentClient,
            )
            setup_chroma(config, require_private=True)
        else:
            MetadataStore(sqlite_path, require_private=True)

    assert str(tmp_path) not in str(exc_info.value)


def test_private_career_storage_rejects_foreign_ownership_via_policy_mock(
    monkeypatch,
    tmp_path,
):
    private_parent = tmp_path / "private-store"
    private_parent.mkdir(mode=0o700)
    sqlite_path = private_parent / "contextwiki.sqlite3"
    monkeypatch.setattr(
        metadata_store_module,
        "_current_uid",
        lambda: os.getuid() + 1,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="owned by current user") as exc_info:
        MetadataStore(sqlite_path, require_private=True)

    assert str(tmp_path) not in str(exc_info.value)


def test_career_disabled_preserves_existing_permissive_chroma_behavior(
    monkeypatch,
    tmp_path,
):
    public_parent = tmp_path / "public-store"
    public_parent.mkdir(mode=0o755)
    config = AppConfig(
        chroma_db_path=public_parent / "chroma",
        metadata_db_path=public_parent / "contextwiki.sqlite3",
        career_manifest_path=None,
    )
    monkeypatch.setattr(
        "environments.config.chromadb.PersistentClient",
        FakePersistentClient,
    )

    setup_chroma(config)
    store = MetadataStore(config.metadata_db_path)
    store.ensure_schema()

    assert config.chroma_db_path.is_dir()
    assert config.metadata_db_path.is_file()


def test_missing_configured_career_source_defaults_to_private_chroma(
    monkeypatch,
    tmp_path,
):
    public_parent = tmp_path / "public-store"
    public_parent.mkdir(mode=0o755)
    config = AppConfig(
        chroma_db_path=public_parent / "chroma",
        metadata_db_path=public_parent / "contextwiki.sqlite3",
        career_manifest_path=tmp_path / "missing-manifest.json",
    )
    monkeypatch.setattr(
        "environments.config.chromadb.PersistentClient",
        FakePersistentClient,
    )

    with pytest.raises(RuntimeError, match="chmod 700") as exc_info:
        setup_chroma(config)

    assert str(tmp_path) not in str(exc_info.value)
    assert _mode(public_parent) == 0o755


@pytest.mark.parametrize("target", ["chroma", "sqlite"])
def test_private_career_storage_rejects_symlinked_ancestor_without_creating_inside(
    monkeypatch,
    tmp_path,
    target,
):
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir(mode=0o700)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    private_parent = linked_parent / "private-store"
    chroma_path = private_parent / "chroma"
    sqlite_path = private_parent / "contextwiki.sqlite3"

    with pytest.raises(RuntimeError, match="symlink") as exc_info:
        if target == "chroma":
            config = AppConfig(
                chroma_db_path=chroma_path,
                metadata_db_path=sqlite_path,
            )
            monkeypatch.setattr(
                "environments.config.chromadb.PersistentClient",
                FakePersistentClient,
            )
            setup_chroma(config, require_private=True)
        else:
            MetadataStore(sqlite_path, require_private=True)

    assert str(tmp_path) not in str(exc_info.value)
    assert not (real_parent / "private-store").exists()


def test_private_chroma_rejects_group_writable_intermediate_before_client_open(
    monkeypatch,
    tmp_path,
):
    writable_ancestor = tmp_path / "writable-ancestor"
    writable_ancestor.mkdir(mode=0o770)
    writable_ancestor.chmod(0o770)
    private_parent = writable_ancestor / "private-store"
    client_calls = []

    class RecordingPersistentClient(FakePersistentClient):
        def __init__(self, path):
            client_calls.append(path)
            super().__init__(path)

    config = AppConfig(
        chroma_db_path=private_parent / "chroma",
        metadata_db_path=private_parent / "contextwiki.sqlite3",
    )
    monkeypatch.setattr(
        "environments.config.chromadb.PersistentClient",
        RecordingPersistentClient,
    )

    with pytest.raises(RuntimeError, match="ancestor.*writable") as exc_info:
        setup_chroma(config, require_private=True)

    assert str(tmp_path) not in str(exc_info.value)
    assert client_calls == []
    assert not private_parent.exists()


def test_private_sqlite_rejects_foreign_owned_intermediate_via_descriptor_mock(
    monkeypatch,
    tmp_path,
):
    foreign_ancestor = tmp_path / "foreign-ancestor"
    foreign_ancestor.mkdir(mode=0o755)
    foreign_inode = foreign_ancestor.stat().st_ino
    real_fstat = metadata_store_module.os.fstat

    def foreign_ancestor_fstat(descriptor):
        file_stat = real_fstat(descriptor)
        if file_stat.st_ino == foreign_inode:
            return SimpleNamespace(
                st_mode=file_stat.st_mode,
                st_uid=os.getuid() + 1,
            )
        return file_stat

    monkeypatch.setattr(metadata_store_module.os, "fstat", foreign_ancestor_fstat)
    sqlite_path = foreign_ancestor / "private-store" / "contextwiki.sqlite3"

    with pytest.raises(RuntimeError, match="ancestor.*owned") as exc_info:
        MetadataStore(sqlite_path, require_private=True)

    assert str(tmp_path) not in str(exc_info.value)
    assert not sqlite_path.parent.exists()


def test_sticky_temp_exception_is_root_owned_and_limited_to_standard_paths():
    sticky_root = SimpleNamespace(
        st_mode=stat.S_IFDIR | stat.S_ISVTX | 0o777,
        st_uid=0,
    )
    sticky_user = SimpleNamespace(
        st_mode=stat.S_IFDIR | stat.S_ISVTX | 0o777,
        st_uid=os.getuid(),
    )

    assert metadata_store_module._trusted_sticky_temp_directory(
        metadata_store_module.Path("/tmp"),
        sticky_root,
    )
    assert not metadata_store_module._trusted_sticky_temp_directory(
        metadata_store_module.Path("/unsafe-tmp"),
        sticky_root,
    )
    assert not metadata_store_module._trusted_sticky_temp_directory(
        metadata_store_module.Path("/tmp"),
        sticky_user,
    )
