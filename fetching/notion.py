import asyncio
import inspect
import logging
import re
import time
from typing import List, Optional
from urllib.parse import urlparse

import httpx

from environments.config import AppConfig, NotionConfig
from core.models import DocumentModel
from core.exceptions import APIError, FetchError
from indexing.background_tasks import safe_error_message

logger = logging.getLogger(__name__)

NOTION_OBJECT_ID_RE = re.compile(
    r"(?i)([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})"
)
NOTION_COMPACT_OBJECT_ID_RE = re.compile(r"(?i)([0-9a-f]{32})$")
NOTION_HYPHENATED_OBJECT_ID_RE = re.compile(
    r"(?i)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
NOTION_HOST_SUFFIXES = ("notion.so", "notion.site")
NOTION_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
NOTION_MAX_RETRY_ATTEMPTS = 3
NOTION_STOP_POLL_INTERVAL_SECONDS = 0.1


class _StopRequested(Exception):
    pass


async def _should_stop(stop_checker) -> bool:
    if stop_checker is None:
        return False
    try:
        result = stop_checker()
        if inspect.isawaitable(result):
            result = await result
    except _StopRequested:
        raise
    except Exception:
        logger.debug("Ignoring stop checker failure")
        return False
    return bool(result)


async def _raise_if_stop_requested(stop_checker) -> None:
    if await _should_stop(stop_checker):
        raise _StopRequested


async def _drain_request_task(task: asyncio.Task) -> None:
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def _await_request_with_stop(request_coro, stop_checker):
    if stop_checker is None:
        return await request_coro
    request_task = asyncio.create_task(request_coro)
    try:
        while True:
            done, _ = await asyncio.wait(
                {request_task},
                timeout=NOTION_STOP_POLL_INTERVAL_SECONDS,
            )
            if request_task in done:
                await _raise_if_stop_requested(stop_checker)
                return await request_task
            await _raise_if_stop_requested(stop_checker)
    except _StopRequested:
        request_task.cancel()
        await _drain_request_task(request_task)
        raise
    except asyncio.CancelledError:
        request_task.cancel()
        await _drain_request_task(request_task)
        raise


async def _sleep_with_stop(delay_seconds: float, stop_checker) -> None:
    remaining = max(0.0, float(delay_seconds))
    if stop_checker is None:
        await asyncio.sleep(remaining)
        return
    while remaining > 0:
        await _raise_if_stop_requested(stop_checker)
        step = min(remaining, NOTION_STOP_POLL_INTERVAL_SECONDS)
        await asyncio.sleep(step)
        remaining -= step
    await _raise_if_stop_requested(stop_checker)


class NotionAPIClient:
    def __init__(self, config: NotionConfig, app_config: AppConfig):
        self.config = config
        self.app_config = app_config
        self.headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Notion-Version": config.api_version,
            "Content-Type": "application/json",
        }
    
    async def search_pages(
        self,
        client: httpx.AsyncClient,
        progress_callback=None,
        progress_stop_signal=None,
        progress_stop_checker=None,
    ) -> List[dict]:
        pages = []
        next_cursor = None
        batch_index = 0
        
        while True:
            await _raise_if_stop_requested(progress_stop_checker)
            payload = self._build_search_payload(next_cursor)

            data = await self._request_json(
                client,
                "post",
                f"{self.config.base_url}/search",
                json=payload,
                stop_checker=progress_stop_checker,
            )

            pages.extend(data.get("results", []))
            batch_index += 1
            await _raise_if_stop_requested(progress_stop_checker)
            if await _emit_progress(
                progress_callback,
                {
                    "event": "search_page_batch_completed",
                    "batch_index": batch_index,
                    "pages_discovered": len(pages),
                    "has_more": bool(data.get("has_more", False)),
                },
                stop_signal=progress_stop_signal,
            ):
                raise _StopRequested

            if not data.get("has_more", False):
                break

            next_cursor = data.get("next_cursor")
            await _raise_if_stop_requested(progress_stop_checker)
        await _raise_if_stop_requested(progress_stop_checker)
        return pages
    
    async def fetch_block_content(
        self, 
        client: httpx.AsyncClient, 
        block_id: str,
        depth: int = 0,
        strict: bool = False,
        stop_checker=None,
    ) -> str:
        """블록 컨텐츠 재귀 추출"""
        if depth > self.app_config.notion_max_depth:
            logger.warning("Max Notion block depth reached; skipping nested content")
            return ""
        
        try:
            await _raise_if_stop_requested(stop_checker)
            blocks = await self._fetch_blocks(client, block_id, stop_checker=stop_checker)
            await _raise_if_stop_requested(stop_checker)
            return await self._extract_text_recursive(
                client,
                blocks,
                depth,
                strict,
                stop_checker=stop_checker,
            )
        except _StopRequested:
            raise
        except Exception as e:
            if strict:
                raise
            logger.debug(f"Failed to fetch block {block_id}: {e}")
            return ""
    
    async def _fetch_blocks(
        self,
        client: httpx.AsyncClient,
        block_id: str,
        stop_checker=None,
    ) -> List[dict]:
        """페이지네이션 지원 블록 가져오기"""
        all_blocks = []
        next_cursor = None
        
        while True:
            await _raise_if_stop_requested(stop_checker)
            params = {"page_size": self.app_config.notion_page_size}
            if next_cursor:
                params["start_cursor"] = next_cursor

            data = await self._request_json(
                client,
                "get",
                f"{self.config.base_url}/blocks/{block_id}/children",
                params=params,
                stop_checker=stop_checker,
            )

            all_blocks.extend(data.get("results", []))

            if not data.get("has_more", False):
                break

            next_cursor = data.get("next_cursor")
        
        return all_blocks

    async def fetch_page(self, client: httpx.AsyncClient, page_id: str, stop_checker=None) -> dict:
        return await self._request_json(
            client,
            "get",
            f"{self.config.base_url}/pages/{page_id}",
            stop_checker=stop_checker,
        )

    async def query_database(self, client: httpx.AsyncClient, database_id: str, stop_checker=None) -> List[dict]:
        pages = []
        next_cursor = None

        while True:
            await _raise_if_stop_requested(stop_checker)
            payload = {"page_size": 100}
            if next_cursor:
                payload["start_cursor"] = next_cursor

            data = await self._request_json(
                client,
                "post",
                f"{self.config.base_url}/databases/{database_id}/query",
                json=payload,
                stop_checker=stop_checker,
            )

            pages.extend(data.get("results", []))
            if not data.get("has_more", False):
                break
            next_cursor = data.get("next_cursor")

        return pages

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        stop_checker=None,
        **kwargs,
    ) -> dict:
        request = getattr(client, method)
        for attempt in range(1, NOTION_MAX_RETRY_ATTEMPTS + 1):
            await _raise_if_stop_requested(stop_checker)
            try:
                response = await _await_request_with_stop(
                    request(
                        url,
                        headers=self.headers,
                        timeout=self.app_config.request_timeout,
                        **kwargs,
                    ),
                    stop_checker,
                )
                await _raise_if_stop_requested(stop_checker)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code
                if (
                    status_code in NOTION_TRANSIENT_STATUS_CODES
                    and attempt < NOTION_MAX_RETRY_ATTEMPTS
                ):
                    await _sleep_with_stop(
                        self._retry_delay_seconds(attempt, e.response),
                        stop_checker,
                    )
                    continue
                raise APIError("Notion", status_code, self._error_message(e))
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if attempt < NOTION_MAX_RETRY_ATTEMPTS:
                    await _sleep_with_stop(
                        self._retry_delay_seconds(attempt),
                        stop_checker,
                    )
                    continue
                raise APIError("Notion", 0, str(e))

        raise APIError("Notion", 0, "Notion request retry loop exited unexpectedly")

    @staticmethod
    def _retry_delay_seconds(attempt: int, response: httpx.Response | None = None) -> float:
        if response is not None and response.status_code == 429:
            retry_after = (response.headers.get("Retry-After") or "").strip()
            try:
                if retry_after:
                    return min(max(0.0, float(retry_after)), 10.0)
            except ValueError:
                pass
        return min(2 ** attempt, 10)

    @staticmethod
    def _error_message(error: httpx.HTTPStatusError) -> str:
        try:
            payload = error.response.json()
        except Exception:
            payload = {}
        code = str(payload.get("code") or "").strip()
        message = str(payload.get("message") or "").strip()
        if code and message:
            return f"{code} | {message}"
        if message:
            return message
        if code:
            return code
        return str(error)
    
    async def _extract_text_recursive(
        self,
        client: httpx.AsyncClient,
        blocks: List[dict],
        depth: int,
        strict: bool = False,
        stop_checker=None,
    ) -> str:
        """재귀적 텍스트 추출"""
        content_parts = []
        
        for block in blocks:
            await _raise_if_stop_requested(stop_checker)
            block_type = block.get("type")
            
            if block_type in self.config.supported_block_types:
                text_array = block.get(block_type, {}).get("rich_text", [])
                content_parts.extend(
                    obj.get("plain_text", "") 
                    for obj in text_array 
                    if obj.get("plain_text")
                )
            
            if block.get("has_children", False):
                child_content = await self.fetch_block_content(
                    client,
                    block["id"],
                    depth + 1,
                    strict=strict,
                    stop_checker=stop_checker,
                )
                if child_content:
                    content_parts.append(child_content)
        
        return " ".join(content_parts).strip()
    
    @staticmethod
    def _build_search_payload(cursor: Optional[str] = None) -> dict:
        """검색 페이로드 생성"""
        payload = {
            "filter": {"property": "object", "value": "page"},
            "page_size": 100
        }
        if cursor:
            payload["start_cursor"] = cursor
        return payload


