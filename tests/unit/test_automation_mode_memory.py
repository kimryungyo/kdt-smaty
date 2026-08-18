"""자리를 비운 뒤 모드 기억과 사용 시간 정지 규칙을 검증한다."""

from datetime import UTC, datetime

import pytest

from smart_desk.config.settings import AutomationSettings
from smart_desk.modules.automation.service import AutomationService
from smart_desk.modules.desk.models import (
    DeskSnapshot, DeskState, HeightProvenance, HeightSnapshot, HeightStatus,
    RelayEvent, RelaySnapshot, RelayState,
)
from smart_desk.modules.identity.models import CurrentUserSnapshot, SessionKind
from smart_desk.modules.profiles.models import ActivityMode, EffectiveActivityMode
from smart_desk.modules.vision.models import (
    CameraObservation, PostureStatus, PresenceStatus, VisionSnapshot,
)


NOW = datetime(2026, 8, 18, tzinfo=UTC)
PROFILE = "profile-" + "1" * 32
STUDY = "mode-" + "2" * 32


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeUsers:
    def __init__(self) -> None:
        self.current: CurrentUserSnapshot | None = None

    async def snapshot(self) -> CurrentUserSnapshot | None:
        return self.current

    async def is_current(self, session_id: str) -> bool:
        return self.current is not None and self.current.session_id == session_id

    async def subscribe(self, callback):  # type: ignore[no-untyped-def]
        async def unsubscribe() -> None:
            return None

        return unsubscribe


class FakeModes:
    """default와 공부 모드만 아는 최소 저장소다."""

    def __init__(self) -> None:
        self.missing: set[str] = set()

    async def list_effective_modes(self, _profile_id: str) -> list[EffectiveActivityMode]:
        return [EffectiveActivityMode(
            key="default", kind="DEFAULT", name="기본", sitting_height_cm=75,
            standing_height_cm=110, led_color=None, tilt_level=None,
            description=None, editable=False,
        )]

    async def get_mode_for_profile(self, _profile_id: str, mode_id: str) -> ActivityMode:
        if mode_id in self.missing:
            raise KeyError(mode_id)
        return ActivityMode(
            id=mode_id, profile_id=PROFILE, name="공부", sitting_height_cm=80,
            standing_height_cm=112, led_color=None, tilt_level=None, description=None,
        )

    async def delete_mode(self, _mode_id: str) -> None:
        return None


class FakeDesk:
    def get_snapshot(self) -> DeskSnapshot:
        return DeskSnapshot(
            DeskState.IDLE,
            HeightSnapshot(90.0, NOW, HeightStatus.ONLINE, HeightProvenance.LIVE),
            RelaySnapshot(RelayEvent.STOPPED, RelayState.STOP, "1", None, None, NOW, None),
            None, None, "", None, NOW,
        )

    async def set_target(self, height_cm: float) -> None: ...
    async def stop_motion(self, reason: str = "") -> None: ...
    async def hold_up(self) -> None: ...
    async def hold_down(self) -> None: ...


def snapshot_of(pair: tuple[float, float] = (1.0, 1.0)) -> VisionSnapshot:
    upper = CameraObservation(True, pair[0], pair[0], NOW, None, 1)
    lower = CameraObservation(True, pair[1], pair[1], NOW, None, 1, PostureStatus.SITTING)
    return VisionSnapshot(
        upper, lower, PresenceStatus.PRESENT_SINGLE, PresenceStatus.PRESENT_SINGLE,
        PostureStatus.SITTING, PostureStatus.SITTING, NOW, NOW, True, (),
    )


class FakeVision:
    def get_snapshot(self) -> VisionSnapshot:
        return snapshot_of()


class RecordingUsage:
    """어떤 순서로 구간이 열리고 닫혔는지만 남긴다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str | None]] = []

    async def start_interval(self, profile_id: str, mode_key: str, mode_name: str) -> None:
        self.calls.append(("start", profile_id, mode_key))

    async def close_open_intervals(self, profile_id: str | None = None) -> None:
        self.calls.append(("close", profile_id, None))


async def install(service: AutomationService, users: FakeUsers, session_id: str,
                  mode_key: str = "default") -> None:
    users.current = user(session_id)
    await service._install_session(
        users.current, await service._read_mode(PROFILE, mode_key), None, snapshot_of(),
    )


def user(session_id: str) -> CurrentUserSnapshot:
    return CurrentUserSnapshot(session_id, SessionKind.REGISTERED, PROFILE, NOW, NOW)


@pytest.fixture
def parts():
    clock = Clock()
    users, modes, usage = FakeUsers(), FakeModes(), RecordingUsage()
    service = AutomationService(
        current_user=users, vision=FakeVision(), activity_modes=modes, desk=FakeDesk(),
        settings=AutomationSettings(execute_automatic_movements=False),
        usage=usage, mode_memory_seconds=1800.0,
        utc_now=lambda: NOW, monotonic=clock,
    )
    return service, users, modes, usage, clock


async def test_mode_returns_when_the_user_comes_back_within_the_window(parts) -> None:
    service, users, _modes, usage, clock = parts
    await install(service, users, "session-a")
    await service.set_activity_mode(STUDY, "session-a")

    assert service.get_snapshot().activity_mode.key == STUDY
    await service._end_session("session-a")

    clock.advance(600)  # 10분 뒤 복귀
    await install(service, users, "session-b", service._recall_mode(PROFILE) or "default")

    assert service.get_snapshot().activity_mode.key == STUDY


async def test_mode_is_forgotten_after_the_window(parts) -> None:
    service, users, _modes, _usage, clock = parts
    await install(service, users, "session-a")
    await service.set_activity_mode(STUDY, "session-a")
    await service._end_session("session-a")

    clock.advance(1801)  # 30분을 넘겼다.

    assert service._recall_mode(PROFILE) is None


async def test_usage_stops_at_session_end_and_resumes_on_return(parts) -> None:
    service, users, _modes, usage, clock = parts
    await install(service, users, "session-a")
    usage.calls.clear()
    await service.set_activity_mode(STUDY, "session-a")
    await service._end_session("session-a")

    # 자리를 비운 동안에는 새 구간을 열지 않는다.
    assert usage.calls == [("start", PROFILE, STUDY), ("close", PROFILE, None)]

    clock.advance(600)
    await install(service, users, "session-b", STUDY)

    assert usage.calls[-1] == ("start", PROFILE, STUDY)


async def test_forgotten_mode_falls_back_to_default(parts) -> None:
    service, users, modes, _usage, clock = parts
    await install(service, users, "session-a")
    await service.set_activity_mode(STUDY, "session-a")
    await service._end_session("session-a")
    modes.missing.add(STUDY)  # 그 사이 모드를 지웠다.

    recalled = service._recall_mode(PROFILE)
    assert recalled == STUDY
    with pytest.raises(Exception):
        await service._read_mode(PROFILE, recalled)
    # 호출 측은 default로 되돌아간다.
    assert (await service._read_mode(PROFILE, "default")).key == "default"
