import asyncio
import traceback

import httpx
import pytest

from environments.config import AppConfig
from fetching.connectors import WebsiteSourceConnector
from fetching.web_docs import FetchResponse, RobotsRules, SITEMAP_NAMESPACE, WebsiteHTTPClient


pytestmark = pytest.mark.unit


class FakeWebHTTP:
    def __init__(self, responses):
        self.responses = responses
        self.requested = []

    async def get_text(self, url, headers=None):
        self.requested.append(url)
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        return self.responses[url]


class HeaderWebHTTP(FakeWebHTTP):
    def __init__(self, responses, headers):
        super().__init__(responses)
        self.headers = headers

    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        return FetchResponse(
            url=url,
            text=self.responses[url],
            headers=self.headers.get(url, {}),
        )


class RedirectingWebHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/start":
            return FetchResponse(
                url="https://other.example.com/landing",
                text="",
                status_code=302,
                headers={"location": "https://other.example.com/landing"},
            )
        if url == "https://other.example.com/landing":
            return FetchResponse(
                url=url,
                text="<html><body><main>Other origin.</main></body></html>",
            )
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        return FetchResponse(url=url, text=self.responses[url])


class SameOriginRedirectingWebHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/start":
            return FetchResponse(
                url="https://docs.example.com/private/secret",
                text="",
                status_code=302,
                headers={"location": "/private/secret"},
            )
        if url == "https://docs.example.com/private/secret":
            return FetchResponse(
                url=url,
                text="<html><body><main>Secret docs.</main></body></html>",
            )
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        return FetchResponse(url=url, text=self.responses[url])


class DeferredRedirectingWebHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/alias":
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "/private/secret"},
            )
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        return FetchResponse(url=url, text=self.responses[url])


class CredentialRedirectingWebHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/start":
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "https://user:secret@docs.example.com/private"},
            )
        raise RuntimeError(f"unexpected credentialed fetch: {url}")


class HTTPStatusWebHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        request = httpx.Request("GET", url)
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError("not found", request=request, response=response)


class RedirectingRobotsHTTPStatusWebHTTP(FakeWebHTTP):
    async def get_robots_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/robots.txt":
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "/robots-error?session=privatevalue"},
            )
        request = httpx.Request("GET", url)
        response = httpx.Response(503, request=request)
        raise httpx.HTTPStatusError("robots unavailable", request=request, response=response)

    async def get_response(self, url, headers=None):
        self.requested.append(url)
        return FetchResponse(url=url, text=self.responses[url])


class MediaRedirectingWebHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/logo.png":
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "/asset"},
            )
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        return FetchResponse(url=url, text=self.responses[url])


class SchemeDowngradeRedirectingWebHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/start":
            return FetchResponse(
                url="https://docs.example.com/start",
                text="",
                status_code=302,
                headers={"location": "http://docs.example.com/start"},
            )
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        return FetchResponse(url=url, text=self.responses[url])


class AliasRedirectingWebHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/start":
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "/guide"},
            )
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        return FetchResponse(url=url, text=self.responses[url])


class DistinctAliasRedirectingWebHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url in {"https://docs.example.com/start", "https://docs.example.com/alias"}:
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "/guide"},
            )
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        return FetchResponse(url=url, text=self.responses[url])


class ManyAliasRedirectingWebHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url.startswith("https://docs.example.com/alias-"):
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "/guide"},
            )
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        return FetchResponse(url=url, text=self.responses[url])


class MultiHopRedirectingWebHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/alias-0":
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "/redirect-1"},
            )
        if url == "https://docs.example.com/redirect-1":
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "/redirect-2"},
            )
        if url == "https://docs.example.com/redirect-2":
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "/redirect-3"},
            )
        if url == "https://docs.example.com/redirect-3":
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "/guide"},
            )
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        return FetchResponse(url=url, text=self.responses[url])


class TrailingSlashRedirectingWebHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/guide":
            return FetchResponse(
                url=url,
                text="",
                status_code=301,
                headers={"location": "/guide/"},
            )
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        return FetchResponse(url=url, text=self.responses[url])


class RedirectingRobotsHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/robots.txt":
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "/robots-rules"},
            )
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        return FetchResponse(url=url, text=self.responses[url])


class FailingRobotsHTTP(FakeWebHTTP):
    async def get_text(self, url, headers=None):
        if url.endswith("/robots.txt"):
            raise RuntimeError("robots unavailable")
        return await super().get_text(url, headers=headers)


class InvalidPortRedirectingWebHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/start":
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "https://docs.example.com:99999/bad"},
            )
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        return FetchResponse(url=url, text=self.responses[url])


class SkippedBodyHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        if url.endswith("/robots.txt"):
            return FetchResponse(url=url, text=self.responses[url])
        return FetchResponse(
            url=url,
            text="",
            headers={"Content-Type": "text/html"},
            body_skipped=True,
        )


class SkippedRobotsHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/robots.txt":
            return FetchResponse(
                url=url,
                text="",
                headers={"Content-Type": "text/plain"},
                body_skipped=True,
            )
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        return FetchResponse(url=url, text=self.responses[url])


class MalformedHostRedirectingWebHTTP(FakeWebHTTP):
    async def get_response(self, url, headers=None):
        self.requested.append(url)
        if url == "https://docs.example.com/start":
            return FetchResponse(
                url=url,
                text="",
                status_code=302,
                headers={"location": "https://[::1"},
            )
        if url not in self.responses:
            raise RuntimeError(f"missing response for {url}")
        return FetchResponse(url=url, text=self.responses[url])


class ExplodingByteStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        raise AssertionError("response body should not be read")
        yield b""


def test_web_connector_reads_sitemap_and_respects_robots_disallow():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "User-agent: *\nDisallow: /private\n",
            "https://docs.example.com/sitemap.xml": """
                <urlset>
                  <url><loc>https://docs.example.com/guide</loc></url>
                  <url><loc>https://docs.example.com/private/secret</loc></url>
                </urlset>
            """,
            "https://docs.example.com/guide": """
                <html>
                  <head>
                    <title>Guide</title>
                    <link rel="canonical" href="https://docs.example.com/guide" />
                  </head>
                  <body>
                    <nav>Navigation</nav>
                    <article><h1>Guide</h1><p>ContextWiki web docs.</p></article>
                  </body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=5, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert connector.supports_stale_cleanup is False
    assert connector.source.source_id == "source_web"
    assert connector.source.enabled is True
    assert connector.source.auth_ref == "env:CONTEXTWIKI_WEB_URLS"
    assert len(documents) == 1
    assert documents[0].document_id == "web:https://docs.example.com/guide"
    assert documents[0].external_id == documents[0].document_id
    assert documents[0].canonical_url == "https://docs.example.com/guide"
    assert documents[0].path == "https://docs.example.com/guide"
    assert documents[0].platform == "Web"
    assert "ContextWiki web docs." in documents[0].content
    assert "Navigation" not in documents[0].content
    assert "https://docs.example.com/private/secret" not in client.requested


def test_web_connector_sitemap_seed_does_not_consume_page_budget():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": """
                <urlset>
                  <url><loc>https://docs.example.com/guide</loc></url>
                </urlset>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=1, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]
    assert connector.supports_stale_cleanup is True


def test_web_connector_disables_stale_cleanup_for_empty_sitemap():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": """
                <urlset>
                </urlset>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=5, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_blocks_percent_encoded_robots_disallowed_paths():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "User-agent: *\nDisallow: /private\n",
            "https://docs.example.com/sitemap.xml": """
                <urlset>
                  <url><loc>https://docs.example.com/pri%76ate/secret</loc></url>
                  <url><loc>https://docs.example.com/guide</loc></url>
                </urlset>
            """,
            "https://docs.example.com/pri%76ate/secret": """
                <html><body><main>Secret docs.</main></body></html>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]
    assert "https://docs.example.com/pri%76ate/secret" not in client.requested


def test_web_connector_ignores_sitemap_extension_loc_entries():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": """
                <urlset xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
                  <url>
                    <loc>https://docs.example.com/guide</loc>
                    <image:image>
                      <image:loc>https://docs.example.com/logo.png</image:loc>
                    </image:image>
                  </url>
                </urlset>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
            "https://docs.example.com/logo.png": """
                <html><body><main>Logo asset.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]
    assert "https://docs.example.com/logo.png" not in client.requested


def test_web_connector_ignores_direct_namespaced_sitemap_extension_entries():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": """
                <urlset xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
                  <image:url>
                    <image:loc>https://docs.example.com/asset-page</image:loc>
                  </image:url>
                  <url>
                    <image:loc>https://docs.example.com/logo.png</image:loc>
                    <loc>https://docs.example.com/guide</loc>
                  </url>
                </urlset>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
            "https://docs.example.com/asset-page": """
                <html><body><main>Asset page.</main></body></html>
            """,
            "https://docs.example.com/logo.png": """
                <html><body><main>Logo asset.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=4, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]
    assert "https://docs.example.com/asset-page" not in client.requested
    assert "https://docs.example.com/logo.png" not in client.requested


def test_web_connector_disables_stale_cleanup_for_unknown_sitemap_child():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": """
                <urlset>
                  <entry><loc>https://docs.example.com/guide</loc></entry>
                </urlset>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert "https://docs.example.com/guide" not in client.requested
    assert connector.supports_stale_cleanup is False


@pytest.mark.parametrize(
    "sitemap_body",
    [
        """
        <urlset>
          <url><loc>https://other.example.com/guide</loc></url>
        </urlset>
        """,
        """
        <sitemapindex>
          <sitemap><loc>https://other.example.com/sitemap.xml</loc></sitemap>
        </sitemapindex>
        """,
    ],
)
def test_web_connector_disables_stale_cleanup_for_cross_origin_sitemap_loc(
    sitemap_body,
):
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": sitemap_body,
            "https://other.example.com/guide": """
                <html><body><main>Other guide.</main></body></html>
            """,
            "https://other.example.com/sitemap.xml": """
                <urlset><url><loc>https://other.example.com/guide</loc></url></urlset>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert "https://other.example.com/guide" not in client.requested
    assert "https://other.example.com/sitemap.xml" not in client.requested
    assert connector.supports_stale_cleanup is False


def test_web_connector_rejects_non_sitemap_namespace_root():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": """
                <urlset xmlns="http://www.google.com/schemas/sitemap-image/1.1">
                  <url><loc>https://docs.example.com/logo.png</loc></url>
                </urlset>
            """,
            "https://docs.example.com/logo.png": """
                <html><body><main>Logo asset.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=client,
    )

    with pytest.raises(ValueError, match="Invalid sitemap"):
        asyncio.run(connector.fetch_documents())

    assert "https://docs.example.com/logo.png" not in client.requested


def test_web_connector_accepts_official_sitemap_namespace_root():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": """
                <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <url><loc>https://docs.example.com/guide</loc></url>
                </urlset>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]


def test_web_connector_accepts_prefixed_official_sitemap_root_on_custom_path():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/docs-map.xml": """
                <sm:urlset xmlns:sm="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <sm:url><sm:loc>https://docs.example.com/guide</sm:loc></sm:url>
                </sm:urlset>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/docs-map.xml",),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]


def test_web_connector_accepts_bom_prefixed_sitemap():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": (
                "\ufeff<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">"
                "<url><loc>https://docs.example.com/guide</loc></url>"
                "</urlset>"
            ),
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]


