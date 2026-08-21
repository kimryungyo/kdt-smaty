"""자리를 비운 뒤 모드 기억과 사용 시간 정지 규칙을 검증한다."""

import asyncio
from datetime import UTC, datetime

import pytest

from smart_desk.config.settings import AutomationSettings
from smart_desk.modules.automation.service import AutomationService
from smart_desk.modules.desk.models import (
    DeskSnapshot, DeskState, HeightProvenance, HeightSnapshot, HeightStatus,
    RelayEvent, RelaySnapshot, RelayState,
)
from smart_desk.modules.identity.models import CurrentUserSnapshot, SessionKind
from smart_desk.modules.profiles.models import ActivityMode, EffectiveActivityMode, Profile
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
        # 프로필이 정해 둔 기본 틸팅 단계. None이면 정하지 않은 것이다.
        self.default_tilt_level: int | None = None
        self.custom_tilt_level: int | None = None

    async def list_effective_modes(self, _profile_id: str) -> list[EffectiveActivityMode]:
        return [EffectiveActivityMode(
            key="default", kind="DEFAULT", name="기본", sitting_height_cm=75,
            standing_height_cm=110, led_color=None, led_brightness=None,
            tilt_level=self.default_tilt_level,
            description=None, editable=False,
        )]

    async def get_mode_for_profile(
        self, _profile_id: str, mode_id: str
    ) -> tuple[ActivityMode, Profile]:
        if mode_id in self.missing:
            raise KeyError(mode_id)
        # 높이는 프로필이 소유하므로 모드와 함께 소유 프로필을 돌려준다.
        return ActivityMode(
            id=mode_id, profile_id=PROFILE, name="공부", sitting_height_cm=80,
            standing_height_cm=112, led_color=None, led_brightness=None,
            tilt_level=self.custom_tilt_level, description=None,
        ), Profile(
            id=PROFILE, name="공부하는 사람", sitting_height_cm=80,
            standing_height_cm=112, led_color=None, led_brightness=None,
            tilt_level=None, description=None,
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
        settings=AutomationSettings(execute_automatic_movements=False,
                                    posture_confirmation_seconds=1.0),
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


class FakeGreeter:
    def __init__(self) -> None:
        self.greeted: list[str] = []

    def greet(self, profile_id: str | None) -> None:
        if profile_id is not None:
            self.greeted.append(profile_id)


async def test_greeting_follows_the_same_window_as_mode_memory(parts) -> None:
    """잠깐 자리를 비웠다 돌아온 것은 같은 방문이라 다시 인사하지 않는다."""

    service, users, _modes, _usage, clock = parts
    greeter = FakeGreeter()
    service.set_greeter(greeter)

    await install(service, users, "session-a")
    assert greeter.greeted == [PROFILE]          # 처음 왔으니 인사한다

    await service._end_session("session-a")
    clock.advance(600)                            # 10분 뒤 복귀
    await install(service, users, "session-b")
    assert greeter.greeted == [PROFILE]           # 같은 방문이라 조용하다

    await service._end_session("session-b")
    clock.advance(1801)                           # 30분을 넘겨 돌아왔다
    await install(service, users, "session-c")
    assert greeter.greeted == [PROFILE, PROFILE]  # 새 방문이라 다시 인사한다


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


class FakeTilt:
    """어느 단계로 옮기라는 요청을 받았는지만 남긴다."""

    def __init__(self, error: Exception | None = None) -> None:
        self.levels: list[int] = []
        self.error = error

    async def set_target(self, level: int) -> None:
        self.levels.append(level)
        if self.error is not None:
            raise self.error


async def drain_tilt(service: AutomationService) -> None:
    """예약된 틸팅 요청이 끝날 때까지 기다린다."""

    task = service._tilt_task
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)


async def test_tilt_moves_to_the_profile_default_on_first_sighting(parts) -> None:
    """처음 알아본 자리에서만 기본 틸팅으로 맞춘다."""

    service, users, modes, _usage, clock = parts
    modes.default_tilt_level = 1
    tilt = FakeTilt()
    service.set_tilt(tilt)

    await install(service, users, "session-a")
    await drain_tilt(service)
    assert tilt.levels == [1]                     # 처음 왔으니 1단계로 옮긴다

    await service._end_session("session-a")
    clock.advance(600)                            # 10분 뒤 복귀
    await install(service, users, "session-b")
    await drain_tilt(service)
    assert tilt.levels == [1]                     # 같은 방문이라 건드리지 않는다

    await service._end_session("session-b")
    clock.advance(1801)                           # 30분을 넘겨 돌아왔다
    await install(service, users, "session-c")
    await drain_tilt(service)
    assert tilt.levels == [1, 1]                  # 새 방문이라 다시 맞춘다


async def test_tilt_stays_when_the_mode_sets_no_level(parts) -> None:
    """단계를 정하지 않은 프로필은 지금 각도를 그대로 둔다."""

    service, users, modes, _usage, _clock = parts
    modes.default_tilt_level = None
    tilt = FakeTilt()
    service.set_tilt(tilt)

    await install(service, users, "session-a")
    await drain_tilt(service)

    assert tilt.levels == []


async def test_session_installs_even_when_tilt_fails(parts) -> None:
    """틸팅이 거절해도 session 설치와 인사는 그대로 이어진다."""

    service, users, modes, _usage, _clock = parts
    modes.default_tilt_level = 2
    tilt = FakeTilt(error=RuntimeError("틸팅 제어기가 실행 중이 아닙니다."))
    greeter = FakeGreeter()
    service.set_tilt(tilt)
    service.set_greeter(greeter)

    await install(service, users, "session-a")
    await drain_tilt(service)

    assert tilt.levels == [2]
    assert greeter.greeted == [PROFILE]
    assert service.get_snapshot().session_id == "session-a"


async def test_tilt_moves_when_the_user_picks_a_mode(parts) -> None:
    """모드를 고르면 그 모드가 정한 단계로 옮긴다.

    session 설치와 달리 사용자가 방금 직접 고른 것이므로, 같은 방문 안에서
    다시 골라도 매번 옮긴다.
    """

    service, users, modes, _usage, _clock = parts
    modes.default_tilt_level = None
    modes.custom_tilt_level = 3
    tilt = FakeTilt()
    service.set_tilt(tilt)

    await install(service, users, "session-a")
    await drain_tilt(service)
    assert tilt.levels == []                      # 기본 모드는 단계를 정하지 않았다

    await service.set_activity_mode(STUDY, "session-a")
    await drain_tilt(service)
    assert tilt.levels == [3]                     # 고른 모드의 단계로 옮긴다

    await service.set_activity_mode(STUDY, "session-a")
    await drain_tilt(service)
    assert tilt.levels == [3, 3]                  # 다시 골랐으니 다시 맞춘다


async def test_picking_a_mode_without_a_level_leaves_the_tilt_alone(parts) -> None:
    """단계를 정하지 않은 모드를 고르면 지금 각도를 그대로 둔다."""

    service, users, modes, _usage, _clock = parts
    modes.default_tilt_level = None
    modes.custom_tilt_level = None
    tilt = FakeTilt()
    service.set_tilt(tilt)

    await install(service, users, "session-a")
    await service.set_activity_mode(STUDY, "session-a")
    await drain_tilt(service)

    assert tilt.levels == []
