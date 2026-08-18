"""The single owner of automatic desk intent, session changes, and commands."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Protocol

from smart_desk.config.settings import AutomationSettings
from smart_desk.modules.automation.models import (
    AutomationSnapshot, AutomationState, ControlMode, HeightPolicy, IntentSource,
)
from smart_desk.modules.identity.models import CurrentUserSnapshot, SessionKind
from smart_desk.modules.desk.models import (
    DeskSnapshot, DeskState, Direction, HeightStatus, RelayEvent, RelayState,
)
from smart_desk.modules.profiles.activity_modes import (
    ActivityModeNotFoundError, ActivityModeOwnershipError, effective_mode_from_activity,
)
from smart_desk.modules.profiles.led_schedule import LedSchedule, parse_schedule
from smart_desk.modules.profiles.models import ActivityMode, EffectiveActivityMode
from smart_desk.modules.vision.models import PresenceStatus, PostureStatus, VisionSnapshot


class AutomationConflictError(RuntimeError):
    """A user-bound command referred to a non-current session or profile."""


class AutomationNotFoundError(RuntimeError):
    """An activity mode does not exist."""


class CurrentUserPort(Protocol):
    async def snapshot(self) -> CurrentUserSnapshot | None: ...
    async def is_current(self, session_id: str) -> bool: ...
    async def subscribe(self, callback: Callable[[object], object]) -> Callable[[], Awaitable[None]]: ...


class VisionPort(Protocol):
    def get_snapshot(self) -> VisionSnapshot: ...


class ActivityModePort(Protocol):
    async def list_effective_modes(self, profile_id: str) -> list[EffectiveActivityMode]: ...
    async def get_mode_for_profile(self, profile_id: str, mode_id: str) -> ActivityMode: ...
    async def delete_mode(self, mode_id: str) -> None: ...


class DeskPort(Protocol):
    async def set_target(self, height_cm: float) -> None: ...
    async def stop_motion(self, reason: str = "") -> None: ...
    async def hold_up(self) -> None: ...
    async def hold_down(self) -> None: ...
    def get_snapshot(self) -> DeskSnapshot: ...


class WledPort(Protocol):
    async def set_solid(self, color: str) -> None: ...
    async def set_brightness(self, brightness: int) -> None: ...
    async def turn_off(self) -> None: ...


# 작업 모드가 조명에 지시하는 한 벌. 밝기가 None이면 지금 밝기를 그대로 둔다.
LedSetting = tuple[str | None, int | None]


class ActivityModeUsagePort(Protocol):
    async def start_interval(self, profile_id: str, mode_key: str, mode_name: str) -> None: ...
    async def close_open_intervals(self, profile_id: str | None = None) -> None: ...


class AnnouncerPort(Protocol):
    """스피커로 짧은 알림을 말해 주는 쪽. 부르는 자리를 붙잡지 않는다."""

    def say_soon(self, text: str) -> None: ...


class GreetingPort(Protocol):
    """알아본 사용자에게 먼저 말을 거는 쪽. 부르는 자리를 붙잡지 않는다."""

    def greet(self, profile_id: str | None) -> None: ...


class AutomationService:
    """Serializes commands separately from short snapshot mutations.

    The state lock protects only replacement of ``AutomationSnapshot`` and task
    ownership.  All repository, session, Desk, and WLED waits happen outside it.
    """

    def __init__(
        self, *, current_user: CurrentUserPort, vision: VisionPort,
        activity_modes: ActivityModePort, desk: DeskPort, settings: AutomationSettings,
        wled: WledPort | None = None, target_tolerance_cm: float = 0.5,
        usage: ActivityModeUsagePort | None = None,
        mode_memory_seconds: float = 1800.0,
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        now = utc_now()
        self._users = current_user
        self._vision = vision
        self._modes = activity_modes
        self._desk = desk
        self._settings = settings
        self._wled = wled
        self._target_tolerance_cm = target_tolerance_cm
        self._utc_now = utc_now
        self._monotonic = monotonic
        self._snapshot = AutomationSnapshot(
            None, None, None, AutomationState.WAITING_USER, None, None, None,
            None, None, (), None, None, 0, 0, "STARTUP", "SYSTEM", now, now,
        )
        self._state_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None
        self._dispatch_task: asyncio.Task[None] | None = None
        self._wled_task: asyncio.Task[None] | None = None
        self._wled_sequence = 0
        self._wled_io_lock = asyncio.Lock()
        self._unsubscribe: Callable[[], Awaitable[None]] | None = None
        self._running = False
        self._candidate_started_mono: float | None = None
        self._candidate_pair: tuple[float, float] | None = None
        self._last_pair: tuple[float, float] | None = None
        self._usage = usage
        # 음성은 automation보다 늦게 조립된다. 준비되면 set_greeter로 끼운다.
        self._greeter: GreetingPort | None = None
        self._announcer: AnnouncerPort | None = None
        # 같은 높이를 두 번 말하지 않도록 마지막으로 알린 목표를 기억한다.
        self._announced_target_cm: float | None = None
        # 지금 걸린 모드의 조명 스케줄과, 그 모드를 켠 시각. 스케줄은 모드가
        # 바뀔 때만 해석하고, 그 뒤로는 구간이 넘어갈 때만 조명을 다시 보낸다.
        self._active_schedule: LedSchedule | None = None
        self._mode_started_mono: float | None = None
        self._schedule_applied: tuple[str, int] | None = None
        # 자리를 비워도 잠깐이면 쓰던 모드로 돌아오게 profile별로 기억한다.
        # 기억하는 동안 사용 시간은 늘지 않는다(구간을 닫아 두기 때문이다).
        self._mode_memory_seconds = mode_memory_seconds
        self._remembered_mode: dict[str, tuple[str, float]] = {}
        self._park_started_mono: float | None = None
        self._park_pair: tuple[float, float] | None = None
        self._startup_pair: tuple[float, float] | None = None
        self._live_automatic = False
        self._auto_completed_target_cm: float | None = None
        self._auto_rearm_started_mono: float | None = None
        self._vision_recovery_baseline_required = False
        # Session kind is transition state, not a derived profile property: an
        # anonymous-to-registered confirmation has a deliberately different
        # AUTO contract from every other session replacement.
        self._session_kind: SessionKind | None = None
        self._session_profile_id: str | None = None

    def get_snapshot(self) -> AutomationSnapshot:
        return self._snapshot

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._running:
                return
            try:
                unsubscribe = await self._users.subscribe(self._on_session_change)
            except Exception:
                self._running = False
                raise
            self._unsubscribe = unsubscribe
            self._running = True
            self._startup_pair = self._pair(self._vision.get_snapshot())
            self._loop_task = asyncio.create_task(self._run_loop(), name="desk-automation")
            self._wake.set()

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            if not self._running and self._unsubscribe is None:
                return
            self._running = False
            unsubscribe, self._unsubscribe = self._unsubscribe, None
            if unsubscribe is not None:
                await unsubscribe()
            async with self._state_lock:
                live = self._invalidate_locked("LIFECYCLE_STOP")
                loop_task, self._loop_task = self._loop_task, None
                led_task, self._wled_task = self._wled_task, None
                self._set_waiting_locked("LIFECYCLE_STOP")
            for task in (loop_task, led_task):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(task for task in (loop_task, led_task) if task is not None),
                                 return_exceptions=True)
            if live:
                await self._safe_stop("자동화 종료 안전 정지")

    def set_announcer(self, announcer: AnnouncerPort | None) -> None:
        """높이 변경을 말해 줄 쪽을 끼운다. 음성이 꺼져 있으면 None으로 둔다."""

        self._announcer = announcer

    def set_greeter(self, greeter: GreetingPort | None) -> None:
        """인사를 건넬 쪽을 끼운다. 음성이 꺼져 있으면 None으로 둔다."""

        self._greeter = greeter

    def _on_session_change(self, _event: object) -> None:
        self._wake.set()

    async def hold(self, direction: Direction, expected_session_id: str | None = None) -> None:
        async with self._command_lock:
            live = await self._make_manual("HOLD", expected_session_id)
            self._raise_if_stop_failed()
            await self._preempt_for_manual_command(
                expected_session_id, live, "수동 HOLD가 자동 이동을 선점했습니다."
            )
            # A Voice turn can become stale while a preemption STOP is in
            # flight.  Never let its HOLD become the new user's command.
            await self._validate_expected_session(expected_session_id)
            if direction is Direction.UP:
                await self._desk.hold_up()
            else:
                await self._desk.hold_down()

    async def set_target(self, target_cm: float, expected_session_id: str | None = None) -> None:
        async with self._command_lock:
            live = await self._make_manual("SET_TARGET", expected_session_id)
            self._raise_if_stop_failed()
            await self._preempt_for_manual_command(
                expected_session_id, live, "직접 목표가 자동 이동을 선점했습니다."
            )
            # Keep the sessionless Dashboard command path unchanged, while a
            # user-bound caller is checked at the final physical boundary.
            await self._validate_expected_session(expected_session_id)
            self._announce_height(target_cm, automatic=False)
            await self._desk.set_target(target_cm)

    async def stop_motion(self, reason: str = "사용자 STOP") -> None:
        """User STOP never waits for a background target and exposes Desk errors."""
        async with self._command_lock:
            async with self._state_lock:
                self._invalidate_locked("USER_STOP")
                if self._snapshot.session_id is not None:
                    self._replace_locked(control_mode=ControlMode.MANUAL,
                                         state=AutomationState.MANUAL,
                                         intent_source=IntentSource.MANUAL,
                                         blocked_reason_codes=())
                else:
                    self._set_waiting_locked("USER_STOP")
            try:
                await self._desk.stop_motion(reason)
            except Exception:
                await self._mark_stop_failed()
                raise
            async with self._state_lock:
                if self._snapshot.session_id is not None:
                    self._replace_locked(
                        state=AutomationState.MANUAL,
                        blocked_reason_codes=self._without_stop_failure(),
                    )
                else:
                    # A successful explicit STOP is the recovery action for a
                    # session-less safety latch too.  Unlike a session-bound
                    # STOP it must not manufacture a MANUAL selection.
                    self._replace_locked(
                        state=AutomationState.WAITING_USER,
                        height_policy=None,
                        blocked_reason_codes=self._without_stop_failure(),
                    )

    async def set_control_mode(self, mode: ControlMode, expected_session_id: str) -> None:
        async with self._command_lock:
            current = await self._users.snapshot()
            if current is None or current.session_id != expected_session_id:
                raise AutomationConflictError("SESSION_MISMATCH")
            async with self._state_lock:
                if self._snapshot.session_id != expected_session_id:
                    raise AutomationConflictError("SESSION_MISMATCH")
                live = self._invalidate_locked("CONTROL_MODE")
                blocked = self._with_stop_failure(())
                state = (AutomationState.BLOCKED if "DESK_STOP_FAILED" in blocked else
                         AutomationState.MANUAL if mode is ControlMode.MANUAL else AutomationState.OBSERVING)
                self._replace_locked(control_mode=mode, state=state,
                                     intent_source=None, target_height_cm=None,
                                     posture_candidate=None, candidate_since=None,
                                     initial_move_due_at=None, park_due_at=None,
                                     blocked_reason_codes=blocked)
                if mode is ControlMode.AUTO:
                    self._last_pair = self._pair(self._vision.get_snapshot())
            # Re-AUTO must STOP even shadow intent: it is a command contract.
            # Deliberately propagate a user-visible STOP failure while preserving
            # the selected AUTO intent for the next observation.
            if live or mode is ControlMode.AUTO:
                await self._stop_or_block("제어 방식 전환 전 정지")

    async def set_activity_mode(self, key: str, expected_session_id: str) -> None:
        async with self._command_lock:
            current = await self._users.snapshot()
            if (current is None or current.session_id != expected_session_id
                    or current.kind is not SessionKind.REGISTERED or current.profile_id is None):
                raise AutomationConflictError("SESSION_MISMATCH")
            selected = await self._read_mode(current.profile_id, key)
            if not await self._users.is_current(expected_session_id):
                raise AutomationConflictError("SESSION_MISMATCH")
            async with self._state_lock:
                if self._snapshot.session_id != expected_session_id:
                    raise AutomationConflictError("SESSION_MISMATCH")
                control = self._snapshot.control_mode or ControlMode.AUTO
                live = self._invalidate_locked("ACTIVITY_MODE") if control is ControlMode.AUTO else False
                state = (AutomationState.BLOCKED if "DESK_STOP_FAILED" in self._snapshot.blocked_reason_codes
                         else AutomationState.OBSERVING if control is ControlMode.AUTO
                         else AutomationState.MANUAL)
                self._replace_locked(activity_mode=selected,
                                     state=state,
                                     intent_source=None, target_height_cm=None,
                                     posture_candidate=None, candidate_since=None,
                                     initial_move_due_at=None,
                                     blocked_reason_codes=self._with_stop_failure(()))
                if control is ControlMode.AUTO:
                    self._last_pair = self._pair(self._vision.get_snapshot())
            # Mode selection and its LED are committed independently of the
            # Desk preemption outcome; a failed STOP must not roll either back.
            self._queue_led(*self._install_mode_lighting(selected))
            self._remember_mode(current.profile_id, selected.key)
            await self._begin_usage(current.profile_id, selected)
            if live:
                await self._stop_or_block("작업 모드 변경 전 정지")

    async def delete_activity_mode(self, mode_id: str) -> None:
        """Serialize delete with mode selection to make the active guard atomic."""
        async with self._command_lock:
            snapshot = self._snapshot
            if snapshot.activity_mode and snapshot.activity_mode.editable and snapshot.activity_mode.key == mode_id:
                raise AutomationConflictError("ACTIVE_ACTIVITY_MODE")
            await self._modes.delete_mode(mode_id)

    async def is_active_custom_mode(self, mode_id: str) -> bool:
        snapshot = self._snapshot
        return bool(snapshot.activity_mode and snapshot.activity_mode.editable and snapshot.activity_mode.key == mode_id)

    async def _read_mode(self, profile_id: str, key: str) -> EffectiveActivityMode:
        if key == "default":
            modes = await self._modes.list_effective_modes(profile_id)
            for mode in modes:
                if mode.key == "default":
                    return mode
            raise AutomationNotFoundError("기본 작업 모드를 찾을 수 없습니다.")
        try:
            mode = await self._modes.get_mode_for_profile(profile_id, key)
        except ActivityModeNotFoundError as error:
            raise AutomationNotFoundError("작업 모드를 찾을 수 없습니다.") from error
        except ActivityModeOwnershipError as error:
            raise AutomationConflictError("ACTIVITY_MODE_OWNERSHIP") from error
        return effective_mode_from_activity(mode)

    async def _make_manual(self, reason: str, expected_session_id: str | None = None) -> bool:
        # This validation deliberately does not hold the current-user lock
        # across Desk I/O.  Every later physical boundary revalidates instead,
        # so session replacement remains quick and stale commands cannot act.
        await self._validate_expected_session(expected_session_id)
        async with self._state_lock:
            # The first check released this lock.  A session replacement can
            # install B in that gap, so A must not invalidate or mark B's
            # snapshot MANUAL before the later Desk-bound checks reject it.
            if (expected_session_id is not None
                    and self._snapshot.session_id != expected_session_id):
                raise AutomationConflictError("SESSION_MISMATCH")
            live = self._invalidate_locked(reason)
            if self._snapshot.session_id is not None:
                state = (AutomationState.BLOCKED if "DESK_STOP_FAILED" in self._snapshot.blocked_reason_codes
                         else AutomationState.MANUAL)
                self._replace_locked(control_mode=ControlMode.MANUAL, state=state,
                                     intent_source=IntentSource.MANUAL, target_height_cm=None,
                                     blocked_reason_codes=self._with_stop_failure(()))
            else:
                self._set_waiting_locked(reason)
            return live

    async def _validate_expected_session(self, expected_session_id: str | None) -> None:
        """Require both current-user and automation ownership when supplied.

        ``None`` is intentionally the Dashboard/HTTP compatibility path: it
        is identity-independent and remains usable with no active session.
        """
        if expected_session_id is None:
            return
        current = await self._users.snapshot()
        if current is None or current.session_id != expected_session_id:
            raise AutomationConflictError("SESSION_MISMATCH")
        async with self._state_lock:
            if self._snapshot.session_id != expected_session_id:
                raise AutomationConflictError("SESSION_MISMATCH")

    async def _preempt_for_manual_command(
        self, expected_session_id: str | None, live: bool, reason: str,
    ) -> None:
        """Preserve a needed safety STOP but reject stale manual side effects."""
        try:
            await self._validate_expected_session(expected_session_id)
        except AutomationConflictError:
            if live:
                # The old AUTO target may still be moving.  STOP is a safety
                # action, so it survives the same race that rejects HOLD/target.
                await self._safe_stop(reason)
            raise
        if live:
            # A user command must not run after its required preemption STOP
            # failed.  _make_manual deliberately happened first, so the caller
            # can safely retry from the preserved MANUAL state.
            await self._stop_or_block(reason)

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._observe_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._mark_blocked("AUTOMATION_OBSERVATION_ERROR")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.1)
            except TimeoutError:
                pass
            self._wake.clear()

    async def _observe_once(self) -> None:
        self._tick_led_schedule()
        current = await self._users.snapshot()
        vision = self._vision.get_snapshot()
        snapshot = self._snapshot
        if current is None:
            if snapshot.session_id is not None:
                await self._end_session(snapshot.session_id)
            await self._observe_park(vision)
            return
        await self._cancel_park_if_needed()
        if snapshot.session_id != current.session_id:
            activity: EffectiveActivityMode | None = None
            failure: str | None = None
            if current.kind is SessionKind.REGISTERED:
                # 잠깐 자리를 비운 것이면 쓰던 모드로 돌아온다. 기억이 만료됐거나
                # 그 모드가 사라졌으면 기본 모드로 되돌린다.
                remembered = self._recall_mode(current.profile_id)
                for key in ([remembered] if remembered else []) + ["default"]:
                    try:
                        activity = await self._read_mode(current.profile_id or "", key)
                        failure = None
                        break
                    except Exception:
                        failure = "DEFAULT_ACTIVITY_MODE_UNAVAILABLE"
            if not await self._users.is_current(current.session_id):
                return
            installed = await self._install_session(
                current, activity, failure, self._vision.get_snapshot()
            )
            if installed:
                # Ordinary replacements establish a baseline for the 2-second
                # initial candidate.  An anonymous upgrade instead schedules
                # its Identity-stabilized posture in _install_session.
                return
        await self._observe_auto(current, vision)

    async def _install_session(self, current: CurrentUserSnapshot,
                               activity: EffectiveActivityMode | None, failure: str | None,
                               vision: VisionSnapshot) -> bool:
        async with self._state_lock:
            if self._snapshot.session_id == current.session_id:
                return False
            anonymous_upgrade = (
                self._session_kind is SessionKind.ANONYMOUS
                and current.kind is SessionKind.REGISTERED
            )
            previous_control = self._snapshot.control_mode
            live = self._invalidate_locked("SESSION_CHANGE")
            control = (
                ControlMode.MANUAL
                if anonymous_upgrade and previous_control is ControlMode.MANUAL
                else ControlMode.AUTO
            )
            unusable_upgrade = anonymous_upgrade and control is ControlMode.AUTO and not self._auto_usable(vision)
            blocked_codes = self._with_stop_failure((failure,) if failure else ())
            if unusable_upgrade:
                blocked_codes = self._with_stop_failure(self._vision_codes(vision))
            self._replace_locked(session_id=current.session_id, control_mode=control,
                                 activity_mode=activity,
                                 state=(AutomationState.BLOCKED if blocked_codes else
                                        AutomationState.MANUAL if control is ControlMode.MANUAL else
                                        AutomationState.OBSERVING),
                                 height_policy=(HeightPolicy.PROFILE_ACTIVITY_MODE if current.kind is SessionKind.REGISTERED
                                                else HeightPolicy.ANONYMOUS_DEFAULT),
                                 posture_candidate=None, candidate_since=None, target_height_cm=None,
                                 intent_source=None, blocked_reason_codes=blocked_codes,
                                 initial_move_due_at=(None if anonymous_upgrade else
                                                      self._utc_now() + timedelta(seconds=2)),
                                 park_due_at=None)
            self._session_kind = current.kind
            self._session_profile_id = current.profile_id
            installed_profile_id, installed_mode = current.profile_id, activity
            greet_profile_id = (
                current.profile_id if current.kind is SessionKind.REGISTERED else None
            )
            self._last_pair = self._pair(vision)
            self._vision_recovery_baseline_required = unusable_upgrade
            expected_generation = self._snapshot.generation
        # 아는 얼굴이면 먼저 인사를 건넨다. 다만 쓰던 모드를 아직 기억하고 있다면
        # 잠깐 자리를 비웠다 돌아온 것이므로 같은 방문으로 보고 말을 걸지 않는다.
        # 모드 기억과 같은 신호를 써서 두 시간이 어긋나지 않게 한다.
        # (_remember_mode는 아래에서 갱신되므로 여기서는 직전 방문이 보인다.)
        if (greet_profile_id is not None and self._greeter is not None
                and self._recall_mode(greet_profile_id) is None):
            self._greeter.greet(greet_profile_id)
        # 새 모드가 걸렸으니 조명 계획을 다시 세운다.
        install_lighting = self._install_mode_lighting(activity)
        # 새 session이 모드를 물고 들어온 시점부터 사용 시간을 다시 센다.
        self._remember_mode(installed_profile_id, installed_mode.key if installed_mode else None)
        await self._begin_usage(installed_profile_id, installed_mode)
        if live:
            if not await self._safe_stop("사용자 교대 안전 정지"):
                await self._queue_install_led(
                    current.session_id, expected_generation, *install_lighting,
                )
                return True
        if not await self._queue_install_led(
            current.session_id, expected_generation, *install_lighting,
        ):
            return True
        if anonymous_upgrade and control is ControlMode.AUTO and failure is None and self._auto_usable(vision):
            await self._schedule_upgrade_target(current, vision, expected_generation)
        return True

    async def _queue_install_led(
        self, expected_session_id: str, expected_generation: int, color: str | None,
        brightness: int | None = None,
    ) -> bool:
        """Queue an installed session's LED only while that install still owns state."""
        if not await self._users.is_current(expected_session_id):
            return False
        async with self._state_lock:
            snapshot = self._snapshot
            if (snapshot.session_id != expected_session_id
                    or snapshot.generation != expected_generation):
                return False
            # This only queues background WLED I/O; it does not await it while
            # holding state ownership.
            self._queue_led_sequence(color, brightness)
            return True

    async def _schedule_upgrade_target(
        self, current: CurrentUserSnapshot, vision: VisionSnapshot, expected_generation: int,
    ) -> None:
        """Apply an anonymous AUTO confirmation without restarting its timers."""
        if not await self._users.is_current(current.session_id):
            return
        posture = vision.stable_posture
        assert posture in (PostureStatus.SITTING, PostureStatus.STANDING)
        async with self._state_lock:
            snapshot = self._snapshot
            if (
                snapshot.session_id != current.session_id
                or snapshot.generation != expected_generation
                or self._session_kind is not SessionKind.REGISTERED
                or snapshot.control_mode is not ControlMode.AUTO
                or "DESK_STOP_FAILED" in snapshot.blocked_reason_codes
            ):
                return
            target = self._target_for(snapshot, posture)
            if target is None:
                self._replace_locked(
                    state=AutomationState.BLOCKED,
                    blocked_reason_codes=("ACTIVITY_MODE_UNAVAILABLE",),
                )
                return
            desk_height = self._desk_height()
            if desk_height is not None and abs(desk_height - target) <= self._target_tolerance_cm:
                self._replace_locked(
                    state=AutomationState.READY,
                    target_height_cm=target,
                    intent_source=IntentSource.AUTO,
                    initial_move_due_at=None,
                )
                return
            self._schedule_locked(target, IntentSource.AUTO, current.session_id)

    async def _end_session(self, expected_session: str) -> None:
        async with self._state_lock:
            if self._snapshot.session_id != expected_session:
                return
            ended_profile = self._session_profile_id
            ended_mode = self._snapshot.activity_mode
            live = self._invalidate_locked("SESSION_ENDED")
            self._set_waiting_locked("SESSION_ENDED")
            self._session_kind = None
            self._session_profile_id = None
        # 쓰던 모드는 기억하되 사용 시간은 여기서 멈춘다.
        if ended_mode is not None:
            self._remember_mode(ended_profile, ended_mode.key)
        await self._end_usage(ended_profile)
        if live:
            await self._safe_stop("사용자 session 종료 안전 정지")
        self._queue_led(None)

    async def _observe_auto(self, current: CurrentUserSnapshot, vision: VisionSnapshot) -> None:
        snapshot = self._snapshot
        if snapshot.session_id != current.session_id or snapshot.control_mode is not ControlMode.AUTO:
            return
        if current.kind is SessionKind.REGISTERED and snapshot.activity_mode is None:
            return
        if "DESK_STOP_FAILED" in snapshot.blocked_reason_codes:
            return
        await self._finish_automatic_if_idle(current.session_id)
        # _finish_automatic_if_idle can fail-close a live intent when the Desk
        # reports ERROR. Do not let this same fresh vision frame overwrite that
        # terminal state with OBSERVING or schedule another target.
        if {
            "DESK_ERROR",
            "DESK_STOPPED_BEFORE_TARGET",
            "DESK_HEIGHT_UNAVAILABLE_AFTER_STOP",
        }.intersection(self._snapshot.blocked_reason_codes):
            return
        if not self._auto_usable(vision):
            await self._block_auto(vision)
            return
        pair = self._pair(vision)
        if pair is None or not self._both_new(pair, self._last_pair):
            return
        # A usable frame after an uncertainty is only a freshness baseline.
        # It cannot contribute to the new posture hold itself.
        if self._vision_recovery_baseline_required:
            async with self._state_lock:
                if (self._snapshot.session_id == current.session_id
                        and self._snapshot.control_mode is ControlMode.AUTO
                        and self._vision_recovery_baseline_required):
                    self._last_pair = pair
                    self._vision_recovery_baseline_required = False
                    self._replace_locked(
                        state=AutomationState.OBSERVING,
                        blocked_reason_codes=self._with_stop_failure(()),
                    )
            return
        self._last_pair = pair
        posture = vision.stable_posture
        assert posture in (PostureStatus.SITTING, PostureStatus.STANDING)
        now_mono = self._monotonic()
        desk_height = self._desk_height()
        async with self._state_lock:
            if self._snapshot.session_id != current.session_id or self._snapshot.control_mode is not ControlMode.AUTO:
                return
            if self._candidate_pair is None or self._snapshot.posture_candidate is not posture:
                self._candidate_pair = pair
                self._candidate_started_mono = now_mono
                self._replace_locked(state=AutomationState.OBSERVING, posture_candidate=posture,
                                     candidate_since=self._utc_now(),
                                     initial_move_due_at=self._utc_now() + timedelta(
                                         seconds=self._settings.posture_confirmation_seconds
                                     ),
                                     blocked_reason_codes=self._with_stop_failure(()))
                return
            assert self._candidate_started_mono is not None
            if now_mono - self._candidate_started_mono < self._settings.posture_confirmation_seconds:
                return
            target = self._target_for(self._snapshot, posture)
            if target is None:
                self._replace_locked(state=AutomationState.BLOCKED, blocked_reason_codes=("ACTIVITY_MODE_UNAVAILABLE",))
                return
            if desk_height is not None and abs(desk_height - target) <= self._target_tolerance_cm:
                self._mark_auto_target_complete_locked(target)
                self._replace_locked(state=AutomationState.READY, target_height_cm=target,
                                     intent_source=IntentSource.AUTO, initial_move_due_at=None,
                                     blocked_reason_codes=self._with_stop_failure(()))
                return
            if self._auto_rearm_pending_locked(target, desk_height, now_mono):
                return
            if (
                self._live_automatic
                and self._snapshot.intent_source is IntentSource.AUTO
                and self._snapshot.target_height_cm == target
            ):
                return
            self._schedule_locked(target, IntentSource.AUTO, current.session_id)

    def _auto_usable(self, vision: VisionSnapshot) -> bool:
        return (vision.usable and vision.stable_presence is PresenceStatus.PRESENT_SINGLE
                and vision.stable_posture in (PostureStatus.SITTING, PostureStatus.STANDING)
                and self._pair(vision) is not None)

    async def _block_auto(self, vision: VisionSnapshot) -> None:
        codes = self._vision_codes(vision)
        # 자동 dispatch와 같은 command lock을 사용한다. vision이 불확실해진 뒤에는
        # 이미 생성된 dispatch가 STOP 뒤에 set_target()을 실행할 수 없어야 한다.
        async with self._command_lock:
            async with self._state_lock:
                snapshot = self._snapshot
                merged_codes = self._with_stop_failure(codes)
                if (snapshot.control_mode is not ControlMode.AUTO
                        or snapshot.state is AutomationState.BLOCKED
                        and snapshot.blocked_reason_codes == merged_codes):
                    return
                live = self._invalidate_locked("VISION_BLOCKED")
                self._reset_candidate_locked()
                self._vision_recovery_baseline_required = True
                self._replace_locked(
                    state=AutomationState.BLOCKED,
                    blocked_reason_codes=merged_codes,
                )
            if live:
                await self._safe_stop("Vision 불확실성 안전 정지")

    async def _finish_automatic_if_idle(self, session_id: str | None) -> None:
        """Finish AUTO/PARK only after a fresh measurement confirms its target."""
        try:
            desk = self._desk.get_snapshot()
        except Exception:
            return
        if desk.state is DeskState.ERROR:
            async with self._state_lock:
                if not self._live_automatic:
                    return
                self._invalidate_locked("DESK_ERROR")
                self._replace_locked(
                    state=AutomationState.BLOCKED,
                    blocked_reason_codes=self._with_stop_failure(("DESK_ERROR",)),
                )
            return
        if desk.state not in {DeskState.IDLE, DeskState.STOPPED}:
            return
        async with self._state_lock:
            snapshot = self._snapshot
            if (snapshot.session_id != session_id or not self._live_automatic
                    or snapshot.intent_source not in {IntentSource.AUTO, IntentSource.PARK}):
                return
            target = snapshot.target_height_cm
            height = desk.height
            target_reached = (
                target is not None
                and height.status is HeightStatus.ONLINE
                and height.height_cm is not None
                and abs(height.height_cm - target) <= self._target_tolerance_cm
            )
            if not target_reached:
                # STOPPED is also used for fail-safe stops and bounded fine
                # correction exhaustion. It is not evidence of completion.
                self._live_automatic = False
                self._auto_completed_target_cm = None
                self._auto_rearm_started_mono = None
                code = (
                    "DESK_STOPPED_BEFORE_TARGET"
                    if height.status is HeightStatus.ONLINE and height.height_cm is not None
                    else "DESK_HEIGHT_UNAVAILABLE_AFTER_STOP"
                )
                self._replace_locked(
                    state=AutomationState.BLOCKED,
                    blocked_reason_codes=self._with_stop_failure((code,)),
                )
                return
            self._live_automatic = False
            if snapshot.intent_source is IntentSource.AUTO and snapshot.target_height_cm is not None:
                self._mark_auto_target_complete_locked(snapshot.target_height_cm)
            self._replace_locked(
                state=AutomationState.READY,
                blocked_reason_codes=self._with_stop_failure(()),
            )

    async def _observe_park(self, vision: VisionSnapshot) -> None:
        # A failed explicit or safety STOP is a global fail-closed latch.  It
        # must win before PARK completion reconciliation can publish READY or
        # a fresh VACANT frame can begin another PARK timer.
        async with self._state_lock:
            snapshot = self._snapshot
            if "DESK_STOP_FAILED" in snapshot.blocked_reason_codes:
                self._park_started_mono = None
                self._park_pair = None
                if snapshot.intent_source is IntentSource.PARK:
                    self._invalidate_locked("DESK_STOP_FAILED")
                if (self._snapshot.state is not AutomationState.BLOCKED
                        or self._snapshot.height_policy is not None
                        or self._snapshot.target_height_cm is not None
                        or self._snapshot.intent_source is not None
                        or self._snapshot.park_due_at is not None):
                    self._replace_locked(
                        state=AutomationState.BLOCKED,
                        height_policy=None,
                        target_height_cm=None,
                        intent_source=None,
                        park_due_at=None,
                        blocked_reason_codes=self._stop_failure_codes(),
                    )
                return
        # PARK has no session, but completion still needs the same live intent
        # reconciliation as AUTO.  Otherwise a completed park remains marked
        # live and later presence incorrectly emits an unnecessary STOP.
        await self._finish_automatic_if_idle(None)
        pair = self._pair(vision)
        safe, desk_code = self._park_desk_safe()
        if not self._park_eligible(vision, pair) or not safe or self._manual_desk_intent():
            await self._cancel_park_if_needed()
            # PARK is a session-less convenience action.  At startup its
            # first vacant frame can precede relay readiness, which
            # only means that PARK cannot begin yet—not that a user command
            # or an in-progress desk movement must be safety-blocked.
            if desk_code not in {"PARK_HEIGHT_UNAVAILABLE", "PARK_RELAY_UNAVAILABLE"} and desk_code is not None:
                await self._mark_blocked(desk_code)
            return
        assert pair is not None
        if not self._both_new(pair, self._startup_pair) or not self._both_new(pair, self._park_pair):
            return
        self._park_pair = pair
        now_mono = self._monotonic()
        async with self._state_lock:
            if self._snapshot.session_id is not None:
                return
            if self._park_started_mono is None:
                self._park_started_mono = now_mono
                self._replace_locked(state=AutomationState.PARK_WAITING, height_policy=HeightPolicy.PARK,
                                     park_due_at=self._utc_now() + timedelta(seconds=30),
                                     blocked_reason_codes=self._with_stop_failure(()))
                return
            if now_mono - self._park_started_mono < 30:
                return
            if self._snapshot.intent_source is IntentSource.PARK:
                return
            self._schedule_locked(75.0, IntentSource.PARK, None)

    def _park_eligible(self, vision: VisionSnapshot, pair: tuple[float, float] | None) -> bool:
        allowed = {"PRESENCE_NOT_SINGLE"}
        return (pair is not None and vision.raw_presence is PresenceStatus.VACANT
                and vision.stable_presence is PresenceStatus.VACANT
                and vision.upper.count == 0 and vision.lower.count == 0
                and {str(code) for code in vision.reason_codes}.issubset(allowed))

    def _park_desk_safe(self) -> tuple[bool, str | None]:
        """Keep PARK policy checks out of DeskController's motion admission.

        Height freshness and cached-height WAKE belong to
        ``DeskController.set_target()``.  Repeating those checks here used to
        reject a sleeping display before the controller could issue WAKE.
        PARK owns policy-level cancellation and live relay-failure detection,
        but must not pre-empt the common target admission path.
        """
        try:
            desk = self._desk.get_snapshot()
        except Exception:
            return False, "DESK_UNAVAILABLE"
        if (desk.state is DeskState.MOVING and self._live_automatic
                and self._snapshot.intent_source is IntentSource.PARK):
            return True, None
        if desk.state is DeskState.ERROR:
            return False, "DESK_ERROR"
        # A WAKE needs a last measurement to choose a bounded direction.  This
        # is not a freshness check: STALE and SENSOR_SLEEPING values continue
        # to DeskController, while a true cold start simply waits for a basis.
        if desk.height.height_cm is None or desk.height.observed_at is None:
            return False, "PARK_HEIGHT_UNAVAILABLE"
        relay = desk.relay
        if (relay.last_error is not None or relay.event in {None, RelayEvent.OFFLINE, RelayEvent.REJECTED}
                or relay.state is not RelayState.STOP):
            return False, "PARK_RELAY_UNAVAILABLE"
        if relay.event not in {RelayEvent.ONLINE, RelayEvent.HEARTBEAT, RelayEvent.STOPPED}:
            return False, "PARK_RELAY_UNAVAILABLE"
        return True, None

    async def _cancel_park_if_needed(self) -> None:
        async with self._state_lock:
            self._park_started_mono = None
            self._park_pair = None
            if self._snapshot.intent_source is not IntentSource.PARK:
                return
            live = self._invalidate_locked("PARK_CANCELLED")
            self._set_waiting_locked("PARK_CANCELLED")
        if live:
            await self._safe_stop("PARK 취소 안전 정지")

    def _manual_desk_intent(self) -> bool:
        try:
            state = self._desk.get_snapshot().state
            if (state is DeskState.MOVING and self._live_automatic
                    and self._snapshot.intent_source is IntentSource.PARK):
                return False
            return state in {DeskState.MANUAL, DeskState.MOVING, DeskState.WAKING}
        except Exception:
            return True

    def _schedule_locked(self, target: float, source: IntentSource, session_id: str | None) -> None:
        if "DESK_STOP_FAILED" in self._snapshot.blocked_reason_codes:
            self._replace_locked(state=AutomationState.BLOCKED)
            return
        generation = self._snapshot.generation
        state = AutomationState.MOVING if source is IntentSource.AUTO else AutomationState.PARKING
        if not self._settings.execute_automatic_movements:
            self._replace_locked(target_height_cm=target, intent_source=source, state=AutomationState.BLOCKED,
                                 height_policy=HeightPolicy.PARK if source is IntentSource.PARK else self._snapshot.height_policy,
                                 initial_move_due_at=None,
                                 blocked_reason_codes=("AUTOMATIC_EXECUTION_DISABLED",))
            return
        self._replace_locked(target_height_cm=target, intent_source=source, state=state,
                             height_policy=HeightPolicy.PARK if source is IntentSource.PARK else self._snapshot.height_policy,
                             initial_move_due_at=None,
                             blocked_reason_codes=self._with_stop_failure(()))
        if source is IntentSource.AUTO:
            self._auto_completed_target_cm = None
            self._auto_rearm_started_mono = None
        self._live_automatic = True
        self._dispatch_task = asyncio.create_task(
            self._dispatch_target(target, source, generation, session_id), name="desk-automation-dispatch"
        )

    async def _dispatch_target(self, target: float, source: IntentSource,
                               generation: int, session_id: str | None) -> None:
        # vision/session invalidation과 desk side effect의 선형화 지점이다.
        async with self._command_lock:
            if not await self._dispatch_valid(generation, session_id, source):
                return
            try:
                self._announce_height(target, automatic=source is IntentSource.AUTO)
                await self._desk.set_target(target)
                await self._finish_automatic_if_idle(session_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                async with self._state_lock:
                    if generation == self._snapshot.generation:
                        self._live_automatic = False
                        self._replace_locked(state=AutomationState.BLOCKED, blocked_reason_codes=("DESK_COMMAND_REJECTED",))

    async def _dispatch_valid(self, generation: int, session_id: str | None, source: IntentSource) -> bool:
        async with self._state_lock:
            snapshot = self._snapshot
            if (generation != snapshot.generation or snapshot.intent_source is not source
                    or snapshot.session_id != session_id):
                return False
        if session_id is not None and not await self._users.is_current(session_id):
            return False
        # The current-user wait stays outside the state lock.  Recheck the
        # generation/intent immediately before Desk I/O so an obsolete task
        # cannot become authoritative after a command or session callback.
        async with self._state_lock:
            snapshot = self._snapshot
            return (generation == snapshot.generation and snapshot.intent_source is source
                    and snapshot.session_id == session_id)

    def _target_for(self, snapshot: AutomationSnapshot, posture: PostureStatus) -> float | None:
        if snapshot.activity_mode is not None:
            return (snapshot.activity_mode.sitting_height_cm if posture is PostureStatus.SITTING
                    else snapshot.activity_mode.standing_height_cm)
        return 75.0 if posture is PostureStatus.SITTING else 110.0

    def _desk_height(self) -> float | None:
        try:
            return self._desk.get_snapshot().height.height_cm
        except Exception:
            return None

    def _pair(self, vision: VisionSnapshot) -> tuple[float, float] | None:
        upper, lower = vision.upper.captured_monotonic, vision.lower.captured_monotonic
        if upper is None or lower is None:
            return None
        return upper, lower

    @staticmethod
    def _both_new(pair: tuple[float, float], previous: tuple[float, float] | None) -> bool:
        return previous is None or (pair[0] > previous[0] and pair[1] > previous[1])

    async def _mark_blocked(self, code: str) -> None:
        async with self._state_lock:
            if self._snapshot.state is AutomationState.BLOCKED and self._snapshot.blocked_reason_codes == (code,):
                return
            live = self._invalidate_locked(code)
            self._replace_locked(
                state=AutomationState.BLOCKED,
                blocked_reason_codes=self._with_stop_failure((code,)),
            )
        if live:
            await self._safe_stop("자동화 관측 오류 안전 정지")

    def _invalidate_locked(self, reason: str) -> bool:
        live = self._live_automatic
        self._live_automatic = False
        self._auto_completed_target_cm = None
        self._auto_rearm_started_mono = None
        self._vision_recovery_baseline_required = False
        self._snapshot = replace(self._snapshot, generation=self._snapshot.generation + 1,
                                 revision=self._snapshot.revision + 1,
                                 last_transition_reason=reason,
                                 last_transition_source="AUTOMATION", last_transition_at=self._utc_now(),
                                 updated_at=self._utc_now())
        task, self._dispatch_task = self._dispatch_task, None
        if task is not None and not task.done():
            task.cancel()
        self._reset_candidate_locked()
        return live

    def _mark_auto_target_complete_locked(self, target: float) -> None:
        self._auto_completed_target_cm = target
        self._auto_rearm_started_mono = None

    def _auto_rearm_pending_locked(
        self,
        target: float,
        desk_height: float | None,
        now_mono: float,
    ) -> bool:
        """Require a sustained, meaningful drift before AUTO repeats a target."""

        if self._auto_completed_target_cm != target:
            return False
        if (desk_height is None
                or abs(desk_height - target) < self._settings.auto_rearm_distance_cm):
            self._auto_rearm_started_mono = None
            return True
        if self._auto_rearm_started_mono is None:
            self._auto_rearm_started_mono = now_mono
            return True
        return now_mono - self._auto_rearm_started_mono < self._settings.auto_rearm_seconds

    def _reset_candidate_locked(self) -> None:
        self._candidate_started_mono = None
        self._candidate_pair = None
        self._last_pair = None
        self._snapshot = replace(self._snapshot, posture_candidate=None,
                                 candidate_since=None, initial_move_due_at=None)

    def _set_waiting_locked(self, reason: str) -> None:
        blocked = self._with_stop_failure(())
        self._replace_locked(session_id=None, control_mode=None, activity_mode=None,
                             state=AutomationState.BLOCKED if blocked else AutomationState.WAITING_USER,
                             height_policy=None,
                             posture_candidate=None, candidate_since=None, target_height_cm=None,
                             intent_source=None, blocked_reason_codes=blocked, initial_move_due_at=None,
                             park_due_at=None, last_transition_reason=reason)

    def _replace_locked(self, **changes: object) -> None:
        now = self._utc_now()
        changes.setdefault("revision", self._snapshot.revision + 1)
        changes.setdefault("updated_at", now)
        changes.setdefault("last_transition_at", now)
        changes.setdefault("last_transition_source", "AUTOMATION")
        self._snapshot = replace(self._snapshot, **changes)

    async def _begin_usage(self, profile_id: str | None, mode: EffectiveActivityMode | None) -> None:
        """모드가 실제로 걸린 순간부터 사용 시간을 세기 시작한다."""

        if self._usage is None or profile_id is None or mode is None:
            return
        try:
            await self._usage.start_interval(profile_id, mode.key, mode.name)
        except Exception:
            LOGGER.exception(
                "작업 모드 사용 기록을 시작하지 못했습니다.",
                extra={"component": "automation", "event": "usage_start_failed"},
            )

    async def _end_usage(self, profile_id: str | None) -> None:
        """자리를 비우면 구간을 닫아 그동안 시간이 늘지 않게 한다."""

        if self._usage is None:
            return
        try:
            await self._usage.close_open_intervals(profile_id)
        except Exception:
            LOGGER.exception(
                "작업 모드 사용 기록을 닫지 못했습니다.",
                extra={"component": "automation", "event": "usage_close_failed"},
            )

    def _remember_mode(self, profile_id: str | None, mode_key: str | None) -> None:
        if profile_id is None or mode_key is None:
            return
        self._remembered_mode[profile_id] = (mode_key, self._monotonic())

    def _recall_mode(self, profile_id: str | None) -> str | None:
        """기억 시간이 지나지 않았으면 마지막으로 쓰던 모드 key를 돌려준다."""

        if profile_id is None:
            return None
        remembered = self._remembered_mode.get(profile_id)
        if remembered is None:
            return None
        mode_key, stored_at = remembered
        if self._monotonic() - stored_at > self._mode_memory_seconds:
            self._remembered_mode.pop(profile_id, None)
            return None
        return mode_key

    def _install_mode_lighting(self, mode: EffectiveActivityMode | None) -> LedSetting:
        """모드가 걸릴 때 조명 계획을 세우고 지금 적용할 값을 돌려준다."""

        self._mode_started_mono = self._monotonic()
        self._schedule_applied = None
        self._active_schedule = None
        if mode is None:
            return (None, None)
        if mode.led_schedule is not None:
            try:
                self._active_schedule = parse_schedule(mode.led_schedule)
            except Exception:
                LOGGER.warning(
                    "조명 스케줄을 읽지 못해 저장된 색을 그대로 씁니다.",
                    extra={"component": "automation", "event": "led_schedule_unreadable"},
                )
        resolved = self._resolve_schedule()
        if resolved is not None:
            self._schedule_applied = resolved
            return resolved
        return (mode.led_color, mode.led_brightness)

    def _resolve_schedule(self) -> tuple[str, int] | None:
        """지금 스케줄이 가리키는 (색, 밝기). 스케줄이 없으면 None."""

        schedule = self._active_schedule
        if schedule is None:
            return None
        started = self._mode_started_mono
        elapsed = None if started is None else (self._monotonic() - started) / 60.0
        return schedule.resolve(now=self._local_now().time(), elapsed_minutes=elapsed)

    def _local_now(self) -> datetime:
        """시각 스케줄이 쓸 현지 시각. 컨테이너가 UTC로 돌아도 어긋나지 않는다."""

        try:
            return self._utc_now().astimezone(ZoneInfo(self._settings.schedule_timezone))
        except Exception:
            return self._utc_now()

    def _tick_led_schedule(self) -> None:
        """구간이 넘어갔으면 조명을 다시 보낸다. 매 관찰마다 값싸게 확인한다."""

        if self._active_schedule is None or self._wled is None:
            return
        resolved = self._resolve_schedule()
        if resolved is None or resolved == self._schedule_applied:
            return
        self._schedule_applied = resolved
        colour, brightness = resolved
        self._queue_led(colour, brightness)

    def _announce_height(self, target: float, *, automatic: bool) -> None:
        """책상이 어디로 가는지 알린다. 실제로 움직일 때만 부른다."""

        if self._announcer is None:
            return
        here = self._desk_height()
        # 이미 그 높이면 움직이지 않으므로 말하지 않는다.
        if here is not None and abs(here - target) <= self._target_tolerance_cm:
            return
        # 같은 목표를 연달아 반복하지 않는다.
        if (self._announced_target_cm is not None
                and abs(self._announced_target_cm - target) <= self._target_tolerance_cm):
            return
        self._announced_target_cm = target
        # 지금 높이를 모르면 어느 쪽인지 단정하지 않는다.
        direction = "옮길게요" if here is None else "올릴게요" if target > here else "내릴게요"
        lead = "자세에 맞춰 " if automatic else ""
        self._announcer.say_soon(f"{lead}책상을 {target:.0f}센티미터로 {direction}.")

    def _queue_led(self, color: str | None, brightness: int | None = None) -> None:
        self._queue_led_values(((color, brightness),))

    def _queue_led_sequence(self, color: str | None, brightness: int | None = None) -> None:
        values: tuple[LedSetting, ...] = (
            ((None, None),) if color is None else ((None, None), (color, brightness))
        )
        self._queue_led_values(values)

    def _queue_led_values(self, values: tuple[LedSetting, ...]) -> None:
        """Coalesce LED ownership; an older session cannot paint over a newer one."""
        if self._wled is None:
            return
        self._wled_sequence += 1
        sequence = self._wled_sequence
        previous = self._wled_task

        async def apply() -> None:
            if previous is not None:
                await asyncio.gather(previous, return_exceptions=True)
            for color, brightness in values:
                if sequence != self._wled_sequence:
                    return
                await self._apply_led(color, brightness, sequence)

        self._wled_task = asyncio.create_task(apply(), name="desk-automation-wled")

    async def _apply_led(self, color: str | None, brightness: int | None, sequence: int) -> None:
        try:
            async with self._wled_io_lock:
                if sequence != self._wled_sequence or self._wled is None:
                    return
                if color is None:
                    await self._wled.turn_off()
                else:
                    # 밝기를 먼저 맞춘 뒤 색을 올린다. 색이 나타나는 순간
                    # 이미 그 모드의 밝기여서 직전 밝기가 스치지 않는다.
                    if brightness is not None:
                        await self._wled.set_brightness(brightness)
                    await self._wled.set_solid(color)
        except Exception:
            async with self._state_lock:
                codes = tuple(dict.fromkeys((*self._snapshot.blocked_reason_codes, "WLED_UNAVAILABLE")))
                self._replace_locked(blocked_reason_codes=codes)
        else:
            async with self._state_lock:
                if "WLED_UNAVAILABLE" in self._snapshot.blocked_reason_codes:
                    self._replace_locked(blocked_reason_codes=tuple(
                        code for code in self._snapshot.blocked_reason_codes if code != "WLED_UNAVAILABLE"
                    ))

    async def _safe_stop(self, reason: str) -> bool:
        try:
            await self._desk.stop_motion(reason)
        except Exception:
            # Safety STOP is best-effort in background work, but hiding a
            # failure would falsely report an unblocked automation state.  A
            # newer command/session may own the selection now, but cannot make
            # the physical STOP failure safe, so retain that selection while
            # globally latching automation as BLOCKED.
            async with self._state_lock:
                self._replace_locked(
                    state=AutomationState.BLOCKED,
                    blocked_reason_codes=self._stop_failure_codes(),
                )
            return False
        return True

    async def _stop_or_block(self, reason: str) -> None:
        """Propagate a command STOP failure after recording the fail-closed state."""
        try:
            await self._desk.stop_motion(reason)
        except Exception:
            await self._mark_stop_failed()
            raise

    async def _mark_stop_failed(self) -> None:
        async with self._state_lock:
            self._replace_locked(
                state=AutomationState.BLOCKED,
                blocked_reason_codes=self._stop_failure_codes(),
            )

    def _raise_if_stop_failed(self) -> None:
        if "DESK_STOP_FAILED" in self._snapshot.blocked_reason_codes:
            raise RuntimeError("DESK_STOP_FAILED: 사용자 STOP 성공 후 다시 시도하세요.")

    def _with_stop_failure(self, codes: tuple[str, ...]) -> tuple[str, ...]:
        """Keep the safety latch while unrelated reasons are refreshed."""
        values = (*codes,)
        if "DESK_STOP_FAILED" in self._snapshot.blocked_reason_codes:
            values = (*values, "DESK_STOP_FAILED")
        return tuple(dict.fromkeys(values))

    def _stop_failure_codes(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys((*self._snapshot.blocked_reason_codes, "DESK_STOP_FAILED"))
        )

    def _without_stop_failure(self) -> tuple[str, ...]:
        return tuple(
            code for code in self._snapshot.blocked_reason_codes
            if code != "DESK_STOP_FAILED"
        )

    @staticmethod
    def _vision_codes(vision: VisionSnapshot) -> tuple[str, ...]:
        return tuple(str(code) for code in vision.reason_codes) or ("VISION_UNUSABLE",)
