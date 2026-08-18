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
from smart_desk.modules.profiles.activity_modes import ActivityModeRepositoryError
from smart_desk.modules.profiles.models import ActivityModeCreate, Profile, ProfileCreate


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

    async def create_profile(self, create: ProfileCreate) -> Profile:
        return Profile(
            id="profile-" + "1" * 32,
            name=create.name,
            sitting_height_cm=create.sitting_height_cm,
            standing_height_cm=create.standing_height_cm,
            led_color=create.led_color,
            led_brightness=create.led_brightness,
            tilt_level=create.tilt_level,
            description=create.description,
        )


class FakeActivityModes:
    def __init__(self, *, fail: bool = False) -> None:
        self.created: list[tuple[str, ActivityModeCreate]] = []
        self.fail = fail

    async def create_mode(self, profile_id: str, create: ActivityModeCreate):
        if self.fail:
            raise ActivityModeRepositoryError("storage down")
        self.created.append((profile_id, create))


class FakeAutomation:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def hold(self, direction: Direction) -> None:
        self.calls.append(("hold", direction))

    async def set_target(self, target: float) -> None:
        self.calls.append(("target", target))

    async def stop_motion(self, reason: str) -> None:
        self.calls.append(("stop", reason))


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
        "height": {"heightCm": 90.0, "observedAt": "2026-08-08T00:00:00Z", "status": "ONLINE", "provenance": None},
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


async def test_dashboard_commands_use_automation_public_boundary_when_assembled() -> None:
    desk = FakeDesk()
    automation = FakeAutomation()
    service = DashboardService(desk, FakeProfiles(), automation)

    await service.hold(Direction.UP)
    await service.set_target(101.5)
    await service.stop_motion("user stop")

    assert automation.calls == [
        ("hold", Direction.UP),
        ("target", 101.5),
        ("stop", "user stop"),
    ]
    assert desk.calls == []


async def test_create_profile_seeds_default_modes_when_activity_modes_wired() -> None:
    activity_modes = FakeActivityModes()
    service = DashboardService(
        FakeDesk(), FakeProfiles(), activity_modes=activity_modes
    )

    profile = await service.create_profile(
        ProfileCreate(name="새 사용자", sittingHeightCm=80, standingHeightCm=105)
    )

    assert [name for _, create in activity_modes.created for name in [create.name]] == [
        "독서",
        "공부",
    ]
    assert all(profile_id == profile.id for profile_id, _ in activity_modes.created)

    # 논문에서 가져온 조명 기본값이 처음부터 들어가 있어야 한다.
    seeded = {create.name: create.led_schedule for _, create in activity_modes.created}
    assert seeded["독서"]["kind"] == "TIME_OF_DAY"
    assert seeded["공부"]["kind"] == "ELAPSED"
    # 공부는 0분 4000K에서 시작해 10분에 6000K로 올라간다.
    study = seeded["공부"]["steps"]
    assert (study[0]["at"], study[0]["color"], study[0]["brightness"]) == (0, "FFD6A4", 153)
    assert (study[-1]["at"], study[-1]["color"], study[-1]["brightness"]) == (10, "FFF6D8", 255)
    assert all(create.description for _, create in activity_modes.created)


async def test_create_profile_skips_seeding_when_activity_modes_not_wired() -> None:
    service = DashboardService(FakeDesk(), FakeProfiles())

    profile = await service.create_profile(
        ProfileCreate(name="새 사용자", sittingHeightCm=80, standingHeightCm=105)
    )

    assert profile.name == "새 사용자"


async def test_create_profile_survives_default_mode_seed_failure() -> None:
    activity_modes = FakeActivityModes(fail=True)
    service = DashboardService(
        FakeDesk(), FakeProfiles(), activity_modes=activity_modes
    )

    profile = await service.create_profile(
        ProfileCreate(name="새 사용자", sittingHeightCm=80, standingHeightCm=105)
    )

    assert profile.name == "새 사용자"
    assert activity_modes.created == []
