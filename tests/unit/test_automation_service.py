"""Pure-fake policy tests for :mod:`smart_desk.modules.automation.service`."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from smart_desk.config.settings import AutomationSettings
from smart_desk.modules.automation.models import AutomationState, ControlMode, HeightPolicy
from smart_desk.modules.automation.service import (
    AutomationConflictError, AutomationNotFoundError, AutomationService,
)
from smart_desk.modules.desk.models import (
    DeskSnapshot, DeskState, Direction, HeightProvenance, HeightSnapshot,
    HeightStatus, RelayEvent, RelaySnapshot, RelayState,
)
from smart_desk.modules.identity.models import CurrentUserSnapshot, SessionKind
from smart_desk.modules.profiles.models import ActivityMode, EffectiveActivityMode
from smart_desk.modules.vision.models import (
    BlockCode, CameraObservation, PostureStatus, PresenceStatus, VisionSnapshot,
)


NOW = datetime(2026, 8, 17, tzinfo=UTC)
PROFILE = "profile-" + "1" * 32


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeUsers:
    def __init__(self, current: CurrentUserSnapshot | None) -> None:
        self.current = current
        self.callback = None
        self.subscriptions = 0

    async def snapshot(self) -> CurrentUserSnapshot | None:
        return self.current

    async def is_current(self, session_id: str) -> bool:
        return self.current is not None and self.current.session_id == session_id

    async def subscribe(self, callback):  # type: ignore[no-untyped-def]
        self.subscriptions += 1
        self.callback = callback

        async def unsubscribe() -> None:
            self.callback = None

        return unsubscribe


class FakeVision:
    def __init__(self, snapshot: VisionSnapshot) -> None:
        self.snapshot = snapshot

    def get_snapshot(self) -> VisionSnapshot:
        return self.snapshot


class FakeModes:
    def __init__(self, default: EffectiveActivityMode | None = None,
                 custom: EffectiveActivityMode | None = None) -> None:
        self.default = default
        self.custom = custom
        self.fail = False
        self.before_list = None

    async def list_effective_modes(self, _profile_id: str) -> list[EffectiveActivityMode]:
        if self.before_list is not None:
            callback, self.before_list = self.before_list, None
            callback()
        if self.fail:
            raise RuntimeError("storage down")
        return [] if self.default is None else [self.default]

    async def get_mode_for_profile(self, _profile_id: str, mode_id: str):  # type: ignore[no-untyped-def]
        if self.custom is not None and self.custom.key == mode_id:
            return ActivityMode(id=mode_id, profile_id=PROFILE, name=self.custom.name,
                                sitting_height_cm=self.custom.sitting_height_cm,
                                standing_height_cm=self.custom.standing_height_cm,
                                led_color=self.custom.led_color,
                                led_brightness=self.custom.led_brightness,
                                tilt_level=self.custom.tilt_level,
                                description=self.custom.description)
        raise AutomationNotFoundError("missing")

    async def delete_mode(self, _mode_id: str) -> None:
        return None


class FakeDesk:
    def __init__(self, *, height: float = 90.0) -> None:
        self.calls: list[tuple[str, object]] = []
        self.raise_stop = False
        self.stop_started: asyncio.Event | None = None
        self.release_stop: asyncio.Event | None = None
        self.target_started: asyncio.Event | None = None
        self.release_target: asyncio.Event | None = None
        self.snapshot = self._snapshot(height)

    def delay_next_stop(self) -> tuple[asyncio.Event, asyncio.Event]:
        self.stop_started = asyncio.Event()
        self.release_stop = asyncio.Event()
        return self.stop_started, self.release_stop

    def delay_next_target(self) -> tuple[asyncio.Event, asyncio.Event]:
        self.target_started = asyncio.Event()
        self.release_target = asyncio.Event()
        return self.target_started, self.release_target

    @staticmethod
    def _snapshot(height: float) -> DeskSnapshot:
        return DeskSnapshot(
            DeskState.IDLE,
            HeightSnapshot(height, NOW, HeightStatus.ONLINE, HeightProvenance.LIVE),
            RelaySnapshot(RelayEvent.STOPPED, RelayState.STOP, "1", None, None, NOW, None),
            None, None, "", None, NOW,
        )

    async def set_target(self, height_cm: float) -> None:
        self.calls.append(("target", height_cm))
        # The real adapter can have changed physical desk intent before its
        # coroutine yields. Model that side effect before an injected delay.
        self.snapshot = DeskSnapshot(DeskState.MOVING, self.snapshot.height, self.snapshot.relay,
                                     height_cm, Direction.UP, "", None, NOW)
        if self.target_started is not None and self.release_target is not None:
            started, release = self.target_started, self.release_target
            self.target_started = self.release_target = None
            started.set()
            await release.wait()

    async def stop_motion(self, reason: str = "") -> None:
        self.calls.append(("stop", reason))
        if self.stop_started is not None and self.release_stop is not None:
            started, release = self.stop_started, self.release_stop
            self.stop_started = self.release_stop = None
            started.set()
            await release.wait()
        if self.raise_stop:
            raise RuntimeError("stop failed")

    async def hold_up(self) -> None:
        self.calls.append(("hold", "UP"))

    async def hold_down(self) -> None:
        self.calls.append(("hold", "DOWN"))

    def get_snapshot(self) -> DeskSnapshot:
        return self.snapshot


class FakeWled:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.fail = False

    async def turn_off(self) -> None:
        self.calls.append(("off", None))
        if self.fail:
            raise RuntimeError("wled down")

    async def set_solid(self, color: str) -> None:
        self.calls.append(("color", color))
        if self.fail:
            raise RuntimeError("wled down")

    async def set_brightness(self, brightness: int) -> None:
        self.calls.append(("brightness", brightness))
        if self.fail:
            raise RuntimeError("wled down")


def user(session_id: str = "session-a", *, registered: bool = False) -> CurrentUserSnapshot:
    return CurrentUserSnapshot(session_id, SessionKind.REGISTERED if registered else SessionKind.ANONYMOUS,
                               PROFILE if registered else None, NOW, NOW)


def mode() -> EffectiveActivityMode:
    return EffectiveActivityMode(key="default", kind="DEFAULT", name="Default", sitting_height_cm=80,
                                 standing_height_cm=112, led_color="112233", led_brightness=None, tilt_level=None,
                                 description=None, editable=False)


def focus_mode() -> EffectiveActivityMode:
    return EffectiveActivityMode(key="mode-" + "2" * 32, kind="CUSTOM", name="Focus", sitting_height_cm=85,
                                 standing_height_cm=115, led_color="445566", led_brightness=None, tilt_level=None,
                                 description=None, editable=True)


def vision(pair: tuple[float, float], *, posture: PostureStatus = PostureStatus.SITTING,
           vacant: bool = False, usable: bool = True,
           reasons: tuple[BlockCode, ...] = ()) -> VisionSnapshot:
    count = 0 if vacant else 1
    upper = CameraObservation(True, pair[0], pair[0], NOW, None, count)
    lower = CameraObservation(True, pair[1], pair[1], NOW, None, count, posture)
    presence = PresenceStatus.VACANT if vacant else PresenceStatus.PRESENT_SINGLE
    return VisionSnapshot(upper, lower, presence, presence, posture, posture, NOW, NOW,
                          usable if not vacant else False,
                          reasons if not vacant else (BlockCode.PRESENCE_NOT_SINGLE,))


def service_for(*, users: FakeUsers, camera: FakeVision, desk: FakeDesk, clock: Clock,
                execute: bool = False, modes: FakeModes | None = None, wled: FakeWled | None = None) -> AutomationService:
    return AutomationService(current_user=users, vision=camera, activity_modes=modes or FakeModes(), desk=desk,
                             wled=wled, settings=AutomationSettings(execute_automatic_movements=execute,
                                                         posture_confirmation_seconds=1.0),
                             utc_now=lambda: NOW, monotonic=clock)


async def observe(service: AutomationService, camera: FakeVision, pair: tuple[float, float], clock: Clock,
                  seconds: float = 0, **kwargs: object) -> None:
    clock.advance(seconds)
    camera.snapshot = vision(pair, **kwargs)  # type: ignore[arg-type]
    await service._observe_once()
    await asyncio.sleep(0)


async def flush_background_tasks() -> None:
    """Let a queued best-effort fake adapter request complete."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def test_auto_completed_target_requires_sustained_rearm_drift() -> None:
    """Small post-settle height variation must not repeatedly click the relay."""

    clock, desk, users = Clock(), FakeDesk(height=75.0), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)

    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    assert desk.calls == []

    desk.snapshot = FakeDesk._snapshot(73.4)
    await observe(service, camera, (4, 4), clock)
    await observe(service, camera, (5, 5), clock, 2.9)
    assert desk.calls == []

    await observe(service, camera, (6, 6), clock, 0.1)
    await flush_background_tasks()
    assert desk.calls == [("target", 75.0)]