def test_web_connector_accepts_bom_prefixed_sitemap_on_custom_path():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/docs-map": (
                "\ufeff<sm:urlset "
                "xmlns:sm=\"http://www.sitemaps.org/schemas/sitemap/0.9\">"
                "<sm:url><sm:loc>https://docs.example.com/guide</sm:loc></sm:url>"
                "</sm:urlset>"
            ),
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/docs-map",),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]


def test_web_connector_skips_unsupported_media_content_type():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/guide.pdf": "%PDF-1.7 binary-ish text",
        },
        {
            "https://docs.example.com/guide.pdf": {
                "Content-Type": "application/pdf",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide.pdf",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_skips_mislabelled_media_content_type():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/guide.pdf": "%PDF-1.7 binary-ish text",
        },
        {
            "https://docs.example.com/guide.pdf": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide.pdf",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


@pytest.mark.parametrize(
    ("media_url", "headers"),
    [
        ("https://docs.example.com/guide.pdf", {"Content-Type": "text/html"}),
        ("https://docs.example.com/download?file=guide.pdf", {"Content-Type": "text/html"}),
        ("https://docs.example.com/guide.pdf", {}),
    ],
)
def test_web_connector_disables_stale_cleanup_for_media_hinted_html_page(
    media_url,
    headers,
):
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            media_url: "<html><body><main>Download landing page.</main></body></html>",
        },
        {
            media_url: headers,
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=(media_url,),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.document_id for document in documents] == [f"web:{media_url}"]
    assert connector.supports_stale_cleanup is False


@pytest.mark.parametrize(
    "media_url",
    [
        "https://docs.example.com/download",
        "https://docs.example.com/download?file=guide.pdf",
    ],
)
def test_web_connector_skips_text_plain_media_body(media_url):
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            media_url: "%PDF-1.7 binary-ish text",
        },
        {
            media_url: {
                "Content-Type": "text/plain",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=(media_url,),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_skips_text_plain_query_key_media_body():
    media_url = "https://docs.example.com/download?logo.svg"
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            media_url: "<svg><text>Logo asset</text></svg>",
        },
        {
            media_url: {
                "Content-Type": "text/plain",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=(media_url,),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_skips_text_plain_raw_png_media_body():
    def handler(request):
        if str(request.url) == "https://docs.example.com/robots.txt":
            return httpx.Response(200, content=b"", request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain"},
            content=b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR binary-ish png",
            request=request,
        )

    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/download",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=WebsiteHTTPClient(
            timeout=1,
            max_response_bytes=1000,
            transport=httpx.MockTransport(handler),
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_rejects_invalid_text_decoding_for_stale_cleanup():
    def handler(request):
        if str(request.url) == "https://docs.example.com/robots.txt":
            return httpx.Response(200, content=b"", request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content=(
                b"<html><body><main>"
                + (b"A" * 1100)
                + b"\xff"
                + b"</main></body></html>"
            ),
            request=request,
        )

    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=WebsiteHTTPClient(
            timeout=1,
            max_response_bytes=2000,
            transport=httpx.MockTransport(handler),
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_skips_text_plain_extensionless_svg_media_body():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/download": (
                "\ufeff  <svg><text>Logo asset</text></svg>"
            ),
        },
        {
            "https://docs.example.com/download": {
                "Content-Type": "text/plain",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/download",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_skips_text_plain_comment_prefixed_svg_media_body():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/download": (
                "<!-- generated asset --><svg><text>Logo asset</text></svg>"
            ),
        },
        {
            "https://docs.example.com/download": {
                "Content-Type": "text/plain",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/download",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_skips_text_plain_bzip2_media_body():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/download": "BZh91AY&SY binary-ish bzip2",
        },
        {
            "https://docs.example.com/download": {
                "Content-Type": "text/plain",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/download",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


@pytest.mark.parametrize(
    "media_body",
    [
        "<rss><channel><title>Feed asset</title></channel></rss>",
        "<feed><title>Atom asset</title></feed>",
        "a" * 257 + "ustar archive marker",
    ],
)
def test_web_connector_skips_text_plain_extensionless_non_page_bodies(media_body):
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/download": media_body,
        },
        {
            "https://docs.example.com/download": {
                "Content-Type": "text/plain",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/download",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_skips_text_plain_unknown_xml_root_with_nested_body():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/download": (
                "<mxfile><diagram><body>Diagram asset</body></diagram></mxfile>"
            ),
        },
        {
            "https://docs.example.com/download": {
                "Content-Type": "text/plain",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/download",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_skips_text_plain_raw_exe_media_body():
    def handler(request):
        if str(request.url) == "https://docs.example.com/robots.txt":
            return httpx.Response(200, content=b"", request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain"},
            content=b"MZ\x90\x00\x03\x00\x00\x00 binary-ish executable",
            request=request,
        )

    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/download",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=WebsiteHTTPClient(
            timeout=1,
            max_response_bytes=1000,
            transport=httpx.MockTransport(handler),
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


@pytest.mark.parametrize(
    "media_body",
    [
        b"\x1aE\xdf\xa3\x93B\x82\x88webm",
        b"\xff\xfb\x90dMP3 frame",
    ],
)
def test_web_connector_skips_text_plain_supported_extension_media_bytes(media_body):
    def handler(request):
        if str(request.url) == "https://docs.example.com/robots.txt":
            return httpx.Response(200, content=b"", request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain"},
            content=media_body,
            request=request,
        )

    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/asset.txt",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=WebsiteHTTPClient(
            timeout=1,
            max_response_bytes=1000,
            transport=httpx.MockTransport(handler),
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_marks_extensionless_text_plain_doc_incomplete():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/release-notes": "Release notes as plain text.",
        },
        {
            "https://docs.example.com/release-notes": {
                "Content-Type": "text/plain",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/release-notes",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.content for document in documents] == [
        "Release notes as plain text.",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_accepts_xhtml_page_with_xml_declaration():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": (
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                "<html xmlns=\"http://www.w3.org/1999/xhtml\">"
                "<body><main>XHTML docs.</main></body></html>"
            ),
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "application/xhtml+xml; charset=utf-8",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.content for document in documents] == ["XHTML docs."]
    assert connector.supports_stale_cleanup is True


def test_web_connector_accepts_xhtml_page_with_inline_svg():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": (
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                "<html xmlns=\"http://www.w3.org/1999/xhtml\">"
                "<body><main><svg><title>Icon</title></svg>"
                "<p>XHTML docs.</p></main></body></html>"
            ),
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "application/xhtml+xml",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.content for document in documents] == ["Icon\nXHTML docs."]
    assert connector.supports_stale_cleanup is True


def test_web_connector_accepts_prefixed_xhtml_page_with_long_head():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": (
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                "<xhtml:html xmlns:xhtml=\"http://www.w3.org/1999/xhtml\">"
                f"<xhtml:head><xhtml:title>Title</xhtml:title>{'x' * 600}</xhtml:head>"
                "<xhtml:body><xhtml:main>Prefixed XHTML docs.</xhtml:main>"
                "</xhtml:body></xhtml:html>"
            ),
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "application/xhtml+xml",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents[0].canonical_url == "https://docs.example.com/start"
    assert documents[0].title == "Title"
    assert documents[0].content == "Prefixed XHTML docs."
    assert connector.supports_stale_cleanup is True


def test_web_connector_extracts_prefixed_xhtml_canonical_and_body_content():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": (
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                "<xhtml:html xmlns:xhtml=\"http://www.w3.org/1999/xhtml\">"
                "<xhtml:head>"
                "<xhtml:title>Docs Title</xhtml:title>"
                "<xhtml:link rel=\"canonical\" href=\"/canonical\" />"
                "</xhtml:head>"
                "<xhtml:body>"
                "<xhtml:nav>Nav</xhtml:nav>"
                "<xhtml:main><xhtml:h1>Heading</xhtml:h1>"
                "<xhtml:p>Body docs.</xhtml:p></xhtml:main>"
                "</xhtml:body></xhtml:html>"
            ),
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "application/xhtml+xml",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents[0].document_id == "web:https://docs.example.com/canonical"
    assert documents[0].canonical_url == "https://docs.example.com/canonical"
    assert documents[0].title == "Heading"
    assert documents[0].content == "Heading\nBody docs."
    assert connector.supports_stale_cleanup is True


def test_web_connector_skips_mislabelled_xhtml_xml_for_stale_cleanup():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": (
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                "<project><name>Not XHTML docs</name></project>"
            ),
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "application/xhtml+xml",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_skips_mislabelled_text_html_xml_for_stale_cleanup():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": (
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                "<project><name>Not HTML docs</name></project>"
            ),
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


@pytest.mark.parametrize("root_name", ["urlset", "sitemapindex"])
def test_web_connector_skips_mislabelled_xhtml_sitemap_roots_for_stale_cleanup(
    root_name,
):
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": f"<{root_name}></{root_name}>",
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "application/xhtml+xml",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


@pytest.mark.parametrize("root_name", ["urlset", "sitemapindex"])
def test_web_connector_skips_mislabelled_text_html_sitemap_roots_for_stale_cleanup(
    root_name,
):
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": f"<{root_name}></{root_name}>",
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_rejects_seed_urls_with_credentials_without_leaking_secret():
    with pytest.raises(ValueError) as exc_info:
        WebsiteSourceConnector(
            seed_urls=("https://user:secret@docs.example.com/start",),
            config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        )

    assert "secret" not in str(exc_info.value)
    assert "user:secret" not in str(exc_info.value)


def test_web_connector_rejects_seed_urls_with_sensitive_query_without_leaking_secret():
    with pytest.raises(ValueError) as exc_info:
        WebsiteSourceConnector(
            seed_urls=("https://docs.example.com/start?token=secret",),
            config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        )

    assert "secret" not in str(exc_info.value)
    assert "token=secret" not in str(exc_info.value)


@pytest.mark.parametrize(
    "seed_url",
    [
        "user:secret@docs.example.com/path?token=foo",
        "token@docs.example.com/path?api_key=secret",
        "user:secret@docs.example.com/path#token=secret",
        "https://[::1?foo=ghp_secret",
        "https://[::1#token=secret",
        "ghp_secret",
        "github_pat_secret",
        "https://docs.example.com/start?foo=ghp_secret123",
        "https://docs.example.com/ghp_secret123",
        "https://docs.example.com/start?foo=ghp%5Fsecret123",
        "https://docs.example.com/%67%68%70%5Fsecret123",
        "https://docs.example.com/guide?X-Amz-Signature=privatevalue",
        "https://docs.example.com/guide?X-Amz-Credential=privatevalue",
        "https://docs.example.com/guide?AWSAccessKeyId=privatevalue",
        "https://docs.example.com/guide?X-Amz-AccessKeyId=privatevalue",
        "https://docs.example.com/guide?accessKeyId=privatevalue",
        "https://docs.example.com/AKIAIOSFODNN7EXAMPLE",
        "https://docs.example.com/AWSAccessKeyId=privatevalue",
        "https://docs.example.com/apiKey=privatevalue",
        "https://docs.example.com/clientSecret=privatevalue",
        "https://docs.example.com/token=privatevalue",
        "https://docs.example.com/password=privatevalue",
        "https://docs.example.com/authorization=privatevalue",
        "https://docs.example.com/signature=privatevalue",
        "https://docs.example.com/credential=privatevalue",
        "https://docs.example.com/guide?session=privatevalue",
        "https://docs.example.com/guide?sessionid=privatevalue",
        "https://docs.example.com/guide?sid=privatevalue",
        "https://docs.example.com/guide?cookie=privatevalue",
        "https://docs.example.com/guide?jwt=privatevalue",
        "https://docs.example.com/guide?csrf=privatevalue",
        "https://docs.example.com/guide?xsrf=privatevalue",
        "https://docs.example.com/key=privatevalue",
        "https://docs.example.com/auth=privatevalue",
        "https://docs.example.com/code=privatevalue",
        "https://docs.example.com/pass=privatevalue",
        "https://docs.example.com/sig=privatevalue",
        "https://docs.example.com/guide?q=session=privatevalue",
        "https://docs.example.com/guide?q=apiKey=privatevalue",
        "https://docs.example.com/guide?JSESSIONID=privatevalue",
        "https://docs.example.com/guide?PHPSESSID=privatevalue",
        "https://docs.example.com/guide?CSRFToken=privatevalue",
        "https://docs.example.com/guide?XSRFToken=privatevalue",
        "https://docs.example.com/guide?JWTToken=privatevalue",
        "https://docs.example.com/guide?sessionToken=privatevalue",
        "https://docs.example.com/session/privatevalue",
        "https://docs.example.com/token/privatevalue",
        "https://docs.example.com/guide?foo=%20token=privatevalue",
        "https://docs.example.com/guide?foo=%0Atoken=privatevalue",
        "https://docs.example.com/guide?foo=(token=privatevalue)",
        "https://docs.example.com/guide?foo=token+%3Dprivatevalue",
        "https://docs.example.com/guide?q=sk-proj-aaaaaaaaaaaaaaaaaaaaaaaa",
        "https://docs.example.com/guide?q=xoxb-123456789012-123456789012-token",
        "https://docs.example.com/guide?q=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.signaturefakefake",
        "https://docs.example.com/guide?q=AIzaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    ],
)
def test_web_connector_rejects_bare_or_malformed_seed_secrets_without_leaking(
    seed_url,
):
    with pytest.raises(ValueError) as exc_info:
        WebsiteSourceConnector(
            seed_urls=(seed_url,),
            config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        )

    message = str(exc_info.value)
    assert "secret" not in message
    assert "foo" not in message
    assert "ghp_secret" not in message
    assert "ghp%5Fsecret" not in message
    assert "%67%68%70%5Fsecret" not in message
    assert "privatevalue" not in message
    assert "AWSAccessKeyId" not in message
    assert "AccessKeyId" not in message
    assert "apiKey" not in message
    assert "clientSecret" not in message
    assert "password" not in message
    assert "authorization" not in message
    assert "signature" not in message
    assert "credential=privatevalue" not in message
    assert "session=privatevalue" not in message
    assert "sessionid=privatevalue" not in message
    assert "cookie=privatevalue" not in message
    assert "jwt=privatevalue" not in message
    assert "csrf=privatevalue" not in message
    assert "xsrf=privatevalue" not in message
    assert "token+%3Dprivatevalue" not in message
    assert "key=privatevalue" not in message
    assert "auth=privatevalue" not in message
    assert "code=privatevalue" not in message
    assert "pass=privatevalue" not in message
    assert "sig=privatevalue" not in message
    assert "JSESSIONID=privatevalue" not in message
    assert "PHPSESSID=privatevalue" not in message
    assert "CSRFToken=privatevalue" not in message
    assert "XSRFToken=privatevalue" not in message
    assert "JWTToken=privatevalue" not in message
    assert "sessionToken=privatevalue" not in message
    assert "token/privatevalue" not in message
    assert "session/privatevalue" not in message
    assert "token=secret" not in message
    assert "api_key=secret" not in message
    assert "X-Amz-Signature" not in message
    assert "X-Amz-Credential" not in message
    assert "X-Amz-AccessKeyId" not in message
    assert "sk-proj" not in message
    assert "xoxb" not in message
    assert "eyJ" not in message
    assert "AIza" not in message
    assert "user:secret" not in message
    assert "token@docs.example.com" not in message


def test_web_connector_redacts_malformed_sitemap_query_in_error_message():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml?foo=privatevalue",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/sitemap.xml?foo=privatevalue": "<urlset><url>",
            }
        ),
    )

    with pytest.raises(ValueError) as exc_info:
        asyncio.run(connector.fetch_documents())

    message = str(exc_info.value)
    assert "privatevalue" not in message
    assert "foo" not in message


