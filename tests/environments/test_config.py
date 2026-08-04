import importlib
from pathlib import Path

import pytest

from environments import runtime_env
from environments.config import AppConfig


pytestmark = pytest.mark.unit


def test_config_module_loads_repo_dotenv_before_reading_defaults(monkeypatch):
    calls = []
    monkeypatch.setattr(
        runtime_env,
        "load_repo_dotenv",
        lambda *args, **kwargs: calls.append((args, kwargs)) or False,
    )

    config_module = importlib.import_module("environments.config")
    importlib.reload(config_module)

    assert calls == [((), {})]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("github_max_files", 0),
        ("github_max_file_bytes", 0),
        ("obsidian_max_files", 0),
        ("obsidian_max_file_bytes", 0),
    ],
)
def test_source_limits_must_be_positive(field, value):
    with pytest.raises(ValueError, match=field):
        AppConfig(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("github_max_files", 1.5),
        ("github_max_file_bytes", 1.5),
        ("github_max_files", float("inf")),
        ("obsidian_max_files", 1.5),
        ("obsidian_max_file_bytes", 1.5),
        ("obsidian_max_files", float("inf")),
    ],
)
def test_source_limit_values_must_be_integer_instances(field, value):
    with pytest.raises(ValueError, match=field):
        AppConfig(**{field: value})


@pytest.mark.parametrize(
    "value",
    [
        "",
        "github_token",
        "GITHUB-TOKEN",
        "GITHUB TOKEN",
        "GITHUB_TOKEN\nEXTRA",
        "ghp_secret123",
        "github_pat_secret123",
        "AKIAIOSFODNN7EXAMPLE",
        "ASIAIOSFODNN7EXAMPLE",
        "GHP_SECRET1234567890",
    ],
)
def test_github_token_env_var_must_be_safe_metadata_reference(value):
    with pytest.raises(ValueError, match="github_token_env_var"):
        AppConfig(github_token_env_var=value)


def test_github_source_list_parses_comma_newline_and_whitespace(monkeypatch):
    monkeypatch.setenv(
        "CONTEXTZIP_GITHUB_REPOSITORIES",
        " eunhwa99/context-zip@main,\n  eunhwa99/docs@release ,, ",
    )

    config = AppConfig()

    assert config.github_repositories == (
        "eunhwa99/context-zip@main",
        "eunhwa99/docs@release",
    )


@pytest.mark.parametrize(
    "name",
    [
        "CONTEXTZIP_GITHUB_MAX_FILES",
        "CONTEXTZIP_GITHUB_MAX_FILE_BYTES",
        "CONTEXTZIP_OBSIDIAN_MAX_FILES",
        "CONTEXTZIP_OBSIDIAN_MAX_FILE_BYTES",
    ],
)
def test_source_limit_env_values_must_be_valid_integers(monkeypatch, name):
    monkeypatch.setenv(name, "oops")

    with pytest.raises(ValueError, match=name):
        AppConfig()


def test_obsidian_limits_load_from_env(monkeypatch):
    monkeypatch.setenv("CONTEXTZIP_OBSIDIAN_MAX_FILES", "17")
    monkeypatch.setenv("CONTEXTZIP_OBSIDIAN_MAX_FILE_BYTES", "4096")

    config = AppConfig()

    assert config.obsidian_max_files == 17
    assert config.obsidian_max_file_bytes == 4096


def test_obsidian_vault_path_expands_user_home_from_env(monkeypatch):
    monkeypatch.setenv("CONTEXTZIP_OBSIDIAN_VAULT_PATH", "~/vaults/context-zip")

    config = AppConfig()

    assert config.obsidian_vault_path is not None
    assert config.obsidian_vault_path.is_absolute()
    assert "~" not in str(config.obsidian_vault_path)


def test_obsidian_vault_path_expands_user_home_from_constructor_string():
    config = AppConfig(obsidian_vault_path="~/vaults/context-zip")

    assert config.obsidian_vault_path is not None
    assert config.obsidian_vault_path.is_absolute()
    assert "~" not in str(config.obsidian_vault_path)


def test_obsidian_vault_path_expands_user_home_from_constructor_path():
    config = AppConfig(obsidian_vault_path=Path("~/vaults/context-zip"))

    assert config.obsidian_vault_path is not None
    assert config.obsidian_vault_path.is_absolute()
    assert "~" not in str(config.obsidian_vault_path)


def test_obsidian_vault_path_invalid_tilde_user_does_not_raise():
    config = AppConfig(obsidian_vault_path="~nonexistentuser/vault")

    assert config.obsidian_vault_path == Path("~nonexistentuser/vault")


def test_obsidian_vault_path_invalid_tilde_user_env_does_not_raise(monkeypatch):
    monkeypatch.setenv("CONTEXTZIP_OBSIDIAN_VAULT_PATH", "~nonexistentuser/vault")

    config = AppConfig()

    assert config.obsidian_vault_path == Path("~nonexistentuser/vault")


def test_cache_dir_defaults_under_context_zip_home():
    config = AppConfig()

    context_zip_home = Path.home() / ".context-zip"
    assert config.cache_dir == str(context_zip_home / "llama_cache")
    assert config.chroma_db_path == context_zip_home / "chroma_db"
    assert config.metadata_db_path == context_zip_home / "context_zip_metadata.sqlite3"
