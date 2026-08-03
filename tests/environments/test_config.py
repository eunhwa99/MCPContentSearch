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


def test_config_import_honors_dotenv_disable_with_populated_repo_env(
    monkeypatch,
    tmp_path,
):
    fake_env_path = tmp_path / ".env"
    fake_env_path.write_text(
        "CONTEXTWIKI_CAREER_MAX_FILES=1\n",
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setenv("CONTEXTWIKI_DISABLE_DOTENV", "1")
    monkeypatch.delenv("CONTEXTWIKI_CAREER_MAX_FILES", raising=False)
    monkeypatch.setattr(runtime_env, "_repo_dotenv_path", lambda: fake_env_path)
    monkeypatch.setattr(
        runtime_env,
        "load_dotenv",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    config_module = importlib.import_module("environments.config")
    importlib.reload(config_module)
    config = config_module.AppConfig()

    assert calls == []
    assert config.career_max_files == 100


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("github_max_files", 0),
        ("github_max_file_bytes", 0),
        ("obsidian_max_files", 0),
        ("obsidian_max_file_bytes", 0),
        ("career_max_file_bytes", 0),
        ("career_max_files", 0),
        ("career_max_total_raw_bytes", 0),
        ("career_max_total_extracted_text_bytes", 0),
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
        ("career_max_file_bytes", 1.5),
        ("career_max_file_bytes", float("inf")),
        ("career_max_files", 1.5),
        ("career_max_total_raw_bytes", 1.5),
        ("career_max_total_extracted_text_bytes", float("inf")),
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
        "CONTEXTWIKI_GITHUB_REPOSITORIES",
        " eunhwa99/MCPContentSearch@main,\n  eunhwa99/docs@release ,, ",
    )

    config = AppConfig()

    assert config.github_repositories == (
        "eunhwa99/MCPContentSearch@main",
        "eunhwa99/docs@release",
    )


@pytest.mark.parametrize(
    "name",
    [
        "CONTEXTWIKI_GITHUB_MAX_FILES",
        "CONTEXTWIKI_GITHUB_MAX_FILE_BYTES",
        "CONTEXTWIKI_OBSIDIAN_MAX_FILES",
        "CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES",
        "CONTEXTWIKI_CAREER_MAX_FILE_BYTES",
        "CONTEXTWIKI_CAREER_MAX_FILES",
        "CONTEXTWIKI_CAREER_MAX_TOTAL_RAW_BYTES",
        "CONTEXTWIKI_CAREER_MAX_TOTAL_EXTRACTED_TEXT_BYTES",
    ],
)
def test_source_limit_env_values_must_be_valid_integers(monkeypatch, name):
    monkeypatch.setenv(name, "oops")

    with pytest.raises(ValueError, match=name):
        AppConfig()


def test_obsidian_limits_load_from_env(monkeypatch):
    monkeypatch.setenv("CONTEXTWIKI_OBSIDIAN_MAX_FILES", "17")
    monkeypatch.setenv("CONTEXTWIKI_OBSIDIAN_MAX_FILE_BYTES", "4096")

    config = AppConfig()

    assert config.obsidian_max_files == 17
    assert config.obsidian_max_file_bytes == 4096


def test_career_per_file_limit_rejects_unbounded_constructor_value():
    with pytest.raises(ValueError, match="career_max_file_bytes.*maximum"):
        AppConfig(career_max_file_bytes=2**63 - 1)


def test_career_per_file_limit_rejects_unbounded_environment_value(monkeypatch):
    monkeypatch.setenv("CONTEXTWIKI_CAREER_MAX_FILE_BYTES", str(2**63 - 1))

    with pytest.raises(
        ValueError,
        match="(?:CONTEXTWIKI_CAREER_MAX_FILE_BYTES|career_max_file_bytes).*maximum",
    ):
        AppConfig()


def test_obsidian_vault_path_expands_user_home_from_env(monkeypatch):
    monkeypatch.setenv("CONTEXTWIKI_OBSIDIAN_VAULT_PATH", "~/vaults/contextwiki")

    config = AppConfig()

    assert config.obsidian_vault_path is not None
    assert config.obsidian_vault_path.is_absolute()
    assert "~" not in str(config.obsidian_vault_path)


def test_obsidian_vault_path_expands_user_home_from_constructor_string():
    config = AppConfig(obsidian_vault_path="~/vaults/contextwiki")

    assert config.obsidian_vault_path is not None
    assert config.obsidian_vault_path.is_absolute()
    assert "~" not in str(config.obsidian_vault_path)


def test_obsidian_vault_path_expands_user_home_from_constructor_path():
    config = AppConfig(obsidian_vault_path=Path("~/vaults/contextwiki"))

    assert config.obsidian_vault_path is not None
    assert config.obsidian_vault_path.is_absolute()
    assert "~" not in str(config.obsidian_vault_path)


def test_obsidian_vault_path_invalid_tilde_user_does_not_raise():
    config = AppConfig(obsidian_vault_path="~nonexistentuser/vault")

    assert config.obsidian_vault_path == Path("~nonexistentuser/vault")


def test_obsidian_vault_path_invalid_tilde_user_env_does_not_raise(monkeypatch):
    monkeypatch.setenv("CONTEXTWIKI_OBSIDIAN_VAULT_PATH", "~nonexistentuser/vault")

    config = AppConfig()

    assert config.obsidian_vault_path == Path("~nonexistentuser/vault")


def test_cache_dir_defaults_under_contextwiki_home():
    config = AppConfig()

    assert config.cache_dir == str(Path.home() / ".mcp_content_search" / "llama_cache")
