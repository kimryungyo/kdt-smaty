"""책상 I/O 컴포넌트가 공유하는 불변 상태 모델."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Direction(StrEnum):
    """릴레이 pulse로 요청할 수 있는 이동 방향."""

    UP = "UP"
    DOWN = "DOWN"


class HeightStatus(StrEnum):
    """현재 높이를 제어에 사용할 수 있는지 나타내는 상태."""

    STOPPED = "STOPPED"
    WAITING = "WAITING"
    ONLINE = "ONLINE"
    STALE = "STALE"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class HeightSnapshot:
    """마지막 실제 높이 관측과 현재 센서 상태."""

    height_cm: float | None
    observed_at: datetime | None
    status: HeightStatus


class RelayEvent(StrEnum):
    """ESP32가 발행하는 릴레이 event."""

    ONLINE = "online"
    HEARTBEAT = "heartbeat"
    MOVING = "moving"
    STOPPED = "stopped"
    REJECTED = "rejected"
    OFFLINE = "offline"


class RelayState(StrEnum):
    """ESP32가 보고한 실제 릴레이 방향 상태."""

    UP = "UP"
    DOWN = "DOWN"
    STOP = "STOP"


@dataclass(frozen=True, slots=True)
class RelaySnapshot:
    """마지막 live ESP32 상태와 수신 payload 검증 오류."""

    event: RelayEvent | None
    state: RelayState | None
    firmware: str | None
    code: str | None
    detail: str | None
    received_at: datetime | None
    last_error: str | None
