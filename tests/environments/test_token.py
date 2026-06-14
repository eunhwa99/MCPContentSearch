import importlib
import sys

import dotenv
import pytest

pytestmark = pytest.mark.unit


def test_tistory_blog_name_defaults_empty_when_env_missing(monkeypatch):
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.delenv("TISTORY_BLOG_NAME", raising=False)

    try:
        sys.modules.pop("environments.token", None)
        reloaded = importlib.import_module("environments.token")

        assert reloaded.TISTORY_BLOG_NAME == ""
    finally:
        monkeypatch.undo()
        sys.modules.pop("environments.token", None)