async def test_anonymous_baseline_then_one_second_and_targets() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    assert service.get_snapshot().target_height_cm == 75
    await observe(service, camera, (4, 4), clock, posture=PostureStatus.STANDING)
    await observe(service, camera, (5, 5), clock, 1.0, posture=PostureStatus.STANDING)
    assert service.get_snapshot().target_height_cm == 110
    assert [call for call in desk.calls if call[0] == "target"] == []


async def test_registered_default_and_default_failure_recover_without_loop_death() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user(registered=True))
    camera, modes = FakeVision(vision((1, 1))), FakeModes(mode())
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, modes=modes)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    assert service.get_snapshot().activity_mode == mode()
    assert service.get_snapshot().target_height_cm == 80
    users.current = user("session-b", registered=True)
    modes.fail = True
    await observe(service, camera, (4, 4), clock)
    assert "DEFAULT_ACTIVITY_MODE_UNAVAILABLE" in service.get_snapshot().blocked_reason_codes
    assert service.get_snapshot().state is AutomationState.OBSERVING
    modes.fail = False
    await observe(service, camera, (5, 5), clock)
    assert service.get_snapshot().activity_mode == mode()
    assert "DEFAULT_ACTIVITY_MODE_UNAVAILABLE" not in service.get_snapshot().blocked_reason_codes


async def test_anonymous_manual_upgrade_preserves_manual_and_only_applies_mode_led() -> None:
    clock, desk, users, led = Clock(), FakeDesk(), FakeUsers(user()), FakeWled()
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock,
                          modes=FakeModes(mode()), wled=led)
    await observe(service, camera, (1, 1), clock)
    await flush_background_tasks()
    assert led.calls == [("off", None)]
    led.calls.clear()
    await service.hold(Direction.UP)
    users.current = user("session-b", registered=True)
    await observe(service, camera, (2, 2), clock)
    await flush_background_tasks()
    snapshot = service.get_snapshot()
    assert snapshot.session_id == "session-b"
    assert snapshot.control_mode is ControlMode.MANUAL
    assert snapshot.activity_mode == mode()
    assert snapshot.target_height_cm is None
    assert desk.calls == [("hold", "UP")]
    assert led.calls[-1] == ("color", "112233")


async def test_anonymous_auto_upgrade_immediately_selects_profile_target() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, modes=FakeModes(mode()))
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    assert service.get_snapshot().target_height_cm == 75
    users.current = user("session-b", registered=True)
    await observe(service, camera, (4, 4), clock)
    snapshot = service.get_snapshot()
    assert snapshot.session_id == "session-b"
    assert snapshot.control_mode is ControlMode.AUTO
    assert snapshot.target_height_cm == 80
    assert snapshot.initial_move_due_at is None
    assert not [call for call in desk.calls if call[0] == "target"]


async def test_live_anonymous_upgrade_discards_profile_effects_after_current_user_changes_during_stop() -> None:
    clock, desk, users, led = Clock(), FakeDesk(), FakeUsers(user()), FakeWled()
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True,
                          modes=FakeModes(mode()), wled=led)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    await flush_background_tasks()
    led.calls.clear()
    stop_started, release_stop = desk.delay_next_stop()
    users.current = user("session-b", registered=True)
    upgrade = asyncio.create_task(observe(service, camera, (4, 4), clock))
    await stop_started.wait()
    users.current = user("session-c", registered=True)
    release_stop.set()
    await upgrade
    await flush_background_tasks()

    assert ("color", "112233") not in led.calls
    assert ("target", 80) not in desk.calls


