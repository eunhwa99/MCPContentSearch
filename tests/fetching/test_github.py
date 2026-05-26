import asyncio
import base64
import hashlib

import httpx
import pytest

from environments.config import AppConfig
from fetching.connectors import GitHubSourceConnector
from fetching.github import (
    GitHubHTTPClient,
    GitHubRepositoryDiscovery,
    parse_repository_or_owner_target,
    parse_repository_spec,
)
from indexing.chunker import DocumentChunker


pytestmark = pytest.mark.unit


_SHA_LABELS: dict[str, str] = {}


def _sha(label: str) -> str:
    value = hashlib.sha1(label.encode("utf-8")).hexdigest()
    _SHA_LABELS[value] = label
    return value


def _labelled_url(url: str) -> str:
    for value, label in _SHA_LABELS.items():
        url = url.replace(value, label)
    return url


def _blob_payload(content: bytes) -> dict:
    return {
        "encoding": "base64",
        "content": base64.b64encode(content).decode(),
        "size": len(content),
    }


def test_github_http_client_streams_blob_json_with_byte_limit():
    payload = _blob_payload(b"hello")

    def handler(request):
        assert request.headers["Authorization"] == "Bearer token"
        return httpx.Response(200, json=payload)

    client = GitHubHTTPClient(timeout=1, transport=httpx.MockTransport(handler))

    result = asyncio.run(
        client.get_blob_json(
            "https://api.github.com/repos/eunhwa99/repo/git/blobs/blob",
            headers={"Authorization": "Bearer token"},
            max_response_bytes=1024,
        )
    )

    assert result == payload


def test_github_connector_exposes_repository_cleanup_prefixes():
    connector = GitHubSourceConnector(
        ("EUNHWA99/MCPContentSearch@main", "eunhwa99/LeetCode@master"),
        AppConfig(),
    )

    assert connector.cleanup_document_id_prefixes == (
        "github:eunhwa99/mcpcontentsearch:",
        "github:eunhwa99/leetcode:",
    )


def test_github_http_client_rejects_blob_json_by_content_length():
    client = GitHubHTTPClient(
        timeout=1,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b"{}",
                headers={"Content-Length": "5"},
            )
        ),
    )

    with pytest.raises(RuntimeError, match="exceeded byte limit"):
        asyncio.run(
            client.get_blob_json(
                "https://api.github.com/repos/eunhwa99/repo/git/blobs/blob",
                max_response_bytes=4,
            )
        )


def test_github_http_client_rejects_streamed_blob_json_over_byte_limit():
    client = GitHubHTTPClient(
        timeout=1,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b"12345",
                headers={"Content-Length": "4"},
            )
        ),
    )

    with pytest.raises(RuntimeError, match="exceeded byte limit"):
        asyncio.run(
            client.get_blob_json(
                "https://api.github.com/repos/eunhwa99/repo/git/blobs/blob",
                max_response_bytes=4,
            )
        )


def test_github_http_client_rejects_invalid_blob_json():
    client = GitHubHTTPClient(
        timeout=1,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"not-json")
        ),
    )

    with pytest.raises(RuntimeError, match="not valid JSON"):
        asyncio.run(
            client.get_blob_json(
                "https://api.github.com/repos/eunhwa99/repo/git/blobs/blob",
                max_response_bytes=1024,
            )
        )


class FakeGitHubHTTP:
    def __init__(self):
        self.json_urls = []
        self.text_urls = []

    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        self.json_urls.append((url, headers or {}))
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": "api/tools.py",
                        "type": "blob",
                        "sha": _sha('blob-tools'),
                        "size": 38,
                    },
                    {
                        "path": "README.md",
                        "type": "blob",
                        "sha": _sha('blob-readme'),
                        "size": 29,
                    },
                    {
                        "path": "assets/logo.png",
                        "type": "blob",
                        "sha": _sha('blob-binary'),
                        "size": 30,
                    },
                    {
                        "path": "large.py",
                        "type": "blob",
                        "sha": _sha('blob-large'),
                        "size": 999_999,
                    },
                ]
            }
        if "/git/blobs/blob-tools" in url:
            return _blob_payload(b"def register_tools():\n    return 'ok'\n")
        if "/git/blobs/blob-readme" in url:
            return _blob_payload(b"# ContextWiki\n\nPhase B docs.\n")
        raise AssertionError(f"unexpected GitHub API URL: {url}")

    async def get_text(self, url, headers=None):
        self.text_urls.append((url, headers or {}))
        if url.endswith("/api/tools.py"):
            return "def register_tools():\n    return 'ok'\n"
        if url.endswith("/README.md"):
            return "# ContextWiki\n\nPhase B docs.\n"
        raise AssertionError(f"unexpected raw fetch: {url}")


