"""틸팅 컨트롤러가 공유하는 불변 상태 모델."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class TiltState(StrEnum):
    """상위 틸팅 제어기의 공개 상태."""

    IDLE = "IDLE"
    MOVING = "MOVING"
    AT_TARGET = "AT_TARGET"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class TiltSnapshot:
    """마지막 틸팅 장치 상태와 제어 의도."""

    state: TiltState
    level: int | None
    target_level: int | None
    position_mm: float | None
    position_valid: bool
    firmware: str | None
    detail: str
    last_error: str | None
    updated_at: datetime