async def test_upgrade_stop_does_not_override_new_activity_selection() -> None:
    clock, desk, users, led = Clock(), FakeDesk(), FakeUsers(user()), FakeWled()
    camera = FakeVision(vision((1, 1)))
    modes = FakeModes(mode(), focus_mode())
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True,
                          modes=modes, wled=led)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    await flush_background_tasks()
    led.calls.clear()
    stop_started, release_stop = desk.delay_next_stop()
    users.current = user("session-b", registered=True)
    upgrade = asyncio.create_task(observe(service, camera, (4, 4), clock))
    await stop_started.wait()
    await service.set_activity_mode(focus_mode().key, "session-b")
    release_stop.set()
    await upgrade
    await flush_background_tasks()

    snapshot = service.get_snapshot()
    assert snapshot.activity_mode == focus_mode()
    assert snapshot.target_height_cm is None
    assert led.calls == [("color", "445566")]


async def test_anonymous_manual_upgrade_with_stop_failure_latch_is_blocked() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, modes=FakeModes(mode()))
    await observe(service, camera, (1, 1), clock)
    desk.raise_stop = True
    with pytest.raises(RuntimeError, match="stop failed"):
        await service.stop_motion()
    users.current = user("session-b", registered=True)
    await observe(service, camera, (2, 2), clock)

    snapshot = service.get_snapshot()
    assert snapshot.control_mode is ControlMode.MANUAL
    assert snapshot.state is AutomationState.BLOCKED
    assert "DESK_STOP_FAILED" in snapshot.blocked_reason_codes


async def test_upgrade_mode_read_race_does_not_install_late_profile_or_led() -> None:
    clock, desk, users, led = Clock(), FakeDesk(), FakeUsers(user()), FakeWled()
    camera, modes = FakeVision(vision((1, 1))), FakeModes(mode())
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, modes=modes, wled=led)
    await observe(service, camera, (1, 1), clock)
    await flush_background_tasks()
    assert led.calls == [("off", None)]
    led.calls.clear()
    users.current = user("session-b", registered=True)
    modes.before_list = lambda: setattr(users, "current", user("session-c", registered=True))
    await observe(service, camera, (2, 2), clock)
    await flush_background_tasks()
    assert service.get_snapshot().session_id == "session-a"
    assert led.calls == []


async def test_registered_replacement_keeps_the_two_second_initial_delay() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user("session-a", registered=True))
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, modes=FakeModes(mode()))
    await observe(service, camera, (1, 1), clock)
    users.current = user("session-b", registered=True)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 10)
    assert service.get_snapshot().target_height_cm is None
    await observe(service, camera, (4, 4), clock)
    await observe(service, camera, (5, 5), clock, 2)
    assert service.get_snapshot().target_height_cm == 80


async def test_same_or_partial_pair_does_not_complete_candidate() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (2, 2), clock, 9)
    assert service.get_snapshot().target_height_cm is None
    await observe(service, camera, (2, 3), clock, 9)
    assert service.get_snapshot().target_height_cm is None
    await observe(service, camera, (3, 3), clock, 9)
    assert service.get_snapshot().target_height_cm == 75


async def test_out_of_order_pair_does_not_advance_posture_candidate() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (3, 3), clock)
    await observe(service, camera, (2, 4), clock, 9)
    assert service.get_snapshot().target_height_cm is None
    await observe(service, camera, (4, 5), clock)
    assert service.get_snapshot().target_height_cm == 75


async def test_reauto_requires_new_one_second_distinct_pair() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    await observe(service, camera, (1, 1), clock)
    await service.set_control_mode(ControlMode.AUTO, "session-a")
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, .9)
    assert service.get_snapshot().target_height_cm is None
    await observe(service, camera, (4, 4), clock, .1)
    assert service.get_snapshot().target_height_cm == 75


async def test_tolerance_is_ready_and_live_target_is_not_duplicated() -> None:
    clock, desk, users = Clock(), FakeDesk(height=75), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    await observe(service, camera, (4, 4), clock)
    assert service.get_snapshot().state is AutomationState.READY
    assert not [call for call in desk.calls if call[0] == "target"]


async def test_stopped_before_target_is_not_reported_ready() -> None:
    """중간 정지는 목표 완료가 아니다. 다만 영구 차단으로 두지도 않는다.

    목표에 못 미치고 멈춘 것은 사실 기록일 뿐 고장이 아니다. BLOCKED로 굳혀
    두면 목표와 다른 높이에 멈춘 채 session이 바뀔 때까지 방치되므로, 다음
    관측에서 다시 목표를 세울 수 있어야 한다.
    """

    clock, desk, users = Clock(), FakeDesk(height=90), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    await flush_background_tasks()
    assert desk.snapshot.state is DeskState.MOVING

    desk.snapshot = DeskSnapshot(
        DeskState.STOPPED, desk.snapshot.height, desk.snapshot.relay,
        75, None, "중간 안전 정지", None, NOW,
    )
    await observe(service, camera, (4, 4), clock)

    snapshot = service.get_snapshot()
    # 완료로 보고하지 않는다(READY 아님). 재개 여지를 남기되 목표는 유지한다.
    assert snapshot.state is not AutomationState.READY
    assert snapshot.target_height_cm == 75


async def test_stopped_at_target_with_fresh_height_is_ready() -> None:
    """A STOP after a fresh in-tolerance measurement remains a completion."""

    clock, desk, users = Clock(), FakeDesk(height=90), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    await flush_background_tasks()
    desk.snapshot = DeskSnapshot(
        DeskState.STOPPED, HeightSnapshot(75, NOW, HeightStatus.ONLINE, HeightProvenance.LIVE),
        desk.snapshot.relay, 75, None, "목표 도달", None, NOW,
    )
    await observe(service, camera, (4, 4), clock)

    assert service.get_snapshot().state is AutomationState.READY