class FailingGitHubHTTP(FakeGitHubHTTP):
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/git/blobs/" in url:
            raise RuntimeError("blob fetch failed")
        return await super().get_json(url, headers=headers)


class TruncatedGitHubHTTP(FakeGitHubHTTP):
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "truncated": True,
                "tree": [
                    {
                        "path": "api/tools.py",
                        "type": "blob",
                        "sha": _sha('blob-tools'),
                        "size": 38,
                    }
                ],
            }
        return await super().get_json(url, headers=headers)


class MissingTreePayloadGitHubHTTP(FakeGitHubHTTP):
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {"sha": _sha('tree-main')}
        return await super().get_json(url, headers=headers)


class MalformedBlobGitHubHTTP(FakeGitHubHTTP):
    def __init__(self, payload):
        super().__init__()
        self.payload = payload

    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": "README.md",
                        "type": "blob",
                        "sha": _sha('blob-readme'),
                        "size": 29,
                    }
                ]
            }
        if "/git/blobs/blob-readme" in url:
            return self.payload
        return await super().get_json(url, headers=headers)


class BinaryBlobGitHubHTTP(FakeGitHubHTTP):
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": "docs/binary.md",
                        "type": "blob",
                        "sha": _sha('blob-binary-doc'),
                        "size": 4,
                    }
                ]
            }
        if "/git/blobs/blob-binary-doc" in url:
            return _blob_payload(b"\x00\x01OK")
        return await super().get_json(url, headers=headers)


class ControlPathGitHubHTTP(FakeGitHubHTTP):
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": "docs/evil\nname.md",
                        "type": "blob",
                        "sha": _sha('blob-control-path'),
                        "size": 20,
                    },
                    {
                        "path": "README.md",
                        "type": "blob",
                        "sha": _sha('blob-readme'),
                        "size": 29,
                    },
                ]
            }
        return await super().get_json(url, headers=headers)


class LateControlBlobGitHubHTTP(FakeGitHubHTTP):
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": "docs/late-control.md",
                        "type": "blob",
                        "sha": _sha('blob-late-control'),
                        "size": 1104,
                    }
                ]
            }
        if "/git/blobs/blob-late-control" in url:
            return {
                "encoding": "base64",
                "content": base64.b64encode(
                    (b"A" * 1100) + b"\x00END"
                ).decode(),
                "size": 1104,
            }
        return await super().get_json(url, headers=headers)


class InvalidSnapshotShaGitHubHTTP(FakeGitHubHTTP):
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": "AKIAIOSFODNN7EXAMPLE",
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        return await super().get_json(url, headers=headers)


class SlugSnapshotShaGitHubHTTP(FakeGitHubHTTP):
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": "commit-main",
                "commit": {"tree": {"sha": "tree-main"}},
            }
        return await super().get_json(url, headers=headers)


class MissingSnapshotTreeShaGitHubHTTP(FakeGitHubHTTP):
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha("commit-main"),
                "commit": {"tree": {}},
            }
        return await super().get_json(url, headers=headers)


class InvalidEntryShaGitHubHTTP(FakeGitHubHTTP):
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": "README.md",
                        "type": "blob",
                        "sha": "token=privatevalue",
                        "size": 20,
                    }
                ]
            }
        raise AssertionError(f"unexpected GitHub API URL: {url}")