def test_web_connector_redacts_unsupported_xml_query_in_error_message():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/feed.xml?foo=privatevalue",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/feed.xml?foo=privatevalue": (
                    "<rss><channel><title>Feed</title></channel></rss>"
                ),
            }
        ),
    )

    with pytest.raises(ValueError) as exc_info:
        asyncio.run(connector.fetch_documents())

    message = str(exc_info.value)
    assert "privatevalue" not in message
    assert "foo" not in message


def test_web_connector_rejects_token_like_page_links_without_persisting_them():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                    <body>
                        <main>Start</main>
                        <a href="/guide?foo=ghp%5Fsecret123">Guide</a>
                      </body>
                </html>
            """,
            "https://docs.example.com/guide?foo=ghp%5Fsecret123": """
                <html><body><main>Secret guide.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.document_id for document in documents] == [
        "web:https://docs.example.com/start",
    ]
    assert connector.supports_stale_cleanup is False
    assert "https://docs.example.com/guide?foo=ghp%5Fsecret123" not in client.requested


def test_web_connector_rejects_access_key_page_links_without_persisting_them():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <body>
                    <main>Start</main>
                    <a href="/guide?AWSAccessKeyId=privatevalue">Guide</a>
                  </body>
                </html>
            """,
            "https://docs.example.com/guide?AWSAccessKeyId=privatevalue": """
                <html><body><main>Secret guide.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.document_id for document in documents] == [
        "web:https://docs.example.com/start",
    ]
    assert connector.supports_stale_cleanup is False
    assert "https://docs.example.com/guide?AWSAccessKeyId=privatevalue" not in client.requested


def test_web_connector_rejects_compact_sensitive_page_links_without_persisting_them():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <body>
                    <main>Start</main>
                    <a href="/apiKey=privatevalue">API key</a>
                    <a href="/clientSecret=privatevalue">Client secret</a>
                    <a href="/token=privatevalue">Token</a>
                    <a href="/password=privatevalue">Password</a>
                  </body>
                </html>
            """,
            "https://docs.example.com/apiKey=privatevalue": """
                <html><body><main>Secret guide.</main></body></html>
            """,
            "https://docs.example.com/clientSecret=privatevalue": """
                <html><body><main>Secret guide.</main></body></html>
            """,
            "https://docs.example.com/token=privatevalue": """
                <html><body><main>Secret guide.</main></body></html>
            """,
            "https://docs.example.com/password=privatevalue": """
                <html><body><main>Secret guide.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=5, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.document_id for document in documents] == [
        "web:https://docs.example.com/start",
    ]
    assert connector.supports_stale_cleanup is False
    assert "https://docs.example.com/apiKey=privatevalue" not in client.requested
    assert "https://docs.example.com/clientSecret=privatevalue" not in client.requested
    assert "https://docs.example.com/token=privatevalue" not in client.requested
    assert "https://docs.example.com/password=privatevalue" not in client.requested


def test_web_connector_rejects_token_shaped_page_links_without_persisting_them():
    token_paths = (
        "/guide?q=sk-proj-aaaaaaaaaaaaaaaaaaaaaaaa",
        "/guide?q=xoxb-123456789012-123456789012-token",
        "/guide?q=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.signaturefakefake",
        "/guide?q=AIzaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <body>
                    <main>Start</main>
                    <a href="/guide?q=sk-proj-aaaaaaaaaaaaaaaaaaaaaaaa">OpenAI</a>
                    <a href="/guide?q=xoxb-123456789012-123456789012-token">Slack</a>
                    <a href="/guide?q=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.signaturefakefake">JWT</a>
                    <a href="/guide?q=AIzaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">Google</a>
                  </body>
                </html>
            """,
            **{
                f"https://docs.example.com{path}": """
                    <html><body><main>Secret guide.</main></body></html>
                """
                for path in token_paths
            },
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=5, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.document_id for document in documents] == [
        "web:https://docs.example.com/start",
    ]
    assert connector.supports_stale_cleanup is False
    assert all(f"https://docs.example.com{path}" not in client.requested for path in token_paths)


def test_web_connector_keeps_security_topic_documentation_paths():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/api-key-authentication": """
                <html><body><main>API key authentication docs.</main>
                  <a href="/client-secret-rotation">Client secret rotation</a>
                  <a href="/access-key-management">Access key management</a>
                </body></html>
            """,
            "https://docs.example.com/client-secret-rotation": """
                <html><body><main>Client secret rotation docs.</main></body></html>
            """,
            "https://docs.example.com/access-key-management": """
                <html><body><main>Access key management docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/api-key-authentication",),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.document_id for document in documents] == [
        "web:https://docs.example.com/api-key-authentication",
        "web:https://docs.example.com/client-secret-rotation",
        "web:https://docs.example.com/access-key-management",
    ]
    assert connector.supports_stale_cleanup is True


def test_web_connector_rejects_session_cookie_jwt_page_links_without_persisting_them():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <body>
                    <main>Start</main>
                    <a href="/guide?session=privatevalue">Session</a>
                    <a href="/guide?cookie=privatevalue">Cookie</a>
                    <a href="/guide?jwt=privatevalue">JWT</a>
                    <a href="/guide?csrf=privatevalue">CSRF</a>
                    <a href="/guide?q=session=privatevalue">Nested session</a>
                    <a href="/guide?JSESSIONID=privatevalue">JSESSIONID</a>
                    <a href="/session/privatevalue">Session path</a>
                  </body>
                </html>
            """,
            "https://docs.example.com/guide?session=privatevalue": """
                <html><body><main>Secret guide.</main></body></html>
            """,
            "https://docs.example.com/guide?cookie=privatevalue": """
                <html><body><main>Secret guide.</main></body></html>
            """,
            "https://docs.example.com/guide?jwt=privatevalue": """
                <html><body><main>Secret guide.</main></body></html>
            """,
            "https://docs.example.com/guide?csrf=privatevalue": """
                <html><body><main>Secret guide.</main></body></html>
            """,
            "https://docs.example.com/guide?q=session=privatevalue": """
                <html><body><main>Secret guide.</main></body></html>
            """,
            "https://docs.example.com/guide?JSESSIONID=privatevalue": """
                <html><body><main>Secret guide.</main></body></html>
            """,
            "https://docs.example.com/session/privatevalue": """
                <html><body><main>Secret guide.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=5, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.document_id for document in documents] == [
        "web:https://docs.example.com/start",
    ]
    assert connector.supports_stale_cleanup is False
    assert "https://docs.example.com/guide?session=privatevalue" not in client.requested
    assert "https://docs.example.com/guide?cookie=privatevalue" not in client.requested
    assert "https://docs.example.com/guide?jwt=privatevalue" not in client.requested
    assert "https://docs.example.com/guide?csrf=privatevalue" not in client.requested
    assert "https://docs.example.com/guide?q=session=privatevalue" not in client.requested
    assert "https://docs.example.com/guide?JSESSIONID=privatevalue" not in client.requested
    assert "https://docs.example.com/session/privatevalue" not in client.requested


