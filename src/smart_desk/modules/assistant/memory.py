"""Lazy, explicit-only Mem0 profile memory boundary."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any


class ProfileMemoryError(RuntimeError):
    """An enabled profile-memory backend could not complete its operation."""


class ProfileMemoryService:
    def __init__(
        self,
        *,
        enabled: bool = False,
        config: dict[str, Any] | None = None,
        search_limit: int = 5,
        timeout_seconds: float = 2.0,
    ) -> None:
        self._enabled = enabled
        self._config = config or {}
        self._search_limit = search_limit
        self._timeout_seconds = timeout_seconds
        self._memory: Any | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    async def _resolve(value: Any) -> Any:
        return await value if inspect.isawaitable(value) else value

    async def _backend(self) -> Any | None:
        if not self._enabled:
            return None
        async with self._lock:
            if self._memory is not None:
                return self._memory
            try:
                from mem0 import AsyncMemory  # type: ignore[import-not-found]

                created = await self._call(
                    lambda: AsyncMemory.from_config(self._config),
                    code="profile_memory_unavailable",
                )
            except Exception as error:
                raise ProfileMemoryError("profile_memory_unavailable") from error
            if created is None or not all(
                callable(getattr(created, method, None))
                for method in ("add", "search", "delete_all")
            ):
                raise ProfileMemoryError("profile_memory_unavailable")
            self._memory = created
            return created

    @staticmethod
    def _namespace(profile_id: str) -> str:
        if not profile_id:
            raise ValueError("등록 profile_id가 필요합니다.")
        return f"profile:{profile_id}"

    async def _call(self, operation: Callable[[], Any], *, code: str) -> Any:
        """Run both synchronous invocation and async completion under one boundary."""

        async def invoke() -> Any:
            return await self._resolve(operation())

        try:
            return await asyncio.wait_for(invoke(), self._timeout_seconds)
        except Exception as error:
            raise ProfileMemoryError(code) from error

    async def search(self, profile_id: str, query: str) -> list[dict[str, Any]]:
        backend = await self._backend()
        if backend is None:
            return []
        result = await self._call(
            lambda: backend.search(
                query,
                filters={"user_id": self._namespace(profile_id)},
                top_k=self._search_limit,
            ),
            code="profile_memory_search_failed",
        )
        if not isinstance(result, dict) or set(result) != {"results"}:
            raise ProfileMemoryError("profile_memory_search_failed")
        values = result["results"]
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise ProfileMemoryError("profile_memory_search_failed")
        return values[: self._search_limit]

    async def remember(self, profile_id: str, content: str, *, explicit: bool) -> None:
        """Store only a caller-confirmed memory; transcripts are never implicit input."""

        if not explicit:
            return
        backend = await self._backend()
        if backend is None:
            return
        await self._call(
            lambda: backend.add(content, user_id=self._namespace(profile_id)),
            code="profile_memory_add_failed",
        )

    async def delete_profile(self, profile_id: str) -> None:
        backend = await self._backend()
        if backend is None:
            return
        await self._call(
            lambda: backend.delete_all(user_id=self._namespace(profile_id)),
            code="profile_memory_delete_failed",
        )