class MalformedTreeEntriesGitHubHTTP(FakeGitHubHTTP):
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha("commit-main"),
                "commit": {"tree": {"sha": _sha("tree-main")}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    None,
                    {
                        "path": "docs/negative.md",
                        "type": "blob",
                        "sha": _sha("blob-negative-size"),
                        "size": -1,
                    },
                    {
                        "path": "docs/bad-size.md",
                        "type": "blob",
                        "sha": _sha("blob-bad-size"),
                        "size": "not-a-size",
                    },
                    {
                        "path": "README.md",
                        "type": "blob",
                        "sha": _sha("blob-readme"),
                        "size": 12,
                    },
                ]
            }
        if "/git/blobs/blob-readme" in url:
            return _blob_payload(b"# Safe docs\n")
        return await super().get_json(url, headers=headers)


class EmptyBlobGitHubHTTP(FakeGitHubHTTP):
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha("commit-main"),
                "commit": {"tree": {"sha": _sha("tree-main")}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": "EMPTY.md",
                        "type": "blob",
                        "sha": _sha("blob-empty"),
                        "size": 0,
                    }
                ]
            }
        if "/git/blobs/blob-empty" in url:
            return {
                "encoding": "base64",
                "content": "",
                "size": 0,
            }
        return await super().get_json(url, headers=headers)


class MultiRepoGitHubHTTP:
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/repos/eunhwa99/repo-one/commits/main" in url:
            return {
                "sha": _sha('repo-one-commit'),
                "commit": {"tree": {"sha": _sha('repo-one-tree')}},
            }
        if "/repos/eunhwa99/repo-two/commits/main" in url:
            return {
                "sha": _sha('repo-two-commit'),
                "commit": {"tree": {"sha": _sha('repo-two-tree')}},
            }
        if "/git/blobs/" in url:
            return _blob_payload(b"print('ok')\n")
        if "/repos/eunhwa99/repo-one/git/trees/repo-one-tree" in url:
            return {
                "tree": [
                    {"path": "a.py", "type": "blob", "sha": _sha('repo-one-a'), "size": 12},
                    {"path": "b.py", "type": "blob", "sha": _sha('repo-one-b'), "size": 12},
                ]
            }
        if "/repos/eunhwa99/repo-two/git/trees/repo-two-tree" in url:
            return {
                "tree": [
                    {"path": "c.py", "type": "blob", "sha": _sha('repo-two-c'), "size": 12},
                    {"path": "d.py", "type": "blob", "sha": _sha('repo-two-d'), "size": 12},
                ]
            }
        raise AssertionError(f"unexpected tree URL: {url}")


class MissingTreeSizeLargeBlobGitHubHTTP:
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": "large.py",
                        "type": "blob",
                        "sha": _sha('blob-large'),
                    }
                ]
            }
        if "/git/blobs/blob-large" in url:
            return {
                "encoding": "base64",
                "content": base64.b64encode(b"print('larger than cap')\n").decode(),
            }
        raise AssertionError(f"unexpected GitHub API URL: {url}")


class CredentialPathGitHubHTTP:
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": "README.md",
                        "type": "blob",
                        "sha": _sha('blob-readme'),
                        "size": 12,
                    },
                    {
                        "path": "docs/ghp_secret123.md",
                        "type": "blob",
                        "sha": _sha('blob-secret-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/AWSAccessKeyId=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-access-key-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/apiKey=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-api-key-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/clientSecret=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-client-secret-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/token=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-token-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/password=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-password-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/AKIAIOSFODNN7EXAMPLE.md",
                        "type": "blob",
                        "sha": _sha('blob-aws-key-path'),
                        "size": 20,
                    },
                ]
            }
        if "/git/blobs/blob-readme" in url:
            return _blob_payload(b"# Safe docs\n")
        raise AssertionError(f"unexpected GitHub API URL: {url}")


class SecurityDocsGitHubHTTP:
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": "docs/api-key-authentication.md",
                        "type": "blob",
                        "sha": _sha('blob-api-key-docs'),
                        "size": 25,
                    },
                    {
                        "path": "docs/client-secret-rotation.md",
                        "type": "blob",
                        "sha": _sha('blob-client-secret-docs'),
                        "size": 25,
                    },
                    {
                        "path": "docs/access-key-management.md",
                        "type": "blob",
                        "sha": _sha('blob-access-key-docs'),
                        "size": 24,
                    },
                ]
            }
        if "/git/blobs/blob-api-key-docs" in url:
            return _blob_payload(b"# API key authentication\n")
        if "/git/blobs/blob-client-secret-docs" in url:
            return _blob_payload(b"# Client secret rotation\n")
        if "/git/blobs/blob-access-key-docs" in url:
            return _blob_payload(b"# Access key management\n")
        raise AssertionError(f"unexpected GitHub API URL: {url}")