def test_web_connector_rejects_query_value_sensitive_page_links_without_persisting_them():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <body>
                    <main>Start</main>
                    <a href="/guide?foo=token+%3Dprivatevalue">Guide</a>
                  </body>
                </html>
            """,
            "https://docs.example.com/guide?foo=token+%3Dprivatevalue": """
                <html><body><main>Secret guide.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.document_id for document in documents] == [
        "web:https://docs.example.com/start",
    ]
    assert "https://docs.example.com/guide?foo=token+%3Dprivatevalue" not in client.requested
    assert connector.supports_stale_cleanup is False


def test_web_connector_rejects_token_like_canonical_without_persisting_it():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <head>
                        <link rel="canonical" href="/start?foo=%67%68%70%5Fsecret123" />
                      </head>
                      <body><main>Start</main></body>
                    </html>
                """,
            }
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents[0].document_id == "web:https://docs.example.com/start"
    assert documents[0].canonical_url == "https://docs.example.com/start"
    assert connector.supports_stale_cleanup is False


def test_web_connector_rejects_access_key_canonical_without_persisting_it():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <head>
                        <link rel="canonical" href="/start?accessKeyId=privatevalue" />
                      </head>
                      <body><main>Start</main></body>
                    </html>
                """,
            }
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents[0].document_id == "web:https://docs.example.com/start"
    assert documents[0].canonical_url == "https://docs.example.com/start"
    assert connector.supports_stale_cleanup is False


def test_web_connector_rejects_compact_sensitive_canonical_without_persisting_it():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <head>
                        <link rel="canonical" href="/clientSecret=privatevalue" />
                      </head>
                      <body><main>Start</main></body>
                    </html>
                """,
            }
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents[0].document_id == "web:https://docs.example.com/start"
    assert documents[0].canonical_url == "https://docs.example.com/start"
    assert connector.supports_stale_cleanup is False


def test_web_connector_rejects_token_shaped_canonical_without_persisting_it():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <head>
                        <link rel="canonical" href="/start?q=sk-proj-aaaaaaaaaaaaaaaaaaaaaaaa" />
                      </head>
                      <body><main>Start</main></body>
                    </html>
                """,
            }
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents[0].document_id == "web:https://docs.example.com/start"
    assert documents[0].canonical_url == "https://docs.example.com/start"
    assert connector.supports_stale_cleanup is False


def test_web_connector_rejects_session_cookie_jwt_canonical_without_persisting_it():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <head>
                        <link rel="canonical" href="/start?session=privatevalue" />
                      </head>
                      <body><main>Start</main></body>
                    </html>
                """,
            }
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents[0].document_id == "web:https://docs.example.com/start"
    assert documents[0].canonical_url == "https://docs.example.com/start"
    assert connector.supports_stale_cleanup is False


def test_web_connector_rejects_nested_sensitive_canonical_without_persisting_it():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <head>
                        <link rel="canonical" href="/start?q=token=privatevalue" />
                      </head>
                      <body><main>Start</main></body>
                    </html>
                """,
            }
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents[0].document_id == "web:https://docs.example.com/start"
    assert documents[0].canonical_url == "https://docs.example.com/start"
    assert connector.supports_stale_cleanup is False


def test_web_connector_rejects_query_value_sensitive_canonical_without_persisting_it():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <head>
                        <link rel="canonical" href="/start?foo=token+%3Dprivatevalue" />
                      </head>
                      <body><main>Start</main></body>
                    </html>
                """,
            }
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents[0].document_id == "web:https://docs.example.com/start"
    assert documents[0].canonical_url == "https://docs.example.com/start"
    assert connector.supports_stale_cleanup is False


def test_web_connector_rejects_path_segment_sensitive_canonical_without_persisting_it():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <head>
                        <link rel="canonical" href="/session/privatevalue" />
                      </head>
                      <body><main>Start</main></body>
                    </html>
                """,
            }
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents[0].document_id == "web:https://docs.example.com/start"
    assert documents[0].canonical_url == "https://docs.example.com/start"
    assert connector.supports_stale_cleanup is False


def test_web_connector_rejects_media_hinted_canonical_without_persisting_it():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html>
                      <head>
                        <link rel="canonical" href="/manual.pdf" />
                      </head>
                      <body><main>Start</main></body>
                    </html>
                """,
            }
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents[0].document_id == "web:https://docs.example.com/start"
    assert documents[0].canonical_url == "https://docs.example.com/start"
    assert connector.supports_stale_cleanup is False


def test_web_connector_redacts_robots_http_status_query_values():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=RedirectingRobotsHTTPStatusWebHTTP(
            {
                "https://docs.example.com/start": """
                    <html><body><main>Start</main></body></html>
                """,
            }
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(connector.fetch_documents())

    message = str(exc_info.value)
    formatted = "".join(traceback.format_exception(exc_info.value))
    assert "privatevalue" not in message
    assert "session" not in message
    assert "privatevalue" not in formatted
    assert "session" not in formatted


def test_web_connector_redacts_http_status_error_query_values():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide?foo=secret",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HTTPStatusWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
            }
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(connector.fetch_documents())

    message = str(exc_info.value)
    formatted = "".join(traceback.format_exception(exc_info.value))
    assert "secret" not in message
    assert "foo" not in message
    assert "secret" not in formatted
    assert "foo" not in formatted


def test_web_connector_rejects_malformed_seed_credentials_without_leaking_secret():
    with pytest.raises(ValueError) as exc_info:
        WebsiteSourceConnector(
            seed_urls=("https://user:secret@[?token=foo",),
            config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        )

    assert "secret" not in str(exc_info.value)
    assert "user:secret" not in str(exc_info.value)
    assert "foo" not in str(exc_info.value)
    assert "token=foo" not in str(exc_info.value)


def test_web_connector_rejects_malformed_seed_sensitive_query_without_leaking_secret():
    with pytest.raises(ValueError) as exc_info:
        WebsiteSourceConnector(
            seed_urls=("https://[::1?token=secret",),
            config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        )

    assert "secret" not in str(exc_info.value)
    assert "token=secret" not in str(exc_info.value)


def test_web_connector_applies_crawl_delay_between_sitemap_and_page_fetches(
    monkeypatch,
):
    events = []

    class DelayedSitemapHTTP(FakeWebHTTP):
        async def get_response(self, url, headers=None):
            events.append(("fetch", url))
            if url not in self.responses:
                raise RuntimeError(f"missing response for {url}")
            return FetchResponse(url=url, text=self.responses[url])

        async def get_robots_response(self, url, headers=None):
            events.append(("robots", url))
            if url not in self.responses:
                raise RuntimeError(f"missing response for {url}")
            return FetchResponse(url=url, text=self.responses[url])

    async def fake_sleep(delay):
        events.append(("sleep", delay))

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    client = DelayedSitemapHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": (
                f"<urlset xmlns=\"{SITEMAP_NAMESPACE}\">"
                "<url><loc>https://docs.example.com/guide</loc></url>"
                "</urlset>"
            ),
            "https://docs.example.com/guide": (
                "<html><body><main>Guide body.</main></body></html>"
            ),
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0.5),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert len(documents) == 1
    sitemap_fetch = events.index(("fetch", "https://docs.example.com/sitemap.xml"))
    sleep_event = events.index(("sleep", 0.5))
    guide_fetch = events.index(("fetch", "https://docs.example.com/guide"))
    assert sitemap_fetch < sleep_event < guide_fetch


def test_web_connector_counts_skipped_seed_pages_against_page_budget():
    class CountingWebHTTP(FakeWebHTTP):
        def __init__(self, responses):
            super().__init__(responses)
            self.page_requested = []

        async def get_response(self, url, headers=None):
            self.page_requested.append(url)
            if url not in self.responses:
                raise RuntimeError(f"missing response for {url}")
            return FetchResponse(
                url=url,
                text=self.responses[url],
                headers={"Content-Type": "image/png"} if url.endswith(".png") else {},
            )

        async def get_robots_response(self, url, headers=None):
            if url not in self.responses:
                raise RuntimeError(f"missing response for {url}")
            return FetchResponse(url=url, text=self.responses[url])

    client = CountingWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/one.png": "not an html document",
            "https://docs.example.com/two.png": "not an html document",
            "https://docs.example.com/three.png": "not an html document",
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=(
            "https://docs.example.com/one.png",
            "https://docs.example.com/two.png",
            "https://docs.example.com/three.png",
        ),
        config=AppConfig(web_max_pages=1, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert client.page_requested == ["https://docs.example.com/one.png"]
    assert connector.supports_stale_cleanup is False


def test_web_connector_does_not_fetch_new_robots_after_page_budget_exhaustion():
    class MultiOriginHTTP(FakeWebHTTP):
        def __init__(self, responses):
            super().__init__(responses)
            self.robot_requests = []

        async def get_response(self, url, headers=None):
            if url not in self.responses:
                raise RuntimeError(f"missing response for {url}")
            return FetchResponse(url=url, text=self.responses[url])

        async def get_robots_response(self, url, headers=None):
            self.robot_requests.append(url)
            if url not in self.responses:
                raise RuntimeError(f"missing response for {url}")
            return FetchResponse(url=url, text=self.responses[url])

    client = MultiOriginHTTP(
        {
            "https://one.example.com/robots.txt": "",
            "https://one.example.com/start": """
                <html><body><main>One</main></body></html>
            """,
            "https://two.example.com/robots.txt": "",
            "https://two.example.com/start": """
                <html><body><main>Two</main></body></html>
            """,
            "https://three.example.com/robots.txt": "",
            "https://three.example.com/start": """
                <html><body><main>Three</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=(
            "https://one.example.com/start",
            "https://two.example.com/start",
            "https://three.example.com/start",
        ),
        config=AppConfig(web_max_pages=1, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == ["https://one.example.com/start"]
    assert client.robot_requests == ["https://one.example.com/robots.txt"]
    assert connector.supports_stale_cleanup is False


def test_web_connector_skips_late_control_body_for_stale_cleanup():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": (
                    "<html><body><main>"
                    + ("A" * 1100)
                    + "\x00"
                    + "</main></body></html>"
                ),
            },
            {
                "https://docs.example.com/start": {
                    "Content-Type": "text/html",
                },
            },
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_populates_version_id_from_response_validators():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html><body><main>Start</main></body></html>
                """,
            },
            {
                "https://docs.example.com/start": {
                    "ETag": '"docs-v1"',
                    "Last-Modified": "Fri, 22 May 2026 10:00:00 GMT",
                },
            },
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents[0].version_id == '"docs-v1"'
    assert documents[0].updated_at == "Fri, 22 May 2026 10:00:00 GMT"


def test_web_connector_uses_last_modified_as_version_when_etag_is_missing():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html><body><main>Start</main></body></html>
                """,
            },
            {
                "https://docs.example.com/start": {
                    "Last-Modified": "Fri, 22 May 2026 10:00:00 GMT",
                },
            },
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents[0].version_id == "Fri, 22 May 2026 10:00:00 GMT"
    assert documents[0].updated_at == "Fri, 22 May 2026 10:00:00 GMT"