async def test_hold_and_target_preempt_shadow_without_safety_stop() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    await observe(service, camera, (1, 1), clock)
    await service.hold(Direction.UP)
    await service.set_target(90)
    assert desk.calls == [("hold", "UP"), ("target", 90)]
    assert service.get_snapshot().control_mode is ControlMode.MANUAL


async def test_user_bound_hold_and_target_reject_stale_or_missing_session() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    await observe(service, camera, (1, 1), clock)

    await service.hold(Direction.UP, "session-a")
    await service.set_target(89, "session-a")
    assert desk.calls == [("hold", "UP"), ("target", 89)]

    users.current = user("session-b")
    with pytest.raises(AutomationConflictError, match="SESSION_MISMATCH"):
        await service.hold(Direction.DOWN, "session-a")
    with pytest.raises(AutomationConflictError, match="SESSION_MISMATCH"):
        await service.set_target(90, "session-a")
    # Current user B alone is insufficient until AutomationService has also
    # installed B's snapshot.
    with pytest.raises(AutomationConflictError, match="SESSION_MISMATCH"):
        await service.set_target(88, "session-b")

    users.current = None
    with pytest.raises(AutomationConflictError, match="SESSION_MISMATCH"):
        await service.set_target(91, "session-a")
    assert desk.calls == [("hold", "UP"), ("target", 89)]


@pytest.mark.parametrize("command", ["hold", "target"])
async def test_user_bound_manual_rechecks_replaced_snapshot_before_mutation(
    command: str,
) -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    await observe(service, camera, (1, 1), clock)

    validate = service._validate_expected_session
    first_validation = True

    async def validate_then_install_b(expected_session_id: str | None) -> None:
        nonlocal first_validation
        await validate(expected_session_id)
        if first_validation:
            first_validation = False
            users.current = user("session-b")
            await observe(service, camera, (2, 2), clock)

    service._validate_expected_session = validate_then_install_b  # type: ignore[method-assign]

    with pytest.raises(AutomationConflictError, match="SESSION_MISMATCH"):
        if command == "hold":
            await service.hold(Direction.UP, "session-a")
        else:
            await service.set_target(90, "session-a")

    snapshot = service.get_snapshot()
    assert snapshot.session_id == "session-b"
    assert snapshot.control_mode is ControlMode.AUTO
    assert snapshot.state is AutomationState.OBSERVING
    assert not desk.calls


@pytest.mark.parametrize("command", ["hold", "target"])
async def test_user_bound_manual_race_keeps_safety_stop_but_rejects_stale_effect(
    command: str,
) -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    await asyncio.sleep(0)
    assert ("target", 75.0) in desk.calls

    stop_started, release_stop = desk.delay_next_stop()
    manual = asyncio.create_task(
        service.hold(Direction.UP, "session-a")
        if command == "hold" else service.set_target(90, "session-a")
    )
    await stop_started.wait()
    users.current = user("session-b")
    await observe(service, camera, (4, 4), clock)
    release_stop.set()

    with pytest.raises(AutomationConflictError, match="SESSION_MISMATCH"):
        await manual

    snapshot = service.get_snapshot()
    assert snapshot.session_id == "session-b"
    assert snapshot.control_mode is ControlMode.AUTO
    assert snapshot.state is AutomationState.OBSERVING
    assert len([call for call in desk.calls if call[0] == "stop"]) == 1
    assert ("hold", "UP") not in desk.calls
    assert ("target", 90) not in desk.calls


async def test_stop_remains_session_independent_during_session_replacement() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    await observe(service, camera, (1, 1), clock)
    users.current = user("session-b")

    await service.stop_motion()

    assert [call for call in desk.calls if call[0] == "stop"]


@pytest.mark.parametrize("command", ["hold", "target"])
async def test_preempting_live_automatic_stop_failure_prevents_manual_command(command: str) -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    await asyncio.sleep(0)
    desk.raise_stop = True

    with pytest.raises(RuntimeError, match="stop failed"):
        if command == "hold":
            await service.hold(Direction.UP)
        else:
            await service.set_target(90)

    assert service.get_snapshot().control_mode is ControlMode.MANUAL
    assert ("hold", "UP") not in desk.calls
    assert ("target", 90) not in desk.calls


async def test_user_stop_preserves_manual_intent_and_propagates_error() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    await observe(service, camera, (1, 1), clock)
    desk.raise_stop = True
    with pytest.raises(RuntimeError, match="stop failed"):
        await service.stop_motion()
    assert service.get_snapshot().control_mode is ControlMode.MANUAL


class FakeAnnouncer:
    def __init__(self) -> None:
        self.said: list[str] = []

    def say_soon(self, text: str) -> None:
        self.said.append(text)


async def test_height_change_is_announced_once_per_target() -> None:
    """움직일 때만, 그리고 같은 목표를 두 번 말하지 않는다."""

    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera, announcer = FakeVision(vision((1, 1))), FakeAnnouncer()
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    service.set_announcer(announcer)

    await service.set_target(104)
    await service.set_target(104)   # 같은 목표는 다시 말하지 않는다
    await service.set_target(88)

    # 지금 높이를 아는 상태이므로 오르내림까지 정확히 말한다.
    assert announcer.said == [
        "책상을 104센티미터로 올릴게요.",
        "책상을 88센티미터로 내릴게요.",
    ]