class NotionPageProcessor:
    """Notion 페이지 처리기"""
    
    def __init__(self, config: NotionConfig):
        self.config = config
    
    def extract_title(self, properties: dict) -> str:
        """제목 추출"""
        for prop_name in self.config.title_property_names:
            if prop_name not in properties:
                continue
            
            title_data = properties[prop_name].get("title", [])
            if title_data:
                return title_data[0].get("plain_text", "Untitled")
        
        return "Untitled"
    
    def build_document(self, page: dict, content: str) -> DocumentModel:
        """DocumentModel 생성"""
        page_id = page["id"]
        return DocumentModel(
            id=f"notion_{page_id}",
            document_id=page_id,
            external_id=page_id,
            platform="Notion",
            title=self.extract_title(page.get("properties", {})),
            content=content,
            url=page.get("url", ""),
            canonical_url=page.get("url", ""),
            date=page.get("created_time", ""),
            updated_at=page.get("last_edited_time", page.get("created_time", "")),
            published_at=page.get("created_time", ""),
            modified_at=page.get("last_edited_time", page.get("created_time", "")),
            date_provenance="notion",
        )


async def _emit_progress(progress_callback, event: dict, *, stop_signal=None) -> bool:
    if progress_callback is None:
        return False
    try:
        result = progress_callback(event)
        if inspect.isawaitable(result):
            result = await result
    except _StopRequested:
        raise
    except Exception:
        logger.debug("Ignoring progress callback failure")
        return False
    return stop_signal is not None and result is stop_signal