def test_web_connector_drops_secret_like_response_validators_from_metadata():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html><body><main>Start</main></body></html>
                """,
            },
            {
                "https://docs.example.com/start": {
                    "ETag": "token=privatevalue",
                    "Last-Modified": "session=privatevalue",
                },
            },
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert len(documents) == 1
    assert documents[0].version_id == ""
    assert documents[0].updated_at == documents[0].canonical_url
    assert "privatevalue" not in repr(documents[0])


def test_web_connector_drops_auth_like_response_validators_from_metadata():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html><body><main>Start</main></body></html>
                """,
            },
            {
                "https://docs.example.com/start": {
                    "ETag": "Bearer eyJhbGciOiJIUzI1NiJ9.fake.fake",
                    "Last-Modified": "Basic dXNlcjpwYXNz",
                },
            },
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert len(documents) == 1
    assert documents[0].version_id == ""
    assert documents[0].updated_at == documents[0].canonical_url
    assert "Bearer" not in repr(documents[0])
    assert "Basic" not in repr(documents[0])


def test_web_connector_uses_last_modified_when_etag_is_credential_like():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html><body><main>Start</main></body></html>
                """,
            },
            {
                "https://docs.example.com/start": {
                    "ETag": '"sk-proj-aaaaaaaaaaaaaaaaaaaaaaaa"',
                    "Last-Modified": "Fri, 22 May 2026 10:00:00 GMT",
                },
            },
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents[0].version_id == "Fri, 22 May 2026 10:00:00 GMT"
    assert documents[0].updated_at == "Fri, 22 May 2026 10:00:00 GMT"


def test_web_connector_drops_malformed_response_validators_from_metadata():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html><body><main>Start</main></body></html>
                """,
            },
            {
                "https://docs.example.com/start": {
                    "ETag": "not-an-etag",
                    "Last-Modified": "not an http date",
                },
            },
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents[0].version_id == ""
    assert documents[0].updated_at == documents[0].canonical_url


def test_web_connector_rejects_credentialed_redirect_without_leaking_secret():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=CredentialRedirectingWebHTTP(
            {"https://docs.example.com/robots.txt": ""}
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(connector.fetch_documents())

    assert "secret" not in str(exc_info.value)
    assert "user:secret" not in str(exc_info.value)


def test_web_connector_accepts_text_html_with_xml_declaration():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": (
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                "<html><body><main>HTML docs.</main></body></html>"
            ),
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert documents[0].content == "HTML docs."
    assert connector.supports_stale_cleanup is True


def test_web_connector_accepts_doctype_html_fragment():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": (
                "<!DOCTYPE html>"
                "<head><title>Fragment</title></head>"
                "<body><main>Fragment docs.</main></body>"
            ),
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert documents[0].title == "Fragment"
    assert documents[0].content == "Fragment docs."
    assert connector.supports_stale_cleanup is True


def test_web_connector_accepts_doctype_title_fragment():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": (
                "<!DOCTYPE html><title>Fragment</title><p>Fragment docs.</p>"
            ),
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert documents[0].title == "Fragment"
    assert documents[0].content == "Fragment docs."
    assert connector.supports_stale_cleanup is True


@pytest.mark.parametrize(
    ("body", "expected_content"),
    [
        ("<h3>Fragment heading</h3><p>Fragment docs.</p>", "Fragment heading\nFragment docs."),
        ("<ul><li>One</li><li>Two</li></ul>", "One\nTwo"),
        ("<pre>code sample</pre>", "code sample"),
        ("<span>Inline docs</span>", "Inline docs"),
        ("<!DOCTYPE html><table><tr><td>Cell</td></tr></table>", "Cell"),
    ],
)
def test_web_connector_accepts_common_text_html_fragments(body, expected_content):
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": body,
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert documents[0].content == expected_content
    assert connector.supports_stale_cleanup is True


@pytest.mark.parametrize(
    ("body", "expected_content"),
    [
        ("Plain docs text only.", "Plain docs text only."),
        ("<script>ignored()</script><p>Docs after script.</p>", "Docs after script."),
    ],
)
def test_web_connector_accepts_explicit_text_html_readable_bodies(
    body,
    expected_content,
):
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": body,
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert documents[0].content == expected_content
    assert connector.supports_stale_cleanup is True


def test_web_connector_skips_head_only_html_body_for_stale_cleanup():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": (
                "<html><head><title>Only title</title></head></html>"
            ),
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_discovers_prefixed_xhtml_links():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": (
                "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                "<xhtml:html xmlns:xhtml=\"http://www.w3.org/1999/xhtml\">"
                "<xhtml:body><xhtml:main>"
                "<xhtml:a href=\"/guide\">Guide</xhtml:a>"
                "<xhtml:p>Start docs.</xhtml:p>"
                "</xhtml:main></xhtml:body></xhtml:html>"
            ),
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "application/xhtml+xml",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
        "https://docs.example.com/guide",
    ]
    assert connector.supports_stale_cleanup is True


def test_web_connector_disables_stale_cleanup_for_markdown_documents():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/README.md": (
                "# Start\n\nSee [Guide](/guide.md).\n"
            ),
        },
        {
            "https://docs.example.com/README.md": {
                "Content-Type": "text/markdown",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/README.md",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/README.md",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_disables_stale_cleanup_for_markdown_content_type_html_body():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/README.md": (
                "<html><body>Index [Guide](/guide.md)</body></html>"
            ),
        },
        {
            "https://docs.example.com/README.md": {
                "Content-Type": "text/markdown",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/README.md",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/README.md",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_disables_stale_cleanup_for_headerless_markdown_html_body():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/README.md": (
                "<p>Index</p>\n\nSee [Guide](/guide.md).\n"
            ),
        },
        {},
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/README.md",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/README.md",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_resolves_links_against_base_href():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/index.html": """
                <html>
                  <head><base target="_blank" /><base href="/docs/" /></head>
                  <body><main><a href="guide">Guide</a>Index</main></body>
                </html>
            """,
            "https://docs.example.com/docs/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/index.html",
        "https://docs.example.com/docs/guide",
    ]
    assert "https://docs.example.com/guide" not in client.requested
    assert connector.supports_stale_cleanup is True


def test_web_connector_ignores_nested_head_base_for_link_resolution():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/index.html": """
                <html>
                  <head>
                    <template><base href="/wrong/" /></template>
                    <base href="/docs/" />
                  </head>
                  <body><main><a href="guide">Guide</a>Index</main></body>
                </html>
            """,
            "https://docs.example.com/docs/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
            "https://docs.example.com/wrong/guide": """
                <html><body><main>Wrong guide.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/index.html",
        "https://docs.example.com/docs/guide",
    ]
    assert "https://docs.example.com/wrong/guide" not in client.requested
    assert connector.supports_stale_cleanup is False


def test_web_connector_ignores_body_base_for_link_resolution():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/index.html": """
                <html>
                  <body>
                    <main>
                      <base href="/wrong/" />
                      <a href="guide">Guide</a>
                      Index
                    </main>
                  </body>
                </html>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
            "https://docs.example.com/wrong/guide": """
                <html><body><main>Wrong guide.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/index.html",
        "https://docs.example.com/guide",
    ]
    assert "https://docs.example.com/wrong/guide" not in client.requested
    assert connector.supports_stale_cleanup is False


def test_web_connector_ignores_body_nested_head_base_for_link_resolution():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/index.html": """
                <html>
                  <body>
                    <main>
                      <head><base href="/wrong/" /></head>
                      <a href="guide">Guide</a>
                      Index
                    </main>
                  </body>
                </html>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
            "https://docs.example.com/wrong/guide": """
                <html><body><main>Wrong guide.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/index.html",
        "https://docs.example.com/guide",
    ]
    assert "https://docs.example.com/wrong/guide" not in client.requested
    assert connector.supports_stale_cleanup is False


def test_web_connector_ignores_fragment_nested_head_base_for_link_resolution():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/index.html": """
                <div>
                  <head><base href="/wrong/" /></head>
                  <a href="guide">Guide</a>
                  Index
                </div>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
            "https://docs.example.com/wrong/guide": """
                <html><body><main>Wrong guide.</main></body></html>
            """,
        },
        {
            "https://docs.example.com/index.html": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/index.html",
        "https://docs.example.com/guide",
    ]
    assert "https://docs.example.com/wrong/guide" not in client.requested
    assert connector.supports_stale_cleanup is False


def test_web_connector_ignores_root_level_late_head_base_for_link_resolution():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/index.html": """
                <p>Index</p>
                <head><base href="/wrong/" /></head>
                <a href="guide">Guide</a>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
            "https://docs.example.com/wrong/guide": """
                <html><body><main>Wrong guide.</main></body></html>
            """,
        },
        {
            "https://docs.example.com/index.html": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/index.html",
        "https://docs.example.com/guide",
    ]
    assert "https://docs.example.com/wrong/guide" not in client.requested
    assert connector.supports_stale_cleanup is False


def test_web_connector_ignores_text_preceded_head_base_for_link_resolution():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/index.html": """
                <!DOCTYPE html>
                Intro text
                <head><base href="/wrong/" /></head>
                <a href="guide">Guide</a>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
            "https://docs.example.com/wrong/guide": """
                <html><body><main>Wrong guide.</main></body></html>
            """,
        },
        {
            "https://docs.example.com/index.html": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/index.html",
        "https://docs.example.com/guide",
    ]
    assert "https://docs.example.com/wrong/guide" not in client.requested
    assert connector.supports_stale_cleanup is False


def test_web_connector_ignores_content_preceded_html_head_base():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/index.html": """
                <p>Intro</p>
                <html>
                  <head><base href="/wrong/" /></head>
                  <body><main><a href="guide">Guide</a>Index</main></body>
                </html>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
            "https://docs.example.com/wrong/guide": """
                <html><body><main>Wrong guide.</main></body></html>
            """,
            "https://docs.example.com/robots.txt": "",
        },
        {
            "https://docs.example.com/index.html": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/index.html",
        "https://docs.example.com/guide",
    ]
    assert "https://docs.example.com/wrong/guide" not in client.requested
    assert connector.supports_stale_cleanup is False


@pytest.mark.parametrize(
    "head_prefix",
    [
        "<title>Intro</title>",
        "<meta name=\"description\" content=\"Intro\" />",
        "<noscript><a href=\"guide\">Guide</a></noscript>",
    ],
)
def test_web_connector_ignores_metadata_preceded_head_base_for_link_resolution(
    head_prefix,
):
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/index.html": f"""
                {head_prefix}
                <head><base href="/wrong/" /></head>
                <a href="guide">Guide</a>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
            "https://docs.example.com/wrong/guide": """
                <html><body><main>Wrong guide.</main></body></html>
            """,
        },
        {
            "https://docs.example.com/index.html": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/index.html",
        "https://docs.example.com/guide",
    ]
    assert "https://docs.example.com/wrong/guide" not in client.requested
    assert connector.supports_stale_cleanup is False


def test_web_connector_resolves_canonical_against_base_href():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/index.html": """
                <html>
                  <head>
                    <base href="/docs/" />
                    <link rel="canonical" href="guide" />
                  </head>
                  <body><main>Guide docs.</main></body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/index.html",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/docs/guide",
    ]
    assert connector.supports_stale_cleanup is True


