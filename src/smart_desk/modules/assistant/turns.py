"""Latest-only provider-neutral Assistant turn projection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from smart_desk.modules.identity.session import CurrentUserSessionService


class TurnPhase(StrEnum):
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    TOOL = "TOOL"
    FINAL = "FINAL"


class TurnStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class AssistantTurn:
    turn_id: str
    session_id: str | None
    profile_id: str | None
    phase: TurnPhase
    sequence: int
    status: TurnStatus
    title: str
    summary: str | None
    detail: str | None
    started_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    error_code: str | None = None


class AssistantTurnStore:
    """One current turn, invalidated synchronously with current-user changes."""

    def __init__(self, current_user: CurrentUserSessionService) -> None:
        self._current_user = current_user
        self._turn: AssistantTurn | None = None
        self._lock = asyncio.Lock()
        self._unsubscribe: Any = None

    async def start(self) -> None:
        async with self._lock:
            if self._unsubscribe is None:
                self._unsubscribe = await self._current_user.subscribe(self._changed)

    async def stop(self) -> None:
        async with self._lock:
            unsubscribe, self._unsubscribe = self._unsubscribe, None
            self._turn = None
        if unsubscribe is not None:
            await unsubscribe()

    async def create(
        self,
        session_id: str | None,
        profile_id: str | None,
        *,
        title: str = "음성 요청",
    ) -> AssistantTurn:
        now = datetime.now(UTC)
        async with self._lock:
            self._turn = AssistantTurn(
                turn_id=f"turn-{uuid4().hex}",
                session_id=session_id,
                profile_id=profile_id,
                phase=TurnPhase.LISTENING,
                sequence=1,
                status=TurnStatus.RUNNING,
                title=title,
                summary=None,
                detail=None,
                started_at=now,
                updated_at=now,
            )
            return self._turn

    async def update(
        self,
        turn_id: str,
        *,
        sequence: int,
        phase: TurnPhase,
        status: TurnStatus | None = None,
        summary: str | None = None,
        detail: str | None = None,
        error_code: str | None = None,
    ) -> AssistantTurn | None:
        async with self._lock:
            turn = self._turn
            if (
                turn is None
                or turn.turn_id != turn_id
                or sequence <= turn.sequence
                or turn.status is not TurnStatus.RUNNING
            ):
                return None
            next_status = status or turn.status
            now = datetime.now(UTC)
            self._turn = replace(
                turn,
                sequence=sequence,
                phase=phase,
                status=next_status,
                summary=summary if summary is not None else turn.summary,
                detail=detail if detail is not None else turn.detail,
                error_code=error_code if error_code is not None else turn.error_code,
                updated_at=now,
                completed_at=now if next_status is not TurnStatus.RUNNING else None,
            )
            return self._turn

    async def latest(self) -> AssistantTurn | None:
        async with self._lock:
            turn = self._turn
            if turn is None:
                return None
            current = await self._current_user.snapshot()
            if turn.session_id is None:
                return turn if current is None else None
            return turn if current is not None and current.session_id == turn.session_id else None

    async def _changed(self, _event: object) -> None:
        async with self._lock:
            # Never retain an old null-session turn through A -> B -> null either.
            self._turn = None
