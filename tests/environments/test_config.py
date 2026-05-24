import pytest

from environments.config import AppConfig


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("github_max_files", 0),
        ("github_max_file_bytes", 0),
        ("web_max_pages", 0),
        ("web_max_response_bytes", 0),
    ],
)
def test_phase_b_limits_must_be_positive(field, value):
    with pytest.raises(ValueError, match=field):
        AppConfig(**{field: value})


def test_web_crawl_delay_must_not_be_negative():
    with pytest.raises(ValueError, match="web_crawl_delay_seconds"):
        AppConfig(web_crawl_delay_seconds=-0.1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("github_max_files", 1.5),
        ("github_max_file_bytes", 1.5),
        ("web_max_pages", 1.5),
        ("web_max_response_bytes", 1.5),
        ("github_max_files", float("inf")),
        ("web_max_pages", float("inf")),
    ],
)
def test_phase_b_limit_values_must_be_integer_instances(field, value):
    with pytest.raises(ValueError, match=field):
        AppConfig(**{field: value})


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_web_crawl_delay_must_be_finite(value):
    with pytest.raises(ValueError, match="web_crawl_delay_seconds"):
        AppConfig(web_crawl_delay_seconds=value)


@pytest.mark.parametrize("value", [True, False, "1", None])
def test_web_crawl_delay_must_be_numeric_not_bool(value):
    with pytest.raises(ValueError, match="web_crawl_delay_seconds"):
        AppConfig(web_crawl_delay_seconds=value)


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


def test_phase_b_source_lists_parse_comma_newline_and_whitespace(monkeypatch):
    monkeypatch.setenv(
        "CONTEXTWIKI_GITHUB_REPOSITORIES",
        " eunhwa99/MCPContentSearch@main,\n  eunhwa99/docs@release ,, ",
    )
    monkeypatch.setenv(
        "CONTEXTWIKI_WEB_URLS",
        " https://docs.example.com,\n  https://api.example.com/docs ,, ",
    )

    config = AppConfig()

    assert config.github_repositories == (
        "eunhwa99/MCPContentSearch@main",
        "eunhwa99/docs@release",
    )
    assert config.web_seed_urls == (
        "https://docs.example.com",
        "https://api.example.com/docs",
    )


@pytest.mark.parametrize(
    "name",
    [
        "CONTEXTWIKI_GITHUB_MAX_FILES",
        "CONTEXTWIKI_GITHUB_MAX_FILE_BYTES",
        "CONTEXTWIKI_WEB_MAX_PAGES",
        "CONTEXTWIKI_WEB_MAX_RESPONSE_BYTES",
    ],
)
def test_phase_b_limit_env_values_must_be_valid_integers(monkeypatch, name):
    monkeypatch.setenv(name, "oops")

    with pytest.raises(ValueError, match=name):
        AppConfig()


@pytest.mark.parametrize("value", ["oops", "nan", "inf", "-inf"])
def test_web_crawl_delay_env_must_be_valid_finite_float(monkeypatch, value):
    monkeypatch.setenv("CONTEXTWIKI_WEB_CRAWL_DELAY_SECONDS", value)

    with pytest.raises(ValueError, match="CONTEXTWIKI_WEB_CRAWL_DELAY_SECONDS"):
        AppConfig()
