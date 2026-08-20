"""얼굴 인식이 끊긴 뒤에도 직전 등록 사용자를 짧게 기억한다.

얼굴 인식은 자리를 잠깐 비우거나 고개를 돌리는 것만으로도 끊긴다. 그때마다
음성 명령이 "지금은 할 수 없다"로 막히면 혼자 쓰는 책상에서도 모드 변경을
쓸 수 없다. 이 모듈은 세션 서비스를 구독만 하는 얇은 관찰자로, 세션 자체의
소유권 규칙은 바꾸지 않고 "마지막으로 로그인했던 profile"만 따로 들고 있다.

세션이 살아 있는 동안에는 언제나 그 세션이 우선이다. 여기 남은 값은 세션이
없을 때 개인화가 필요한 명령이 참고하는 폴백일 뿐이다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from smart_desk.modules.identity.models import SessionKind


class RecentUserMemory:
    """직전 등록 사용자의 profile_id를 만료 시간과 함께 보관한다."""

    def __init__(
        self,
        *,
        current_user: object,
        retention_seconds: float = 1800.0,
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._users = current_user
        self._retention_seconds = retention_seconds
        self._utc_now = utc_now
        self._lock = asyncio.Lock()
        self._profile_id: str | None = None
        self._remembered_at: datetime | None = None
        self._unsubscribe: Callable[[], Awaitable[None]] | None = None

    async def start(self) -> None:
        async with self._lock:
            if self._unsubscribe is not None:
                return
            unsubscribe = await self._users.subscribe(self._on_change)  # type: ignore[attr-defined]
            self._unsubscribe = unsubscribe
        # 구독 전에 이미 로그인해 있었다면 그 사용자부터 기억한다.
        snapshot = await self._users.snapshot()  # type: ignore[attr-defined]
        if snapshot is not None:
            await self._remember(snapshot)

    async def stop(self) -> None:
        async with self._lock:
            unsubscribe, self._unsubscribe = self._unsubscribe, None
        if unsubscribe is not None:
            await unsubscribe()

    async def profile_id(self) -> str | None:
        """지금 쓸 수 있는 profile_id. 살아 있는 세션이 언제나 우선한다."""
        snapshot = await self._users.snapshot()  # type: ignore[attr-defined]
        if snapshot is not None and snapshot.profile_id is not None:
            return snapshot.profile_id
        async with self._lock:
            if self._profile_id is None or self._remembered_at is None:
                return None
            age = (self._utc_now() - self._remembered_at).total_seconds()
            if age > self._retention_seconds:
                # 오래 비운 자리는 남의 자리일 수 있다. 기억을 버린다.
                self._profile_id = None
                self._remembered_at = None
                return None
            return self._profile_id

    async def _on_change(self, event: object) -> None:
        current = getattr(event, "current", None)
        if current is not None:
            await self._remember(current)

    async def _remember(self, snapshot: object) -> None:
        if getattr(snapshot, "kind", None) is not SessionKind.REGISTERED:
            return
        profile_id = getattr(snapshot, "profile_id", None)
        if profile_id is None:
            return
        async with self._lock:
            self._profile_id = profile_id
            self._remembered_at = self._utc_now()