def test_web_connector_ignores_body_canonical_href():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <body>
                    <main>
                      <link rel="canonical" href="/wrong" />
                      Start docs.
                    </main>
                  </body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_ignores_nested_head_canonical_href():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <head>
                    <template><link rel="canonical" href="/wrong" /></template>
                  </head>
                  <body><main>Start docs.</main></body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_ignores_body_nested_head_canonical_href():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <body>
                    <main>
                      <head><link rel="canonical" href="/wrong" /></head>
                      Start docs.
                    </main>
                  </body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_ignores_late_document_head_canonical_href():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <body><main>Start docs.</main></body>
                  <head><link rel="canonical" href="/wrong" /></head>
                </html>
            """,
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_ignores_html_content_preceded_head_canonical_href():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <main>Start docs.</main>
                  <head><link rel="canonical" href="/wrong" /></head>
                </html>
            """,
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_ignores_non_fetchable_page_anchors_for_cleanup():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <body><main>
                    <a href="mailto:docs@example.com">Email</a>
                    <a href="tel:+15555550100">Call</a>
                    <a href="javascript:void(0)">JS</a>
                    <a href="/guide">Guide</a>
                    Start
                  </main></body>
                </html>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
        "https://docs.example.com/guide",
    ]
    assert connector.supports_stale_cleanup is True


def test_web_connector_ignores_credentialed_page_links_for_cleanup():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html><body><main>
                  <a href="https://user:secret@docs.example.com/private">Private</a>
                  <p>Start docs.</p>
                </main></body></html>
            """,
            "https://user:secret@docs.example.com/private": """
                <html><body><main>Private docs.</main></body></html>
            """,
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert "https://user:secret@docs.example.com/private" not in client.requested
    assert connector.supports_stale_cleanup is False


def test_web_connector_ignores_sensitive_query_page_links_for_cleanup():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html><body><main>
                  <a href="https://docs.example.com/private?token=secret">Private</a>
                  <p>Start docs.</p>
                </main></body></html>
            """,
            "https://docs.example.com/private?token=secret": """
                <html><body><main>Private docs.</main></body></html>
            """,
        },
        {
            "https://docs.example.com/start": {
                "Content-Type": "text/html",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert "https://docs.example.com/private?token=secret" not in client.requested
    assert connector.supports_stale_cleanup is False


def test_web_connector_disables_stale_cleanup_for_markdown_with_html_words():
    client = HeaderWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/README.md": (
                "# Start\n\nThis doc mentions <body> tags. See [Guide](/guide.md).\n"
            ),
        },
        {
            "https://docs.example.com/README.md": {
                "Content-Type": "text/markdown",
            },
        },
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/README.md",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/README.md",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_preserves_ipv6_brackets_for_origin_urls():
    client = FakeWebHTTP(
        {
            "http://[::1]:8000/robots.txt": "",
            "http://[::1]:8000/start": """
                <html><body><main>IPv6 docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("http://[::1]:8000/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == ["http://[::1]:8000/start"]
    assert "http://[::1]:8000/robots.txt" in client.requested


def test_web_connector_skips_headerless_media_by_url_extension():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/guide.pdf": "%PDF-1.7 binary-ish text",
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide.pdf",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_skips_headerless_svg_media():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/logo.svg": """
                <svg><text>Logo asset</text></svg>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/logo.svg",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_skips_headerless_query_media_by_url_extension():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/logo.png?v=1": "PNG binary-ish text",
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/logo.png?v=1",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_skips_headerless_percent_encoded_media_extension():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/guide%2Epdf": "%PDF-1.7 binary-ish text",
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide%2Epdf",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_skips_headerless_extensionless_binary_body():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/download?file=guide.pdf": (
                "%PDF-1.7 binary-ish text"
            ),
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/download?file=guide.pdf",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_skips_headerless_media_redirect_to_extensionless_asset():
    client = MediaRedirectingWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/asset": "PNG binary-ish text",
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/logo.png",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_disables_stale_cleanup_for_media_hinted_redirect_to_html():
    client = MediaRedirectingWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/asset": """
                <html><body><main>Asset landing page.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/logo.png",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.document_id for document in documents] == [
        "web:https://docs.example.com/asset",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_bounds_over_budget_sitemap_frontier():
    sitemap_entries = "\n".join(
        f"<url><loc>https://docs.example.com/page-{index}</loc></url>"
        for index in range(50)
    )
    client = FakeWebHTTP(
        {
            "https://docs.example.com/sitemap.xml": f"<urlset>{sitemap_entries}</urlset>",
            "https://docs.example.com/page-0": """
                <html><body><main>Page zero.</main></body></html>
            """,
            "https://docs.example.com/page-1": """
                <html><body><main>Page one.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )
    allowed_calls = []

    async def bounded_allowed_by_robots(url):
        allowed_calls.append(url)
        if len(allowed_calls) > 15:
            raise AssertionError("frontier processed too many over-budget candidates")
        return True

    connector.fetcher._allowed_by_robots = bounded_allowed_by_robots

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/page-0",
        "https://docs.example.com/page-1",
    ]
    assert connector.supports_stale_cleanup is False
    assert len(allowed_calls) <= 15


def test_web_connector_external_links_do_not_trigger_frontier_overflow():
    external_links = "\n".join(
        f'<a href="https://external.example.com/page-{index}">External</a>'
        for index in range(200)
    )
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": f"""
                <html><body><main>
                  <p>Start docs.</p>
                  {external_links}
                  <a href="/guide">Guide</a>
                </main></body></html>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
        "https://docs.example.com/guide",
    ]
    assert connector.supports_stale_cleanup is True


def test_web_connector_ignores_invalid_port_page_links():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html><body><main>
                  <p>Start docs.</p>
                  <a href="https://docs.example.com:99999/bad">Bad</a>
                  <a href="/guide">Guide</a>
                </main></body></html>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
        "https://docs.example.com/guide",
    ]
    assert connector.supports_stale_cleanup is False
    assert "https://docs.example.com:99999/bad" not in client.requested


def test_web_connector_ignores_invalid_port_sitemap_entries():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": """
                <urlset>
                  <url><loc>https://docs.example.com:99999/bad</loc></url>
                  <url><loc>https://docs.example.com/guide</loc></url>
                </urlset>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]
    assert connector.supports_stale_cleanup is False
    assert "https://docs.example.com:99999/bad" not in client.requested


def test_web_connector_rejects_invalid_port_redirect_location_as_cross_origin():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=InvalidPortRedirectingWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
            }
        ),
    )

    with pytest.raises(RuntimeError, match="Blocked cross-origin redirect"):
        asyncio.run(connector.fetch_documents())


def test_web_connector_skips_oversized_response_body():
    client = SkippedBodyHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": "<html><body><main>Too large.</main></body></html>",
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_fails_closed_when_robots_body_is_skipped():
    client = SkippedRobotsHTTP(
        {
            "https://docs.example.com/private/secret": """
                <html><body><main>Secret docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/private/secret",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    with pytest.raises(RuntimeError, match="robots.txt body was skipped"):
        asyncio.run(connector.fetch_documents())

    assert "https://docs.example.com/private/secret" not in client.requested


def test_robots_parser_honors_utf8_bom_prefixed_user_agent():
    rules = RobotsRules.parse(
        "\ufeffUser-agent: *\n"
        "Disallow: /private\n"
    )

    assert rules.allows("https://docs.example.com/public") is True
    assert rules.allows("https://docs.example.com/private/secret") is False


def test_web_http_client_skips_body_when_content_length_exceeds_limit():
    def handler(request):
        return httpx.Response(
            200,
            headers={
                "Content-Length": "2048",
                "Content-Type": "application/pdf",
            },
            stream=ExplodingByteStream(),
            request=request,
        )

    client = WebsiteHTTPClient(
        timeout=1,
        max_response_bytes=10,
        transport=httpx.MockTransport(handler),
    )

    response = asyncio.run(client.get_response("https://docs.example.com/guide.pdf"))

    assert response.body_skipped is True
    assert response.text == ""


def test_web_http_client_reads_robots_body_regardless_of_content_type():
    def handler(request):
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/octet-stream",
            },
            content=b"User-agent: *\nDisallow: /private\n",
            request=request,
        )

    client = WebsiteHTTPClient(
        timeout=1,
        max_response_bytes=100,
        transport=httpx.MockTransport(handler),
    )

    page_response = asyncio.run(client.get_response("https://docs.example.com/robots.txt"))
    robots_response = asyncio.run(
        client.get_robots_response("https://docs.example.com/robots.txt")
    )

    assert page_response.text == ""
    assert "Disallow: /private" in robots_response.text
    assert robots_response.body_skipped is False


def test_web_connector_ignores_malformed_bracket_page_links():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html><body><main>
                  <p>Start docs.</p>
                  <a href="https://[::1">Bad</a>
                  <a href="/guide">Guide</a>
                </main></body></html>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
        "https://docs.example.com/guide",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_ignores_malformed_bracket_sitemap_entries():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": """
                <urlset>
                  <url><loc>https://[::1</loc></url>
                  <url><loc>https://docs.example.com/guide</loc></url>
                </urlset>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_disables_stale_cleanup_for_empty_sitemap_loc():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": """
                <urlset>
                  <url><loc> </loc></url>
                  <url><loc>https://docs.example.com/guide</loc></url>
                </urlset>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_disables_stale_cleanup_for_wrong_namespace_sitemap_child():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": """
                <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <url xmlns="">
                    <loc>https://docs.example.com/guide</loc>
                  </url>
                </urlset>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert "https://docs.example.com/guide" not in client.requested
    assert connector.supports_stale_cleanup is False


def test_web_connector_disables_stale_cleanup_for_non_http_sitemap_loc():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": """
                <urlset>
                  <url><loc>ftp://docs.example.com/guide</loc></url>
                  <url><loc>https://docs.example.com/guide</loc></url>
                </urlset>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_disables_stale_cleanup_for_missing_sitemap_loc():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": """
                <urlset>
                  <url><lastmod>2026-05-22</lastmod></url>
                  <url><loc>https://docs.example.com/guide</loc></url>
                </urlset>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_ignores_malformed_bracket_canonical_url():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <head><link rel="canonical" href="https://[::1" /></head>
                  <body><main>Start docs.</main></body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_disables_stale_cleanup_for_invalid_port_canonical_url():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <head>
                    <link rel="canonical" href="https://docs.example.com:99999/bad" />
                  </head>
                  <body><main>Start docs.</main></body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert connector.supports_stale_cleanup is False
    assert "https://docs.example.com:99999/bad" not in client.requested


def test_web_connector_disables_stale_cleanup_for_empty_canonical_href():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <head><link rel="canonical" href="" /></head>
                  <body><main>Start docs.</main></body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_disables_stale_cleanup_for_non_http_canonical_href():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <head><link rel="canonical" href="ftp://docs.example.com/guide" /></head>
                  <body><main>Start docs.</main></body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert connector.supports_stale_cleanup is False


def test_web_connector_rejects_malformed_bracket_redirect_location_as_cross_origin():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=MalformedHostRedirectingWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
            }
        ),
    )

    with pytest.raises(RuntimeError, match="Blocked cross-origin redirect"):
        asyncio.run(connector.fetch_documents())


def test_web_connector_disallowed_links_do_not_trigger_frontier_overflow():
    disallowed_links = "\n".join(
        f'<a href="/private/page-{index}">Private</a>'
        for index in range(200)
    )
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": (
                "User-agent: *\nDisallow: /private\n"
            ),
            "https://docs.example.com/start": f"""
                <html><body><main>
                  <p>Start docs.</p>
                  {disallowed_links}
                  <a href="/guide">Guide</a>
                </main></body></html>
            """,
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
        "https://docs.example.com/guide",
    ]
    assert connector.supports_stale_cleanup is False
    assert "https://docs.example.com/private/page-0" not in client.requested


