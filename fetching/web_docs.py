import asyncio
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
import re
from urllib.parse import parse_qsl, unquote, urldefrag, urljoin, urlparse
import warnings
from xml.etree import ElementTree

import httpx
from bs4 import (
    BeautifulSoup,
    Comment,
    Declaration,
    Doctype,
    NavigableString,
    ProcessingInstruction,
    XMLParsedAsHTMLWarning,
)

from core.models import DocumentModel
from environments.config import AppConfig


SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
ALLOWED_SITEMAP_NAMESPACES = {"", SITEMAP_NAMESPACE}
SUPPORTED_PAGE_CONTENT_TYPES = {
    "application/markdown",
    "application/xhtml+xml",
    "text/html",
    "text/markdown",
    "text/plain",
    "text/x-markdown",
}
READABLE_RESPONSE_CONTENT_TYPES = SUPPORTED_PAGE_CONTENT_TYPES | {
    "application/atom+xml",
    "application/rss+xml",
    "application/xml",
    "text/xml",
}
SUPPORTED_PAGE_EXTENSIONS = {
    ".htm",
    ".html",
    ".markdown",
    ".md",
    ".rst",
    ".txt",
}
UNSUPPORTED_PAGE_EXTENSIONS = {
    ".7z",
    ".avi",
    ".bmp",
    ".bz2",
    ".dmg",
    ".doc",
    ".docx",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".iso",
    ".jar",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".svg",
    ".svgz",
    ".tar",
    ".tgz",
    ".webm",
    ".webp",
    ".xls",
    ".xlsx",
    ".zip",
}
HTML_DOCUMENT_ROOTS = {
    "article",
    "body",
    "div",
    "h1",
    "h2",
    "head",
    "html",
    "main",
    "p",
    "section",
    "title",
}
HTML_FRAGMENT_ROOTS = HTML_DOCUMENT_ROOTS | {
    "a",
    "abbr",
    "aside",
    "b",
    "blockquote",
    "cite",
    "code",
    "data",
    "dd",
    "details",
    "dfn",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "h3",
    "h4",
    "h5",
    "h6",
    "i",
    "kbd",
    "li",
    "mark",
    "ol",
    "pre",
    "q",
    "s",
    "samp",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
    "var",
    "wbr",
}
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "cookie",
    "code",
    "csrf",
    "jwt",
    "key",
    "pass",
    "password",
    "secret",
    "session",
    "session_id",
    "sessionid",
    "sig",
    "signature",
    "sid",
    "token",
    "xsrf",
}
CREDENTIAL_LIKE_RE = re.compile(
    r"(?:"
    r"gh[pousr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AIza[A-Za-z0-9_-]{30,}|"
    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|"
    r"(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"
    r")",
    re.IGNORECASE,
)
SAFE_ETAG_RE = re.compile(r'^(?:W/)?"[\x20-\x21\x23-\x7e]*"$')
SENSITIVE_ASSIGNMENT_KEY_RE = re.compile(
    r"(?:^|[^A-Za-z0-9])"
    r"(?:access[-_]?key(?:[-_]?id)?|access[-_]?token|api[-_]?key|"
    r"auth|authorization|aws[-_]?access[-_]?key[-_]?id|client[-_]?secret|"
    r"code|cookie|credential|csrf[-_]?token|csrf|j[-_]?session[-_]?id|"
    r"jwt[-_]?token|jwt|key|pass|password|php[-_]?sess[-_]?id|secret|"
    r"session[-_]?id|session[-_]?token|session|sig|signature|sid|token|"
    r"xsrf[-_]?token|xsrf|"
    r"x[-_]?amz[-_]?access[-_]?key[-_]?id|x[-_]?amz[-_]?credential|"
    r"x[-_]?amz[-_]?signature)"
    r"\s*[:=]",
    re.IGNORECASE,
)
SENSITIVE_PATH_SEGMENT_KEYS = {
    "accesskey",
    "accesskeyid",
    "accesstoken",
    "apikey",
    "authorization",
    "awsaccesskeyid",
    "clientsecret",
    "cookie",
    "credential",
    "csrf",
    "csrftoken",
    "jwt",
    "jwttoken",
    "jsessionid",
    "password",
    "phpsessid",
    "secret",
    "session",
    "sessionid",
    "sessiontoken",
    "sid",
    "token",
    "xamzaccesskeyid",
    "xamzcredential",
    "xamzsignature",
    "xsrf",
    "xsrftoken",
}


@dataclass(frozen=True)
class FetchResponse:
    url: str
    text: str
    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body_skipped: bool = False
    body_prefix: bytes = b""
    body_decode_failed: bool = False


