import pytest

from environments.config import AppConfig


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("github_max_files", 0),
        ("github_max_file_bytes", 0),
    ],
)
def test_github_limits_must_be_positive(field, value):
    with pytest.raises(ValueError, match=field):
        AppConfig(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("github_max_files", 1.5),
        ("github_max_file_bytes", 1.5),
        ("github_max_files", float("inf")),
    ],
)
def test_github_limit_values_must_be_integer_instances(field, value):
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
    ],
)
def test_github_limit_env_values_must_be_valid_integers(monkeypatch, name):
    monkeypatch.setenv(name, "oops")

    with pytest.raises(ValueError, match=name):
        AppConfig()