async def fetch_notion_pages(
    api_key: str,
    app_config: AppConfig,
    progress_callback=None,
    progress_stop_signal=None,
    progress_stop_checker=None,
) -> List[DocumentModel]:
    """Notion 페이지 가져오기"""
    if not api_key:
        logger.warning("NOTION_API_KEY not set. Skipping.")
        return []
    
    notion_config = NotionConfig(api_key=api_key)
    api_client = NotionAPIClient(notion_config, app_config)
    processor = NotionPageProcessor(notion_config)
    
    documents = []
    
    try:
        async with httpx.AsyncClient(timeout=app_config.request_timeout) as client:
            await _raise_if_stop_requested(progress_stop_checker)
            if await _emit_progress(
                progress_callback,
                {"event": "search_started"},
                stop_signal=progress_stop_signal,
            ):
                raise _StopRequested
            try:
                raw_pages = await api_client.search_pages(
                    client,
                    progress_callback=progress_callback,
                    progress_stop_signal=progress_stop_signal,
                    progress_stop_checker=progress_stop_checker,
                )
            except _StopRequested:
                raise
            logger.info(f"Found {len(raw_pages)} Notion pages")
            await _raise_if_stop_requested(progress_stop_checker)
            if await _emit_progress(
                progress_callback,
                {
                    "event": "search_completed",
                    "total_pages": len(raw_pages),
                },
                stop_signal=progress_stop_signal,
            ):
                raise _StopRequested
            
            for idx, page in enumerate(raw_pages, 1):
                await _raise_if_stop_requested(progress_stop_checker)
                page_id = page["id"]
                title = processor.extract_title(page.get("properties", {}))
                if await _emit_progress(
                    progress_callback,
                    {
                        "event": "page_fetch_started",
                        "current_page": idx,
                        "total_pages": len(raw_pages),
                        "page_id": page_id,
                        "title": title,
                    },
                    stop_signal=progress_stop_signal,
                ):
                    raise _StopRequested
                started_at = time.monotonic()
                try:
                    content = await api_client.fetch_block_content(
                        client,
                        page_id,
                        strict=True,
                        stop_checker=progress_stop_checker,
                    )
                except _StopRequested:
                    raise
                except Exception:
                    logger.error(
                        "Notion page fetch failed at %s/%s",
                        idx,
                        len(raw_pages),
                    )
                    raise
                document = processor.build_document(page, content)
                documents.append(document)
                await _raise_if_stop_requested(progress_stop_checker)
                if await _emit_progress(
                    progress_callback,
                    {
                        "event": "page_fetch_completed",
                        "current_page": idx,
                        "total_pages": len(raw_pages),
                        "page_id": page_id,
                        "title": title,
                        "elapsed_seconds": time.monotonic() - started_at,
                    },
                    stop_signal=progress_stop_signal,
                ):
                    raise _StopRequested
                
                if idx % 10 == 0:
                    logger.info(f"Progress: {idx}/{len(raw_pages)}")
            
            logger.info(f"✅ Complete: {len(documents)} pages")

    except APIError as e:
        logger.error("Notion API error: %s", safe_error_message(e))
        raise
    except _StopRequested:
        raise
    except Exception as e:
        message = safe_error_message(e)
        logger.error("Unexpected error: %s", message)
        raise FetchError(f"Failed to fetch Notion pages: {message}")
    
    return documents


