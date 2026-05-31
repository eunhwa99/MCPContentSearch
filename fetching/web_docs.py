import asyncio
from dataclasses import dataclass, field
import re
from urllib.parse import unquote, urlparse
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
from fetching.web_media import (
    content_type_header as _content_type,
    explicit_html_response_has_unsupported_xml_root as _explicit_html_response_has_unsupported_xml_root,
    has_unsupported_media_hint as _has_unsupported_media_hint,
    looks_like_xml as _looks_like_xml,
    markup_root_name as _markup_root_name,
    response_disables_stale_cleanup as _response_disables_stale_cleanup,
    should_read_response_body as _should_read_response_body,
    strip_leading_bom as _strip_leading_bom,
    supports_page_content as _supports_page_content,
)
from fetching.web_safety import (
    canonical_url as _canonical_url,
    crawl_key as _crawl_key,
    fetch_url as _fetch_url,
    has_non_fetchable_scheme as _has_non_fetchable_scheme,
    join_fetch_url as _join_fetch_url,
    origin_url as _origin_url,
    redact_url_credentials as _redact_url_credentials,
    safe_response_header_value as _safe_response_header_value,
    same_origin as _same_origin,
    seed_url as _seed_url,
)


SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
ALLOWED_SITEMAP_NAMESPACES = {"", SITEMAP_NAMESPACE}


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


def _response_version_id(response: FetchResponse) -> str:
    return _safe_response_header_value(response.headers, "etag") or _response_updated_at(response)


def _response_updated_at(response: FetchResponse) -> str:
    return _safe_response_header_value(response.headers, "last-modified")


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


def _decode_response_body(body: bytes, encoding: str | None) -> tuple[str, bool]:
    try:
        return body.decode(encoding or "utf-8"), False
    except (LookupError, UnicodeDecodeError):
        return "", True


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
