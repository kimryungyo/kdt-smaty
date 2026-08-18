"""Scoped, explicit-only Mem0 profile-memory boundary."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic
from typing import Any


LOGGER = logging.getLogger(__name__)


class ProfileMemoryError(RuntimeError):
    """An enabled profile-memory backend could not complete its operation."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProfileMemoryStatus(StrEnum):
    DISABLED = "DISABLED"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True, slots=True)
class ProfileMemorySnapshot:
    enabled: bool
    status: ProfileMemoryStatus
    detail: str


class ProfileMemoryService:
    """Keep Mem0 SDK details, profile scope and failure handling in one place."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        config: dict[str, Any] | None = None,
        search_limit: int = 5,
        timeout_seconds: float = 2.0,
        write_timeout_seconds: float = 8.0,
        fact_limit: int = 500,
        circuit_failure_threshold: int = 3,
        circuit_open_seconds: float = 30.0,
    ) -> None:
        self._enabled = enabled
        self._config = config or {}
        self._search_limit = search_limit
        self._timeout_seconds = timeout_seconds
        self._write_timeout_seconds = write_timeout_seconds
        self._fact_limit = fact_limit
        self._circuit_failure_threshold = circuit_failure_threshold
        self._circuit_open_seconds = circuit_open_seconds
        self._memory: Any | None = None
        self._lock = asyncio.Lock()
        self._profile_locks: dict[str, asyncio.Lock] = {}
        self._deleting_profiles: set[str] = set()
        self._status = ProfileMemoryStatus.DISABLED if not enabled else ProfileMemoryStatus.INITIALIZING
        self._detail = "profile memory가 비활성화되어 있습니다." if not enabled else "초기화를 기다리고 있습니다."
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0

    @staticmethod
    async def _resolve(value: Any) -> Any:
        return await value if inspect.isawaitable(value) else value

    @staticmethod
    def _namespace(profile_id: str) -> str:
        normalized = profile_id.strip()
        if not normalized:
            raise ValueError("등록 profile_id가 필요합니다.")
        return f"profile:{normalized}"

    def snapshot(self) -> ProfileMemorySnapshot:
        return ProfileMemorySnapshot(
            enabled=self._enabled,
            status=self._status,
            detail=self._detail,
        )

    async def start(self) -> None:
        """Check local paths and construct the optional backend without blocking app readiness."""

        if not self._enabled:
            return
        try:
            await self._preflight_paths()
            await self._backend()
        except ProfileMemoryError as error:
            self._mark_degraded(error.code)
            LOGGER.warning(
                "Profile memory preflight failed; voice will continue without memory.",
                extra={
                    "component": "profile_memory",
                    "event": "profile_memory_preflight_failed",
                    "error_code": error.code,
                },
            )

    async def stop(self) -> None:
        """Drop process-local references; all durable data remains in the configured volume."""

        async with self._lock:
            self._memory = None
            self._profile_locks.clear()
            self._deleting_profiles.clear()

    async def _preflight_paths(self) -> None:
        vector_config = self._config.get("vector_store", {})
        vector_path = (
            vector_config.get("config", {}).get("path")
            if isinstance(vector_config, dict)
            else None
        )
        history_path = self._config.get("history_db_path")
        candidates = [Path(value) for value in (vector_path, history_path) if isinstance(value, str)]
        runtime_dir = os.environ.get("MEM0_DIR")
        if runtime_dir:
            candidates.append(Path(runtime_dir))

        def verify() -> None:
            for candidate in candidates:
                directory = candidate if candidate.suffix == "" else candidate.parent
                directory.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(dir=directory, prefix=".smart-desk-preflight-", delete=True) as handle:
                    handle.write(b"ok")
                    handle.flush()
                    os.fsync(handle.fileno())

        try:
            await asyncio.to_thread(verify)
        except Exception as error:
            raise ProfileMemoryError("profile_memory_unavailable") from error

    async def _backend(self) -> Any | None:
        if not self._enabled:
            return None
        self._ensure_circuit_closed()
        async with self._lock:
            self._ensure_circuit_closed()
            if self._memory is not None:
                return self._memory
            self._status = ProfileMemoryStatus.INITIALIZING
            self._detail = "Mem0 backend를 초기화하고 있습니다."
            try:
                from mem0 import AsyncMemory  # type: ignore[import-not-found]

                created = await self._call(
                    lambda: AsyncMemory.from_config(self._config),
                    code="profile_memory_unavailable",
                )
            except Exception as error:
                self._mark_degraded("profile_memory_unavailable")
                raise ProfileMemoryError("profile_memory_unavailable") from error
            if created is None or not all(
                callable(getattr(created, method, None))
                for method in ("add", "search", "get_all", "update", "delete", "delete_all")
            ):
                self._mark_degraded("profile_memory_unavailable")
                raise ProfileMemoryError("profile_memory_unavailable")
            self._memory = created
            self._status = ProfileMemoryStatus.READY
            self._detail = "Mem0 profile memory를 사용할 수 있습니다."
            LOGGER.info(
                "Profile memory initialized.",
                extra={"component": "profile_memory", "event": "profile_memory_initialized"},
            )
            return created

    def _mark_degraded(self, code: str) -> None:
        if self._enabled:
            self._status = ProfileMemoryStatus.DEGRADED
            self._detail = code

    def _ensure_circuit_closed(self) -> None:
        if monotonic() < self._circuit_open_until:
            self._mark_degraded("profile_memory_circuit_open")
            raise ProfileMemoryError("profile_memory_unavailable")

    def _record_failure(self, code: str) -> None:
        self._consecutive_failures += 1
        self._mark_degraded(code)
        if self._consecutive_failures < self._circuit_failure_threshold:
            return
        self._circuit_open_until = monotonic() + self._circuit_open_seconds
        self._detail = "profile_memory_circuit_open"
        LOGGER.warning(
            "Profile memory circuit opened.",
            extra={
                "component": "profile_memory",
                "event": "profile_memory_circuit_opened",
                "error_code": code,
            },
        )

    def _mark_ready(self) -> None:
        if self._enabled and self._memory is not None:
            self._status = ProfileMemoryStatus.READY
            self._detail = "Mem0 profile memory를 사용할 수 있습니다."
            self._consecutive_failures = 0
            self._circuit_open_until = 0.0

    async def _call(
        self, operation: Callable[[], Any], *, code: str, timeout_seconds: float | None = None
    ) -> Any:
        """Run sync or async SDK operations under one timeout and error boundary."""

        async def invoke() -> Any:
            return await self._resolve(operation())

        started = monotonic()
        try:
            result = await asyncio.wait_for(invoke(), timeout_seconds or self._timeout_seconds)
        except Exception as error:
            self._record_failure(code)
            LOGGER.warning(
                "Profile memory operation failed.",
                extra={
                    "component": "profile_memory",
                    "event": "profile_memory_operation_failed",
                    "error_code": code,
                    "duration_ms": round((monotonic() - started) * 1000),
                },
            )
            raise ProfileMemoryError(code) from error
        self._mark_ready()
        return result

    def _profile_lock(self, profile_id: str) -> asyncio.Lock:
        return self._profile_locks.setdefault(profile_id, asyncio.Lock())

    @staticmethod
    def _results(result: Any, *, code: str) -> list[dict[str, Any]]:
        if not isinstance(result, dict):
            raise ProfileMemoryError(code)
        values = result.get("results")
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise ProfileMemoryError(code)
        return values

    @classmethod
    def _scoped_results(
        cls, result: Any, *, profile_id: str, code: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        namespace = cls._namespace(profile_id)
        values = cls._results(result, code=code)
        scoped: list[dict[str, Any]] = []
        for value in values:
            user_id = value.get("user_id")
            if user_id is not None and user_id != namespace:
                raise ProfileMemoryError(code)
            memory_id, text = value.get("id"), value.get("memory")
            if not isinstance(memory_id, str) or not isinstance(text, str):
                raise ProfileMemoryError(code)
            scoped.append(value)
        return scoped if limit is None else scoped[:limit]

    async def search(self, profile_id: str, query: str) -> list[dict[str, Any]]:
        backend = await self._backend()
        if backend is None:
            return []
        normalized_query = query.strip()
        if not normalized_query:
            return []
        result = await self._call(
            lambda: backend.search(
                normalized_query,
                filters={"user_id": self._namespace(profile_id)},
                top_k=self._search_limit,
            ),
            code="profile_memory_search_failed",
        )
        values = self._scoped_results(
            result,
            profile_id=profile_id,
            code="profile_memory_search_failed",
            limit=self._search_limit,
        )
        LOGGER.info(
            "Profile memory search completed.",
            extra={
                "component": "profile_memory",
                "event": "profile_memory_search_completed",
                "result_count": len(values),
            },
        )
        return values

    async def remember(
        self,
        profile_id: str,
        content: str,
        *,
        explicit: bool,
        is_valid: Callable[[], Awaitable[bool]] | None = None,
        source: str = "explicit_voice",
        infer: bool = True,
    ) -> bool:
        """Persist one caller-approved fact and return whether Mem0 accepted it."""

        if not explicit:
            return False
        fact = content.strip()
        if not fact or len(fact) > self._fact_limit:
            raise ProfileMemoryError("profile_memory_policy_rejected")
        if is_valid is not None and not await is_valid():
            raise ProfileMemoryError("profile_memory_session_mismatch")
        backend = await self._backend()
        if backend is None:
            raise ProfileMemoryError("profile_memory_unavailable")
        # Mem0's direct-import mode expects chat messages.  Preserve dashboard
        # entries verbatim instead of asking the LLM to infer a different fact.
        payload: str | list[dict[str, str]] = fact
        if not infer:
            payload = [{"role": "user", "content": fact}]
        lock = self._profile_lock(profile_id)
        async with lock:
            if profile_id in self._deleting_profiles:
                raise ProfileMemoryError("profile_memory_session_mismatch")
            if is_valid is not None and not await is_valid():
                raise ProfileMemoryError("profile_memory_session_mismatch")
            await self._call(
                lambda: backend.add(
                    payload,
                    user_id=self._namespace(profile_id),
                    metadata={
                        "schema_version": 1,
                        "source": source,
                        "category": "preference",
                        "language": "ko",
                    },
                    infer=infer,
                ),
                code="profile_memory_add_failed",
                timeout_seconds=self._write_timeout_seconds,
            )
        LOGGER.info(
            "Profile memory write completed.",
            extra={"component": "profile_memory", "event": "profile_memory_write_completed"},
        )
        return True

    async def list_profile(self, profile_id: str) -> list[dict[str, Any]]:
        backend = await self._backend()
        if backend is None:
            raise ProfileMemoryError("profile_memory_unavailable")
        result = await self._call(
            lambda: backend.get_all(
                filters={"user_id": self._namespace(profile_id)}, top_k=100
            ),
            code="profile_memory_search_failed",
        )
        return self._scoped_results(result, profile_id=profile_id, code="profile_memory_search_failed")

    async def update(
        self,
        profile_id: str,
        memory_id: str,
        content: str,
        *,
        is_valid: Callable[[], Awaitable[bool]] | None = None,
    ) -> dict[str, Any]:
        fact = content.strip()
        if not fact or len(fact) > self._fact_limit:
            raise ProfileMemoryError("profile_memory_policy_rejected")
        if is_valid is not None and not await is_valid():
            raise ProfileMemoryError("profile_memory_session_mismatch")
        backend = await self._backend()
        if backend is None:
            raise ProfileMemoryError("profile_memory_unavailable")
        lock = self._profile_lock(profile_id)
        async with lock:
            if profile_id in self._deleting_profiles or (
                is_valid is not None and not await is_valid()
            ):
                raise ProfileMemoryError("profile_memory_session_mismatch")
            existing = await self._find(profile_id, memory_id, backend)
            result = await self._call(
                lambda: backend.update(memory_id, text=fact),
                code="profile_memory_update_failed",
            )
        if isinstance(result, dict) and isinstance(result.get("memory"), str):
            return result
        return {**existing, "memory": fact}

    async def delete(
        self,
        profile_id: str,
        memory_id: str,
        *,
        is_valid: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        if is_valid is not None and not await is_valid():
            raise ProfileMemoryError("profile_memory_session_mismatch")
        backend = await self._backend()
        if backend is None:
            raise ProfileMemoryError("profile_memory_unavailable")
        lock = self._profile_lock(profile_id)
        async with lock:
            if profile_id in self._deleting_profiles or (
                is_valid is not None and not await is_valid()
            ):
                raise ProfileMemoryError("profile_memory_session_mismatch")
            await self._find(profile_id, memory_id, backend)
            await self._call(
                lambda: backend.delete(memory_id), code="profile_memory_delete_failed"
            )

    async def _find(self, profile_id: str, memory_id: str, backend: Any) -> dict[str, Any]:
        values = await self.list_profile(profile_id)
        for value in values:
            if value["id"] == memory_id:
                return value
        raise ProfileMemoryError("profile_memory_not_found")

    async def begin_profile_deletion(self, profile_id: str) -> None:
        lock = self._profile_lock(profile_id)
        async with lock:
            self._deleting_profiles.add(profile_id)

    async def abort_profile_deletion(self, profile_id: str) -> None:
        lock = self._profile_lock(profile_id)
        async with lock:
            self._deleting_profiles.discard(profile_id)

    async def delete_profile(self, profile_id: str) -> None:
        backend = await self._backend()
        if backend is None:
            return
        lock = self._profile_lock(profile_id)
        async with lock:
            self._deleting_profiles.add(profile_id)
            await self._call(
                lambda: backend.delete_all(user_id=self._namespace(profile_id)),
                code="profile_memory_delete_failed",
            )
            result = await self._call(
                lambda: backend.get_all(
                    filters={"user_id": self._namespace(profile_id)}, top_k=1
                ),
                code="profile_memory_delete_failed",
            )
            if self._results(result, code="profile_memory_delete_failed"):
                raise ProfileMemoryError("profile_memory_delete_failed")
        LOGGER.info(
            "Profile memory deleted.",
            extra={"component": "profile_memory", "event": "profile_memory_profile_deleted"},
        )
