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


class FakeDatetimeResponse(FakeResponse):
    async def text(self):
        return """
        <html>
          <h1>Post title</h1>
          <time datetime="2026-05-22T03:04:05+09:00">2026. 5. 22. 03:04</time>
          <div class="entry-content">Post body</div>
        </html>
        """


class FakeDatetimeSession(FakeSession):
    def get(self, url, ssl, timeout):
        self.url = url
        return FakeDatetimeResponse()


class FakeDisplayDateResponse(FakeResponse):
    async def text(self):
        return """
        <html>
          <h1>Post title</h1>
          <span class="date">어제 오후 3시</span>
          <div class="entry-content">Post body</div>
        </html>
        """


class FakeDisplayDateSession(FakeSession):
    def get(self, url, ssl, timeout):
        self.url = url
        return FakeDisplayDateResponse()


def test_fetch_post_populates_native_external_id():
    session = FakeSession()

    post = asyncio.run(fetch_post(session, "devlog", 7, 1.0))

    assert post["id"] == "tistory_7"
    assert post["document_id"] == "devlog:7"
    assert post["external_id"] == "devlog:7"
    assert post["canonical_url"] == "https://devlog.tistory.com/7"
    assert post["published_at"] == "2026-05-22"
    assert post["date_provenance"] == "tistory"
    assert session.url == "https://devlog.tistory.com/7"


def test_fetch_post_prefers_time_datetime_for_normalized_publication():
    post = asyncio.run(fetch_post(FakeDatetimeSession(), "devlog", 7, 1.0))

    assert post["date"] == "2026. 5. 22. 03:04"
    assert post["published_at"] == "2026-05-22T03:04:05+09:00"


def test_fetch_post_keeps_non_iso_display_date_out_of_normalized_field():
    post = asyncio.run(fetch_post(FakeDisplayDateSession(), "devlog", 7, 1.0))

    assert post["date"] == "어제 오후 3시"
    assert post["published_at"] == ""
    assert post["date_provenance"] == ""
