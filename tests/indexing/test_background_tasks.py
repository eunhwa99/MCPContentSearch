import asyncio

import pytest

from indexing.background_tasks import BackgroundTaskRegistry, safe_error_message


pytestmark = pytest.mark.unit


def test_safe_error_message_redacts_broad_secret_tokens():
    message = (
        "OpenAI sk-proj-abcdefghijklmnopqrstuvwxyz123456 "
        "GitHub github_pat_abcdefghijklmnopqrstuvwxyz123456 "
        "AWS AKIAIOSFODNN7EXAMPLE "
        "Slack xoxb-1234567890-secret "
        "JWT eyJheader.payload123456.signature123456 "
        "Authorization: Basic dXNlcjpwYXNzd29yZA== "
        "api_key: plain secret with spaces\n"
        "next line"
    )

    redacted = safe_error_message(RuntimeError(message))

    assert "sk-proj-" not in redacted
    assert "github_pat_" not in redacted
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted
    assert "xoxb-" not in redacted
    assert "eyJheader" not in redacted
    assert "Basic dXNlcjpwYXNzd29yZA==" not in redacted
    assert "plain secret with spaces" not in redacted
    assert "api_key: <redacted>" in redacted


def test_registry_preserves_active_tasks_when_history_cap_is_exceeded():
    async def run_tasks():
        registry = BackgroundTaskRegistry(max_tasks=2)
        futures = [asyncio.Future() for _ in range(3)]
        tasks = [
            registry.schedule(f"task-{index}", future, total_docs=1)
            for index, future in enumerate(futures)
        ]

        await asyncio.sleep(0)
        active_snapshot = registry.snapshot()

        for future in futures:
            future.set_result(1)
        await asyncio.gather(*tasks)

        return active_snapshot, registry.snapshot()

    active_snapshot, final_snapshot = asyncio.run(run_tasks())

    assert len(active_snapshot) == 3
    assert {record["state"] for record in active_snapshot} == {"running"}
    assert len(final_snapshot) == 2
    assert [record["label"] for record in final_snapshot] == ["task-1", "task-2"]


def test_registry_keeps_strong_reference_to_pending_task():
    async def run_task():
        registry = BackgroundTaskRegistry()
        future = asyncio.Future()
        task = registry.schedule("pending", future, total_docs=1)

        await asyncio.sleep(0)
        assert task in registry._tasks

        future.set_result(1)
        await task

        assert task not in registry._tasks

    asyncio.run(run_task())
