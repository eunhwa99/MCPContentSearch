from __future__ import annotations

import json
import socket
import threading
import time
from urllib.request import urlopen

import uvicorn

from core.models import SourceModel, SourceType, SyncJobModel, SyncJobStatus, SyncStatus
from web_console.app import ConsoleDependencies, create_console_app


class FakeAnswerService:
    def __init__(self):
        self.calls: list[dict] = []

    async def answer_with_citations(self, question, filters=None, top_k=5, include_debug=False):
        self.calls.append(
            {
                "question": question,
                "filters": filters or {},
                "top_k": top_k,
                "include_debug": include_debug,
            }
        )
        return {
            "question": question,
            "answer": "## Summary\n\n- Playwright debug summary.",
            "answer_mode": "contextwiki_debug",
            "evidence_status": "grounded",
            "citations": [{"chunk_id": "chunk-1", "title": "README"}],
            "used_chunks": ["chunk-1"],
            "debug_markdown": "## Query\n\n- original: `What changed in ContextWiki?`\n\n## Summary\n\n- Playwright debug summary.\n",
            "debug": {"retrieved_count": 1, "grounded_count": 1},
        }


class FakeWikiService:
    async def generate_wiki_page(self, topic, filters=None, top_k=8):
        return {
            "topic": topic,
            "status": "generated",
            "title": f"{topic} Wiki",
            "markdown": "# ContextWiki\n\nGenerated page [C1]\n",
            "sections": [{"heading": "Overview", "content": "Generated page [C1]"}],
            "citations": [{"marker": "C1", "chunk_id": "chunk-1"}],
            "backlinks": [{"document_id": "doc-1", "chunk_ids": ["chunk-1"]}],
            "used_chunks": ["chunk-1"],
        }


class FakeCodexAnswerService:
    def __init__(self):
        self.calls: list[dict] = []

    async def answer_with_codex(self, question, filters=None, top_k=5):
        self.calls.append({"question": question, "filters": filters or {}, "top_k": top_k})
        return {
            "question": question,
            "answer": "Playwright codex answer [C1]",
            "answer_mode": "codex_cli",
            "codex_status": "succeeded",
            "evidence_status": "grounded",
            "citations": [{"chunk_id": "chunk-1", "title": "README"}],
            "used_chunks": ["chunk-1"],
        }


class FakeMetadataStore:
    def __init__(self):
        self.sources = [
            SourceModel(
                source_id="source_github",
                source_type=SourceType.GITHUB,
                name="MCPContentSearch",
                sync_status=SyncStatus.SUCCEEDED,
            ),
            SourceModel(
                source_id="source_notion",
                source_type=SourceType.NOTION,
                name="ContextWiki Notes",
                sync_status=SyncStatus.IDLE,
            ),
        ]

    def list_sources(self):
        return self.sources

    def get_source(self, source_id):
        for source in self.sources:
            if source.source_id == source_id:
                return source
        return None

    def get_latest_sync_job(self, source_id):
        return SyncJobModel(
            job_id=f"job-{source_id}",
            source_id=source_id,
            status=SyncJobStatus.SUCCEEDED,
            total_documents=2,
            processed_documents=2,
            indexed_chunks=3,
        )


class FakeIngestionService:
    async def sync_source(self, source_id):
        return SyncJobModel(
            job_id=f"job-sync-{source_id}",
            source_id=source_id,
            status=SyncJobStatus.SUCCEEDED,
            total_documents=3,
            processed_documents=3,
            indexed_chunks=5,
        )