class WebsiteHTTPClient:
    def __init__(
        self,
        timeout: float,
        max_response_bytes: int,
        *,
        transport=None,
    ):
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.transport = transport

    async def get_response(self, url: str, headers: dict[str, str] | None = None) -> FetchResponse:
        return await self._get_response(
            url,
            headers=headers,
            respect_content_type=True,
        )

    async def get_robots_response(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> FetchResponse:
        return await self._get_response(
            url,
            headers=headers,
            respect_content_type=False,
        )

    async def _get_response(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        respect_content_type: bool,
    ) -> FetchResponse:
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            async with client.stream("GET", url, headers=headers) as response:
                response_headers = dict(response.headers)
                response_url = str(response.url)
                if 300 <= response.status_code < 400:
                    return FetchResponse(
                        url=response_url,
                        text="",
                        status_code=response.status_code,
                        headers=response_headers,
                    )
                response.raise_for_status()
                if _content_length_exceeds(response_headers, self.max_response_bytes):
                    return FetchResponse(
                        url=response_url,
                        text="",
                        status_code=response.status_code,
                        headers=response_headers,
                        body_skipped=True,
                    )
                if respect_content_type and not _should_read_response_body(
                    response_url,
                    response_headers,
                ):
                    return FetchResponse(
                        url=response_url,
                        text="",
                        status_code=response.status_code,
                        headers=response_headers,
                    )

                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self.max_response_bytes:
                        return FetchResponse(
                            url=response_url,
                            text="",
                            status_code=response.status_code,
                            headers=response_headers,
                            body_skipped=True,
                        )
                decoded_text, body_decode_failed = _decode_response_body(
                    bytes(body),
                    response.encoding,
                )
                return FetchResponse(
                    url=response_url,
                    text=decoded_text,
                    status_code=response.status_code,
                    headers=response_headers,
                    body_prefix=bytes(body[:512]),
                    body_decode_failed=body_decode_failed,
                )

    async def get_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        return (await self.get_response(url, headers=headers)).text


@dataclass(frozen=True)
class RobotsRules:
    rules: tuple[tuple[str, str], ...] = ()

    def allows(self, url: str) -> bool:
        parsed = urlparse(url)
        path = unquote(parsed.path or "/")
        if parsed.query:
            path = f"{path}?{unquote(parsed.query)}"
        matches = [
            (len(rule_path), directive)
            for directive, rule_path in self.rules
            if rule_path and _robots_rule_matches(path, rule_path)
        ]
        if not matches:
            return True
        longest_match = max(length for length, _ in matches)
        return any(
            directive == "allow"
            for length, directive in matches
            if length == longest_match
        )

    @classmethod
    def parse(cls, text: str, user_agent: str = "ContextWikiBot") -> "RobotsRules":
        agent_token = _user_agent_token(user_agent)
        user_agent_lower = user_agent.lower()
        groups: list[tuple[list[str], list[tuple[str, str]]]] = []
        group_agents: list[str] = []
        group_rules: list[tuple[str, str]] = []
        group_has_directives = False

        def finish_group():
            if group_agents:
                groups.append((list(group_agents), list(group_rules)))

        for raw_line in text.lstrip("\ufeff").splitlines():
            raw_stripped = raw_line.strip()
            line = raw_line.split("#", 1)[0].strip()
            if not raw_stripped:
                if group_agents:
                    finish_group()
                    group_agents = []
                    group_rules = []
                    group_has_directives = False
                continue
            if not line:
                continue
            if ":" not in line:
                continue
            key, value = [part.strip() for part in line.split(":", 1)]
            key = key.lower()
            if key == "user-agent":
                if group_has_directives:
                    finish_group()
                    group_agents = []
                    group_rules = []
                    group_has_directives = False
                group_agents.append(value.lower())
            elif key in {"allow", "disallow"}:
                group_has_directives = True
                if value:
                    group_rules.append((key, unquote(value)))
        finish_group()
        rules = _select_robots_rules(groups, agent_token, user_agent_lower)
        return cls(rules=tuple(rules))


class WebsiteDocsFetcher:
    """Bounded docs crawler with sitemap and robots.txt support."""

    def __init__(self, seed_urls: tuple[str, ...], config: AppConfig, *, http_client=None):
        self.seed_urls = tuple(_seed_url(url) for url in seed_urls if url.strip())
        self.config = config
        self.http_client = http_client or WebsiteHTTPClient(
            config.request_timeout,
            config.web_max_response_bytes,
        )
        self._robots_cache: dict[str, RobotsRules] = {}
        self._page_fetch_started = False
        self.snapshot_complete = True

    async def fetch_documents(self) -> list[DocumentModel]:
        self.snapshot_complete = True
        self._robots_cache = {}
        self._page_fetch_started = False
        documents: list[DocumentModel] = []
        queue = []
        queued = set()
        for seed_url in self.seed_urls:
            seed_key = _crawl_key(seed_url)
            if seed_key in queued:
                continue
            queue.append(seed_url)
            queued.add(seed_key)
        attempted = set()
        visited = set()
        blocked = set()
        deferred_candidates: dict[str, str] = {}
        document_positions: dict[str, int] = {}
        page_visited = set()
        fetch_attempts = 0
        max_fetch_attempts = (self.config.web_max_pages * 2) + len(queue)
        max_deferred_candidates = max(1, self.config.web_max_pages)
        request_budget_exhausted = False
        frontier_overflow = False

        async def queue_candidate(base_url: str, linked_url: str) -> bool:
            nonlocal frontier_overflow
            if not _same_origin(base_url, linked_url):
                return False
            linked_key = _crawl_key(linked_url)
            if (
                linked_key in queued
                or linked_key in visited
                or linked_key in blocked
                or linked_key in deferred_candidates
            ):
                return False
            if not await self._allowed_by_robots(linked_url):
                blocked.add(linked_key)
                self.snapshot_complete = False
                return False
            if await self._remaining_queue_slots(
                page_visited,
                queue,
                blocked,
                attempted,
            ) <= 0:
                if len(deferred_candidates) >= max_deferred_candidates:
                    frontier_overflow = True
                    return True
                deferred_candidates[linked_key] = linked_url
                return False
            queue.append(linked_url)
            queued.add(linked_key)
            deferred_candidates.pop(linked_key, None)
            return False

        while (
            queue
            and len(page_visited) < self.config.web_max_pages
            and fetch_attempts < max_fetch_attempts
        ):
            url = queue.pop(0)
            url_key = _crawl_key(url)
            if url_key in attempted or url_key in visited or url_key in blocked:
                continue
            if not await self._allowed_by_robots(url):
                blocked.add(url_key)
                self.snapshot_complete = False
                await self._queue_deferred_candidates(
                    deferred_candidates,
                    queue,
                    queued,
                    page_visited,
                    blocked,
                    attempted,
                )
                continue
            attempted.add(url_key)

            (
                response,
                request_count,
                budget_exhausted,
                media_hinted_redirect_chain,
            ) = await self._fetch_response(
                url,
                remaining_requests=max_fetch_attempts - fetch_attempts,
            )
            fetch_attempts += request_count
            request_budget_exhausted = request_budget_exhausted or budget_exhausted
            if media_hinted_redirect_chain:
                self.snapshot_complete = False
            if response is None:
                await self._queue_deferred_candidates(
                    deferred_candidates,
                    queue,
                    queued,
                    page_visited,
                    blocked,
                    attempted,
                )
                continue
            if not _same_origin(url, response.url):
                raise RuntimeError(
                    "Blocked cross-origin redirect from "
                    f"{_redact_url_credentials(url)} to "
                    f"{_redact_url_credentials(response.url)}"
                )
            resolved_url = _fetch_url(response.url)
            resolved_key = _crawl_key(resolved_url)
            already_visited = resolved_key in visited
            if already_visited:
                await self._queue_deferred_candidates(
                    deferred_candidates,
                    queue,
                    queued,
                    page_visited,
                    blocked,
                    attempted,
                )
                continue
            visited.add(resolved_key)
            queued.add(resolved_key)
            body = response.text
            if _explicit_html_response_has_unsupported_xml_root(response):
                page_visited.add(resolved_key)
                self.snapshot_complete = False
                await self._queue_deferred_candidates(
                    deferred_candidates,
                    queue,
                    queued,
                    page_visited,
                    blocked,
                    attempted,
                )
                continue
            if _looks_like_sitemap(response.url, body):
                has_sitemap_url = False
                for linked_url in self._parse_sitemap(response.url, body):
                    has_sitemap_url = True
                    if _crawl_key(linked_url) == _crawl_key(response.url):
                        self.snapshot_complete = False
                        continue
                    if not _same_origin(response.url, linked_url):
                        self.snapshot_complete = False
                        continue
                    if await queue_candidate(response.url, linked_url):
                        break
                if not has_sitemap_url:
                    self.snapshot_complete = False
                continue
            page_visited.add(resolved_key)
            if response.body_skipped:
                self.snapshot_complete = False
                await self._queue_deferred_candidates(
                    deferred_candidates,
                    queue,
                    queued,
                    page_visited,
                    blocked,
                    attempted,
                )
                continue
            if response.body_decode_failed:
                self.snapshot_complete = False
                await self._queue_deferred_candidates(
                    deferred_candidates,
                    queue,
                    queued,
                    page_visited,
                    blocked,
                    attempted,
                )
                continue
            if (
                _looks_like_xml(response.url, body)
                and _content_type(response.headers) != "application/xhtml+xml"
                and not _supports_page_content(response)
            ):
                raise ValueError(
                    "Invalid sitemap or unsupported XML document: "
                    f"{_redact_url_credentials(response.url)}"
                )
            if not _supports_page_content(response):
                self.snapshot_complete = False
                await self._queue_deferred_candidates(
                    deferred_candidates,
                    queue,
                    queued,
                    page_visited,
                    blocked,
                    attempted,
                )
                continue
            if _response_disables_stale_cleanup(response):
                self.snapshot_complete = False

            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
                soup = BeautifulSoup(body, "html.parser")
            base_url = await self._base_url(response.url, soup)
            for linked_url in _iter_page_links(
                base_url,
                soup,
                on_malformed=self._mark_snapshot_incomplete,
            ):
                if await queue_candidate(response.url, linked_url):
                    break

            document = await self._parse_page(
                response.url,
                soup,
                base_url=base_url,
                response=response,
            )
            is_canonical_fetch = _canonical_url(response.url) == document.canonical_url
            if document.content.strip() and document.document_id not in document_positions:
                document_positions[document.document_id] = len(documents)
                documents.append(document)
            elif document.content.strip() and is_canonical_fetch:
                documents[document_positions[document.document_id]] = document
            elif not document.content.strip():
                self.snapshot_complete = False

        has_unvisited_allowed_queue = await self._has_unvisited_allowed_queue(
            [*queue, *deferred_candidates.values()],
            page_visited,
            blocked,
            attempted,
        )
        self.snapshot_complete = self.snapshot_complete and not (
            request_budget_exhausted or frontier_overflow or has_unvisited_allowed_queue
        )
        return documents

    async def _fetch_response(
        self,
        url: str,
        remaining_requests: int,
    ) -> tuple[FetchResponse | None, int, bool, bool]:
        current_url = url
        request_count = 0
        media_hinted_redirect_chain = _has_unsupported_media_hint(current_url)
        for _ in range(6):
            media_hinted_redirect_chain = (
                media_hinted_redirect_chain or _has_unsupported_media_hint(current_url)
            )
            if not await self._allowed_by_robots(current_url):
                self.snapshot_complete = False
                return None, request_count, False, media_hinted_redirect_chain
            if request_count >= remaining_requests:
                return None, request_count, True, media_hinted_redirect_chain
            await self._delay_before_page_fetch()
            try:
                response = await self._fetch_once(current_url)
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    "HTTP error while fetching "
                    f"{_redact_url_credentials(str(exc.request.url))}: "
                    f"{exc.response.status_code}"
                ) from None
            request_count += 1
            if response.status_code not in range(300, 400):
                if not _same_origin(url, response.url):
                    raise RuntimeError(
                        "Blocked cross-origin redirect from "
                        f"{_redact_url_credentials(url)} to "
                        f"{_redact_url_credentials(response.url)}"
                    )
                if response.url != url and not await self._allowed_by_robots(response.url):
                    self.snapshot_complete = False
                    return None, request_count, False, media_hinted_redirect_chain
                media_hinted_redirect_chain = (
                    media_hinted_redirect_chain
                    or _has_unsupported_media_hint(response.url)
                )
                return response, request_count, False, media_hinted_redirect_chain

            location = response.headers.get("location")
            if not location:
                raise RuntimeError(
                    "Redirect response missing Location header: "
                    f"{_redact_url_credentials(current_url)}"
                )
            next_url = _join_fetch_url(response.url, location)
            media_hinted_redirect_chain = (
                media_hinted_redirect_chain or _has_unsupported_media_hint(next_url)
            )
            if not next_url or not _same_origin(current_url, next_url):
                raise RuntimeError(
                    "Blocked cross-origin redirect from "
                    f"{_redact_url_credentials(current_url)} to "
                    f"{_redact_url_credentials(next_url)}"
                )
            current_url = next_url
        raise RuntimeError(f"Too many redirects while fetching {_redact_url_credentials(url)}")

    def _mark_snapshot_incomplete(self) -> None:
        self.snapshot_complete = False

    async def _delay_before_page_fetch(self) -> None:
        if self.config.web_crawl_delay_seconds <= 0:
            return
        if self._page_fetch_started:
            await asyncio.sleep(self.config.web_crawl_delay_seconds)
        self._page_fetch_started = True

    async def _fetch_once(self, url: str) -> FetchResponse:
        get_response = getattr(self.http_client, "get_response", None)
        if callable(get_response):
            return await get_response(url, headers=self._headers())
        return FetchResponse(
            url=url,
            text=await self.http_client.get_text(url, headers=self._headers()),
        )

    async def _fetch_robots_once(self, url: str) -> FetchResponse:
        get_robots_response = getattr(self.http_client, "get_robots_response", None)
        if callable(get_robots_response):
            return await get_robots_response(url, headers=self._headers())
        return await self._fetch_once(url)

    async def _allowed_by_robots(self, url: str) -> bool:
        rules = await self._robots_for(url)
        return rules.allows(url)

    async def _robots_for(self, url: str) -> RobotsRules:
        parsed = urlparse(url)
        origin = _origin_url(parsed)
        if origin not in self._robots_cache:
            try:
                robots_response = await self._fetch_robots_response(f"{origin}/robots.txt")
                if robots_response.body_skipped:
                    self.snapshot_complete = False
                    raise RuntimeError(f"robots.txt body was skipped for {origin}")
                if robots_response.body_decode_failed:
                    self.snapshot_complete = False
                    raise RuntimeError(f"robots.txt body could not be decoded for {origin}")
                robots_text = robots_response.text
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {404, 410}:
                    robots_text = ""
                else:
                    raise RuntimeError(
                        "HTTP error while fetching "
                        f"{_redact_url_credentials(str(exc.request.url))}: "
                        f"{exc.response.status_code}"
                    ) from None
            except Exception:
                raise
            self._robots_cache[origin] = RobotsRules.parse(
                robots_text,
                self.config.web_user_agent,
            )
        return self._robots_cache[origin]

    async def _fetch_robots_response(self, url: str) -> FetchResponse:
        current_url = url
        for _ in range(6):
            response = await self._fetch_robots_once(current_url)
            if response.status_code not in range(300, 400):
                return response

            location = response.headers.get("location")
            if not location:
                raise RuntimeError(
                    "Redirect response missing Location header: "
                    f"{_redact_url_credentials(current_url)}"
                )
            next_url = _join_fetch_url(response.url, location)
            if not next_url or not _same_origin(current_url, next_url):
                raise RuntimeError(
                    "Blocked cross-origin robots redirect from "
                    f"{_redact_url_credentials(current_url)} to "
                    f"{_redact_url_credentials(next_url)}"
                )
            current_url = next_url
        raise RuntimeError(f"Too many redirects while fetching {_redact_url_credentials(url)}")

    def _parse_sitemap(self, sitemap_url: str, body: str):
        try:
            root = ElementTree.fromstring(_strip_leading_bom(body))
        except ElementTree.ParseError as exc:
            raise ValueError(
                f"Invalid sitemap: {_redact_url_credentials(sitemap_url)}"
            ) from exc
        root_namespace, root_name = _split_tag(root.tag)
        if (
            root_namespace not in ALLOWED_SITEMAP_NAMESPACES
            or root_name not in {"urlset", "sitemapindex"}
        ):
            raise ValueError(f"Invalid sitemap: {_redact_url_credentials(sitemap_url)}")
        child_name = "url" if root_name == "urlset" else "sitemap"
        for child in root:
            if not _sitemap_tag_matches(child.tag, child_name, root_namespace):
                _, local_name = _split_tag(child.tag)
                if local_name:
                    self.snapshot_complete = False
                continue
            loc_seen = False
            for element in child:
                if not _sitemap_tag_matches(element.tag, "loc", root_namespace):
                    continue
                loc_seen = True
                if not element.text or not element.text.strip():
                    self.snapshot_complete = False
                    continue
                candidate = _join_fetch_url(sitemap_url, element.text.strip())
                if candidate:
                    if _crawl_key(candidate) == _crawl_key(sitemap_url):
                        self.snapshot_complete = False
                    else:
                        yield candidate
                else:
                    self.snapshot_complete = False
                break
            if not loc_seen:
                self.snapshot_complete = False

    async def _parse_page(
        self,
        url: str,
        soup: BeautifulSoup,
        *,
        base_url: str,
        response: FetchResponse,
    ) -> DocumentModel:
        canonical = await self._canonical_url(url, soup, base_url=base_url)
        title = self._title(url, soup)
        version_id = _response_version_id(response)
        updated_at = _response_updated_at(response) or canonical
        for element in _find_all_by_local_name(
            soup,
            {
                "base",
                "footer",
                "head",
                "header",
                "link",
                "meta",
                "nav",
                "noscript",
                "script",
                "style",
            },
        ):
            element.decompose()
        for element in _find_all_by_local_name(soup, {"title"}):
            if not _has_ancestor_local_name(element, "svg"):
                element.decompose()

        content_root = (
            _find_by_local_name(soup, "article")
            or _find_by_local_name(soup, "main")
            or _find_by_local_name(soup, "body")
            or soup
        )
        text = "\n".join(
            line.strip()
            for line in content_root.get_text("\n").splitlines()
            if line.strip()
        )
        document_id = f"web:{canonical}"

        return DocumentModel(
            id=document_id,
            document_id=document_id,
            external_id=document_id,
            title=title,
            content=text,
            url=canonical,
            canonical_url=canonical,
            platform="Web",
            source_id="source_web",
            path=canonical,
            updated_at=updated_at,
            version_id=version_id,
        )

    async def _remaining_queue_slots(
        self,
        page_visited: set[str],
        queue: list[str],
        blocked: set[str],
        attempted: set[str],
    ) -> int:
        pending_queue_size = 0
        for url in queue:
            url_key = _crawl_key(url)
            if url_key in blocked or url_key in page_visited or url_key in attempted:
                continue
            pending_queue_size += 1
        return max(0, self.config.web_max_pages - len(page_visited) - pending_queue_size)

    async def _has_unvisited_allowed_queue(
        self,
        queue: list[str],
        page_visited: set[str],
        blocked: set[str],
        attempted: set[str],
    ) -> bool:
        for url in queue:
            url_key = _crawl_key(url)
            if url_key in blocked or url_key in page_visited or url_key in attempted:
                continue
            return True
        return False

    async def _queue_deferred_candidates(
        self,
        deferred_candidates: dict[str, str],
        queue: list[str],
        queued: set[str],
        page_visited: set[str],
        blocked: set[str],
        attempted: set[str],
    ) -> None:
        for url_key, url in list(deferred_candidates.items()):
            if url_key in queued or url_key in page_visited or url_key in blocked:
                deferred_candidates.pop(url_key, None)
                continue
            if url_key in attempted:
                continue
            if not await self._allowed_by_robots(url):
                blocked.add(url_key)
                deferred_candidates.pop(url_key, None)
                self.snapshot_complete = False
                continue
            if await self._remaining_queue_slots(
                page_visited,
                queue,
                blocked,
                attempted,
            ) <= 0:
                continue
            queue.append(url)
            queued.add(url_key)
            deferred_candidates.pop(url_key, None)

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self.config.web_user_agent}

    async def _base_url(self, url: str, soup: BeautifulSoup) -> str:
        head = _document_head(soup)
        if _has_metadata_outside_head(soup, head, "base"):
            self.snapshot_complete = False
        if not head:
            return url
        href = ""
        for base in _find_direct_children_by_local_name(head, {"base"}):
            value = base.get("href")
            if value and str(value).strip():
                href = str(value).strip()
                break
        if not href:
            return url
        candidate = _join_fetch_url(url, href)
        if not candidate:
            self.snapshot_complete = False
            return url
        if not _same_origin(url, candidate):
            self.snapshot_complete = False
            return url
        if not await self._allowed_by_robots(candidate):
            self.snapshot_complete = False
            return url
        return candidate

    async def _canonical_url(self, url: str, soup: BeautifulSoup, *, base_url: str) -> str:
        head = _document_head(soup)
        if _has_metadata_outside_head(soup, head, "link", rel="canonical"):
            self.snapshot_complete = False
        link = None
        if head:
            for candidate_link in _find_direct_children_by_local_name(head, {"link"}):
                if _rel_contains(candidate_link.get("rel"), "canonical"):
                    link = candidate_link
                    break
        href = link.get("href") if link else ""
        fallback = _canonical_url(url)
        if not href or not str(href).strip():
            if link:
                self.snapshot_complete = False
            return fallback
        candidate = _join_fetch_url(base_url, str(href).strip())
        if not candidate:
            self.snapshot_complete = False
            return fallback
        if not _same_origin(url, candidate):
            self.snapshot_complete = False
            return fallback
        if not await self._allowed_by_robots(candidate):
            self.snapshot_complete = False
            return fallback
        if _has_unsupported_media_hint(candidate):
            self.snapshot_complete = False
            return fallback
        return _canonical_url(candidate)

    @staticmethod
    def _title(url: str, soup: BeautifulSoup) -> str:
        heading = _find_by_local_name(soup, "h1")
        if heading and heading.get_text(strip=True):
            return heading.get_text(strip=True)
        title = _find_by_local_name(soup, "title")
        if title and title.get_text(strip=True):
            return title.get_text(strip=True)
        return url


