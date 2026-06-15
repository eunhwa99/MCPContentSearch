import importlib
import sys

import pytest

from environments import runtime_env

pytestmark = pytest.mark.unit


def test_tistory_blog_name_defaults_empty_when_env_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(
        runtime_env,
        "load_repo_dotenv",
        lambda *args, **kwargs: calls.append((args, kwargs)) or False,
    )
    monkeypatch.delenv("TISTORY_BLOG_NAME", raising=False)

    try:
        sys.modules.pop("environments.token", None)
        reloaded = importlib.import_module("environments.token")

        assert reloaded.TISTORY_BLOG_NAME == ""
        assert calls == [((), {})]
    finally:
        monkeypatch.undo()
        sys.modules.pop("environments.token", None)