def test_web_connector_bounds_same_origin_crawl_and_ignores_external_links():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <head><title>Start</title></head>
                  <body>
                    <main>
                      <p>Start docs.</p>
                      <a href="/guide">Guide</a>
                      <a href="https://other.example.com/out">External</a>
                    </main>
                  </body>
                </html>
            """,
            "https://docs.example.com/guide": """
                <html>
                  <head><title>Guide</title></head>
                  <body><main><p>Bounded linked docs.</p></main></body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
        "https://docs.example.com/guide",
    ]
    assert "https://other.example.com/out" not in client.requested


def test_web_connector_collects_nav_links_without_indexing_nav_text():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <head><title>Start</title></head>
                  <body>
                    <nav><a href="/guide">Guide Link</a> Navigation Text</nav>
                    <main><p>Start docs.</p></main>
                  </body>
                </html>
            """,
            "https://docs.example.com/guide": """
                <html>
                  <head><title>Guide</title></head>
                  <body><main><p>Guide body.</p></main></body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
        "https://docs.example.com/guide",
    ]
    assert "Navigation Text" not in documents[0].content


def test_web_connector_fails_malformed_sitemap_so_sync_can_skip_tombstones():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=5, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/sitemap.xml": "<urlset><url>",
            }
        ),
    )

    with pytest.raises(ValueError, match="Invalid sitemap"):
        asyncio.run(connector.fetch_documents())


def test_web_connector_fails_robots_fetch_errors_so_sync_can_skip_tombstones():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FailingRobotsHTTP(
            {
                "https://docs.example.com/start": """
                    <html><body><main><p>Start docs.</p></main></body></html>
                """,
            }
        ),
    )

    with pytest.raises(RuntimeError, match="robots unavailable"):
        asyncio.run(connector.fetch_documents())


def test_web_connector_uses_configured_user_agent_for_robots_matching():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": (
                "User-agent: OtherBot\n"
                "User-agent: MyDocsBot\n"
                "Disallow: /blocked\n"
                "User-agent: *\nDisallow:\n"
            ),
            "https://docs.example.com/blocked": """
                <html><body><main>Blocked docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/blocked",),
        config=AppConfig(
            web_max_pages=2,
            web_crawl_delay_seconds=0,
            web_user_agent="MyDocsBot/1.0",
        ),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert "https://docs.example.com/blocked" not in client.requested


def test_web_connector_prefers_specific_robots_group_over_wildcard():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": (
                "User-agent: ContextWikiBot\nDisallow:\n"
                "User-agent: *\nDisallow: /public\n"
            ),
            "https://docs.example.com/public": """
                <html><body><main>Specific bot allowed.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/public",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert len(documents) == 1
    assert documents[0].path == "https://docs.example.com/public"
    assert "Specific bot allowed." in documents[0].content


def test_web_connector_treats_blank_line_as_robots_group_boundary():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": (
                "User-agent: ContextWikiBot\n"
                "\n"
                "User-agent: OtherBot\n"
                "Disallow: /\n"
            ),
            "https://docs.example.com/guide": """
                <html><body><main>Guide docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]
    assert "Guide docs." in documents[0].content


def test_web_connector_ignores_comment_only_lines_inside_robots_group():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": (
                "User-agent: ContextWikiBot\n"
                "# keep this group active\n"
                "Disallow: /private\n"
            ),
            "https://docs.example.com/private/secret": """
                <html><body><main>Secret docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/private/secret",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert "https://docs.example.com/private/secret" not in client.requested


def test_web_connector_honors_robots_allow_precedence():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": (
                "User-agent: *\nDisallow: /\nAllow: /docs\n"
            ),
            "https://docs.example.com/docs": """
                <html><body><main>Allowed docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/docs",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert len(documents) == 1
    assert documents[0].path == "https://docs.example.com/docs"
    assert "Allowed docs." in documents[0].content


def test_web_connector_honors_robots_wildcard_disallow_before_fetch():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": (
                "User-agent: *\nDisallow: /private/*\n"
            ),
            "https://docs.example.com/private/secret": """
                <html><body><main>Secret docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/private/secret",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    assert asyncio.run(connector.fetch_documents()) == []
    assert "https://docs.example.com/private/secret" not in client.requested


def test_web_connector_honors_robots_end_anchor_disallow_before_fetch():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": (
                "User-agent: *\nDisallow: /*.pdf$\n"
            ),
            "https://docs.example.com/guide.pdf": "pdf text",
            "https://docs.example.com/guide.pdf?download=1": """
                <html><body><main>Download page.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=(
            "https://docs.example.com/guide.pdf",
            "https://docs.example.com/guide.pdf?download=1",
        ),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide.pdf?download=1",
    ]
    assert "https://docs.example.com/guide.pdf" not in client.requested


def test_web_connector_applies_redirected_robots_rules_before_fetch():
    client = RedirectingRobotsHTTP(
        {
            "https://docs.example.com/robots-rules": (
                "User-agent: *\nDisallow: /private\n"
            ),
            "https://docs.example.com/private/secret": """
                <html><body><main>Secret docs.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/private/secret",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    assert asyncio.run(connector.fetch_documents()) == []
    assert "https://docs.example.com/robots-rules" in client.requested
    assert "https://docs.example.com/private/secret" not in client.requested


def test_web_connector_rejects_cross_origin_redirects():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=RedirectingWebHTTP({"https://docs.example.com/robots.txt": ""}),
    )

    with pytest.raises(RuntimeError, match="cross-origin redirect"):
        asyncio.run(connector.fetch_documents())


def test_web_connector_skips_same_origin_redirects_to_robots_disallowed_paths():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=SameOriginRedirectingWebHTTP(
            {"https://docs.example.com/robots.txt": "User-agent: *\nDisallow: /private\n"}
        ),
    )

    assert asyncio.run(connector.fetch_documents()) == []
    assert connector.supports_stale_cleanup is False
    assert "https://docs.example.com/private/secret" not in connector.fetcher.http_client.requested


def test_web_connector_promotes_deferred_candidate_after_disallowed_redirect():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=DeferredRedirectingWebHTTP(
            {
                "https://docs.example.com/robots.txt": (
                    "User-agent: *\nDisallow: /private\n"
                ),
                "https://docs.example.com/start": """
                    <html><body><main>Start</main>
                      <a href="/alias">Alias</a>
                      <a href="/two">Two</a>
                      <a href="/three">Three</a>
                    </body></html>
                """,
                "https://docs.example.com/two": """
                    <html><body><main>Two</main></body></html>
                """,
                "https://docs.example.com/three": """
                    <html><body><main>Three</main></body></html>
                """,
            }
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
        "https://docs.example.com/two",
        "https://docs.example.com/three",
    ]
    assert connector.supports_stale_cleanup is False
    assert "https://docs.example.com/private/secret" not in connector.fetcher.http_client.requested


def test_web_connector_rejects_scheme_downgrade_redirects():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=SchemeDowngradeRedirectingWebHTTP(
            {"https://docs.example.com/robots.txt": ""}
        ),
    )

    with pytest.raises(RuntimeError, match="cross-origin redirect"):
        asyncio.run(connector.fetch_documents())

    assert "http://docs.example.com/start" not in connector.fetcher.http_client.requested


def test_web_connector_preserves_trailing_slash_redirect_fetch_url():
    client = TrailingSlashRedirectingWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/guide/": """
                <html><body><main>Guide body.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]
    assert "Guide body." in documents[0].content
    assert "https://docs.example.com/guide/" in client.requested


def test_web_connector_ignores_cross_scheme_links():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <body>
                    <main>
                      <p>Start docs.</p>
                      <a href="http://docs.example.com/guide">HTTP downgrade</a>
                    </main>
                  </body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert "http://docs.example.com/guide" not in client.requested


def test_web_connector_does_not_refetch_redirect_final_url_from_self_link():
    client = AliasRedirectingWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/guide": """
                <html>
                  <body>
                    <main>
                      <a href="/guide">Self</a>
                      <a href="/other">Other</a>
                      <p>Guide.</p>
                    </main>
                  </body>
                </html>
            """,
            "https://docs.example.com/other": """
                <html><body><main>Other.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
        "https://docs.example.com/other",
    ]
    assert client.requested.count("https://docs.example.com/guide") == 1


def test_web_connector_visited_seed_aliases_do_not_consume_link_budget():
    client = AliasRedirectingWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/guide": """
                <html>
                  <body>
                    <main>
                      <a href="/other">Other</a>
                      <p>Guide.</p>
                    </main>
                  </body>
                </html>
            """,
            "https://docs.example.com/other": """
                <html><body><main>Other.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start", "https://docs.example.com/guide"),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
        "https://docs.example.com/other",
    ]
    assert client.requested.count("https://docs.example.com/guide") == 1


def test_web_connector_duplicate_redirect_seeds_do_not_consume_link_budget():
    client = AliasRedirectingWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/guide": """
                <html>
                  <body>
                    <main>
                      <a href="/other">Other</a>
                      <p>Guide.</p>
                    </main>
                  </body>
                </html>
            """,
            "https://docs.example.com/other": """
                <html><body><main>Other.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start", "https://docs.example.com/start"),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
        "https://docs.example.com/other",
    ]
    assert client.requested.count("https://docs.example.com/start") == 1


def test_web_connector_distinct_redirect_aliases_do_not_consume_link_budget():
    client = DistinctAliasRedirectingWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/guide": """
                <html>
                  <body>
                    <main>
                      <a href="/other">Other</a>
                      <p>Guide.</p>
                    </main>
                  </body>
                </html>
            """,
            "https://docs.example.com/other": """
                <html><body><main>Other.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start", "https://docs.example.com/alias"),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
        "https://docs.example.com/other",
    ]
    assert connector.supports_stale_cleanup is True
    assert client.requested.count("https://docs.example.com/start") == 1
    assert client.requested.count("https://docs.example.com/alias") == 1


def test_web_connector_bounds_fetch_attempts_for_many_redirect_aliases():
    alias_links = "\n".join(
        f'<a href="/alias-{index}">Alias {index}</a>'
        for index in range(10)
    )
    client = ManyAliasRedirectingWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": f"""
                <html><body><main>{alias_links}<p>Start.</p></main></body></html>
            """,
            "https://docs.example.com/guide": """
                <html><body><main><p>Guide.</p></main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())
    non_robots_requests = [
        url
        for url in client.requested
        if not url.endswith("/robots.txt")
    ]

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
        "https://docs.example.com/guide",
    ]
    assert len(non_robots_requests) <= 3
    assert connector.supports_stale_cleanup is False


def test_web_connector_bounds_fetch_attempts_across_redirect_hops():
    client = MultiHopRedirectingWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html><body><main><a href="/alias-0">Alias</a><p>Start.</p></main></body></html>
            """,
            "https://docs.example.com/guide": """
                <html><body><main><p>Guide.</p></main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())
    non_robots_requests = [
        url
        for url in client.requested
        if not url.endswith("/robots.txt")
    ]

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
    ]
    assert len(non_robots_requests) <= 5
    assert connector.supports_stale_cleanup is False