async def test_brief_posture_flicker_does_not_restart_the_confirmation() -> None:
    """자세를 바꾸는 동안 인식이 오락가락해도 5초를 다시 세지 않는다.

    실제로 재보니 자세가 2~3초마다 흔들렸고, 흔들릴 때마다 카운트가 0으로
    돌아가 앉은 지 20초가 지나서야 책상이 움직였다.
    """

    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = AutomationService(
        current_user=users, vision=camera, activity_modes=FakeModes(), desk=desk,
        settings=AutomationSettings(execute_automatic_movements=False,
                                    posture_confirmation_seconds=5.0,
                                    posture_flicker_grace_seconds=2.0),
        utc_now=lambda: NOW, monotonic=clock,
    )
    await observe(service, camera, (1, 1), clock)
    # 앉기 시작. 그 뒤 1초 간격으로 한 번씩 서기가 잘못 잡힌다.
    await observe(service, camera, (2, 2), clock, posture=PostureStatus.SITTING)
    await observe(service, camera, (3, 3), clock, 1, posture=PostureStatus.STANDING)
    await observe(service, camera, (4, 4), clock, 1, posture=PostureStatus.SITTING)
    await observe(service, camera, (5, 5), clock, 1, posture=PostureStatus.STANDING)
    await observe(service, camera, (6, 6), clock, 1, posture=PostureStatus.SITTING)
    assert service.get_snapshot().target_height_cm is None, "아직 5초가 안 됐다"

    await observe(service, camera, (7, 7), clock, 1, posture=PostureStatus.SITTING)
    assert service.get_snapshot().target_height_cm == 75, "흔들림에도 5초 만에 확인된다"


async def test_a_real_posture_change_still_switches() -> None:
    """오래 유지되는 반대 자세는 흔들림이 아니라 진짜 변화다."""

    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = AutomationService(
        current_user=users, vision=camera, activity_modes=FakeModes(), desk=desk,
        settings=AutomationSettings(execute_automatic_movements=False,
                                    posture_confirmation_seconds=5.0,
                                    posture_flicker_grace_seconds=2.0),
        utc_now=lambda: NOW, monotonic=clock,
    )
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock, posture=PostureStatus.SITTING)
    await observe(service, camera, (3, 3), clock, 5, posture=PostureStatus.SITTING)
    assert service.get_snapshot().target_height_cm == 75

    # 일어선다. 흔들림 유예를 지나 계속 서 있으면 서기 높이로 간다.
    # vision 값이 매번 달라야 새 관찰로 취급된다.
    for step in range(6):
        await observe(service, camera, (9 + step, 9 + step), clock, 1,
                      posture=PostureStatus.STANDING)
    assert service.get_snapshot().target_height_cm == 110


async def test_activity_mode_reselect_keeps_auto_but_set_target_does_not() -> None:
    """자동 제어에서 높이를 반영하는 두 경로의 차이를 못박는다.

    직접 목표를 주면 서버는 그 순간 수동으로 바꾼다. 대시보드가 자동에서
    프로필 높이를 반영할 때 모드를 다시 고르는 이유다.
    """

    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user("a", registered=True))
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock,
                          modes=FakeModes(mode()))
    await observe(service, camera, (1, 1), clock)
    await service.set_control_mode(ControlMode.AUTO, "a")
    assert service.get_snapshot().control_mode is ControlMode.AUTO

    await service.set_activity_mode("default", "a")
    assert service.get_snapshot().control_mode is ControlMode.AUTO, "모드 재선택은 자동을 지킨다"

    await service.set_target(95, "a")
    assert service.get_snapshot().control_mode is ControlMode.MANUAL, "직접 목표는 수동으로 바꾼다"


async def test_study_schedule_ramps_up_while_the_mode_stays_on() -> None:
    """공부 모드는 앉아 있는 동안 구간이 넘어갈 때마다 조명을 다시 보낸다."""

    from smart_desk.modules.profiles.led_schedule import DEFAULT_STUDY_SCHEDULE

    clock, desk, users, led = Clock(), FakeDesk(), FakeUsers(user("a", registered=True)), FakeWled()
    camera = FakeVision(vision((1, 1)))
    study = EffectiveActivityMode(
        key="default", kind="DEFAULT", name="공부", sitting_height_cm=80,
        standing_height_cm=112, led_color=None, led_brightness=None,
        led_schedule=DEFAULT_STUDY_SCHEDULE, tilt_level=None, description=None,
        editable=False,
    )
    service = service_for(users=users, camera=camera, desk=desk, clock=clock,
                          modes=FakeModes(study), wled=led)
    await observe(service, camera, (1, 1), clock)
    await asyncio.sleep(0)
    await service.set_activity_mode("default", "a")
    await asyncio.sleep(.01)
    assert led.calls[-2:] == [("brightness", 153), ("color", "FFD6A4")]   # 0분 4000K

    clock.advance(4 * 60)                       # 4분 경과
    await observe(service, camera, (2, 2), clock)
    await asyncio.sleep(.01)
    assert led.calls[-2:] == [("brightness", 179), ("color", "FFE0B5")]   # 4500K

    clock.advance(6 * 60)                       # 10분 경과
    await observe(service, camera, (3, 3), clock)
    await asyncio.sleep(.01)
    assert led.calls[-2:] == [("brightness", 255), ("color", "FFF6D8")]   # 6000K

    before_idle = len(led.calls)
    clock.advance(30 * 60)                      # 그 뒤로는 더 보내지 않는다
    await observe(service, camera, (4, 4), clock)
    await asyncio.sleep(.01)
    assert len(led.calls) == before_idle


async def test_mode_brightness_is_applied_before_its_colour() -> None:
    """색이 켜지는 순간 이미 그 모드의 밝기여야 직전 밝기가 스치지 않는다."""

    clock, desk, users, led = Clock(), FakeDesk(), FakeUsers(user("a", registered=True)), FakeWled()
    camera = FakeVision(vision((1, 1)))
    dim = EffectiveActivityMode(
        key="default", kind="DEFAULT", name="독서", sitting_height_cm=80,
        standing_height_cm=112, led_color="112233", led_brightness=30,
        tilt_level=None, description=None, editable=False,
    )
    service = service_for(users=users, camera=camera, desk=desk, clock=clock,
                          modes=FakeModes(dim), wled=led)
    await observe(service, camera, (1, 1), clock)
    await asyncio.sleep(0)
    await service.set_activity_mode("default", "a")
    await asyncio.sleep(.01)

    assert led.calls[-2:] == [("brightness", 30), ("color", "112233")]