def _normalize_url(url: str) -> str:
    normalized, _ = urldefrag(url.strip())
    parsed = urlparse(normalized)
    path = parsed.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return parsed._replace(path=path).geturl()


def _fetch_url(url: str) -> str:
    normalized, _ = urldefrag(url.strip())
    return normalized


def _seed_url(url: str) -> str:
    try:
        normalized = _fetch_url(url)
    except ValueError as exc:
        raise ValueError(
            f"Invalid website seed URL: {_redact_url_credentials(url)}"
        ) from exc
    if not _valid_fetch_url(normalized):
        raise ValueError(f"Invalid website seed URL: {_redact_url_credentials(normalized)}")
    return normalized


def _join_fetch_url(base_url: str, value: str) -> str:
    try:
        joined_url = _fetch_url(urljoin(base_url, value))
    except ValueError:
        return ""
    if not _valid_fetch_url(joined_url):
        return ""
    return joined_url


def _valid_fetch_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and not _has_url_credentials(parsed)
        and not _has_sensitive_query(parsed)
        and not _contains_credential_like_value(url)
        and _origin_host_port(parsed) is not None
    )


def _canonical_url(url: str) -> str:
    normalized = _normalize_url(url)
    parsed = urlparse(normalized)
    origin = _origin_url(parsed)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{origin}{path}{query}"