class SessionPathGitHubHTTP:
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha('commit-main'),
                "commit": {"tree": {"sha": _sha('tree-main')}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": "README.md",
                        "type": "blob",
                        "sha": _sha('blob-readme'),
                        "size": 12,
                    },
                    {
                        "path": "docs/session=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-session-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/cookie=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-cookie-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/jwt=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-jwt-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/csrf=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-csrf-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/key=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-key-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/auth=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-auth-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/code=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-code-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/pass=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-pass-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/sig=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-sig-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/q=session=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-nested-session-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/q=apiKey=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-nested-api-key-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/JSESSIONID=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-jsessionid-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/PHPSESSID=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-phpsessid-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/CSRFToken=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-csrf-token-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/sessionToken=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-session-token-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/session/privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-session-segment-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/token/privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-token-segment-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/(token=privatevalue).md",
                        "type": "blob",
                        "sha": _sha('blob-punct-token-path'),
                        "size": 20,
                    },
                    {
                        "path": "docs/foo token=privatevalue.md",
                        "type": "blob",
                        "sha": _sha('blob-space-token-path'),
                        "size": 20,
                    },
                ]
            }
        if "/git/blobs/blob-readme" in url:
            return _blob_payload(b"# Safe docs\n")
        raise AssertionError(f"unexpected GitHub API URL: {url}")


class TokenShapedPathGitHubHTTP:
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/main" in url:
            return {
                "sha": _sha("commit-main"),
                "commit": {"tree": {"sha": _sha("tree-main")}},
            }
        if "/git/trees/tree-main" in url:
            return {
                "tree": [
                    {
                        "path": "README.md",
                        "type": "blob",
                        "sha": _sha("blob-readme"),
                        "size": 12,
                    },
                    {
                        "path": "docs/sk-proj-aaaaaaaaaaaaaaaaaaaaaaaa.md",
                        "type": "blob",
                        "sha": _sha("blob-openai-token-path"),
                        "size": 20,
                    },
                    {
                        "path": "docs/xoxb-123456789012-123456789012-token.md",
                        "type": "blob",
                        "sha": _sha("blob-slack-token-path"),
                        "size": 20,
                    },
                    {
                        "path": "docs/eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.signaturefakefake.md",
                        "type": "blob",
                        "sha": _sha("blob-jwt-token-path"),
                        "size": 20,
                    },
                    {
                        "path": "docs/AIzaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md",
                        "type": "blob",
                        "sha": _sha("blob-google-token-path"),
                        "size": 20,
                    },
                ]
            }
        if "/git/blobs/blob-readme" in url:
            return _blob_payload(b"# Safe docs\n")
        raise AssertionError(f"unexpected GitHub API URL: {url}")


class RefChangingGitHubHTTP:
    async def get_json(self, url, headers=None):
        url = _labelled_url(url)
        if "/commits/release" in url:
            return {
                "sha": _sha('commit-release'),
                "commit": {"tree": {"sha": _sha('tree-release')}},
            }
        if "/git/trees/tree-release" in url:
            return {
                "tree": [
                    {
                        "path": "api/tools.py",
                        "type": "blob",
                        "sha": _sha('blob-release-tools'),
                        "size": 37,
                    }
                ]
            }
        if "/git/blobs/blob-release-tools" in url:
            return _blob_payload(b"def release_tools():\n    return 'ok'\n")
        raise AssertionError(f"unexpected GitHub API URL: {url}")