async def test_mode_without_brightness_leaves_the_current_level_alone() -> None:
    """밝기를 지정하지 않은 모드는 지금 밝기를 건드리지 않는다."""

    clock, desk, users, led = Clock(), FakeDesk(), FakeUsers(user("a", registered=True)), FakeWled()
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock,
                          modes=FakeModes(mode()), wled=led)
    await observe(service, camera, (1, 1), clock)
    await asyncio.sleep(0)
    await service.set_activity_mode("default", "a")
    await asyncio.sleep(.01)

    assert not any(call == "brightness" for call, _ in led.calls)


async def test_wled_session_order_and_failure_never_rolls_back_mode() -> None:
    clock, desk, users, led = Clock(), FakeDesk(), FakeUsers(user("a", registered=True)), FakeWled()
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, modes=FakeModes(mode()), wled=led)
    await observe(service, camera, (1, 1), clock)
    await asyncio.sleep(0)
    users.current = user("b", registered=True)
    await observe(service, camera, (2, 2), clock)
    await asyncio.sleep(0)
    assert led.calls[-2:] == [("off", None), ("color", "112233")]
    led.fail = True
    await service.set_activity_mode("default", "b")
    await asyncio.sleep(.01)
    assert service.get_snapshot().activity_mode == mode()
    assert "WLED_UNAVAILABLE" in service.get_snapshot().blocked_reason_codes


async def test_auto_stop_failure_keeps_auto_selection() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    await observe(service, camera, (1, 1), clock)
    desk.raise_stop = True
    with pytest.raises(RuntimeError):
        await service.set_control_mode(ControlMode.AUTO, "session-a")
    assert service.get_snapshot().control_mode is ControlMode.AUTO


async def test_live_dispatch_rejects_a_session_that_changed_before_side_effect() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    users.current = None
    await observe(service, camera, (3, 3), clock, 2)
    await asyncio.sleep(0)
    assert not [call for call in desk.calls if call[0] == "target"]


async def test_vacant_session_end_waits_for_the_running_target_to_finish() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    await flush_background_tasks()
    assert service.get_snapshot().state is AutomationState.MOVING

    users.current = None
    await observe(service, camera, (4, 4), clock, vacant=True)
    assert service.get_snapshot().session_id == "session-a"
    assert service.get_snapshot().state is AutomationState.MOVING
    assert not [call for call in desk.calls if call[0] == "stop"]

    desk.snapshot = DeskSnapshot(
        DeskState.STOPPED,
        HeightSnapshot(75.0, NOW, HeightStatus.ONLINE, HeightProvenance.LIVE),
        desk.snapshot.relay,
        None,
        None,
        "target reached",
        None,
        NOW,
    )
    await observe(service, camera, (5, 5), clock, vacant=True)
    assert service.get_snapshot().session_id is None
    assert service.get_snapshot().state is AutomationState.PARK_WAITING
    assert not [call for call in desk.calls if call[0] == "stop"]


async def test_vision_uncertainty_does_not_stop_a_live_automatic_target() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    desk.snapshot = DeskSnapshot(DeskState.MOVING, desk.snapshot.height, desk.snapshot.relay,
                                 75, Direction.UP, "", None, NOW)
    await observe(service, camera, (4, 4), clock, usable=False,
                  reasons=(BlockCode.MULTIPLE_PEOPLE,))
    await observe(service, camera, (5, 5), clock, usable=False,
                  reasons=(BlockCode.MULTIPLE_PEOPLE,))
    assert not [call for call in desk.calls if call[0] == "stop"]
    assert service.get_snapshot().state is AutomationState.MOVING


async def test_vision_uncertainty_during_dispatch_keeps_the_accepted_target() -> None:

    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    target_started, release_target = desk.delay_next_target()
    dispatch = asyncio.create_task(observe(service, camera, (3, 3), clock, 2))
    await target_started.wait()

    blocked = asyncio.create_task(
        observe(service, camera, (4, 4), clock, usable=False, reasons=(BlockCode.MULTIPLE_PEOPLE,))
    )
    await asyncio.sleep(0)
    assert not [call for call in desk.calls if call[0] == "stop"]

    release_target.set()
    await dispatch
    await blocked

    assert desk.calls == [("target", 75.0)]
    snapshot = service.get_snapshot()
    assert snapshot.state is AutomationState.MOVING
    assert snapshot.blocked_reason_codes == ()


async def test_live_automatic_desk_error_is_reflected_as_blocked_not_moving() -> None:
    """Physical Desk errors must win over a previously accepted AUTO intent."""

    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    await flush_background_tasks()
    assert service.get_snapshot().state is AutomationState.MOVING

    desk.snapshot = DeskSnapshot(
        DeskState.ERROR,
        desk.snapshot.height,
        desk.snapshot.relay,
        None,
        None,
        "relay stopped",
        "unexpected relay stop",
        NOW,
    )
    await observe(service, camera, (4, 4), clock)

    # 릴레이가 정상 STOP을 보고하는 한 Desk ERROR는 상위 판단으로 이동이 끊긴
    # 흔적일 뿐이다. 다음 관측에서 다시 목표를 세울 수 있어야, 목표와 다른
    # 높이로 멈춘 채 세션이 바뀔 때까지 방치되지 않는다.
    snapshot = service.get_snapshot()
    assert snapshot.state is AutomationState.OBSERVING
    assert snapshot.blocked_reason_codes == ()
    assert snapshot.last_transition_reason == "DESK_ERROR"


async def test_desk_error_stays_blocked_when_the_relay_is_not_recoverable() -> None:
    """릴레이가 응답하지 않으면 재개하지 않고 그대로 막는다."""

    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    await flush_background_tasks()
    assert service.get_snapshot().state is AutomationState.MOVING

    desk.snapshot = DeskSnapshot(
        DeskState.ERROR,
        desk.snapshot.height,
        RelaySnapshot(RelayEvent.OFFLINE, None, "1", None, None, NOW, "relay offline"),
        None,
        None,
        "relay offline",
        "relay offline",
        NOW,
    )
    await observe(service, camera, (4, 4), clock)

    snapshot = service.get_snapshot()
    assert snapshot.state is AutomationState.BLOCKED
    assert snapshot.blocked_reason_codes == ("DESK_ERROR",)

    desk.snapshot = DeskSnapshot(
        DeskState.ERROR,
        desk.snapshot.height,
        RelaySnapshot(
            RelayEvent.HEARTBEAT,
            RelayState.STOP,
            "1",
            "ready",
            None,
            NOW + timedelta(seconds=1),
            None,
        ),
        None,
        None,
        "relay recovered",
        None,
        NOW + timedelta(seconds=1),
    )
    await observe(service, camera, (5, 5), clock)
    snapshot = service.get_snapshot()
    assert snapshot.state is AutomationState.OBSERVING
    assert "DESK_ERROR" not in snapshot.blocked_reason_codes
    assert snapshot.last_transition_reason == "DESK_RECOVERED"