async def fetch_notion_target(
    api_key: str,
    app_config: AppConfig,
    target: str,
) -> List[DocumentModel]:
    """Fetch one Notion page URL/id or database URL/id using the configured token."""
    if not api_key:
        raise FetchError("NOTION_API_KEY is required for Notion target sync")

    object_id = parse_notion_object_id(target)
    notion_config = NotionConfig(api_key=api_key)
    api_client = NotionAPIClient(notion_config, app_config)
    processor = NotionPageProcessor(notion_config)

    try:
        async with httpx.AsyncClient(timeout=app_config.request_timeout) as client:
            page_error: APIError | None = None
            try:
                page = await api_client.fetch_page(client, object_id)
                content = await api_client.fetch_block_content(client, object_id, strict=True)
                return [_notion_source_document(processor.build_document(page, content))]
            except APIError as exc:
                page_error = exc
                if page_error.status_code != 404:
                    raise

            try:
                pages = await api_client.query_database(client, object_id)
            except APIError as database_error:
                if page_error is not None and database_error.status_code in {400, 404}:
                    raise page_error
                raise database_error
            documents = []
            for page in pages:
                page_id = page["id"]
                content = await api_client.fetch_block_content(client, page_id, strict=True)
                documents.append(_notion_source_document(processor.build_document(page, content)))
            return documents
    except APIError:
        raise
    except Exception as e:
        logger.error("Unexpected Notion target error")
        raise FetchError(f"Failed to fetch Notion target: {e}")


def parse_notion_object_id(value: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError("Notion target is required")
    if "://" in normalized:
        parsed = urlparse(normalized)
        host = (parsed.hostname or "").lower()
        if parsed.username or parsed.password or not any(
            host == suffix or host.endswith(f".{suffix}") for suffix in NOTION_HOST_SUFFIXES
        ):
            raise ValueError("Invalid Notion URL")
        candidate = parsed.path.strip("/").split("/")[-1]
    else:
        candidate = normalized
    match = NOTION_HYPHENATED_OBJECT_ID_RE.search(candidate)
    if not match:
        match = NOTION_COMPACT_OBJECT_ID_RE.search(candidate)
    if not match:
        match = NOTION_OBJECT_ID_RE.fullmatch(candidate)
    if not match:
        raise ValueError("Notion URL or id did not include a page/database id")
    return _format_notion_uuid(match.group(1).replace("-", ""))


def _format_notion_uuid(value: str) -> str:
    compact = value.lower().replace("-", "")
    if len(compact) != 32 or not re.fullmatch(r"[0-9a-f]{32}", compact):
        raise ValueError("Invalid Notion id")
    return (
        f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-"
        f"{compact[16:20]}-{compact[20:32]}"
    )


def _notion_source_document(document: DocumentModel) -> DocumentModel:
    document_id = document.external_id or document.document_id or document.id
    return document.model_copy(
        update={
            "source_id": "source_notion",
            "document_id": document_id,
            "external_id": document_id,
            "canonical_url": document.canonical_url or document.url,
            "path": document.path or document.title,
            "updated_at": document.updated_at or document.date,
        }
    )