def test_github_connector_fetches_text_files_with_stable_identity_and_citations():
    client = FakeGitHubHTTP()
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        token="secret-token",
        http_client=client,
    )

    documents = asyncio.run(connector.fetch_documents())

    assert connector.supports_stale_cleanup is False
    assert connector.source.source_id == "source_github"
    assert connector.source.enabled is True
    assert connector.source.auth_ref == "env:GITHUB_TOKEN"
    assert len(documents) == 2
    assert [document.path for document in documents] == ["README.md", "api/tools.py"]

    tools_doc = next(document for document in documents if document.path == "api/tools.py")
    assert tools_doc.document_id == "github:eunhwa99/mcpcontentsearch:api/tools.py"
    assert tools_doc.external_id == tools_doc.document_id
    assert tools_doc.canonical_url == (
        f"https://github.com/eunhwa99/MCPContentSearch/blob/{_sha('commit-main')}/api/tools.py"
    )
    assert tools_doc.url == tools_doc.canonical_url
    assert tools_doc.version_id == _sha("blob-tools")
    assert tools_doc.platform == "GitHub"
    assert "register_tools" in tools_doc.content

    chunks = DocumentChunker(max_chars=80, overlap_chars=0).chunk_document(tools_doc)
    assert chunks[0].path == "api/tools.py"
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 2

    _, headers = client.json_urls[0]
    assert headers["Authorization"] == "Bearer secret-token"
    assert any("/git/trees/tree-main" in url for url, _ in client.json_urls)
    assert any("/git/blobs/blob-tools" in url for url, _ in client.json_urls)
    assert not client.text_urls
    assert "secret-token" not in repr(connector.source)


def test_github_document_identity_does_not_include_configured_ref():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@release",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=RefChangingGitHubHTTP(),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents[0].document_id == "github:eunhwa99/mcpcontentsearch:api/tools.py"
    assert documents[0].external_id == documents[0].document_id
    assert documents[0].canonical_url == (
        f"https://github.com/eunhwa99/MCPContentSearch/blob/{_sha('commit-release')}/api/tools.py"
    )
    assert documents[0].version_id == _sha("blob-release-tools")


def test_github_document_identity_normalizes_owner_repo_case():
    lower_connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=FakeGitHubHTTP(),
    )
    upper_connector = GitHubSourceConnector(
        repositories=("EUNHWA99/mcpcontentsearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=FakeGitHubHTTP(),
    )

    lower_documents = asyncio.run(lower_connector.fetch_documents())
    upper_documents = asyncio.run(upper_connector.fetch_documents())

    assert {
        document.path: document.document_id for document in lower_documents
    } == {
        document.path: document.document_id for document in upper_documents
    }
    assert {
        document.document_id for document in lower_documents
    } == {
        "github:eunhwa99/mcpcontentsearch:README.md",
        "github:eunhwa99/mcpcontentsearch:api/tools.py",
    }


def test_github_connector_fails_required_blob_errors_so_sync_can_skip_tombstones():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=FailingGitHubHTTP(),
    )

    with pytest.raises(RuntimeError, match="blob fetch failed"):
        asyncio.run(connector.fetch_documents())


def test_github_connector_fails_truncated_tree_so_sync_can_skip_tombstones():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=TruncatedGitHubHTTP(),
    )

    with pytest.raises(RuntimeError, match="truncated"):
        asyncio.run(connector.fetch_documents())


def test_github_connector_fails_missing_tree_payload_so_sync_can_skip_tombstones():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=MissingTreePayloadGitHubHTTP(),
    )

    with pytest.raises(RuntimeError, match="missing tree"):
        asyncio.run(connector.fetch_documents())


@pytest.mark.parametrize(
    "payload",
    [
        {"encoding": "base64"},
        {"encoding": "base64", "size": 20},
        {"encoding": "base64", "content": base64.b64encode(b"x").decode()},
        {"encoding": "base64", "content": base64.b64encode(b"12345678901234567890").decode()},
        {"encoding": "base64", "content": "", "size": 20},
        {"encoding": "base64", "content": "not base64", "size": 20},
        {"encoding": "rot13", "content": "uryyb", "size": 5},
    ],
)
def test_github_connector_fails_malformed_blob_payloads(payload):
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=MalformedBlobGitHubHTTP(payload),
    )

    with pytest.raises(RuntimeError):
        asyncio.run(connector.fetch_documents())