async def test_vision_recovery_uses_first_usable_pair_only_as_baseline() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, usable=False)
    await observe(service, camera, (4, 4), clock, 100)
    assert service.get_snapshot().posture_candidate is None
    await observe(service, camera, (5, 5), clock)
    await observe(service, camera, (6, 6), clock, .9)
    assert service.get_snapshot().target_height_cm is None
    await observe(service, camera, (7, 7), clock, .1)
    assert service.get_snapshot().target_height_cm == 75


async def test_vision_loss_does_not_duplicate_or_interrupt_the_live_target() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(
        users=users, camera=camera, desk=desk, clock=clock, execute=True
    )
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    await asyncio.sleep(0)
    assert len([call for call in desk.calls if call[0] == "target"]) == 1

    await observe(service, camera, (4, 4), clock, usable=False)
    await observe(service, camera, (5, 5), clock)  # recovery freshness baseline
    await observe(service, camera, (6, 6), clock)  # new posture hold
    await observe(service, camera, (7, 7), clock, 2)
    await asyncio.sleep(0)

    targets = [call for call in desk.calls if call[0] == "target"]
    assert targets == [("target", 75.0)]


async def test_vision_loss_never_calls_stop_even_if_stop_transport_is_down() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    await asyncio.sleep(0)
    desk.raise_stop = True
    await observe(service, camera, (4, 4), clock, usable=False)
    snapshot = service.get_snapshot()
    assert snapshot.control_mode is ControlMode.AUTO
    assert snapshot.state is AutomationState.MOVING
    assert snapshot.blocked_reason_codes == ()


async def test_stop_failure_latches_across_vision_and_only_successful_user_stop_clears_it() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    await observe(service, camera, (1, 1), clock)
    await observe(service, camera, (2, 2), clock)
    await observe(service, camera, (3, 3), clock, 2)
    await asyncio.sleep(0)
    desk.raise_stop = True
    users.current = user("session-b")
    await observe(service, camera, (4, 4), clock)
    await observe(service, camera, (5, 5), clock)
    snapshot = service.get_snapshot()
    assert snapshot.state is AutomationState.BLOCKED
    assert "DESK_STOP_FAILED" in snapshot.blocked_reason_codes
    assert not [call for call in desk.calls if call[0] == "target"][1:]
    desk.raise_stop = False
    await service.stop_motion()
    snapshot = service.get_snapshot()
    assert snapshot.control_mode is ControlMode.MANUAL
    assert snapshot.state is AutomationState.MANUAL
    assert "DESK_STOP_FAILED" not in snapshot.blocked_reason_codes


async def test_sessionless_user_stop_recovers_stop_latch_to_waiting() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(None)
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    desk.raise_stop = True

    with pytest.raises(RuntimeError, match="stop failed"):
        await service.stop_motion()

    blocked = service.get_snapshot()
    assert blocked.state is AutomationState.BLOCKED
    assert "DESK_STOP_FAILED" in blocked.blocked_reason_codes
    desk.raise_stop = False
    await service.stop_motion()

    snapshot = service.get_snapshot()
    assert snapshot.session_id is None
    assert snapshot.blocked_reason_codes == ()
    assert snapshot.state is AutomationState.WAITING_USER
    assert snapshot.height_policy is None
    assert "DESK_STOP_FAILED" not in snapshot.blocked_reason_codes


async def test_newer_live_relay_stop_automatically_recovers_stop_latch() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user())
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    await observe(service, camera, (1, 1), clock)
    desk.raise_stop = True
    with pytest.raises(RuntimeError, match="stop failed"):
        await service.stop_motion()
    assert "DESK_STOP_FAILED" in service.get_snapshot().blocked_reason_codes

    desk.raise_stop = False
    desk.snapshot = DeskSnapshot(
        DeskState.STOPPED,
        desk.snapshot.height,
        RelaySnapshot(
            RelayEvent.HEARTBEAT,
            RelayState.STOP,
            "1",
            "ready",
            None,
            NOW + timedelta(seconds=1),
            None,
        ),
        None,
        None,
        "stopped",
        None,
        NOW + timedelta(seconds=1),
    )
    await observe(service, camera, (2, 2), clock)
    snapshot = service.get_snapshot()
    assert snapshot.state is AutomationState.MANUAL
    assert "DESK_STOP_FAILED" not in snapshot.blocked_reason_codes
    assert snapshot.last_transition_reason == "DESK_STOP_RECOVERED"


async def test_sessionless_stop_failure_latch_blocks_fresh_vacant_park() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(None)
    camera = FakeVision(vision((1, 1), vacant=True))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    desk.raise_stop = True

    with pytest.raises(RuntimeError, match="stop failed"):
        await service.stop_motion()

    await observe(service, camera, (1, 1), clock, vacant=True)
    await observe(service, camera, (2, 2), clock, vacant=True)
    await observe(service, camera, (3, 3), clock, 30, vacant=True)

    snapshot = service.get_snapshot()
    assert snapshot.state is AutomationState.BLOCKED
    assert "DESK_STOP_FAILED" in snapshot.blocked_reason_codes
    assert snapshot.park_due_at is None
    assert snapshot.target_height_cm is None
    assert snapshot.intent_source is None
    assert not [call for call in desk.calls if call[0] == "target"]


