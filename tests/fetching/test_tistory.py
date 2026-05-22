import asyncio

import pytest

from fetching.tistory import fetch_post


pytestmark = pytest.mark.unit


class FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return """
        <html>
          <h1>Post title</h1>
          <time>2026-05-22</time>
          <div class="entry-content">Post body</div>
        </html>
        """


class FakeSession:
    def get(self, url, ssl, timeout):
        self.url = url
        return FakeResponse()


def test_fetch_post_populates_native_external_id():
    session = FakeSession()

    post = asyncio.run(fetch_post(session, "devlog", 7, 1.0))

    assert post["id"] == "tistory_7"
    assert post["document_id"] == "devlog:7"
    assert post["external_id"] == "devlog:7"
    assert post["canonical_url"] == "https://devlog.tistory.com/7"
    assert session.url == "https://devlog.tistory.com/7"
