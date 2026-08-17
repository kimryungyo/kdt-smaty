"""The single owner of automatic desk intent, session changes, and commands."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Protocol

from smart_desk.config.settings import AutomationSettings
from smart_desk.modules.automation.models import (
    AutomationSnapshot, AutomationState, ControlMode, HeightPolicy, IntentSource,
)
from smart_desk.modules.identity.models import CurrentUserSnapshot, SessionKind
from smart_desk.modules.desk.models import (
    DeskSnapshot, DeskState, Direction, HeightProvenance, HeightStatus,
    RelayEvent, RelayState,
)
from smart_desk.modules.profiles.activity_modes import (
    ActivityModeNotFoundError, ActivityModeOwnershipError, effective_mode_from_activity,
)
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
    async def turn_off(self) -> None: ...


class AutomationService:
    """Serializes commands separately from short snapshot mutations.

    The state lock protects only replacement of ``AutomationSnapshot`` and task
    ownership.  All repository, session, Desk, and WLED waits happen outside it.
    """

    def __init__(
        self, *, current_user: CurrentUserPort, vision: VisionPort,
        activity_modes: ActivityModePort, desk: DeskPort, settings: AutomationSettings,
        wled: WledPort | None = None, target_tolerance_cm: float = 0.5,
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
        self._first_auto = True
        self._park_started_mono: float | None = None
        self._park_pair: tuple[float, float] | None = None
        self._startup_pair: tuple[float, float] | None = None
        self._live_automatic = False
        self._vision_recovery_baseline_required = False
        # Session kind is transition state, not a derived profile property: an
        # anonymous-to-registered confirmation has a deliberately different
        # AUTO contract from every other session replacement.
        self._session_kind: SessionKind | None = None

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

    def _on_session_change(self, _event: object) -> None:
        self._wake.set()

    async def hold(self, direction: Direction) -> None:
        async with self._command_lock:
            live = await self._make_manual("HOLD")
            self._raise_if_stop_failed()
            if live:
                # A user command must not run after its required preemption
                # STOP failed.  _make_manual deliberately happened first, so
                # the caller can safely retry from the preserved MANUAL state.
                await self._stop_or_block("수동 HOLD가 자동 이동을 선점했습니다.")
            if direction is Direction.UP:
                await self._desk.hold_up()
            else:
                await self._desk.hold_down()

    async def set_target(self, target_cm: float) -> None:
        async with self._command_lock:
            live = await self._make_manual("SET_TARGET")
            self._raise_if_stop_failed()
            if live:
                await self._stop_or_block("직접 목표가 자동 이동을 선점했습니다.")
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
                self._first_auto = False
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
                    self._first_auto = False
                    self._last_pair = self._pair(self._vision.get_snapshot())
            # Mode selection and its LED are committed independently of the
            # Desk preemption outcome; a failed STOP must not roll either back.
            self._queue_led(selected.led_color)
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

    async def _make_manual(self, reason: str) -> bool:
        async with self._state_lock:
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
                try:
                    activity = await self._read_mode(current.profile_id or "", "default")
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
            self._first_auto = not anonymous_upgrade
            self._last_pair = self._pair(vision)
            self._vision_recovery_baseline_required = unusable_upgrade
            expected_generation = self._snapshot.generation
        if live:
            if not await self._safe_stop("사용자 교대 안전 정지"):
                await self._queue_install_led(
                    current.session_id, expected_generation,
                    activity.led_color if activity is not None else None,
                )
                return True
        if not await self._queue_install_led(
            current.session_id, expected_generation,
            activity.led_color if activity is not None else None,
        ):
            return True
        if anonymous_upgrade and control is ControlMode.AUTO and failure is None and self._auto_usable(vision):
            await self._schedule_upgrade_target(current, vision, expected_generation)
        return True

    async def _queue_install_led(
        self, expected_session_id: str, expected_generation: int, color: str | None,
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
            self._queue_led_sequence(color)
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
            live = self._invalidate_locked("SESSION_ENDED")
            self._set_waiting_locked("SESSION_ENDED")
            self._session_kind = None
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
                due = 2.0 if self._first_auto else 5.0
                self._replace_locked(state=AutomationState.OBSERVING, posture_candidate=posture,
                                     candidate_since=self._utc_now(),
                                     initial_move_due_at=self._utc_now() + timedelta(seconds=due),
                                     blocked_reason_codes=self._with_stop_failure(()))
                return
            assert self._candidate_started_mono is not None
            due = 2.0 if self._first_auto else 5.0
            if now_mono - self._candidate_started_mono < due:
                return
            target = self._target_for(self._snapshot, posture)
            if target is None:
                self._replace_locked(state=AutomationState.BLOCKED, blocked_reason_codes=("ACTIVITY_MODE_UNAVAILABLE",))
                return
            if desk_height is not None and abs(desk_height - target) <= self._target_tolerance_cm:
                self._replace_locked(state=AutomationState.READY, target_height_cm=target,
                                     intent_source=IntentSource.AUTO, initial_move_due_at=None,
                                     blocked_reason_codes=self._with_stop_failure(()))
                self._first_auto = False
                return
            if self._snapshot.intent_source is IntentSource.AUTO and self._snapshot.target_height_cm == target:
                return
            self._schedule_locked(target, IntentSource.AUTO, current.session_id)
            self._first_auto = False

    def _auto_usable(self, vision: VisionSnapshot) -> bool:
        return (vision.usable and vision.stable_presence is PresenceStatus.PRESENT_SINGLE
                and vision.stable_posture in (PostureStatus.SITTING, PostureStatus.STANDING)
                and self._pair(vision) is not None)

    async def _block_auto(self, vision: VisionSnapshot) -> None:
        codes = self._vision_codes(vision)
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
            # Recovery is a re-entry boundary, never the initial 2-second
            # session delay, even when uncertainty interrupted that delay.
            self._first_auto = False
            self._replace_locked(
                state=AutomationState.BLOCKED,
                blocked_reason_codes=merged_codes,
            )
        if live:
            await self._safe_stop("Vision 불확실성 안전 정지")

    async def _finish_automatic_if_idle(self, session_id: str | None) -> None:
        """Keep an accepted AUTO/PARK command as intent after the desk settles."""
        try:
            desk_state = self._desk.get_snapshot().state
        except Exception:
            return
        if desk_state not in {DeskState.IDLE, DeskState.STOPPED}:
            return
        async with self._state_lock:
            snapshot = self._snapshot
            if (snapshot.session_id != session_id or not self._live_automatic
                    or snapshot.intent_source not in {IntentSource.AUTO, IntentSource.PARK}):
                return
            self._live_automatic = False
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
            if desk_code is not None:
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
        """PARK is intentionally stricter than ordinary AUTO dispatch."""
        try:
            desk = self._desk.get_snapshot()
        except Exception:
            return False, "DESK_UNAVAILABLE"
        if (desk.state is DeskState.MOVING and self._live_automatic
                and self._snapshot.intent_source is IntentSource.PARK):
            return True, None
        if desk.state is DeskState.ERROR:
            return False, "DESK_ERROR"
        height = desk.height
        relay = desk.relay
        if (height.status is not HeightStatus.ONLINE
                or height.provenance is not HeightProvenance.LIVE
                or height.height_cm is None):
            return False, "PARK_HEIGHT_UNAVAILABLE"
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
        self._live_automatic = True
        self._dispatch_task = asyncio.create_task(
            self._dispatch_target(target, source, generation, session_id), name="desk-automation-dispatch"
        )

    async def _dispatch_target(self, target: float, source: IntentSource,
                               generation: int, session_id: str | None) -> None:
        if not await self._dispatch_valid(generation, session_id, source):
            return
        try:
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

    def _queue_led(self, color: str | None) -> None:
        self._queue_led_values((color,))

    def _queue_led_sequence(self, color: str | None) -> None:
        values: tuple[str | None, ...] = (None,) if color is None else (None, color)
        self._queue_led_values(values)

    def _queue_led_values(self, values: tuple[str | None, ...]) -> None:
        """Coalesce LED ownership; an older session cannot paint over a newer one."""
        if self._wled is None:
            return
        self._wled_sequence += 1
        sequence = self._wled_sequence
        previous = self._wled_task

        async def apply() -> None:
            if previous is not None:
                await asyncio.gather(previous, return_exceptions=True)
            for color in values:
                if sequence != self._wled_sequence:
                    return
                await self._apply_led(color, sequence)

        self._wled_task = asyncio.create_task(apply(), name="desk-automation-wled")

    async def _apply_led(self, color: str | None, sequence: int) -> None:
        try:
            async with self._wled_io_lock:
                if sequence != self._wled_sequence or self._wled is None:
                    return
                if color is None:
                    await self._wled.turn_off()
                else:
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
