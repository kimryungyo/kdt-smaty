"""책상 높이, ESP32 relay와 상위 제어기의 공개 타입."""

from smart_desk.core.container import get_container

from smart_desk.modules.desk.controller import (
    DeskCommandRejectedError,
    DeskController,
    SUPPORTED_RELAY_FIRMWARES,
)
from smart_desk.modules.desk.height_monitor import DeskHeightMonitor
from smart_desk.modules.desk.models import (
    DeskSnapshot,
    DeskState,
    Direction,
    HeightSnapshot,
    HeightStatus,
    RelayEvent,
    RelaySnapshot,
    RelayState,
)
from smart_desk.modules.desk.relay import RelayClient
from smart_desk.modules.desk.segment import SegmentDecoder


def get_desk() -> DeskController:
    """AppContainer가 소유한 프로세스 공용 책상 제어기를 반환한다."""

    return get_container().desk


__all__ = [
    "DeskCommandRejectedError",
    "DeskController",
    "DeskHeightMonitor",
    "DeskSnapshot",
    "DeskState",
    "Direction",
    "HeightSnapshot",
    "HeightStatus",
    "RelayClient",
    "RelayEvent",
    "RelaySnapshot",
    "RelayState",
    "SegmentDecoder",
    "SUPPORTED_RELAY_FIRMWARES",
    "get_desk",
]
