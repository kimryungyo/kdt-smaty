"""DashboardService가 공개 Desk/Profile 계약만 사용하는지 검증한다."""

from datetime import UTC, datetime

from smart_desk.modules.dashboard.service import DashboardService
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


class FakeDesk:
    def __init__(self) -> None:
        now = datetime(2026, 8, 8, tzinfo=UTC)
        self.snapshot = DeskSnapshot(
            state=DeskState.IDLE,
            height=HeightSnapshot(90.0, now, HeightStatus.ONLINE),
            relay=RelaySnapshot(RelayEvent.ONLINE, RelayState.STOP, "test", None, None, now, None),
            target_height_cm=None,
            direction=None,
            detail="ready",
            last_error=None,
            updated_at=now,
        )
        self.calls: list[object] = []

    def get_snapshot(self) -> DeskSnapshot:
        return self.snapshot

    async def hold_up(self) -> None:
        self.calls.append("up")

    async def hold_down(self) -> None:
        self.calls.append("down")

    async def stop_motion(self, reason: str) -> None:
        self.calls.append(("stop", reason))

    async def set_target(self, target: float) -> None:
        self.calls.append(("target", target))


class FakeProfiles:
    async def list_profiles(self):
        return []


async def test_status_mapping_and_desk_commands_are_delegated_once() -> None:
    desk = FakeDesk()
    service = DashboardService(desk, FakeProfiles())  # type: ignore[arg-type]

    status = service.get_status()
    await service.hold(Direction.UP)
    await service.hold(Direction.DOWN)
    await service.set_target(101.5)
    await service.cancel_target()

    assert status.model_dump(mode="json", by_alias=True) == {
        "state": "IDLE",
        "height": {"heightCm": 90.0, "observedAt": "2026-08-08T00:00:00Z", "status": "ONLINE"},
        "relay": {"event": "online", "state": "STOP", "firmware": "test", "code": None, "detail": None, "receivedAt": "2026-08-08T00:00:00Z", "lastError": None},
        "targetHeightCm": None,
        "direction": None,
        "detail": "ready",
        "lastError": None,
        "updatedAt": "2026-08-08T00:00:00Z",
    }
    assert desk.calls == [
        "up",
        "down",
        ("target", 101.5),
        ("stop", "대시보드에서 목표 이동을 취소했습니다."),
    ]