def test_github_connector_skips_binary_blob_content_for_stale_cleanup():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=BinaryBlobGitHubHTTP(),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_github_connector_skips_control_character_paths_for_stale_cleanup():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=ControlPathGitHubHTTP(),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == ["README.md"]
    assert all("\n" not in document.document_id for document in documents)
    assert connector.supports_stale_cleanup is False


def test_github_connector_skips_late_control_blob_content_for_stale_cleanup():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=2000),
        http_client=LateControlBlobGitHubHTTP(),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_github_connector_fails_invalid_commit_sha_before_persisting_metadata():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=InvalidSnapshotShaGitHubHTTP(),
    )

    with pytest.raises(RuntimeError, match="commit sha"):
        asyncio.run(connector.fetch_documents())


def test_github_connector_fails_slug_commit_sha_before_persisting_metadata():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=SlugSnapshotShaGitHubHTTP(),
    )

    with pytest.raises(RuntimeError, match="commit sha"):
        asyncio.run(connector.fetch_documents())


def test_github_connector_fails_missing_tree_sha_before_fetching_tree():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=MissingSnapshotTreeShaGitHubHTTP(),
    )

    with pytest.raises(RuntimeError, match="tree sha"):
        asyncio.run(connector.fetch_documents())


def test_github_connector_skips_invalid_tree_entry_sha_for_stale_cleanup():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=InvalidEntryShaGitHubHTTP(),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_github_connector_skips_malformed_tree_entries_for_stale_cleanup():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=MalformedTreeEntriesGitHubHTTP(),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == ["README.md"]
    assert connector.supports_stale_cleanup is False


def test_github_connector_accepts_explicit_empty_text_blob():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=EmptyBlobGitHubHTTP(),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert len(documents) == 1
    assert documents[0].content == ""
    assert connector.supports_stale_cleanup is True


def test_github_file_cap_marks_snapshot_incomplete_per_repository():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/repo-one@main", "eunhwa99/repo-two@main"),
        config=AppConfig(github_max_files=1, github_max_file_bytes=1000),
        http_client=MultiRepoGitHubHTTP(),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == ["a.py", "c.py"]
    assert {document.document_id for document in documents} == {
        "github:eunhwa99/repo-one:a.py",
        "github:eunhwa99/repo-two:c.py",
    }
    assert connector.supports_stale_cleanup is False


def test_github_blob_byte_cap_marks_snapshot_incomplete_when_tree_size_is_missing():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=5),
        http_client=MissingTreeSizeLargeBlobGitHubHTTP(),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert documents == []
    assert connector.supports_stale_cleanup is False


def test_github_connector_skips_token_like_tree_paths_for_stale_cleanup():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=CredentialPathGitHubHTTP(),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == ["README.md"]
    assert connector.supports_stale_cleanup is False
    assert all("ghp_secret" not in document.document_id for document in documents)
    assert all("AWSAccessKeyId" not in document.document_id for document in documents)
    assert all("apiKey" not in document.document_id for document in documents)
    assert all("clientSecret" not in document.document_id for document in documents)
    assert all("token" not in document.document_id for document in documents)
    assert all("password" not in document.document_id for document in documents)
    assert all("AKIA" not in document.document_id for document in documents)


def test_github_connector_keeps_security_topic_documentation_paths():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/api-key-docs@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=SecurityDocsGitHubHTTP(),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == [
        "docs/access-key-management.md",
        "docs/api-key-authentication.md",
        "docs/client-secret-rotation.md",
    ]
    assert connector.supports_stale_cleanup is True
    assert {document.document_id for document in documents} == {
        "github:eunhwa99/api-key-docs:docs/access-key-management.md",
        "github:eunhwa99/api-key-docs:docs/api-key-authentication.md",
        "github:eunhwa99/api-key-docs:docs/client-secret-rotation.md",
    }


