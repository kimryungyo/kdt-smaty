"""Small, bounded Agents SDK session ownership for the current desk user."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from smart_desk.modules.identity.models import SessionKind
from smart_desk.modules.identity.session import CurrentUserSessionService


class BoundedSession:
    """The async four-method ``agents.Session`` contract used by ``Runner``."""

    def __init__(self, session_id: str, *, item_cap: int = 24) -> None:
        if item_cap <= 0:
            raise ValueError("session item_cap은 양수여야 합니다.")
        self.session_id = session_id
        self._item_cap = item_cap
        self._items: list[Any] = []
        self._lock = asyncio.Lock()

    async def get_items(self, limit: int | None = None) -> list[Any]:
        """Return all, none, or the newest items with SQLite Session limit semantics."""

        async with self._lock:
            if limit is None or limit < 0:
                return list(self._items)
            if limit == 0:
                return []
            return list(self._items[-limit:])

    async def add_items(self, items: Sequence[Any]) -> None:
        async with self._lock:
            self._items.extend(items)
            overflow = len(self._items) - self._item_cap
            if overflow > 0:
                del self._items[:overflow]

    async def pop_item(self) -> Any | None:
        async with self._lock:
            return self._items.pop() if self._items else None

    async def clear_session(self) -> None:
        async with self._lock:
            self._items.clear()


@dataclass(frozen=True, slots=True)
class TurnContext:
    session_id: str | None
    profile_id: str | None
    personalized: bool
    session: BoundedSession
    generation: int


class CurrentUserSessionManager:
    """Own current-user SDK sessions; changes cancel runs and discard history."""

    def __init__(self, current_user: CurrentUserSessionService, *, item_cap: int = 24) -> None:
        self._current_user = current_user
        self._item_cap = item_cap
        self._sessions: dict[str, BoundedSession] = {}
        self._generation = 0
        self._lock = asyncio.Lock()
        self._unsubscribe: Any = None
        self._tasks: set[asyncio.Task[object]] = set()

    async def start(self) -> None:
        async with self._lock:
            if self._unsubscribe is None:
                self._unsubscribe = await self._current_user.subscribe(self._changed)

    async def stop(self) -> None:
        async with self._lock:
            unsubscribe, self._unsubscribe = self._unsubscribe, None
            self._generation += 1
            tasks = tuple(self._tasks)
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
        if unsubscribe is not None:
            await unsubscribe()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for session in sessions:
            await session.clear_session()

    async def capture(self, *, personalization_allowed: bool = True) -> TurnContext:
        snapshot = await self._current_user.snapshot()
        async with self._lock:
            generation = self._generation
            if snapshot is not None and personalization_allowed:
                session = self._sessions.setdefault(
                    snapshot.session_id,
                    BoundedSession(snapshot.session_id, item_cap=self._item_cap),
                )
            else:
                # Never reuse a session while there is no user or personalization is blocked.
                session = BoundedSession("temporary", item_cap=self._item_cap)
        personalized = bool(
            snapshot
            and personalization_allowed
            and snapshot.kind is SessionKind.REGISTERED
        )
        return TurnContext(
            session_id=snapshot.session_id if snapshot else None,
            profile_id=snapshot.profile_id if personalized else None,
            personalized=personalized,
            session=session,
            generation=generation,
        )

    async def is_valid(self, context: TurnContext) -> bool:
        async with self._lock:
            if context.generation != self._generation:
                return False
        snapshot = await self._current_user.snapshot()
        if context.session_id is None:
            return snapshot is None
        return snapshot is not None and snapshot.session_id == context.session_id

    def register_run(self, task: asyncio.Task[object]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def discard_all(self) -> None:
        async with self._lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await session.clear_session()

    async def _changed(self, _event: object) -> None:
        async with self._lock:
            self._generation += 1
            tasks = tuple(self._tasks)
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
        for task in tasks:
            task.cancel()
        for session in sessions:
            await session.clear_session()