def _crawl_key(url: str) -> str:
    return _canonical_url(url)


def _iter_page_links(url: str, soup: BeautifulSoup, *, on_malformed=None):
    seen_links = set()
    for element in soup.descendants:
        if _tag_local_name(element) != "a":
            continue
        href = element.get("href")
        if not href:
            continue
        if _has_non_fetchable_scheme(str(href)):
            continue
        linked_url = _join_fetch_url(url, href)
        if not linked_url:
            if on_malformed:
                on_malformed()
            continue
        if not _same_origin(url, linked_url):
            continue
        linked_key = _crawl_key(linked_url)
        if linked_key in seen_links:
            continue
        seen_links.add(linked_key)
        yield linked_url


def _has_non_fetchable_scheme(value: str) -> bool:
    try:
        scheme = urlparse(value.strip()).scheme.lower()
    except ValueError:
        return False
    return bool(scheme) and scheme not in {"http", "https"}


def _same_origin(left: str, right: str) -> bool:
    try:
        left_parsed = urlparse(left)
        right_parsed = urlparse(right)
    except ValueError:
        return False
    left_origin = _origin_host_port(left_parsed)
    right_origin = _origin_host_port(right_parsed)
    return (
        left_parsed.scheme in {"http", "https"}
        and right_parsed.scheme in {"http", "https"}
        and left_parsed.scheme == right_parsed.scheme
        and left_origin is not None
        and right_origin is not None
        and left_origin == right_origin
    )