def test_github_connector_skips_session_cookie_jwt_tree_paths_for_stale_cleanup():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=SessionPathGitHubHTTP(),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == ["README.md"]
    assert connector.supports_stale_cleanup is False
    assert all("session" not in document.document_id for document in documents)
    assert all("cookie" not in document.document_id for document in documents)
    assert all("jwt" not in document.document_id for document in documents)
    assert all("csrf" not in document.document_id for document in documents)
    assert all("key=privatevalue" not in document.document_id for document in documents)
    assert all("auth=privatevalue" not in document.document_id for document in documents)
    assert all("code=privatevalue" not in document.document_id for document in documents)
    assert all("pass=privatevalue" not in document.document_id for document in documents)
    assert all("sig=privatevalue" not in document.document_id for document in documents)
    assert all("JSESSIONID" not in document.document_id for document in documents)
    assert all("PHPSESSID" not in document.document_id for document in documents)
    assert all("sessionToken" not in document.document_id for document in documents)
    assert all("session/privatevalue" not in document.document_id for document in documents)
    assert all("token/privatevalue" not in document.document_id for document in documents)
    assert all("token=privatevalue" not in document.document_id for document in documents)


def test_github_connector_skips_token_shaped_tree_paths_for_stale_cleanup():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=TokenShapedPathGitHubHTTP(),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == ["README.md"]
    assert connector.supports_stale_cleanup is False
    assert all("sk-proj" not in document.document_id for document in documents)
    assert all("xoxb" not in document.document_id for document in documents)
    assert all("eyJ" not in document.document_id for document in documents)
    assert all("AIza" not in document.document_id for document in documents)


def test_github_byte_cap_marks_snapshot_incomplete_for_supported_file():
    connector = GitHubSourceConnector(
        repositories=("eunhwa99/MCPContentSearch@main",),
        config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        http_client=FakeGitHubHTTP(),
    )

    documents = asyncio.run(connector.fetch_documents())

    assert [document.path for document in documents] == ["README.md", "api/tools.py"]
    assert connector.supports_stale_cleanup is False


def test_github_connector_is_disabled_without_configured_repositories():
    connector = GitHubSourceConnector(repositories=(), config=AppConfig())

    assert connector.source.enabled is False
    assert asyncio.run(connector.fetch_documents()) == []


def test_parse_repository_spec_supports_clone_url_with_git_suffix_and_ref():
    spec = parse_repository_spec("https://github.com/eunhwa99/MCPContentSearch.git@main")

    assert spec.owner == "eunhwa99"
    assert spec.repo == "MCPContentSearch"
    assert spec.ref == "main"


def test_parse_repository_or_owner_target_accepts_github_owner_url():
    assert parse_repository_or_owner_target("github.com/eunhwa99") == (
        "eunhwa99",
        "",
        "",
    )


def test_parse_repository_or_owner_target_accepts_repository_url():
    assert parse_repository_or_owner_target("https://github.com/eunhwa99/repo") == (
        "eunhwa99",
        "repo",
        "main",
    )


def test_parse_repository_or_owner_target_rejects_secret_url_without_leaking_secret():
    with pytest.raises(ValueError) as exc_info:
        parse_repository_or_owner_target("https://github.com/eunhwa99?token=secret")

    message = str(exc_info.value)
    assert "secret" not in message
    assert "token=secret" not in message


class OwnerRepositoryListHTTP:
    def __init__(self):
        self.urls = []

    async def get_json(self, url, headers=None):
        self.urls.append(url)
        if "page=1" in url:
            return [
                {
                    "name": "algorithms",
                    "default_branch": "main",
                    "owner": {"login": "eunhwa99"},
                },
                {
                    "name": "neetcode",
                    "default_branch": "trunk",
                    "owner": {"login": "eunhwa99"},
                },
                {
                    "name": "ghp_secret",
                    "default_branch": "main",
                    "owner": {"login": "eunhwa99"},
                },
            ]
        return []


def test_github_repository_discovery_expands_owner_url_to_repo_specs():
    http = OwnerRepositoryListHTTP()
    discovery = GitHubRepositoryDiscovery(AppConfig(), http_client=http)

    specs = asyncio.run(discovery.discover_repository_specs("github.com/eunhwa99"))

    assert specs == [
        "eunhwa99/algorithms@main",
        "eunhwa99/neetcode@trunk",
    ]
    assert http.urls[0].startswith("https://api.github.com/users/eunhwa99/repos?")


