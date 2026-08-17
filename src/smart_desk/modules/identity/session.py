"""Atomic current-user session state and ordered change notifications."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeAlias
from uuid import uuid4

from smart_desk.modules.identity.models import CurrentUserSnapshot, SessionChange, SessionKind


SessionCallback: TypeAlias = Callable[[SessionChange], object]
SessionIdFactory: TypeAlias = Callable[[], str]


def generate_session_id() -> str:
    return f"session-{uuid4().hex}"


class CurrentUserSessionService:
    """Owns session replacement; callbacks never run while its state lock is held."""

    def __init__(
        self,
        *,
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
        session_id_factory: SessionIdFactory = generate_session_id,
    ) -> None:
        self._lock = asyncio.Lock()
        self._current: CurrentUserSnapshot | None = None
        self._sequence = 0
        self._callbacks: dict[str, SessionCallback] = {}
        self._utc_now = utc_now
        self._session_id_factory = session_id_factory

    async def snapshot(self) -> CurrentUserSnapshot | None:
        async with self._lock:
            return self._current

    async def is_current(self, session_id: str) -> bool:
        async with self._lock:
            return self._current is not None and self._current.session_id == session_id

    async def subscribe(self, callback: SessionCallback) -> Callable[[], Awaitable[None]]:
        """Register a callback before returning an async, idempotent unsubscriber."""
        token = uuid4().hex

        async with self._lock:
            self._callbacks[token] = callback

        async def unsubscribe() -> None:
            async with self._lock:
                self._callbacks.pop(token, None)

        return unsubscribe

    async def select(
        self,
        kind: SessionKind,
        profile_id: str | None,
        reason: str,
    ) -> CurrentUserSnapshot:
        if (kind is SessionKind.REGISTERED) != (profile_id is not None):
            raise ValueError("session kind/profile 불일치")
        async with self._lock:
            previous = self._current
            if previous is not None and (previous.kind, previous.profile_id) == (kind, profile_id):
                return previous
            now = self._utc_now()
            current = CurrentUserSnapshot(
                self._session_id_factory(), kind, profile_id, now, now
            )
            self._current = current
            self._sequence += 1
            event = SessionChange(
                self._sequence,
                previous.session_id if previous else None,
                current.session_id,
                reason,
                now,
                current,
            )
            callbacks = tuple(self._callbacks.values())
        await self._notify(callbacks, event)
        return current

    async def end(self, reason: str) -> None:
        async with self._lock:
            previous = self._current
            if previous is None:
                return
            now = self._utc_now()
            self._current = None
            self._sequence += 1
            event = SessionChange(
                self._sequence, previous.session_id, None, reason, now, None
            )
            callbacks = tuple(self._callbacks.values())
        await self._notify(callbacks, event)

    async def end_if_current(self, session_id: str, reason: str) -> bool:
        """End only the session selected by an earlier async operation."""
        async with self._lock:
            previous = self._current
            if previous is None or previous.session_id != session_id:
                return False
            now = self._utc_now()
            self._current = None
            self._sequence += 1
            event = SessionChange(
                self._sequence, previous.session_id, None, reason, now, None
            )
            callbacks = tuple(self._callbacks.values())
        await self._notify(callbacks, event)
        return True

    async def _notify(
        self, callbacks: tuple[SessionCallback, ...], event: SessionChange
    ) -> None:
        for callback in callbacks:
            try:
                result = callback(event)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                # Subscribers are observers.  A broken Desk/Voice subscriber must not
                # roll back this already committed state transition or starve others.
                continue
