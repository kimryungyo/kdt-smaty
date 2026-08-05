"""장기 비동기 작업의 등록과 실패 처리 테스트."""

import asyncio

import pytest

from smart_desk.core.exceptions import DuplicateTaskError
from smart_desk.core.task_manager import TaskManager


async def test_duplicate_running_task_is_rejected() -> None:
    manager = TaskManager()
    blocker = asyncio.Event()
    manager.create("worker", blocker.wait())

    with pytest.raises(DuplicateTaskError):
        manager.create("worker", blocker.wait())

    await manager.shutdown()


async def test_critical_failure_calls_handler() -> None:
    reported: list[tuple[str, str]] = []
    manager = TaskManager(
        on_critical_failure=lambda name, error: reported.append((name, str(error)))
    )

    async def fail() -> None:
        raise RuntimeError("작업 실패")

    task = manager.create("critical-worker", fail(), critical=True)
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert reported == [("critical-worker", "작업 실패")]
    assert manager.failures()[0].critical is True
    await manager.shutdown()