class FakeTargetSyncService:
    async def sync_target(self, source_type, target):
        source_id = {
            "github": "source_github",
            "notion": "source_notion",
            "web": "source_web",
        }.get(source_type, "source_web")
        return {
            "status": "succeeded",
            "source_id": source_id,
            "target_type": source_type,
            "target": target,
            "stale_cleanup": "disabled",
            "job": {
                "job_id": f"job-target-{source_type}",
                "source_id": source_id,
                "status": "succeeded",
                "total_documents": 1,
                "processed_documents": 1,
                "indexed_chunks": 1,
                "skipped_documents": 0,
                "error_message": "",
            },
        }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_ready(base_url: str, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/api/health", timeout=1.0) as response:
                if response.status == 200:
                    return
        except Exception as exc:  # pragma: no cover - startup race
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError(f"Web console did not become ready: {last_error}")


def _run_browser_checks(base_url: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(accept_downloads=True)
        page.set_default_timeout(10000)
        page.goto(base_url, wait_until="domcontentloaded")

        # Failure path: empty target sync input should show client-side validation.
        page.locator("#targetSyncButton").click()
        page.wait_for_function(
            "() => document.querySelector('#answerPane')?.textContent?.includes('Enter a target URL or id before calling /api/targets/sync.')"
        )

        # Filter + answer success path.
        page.locator('input[name="sourceType"][value="github"]').check()
        page.locator("#sourceIdInput").fill("source_github")
        page.locator("#topKInput").fill("4")
        page.locator("#questionInput").fill("What changed in ContextWiki?")
        page.locator("#answerButton").click()
        page.wait_for_function(
            "() => document.querySelector('#statusText')?.textContent?.toLowerCase().includes('completed answer')"
        )
        page.wait_for_function(
            "() => document.querySelector('#answerPane')?.textContent?.includes('## Query')"
        )
        page.wait_for_function(
            "() => document.querySelector('#answerPane')?.textContent?.includes('Playwright debug summary.')"
        )

        # Download buttons should emit files.
        with page.expect_download() as md_download:
            page.locator("#downloadMarkdownButton").click()
        with page.expect_download() as json_download:
            page.locator("#downloadJsonButton").click()
        md_path = md_download.value.path()
        json_path = json_download.value.path()
        if not md_path or not json_path:
            raise AssertionError("Expected markdown/json downloads were not produced")

        # Configured source sync path.
        page.locator('[data-sync-source-id="source_github"]').click()
        page.wait_for_function(
            "() => document.querySelector('#syncProgressLabel')?.textContent?.toLowerCase().includes('source_github')"
        )

        # Target sync success path.
        page.locator("#targetSourceTypeSelect").select_option("web")
        page.locator("#targetSyncInput").fill("https://docs.example.com/target")
        page.locator("#targetSyncButton").click()
        page.wait_for_function(
            "() => document.querySelector('#statusText')?.textContent?.toLowerCase().includes('completed web target sync')"
        )

        browser.close()


def main() -> None:
    answer_service = FakeAnswerService()
    codex_answer_service = FakeCodexAnswerService()
    app = create_console_app(
        ConsoleDependencies(
            answer_service=answer_service,
            wiki_service=FakeWikiService(),
            codex_answer_service=codex_answer_service,
            metadata_store=FakeMetadataStore(),
            ingestion_service=FakeIngestionService(),
            target_sync_service=FakeTargetSyncService(),
            auto_sync_source_ids=(),
        )
    )

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        _wait_until_ready(base_url)
        _run_browser_checks(base_url)
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        if thread.is_alive():
            raise RuntimeError("Failed to stop local web console server cleanly")

    if not answer_service.calls:
        raise AssertionError("Expected indexed evidence answer request was not issued")
    call = answer_service.calls[-1]
    source_ids = call["filters"].get("source_ids", [])
    if "source_github" not in source_ids:
        raise AssertionError(f"Expected source_github filter in answer call, got {call}")
    if call["top_k"] != 4:
        raise AssertionError(f"Expected top_k=4 in answer call, got {call['top_k']}")
    if not call.get("include_debug"):
        raise AssertionError("Expected browser answer flow to request include_debug=True")

    print(
        json.dumps(
            {
                "status": "passed",
                "mode": "playwright-web-console",
                "base_url": base_url,
                "checks": [
                    "failure path: empty target sync validation",
                    "filter + debug-first answer request",
                    "markdown/json download",
                    "configured source sync",
                    "target sync",
                ],
            }
        )
    )


if __name__ == "__main__":
    main()
