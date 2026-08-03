import os

import pytest

from environments import runtime_env


pytestmark = pytest.mark.unit


def test_load_repo_dotenv_uses_explicit_repo_env_path(monkeypatch, tmp_path):
    calls = []
    monkeypatch.delenv("CONTEXTWIKI_DISABLE_DOTENV", raising=False)
    fake_env_path = tmp_path / ".env"
    fake_env_path.write_text("CONTEXTWIKI_OBSIDIAN_VAULT_PATH=/tmp/vault\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runtime_env, "_repo_dotenv_path", lambda: fake_env_path)
    monkeypatch.setattr(
        runtime_env,
        "load_dotenv",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    runtime_env.load_repo_dotenv()

    assert calls == [
        (
            (),
            {
                "dotenv_path": fake_env_path,
                "override": False,
            },
        )
    ]


def test_get_env_secret_loads_repo_dotenv_before_lookup(monkeypatch, tmp_path):
    monkeypatch.delenv("CONTEXTWIKI_DISABLE_DOTENV", raising=False)
    fake_env_path = tmp_path / ".env"
    fake_env_path.write_text("OPENAI_API_KEY=from-dotenv\n")

    def fake_load_dotenv(*args, **kwargs):
        os.environ["OPENAI_API_KEY"] = "from-dotenv"
        return True

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runtime_env, "_repo_dotenv_path", lambda: fake_env_path)
    monkeypatch.setattr(runtime_env, "load_dotenv", fake_load_dotenv)

    assert runtime_env.get_env_secret("OPENAI_API_KEY") == "from-dotenv"


def test_load_repo_dotenv_disable_switch_ignores_populated_repo_env(
    monkeypatch,
    tmp_path,
):
    fake_env_path = tmp_path / ".env"
    fake_env_path.write_text(
        "CONTEXTWIKI_CAREER_MAX_FILES=1\nOPENAI_API_KEY=must-not-load\n",
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setenv("CONTEXTWIKI_DISABLE_DOTENV", "1")
    monkeypatch.delenv("CONTEXTWIKI_CAREER_MAX_FILES", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(runtime_env, "_repo_dotenv_path", lambda: fake_env_path)
    monkeypatch.setattr(
        runtime_env,
        "load_dotenv",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    assert runtime_env.load_repo_dotenv() is False
    assert calls == []
    assert os.getenv("CONTEXTWIKI_CAREER_MAX_FILES") is None
    assert os.getenv("OPENAI_API_KEY") is None