async def test_session_end_clears_previous_user_height_policy() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(user(registered=True))
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, modes=FakeModes(mode()))
    await observe(service, camera, (1, 1), clock)
    assert service.get_snapshot().height_policy is HeightPolicy.PROFILE_ACTIVITY_MODE

    users.current = None
    await observe(service, camera, (2, 2), clock)

    snapshot = service.get_snapshot()
    assert snapshot.session_id is None
    assert snapshot.state is AutomationState.WAITING_USER
    assert snapshot.height_policy is None


async def test_park_needs_fresh_baseline_and_safe_desk() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(None)
    camera = FakeVision(vision((1, 1), vacant=True))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    await service.start()
    await observe(service, camera, (1, 1), clock, vacant=True)
    await observe(service, camera, (2, 2), clock, vacant=True)
    await observe(service, camera, (3, 3), clock, 30, vacant=True)
    assert service.get_snapshot().target_height_cm == 75
    assert not [call for call in desk.calls if call[0] == "target"]
    await service.stop()


@pytest.mark.parametrize("status, provenance", [
    (HeightStatus.STALE, HeightProvenance.LIVE),
    (HeightStatus.SENSOR_SLEEPING, HeightProvenance.CACHED),
])
async def test_park_delegates_non_live_height_to_desk_controller(
    status: HeightStatus, provenance: HeightProvenance,
) -> None:
    """PARK must reach the controller so its common WAKE path can run."""

    clock, desk, users = Clock(), FakeDesk(height=110.0), FakeUsers(None)
    camera = FakeVision(vision((1, 1), vacant=True))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    desk.snapshot = DeskSnapshot(
        DeskState.IDLE,
        HeightSnapshot(110.0, NOW, status, provenance),
        desk.snapshot.relay,
        None, None, "", None, NOW,
    )

    await service.start()
    await observe(service, camera, (1, 1), clock, vacant=True)
    await observe(service, camera, (2, 2), clock, vacant=True)
    await observe(service, camera, (3, 3), clock, 30, vacant=True)
    await flush_background_tasks()

    assert ("target", 75.0) in desk.calls
    assert service.get_snapshot().state is AutomationState.PARKING
    await service.stop()


async def test_park_without_any_height_basis_waits_without_false_block() -> None:
    """A cold start cannot select a bounded WAKE direction yet."""

    clock, desk, users = Clock(), FakeDesk(), FakeUsers(None)
    camera = FakeVision(vision((1, 1), vacant=True))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    desk.snapshot = DeskSnapshot(
        DeskState.IDLE,
        HeightSnapshot(None, None, HeightStatus.WAITING, HeightProvenance.LIVE),
        desk.snapshot.relay,
        None, None, "", None, NOW,
    )

    await service.start()
    await observe(service, camera, (1, 1), clock, vacant=True)
    await observe(service, camera, (2, 2), clock, vacant=True)
    await observe(service, camera, (3, 3), clock, 30, vacant=True)

    snapshot = service.get_snapshot()
    assert snapshot.blocked_reason_codes == ()
    assert snapshot.state is AutomationState.WAITING_USER
    assert not [call for call in desk.calls if call[0] == "target"]
    await service.stop()


@pytest.mark.parametrize("relay_event", [None, RelayEvent.OFFLINE])
async def test_sessionless_park_readiness_wait_does_not_block_automation(
    relay_event: RelayEvent | None,
) -> None:
    """Startup can observe VACANT before MQTT relay readiness without a false BLOCKED state."""

    clock, desk, users = Clock(), FakeDesk(), FakeUsers(None)
    camera = FakeVision(vision((1, 1), vacant=True))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    desk.snapshot = DeskSnapshot(
        DeskState.IDLE, desk.snapshot.height,
        RelaySnapshot(relay_event, RelayState.STOP, "1", None, None, NOW, None),
        None, None, "", None, NOW,
    )

    await observe(service, camera, (1, 1), clock, vacant=True)

    snapshot = service.get_snapshot()
    assert snapshot.state is AutomationState.WAITING_USER
    assert snapshot.blocked_reason_codes == ()
    assert snapshot.park_due_at is None


async def test_park_cancels_for_manual_or_relay_error_but_not_its_own_motion() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(None)
    camera = FakeVision(vision((1, 1), vacant=True))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    await service.start()
    await observe(service, camera, (2, 2), clock, vacant=True)
    await observe(service, camera, (3, 3), clock, 30, vacant=True)
    desk.snapshot = DeskSnapshot(DeskState.MOVING, desk.snapshot.height, desk.snapshot.relay,
                                 75, Direction.UP, "", None, NOW)
    await observe(service, camera, (4, 4), clock, vacant=True)
    assert service.get_snapshot().state is AutomationState.PARKING
    desk.snapshot = DeskSnapshot(DeskState.MANUAL, desk.snapshot.height, desk.snapshot.relay,
                                 None, None, "", None, NOW)
    await observe(service, camera, (5, 5), clock, vacant=True)
    assert service.get_snapshot().state is AutomationState.WAITING_USER
    await service.stop()


async def test_completed_live_park_releases_live_intent_before_presence_returns() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(None)
    camera = FakeVision(vision((1, 1), vacant=True))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock, execute=True)
    await service.start()
    await observe(service, camera, (2, 2), clock, vacant=True)
    await observe(service, camera, (3, 3), clock, 30, vacant=True)
    await asyncio.sleep(0)
    desk.snapshot = FakeDesk._snapshot(75)
    await observe(service, camera, (4, 4), clock, vacant=True)
    assert service.get_snapshot().state is AutomationState.READY
    await observe(service, camera, (5, 5), clock)
    assert [call for call in desk.calls if call[0] == "stop"] == []
    await service.stop()


async def test_start_stop_is_idempotent_and_subscribe_failure_rolls_back() -> None:
    clock, desk, users = Clock(), FakeDesk(), FakeUsers(None)
    camera = FakeVision(vision((1, 1)))
    service = service_for(users=users, camera=camera, desk=desk, clock=clock)
    await asyncio.gather(service.start(), service.start())
    assert users.subscriptions == 1
    await service.stop()
    await service.stop()
    assert users.callback is None