def _origin_host_port(parsed) -> tuple[str, int | None] | None:
    if _has_url_credentials(parsed):
        return None
    try:
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return None
        port = parsed.port
    except ValueError:
        return None
    if port is None and parsed.scheme == "http":
        port = 80
    elif port is None and parsed.scheme == "https":
        port = 443
    return hostname, port


def _origin_url(parsed) -> str:
    origin = _origin_host_port(parsed)
    if origin is None:
        raise ValueError(f"Invalid URL origin: {_redact_url_credentials(parsed.geturl())}")
    hostname, port = origin
    default_port = (parsed.scheme == "http" and port == 80) or (
        parsed.scheme == "https" and port == 443
    )
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host if default_port or port is None else f"{host}:{port}"
    return f"{parsed.scheme}://{netloc}"


def _has_url_credentials(parsed) -> bool:
    return bool(parsed.username or parsed.password)


def _has_sensitive_query(parsed) -> bool:
    return any(
        _is_sensitive_query_key(key) or _contains_credential_like_value(value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    )


def _is_sensitive_query_key(key: str) -> bool:
    for variant in _decoded_variants(key.strip()):
        normalized = variant.lower().replace("-", "_")
        compact = re.sub(r"[^a-z0-9]", "", variant.lower())
        if (
            normalized in SENSITIVE_QUERY_KEYS
            or normalized.endswith("_token")
            or normalized.endswith("_session")
            or normalized.endswith("_cookie")
            or normalized.endswith("_jwt")
            or normalized.endswith("_csrf")
            or normalized.endswith("_xsrf")
            or normalized.endswith("_secret")
            or normalized.endswith("_key")
            or normalized.endswith("_signature")
            or normalized.endswith("_credential")
            or "access_key" in normalized
            or compact in {
                "accesskey",
                "accesskeyid",
                "awsaccesskeyid",
                "cookie",
                "csrf",
                "csrftoken",
                "jwt",
                "jwttoken",
                "jsessionid",
                "phpsessid",
                "session",
                "sessionid",
                "sessiontoken",
                "sid",
                "xsrf",
                "xsrftoken",
            }
            or compact.endswith("token")
            or compact.endswith("session")
            or compact.endswith("sessionid")
            or compact.endswith("cookie")
            or compact.endswith("csrf")
            or compact.endswith("xsrf")
            or compact.endswith("jwt")
            or compact.endswith("accesskey")
            or compact.endswith("accesskeyid")
        ):
            return True
    return False


def _contains_credential_like_value(value: str) -> bool:
    return any(
        CREDENTIAL_LIKE_RE.search(variant)
        or _contains_sensitive_key_marker(variant)
        or _contains_sensitive_path_segment(variant)
        for variant in _decoded_variants(value)
    )


def _contains_sensitive_key_marker(value: str) -> bool:
    return bool(SENSITIVE_ASSIGNMENT_KEY_RE.search(value))


def _contains_sensitive_path_segment(value: str) -> bool:
    segments = [segment for segment in re.split(r"[\\/]+", value) if segment]
    for index, segment in enumerate(segments[:-1]):
        if _is_sensitive_path_segment_key(segment) and segments[index + 1]:
            return True
    return False


def _is_sensitive_path_segment_key(value: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", value.lower())
    return compact in SENSITIVE_PATH_SEGMENT_KEYS


def _decoded_variants(value: str) -> tuple[str, ...]:
    variants = [value]
    current = value
    for _ in range(3):
        decoded = unquote(current)
        if decoded == current:
            break
        variants.append(decoded)
        current = decoded
    return tuple(variants)


def _redact_url_credentials(url: str) -> str:
    if not url:
        return url
    if _contains_credential_like_value(url):
        return "<redacted>"
    try:
        parsed = urlparse(url)
    except ValueError:
        return _redact_raw_url_credentials(url)
    if "@" not in parsed.netloc:
        redacted = parsed._replace(
            query=_redact_query_secrets(parsed.query),
            fragment=_redact_fragment(parsed.fragment),
        ).geturl()
        if "@" in redacted and not parsed.netloc:
            return _redact_raw_url_credentials(redacted)
        return redacted
    host_part = parsed.netloc.rsplit("@", 1)[1]
    return parsed._replace(
        netloc=f"<credentials>@{host_part}",
        query=_redact_query_secrets(parsed.query),
        fragment=_redact_fragment(parsed.fragment),
    ).geturl()


def _redact_query_secrets(query: str) -> str:
    if not query:
        return query
    return "redacted"


def _redact_fragment(fragment: str) -> str:
    return "<redacted>" if fragment else fragment


def _redact_raw_url_credentials(url: str) -> str:
    scheme_end = url.find("://")
    authority_start = scheme_end + 3 if scheme_end != -1 else 0
    at_index = url.find("@", authority_start)
    redacted = url
    if at_index != -1:
        redacted = f"{url[:authority_start]}<credentials>@{url[at_index + 1:]}"
    return _redact_raw_query_secrets(redacted)


def _redact_raw_query_secrets(url: str) -> str:
    query_start = url.find("?")
    fragment_start = url.find("#")
    if query_start == -1:
        if fragment_start == -1:
            return url
        return f"{url[:fragment_start]}#<redacted>"
    fragment_start = url.find("#", query_start)
    query_end = len(url) if fragment_start == -1 else fragment_start
    query = url[query_start + 1 : query_end]
    redacted_query = _redact_query_secrets(query)
    redacted_url = f"{url[:query_start + 1]}{redacted_query}{url[query_end:]}"
    redacted_fragment_start = redacted_url.find("#", query_start)
    if redacted_fragment_start == -1:
        return redacted_url
    return f"{redacted_url[:redacted_fragment_start]}#<redacted>"


def _looks_like_sitemap(url: str, body: str) -> bool:
    lowered_path = urlparse(url).path.lower().rstrip("/")
    stripped = _strip_leading_bom(body).lstrip().lower()
    root_name = _markup_root_name(body)
    return (
        (
            (lowered_path.endswith("/sitemap") or lowered_path.endswith("/sitemap.xml"))
            and _looks_like_xml(url, body)
        )
        or root_name in {"urlset", "sitemapindex"}
        or _has_sitemap_root(body)
        or (
            stripped.startswith("<?xml")
            and root_name in {"urlset", "sitemapindex"}
        )
    )


def _looks_like_xml(url: str, body: str) -> bool:
    stripped = _strip_leading_bom(body).lstrip().lower()
    return stripped.startswith("<?xml") or urlparse(url).path.lower().endswith(".xml")


def _supports_page_content(response: FetchResponse) -> bool:
    content_type = _content_type(response.headers)
    extension = _url_extension(response.url)
    if _response_looks_like_binary_media(response):
        return False
    if _has_unsupported_media_hint(response.url):
        return _body_looks_like_html_page(response.text)
    if not content_type:
        if extension in SUPPORTED_PAGE_EXTENSIONS:
            return True
        return _body_looks_like_html_page(response.text)
    if content_type == "text/html":
        return True
    if content_type == "application/xhtml+xml":
        return _body_looks_like_html_page(response.text, allow_fragments=True)
    if content_type == "text/plain" and _markup_root_name(response.text):
        return _body_looks_like_html_page(response.text)
    return content_type in SUPPORTED_PAGE_CONTENT_TYPES


def _explicit_html_response_has_unsupported_xml_root(response: FetchResponse) -> bool:
    content_type = _content_type(response.headers)
    root_name = _markup_root_name(response.text)
    if content_type == "application/xhtml+xml":
        return not _body_looks_like_html_page(response.text, allow_fragments=True)
    if content_type == "text/html" and root_name in {"urlset", "sitemapindex"}:
        return True
    if content_type == "text/html" and _looks_like_xml(response.url, response.text):
        return not _body_looks_like_html_page(response.text, allow_fragments=True)
    return False


def _is_supported_xhtml_response(response: FetchResponse) -> bool:
    return (
        _content_type(response.headers) == "application/xhtml+xml"
        and _supports_page_content(response)
    )


def _response_disables_stale_cleanup(response: FetchResponse) -> bool:
    content_type = _content_type(response.headers)
    extension = _url_extension(response.url)
    if _has_unsupported_media_hint(response.url):
        return True
    if content_type == "text/html":
        return False
    if content_type == "application/xhtml+xml":
        return not _body_looks_like_html_page(response.text, allow_fragments=True)
    if content_type in SUPPORTED_PAGE_CONTENT_TYPES:
        return True
    if not content_type and extension in SUPPORTED_PAGE_EXTENSIONS:
        if extension not in {".htm", ".html"}:
            return True
        return not _body_looks_like_html_page(response.text, allow_fragments=True)
    return False


def _response_version_id(response: FetchResponse) -> str:
    return _safe_response_header_value(response.headers, "etag") or _response_updated_at(response)


def _response_updated_at(response: FetchResponse) -> str:
    return _safe_response_header_value(response.headers, "last-modified")


def _safe_response_header_value(headers: dict[str, str], name: str) -> str:
    value = _header_value(headers, name)
    if (
        not value
        or _contains_disallowed_control_text(value)
        or _contains_credential_like_value(value)
    ):
        return ""
    normalized_name = name.lower()
    if normalized_name == "etag" and not _valid_etag(value):
        return ""
    if normalized_name == "last-modified" and not _valid_http_date(value):
        return ""
    return value


def _valid_etag(value: str) -> bool:
    return bool(SAFE_ETAG_RE.fullmatch(value))


def _valid_http_date(value: str) -> bool:
    try:
        return parsedate_to_datetime(value) is not None
    except (TypeError, ValueError, IndexError, OverflowError):
        return False


def _header_value(headers: dict[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name:
            return value.strip()
    return ""


def _content_length_exceeds(headers: dict[str, str], max_response_bytes: int) -> bool:
    content_length = ""
    for key, value in headers.items():
        if key.lower() == "content-length":
            content_length = value.strip()
            break
    if not content_length:
        return False
    try:
        return int(content_length) > max_response_bytes
    except ValueError:
        return False


def _should_read_response_body(url: str, headers: dict[str, str]) -> bool:
    content_type = _content_type(headers)
    return not content_type or content_type in READABLE_RESPONSE_CONTENT_TYPES


def _decode_response_body(body: bytes, encoding: str | None) -> tuple[str, bool]:
    try:
        return body.decode(encoding or "utf-8"), False
    except (LookupError, UnicodeDecodeError):
        return "", True


def _url_extension(url: str) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path).lower()
    filename = path.rsplit("/", 1)[-1]
    if "." not in filename:
        return ""
    return f".{filename.rsplit('.', 1)[-1]}"


def _has_unsupported_media_hint(url: str) -> bool:
    extension = _url_extension(url)
    if extension in UNSUPPORTED_PAGE_EXTENSIONS:
        return True
    return _query_extension(url) in UNSUPPORTED_PAGE_EXTENSIONS


def _query_extension(url: str) -> str:
    parsed = urlparse(url)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        for part in (key, value):
            part = unquote(part).lower()
            filename = part.rsplit("/", 1)[-1]
            if "." in filename:
                extension = f".{filename.rsplit('.', 1)[-1]}"
                if extension in UNSUPPORTED_PAGE_EXTENSIONS:
                    return extension
    return ""


def _response_looks_like_binary_media(response: FetchResponse) -> bool:
    return _body_prefix_looks_like_binary_media(
        response.body_prefix
    ) or _body_looks_like_textual_media(response.text) or _body_looks_like_binary_media(
        response.text
    )


def _body_prefix_looks_like_binary_media(prefix: bytes) -> bool:
    stripped = prefix.lstrip(b"\xef\xbb\xbf\r\n\t ")
    lowered = stripped.lower()
    control_bytes = [
        byte
        for byte in prefix[:256]
        if (byte < 32 and byte not in {9, 10, 13}) or byte == 127
    ]
    return (
        stripped.startswith(b"%PDF-")
        or stripped.startswith(b"\x89PNG")
        or stripped.startswith(b"GIF87a")
        or stripped.startswith(b"GIF89a")
        or stripped.startswith(b"\xff\xd8\xff")
        or stripped.startswith(b"PK\x03\x04")
        or stripped.startswith(b"\x1f\x8b")
        or stripped.startswith(b"BZh")
        or stripped.startswith(b"MZ")
        or stripped.startswith(b"BM")
        or stripped.startswith(b"\xd0\xcf\x11\xe0")
        or stripped.startswith(b"\xfd7zXZ\x00")
        or stripped.startswith(b"\x1aE\xdf\xa3")
        or stripped.startswith(b"\xff\xfb")
        or stripped.startswith(b"\xff\xf3")
        or stripped.startswith(b"\xff\xf2")
        or lowered.startswith(b"rar!")
        or lowered.startswith(b"7z")
        or lowered.startswith(b"id3")
        or (len(stripped) >= 12 and stripped.startswith(b"RIFF") and stripped[8:12] == b"WEBP")
        or (len(stripped) >= 8 and stripped[4:8] == b"ftyp")
        or (len(prefix) >= 262 and prefix[257:262] == b"ustar")
        or b"\x00" in prefix[:256]
        or len(control_bytes) >= 3
    )


def _body_looks_like_textual_media(body: str) -> bool:
    return _markup_root_name(body) in {"feed", "opml", "rdf", "rss", "svg"}


def _body_looks_like_binary_media(body: str) -> bool:
    stripped = body.lstrip("\ufeff\r\n\t ")
    lowered = stripped.lower()
    return (
        stripped.startswith("%PDF-")
        or stripped.startswith("\x89PNG")
        or stripped.startswith("GIF87a")
        or stripped.startswith("GIF89a")
        or stripped.startswith("PK\x03\x04")
        or stripped.startswith("BZh")
        or stripped.startswith("MZ")
        or stripped.startswith("BM")
        or stripped.startswith("\x1aEߣ")
        or stripped.startswith("���")
        or lowered.startswith("rar!")
        or lowered.startswith("7z")
        or lowered.startswith("id3")
        or (len(stripped) >= 262 and stripped[257:262] == "ustar")
        or _contains_disallowed_control_text(stripped)
    )


def _contains_disallowed_control_text(value: str) -> bool:
    return any(
        (ord(character) < 32 and character not in {"\t", "\n", "\r"})
        or ord(character) == 127
        for character in value
    )


def _markup_root_name(body: str) -> str:
    stripped = _strip_markup_preamble(body)
    match = re.match(r"<\s*([A-Za-z_][\w:.-]*)\b", stripped)
    if not match:
        return ""
    return match.group(1).rsplit(":", 1)[-1].lower()


def _strip_markup_preamble(body: str) -> str:
    stripped = _strip_leading_bom(body).lstrip()
    for _ in range(20):
        lowered = stripped.lower()
        if lowered.startswith("<?"):
            end = stripped.find("?>")
            if end == -1:
                return stripped
            stripped = stripped[end + 2 :].lstrip()
            continue
        if lowered.startswith("<!--"):
            end = stripped.find("-->")
            if end == -1:
                return stripped
            stripped = stripped[end + 3 :].lstrip()
            continue
        if lowered.startswith("<!doctype"):
            end = stripped.find(">")
            if end == -1:
                return stripped
            stripped = stripped[end + 1 :].lstrip()
            continue
        break
    return stripped


def _body_looks_like_html_page(body: str, *, allow_fragments: bool = False) -> bool:
    leading = _strip_leading_bom(body).lstrip().lower()
    root_name = _markup_root_name(body)
    if root_name:
        html_roots = HTML_FRAGMENT_ROOTS if allow_fragments else HTML_DOCUMENT_ROOTS
        return root_name in html_roots or (
            leading.startswith("<!doctype html") and root_name in html_roots
        )
    return leading.startswith("<!doctype html")


def _content_type(headers: dict[str, str]) -> str:
    for key, value in headers.items():
        if key.lower() == "content-type":
            return value.split(";", 1)[0].strip().lower()
    return ""


def _find_by_local_name(soup: BeautifulSoup, name: str):
    return soup.find(lambda tag: _tag_local_name(tag) == name)


def _find_all_by_local_name(soup: BeautifulSoup, names: set[str]):
    return soup.find_all(lambda tag: _tag_local_name(tag) in names)


def _find_direct_children_by_local_name(tag, names: set[str]):
    return [
        child
        for child in getattr(tag, "children", ())
        if _tag_local_name(child) in names
    ]


def _document_head(soup: BeautifulSoup):
    for head in _find_all_by_local_name(soup, {"head"}):
        parent = getattr(head, "parent", None)
        parent_name = _tag_local_name(parent)
        if parent_name == "html":
            if not _is_document_root_child(parent):
                continue
            if _has_previous_content_sibling(parent) or _has_previous_content_sibling(head):
                continue
            return head
        if isinstance(parent, BeautifulSoup):
            if _has_previous_content_sibling(head):
                continue
            return head
    return None


def _is_document_root_child(tag) -> bool:
    return isinstance(getattr(tag, "parent", None), BeautifulSoup)


def _tag_local_name(tag) -> str:
    name = getattr(tag, "name", "") or ""
    return name.rsplit(":", 1)[-1].lower()


def _has_metadata_outside_head(
    soup: BeautifulSoup,
    head,
    name: str,
    *,
    rel: str | None = None,
) -> bool:
    for tag in soup.find_all(lambda item: _tag_local_name(item) == name):
        if rel and not _rel_contains(tag.get("rel"), rel):
            continue
        if head and _is_direct_child_of(tag, head):
            continue
        return True
    return False


def _is_direct_child_of(tag, parent) -> bool:
    return getattr(tag, "parent", None) is parent


def _has_ancestor_local_name(tag, name: str) -> bool:
    current = getattr(tag, "parent", None)
    while current is not None:
        if _tag_local_name(current) == name:
            return True
        current = getattr(current, "parent", None)
    return False


def _has_previous_content_sibling(tag) -> bool:
    for sibling in getattr(tag, "previous_siblings", ()):
        sibling_name = _tag_local_name(sibling)
        if sibling_name:
            return True
        if isinstance(sibling, (Comment, Declaration, Doctype, ProcessingInstruction)):
            continue
        if isinstance(sibling, NavigableString) and str(sibling).strip():
            return True
    return False


def _rel_contains(value, expected: str) -> bool:
    if not value:
        return False
    if isinstance(value, str):
        return expected in value.lower().split()
    return any(str(item).lower() == expected for item in value)


def _local_name(tag: str) -> str:
    return _split_tag(tag)[1]


def _split_tag(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        namespace, _, local_name = tag[1:].partition("}")
        return namespace, local_name.lower()
    return "", tag.lower()


def _sitemap_tag_matches(tag: str, local_name: str, root_namespace: str) -> bool:
    namespace, actual_name = _split_tag(tag)
    return actual_name == local_name and namespace == root_namespace


def _has_sitemap_root(body: str) -> bool:
    body = _strip_leading_bom(body)
    if not body.lstrip().startswith("<"):
        return False
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return False
    namespace, root_name = _split_tag(root.tag)
    return namespace in ALLOWED_SITEMAP_NAMESPACES and root_name in {
        "urlset",
        "sitemapindex",
    }


def _strip_leading_bom(text: str) -> str:
    return text.lstrip("\ufeff")


def _user_agent_token(user_agent: str) -> str:
    token = user_agent.split("/", 1)[0].strip().lower()
    return token or "contextwikibot"


def _select_robots_rules(
    groups: list[tuple[list[str], list[tuple[str, str]]]],
    agent_token: str,
    user_agent_lower: str,
) -> list[tuple[str, str]]:
    matches: list[tuple[int, list[tuple[str, str]]]] = []
    for agents, rules in groups:
        score = max(
            (
                len(agent)
                for agent in agents
                if agent != "*"
                and (agent_token.startswith(agent) or user_agent_lower.startswith(agent))
            ),
            default=-1,
        )
        if score >= 0:
            matches.append((score, rules))

    if not matches:
        matches = [
            (0, rules)
            for agents, rules in groups
            if any(agent == "*" for agent in agents)
        ]
    if not matches:
        return []

    best_score = max(score for score, _ in matches)
    selected: list[str] = []
    for score, rules in matches:
        if score == best_score:
            selected.extend(rules)
    return selected


def _robots_rule_matches(path: str, rule_path: str) -> bool:
    anchored = rule_path.endswith("$")
    pattern = rule_path[:-1] if anchored else rule_path
    regex = "^" + ".*".join(re.escape(part) for part in pattern.split("*"))
    if anchored:
        regex += "$"
    return re.match(regex, path) is not None
