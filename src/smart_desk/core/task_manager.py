"""프로세스의 장기 실행 asyncio 작업을 이름으로 감독한다."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
import logging
from typing import Any

from smart_desk.core.exceptions import DuplicateTaskError


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TaskFailure:
    """예상하지 못한 비동기 작업 종료 기록이다."""

    name: str
    critical: bool
    error: BaseException


class TaskManager:
    """이름이 있는 비동기 작업의 생성·실패·종료를 관리한다."""

    def __init__(
        self,
        on_critical_failure: Callable[[str, BaseException], None] | None = None,
    ) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._critical: dict[str, bool] = {}
        self._failures: list[TaskFailure] = []
        self._on_critical_failure = on_critical_failure
        self._closing = False

    def create(
        self,
        name: str,
        coroutine: Coroutine[Any, Any, Any],
        *,
        critical: bool = False,
    ) -> asyncio.Task[Any]:
        """중복되지 않는 이름으로 비동기 작업을 시작한다."""

        existing = self._tasks.get(name)
        if existing is not None and not existing.done():
            coroutine.close()
            raise DuplicateTaskError(f"비동기 작업 '{name}'이(가) 이미 실행 중입니다.")
        if self._closing:
            coroutine.close()
            raise RuntimeError("TaskManager가 종료 중이어서 새 작업을 만들 수 없습니다.")

        task = asyncio.create_task(coroutine, name=name)
        self._tasks[name] = task
        self._critical[name] = critical
        task.add_done_callback(lambda completed, task_name=name: self._on_done(task_name, completed))
        return task

    def active_task_names(self) -> tuple[str, ...]:
        """현재 끝나지 않은 작업 이름을 반환한다."""

        return tuple(name for name, task in self._tasks.items() if not task.done())

    def failures(self) -> tuple[TaskFailure, ...]:
        """예상하지 못하고 종료된 작업 기록을 반환한다."""

        return tuple(self._failures)

    async def shutdown(self) -> None:
        """실행 중인 작업을 모두 취소하고 종료될 때까지 기다린다."""

        self._closing = True
        pending = [task for task in self._tasks.values() if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    def _on_done(self, name: str, task: asyncio.Task[Any]) -> None:
        """작업 종료를 기록하고 필수 작업 실패 callback을 호출한다."""

        if task.cancelled() or self._closing:
            return
        error = task.exception()
        if error is None:
            return

        critical = self._critical.get(name, False)
        failure = TaskFailure(name=name, critical=critical, error=error)
        self._failures.append(failure)
        LOGGER.error(
            "비동기 작업이 예기치 않게 종료되었습니다.",
            extra={"component": "task_manager", "event": "task_failed", "task_name": name},
            exc_info=(type(error), error, error.__traceback__),
        )
        if critical and self._on_critical_failure is not None:
            self._on_critical_failure(name, error)