def test_web_connector_case_variant_self_links_do_not_consume_link_budget():
    responses = {
        "https://docs.example.com/robots.txt": "",
        "https://Docs.example.com/start": """
            <html>
              <body>
                <main>
                  <a href="https://docs.example.com/start">Same page</a>
                  <a href="https://docs.example.com/other">Other</a>
                  <p>Start.</p>
                </main>
              </body>
            </html>
        """,
        "https://docs.example.com/other": """
            <html><body><main>Other.</main></body></html>
        """,
    }
    client = FakeWebHTTP(responses)
    connector = WebsiteSourceConnector(
        seed_urls=("https://Docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/start",
        "https://docs.example.com/other",
    ]
    assert "https://docs.example.com/start" not in client.requested


def test_web_connector_fails_well_formed_non_sitemap_xml():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/feed.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/feed.xml": (
                    "<rss><channel><title>Feed</title></channel></rss>"
                ),
            }
        ),
    )

    with pytest.raises(ValueError, match="Invalid sitemap"):
        asyncio.run(connector.fetch_documents())


@pytest.mark.parametrize("root_name", ["urlset-doc", "sitemapindex-doc"])
def test_web_connector_does_not_treat_html_prefixed_sitemap_roots_as_sitemaps(
    root_name,
):
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/custom",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=HeaderWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/custom": (
                    f"<{root_name}><p>Custom docs.</p></{root_name}>"
                ),
            },
            {
                "https://docs.example.com/custom": {
                    "Content-Type": "text/html",
                },
            },
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert len(documents) == 1
    assert documents[0].path == "https://docs.example.com/custom"
    assert documents[0].content == "Custom docs."
    assert connector.supports_stale_cleanup is True


def test_web_connector_does_not_treat_html_sitemap_named_pages_as_sitemaps():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap-guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/sitemap-guide": """
                    <html><body><main>How to read a sitemap.</main></body></html>
                """,
            }
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert len(documents) == 1
    assert documents[0].path == "https://docs.example.com/sitemap-guide"
    assert "How to read a sitemap." in documents[0].content


def test_web_connector_caps_sitemap_expansion_by_fetch_budget():
    responses = {
        "https://docs.example.com/robots.txt": "",
        "https://docs.example.com/sitemap.xml": """
            <urlset>
              <url><loc>https://docs.example.com/one</loc></url>
              <url><loc>https://docs.example.com/two</loc></url>
              <url><loc>https://docs.example.com/three</loc></url>
              <url><loc>https://docs.example.com/four</loc></url>
            </urlset>
        """,
        "https://docs.example.com/one": "<html><body><main>One</main></body></html>",
        "https://docs.example.com/two": "<html><body><main>Two</main></body></html>",
        "https://docs.example.com/three": "<html><body><main>Three</main></body></html>",
        "https://docs.example.com/four": "<html><body><main>Four</main></body></html>",
    }
    client = FakeWebHTTP(responses)
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=3, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/one",
        "https://docs.example.com/two",
        "https://docs.example.com/three",
    ]
    assert connector.supports_stale_cleanup is False
    assert "https://docs.example.com/four" not in client.requested


def test_web_connector_disallowed_sitemap_urls_do_not_consume_fetch_budget():
    responses = {
        "https://docs.example.com/robots.txt": "User-agent: *\nDisallow: /private\n",
        "https://docs.example.com/sitemap.xml": """
            <urlset>
              <url><loc>https://docs.example.com/private/one</loc></url>
              <url><loc>https://docs.example.com/private/two</loc></url>
              <url><loc>https://docs.example.com/guide</loc></url>
            </urlset>
        """,
        "https://docs.example.com/private/one": "<html><main>Private one</main></html>",
        "https://docs.example.com/private/two": "<html><main>Private two</main></html>",
        "https://docs.example.com/guide": "<html><body><main>Guide</main></body></html>",
    }
    client = FakeWebHTTP(responses)
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]
    assert "https://docs.example.com/private/one" not in client.requested
    assert "https://docs.example.com/private/two" not in client.requested


def test_web_connector_disallowed_seed_urls_do_not_consume_sitemap_fetch_budget():
    responses = {
        "https://docs.example.com/robots.txt": "User-agent: *\nDisallow: /private\n",
        "https://docs.example.com/sitemap.xml": """
            <urlset>
              <url><loc>https://docs.example.com/guide</loc></url>
            </urlset>
        """,
        "https://docs.example.com/private": "<html><main>Private</main></html>",
        "https://docs.example.com/guide": "<html><body><main>Guide</main></body></html>",
    }
    client = FakeWebHTTP(responses)
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml", "https://docs.example.com/private"),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]
    assert "https://docs.example.com/private" not in client.requested


def test_web_connector_disables_stale_cleanup_for_self_sitemap_loc():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/sitemap.xml": """
                <urlset>
                  <url><loc>#self</loc></url>
                </urlset>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_treats_case_variant_hosts_as_same_origin():
    responses = {
        "https://docs.example.com/robots.txt": "",
        "https://Docs.example.com/sitemap.xml": """
            <urlset>
              <url><loc>https://docs.example.com/guide</loc></url>
            </urlset>
        """,
        "https://docs.example.com/guide": "<html><body><main>Guide</main></body></html>",
    }
    client = FakeWebHTTP(responses)
    connector = WebsiteSourceConnector(
        seed_urls=("https://Docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]


def test_web_connector_treats_default_port_as_same_origin():
    responses = {
        "https://docs.example.com/robots.txt": "",
        "https://docs.example.com/sitemap.xml": """
            <urlset>
              <url><loc>https://docs.example.com:443/guide</loc></url>
            </urlset>
        """,
        "https://docs.example.com:443/guide": """
            <html><body><main>Guide with port.</main></body></html>
        """,
    }
    client = FakeWebHTTP(responses)
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/sitemap.xml",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/guide",
    ]


@pytest.mark.parametrize(
    "linked_url",
    [
        "https://Docs.example.com/guide",
        "https://docs.example.com:443/guide",
    ],
)
def test_web_connector_replaces_alias_with_equivalent_canonical_fetch(linked_url):
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": f"""
                <html>
                  <head>
                    <title>Start</title>
                    <link rel="canonical" href="https://docs.example.com/guide" />
                  </head>
                  <body><main><a href="{linked_url}">Guide</a><p>Alias body.</p></main></body>
                </html>
            """,
            linked_url: """
                <html>
                  <head><title>Guide</title></head>
                  <body><main><p>Canonical body.</p></main></body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.document_id for document in documents] == [
        "web:https://docs.example.com/guide",
    ]
    assert documents[0].title == "Guide"
    assert "Canonical body." in documents[0].content
    assert "Alias body." not in documents[0].content
    assert connector.supports_stale_cleanup is True


def test_web_connector_ignores_cross_origin_canonical_href():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <head>
                    <title>Start</title>
                    <link rel="canonical" href="https://other.example.com/start" />
                  </head>
                  <body><main><p>Same origin body.</p></main></body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.document_id for document in documents] == [
        "web:https://docs.example.com/start",
    ]
    assert documents[0].canonical_url == "https://docs.example.com/start"
    assert documents[0].path == "https://docs.example.com/start"


def test_web_connector_ignores_credentialed_canonical_href_for_cleanup():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <head>
                    <title>Start</title>
                    <link rel="canonical" href="https://user:secret@docs.example.com/private" />
                  </head>
                  <body><main><p>Same origin body.</p></main></body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.document_id for document in documents] == [
        "web:https://docs.example.com/start",
    ]
    assert documents[0].canonical_url == "https://docs.example.com/start"
    assert connector.supports_stale_cleanup is False


def test_web_connector_ignores_sensitive_query_canonical_href_for_cleanup():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <head>
                    <title>Start</title>
                    <link rel="canonical" href="https://docs.example.com/private?token=secret" />
                  </head>
                  <body><main><p>Same origin body.</p></main></body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.document_id for document in documents] == [
        "web:https://docs.example.com/start",
    ]
    assert documents[0].canonical_url == "https://docs.example.com/start"
    assert connector.supports_stale_cleanup is False


def test_web_connector_ignores_robots_disallowed_canonical_href():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": (
                "User-agent: *\nDisallow: /private\n"
            ),
            "https://docs.example.com/alias": """
                <html>
                  <head>
                    <title>Alias</title>
                    <link rel="canonical" href="https://docs.example.com/private/secret" />
                  </head>
                  <body><main><p>Alias body.</p></main></body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/alias",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.document_id for document in documents] == [
        "web:https://docs.example.com/alias",
    ]
    assert documents[0].canonical_url == "https://docs.example.com/alias"
    assert connector.supports_stale_cleanup is False
    assert "https://docs.example.com/private/secret" not in client.requested


def test_web_connector_preserves_query_values_ending_with_slash():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/search?q=api/": """
                <html><body><main>Search result.</main></body></html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/search?q=api/",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "https://docs.example.com/search?q=api/",
    ]
    assert client.requested.count("https://docs.example.com/search?q=api/") == 1


def test_web_connector_dedupes_duplicate_canonical_documents():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/start": """
                <html>
                  <head>
                    <title>Start</title>
                    <link rel="canonical" href="https://docs.example.com/guide" />
                  </head>
                  <body><main><a href="/guide">Guide</a><p>Alias body.</p></main></body>
                </html>
            """,
            "https://docs.example.com/guide": """
                <html>
                  <head><title>Guide</title></head>
                  <body><main><p>Canonical body.</p></main></body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.document_id for document in documents] == [
        "web:https://docs.example.com/guide",
    ]
    assert documents[0].title == "Guide"
    assert "Canonical body." in documents[0].content
    assert "Alias body." not in documents[0].content


def test_web_connector_does_not_replace_canonical_document_with_later_alias():
    client = FakeWebHTTP(
        {
            "https://docs.example.com/robots.txt": "",
            "https://docs.example.com/guide": """
                <html>
                  <head><title>Guide</title></head>
                  <body><main><a href="/alias">Alias</a><p>Canonical body.</p></main></body>
                </html>
            """,
            "https://docs.example.com/alias": """
                <html>
                  <head>
                    <title>Alias</title>
                    <link rel="canonical" href="https://docs.example.com/guide" />
                  </head>
                  <body><main><p>Alias body.</p></main></body>
                </html>
            """,
        }
    )
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/guide",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.document_id for document in documents] == [
        "web:https://docs.example.com/guide",
    ]
    assert documents[0].title == "Guide"
    assert "Canonical body." in documents[0].content
    assert "Alias body." not in documents[0].content


def test_web_connector_fails_required_page_errors_so_sync_can_skip_tombstones():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP({"https://docs.example.com/robots.txt": ""}),
    )

    with pytest.raises(RuntimeError, match="missing response"):
        asyncio.run(connector.fetch_documents())


def test_web_connector_marks_empty_extracted_pages_incomplete_for_cleanup():
    connector = WebsiteSourceConnector(
        seed_urls=("https://docs.example.com/start",),
        config=AppConfig(web_max_pages=2, web_crawl_delay_seconds=0),
        http_client=FakeWebHTTP(
            {
                "https://docs.example.com/robots.txt": "",
                "https://docs.example.com/start": """
                    <html><body><script>renderLater()</script></body></html>
                """,
            }
        ),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_web_connector_is_disabled_without_seed_urls():
    connector = WebsiteSourceConnector(seed_urls=(), config=AppConfig())

    assert connector.source.enabled is False
    assert asyncio.run(connector.fetch_documents()) == []


def test_web_connector_is_disabled_for_whitespace_only_seed_urls():
    connector = WebsiteSourceConnector(seed_urls=("   ", "\t"), config=AppConfig())

    assert connector.source.enabled is False
    assert connector.seed_urls == ()
    assert asyncio.run(connector.fetch_documents()) == []