def test_github_repository_discovery_keeps_explicit_repository_target_off_network():
    http = OwnerRepositoryListHTTP()
    discovery = GitHubRepositoryDiscovery(AppConfig(), http_client=http)

    specs = asyncio.run(discovery.discover_repository_specs("eunhwa99/neetcode@main"))

    assert specs == ["eunhwa99/neetcode@main"]
    assert http.urls == []


def test_parse_repository_spec_rejects_credentialed_clone_url_without_leaking_secret():
    with pytest.raises(ValueError) as exc_info:
        parse_repository_spec(
            "https://ghp_secret@github.com/eunhwa99/MCPContentSearch.git@main"
        )

    assert "ghp_secret" not in str(exc_info.value)


@pytest.mark.parametrize(
    "value",
    [
        "ssh://ghp_secret@github.com/eunhwa99/MCPContentSearch.git",
        "git+https://ghp_secret@github.com/eunhwa99/MCPContentSearch.git",
        "https://example.com/eunhwa99/MCPContentSearch.git?token=secret",
        "https://github.com/eunhwa99/MCPContentSearch?token=secret",
        "https://github.com/eunhwa99/MCPContentSearch?foo=ghp_secret",
        "https://github.com/eunhwa99/MCPContentSearch#token=secret",
        "eunhwa99/MCPContentSearch?token=secret",
        "token@github.com/eunhwa99/MCPContentSearch",
        "ghp_secret",
        "github_pat_secret",
        "eunhwa99/ghp_secret123@main",
        "eunhwa99/ghp%5Fsecret123@main",
        "eunhwa99/%67%68%70%5Fsecret123@main",
    ],
)
def test_parse_repository_spec_rejects_url_like_specs_without_leaking_secret(
    value,
):
    with pytest.raises(ValueError) as exc_info:
        parse_repository_spec(value)

    message = str(exc_info.value)
    assert "ghp_secret" not in message
    assert "secret" not in message
    assert "token=secret" not in message
    assert "token@github.com" not in message


@pytest.mark.parametrize(
    "default_ref",
    [
        "main?token=secret",
        "main#token=secret",
        "bad ref",
        "../main",
        "feature[bad]",
        "",
        "   ",
        "\x01main",
        "ghp_secret",
        "ghp%5Fsecret",
        "github%5Fpat%5Fsecret",
        "@",
        ".hidden",
        "feature/.hidden",
        "foo.lock/bar",
    ],
)
def test_parse_repository_spec_rejects_invalid_default_ref_without_leaking_secret(
    default_ref,
):
    with pytest.raises(ValueError) as exc_info:
        parse_repository_spec("eunhwa99/MCPContentSearch", default_ref)

    message = str(exc_info.value)
    assert "secret" not in message
    assert "token=secret" not in message


@pytest.mark.parametrize(
    "value",
    [
        "eunhwa99/MCPContentSearch@main#token=secret",
        "eunhwa99/MCPContentSearch@main\x01",
        "eunhwa99/MCPContentSearch@ghp_secret",
        "eunhwa99/MCPContentSearch@ghp%5Fsecret",
        "eunhwa99/MCPContentSearch@%67%68%70%5Fsecret",
        "eunhwa99/MCPContentSearch@release(token=secret)",
        "eunhwa99/MCPContentSearch@release%28token=secret%29",
        "eunhwa99/MCPContentSearch@release%20token=secret",
        "eunhwa99/MCPContentSearch@@",
        "eunhwa99/MCPContentSearch@.hidden",
        "eunhwa99/MCPContentSearch@feature/.hidden",
        "eunhwa99/MCPContentSearch@foo.lock/bar",
    ],
)
def test_parse_repository_spec_rejects_invalid_explicit_ref_without_leaking_secret(
    value,
):
    with pytest.raises(ValueError) as exc_info:
        parse_repository_spec(value)

    message = str(exc_info.value)
    assert "secret" not in message
    assert "token=secret" not in message
    assert "ghp_secret" not in message


def test_github_connector_rejects_duplicate_repository_refs():
    with pytest.raises(ValueError, match="Duplicate GitHub repository spec"):
        GitHubSourceConnector(
            repositories=(
                "eunhwa99/MCPContentSearch@main",
                "eunhwa99/MCPContentSearch@release",
            ),
            config=AppConfig(github_max_files=10, github_max_file_bytes=1000),
        )
